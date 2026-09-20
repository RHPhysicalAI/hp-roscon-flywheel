# This project was developed with assistance from AI tools.
"""Pure helpers of robot zero's frame bridge: the renderer's pictures as the camera frames the policy expects, with no ROS imports."""

# What the policy expects is the rosetta contract of the runtime image (pai_data_collection's so_arm101.yaml at the
# image's pinned commit): sensor_msgs/msg/Image on /wrist_camera/image_raw and /static_camera/image_raw, subscribed
# best effort, the header stamp used, and every frame squeezed to 480x480 by nearest neighbour whatever its size.
# It was trained on Gazebo's 640x480 rgb8 frames squeezed that way. The fleet's renderer draws 480x480 with the
# same vertical field of view, so its picture IS the middle 480 columns of such a frame: it is centred and padded
# left and right, never stretched, and the policy's own squeeze then puts every pixel where training had it.
# The renderer's half is docs/internal/FLEET-RENDER-CONTRACT.md ("Pictures: renderer -> anyone").

import io
import re
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np

FRAME_WIDTH, FRAME_HEIGHT = 640, 480
ENCODING = "rgb8"
# The bridge trusts the renderer's geometry, not its good behaviour: what it will take from the socket, and what
# it will hand a decoder, are bounded here. The renderer's pictures are 480 px square and a few tens of kB.
MAX_JPEG_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 4_000_000
MAX_HEAD_BYTES = 16 * 1024
_ROBOT_ID = re.compile(r"r\d\d")
_STATUS_LINE = re.compile(rb"HTTP/1\.[01] (\d{3})(?: ([^\r\n]*))?")


@dataclass(frozen=True)
class Camera:
    """One camera of the contract: the renderer's name for it, the policy's topic, the frame id Gazebo gave it."""

    name: str
    topic: str
    frame_id: str


CAMERAS = (Camera("static", "/static_camera/image_raw", "static_camera_link"),
           Camera("wrist", "/wrist_camera/image_raw", "wrist_camera_link"))


class FetchError(Exception):
    """A picture that could not be had; the text is the reason for the log."""


def valid_robot_id(robot: str) -> str:
    """The robot id unchanged when it is 'r' plus two digits, ValueError otherwise."""
    if not isinstance(robot, str) or not _ROBOT_ID.fullmatch(robot):
        raise ValueError(f"robot id must be 'r' and two digits (r00), got {robot!r}")
    return robot


def parse_addr(addr: str) -> tuple[str, int]:
    """Split 'host:port' into its parts, ValueError when it is not one."""
    host, sep, port = addr.rpartition(":")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"expected host:port, got {addr!r}")
    return host, int(port)


def _number(env: Mapping[str, str], key: str, default: str, low: float, high: float) -> float:
    """A float from the environment that lies in (low, high], ValueError otherwise."""
    raw = env.get(key, default)
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{key} must be a number, got {raw!r}") from None
    if not low < value <= high:
        raise ValueError(f"{key} must be above {low:g} and at most {high:g}, got {raw!r}")
    return value


@dataclass(frozen=True)
class Config:
    """The environment's knobs; the defaults are robot zero on the GPU host."""

    robot: str
    http_addr: tuple[str, int]
    hz: float
    budget_s: float
    stale_after_s: float
    zenoh_router: tuple[str, int]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        """The configuration an environment describes, ValueError naming the variable that is wrong."""
        return cls(robot=valid_robot_id(env.get("ROBOT_ID", "r00")),
                   http_addr=parse_addr(env.get("RENDER_HTTP_ADDR", "10.20.0.1:9702")),
                   hz=_number(env, "FRAME_HZ", "15", 0, 60),
                   budget_s=_number(env, "FETCH_TIMEOUT_S", "1.0", 0, 10),
                   stale_after_s=_number(env, "STALE_AFTER_S", "2.0", 0, 600),
                   zenoh_router=parse_addr(env.get("ZENOH_ROUTER", "127.0.0.1:7447")))

    def url(self, camera: Camera) -> str:
        """Where the renderer serves this robot's latest picture of a camera."""
        return f"http://{self.http_addr[0]}:{self.http_addr[1]}/robot/{self.robot}/{camera.name}.jpg"


