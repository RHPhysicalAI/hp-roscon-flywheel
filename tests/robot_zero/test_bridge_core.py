# This project was developed with assistance from AI tools.
"""Robot zero's frame bridge without ROS: the frame's geometry, the environment, the fetch, the stamps, staleness."""

import io
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bridge_core as core
import numpy as np
import pytest

PIL_Image = pytest.importorskip("PIL.Image")


def jpeg_of(pixels: np.ndarray) -> bytes:
    out = io.BytesIO()
    PIL_Image.fromarray(pixels).save(out, "JPEG", quality=95)
    return out.getvalue()


def quadrants(size: int = 480) -> np.ndarray:
    """Red, green, blue and white quarters: flat colours survive JPEG, and a swapped channel order shows."""
    picture = np.zeros((size, size, 3), np.uint8)
    half = size // 2
    picture[:half, :half] = (255, 0, 0)
    picture[:half, half:] = (0, 255, 0)
    picture[half:, :half] = (0, 0, 255)
    picture[half:, half:] = (255, 255, 255)
    return picture


def test_module_imports_no_ros():
    """The pure helpers load without rclpy or the message packages."""
    assert not {name.split(".")[0] for name in sys.modules} & {"rclpy", "sensor_msgs"}


def test_the_contract_the_policy_subscribes_to():
    """Both cameras, on the topics of the rosetta contract, as 640x480 rgb8."""
    assert {(c.name, c.topic) for c in core.CAMERAS} == {("static", "/static_camera/image_raw"),
                                                         ("wrist", "/wrist_camera/image_raw")}
    assert (core.FRAME_WIDTH, core.FRAME_HEIGHT, core.ENCODING) == (640, 480, "rgb8")


# ---- the frame ----

def test_a_square_picture_is_centred_and_padded_left_and_right():
    """480x480 lands in columns 80..559 untouched; the rest of the 640x480 frame is black."""
    picture = np.random.default_rng(0).integers(1, 256, (480, 480, 3), dtype=np.uint8)
    frame = core.fit_frame(picture)
    assert frame.shape == (480, 640, 3) and frame.dtype == np.uint8 and frame.flags["C_CONTIGUOUS"]
    assert np.array_equal(frame[:, 80:560], picture)
    assert not frame[:, :80].any() and not frame[:, 560:].any()


def test_the_picture_centre_is_the_frame_centre():
    """The renderer's principal point (240) lands on the 640-wide camera's (320), so nothing shifts sideways."""
    picture = np.zeros((480, 480, 3), np.uint8)
    picture[:, 239:241] = 255
    columns = np.flatnonzero(core.fit_frame(picture).any(axis=(0, 2)))
    assert list(columns) == [319, 320]


def test_never_stretched():
    """A circle stays a circle: as wide as it is high after fitting."""
    yy, xx = np.mgrid[:480, :480]
    picture = np.zeros((480, 480, 3), np.uint8)
    picture[(yy - 240) ** 2 + (xx - 240) ** 2 <= 100 ** 2] = 255
    lit = core.fit_frame(picture).any(axis=2)
    assert np.flatnonzero(lit.any(axis=0)).size == np.flatnonzero(lit.any(axis=1)).size == 201


def test_the_policys_own_squeeze_puts_pixels_where_training_had_them():
    """After the contract's nearest-neighbour squeeze to 480x480 the picture is the middle three quarters, as 480 of 640 columns were."""
    picture = np.full((480, 480, 3), 200, np.uint8)
    frame = core.fit_frame(picture)
    squeezed = frame[:, np.linspace(0, 639, 480).astype(np.int64)]   # rosetta's _nearest_resize along x
    lit = np.flatnonzero(squeezed.any(axis=(0, 2)))
    assert (lit[0], lit[-1], lit.size) == (60, 419, 360)


