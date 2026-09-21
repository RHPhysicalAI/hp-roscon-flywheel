# This project was developed with assistance from AI tools.
"""The fleet renderer without a renderer: state contract, robot table, qpos assembly, colour, mosaic, MJPEG framing."""

# Everything here is numpy and the standard library, so it is tested without a GPU, Warp or MuJoCo.
# The contract is docs/internal/FLEET-RENDER-CONTRACT.md; time is always passed in, never read.

import json
import math
import re
from dataclasses import dataclass, field

import numpy as np

VERSION = 1
MAX_DATAGRAM = 1200
ARM_JOINTS = ("shoulder_pan_joint", "shoulder_lift_joint", "elbow_flex_joint",
              "wrist_flex_joint", "wrist_roll_joint", "gripper_joint")
CUBES = ("cube_small", "cube_medium", "cube_large")
CAMERAS = ("static", "wrist")
ROBOT_ID = re.compile(r"r[0-9]{2}\Z")   # not \d: that matches every script's digits
STALE_AFTER_S = 5.0
GONE_AFTER_S = 60.0
RESTART_GAP_S = 5.0
RATE_WINDOW_S = 1.0

MJPEG_BOUNDARY = "frame"
MJPEG_CONTENT_TYPE = f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}"
MOSAIC_MAX_WIDTH = 1920
MOSAIC_ASPECT = 16 / 9
BACKGROUND = 1    # linear; the sRGB table makes it 13