def zenoh_client_config(router: tuple[str, int]) -> str:
    """The rmw_zenoh session file of a client of the sim's router, as the runtime image's entrypoint writes it."""
    return f'{{\n  mode: "client",\n  connect: {{\n    endpoints: ["tcp/{router[0]}:{router[1]}"]\n  }}\n}}\n'


def write_zenoh_config(router: tuple[str, int], path: Path) -> dict[str, str]:
    """Write the session file and return the environment rmw_zenoh needs to use it without a router of its own."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(zenoh_client_config(router))
    return {"ZENOH_SESSION_CONFIG_URI": str(path), "RMW_ZENOH_CONFIG_FILE": str(path),
            "RMW_ZENOH_ROUTER_CHECK_ATTEMPTS": "0"}


def _receive(sock: socket.socket, left: Callable[[], float], size: int) -> bytes:
    """One recv that cannot outlast the fetch's budget."""
    sock.settimeout(left())
    return sock.recv(size)


def fetch_jpeg(url: str, budget_s: float) -> bytes:
    """One JPEG over plain HTTP within budget_s of wall time, connect to last byte; FetchError with the reason otherwise."""
    # A socket of its own rather than urllib: a timeout there is per read, so a server that sends a byte now and
    # then holds the caller for ever, and the caller is the bridge's only thread. Here every wait is given what is
    # left of one deadline. No proxy, no redirect, no keep-alive: the renderer answers HTTP/1.0 and closes.
    parts = urlsplit(url)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    if parts.scheme != "http" or not parts.hostname or any(c <= " " or c == "\x7f" for c in path):
        raise FetchError(f"not a plain http url: {url!r}")
    port = parts.port or 80
    deadline = time.monotonic() + budget_s

    def left() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError(f"no complete answer within {budget_s:g} s")
        return remaining

    try:
        with socket.create_connection((parts.hostname, port), timeout=left()) as sock:
            sock.settimeout(left())
            sock.sendall(f"GET {path} HTTP/1.0\r\nHost: {parts.hostname}:{port}\r\nConnection: close\r\n\r\n".encode("ascii"))
            received = bytearray()
            while b"\r\n\r\n" not in received and len(received) <= MAX_HEAD_BYTES:
                chunk = _receive(sock, left, 4096)
                if not chunk:
                    raise FetchError("the connection closed before the headers ended")
                received += chunk
            head, _, body = bytes(received).partition(b"\r\n\r\n")
            if len(head) > MAX_HEAD_BYTES:
                raise FetchError(f"more than {MAX_HEAD_BYTES} bytes of headers")
            status = _STATUS_LINE.match(head)
            if not status:
                raise FetchError("the answer is not HTTP")
            headers = {name.strip().lower(): value.strip() for name, _, value in
                       (line.partition(b":") for line in head.split(b"\r\n")[1:])}
            code = int(status.group(1))
            if code != 200:
                # the renderer says why in a line of text: worth one more read, within the same budget
                if not body:
                    body = _receive(sock, left, 200)
                said = body[:200].decode("utf-8", "replace").strip().splitlines()
                raise FetchError(f"{code} {said[0] if said else (status.group(2) or b'').decode('ascii', 'replace')}")
            if b"chunked" in headers.get(b"transfer-encoding", b"").lower():
                raise FetchError("a chunked answer, which the renderer does not send")
            declared = headers.get(b"content-length")
            if declared is not None and (not declared.isdigit() or int(declared) > MAX_JPEG_BYTES):
                raise FetchError(f"Content-Length {declared[:20].decode('ascii', 'replace')}: more than {MAX_JPEG_BYTES} bytes, or not a number")
            want = int(declared) if declared is not None else MAX_JPEG_BYTES + 1
            data = bytearray(body[:want])
            while len(data) < want:
                chunk = _receive(sock, left, min(65536, want - len(data)))
                if not chunk:
                    break
                data += chunk
    except OSError as err:   # refused, unreachable, reset, and the socket's own timeout
        raise FetchError(f"no complete answer within {budget_s:g} s" if isinstance(err, TimeoutError) else str(err)) from None
    if declared is not None and len(data) < want:
        raise FetchError(f"the connection closed after {len(data)} of {want} bytes")
    if len(data) > MAX_JPEG_BYTES:
        raise FetchError(f"more than {MAX_JPEG_BYTES} bytes")
    if not data.startswith(b"\xff\xd8"):
        raise FetchError("the answer is not a JPEG")
    return bytes(data)