@pytest.mark.parametrize("size", [240, 384, 960])
def test_another_render_size_is_scaled_by_height_alone(size):
    """One scale for both axes: the height becomes 480 and the width follows it."""
    frame = core.fit_frame(quadrants(size))
    assert frame.shape == (480, 640, 3)
    assert np.flatnonzero(frame.any(axis=(0, 2))).size == 480
    assert tuple(frame[10, 90]) == (255, 0, 0) and tuple(frame[470, 550]) == (255, 255, 255)
    assert tuple(frame[10, 550]) == (0, 255, 0) and tuple(frame[470, 90]) == (0, 0, 255)


def test_a_wider_picture_is_cropped_not_squeezed():
    """Wider than 4:3 loses its sides evenly."""
    picture = np.zeros((480, 800, 3), np.uint8)
    picture[:, 80:720] = 9
    frame = core.fit_frame(picture)
    assert frame.shape == (480, 640, 3) and (frame == 9).all()


@pytest.mark.parametrize("bad", [np.zeros((480, 480), np.uint8), np.zeros((480, 480, 4), np.uint8),
                                 np.zeros((480, 480, 3), np.float32), np.zeros((0, 480, 3), np.uint8)])
def test_other_arrays_are_refused(bad):
    """Only uint8 (height, width, 3) is a picture."""
    with pytest.raises(ValueError):
        core.fit_frame(bad)


# ---- the JPEG ----

@pytest.mark.parametrize("library", ["cv2", "pillow"])
def test_decode_gives_rgb_in_that_order(library):
    """Red stays red with either library (OpenCV decodes to BGR)."""
    if library == "cv2":
        pytest.importorskip("cv2")
    pixels = core.decode_jpeg(jpeg_of(quadrants()), library)
    assert pixels.shape == (480, 480, 3) and pixels.dtype == np.uint8
    assert np.abs(pixels[100, 100].astype(int) - (255, 0, 0)).max() <= 3
    assert np.abs(pixels[400, 100].astype(int) - (0, 0, 255)).max() <= 3
    assert core.fit_frame(pixels).shape == (480, 640, 3)


@pytest.mark.parametrize("library", ["cv2", "pillow"])
def test_garbage_is_a_value_error(library):
    """A JPEG start marker and nothing behind it does not decode."""
    if library == "cv2":
        pytest.importorskip("cv2")
    with pytest.raises(ValueError):
        core.decode_jpeg(b"\xff\xd8 not a picture", library)


def declare(jpeg: bytes, width: int, height: int) -> bytes:
    """The same file with another size written into its frame header: what a decompression bomb is."""
    at = jpeg.index(b"\xff\xc0")
    return jpeg[:at + 5] + height.to_bytes(2, "big") + width.to_bytes(2, "big") + jpeg[at + 9:]


def test_the_declared_size_is_read_without_decoding():
    """Width and height come from the frame header."""
    assert core.jpeg_size(jpeg_of(quadrants())) == (480, 480)
    assert core.jpeg_size(jpeg_of(np.zeros((480, 640, 3), np.uint8))) == (640, 480)
    assert core.jpeg_size(declare(jpeg_of(quadrants()), 65535, 65535)) == (65535, 65535)


@pytest.mark.parametrize("data", [b"", b"GIF89a", b"\xff\xd8", b"\xff\xd8\xff\xe0\x00\x10" + b"x" * 14 + b"\xff\xd9",
                                  b"\xff\xd8\xff\xc0\x00\x11\x08", b"\xff\xd8\x00\x00", b"\xff\xd8\xff\xe0\x00\x00"])
def test_no_frame_header_is_a_value_error(data):
    """Not a JPEG, a JPEG cut short, or one with no frame header: refused, never an index error."""
    with pytest.raises(ValueError, match="not a JPEG"):
        core.jpeg_size(data)


@pytest.mark.parametrize("library", ["cv2", "pillow"])
def test_a_bomb_is_refused_before_any_decoder_sees_it(library, monkeypatch):
    """A few hundred bytes that declare 65535 x 65535 are a ValueError, and no decoder is called to find out."""
    if library == "cv2":
        pytest.importorskip("cv2")
    core.decode_jpeg(jpeg_of(quadrants()), library)   # the decoder is loaded, so that replacing it below is seen

    def must_not_run(_data):
        raise AssertionError("the decoder was handed a bomb")

    monkeypatch.setitem(core._loaded, library, must_not_run)
    with pytest.raises(ValueError, match="65535 x 65535"):
        core.decode_jpeg(declare(jpeg_of(quadrants(64)), 65535, 65535), library)


