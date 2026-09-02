"""Camera bridge — high-performance MJPEG streaming from ROS 2 camera topics.

Subscribes to /wrist_camera/image_raw and /static_camera/image_raw,
encodes frames as JPEG, and serves them as MJPEG streams. Designed for
smooth, near-real-time viewing in a browser.

Key design choices for performance:
- Frames served at source rate (up to 30 fps), not throttled by sleep()
- No Flask proxy — uses raw HTTP server for zero-copy streaming
- JPEG quality tuned for speed (quality=60, ~3ms encode on CPU)
- Threading: one ROS spinner thread, one HTTP server thread per camera
- Browser connects directly to this server (no dashboard proxy hop)
"""

import io
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

import numpy as np

PORT = int(os.environ.get("CAMERA_BRIDGE_PORT", "8081"))

# Latest frame storage — lock-free via GIL for single-writer
_latest = {"wrist": None, "static": None}


def _init_ros():
    """Initialize ROS 2 and subscribe to camera topics."""
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
    from sensor_msgs.msg import Image
    import cv2

    rclpy.init()

    class CamNode(Node):
        def __init__(self):
            super().__init__("camera_bridge")
            qos = QoSProfile(
                reliability=ReliabilityPolicy.BEST_EFFORT,
                history=HistoryPolicy.KEEP_LAST,
                depth=1,
            )
            self.create_subscription(Image, "/wrist_camera/image_raw", self._on_wrist, qos)
            self.create_subscription(Image, "/static_camera/image_raw", self._on_static, qos)
            self.get_logger().info("Camera bridge: subscribed to wrist + static cameras")

        def _on_wrist(self, msg):
            self._encode(msg, "wrist")

        def _on_static(self, msg):
            self._encode(msg, "static")

        def _encode(self, msg, name):
            try:
                if msg.encoding == "rgb8":
                    arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 3)
                    arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                elif msg.encoding == "bgr8":
                    arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 3)
                else:
                    arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, -1)
                _, jpeg = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 60])
                _latest[name] = jpeg.tobytes()
            except Exception:
                pass

    node = CamNode()
    rclpy.spin(node)


class MJPEGHandler(BaseHTTPRequestHandler):
    """Serve MJPEG streams and snapshots with minimal overhead."""

    def log_message(self, format, *args):
        pass  # silence per-request logging

    def do_GET(self):
        if self.path == "/wrist":
            self._stream("wrist")
        elif self.path == "/static":
            self._stream("static")
        elif self.path == "/wrist/snapshot":
            self._snapshot("wrist")
        elif self.path == "/static/snapshot":
            self._snapshot("static")
        elif self.path == "/health":
            self._health()
        else:
            self.send_error(404)

    def _stream(self, cam):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        last_frame = None
        try:
            while True:
                frame = _latest[cam]
                if frame is not None and frame is not last_frame:
                    last_frame = frame
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(frame)}\r\n".encode())
                    self.wfile.write(b"\r\n")
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                else:
                    time.sleep(0.02)  # 50 fps poll ceiling
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _snapshot(self, cam):
        frame = _latest[cam]
        if not frame:
            self.send_error(503, "No frame yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(frame)

    def _health(self):
        import json
        data = json.dumps({"wrist": _latest["wrist"] is not None, "static": _latest["static"] is not None})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data.encode())


class ThreadedHTTPServer(HTTPServer):
    """Handle each request in a new thread for concurrent streams."""
    def process_request(self, request, client_address):
        t = threading.Thread(target=self.finish_request, args=(request, client_address))
        t.daemon = True
        t.start()


if __name__ == "__main__":
    # Start ROS 2 in background
    ros_thread = threading.Thread(target=_init_ros, daemon=True)
    ros_thread.start()
    time.sleep(3)

    print(f"[camera-bridge] MJPEG server on port {PORT}", flush=True)
    print(f"[camera-bridge]   /wrist   — wrist camera stream", flush=True)
    print(f"[camera-bridge]   /static  — overhead camera stream", flush=True)
    server = ThreadedHTTPServer(("0.0.0.0", PORT), MJPEGHandler)
    server.serve_forever()
