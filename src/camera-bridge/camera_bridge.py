"""Camera bridge — serves ROS 2 image topics as MJPEG streams over HTTP.

Subscribes to /wrist_camera/image_raw and /static_camera/image_raw,
converts each frame to JPEG, and serves them as multipart MJPEG streams
at /wrist and /static endpoints. The dashboard embeds these as <img> tags.

Also serves the latest frame as a single JPEG at /wrist/snapshot and
/static/snapshot for low-bandwidth or thumbnail use.

Runs as a separate process in the so-arm-sim container alongside the
Gazebo sim and episode emitter.
"""

import io
import os
import threading
import time

from flask import Flask, Response
import numpy as np

# Lazy imports — rclpy and cv2 are heavy
_rclpy_ready = False
_node = None
_latest_frames = {"wrist": None, "static": None}
_frame_locks = {"wrist": threading.Lock(), "static": threading.Lock()}

app = Flask(__name__)
PORT = int(os.environ.get("CAMERA_BRIDGE_PORT", "8081"))


def _init_ros():
    """Initialize ROS 2 subscriptions in a background thread."""
    global _rclpy_ready, _node
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
    from sensor_msgs.msg import Image

    rclpy.init()

    class CameraBridgeNode(Node):
        def __init__(self):
            super().__init__("camera_bridge")
            qos = QoSProfile(
                reliability=ReliabilityPolicy.BEST_EFFORT,
                history=HistoryPolicy.KEEP_LAST,
                depth=1,
            )
            self.create_subscription(
                Image, "/wrist_camera/image_raw", self._on_wrist, qos
            )
            self.create_subscription(
                Image, "/static_camera/image_raw", self._on_static, qos
            )
            self.get_logger().info("Camera bridge subscribed to wrist + static cameras")

        def _on_wrist(self, msg):
            self._store_frame("wrist", msg)

        def _on_static(self, msg):
            self._store_frame("static", msg)

        def _store_frame(self, name, msg):
            """Convert ROS Image to JPEG bytes and store."""
            try:
                import cv2

                # ROS Image -> numpy array
                if msg.encoding == "rgb8":
                    arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(
                        msg.height, msg.width, 3
                    )
                    arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
                elif msg.encoding == "bgr8":
                    arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(
                        msg.height, msg.width, 3
                    )
                elif msg.encoding in ("mono8", "8UC1"):
                    arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(
                        msg.height, msg.width
                    )
                else:
                    # Try generic 3-channel
                    arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(
                        msg.height, msg.width, -1
                    )

                _, jpeg = cv2.imencode(
                    ".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 70]
                )
                with _frame_locks[name]:
                    _latest_frames[name] = jpeg.tobytes()
            except Exception as e:
                self.get_logger().warn(f"Frame encode error ({name}): {e}")

    _node = CameraBridgeNode()
    _rclpy_ready = True

    # Spin in this thread
    rclpy.spin(_node)


def _mjpeg_stream(camera_name):
    """Generate MJPEG multipart stream from stored frames."""
    while True:
        with _frame_locks[camera_name]:
            frame = _latest_frames[camera_name]
        if frame:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            )
        time.sleep(0.1)  # ~10 fps stream rate


@app.route("/wrist")
def wrist_stream():
    return Response(
        _mjpeg_stream("wrist"),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/static")
def static_stream():
    return Response(
        _mjpeg_stream("static"),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/wrist/snapshot")
def wrist_snapshot():
    with _frame_locks["wrist"]:
        frame = _latest_frames["wrist"]
    if not frame:
        return ("no frame yet", 503)
    return Response(frame, mimetype="image/jpeg")


@app.route("/static/snapshot")
def static_snapshot():
    with _frame_locks["static"]:
        frame = _latest_frames["static"]
    if not frame:
        return ("no frame yet", 503)
    return Response(frame, mimetype="image/jpeg")


@app.route("/health")
def health():
    return {
        "wrist": _latest_frames["wrist"] is not None,
        "static": _latest_frames["static"] is not None,
    }


if __name__ == "__main__":
    # Start ROS 2 in background thread
    ros_thread = threading.Thread(target=_init_ros, daemon=True)
    ros_thread.start()

    # Wait for ROS to initialize
    for _ in range(30):
        if _rclpy_ready:
            break
        time.sleep(1)

    print(f"[camera-bridge] Serving MJPEG streams on port {PORT}", flush=True)
    print(f"[camera-bridge]   /wrist   — wrist camera MJPEG stream", flush=True)
    print(f"[camera-bridge]   /static  — static camera MJPEG stream", flush=True)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