def test_the_pixel_limit_is_about_four_million():
    """2000 x 2000 is a picture, 2001 x 2000 is not; the renderer's are 480 x 480."""
    assert core.MAX_PIXELS == 4_000_000
    small = jpeg_of(np.zeros((8, 8, 3), np.uint8))
    with pytest.raises(ValueError, match="more than 4000000 pixels"):
        core.decode_jpeg(declare(small, 2001, 2000))
    with pytest.raises(ValueError, match="or none"):
        core.decode_jpeg(declare(small, 0, 480))


def test_a_decoder_that_returns_another_size_than_declared_is_refused(monkeypatch):
    """The header's size is what was checked against the limit, so it is also what the pixels must have."""
    core.decode_jpeg(jpeg_of(quadrants()), "pillow")
    monkeypatch.setitem(core._loaded, "pillow", lambda _data: np.zeros((10, 10, 3), np.uint8))
    with pytest.raises(ValueError, match="decoded to 10 x 10, the header said 480 x 480"):
        core.decode_jpeg(jpeg_of(quadrants()), "pillow")


@pytest.mark.parametrize("library", ["cv2", "pillow"])
def test_whatever_a_decoder_raises_is_a_value_error(library, monkeypatch):
    """OpenCV's own error type, Pillow's bomb error, a MemoryError: none of them leaves decode_jpeg as itself."""
    if library == "cv2":
        cv2 = pytest.importorskip("cv2")
        monkeypatch.setattr(cv2, "imdecode", lambda *a: (_ for _ in ()).throw(cv2.error("huge")))
    else:
        from PIL import Image
        monkeypatch.setattr(Image, "open", lambda *a: (_ for _ in ()).throw(Image.DecompressionBombError("huge")))
    monkeypatch.delitem(core._loaded, library, raising=False)
    with pytest.raises(ValueError, match="huge"):
        core.decode_jpeg(jpeg_of(quadrants(64)), library)
    monkeypatch.delitem(core._loaded, library, raising=False)


# ---- the environment ----

def test_defaults_are_robot_zero_on_the_gpu_host():
    """Nothing set: r00 from the renderer's hub-side address at its render rate, through the local router."""
    config = core.Config.from_env({})
    assert (config.robot, config.http_addr, config.hz) == ("r00", ("10.20.0.1", 9702), 15.0)
    assert (config.budget_s, config.stale_after_s, config.zenoh_router) == (1.0, 2.0, ("127.0.0.1", 7447))
    assert config.url(core.CAMERAS[0]) == "http://10.20.0.1:9702/robot/r00/static.jpg"
    assert config.url(core.CAMERAS[1]) == "http://10.20.0.1:9702/robot/r00/wrist.jpg"


def test_the_environment_overrides():
    """Every knob is read."""
    config = core.Config.from_env({"ROBOT_ID": "r07", "RENDER_HTTP_ADDR": "127.0.0.1:1234", "FRAME_HZ": "7.5",
                                   "FETCH_TIMEOUT_S": "0.25", "STALE_AFTER_S": "5", "ZENOH_ROUTER": "10.20.0.1:7448"})
    assert config == core.Config("r07", ("127.0.0.1", 1234), 7.5, 0.25, 5.0, ("10.20.0.1", 7448))


@pytest.mark.parametrize("env, names", [
    ({"ROBOT_ID": "robot0"}, "robot id"), ({"ROBOT_ID": "r0/../x"}, "robot id"),
    ({"RENDER_HTTP_ADDR": "10.20.0.1"}, "host:port"), ({"ZENOH_ROUTER": "7447"}, "host:port"),
    ({"FRAME_HZ": "0"}, "FRAME_HZ"), ({"FRAME_HZ": "61"}, "FRAME_HZ"), ({"FRAME_HZ": "fast"}, "FRAME_HZ"),
    ({"FRAME_HZ": "nan"}, "FRAME_HZ"), ({"FETCH_TIMEOUT_S": "-1"}, "FETCH_TIMEOUT_S"),
    ({"STALE_AFTER_S": "0"}, "STALE_AFTER_S"),
])
def test_a_wrong_value_is_named(env, names):
    """A bad setting stops the bridge with the variable's name, never a silent default."""
    with pytest.raises(ValueError, match=names):
        core.Config.from_env(env)


