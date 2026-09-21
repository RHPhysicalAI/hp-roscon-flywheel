# This project was developed with assistance from AI tools.
"""Robot zero's cameras: fetch its two pictures from the fleet's renderer and publish them on the policy's image topics."""

# Robot zero (D164) is the GPU host's own policy on a MIG slice. A slice has no graphics, so its sim is physics-only
# and its cameras are the rendering tenant's ray-traced pictures of robot r00 - the sim's state forwarder is the
# other half of the loop. Pictures in over HTTP, sensor_msgs/Image out through the sim's Zenoh router; the
# geometry, the stamps and what happens when the renderer is away are in bridge_core.py.
#
#   ROBOT_ID            the robot whose pictures to fetch                          (default r00)
#   RENDER_HTTP_ADDR    host:port of the renderer's HTTP side                      (default 10.20.0.1:9702)
#   FRAME_HZ            fetches a second per camera; the renderer draws 15         (default 15)
#   FETCH_TIMEOUT_S     wall time one picture may take, connect to last byte       (default 1.0)
#   STALE_AFTER_S       without a picture for this long, say so                    (default 2.0)
#   ZENOH_ROUTER        host:port of the sim's Zenoh router                        (default 127.0.0.1:7447)

import array
import os
import sys
import time
from pathlib import Path

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_core as core

STATS_EVERY_S = 30.0
ZENOH_CONFIG = Path("/tmp/robot-zero/zenoh_session.json5")


def log(msg: str) -> None:
    print(f"[frame-bridge] {msg}", flush=True)


class FrameBridge(Node):
    """One timer: both pictures fetched, fitted and published with one stamp."""

    def __init__(self, config: core.Config) -> None:
        super().__init__("robot_zero_frame_bridge")
        self.config = config
        # Reliable, latest only: it matches the policy's best-effort subscription and a reliable one alike.
        qos = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.RELIABLE)
        self.pubs = {camera.name: self.create_publisher(Image, camera.topic, qos) for camera in core.CAMERAS}
        self.stamper = core.Stamper()
        self.fresh = core.Freshness(tuple(c.name for c in core.CAMERAS), config.stale_after_s, time.monotonic())
        self.published = self.failed = self.ticks = 0
        self.spent_s = 0.0
        self.t_stats = time.monotonic()
        self.create_timer(1.0 / config.hz, self.on_timer)

    def on_timer(self) -> None:
        started = time.monotonic()
        stamp_ns = self.stamper.next(time.time_ns())
        for camera in core.CAMERAS:
            # Exception, on purpose: this loop is built to outlive a bad tick. Whatever a renderer that is wedged,
            # restarting or hostile makes the fetch, a decoder or the publisher raise is a picture that did not
            # arrive - said once by Freshness - and never the end of the process: a bridge that exits is restarted
            # by systemd until the start limit is used up, and then robot zero has no cameras at all.
            try:
                picture = core.decode_jpeg(core.fetch_jpeg(self.config.url(camera), self.config.budget_s))
                self.publish(camera, core.fit_frame(picture), stamp_ns)
            except Exception as err:  # noqa: BLE001 - see above: a bad tick is survived
                self.failed += 1
                reason = str(err) if isinstance(err, (core.FetchError, ValueError)) else f"{type(err).__name__}: {err}"
                note = self.fresh.bad(camera.name, time.monotonic(), reason)
            else:
                self.published += 1
                note = self.fresh.good(camera.name, time.monotonic())
            if note:
                log(note)
        now = time.monotonic()
        self.ticks += 1
        self.spent_s += now - started
        if now - self.t_stats >= STATS_EVERY_S:
            dt = now - self.t_stats
            log(f"{self.published / dt / len(core.CAMERAS):.1f} frames/s a camera to the policy's topics, "
                f"{self.failed} fetches failed, {1e3 * self.spent_s / self.ticks:.0f} ms a tick, "
                f"{'live' if self.fresh.live() else 'NOT live'}")
            self.published = self.failed = self.ticks = 0
            self.spent_s, self.t_stats = 0.0, now

    def publish(self, camera: core.Camera, frame, stamp_ns: int) -> None:
        msg = Image()
        msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(stamp_ns, 1_000_000_000)
        msg.header.frame_id = camera.frame_id
        msg.height, msg.width = frame.shape[:2]
        msg.encoding = core.ENCODING
        msg.is_bigendian = 0
        msg.step = frame.shape[1] * 3
        # an array of bytes is the field's own type; anything else is converted element by element
        msg.data = array.array("B", frame.tobytes())
        self.pubs[camera.name].publish(msg)


def main() -> None:
    try:
        config = core.Config.from_env(os.environ)
    except ValueError as err:
        sys.exit(f"frame_bridge: {err}")
    # before the middleware starts: a client of the sim's router, never a router of its own
    os.environ.update(core.write_zenoh_config(config.zenoh_router, ZENOH_CONFIG))
    log(f"robot {config.robot}: http://{config.http_addr[0]}:{config.http_addr[1]} -> "
        f"{', '.join(c.topic for c in core.CAMERAS)} at {config.hz:g} Hz, {core.FRAME_WIDTH}x{core.FRAME_HEIGHT} "
        f"{core.ENCODING}, router tcp/{config.zenoh_router[0]}:{config.zenoh_router[1]}")
    rclpy.init()
    node = FrameBridge(config)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