class DatagramError(ValueError):
    """A datagram the contract says to count and drop; reason names the counter."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RobotState:
    """One accepted datagram: Gazebo's joint values and the cubes' world poses (x y z qw qx qy qz)."""

    robot: str
    t: float
    q: dict[str, float]
    cubes: dict[str, tuple[float, ...]]


def _number(value: object) -> float | None:
    """A finite float, or None for anything else (bool is not a number here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _no_constants(name: str) -> float:
    raise ValueError(name)


def parse_datagram(data: bytes, joints: tuple[str, ...] = ARM_JOINTS, cubes: tuple[str, ...] = CUBES) -> RobotState:
    """Validate one datagram against the contract; raises DatagramError with the drop reason."""
    if len(data) > MAX_DATAGRAM:
        raise DatagramError("oversize")
    try:
        # NaN and Infinity are not JSON; python's parser would let them through
        doc = json.loads(data.decode("utf-8"), parse_constant=_no_constants)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise DatagramError("unparseable") from None
    if not isinstance(doc, dict):
        raise DatagramError("unparseable")
    version = doc.get("v")
    if isinstance(version, bool) or version != VERSION:
        raise DatagramError("version")
    robot = doc.get("robot")
    if not isinstance(robot, str) or not ROBOT_ID.match(robot):
        raise DatagramError("robot")
    t = _number(doc.get("t"))
    if t is None:
        raise DatagramError("t")

    q_in, q = doc.get("q"), {}
    if not isinstance(q_in, dict):
        raise DatagramError("q")
    for name in joints:
        value = _number(q_in.get(name))
        if value is None:
            raise DatagramError("q")
        q[name] = value

    cubes_in, poses = doc.get("cubes"), {}
    if not isinstance(cubes_in, dict):
        raise DatagramError("cubes")
    for name in cubes:
        pose = cubes_in.get(name)
        if not isinstance(pose, list) or len(pose) != 7:
            raise DatagramError("cubes")
        values = [_number(v) for v in pose]
        if None in values:
            raise DatagramError("cubes")
        norm = math.sqrt(sum(v * v for v in values[3:]))
        if norm < 1e-6:
            raise DatagramError("cubes")
        # order is passed through (w x y z already); only the length is made exactly one
        poses[name] = (*values[:3], *(v / norm for v in values[3:]))
    return RobotState(robot=robot, t=t, q=q, cubes=poses)


@dataclass
class _Entry:
    state: RobotState
    seen: float
    window_start: float
    window_count: int = 0
    rate: float = 0.0
    total: int = 0


@dataclass(frozen=True)
class RobotView:
    """A robot as the render loop and /status see it at one moment."""

    robot: str
    state: RobotState
    age_s: float
    live: bool
    datagrams_per_s: float
    datagrams: int


@dataclass
class StateTable:
    """Latest state per robot, with the contract's ordering rule and ageing. Not thread safe: the caller locks."""

    max_robots: int
    stale_after_s: float = STALE_AFTER_S
    gone_after_s: float = GONE_AFTER_S
    restart_gap_s: float = RESTART_GAP_S
    _robots: dict[str, _Entry] = field(default_factory=dict)

    def accept(self, state: RobotState, now: float) -> str | None:
        """Take a state as the robot's latest; returns None, or the reason it was dropped."""
        entry = self._robots.get(state.robot)
        if entry is None:
            if len(self._robots) >= self.max_robots:
                return "over_capacity"
            entry = self._robots[state.robot] = _Entry(state=state, seen=now, window_start=now)
        else:
            behind = entry.state.t - state.t
            # older than the last accepted one is a reordered datagram - unless it is so much older that the
            # world has restarted and counts from zero again
            if 0 < behind <= self.restart_gap_s:
                return "stale_t"
            entry.state, entry.seen = state, now
        entry.total += 1
        if now - entry.window_start >= RATE_WINDOW_S:
            entry.rate = entry.window_count / (now - entry.window_start)
            entry.window_start, entry.window_count = now, 0
        entry.window_count += 1
        return None

    def expire(self, now: float) -> list[str]:
        """Forget the robots not heard from for gone_after_s; returns their ids."""
        gone = [robot for robot, entry in self._robots.items() if now - entry.seen >= self.gone_after_s]
        for robot in gone:
            del self._robots[robot]
        return gone

    def views(self, now: float) -> list[RobotView]:
        """Every known robot in id order; live is False once it has been silent for stale_after_s."""
        out = []
        for robot in sorted(self._robots):
            entry = self._robots[robot]
            age = now - entry.seen
            # a window that never closed because the robot went quiet is not a rate
            rate = entry.rate if age < 2 * RATE_WINDOW_S else 0.0
            out.append(RobotView(robot=robot, state=entry.state, age_s=age, live=age < self.stale_after_s,
                                 datagrams_per_s=rate, datagrams=entry.total))
        return out


@dataclass(frozen=True)
class SceneMap:
    """Where a robot's state goes in MuJoCo's qpos, written next to scene.xml when the image is built."""

    nq: int
    joints: dict[str, tuple[int, float]]   # name -> (qpos address, offset added to Gazebo's value)
    cubes: dict[str, int]                  # name -> qpos address of the free joint (7 values)
    cameras: dict[str, int]                # "static" | "wrist" -> camera index in the model

    @classmethod
    def from_json(cls, doc: dict) -> "SceneMap":
        """Read scene_map.json and refuse a map that would write outside qpos or lacks a joint, cube or camera."""
        nq = int(doc["nq"])
        joints = {name: (int(j["qposadr"]), float(j.get("offset", 0.0))) for name, j in doc["joints"].items()}
        cubes = {name: int(c["qposadr"]) for name, c in doc["cubes"].items()}
        cameras = {name: int(index) for name, index in doc["cameras"].items()}
        missing = [n for n in ARM_JOINTS if n not in joints] + [n for n in CUBES if n not in cubes] + \
                  [n for n in CAMERAS if n not in cameras]
        if missing:
            raise ValueError(f"scene map lacks {missing}")
        if any(not 0 <= adr < nq for adr, _ in joints.values()) or any(not 0 <= adr <= nq - 7 for adr in cubes.values()):
            raise ValueError(f"scene map has an address outside qpos (nq={nq})")
        return cls(nq=nq, joints=joints, cubes=cubes, cameras=cameras)


def assemble_qpos(row: np.ndarray, state: RobotState, scene: SceneMap) -> None:
    """Write one robot's state into its row of the (nworld, nq) qpos array."""
    for name, (adr, offset) in scene.joints.items():
        row[adr] = state.q[name] + offset
    for name, adr in scene.cubes.items():
        row[adr: adr + 7] = state.cubes[name]


def srgb_lut() -> np.ndarray:
    """256-entry table from the renderer's linear 8-bit values to sRGB-encoded ones."""
    linear = np.arange(256, dtype=np.float64) / 255.0
    encoded = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * np.power(linear, 1 / 2.4) - 0.055)
    return np.rint(encoded * 255.0).astype(np.uint8)


def unpack_rgb(packed_row: np.ndarray, start: int, width: int, height: int) -> np.ndarray:
    """One camera of one world out of the render context's packed uint32 0xAARRGGBB buffer, as (H, W, 3) RGB."""
    bgra = packed_row[start: start + width * height].view(np.uint8).reshape(height, width, 4)
    return np.ascontiguousarray(bgra[..., 2::-1])   # little-endian host: the bytes are B G R A