def test_zenoh_session_file(tmp_path):
    """A client of the named router, and the three variables rmw_zenoh reads."""
    path = tmp_path / "deep" / "session.json5"
    env = core.write_zenoh_config(("127.0.0.1", 7447), path)
    text = path.read_text()
    assert 'mode: "client"' in text and 'endpoints: ["tcp/127.0.0.1:7447"]' in text
    assert env == {"ZENOH_SESSION_CONFIG_URI": str(path), "RMW_ZENOH_CONFIG_FILE": str(path),
                   "RMW_ZENOH_ROUTER_CHECK_ATTEMPTS": "0"}


# ---- the fetch ----

@pytest.fixture
def renderer():
    """A stand-in for the renderer's HTTP side on the loopback address."""
    jpeg = jpeg_of(quadrants())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            answers = {"/robot/r00/static.jpg": (200, "image/jpeg", jpeg),
                       "/robot/r00/wrist.jpg": (200, "image/jpeg", jpeg),
                       "/robot/r05/static.jpg": (404, "text/plain", b"no picture of that robot: it has left\n"),
                       "/robot/r06/static.jpg": (503, "text/plain", b"no pictures yet: warming up\n"),
                       "/robot/r07/static.jpg": (200, "text/html", b"<html>hello</html>")}
            code, kind, body = answers.get(self.path, (404, "text/plain", b"not a path\n"))
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_fetch_decode_fit_end_to_end(renderer):
    """What the bridge does each tick, against a local server."""
    config = core.Config.from_env({"RENDER_HTTP_ADDR": renderer})
    for camera in core.CAMERAS:
        frame = core.fit_frame(core.decode_jpeg(core.fetch_jpeg(config.url(camera), 2.0)))
        assert frame.shape == (480, 640, 3)
        assert np.abs(frame[100, 180].astype(int) - (255, 0, 0)).max() <= 3 and not frame[100, 40].any()


@pytest.mark.parametrize("robot, reason", [("r05", "404 no picture of that robot"), ("r06", "503 no pictures yet"),
                                           ("r07", "not a JPEG")])
def test_the_renderers_refusals_become_reasons(renderer, robot, reason):
    """Not found, warming up and a wrong body each give a FetchError that says which."""
    config = core.Config.from_env({"RENDER_HTTP_ADDR": renderer, "ROBOT_ID": robot})
    with pytest.raises(core.FetchError, match=reason):
        core.fetch_jpeg(config.url(core.CAMERAS[0]), 2.0)


def test_nobody_listening_is_a_fetch_error(renderer):
    """A renderer that is away is a reason, not a crash."""
    port = int(renderer.rsplit(":", 1)[1])
    probe = __import__("socket").socket()
    probe.bind(("127.0.0.1", 0))
    free = probe.getsockname()[1]
    probe.close()
    assert free != port
    with pytest.raises(core.FetchError):
        core.fetch_jpeg(f"http://127.0.0.1:{free}/robot/r00/static.jpg", 0.5)


def test_a_proxy_in_the_environment_is_ignored(renderer, monkeypatch):
    """The renderer is on this host: http_proxy must not send the fetch elsewhere."""
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    config = core.Config.from_env({"RENDER_HTTP_ADDR": renderer})
    assert core.fetch_jpeg(config.url(core.CAMERAS[0]), 2.0).startswith(b"\xff\xd8")


