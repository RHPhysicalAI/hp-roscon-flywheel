# This project was developed with assistance from AI tools.
"""The bridge's node with ROS stubbed out: what one tick publishes, and that a renderer that is away publishes nothing."""

import importlib
import io
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bridge_core as core
import numpy as np
import pytest

PIL_Image = pytest.importorskip("PIL.Image")


class FakeImage:
    """The fields of sensor_msgs/msg/Image the bridge fills."""

    def __init__(self) -> None:
        stamp = types.SimpleNamespace(sec=0, nanosec=0)
        self.header = types.SimpleNamespace(stamp=stamp, frame_id="")
        self.height = self.width = self.step = self.is_bigendian = 0
        self.encoding, self.data = "", None


class FakeNode:
    def __init__(self, name: str) -> None:
        self.name, self.sent, self.timers = name, {}, []

    def create_publisher(self, _type, topic, _qos):
        self.sent[topic] = []
        return types.SimpleNamespace(publish=self.sent[topic].append)

    def create_timer(self, period, callback) -> None:
        self.timers.append((period, callback))


@pytest.fixture
def bridge_module(monkeypatch):
    """frame_bridge imported against stand-ins for rclpy and sensor_msgs, and forgotten again afterwards."""
    names = {"rclpy": {}, "rclpy.executors": {"ExternalShutdownException": type("ExternalShutdownException", (Exception,), {})},
             "rclpy.node": {"Node": FakeNode},
             "rclpy.qos": {"QoSProfile": lambda **kw: kw, "HistoryPolicy": types.SimpleNamespace(KEEP_LAST=1),
                           "ReliabilityPolicy": types.SimpleNamespace(RELIABLE=1)},
             "sensor_msgs": {}, "sensor_msgs.msg": {"Image": FakeImage}}
    for name, attrs in names.items():
        module = types.ModuleType(name)
        vars(module).update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "frame_bridge", raising=False)
    module = importlib.import_module("frame_bridge")
    yield module
    sys.modules.pop("frame_bridge", None)


@pytest.fixture
def renderer():
    """The renderer's two pictures of r00 on the loopback address: a white square, 480x480."""
    out = io.BytesIO()
    PIL_Image.fromarray(np.full((480, 480, 3), 255, np.uint8)).save(out, "JPEG")
    jpeg = out.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            found = self.path in ("/robot/r00/static.jpg", "/robot/r00/wrist.jpg")
            body = jpeg if found else b"no picture of that robot\n"
            self.send_response(200 if found else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_one_tick_publishes_both_cameras_as_the_policy_expects(bridge_module, renderer):
    """640x480 rgb8, step 1920, 921600 bytes, the camera's frame id, one wall-clock stamp for the pair."""
    node = bridge_module.FrameBridge(core.Config.from_env({"RENDER_HTTP_ADDR": renderer}))
    assert node.timers[0][0] == pytest.approx(1 / 15)
    node.on_timer()
    stamps = set()
    for camera in core.CAMERAS:
        (msg,) = node.sent[camera.topic]
        assert (msg.height, msg.width, msg.step, msg.encoding, msg.is_bigendian) == (480, 640, 1920, "rgb8", 0)
        assert msg.header.frame_id == camera.frame_id
        assert msg.data.typecode == "B" and len(msg.data) == 480 * 640 * 3
        pixels = np.frombuffer(msg.data, np.uint8).reshape(480, 640, 3)
        assert pixels[240, 320].min() > 250 and not pixels[:, :80].any() and not pixels[:, 560:].any()
        assert 0 <= msg.header.stamp.nanosec < 1_000_000_000 and msg.header.stamp.sec > 1_700_000_000
        stamps.add((msg.header.stamp.sec, msg.header.stamp.nanosec))
    assert len(stamps) == 1
    assert node.fresh.live()


def test_stamps_rise_from_tick_to_tick(bridge_module, renderer):
    """The policy keeps a frame only if its stamp is not before the last one."""
    node = bridge_module.FrameBridge(core.Config.from_env({"RENDER_HTTP_ADDR": renderer}))
    node.on_timer()
    node.on_timer()
    first, second = node.sent["/static_camera/image_raw"]
    assert (second.header.stamp.sec, second.header.stamp.nanosec) > (first.header.stamp.sec, first.header.stamp.nanosec)


def test_a_robot_the_renderer_does_not_know_publishes_nothing(bridge_module, renderer):
    """404 from the renderer: no message on either topic, and the bridge does not raise."""
    node = bridge_module.FrameBridge(core.Config.from_env({"RENDER_HTTP_ADDR": renderer, "ROBOT_ID": "r05"}))
    node.on_timer()
    assert all(not sent for sent in node.sent.values())
    assert node.failed == 2 and not node.fresh.live()


@pytest.mark.parametrize("stage, error", [("fetch_jpeg", OSError("socket went away")), ("decode_jpeg", MemoryError("bomb")),
                                          ("fit_frame", RuntimeError("anything at all"))])
def test_a_tick_survives_whatever_is_raised(bridge_module, renderer, monkeypatch, stage, error):
    """An exception of any kind in a tick is a picture that did not arrive: nothing published, nothing raised, and the next tick works."""
    node = bridge_module.FrameBridge(core.Config.from_env({"RENDER_HTTP_ADDR": renderer}))
    with monkeypatch.context() as patched:
        patched.setattr(core, stage, lambda *a, **kw: (_ for _ in ()).throw(error))
        node.on_timer()
    assert all(not sent for sent in node.sent.values()) and node.failed == 2 and not node.fresh.live()
    node.on_timer()
    assert all(len(sent) == 1 for sent in node.sent.values()) and node.fresh.live()


def test_a_publisher_that_raises_does_not_end_the_bridge(bridge_module, renderer):
    """The middleware failing to publish is survived like a failed fetch."""
    node = bridge_module.FrameBridge(core.Config.from_env({"RENDER_HTTP_ADDR": renderer}))
    for camera in core.CAMERAS:
        node.pubs[camera.name].publish = lambda _msg: (_ for _ in ()).throw(RuntimeError("context is shut down"))
    node.on_timer()
    assert node.failed == 2 and node.published == 0