def jpeg_size(data: bytes) -> tuple[int, int]:
    """Width and height a JPEG declares in its frame header, read without decoding; ValueError when there is none."""
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG: no start marker")
    at = 2
    while at + 4 <= len(data):
        if data[at] != 0xFF:
            raise ValueError("not a JPEG: a segment does not start with a marker")
        marker = data[at + 1]
        if marker == 0xFF:          # fill byte
            at += 1
            continue
        if marker in (0x01, *range(0xD0, 0xDA)):   # markers that stand alone; 0xD9/0xDA end the headers
            if marker in (0xD9, 0xDA):
                break
            at += 2
            continue
        length = int.from_bytes(data[at + 2:at + 4], "big")
        if length < 2:
            raise ValueError("not a JPEG: a segment shorter than its own length field")
        # the frame headers: baseline, progressive and the rest, but not the tables that share their range
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if length < 7 or at + 9 > len(data):
                raise ValueError("not a JPEG: a truncated frame header")
            return int.from_bytes(data[at + 7:at + 9], "big"), int.from_bytes(data[at + 5:at + 7], "big")
        at += 2 + length
    raise ValueError("not a JPEG: no frame header")


# The decoders are where bytes from outside meet a C library. Whatever either raises on bad input - OpenCV's own
# error type, Pillow's bomb and syntax errors, a MemoryError - is a picture that could not be had, never the
# bridge's end: hence the broad catch, here and nowhere else in this module.
def _cv2_decoder():
    import cv2

    def decode(data: bytes) -> np.ndarray:
        try:
            bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        except Exception as err:  # noqa: BLE001 - see above: the decoder boundary
            raise ValueError(f"not a decodable JPEG: {err}") from None
        if bgr is None:
            raise ValueError("not a decodable JPEG")
        return bgr[..., ::-1]
    return decode


def _pillow_decoder():
    from PIL import Image

    def decode(data: bytes) -> np.ndarray:
        try:
            return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
        except Exception as err:  # noqa: BLE001 - see above: the decoder boundary
            raise ValueError(f"not a decodable JPEG: {err}") from None
    return decode


_DECODERS = {"cv2": _cv2_decoder, "pillow": _pillow_decoder}
_loaded: dict[str, object] = {}


def decode_jpeg(data: bytes, library: str | None = None) -> np.ndarray:
    """RGB pixels (height, width, 3) of a JPEG, by OpenCV (what the sim image has) or else Pillow; ValueError when it is too big or does not decode."""
    # The size is read from the header first: a few hundred bytes can declare 65535 x 65535, and a decoder would
    # try to allocate that before it finds the file is a lie.
    width, height = jpeg_size(data)
    if width * height == 0 or width * height > MAX_PIXELS:
        raise ValueError(f"a {width} x {height} picture: more than {MAX_PIXELS} pixels, or none")
    for name in (library,) if library else tuple(_DECODERS):
        if name not in _loaded:
            try:
                _loaded[name] = _DECODERS[name]()
            except ImportError:
                _loaded[name] = None
        if _loaded[name] is not None:
            pixels = _loaded[name](data)
            if pixels.shape[:2] != (height, width):
                raise ValueError(f"decoded to {pixels.shape[1]} x {pixels.shape[0]}, the header said {width} x {height}")
            return pixels
    raise RuntimeError(f"no JPEG decoder: neither of {', '.join(_DECODERS)} can be imported")


