# This project was developed with assistance from AI tools.
"""MJPEG framing, the contract's paths, and the environment's address and boolean forms."""
import pytest
import render_core as core


def test_mjpeg_part_framing():
    """A part is the boundary, the type, the exact length, a blank line, the JPEG and CRLF."""
    jpeg = b"\xff\xd8" + b"x" * 10 + b"\xff\xd9"
    part = core.mjpeg_part(jpeg)
    head, _, body = part.partition(b"\r\n\r\n")
    assert head.split(b"\r\n") == [b"--frame", b"Content-Type: image/jpeg", b"Content-Length: 14"]
    assert body == jpeg + b"\r\n"


def test_mjpeg_content_type_names_the_same_boundary():
    """The response header's boundary is the one the parts use."""
    assert core.MJPEG_CONTENT_TYPE == "multipart/x-mixed-replace; boundary=frame"
    assert core.mjpeg_part(b"")[2:7].decode() == core.MJPEG_CONTENT_TYPE.rsplit("=", 1)[1]


def test_parts_concatenate_into_a_stream():
    """Two parts back to back start with a boundary each."""
    stream = core.mjpeg_part(b"one") + core.mjpeg_part(b"three")
    assert stream.count(b"--frame\r\n") == 2 and stream.endswith(b"three\r\n")


@pytest.mark.parametrize("path, target", [
    ("/", ("index",)), ("/status", ("status",)), ("/healthz", ("healthz",)),
    ("/wall.mjpg", ("wall", "mjpg")), ("/wall.jpg", ("wall", "jpg")), ("/wall.mjpg?t=1", ("wall", "mjpg")),
    ("/robot/r07/static.mjpg", ("robot", "r07", "static", "mjpg")),
    ("/robot/r00/wrist.jpg", ("robot", "r00", "wrist", "jpg")),
])
def test_contract_paths(path, target):
    """Every path of the contract is recognised."""
    assert core.route(path) == target


@pytest.mark.parametrize("path", ["/robot/r7/static.jpg", "/robot/r07/depth.jpg", "/robot/r07/static.png",
                                  "/robot/../status", "/robot/r07/static.jpg/x", "/wall", "/statusz", "//status"])
def test_other_paths_are_not_found(path):
    """Anything else is no route."""
    assert core.route(path) is None


def test_parse_addr():
    """host:port, and nothing else."""
    assert core.parse_addr("10.20.0.1:9701") == ("10.20.0.1", 9701)
    for bad in ("9701", "10.20.0.1", "10.20.0.1:", ":9701", "host:port", "h:0", "h:70000"):
        with pytest.raises(ValueError):
            core.parse_addr(bad)


def test_parse_bool():
    """The usual spellings of on and off; anything else is an error, not a silent default."""
    assert all(core.parse_bool(v) for v in ("1", "true", "True", "on", " yes "))
    assert not any(core.parse_bool(v) for v in ("0", "false", "OFF", "no"))
    with pytest.raises(ValueError):
        core.parse_bool("maybe")