def grey_out(image: np.ndarray) -> np.ndarray:
    """A stale robot's picture: luminance only, at half brightness."""
    weights = np.array([54, 183, 19], dtype=np.uint32)   # Rec. 709 luma, scaled to sum 256
    luma = ((image.astype(np.uint32) @ weights) >> 9).astype(np.uint8)
    return np.repeat(luma[..., None], 3, axis=2)


# 5x7 glyphs, rows top to bottom. Labels are white on black, which the sRGB table leaves alone, so they can be
# drawn before or after the conversion.
_GLYPHS = {
    "0": ".###. #...# #..## #.#.# ##..# #...# .###.", "1": "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
    "2": ".###. #...# ....# ...#. ..#.. .#... #####", "3": ".###. #...# ....# ..##. ....# #...# .###.",
    "4": "...#. ..##. .#.#. #..#. ##### ...#. ...#.", "5": "##### #.... ####. ....# ....# #...# .###.",
    "6": "..##. .#... #.... ####. #...# #...# .###.", "7": "##### ....# ...#. ..#.. .#... .#... .#...",
    "8": ".###. #...# #...# .###. #...# #...# .###.", "9": ".###. #...# #...# .#### ....# ...#. .##..",
    "a": "..... ..... .###. ....# .#### #...# .####", "b": "#.... #.... ####. #...# #...# #...# ####.",
    "c": "..... ..... .###. #.... #.... #...# .###.", "d": "....# ....# .#### #...# #...# #...# .####",
    "e": "..... ..... .###. #...# ##### #.... .###.", "f": "..##. .#..# .#... ###.. .#... .#... .#...",
    "g": "..... .#### #...# #...# .#### ....# .###.", "h": "#.... #.... #.##. ##..# #...# #...# #...#",
    "i": "..#.. ..... .##.. ..#.. ..#.. ..#.. .###.", "j": "...#. ..... ..##. ...#. ...#. #..#. .##..",
    "k": "#.... #.... #..#. #.#.. ##... #.#.. #..#.", "l": ".##.. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
    "m": "..... ..... ##.#. #.#.# #.#.# #...# #...#", "n": "..... ..... #.##. ##..# #...# #...# #...#",
    "o": "..... ..... .###. #...# #...# #...# .###.", "p": "..... ####. #...# #...# ####. #.... #....",
    "q": "..... .#### #...# #...# .#### ....# ....#", "r": "..... ..... #.##. ##..# #.... #.... #....",
    "s": "..... ..... .#### #.... .###. ....# ####.", "t": ".#... .#... ###.. .#... .#... .#..# ..##.",
    "u": "..... ..... #...# #...# #...# #..## .##.#", "v": "..... ..... #...# #...# #...# .#.#. ..#..",
    "w": "..... ..... #...# #...# #.#.# #.#.# .#.#.", "x": "..... ..... #...# .#.#. ..#.. .#.#. #...#",
    "y": "..... #...# #...# .#### ....# ....# .###.", "z": "..... ..... ##### ...#. ..#.. .#... #####",
    " ": "..... ..... ..... ..... ..... ..... .....", "-": "..... ..... ..... .###. ..... ..... .....",
    ".": "..... ..... ..... ..... ..... .##.. .##..", ":": "..... .##.. .##.. ..... .##.. .##.. .....",
}
_UNKNOWN = "##### ##### ##### ##### ##### ##### #####"
GLYPH_W, GLYPH_H = 5, 7


def text_bitmap(text: str, scale: int = 1) -> np.ndarray:
    """A boolean (7*scale, (6*len-1)*scale) picture of the text in the built-in font."""
    columns = []
    for char in text.lower():
        rows = _GLYPHS.get(char, _UNKNOWN).split()
        columns.append(np.array([[c == "#" for c in row] for row in rows], dtype=bool))
        columns.append(np.zeros((GLYPH_H, 1), dtype=bool))
    bitmap = np.hstack(columns[:-1]) if columns else np.zeros((GLYPH_H, 0), dtype=bool)
    return np.kron(bitmap, np.ones((scale, scale), dtype=bool))


