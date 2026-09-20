# This project was developed with assistance from AI tools.
"""Send this world's joint positions and cube poses to the fleet's renderer, one UDP datagram per update."""

# The sending half of docs/internal/FLEET-RENDER-CONTRACT.md. Joints come from /joint_states (rclpy), cube poses
# from gz transport. Nothing is ever received; a renderer that is away costs nothing but lost datagrams.
#
#   ROBOT_ID            r01, r02, ...                                              (required)
#   RENDER_STATE_ADDR   host:port of the renderer                                  (default 10.20.0.1:9701)
#   STATE_HZ            datagrams a second                                         (default 30)
#   CUBE_POSE_SOURCE    auto | python | cli - gz python bindings or `gz topic -e`  (default auto: bindings, else cli)
#   GZ_WORLD            the Gazebo world's name                                    (default pai_world)
#   FORWARDER_ALIVE     file touched while joint states flow, for the pod's probes (default /tmp/fleet-world/alive)

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forwarder_core import (CUBES, build_datagram, cubes_from_json, first_cube_poses,  # noqa: E402
                            parse_addr, select_joints, valid_robot_id)

WORLD = os.environ.get("GZ_WORLD", "pai_world")
POSE_TOPIC = f"/world/{WORLD}/dynamic_pose/info"
ALIVE = Path(os.environ.get("FORWARDER_ALIVE", "/tmp/fleet-world/alive"))
STATS_EVERY_S = 30.0


def log(msg: str) -> None:
    print(f"[state-forwarder] {msg}", flush=True)


class CubePoses:
    """The latest sim-time stamp and pose of the three cubes, from whichever source feeds it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: tuple[float, dict] | None = None
        self.messages = 0

    def update(self, stamp: float, cubes: dict) -> None:
        if len(cubes) == len(CUBES):
            with self._lock:
                self._latest = (stamp, cubes)
                self.messages += 1

    def latest(self) -> tuple[float, dict] | None:
        with self._lock:
            return self._latest


def start_python_source(sink: CubePoses):
    """Subscribe through the gz transport python bindings; the returned node must stay referenced."""
    from gz.msgs11.pose_v_pb2 import Pose_V
    from gz.transport14 import Node as GzNode

    def on_poses(msg) -> None:
        stamp = msg.header.stamp.sec + msg.header.stamp.nsec * 1e-9
        sink.update(stamp, first_cube_poses(
            (p.name, (p.position.x, p.position.y, p.position.z),
             (p.orientation.x, p.orientation.y, p.orientation.z, p.orientation.w)) for p in msg.pose))

    node = GzNode()
    if not node.subscribe(Pose_V, POSE_TOPIC, on_poses):
        raise RuntimeError(f"gz subscribe to {POSE_TOPIC} failed")
    return node


def start_cli_source(sink: CubePoses):
    """Follow `gz topic -e --json-output` in a thread: one JSON message a line."""
    proc = subprocess.Popen(["gz", "topic", "-e", "--json-output", "-t", POSE_TOPIC],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def pump() -> None:
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                sink.update(*cubes_from_json(json.loads(line)))
            except (ValueError, TypeError, AttributeError):
                continue
        log("gz topic exited: cube poses have stopped")

    threading.Thread(target=pump, daemon=True).start()
    return proc


def start_cube_source(sink: CubePoses):
    """The source CUBE_POSE_SOURCE names; auto falls back to the cli when the bindings do not load or subscribe."""
    choice = os.environ.get("CUBE_POSE_SOURCE", "auto")
    if choice not in ("auto", "python", "cli"):
        sys.exit(f"CUBE_POSE_SOURCE must be auto, python or cli, not {choice!r}")
    if choice in ("auto", "python"):
        try:
            handle = start_python_source(sink)
            log(f"cube poses: gz python bindings on {POSE_TOPIC}")
            return handle
        except (ImportError, RuntimeError) as err:
            if choice == "python":
                sys.exit(f"gz python bindings unusable: {err}")
            log(f"gz python bindings unusable ({err}): falling back to gz topic -e")
    handle = start_cli_source(sink)
    log(f"cube poses: gz topic -e --json-output on {POSE_TOPIC}")
    return handle


class Forwarder(Node):
    """Keeps the latest joint sample and sends the datagram on a wall-clock timer."""

    def __init__(self, robot: str, addr: tuple[str, int], hz: float, cubes: CubePoses) -> None:
        super().__init__("fleet_state_forwarder")
        self.robot, self.addr, self.cubes = robot, addr, cubes
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.joints: tuple[float, dict] | None = None
        self.fresh = False
        self.sent = self.errors = self.skipped = self.joint_msgs = self.last_size = 0
        self.last_error = ""
        self.t_stats = self.t_alive = time.monotonic()
        ALIVE.parent.mkdir(parents=True, exist_ok=True)
        self.create_subscription(JointState, "/joint_states", self.on_joints, 10)
        self.create_timer(1.0 / hz, self.on_timer)

    def on_joints(self, msg: JointState) -> None:
        joints = select_joints(msg.name, msg.position)
        if joints is None:
            return
        self.joints = (msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9, joints)
        self.fresh = True
        self.joint_msgs += 1
        now = time.monotonic()
        if now - self.t_alive >= 1.0:
            self.t_alive = now
            ALIVE.write_text(f"{self.joints[0]:.3f} sim s, {self.sent} datagrams sent\n")

    def on_timer(self) -> None:
        now = time.monotonic()
        if now - self.t_stats >= STATS_EVERY_S:
            dt = now - self.t_stats
            log(f"{self.sent / dt:.1f} datagrams/s ({self.last_size} bytes) to {self.addr[0]}:{self.addr[1]}, "
                f"joint states {self.joint_msgs / dt:.1f}/s, cube poses {self.cubes.messages / dt:.1f}/s, "
                f"{self.skipped} ticks without new state, {self.errors} send errors {self.last_error}")
            self.sent = self.errors = self.skipped = self.joint_msgs = self.cubes.messages = 0
            self.last_error = ""
            self.t_stats = now
        cubes = self.cubes.latest()
        # A stalled sim must look stalled to the renderer: only a new joint sample is worth a datagram.
        if not self.fresh or self.joints is None or cubes is None:
            self.skipped += 1
            return
        self.fresh = False
        try:
            data = build_datagram(self.robot, max(self.joints[0], cubes[0]), self.joints[1], cubes[1])
            self.sock.sendto(data, self.addr)
            self.sent += 1
            self.last_size = len(data)
        except (ValueError, OSError) as err:
            self.errors += 1
            self.last_error = f"(last: {err})"


def main() -> None:
    try:
        robot = valid_robot_id(os.environ.get("ROBOT_ID", ""))
        addr = parse_addr(os.environ.get("RENDER_STATE_ADDR", "10.20.0.1:9701"))
        hz = float(os.environ.get("STATE_HZ", "30"))
        if not 0 < hz <= 200:
            raise ValueError(f"STATE_HZ must be above 0 and at most 200, got {hz}")
    except ValueError as err:
        sys.exit(f"state_forwarder: {err}")
    log(f"robot {robot} -> udp {addr[0]}:{addr[1]} at {hz:g} Hz")
    cubes = CubePoses()
    source = start_cube_source(cubes)  # noqa: F841 - held so the subscription lives
    rclpy.init()
    node = Forwarder(robot, addr, hz, cubes)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