def fit_frame(picture: np.ndarray, width: int = FRAME_WIDTH, height: int = FRAME_HEIGHT) -> np.ndarray:
    """The picture centred in a black frame, one scale for both axes: padded (or cropped) left and right, never stretched."""
    if picture.ndim != 3 or picture.shape[2] != 3 or picture.dtype != np.uint8 or 0 in picture.shape:
        raise ValueError(f"expected uint8 pixels (height, width, 3), got {picture.dtype} {picture.shape}")
    h, w = picture.shape[:2]
    if h != height:
        # A renderer set to another size keeps its vertical field of view, so height is what is matched. Nearest
        # neighbour at pixel centres, the same scale across as down.
        scaled_w = max(1, round(w * height / h))
        rows = np.minimum(((np.arange(height) + 0.5) * h / height).astype(np.int64), h - 1)
        cols = np.minimum(((np.arange(scaled_w) + 0.5) * w / scaled_w).astype(np.int64), w - 1)
        picture, w = picture[rows][:, cols], scaled_w
    frame = np.zeros((height, width, 3), np.uint8)
    if w <= width:
        left = (width - w) // 2
        frame[:, left:left + w] = picture
    else:
        left = (w - width) // 2
        frame[:] = picture[:, left:left + width]
    return frame


class Stamper:
    """Wall-clock stamps in nanoseconds that never repeat or step back."""

    # Wall time, not sim time: the policy's client runs on wall time, keeps only a frame stamped at or after the
    # last one it took, and throws its buffer away when a stamp is ahead of its own clock. On one host a wall
    # stamp is never ahead, and it keeps rising across a restart of this bridge.

    def __init__(self) -> None:
        self._last = 0

    def next(self, now_ns: int) -> int:
        """The stamp for a frame published now."""
        self._last = max(int(now_ns), self._last + 1)
        return self._last


class Freshness:
    """Whether every camera's pictures are arriving; it speaks only when that changes."""

    # A picture is published once, when it has just been fetched; an old one is never sent again. With the
    # renderer away the topics therefore go quiet, and the policy holds the last frame it got (the contract's
    # "hold"), as it would with a camera that stopped.

    REMIND_S = 30.0

    def __init__(self, cameras: tuple[str, ...], stale_after_s: float, now: float) -> None:
        self._stale_after_s = stale_after_s
        self._last_good: dict[str, float | None] = {camera: None for camera in cameras}
        self._live = {camera: False for camera in cameras}
        self._speak_at = {camera: now + stale_after_s for camera in cameras}

    def good(self, camera: str, now: float) -> str | None:
        """Note a fetched picture; the line to log when this camera has just come alive."""
        was_live = self._live[camera]
        self._last_good[camera], self._live[camera] = now, True
        return None if was_live else f"{camera}: pictures are arriving"

    def bad(self, camera: str, now: float, reason: str) -> str | None:
        """Note a failed fetch; the line to log when this camera has just gone stale, and now and then while it stays away."""
        last = self._last_good[camera]
        if self._live[camera]:
            if now - last < self._stale_after_s:
                return None
            self._live[camera] = False
            self._speak_at[camera] = now + self.REMIND_S
            return f"{camera}: no picture for {now - last:.1f} s ({reason}) - publishing nothing until one arrives"
        if now < self._speak_at[camera]:
            return None
        self._speak_at[camera] = now + self.REMIND_S
        return f"{camera}: still no picture ({reason})" if last is not None else f"{camera}: no picture yet ({reason})"

    def live(self) -> bool:
        """True while every camera is delivering."""
        return all(self._live.values())