def draw_label(image: np.ndarray, x: int, y: int, text: str, scale: int) -> None:
    """White text on a black box with its top-left corner at (x, y), clipped to the image."""
    bitmap = text_bitmap(text, scale)
    pad = scale
    box = np.zeros((bitmap.shape[0] + 2 * pad, bitmap.shape[1] + 2 * pad), dtype=bool)
    box[pad: pad + bitmap.shape[0], pad: pad + bitmap.shape[1]] = bitmap
    height = max(0, min(box.shape[0], image.shape[0] - y))
    width = max(0, min(box.shape[1], image.shape[1] - x))
    image[y: y + height, x: x + width] = np.where(box[:height, :width, None], 255, 0).astype(np.uint8)


@dataclass(frozen=True)
class MosaicLayout:
    """A grid of square tiles: cols x rows, tile pixels each."""

    cols: int
    rows: int
    tile: int

    @property
    def width(self) -> int:
        return self.cols * self.tile

    @property
    def height(self) -> int:
        return self.rows * self.tile

    @property
    def label_scale(self) -> int:
        return max(1, self.tile // 110)

    def cell(self, index: int) -> tuple[int, int]:
        """Top-left pixel of the index-th tile, filling rows left to right."""
        return (index % self.cols) * self.tile, (index // self.cols) * self.tile


def mosaic_layout(n: int, source: int, max_width: int = MOSAIC_MAX_WIDTH) -> MosaicLayout:
    """The grid for n square tiles: a full one when n divides into a screen-like shape, else close to a screen's with no empty row."""
    n = max(1, n)
    # 16 robots are 4 x 4, not 6 x 3 with two dark cells; a grid more than three times as wide as high is not a wall
    full = [(c, n // c) for c in range(1, n + 1) if n % c == 0 and 1 <= c / (n // c) <= 3]
    if full:
        cols, rows = min(full, key=lambda g: abs(g[0] / g[1] - MOSAIC_ASPECT))
    else:
        rows = math.ceil(n / math.ceil(math.sqrt(n * MOSAIC_ASPECT)))
        cols = math.ceil(n / rows)
    return MosaicLayout(cols=cols, rows=rows, tile=min(source, max_width // cols))


def compose_mosaic(layout: MosaicLayout, tiles: list[np.ndarray | None], labels: list[str],
                   greyed: list[bool]) -> np.ndarray:
    """Paste tiles (already layout.tile square, or None for a robot with no picture yet) and label them."""
    mosaic = np.full((layout.height, layout.width, 3), BACKGROUND, dtype=np.uint8)
    for index, (tile, label, grey) in enumerate(zip(tiles, labels, greyed)):
        x, y = layout.cell(index)
        if tile is not None:
            if tile.shape != (layout.tile, layout.tile, 3):
                raise ValueError(f"tile {index} is {tile.shape}, the layout wants {layout.tile} square")
            mosaic[y: y + layout.tile, x: x + layout.tile] = grey_out(tile) if grey else tile
        draw_label(mosaic, x, y, f"{label} stale" if grey else label, layout.label_scale)
    return mosaic


def placeholder(width: int, height: int, text: str) -> np.ndarray:
    """A dark picture with one centred line of text, for a wall with no robots on it."""
    image = np.full((height, width, 3), BACKGROUND, dtype=np.uint8)
    scale = max(1, width // 240)
    bitmap = text_bitmap(text, scale)
    draw_label(image, max(0, (width - bitmap.shape[1]) // 2), max(0, (height - bitmap.shape[0]) // 2), text, scale)
    return image


def mjpeg_part(jpeg: bytes) -> bytes:
    """One frame of a multipart/x-mixed-replace stream."""
    head = f"--{MJPEG_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
    return head.encode("ascii") + jpeg + b"\r\n"


def route(path: str) -> tuple[str, ...] | None:
    """The contract's paths: ("index",), ("status",), ("healthz",), ("wall", ext), ("robot", id, camera, ext)."""
    path = path.split("?", 1)[0]
    if path in ("/", "/status", "/healthz"):
        return (path.strip("/") or "index",)
    if path in ("/wall.mjpg", "/wall.jpg"):
        return ("wall", path.rsplit(".", 1)[1])
    match = re.fullmatch(r"/robot/(r[0-9]{2})/(static|wrist)\.(mjpg|jpg)", path)
    return ("robot", *match.groups()) if match else None


def parse_addr(value: str) -> tuple[str, int]:
    """host:port from an environment variable."""
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"not host:port: {value!r}")
    return host, int(port)


def parse_bool(value: str) -> bool:
    """An on/off environment variable."""
    if value.strip().lower() in ("1", "true", "yes", "on"):
        return True
    if value.strip().lower() in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"not a boolean: {value!r}")