def raw_server(respond):
    """One connection served by respond(conn) on the loopback address; returns host:port and the thread."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve() -> None:
        conn, _ = listener.accept()
        with conn:
            try:
                conn.recv(4096)
                respond(conn)
            except OSError:
                pass     # the bridge hung up first: that is the point of these tests
        listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return f"127.0.0.1:{listener.getsockname()[1]}", thread


def dribble(data: bytes, every_s: float):
    def respond(conn) -> None:
        for byte in data:
            conn.sendall(bytes([byte]))
            time.sleep(every_s)
    return respond


@pytest.mark.parametrize("what, respond", [
    ("the body", lambda conn: (conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 5000\r\n\r\n\xff\xd8"),
                               dribble(b"x" * 400, 0.05)(conn))),
    ("the headers", dribble(b"HTTP/1.0 200 OK\r\nContent-Type: image/jpeg\r\n" + b"X-Pad: y\r\n" * 40, 0.05)),
    ("nothing at all", lambda conn: time.sleep(3)),
])
def test_a_dribbling_renderer_costs_the_budget_and_no_more(what, respond):
    """A byte now and then keeps a per-read timeout happy for ever; the fetch has one deadline, connect to last byte."""
    addr, thread = raw_server(respond)
    started = time.monotonic()
    with pytest.raises(core.FetchError, match="no complete answer within 0.4 s"):
        core.fetch_jpeg(f"http://{addr}/robot/r00/static.jpg", 0.4)
    assert time.monotonic() - started < 1.0, f"dribbling {what} held the fetch"
    thread.join(timeout=5)


def test_an_oversized_answer_is_refused_by_its_content_length_before_it_is_read():
    """More than MAX_JPEG_BYTES declared: a FetchError at once, and the body is never waited for."""
    addr, thread = raw_server(lambda conn: (conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: %d\r\n\r\n\xff\xd8"
                                                         % (core.MAX_JPEG_BYTES + 1)), time.sleep(2)))
    started = time.monotonic()
    with pytest.raises(core.FetchError, match="Content-Length"):
        core.fetch_jpeg(f"http://{addr}/robot/r00/static.jpg", 5.0)
    assert time.monotonic() - started < 1.0
    thread.join(timeout=5)


def test_an_oversized_answer_without_a_content_length_is_cut_off(monkeypatch):
    """No length declared and more than the limit sent: refused, and no more than the limit plus one byte is kept."""
    monkeypatch.setattr(core, "MAX_JPEG_BYTES", 20_000)
    addr, thread = raw_server(lambda conn: conn.sendall(b"HTTP/1.0 200 OK\r\n\r\n\xff\xd8" + b"x" * 200_000))
    with pytest.raises(core.FetchError, match="more than 20000 bytes"):
        core.fetch_jpeg(f"http://{addr}/robot/r00/static.jpg", 5.0)
    thread.join(timeout=5)


@pytest.mark.parametrize("answer, reason", [
    (b"HTTP/1.0 200 OK\r\nContent-Length: 100\r\n\r\n\xff\xd8short", "closed after 7 of 100 bytes"),
    (b"HTTP/1.0 200 OK\r\nContent-Length: lots\r\n\r\n\xff\xd8", "not a number"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n\xff\xd8\r\n0\r\n\r\n", "chunked"),
    (b"SSH-2.0-OpenSSH_9.9\r\n\r\n", "not HTTP"),
    (b"HTTP/1.0 200 OK\r\n" + b"X-Pad: " + b"y" * 20_000 + b"\r\n\r\n", "bytes of headers"),
    (b"HTTP/1.0 200 OK\r\n", "closed before the headers ended"),
])
def test_answers_that_are_not_a_whole_picture(answer, reason):
    """Cut short, mislabelled, chunked, not HTTP, endless headers: each a FetchError that says which."""
    addr, thread = raw_server(lambda conn: conn.sendall(answer))
    with pytest.raises(core.FetchError, match=reason):
        core.fetch_jpeg(f"http://{addr}/robot/r00/static.jpg", 2.0)
    thread.join(timeout=5)


def test_a_redirect_is_not_followed(renderer):
    """302 to a picture that exists: a refusal, and the other address is never asked."""
    target = f"http://{renderer}/robot/r00/static.jpg"
    addr, thread = raw_server(lambda conn: conn.sendall(f"HTTP/1.0 302 Found\r\nLocation: {target}\r\n\r\n".encode()))
    with pytest.raises(core.FetchError, match="302"):
        core.fetch_jpeg(f"http://{addr}/robot/r00/static.jpg", 2.0)
    thread.join(timeout=5)


@pytest.mark.parametrize("url", ["https://127.0.0.1:1/robot/r00/static.jpg", "file:///etc/hostname", "http:///x.jpg",
                                 "http://127.0.0.1:1/robot/r00/static.jpg HTTP/1.0\r\nX: y", "ftp://127.0.0.1/x.jpg"])
def test_only_a_plain_http_url_is_fetched(url):
    """Another scheme, no host, or a path that would break out of the request line: refused before any socket."""
    with pytest.raises(core.FetchError, match="not a plain http url"):
        core.fetch_jpeg(url, 1.0)


# ---- the stamps ----

def test_stamps_follow_the_clock_and_never_repeat_or_step_back():
    """The policy drops a frame stamped before the last one: a clock that stalls or steps back still gives rising stamps."""
    stamper = core.Stamper()
    assert stamper.next(1_000) == 1_000
    assert stamper.next(2_000) == 2_000
    assert stamper.next(2_000) == 2_001
    assert stamper.next(500) == 2_002
    assert stamper.next(3_000) == 3_000


# ---- staleness ----

def test_coming_alive_is_said_once_per_camera():
    """The first picture of each camera is a log line; the following ones are not."""
    fresh = core.Freshness(("static", "wrist"), 2.0, now=0.0)
    assert not fresh.live()
    assert fresh.good("static", 0.1) == "static: pictures are arriving"
    assert not fresh.live()
    assert fresh.good("wrist", 0.1) == "wrist: pictures are arriving"
    assert fresh.live()
    assert fresh.good("static", 0.2) is None and fresh.good("wrist", 0.2) is None


def test_a_hiccup_is_not_stale():
    """Failures shorter than STALE_AFTER_S say nothing and leave the camera live."""
    fresh = core.Freshness(("static",), 2.0, now=0.0)
    fresh.good("static", 1.0)
    assert fresh.bad("static", 1.5, "timed out") is None
    assert fresh.bad("static", 2.9, "timed out") is None
    assert fresh.live()
    assert fresh.good("static", 3.0) is None


def test_going_stale_is_said_once_then_only_as_a_reminder():
    """Past STALE_AFTER_S: one line with the reason, silence, a reminder every 30 s, and one line when it is back."""
    fresh = core.Freshness(("static",), 2.0, now=0.0)
    fresh.good("static", 1.0)
    line = fresh.bad("static", 3.5, "Connection refused")
    assert line.startswith("static: no picture for 2.5 s (Connection refused)") and "publishing nothing" in line
    assert not fresh.live()
    assert fresh.bad("static", 4.0, "Connection refused") is None
    assert fresh.bad("static", 33.0, "Connection refused") is None
    assert fresh.bad("static", 33.6, "Connection refused") == "static: still no picture (Connection refused)"
    assert fresh.bad("static", 40.0, "Connection refused") is None
    assert fresh.good("static", 41.0) == "static: pictures are arriving"
    assert fresh.live()


def test_a_renderer_that_was_never_there_is_said_after_the_grace_and_then_as_a_reminder():
    """Waiting for the renderer's first picture is not an error per tick."""
    fresh = core.Freshness(("static",), 2.0, now=10.0)
    assert fresh.bad("static", 10.5, "404 no picture of that robot") is None
    assert fresh.bad("static", 12.0, "404 no picture of that robot") == "static: no picture yet (404 no picture of that robot)"
    assert fresh.bad("static", 20.0, "404 no picture of that robot") is None
    assert fresh.bad("static", 42.0, "503 warming up") == "static: no picture yet (503 warming up)"


def test_one_camera_away_is_not_live():
    """Live means both cameras."""
    fresh = core.Freshness(("static", "wrist"), 2.0, now=0.0)
    fresh.good("static", 0.0)
    fresh.good("wrist", 0.0)
    fresh.good("static", 5.0)
    assert fresh.bad("wrist", 5.0, "timed out") is not None
    assert not fresh.live()
