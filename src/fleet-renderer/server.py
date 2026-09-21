# This project was developed with assistance from AI tools.
"""The fleet's rendering tenant: robot states in over UDP, both cameras of every robot ray-traced in one batch, pictures out over HTTP."""

# docs/internal/FLEET-RENDER-CONTRACT.md is what this implements. Three kinds of thread:
#   state    one UDP listener; the latest state per robot, never a queue
#   render   the main thread: every 1/RENDER_FPS s of wall time, the live robots in one batch. A robot that has
#            gone quiet is not rendered again - its last picture is kept and shown greyed. With nobody live it
#            renders one world twice a second, so /healthz keeps saying something about the device.
#   http     one thread per client. Pictures are made here and not in the render loop, only when somebody asks
#            and once per new frame however many ask: the mosaic is one JPEG per tick while the wall is watched,
#            a robot's own stream costs nothing until it is opened.

import io
import json
import os
import signal
import socket
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image

import render_core as core

IDLE_RENDER_S = 0.5
HEALTHY_WITHIN_S = 2.0
STREAM_REPEAT_S = 1.0
LOG_EVERY_S = 30.0


@dataclass(frozen=True)
class Config:
    """The environment's knobs; the defaults are the contract's."""

    state_addr: tuple[str, int]
    http_addr: tuple[str, int]
    size: int
    fps: float
    shadows: bool
    device: str
    max_robots: int
    scene_dir: Path
    jpeg_quality: int
    max_streams: int

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        config = cls(state_addr=core.parse_addr(env("RENDER_STATE_ADDR", "10.20.0.1:9701")),
                     http_addr=core.parse_addr(env("RENDER_HTTP_ADDR", "10.20.0.1:9702")),
                     size=int(env("RENDER_SIZE", "480")), fps=float(env("RENDER_FPS", "15")),
                     shadows=core.parse_bool(env("RENDER_SHADOWS", "true")),
                     device=env("RENDER_DEVICE", "cuda:0"), max_robots=int(env("RENDER_MAX_ROBOTS", "24")),
                     scene_dir=Path(env("RENDER_SCENE_DIR", "/opt/fleet-renderer/scene")),
                     jpeg_quality=int(env("RENDER_JPEG_QUALITY", "80")),
                     max_streams=int(env("RENDER_MAX_STREAMS", "64")))
        if not (16 <= config.size <= 1920 and 0 < config.fps <= 120 and 1 <= config.max_robots <= 100):
            raise ValueError("RENDER_SIZE wants 16..1920, RENDER_FPS 0..120, RENDER_MAX_ROBOTS 1..100")
        return config


@dataclass(frozen=True)
class Picture:
    """A robot's last rendered pixels: its row of a batch's packed buffer, never written again."""

    packed: np.ndarray
    version: int
    greyed: bool


class Hub:
    """What the threads share. One condition guards all of it; waiters wake on every published tick."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.changed = threading.Condition()
        self.table = core.StateTable(max_robots=config.max_robots)
        self.dropped: Counter[str] = Counter()
        self.accepted = 0
        self.pictures: dict[str, Picture] = {}
        self.order: list[str] = []
        self.wall_version = 0
        self.phase = "warming up"
        self.device_name = ""
        self.started = time.monotonic()
        self.last_frame = 0.0
        self.batches: deque[tuple[float, float, int]] = deque(maxlen=64)   # finished at, seconds, worlds
        self.overruns = 0
        self.streams = 0
        self.jpegs: deque[float] = deque(maxlen=512)
        self.wall_ms: deque[float] = deque(maxlen=32)

    def datagram(self, data: bytes) -> None:
        """Count a datagram in or out."""
        try:
            state = core.parse_datagram(data)
        except core.DatagramError as err:
            reason = err.reason
        else:
            with self.changed:
                reason = self.table.accept(state, time.monotonic())
                if reason is None:
                    self.accepted += 1
                    return
        with self.changed:
            self.dropped[reason] += 1

    def publish(self, views: list[core.RobotView], live: list[core.RobotView], packed: np.ndarray | None) -> None:
        """After a tick: new pictures for the live robots, the quiet ones greyed, the gone ones forgotten."""
        with self.changed:
            before = (self.order, [self.pictures[r].greyed for r in self.order if r in self.pictures])
            for index, view in enumerate(live):
                old = self.pictures.get(view.robot)
                self.pictures[view.robot] = Picture(packed[index], (old.version if old else 0) + 1, False)
            known = {view.robot for view in views}
            rendered = {view.robot for view in live}
            for robot in list(self.pictures):
                picture = self.pictures[robot]
                if robot not in known:
                    del self.pictures[robot]
                elif robot not in rendered and not picture.greyed:
                    # its own copy: a view would keep the whole batch it came from alive
                    self.pictures[robot] = Picture(picture.packed.copy(), picture.version + 1, True)
            self.order = [view.robot for view in views]
            after = (self.order, [self.pictures[r].greyed for r in self.order if r in self.pictures])
            if live or before != after:
                self.wall_version += 1
            self.changed.notify_all()

    def wait_for(self, current, seen: int, timeout: float) -> int:
        """Block until current() differs from seen, or for timeout; returns current()."""
        with self.changed:
            self.changed.wait_for(lambda: current() != seen, timeout)
            return current()

    def status(self) -> dict:
        """The document behind GET /status."""
        now = time.monotonic()
        with self.changed:
            views = self.table.views(now)
            recent = [b for b in self.batches if now - b[0] <= 2.0 and b[2] > 0]
            status = {
                "phase": self.phase,
                "robots": [{"id": v.robot, "state": "live" if v.live else "stale", "age_s": round(v.age_s, 2),
                            "datagrams_per_s": round(v.datagrams_per_s, 1), "datagrams": v.datagrams} for v in views],
                "robots_live": sum(v.live for v in views),
                "robots_stale": sum(not v.live for v in views),
                "render_fps": round(len(recent) / 2.0, 1),
                "batch_ms": round(1e3 * sum(b[1] for b in recent) / len(recent), 2) if recent else None,
                "batch_worlds": recent[-1][2] if recent else 0,
                "idle_batch_ms": next((round(1e3 * b[1], 2) for b in reversed(self.batches) if b[2] == 0), None),
                "fps_target": self.config.fps,
                "render_size": self.config.size,
                "shadows": self.config.shadows,
                "device": self.device_name,
                "max_robots": self.config.max_robots,
                "datagrams_accepted": self.accepted,
                "dropped": {"total": sum(self.dropped.values()), **dict(sorted(self.dropped.items()))},
                "overruns": self.overruns,
                "streams": self.streams,
                "jpegs_per_s": round(sum(now - t <= 2.0 for t in self.jpegs) / 2.0, 1),
                "wall_ms": round(sum(self.wall_ms) / len(self.wall_ms), 1) if self.wall_ms else None,
                "last_frame_age_s": round(now - self.last_frame, 2) if self.last_frame else None,
                "uptime_s": round(now - self.started),
            }
        return status

    def healthy(self) -> bool:
        with self.changed:
            return bool(self.last_frame) and time.monotonic() - self.last_frame < HEALTHY_WITHIN_S


class Pictures:
    """JPEGs on demand: at most one encode in flight per stream, one per new frame however many watch."""

    def __init__(self, hub: Hub, scene: core.SceneMap, rgb_adr: list[int]) -> None:
        self.hub, self.scene, self.rgb_adr = hub, scene, rgb_adr
        self.lut = core.srgb_lut()
        self.locks: dict[tuple, threading.Lock] = {}
        self.cache: dict[tuple, tuple[int, bytes]] = {}
        self.guard = threading.Lock()

    def _lock(self, key: tuple) -> threading.Lock:
        with self.guard:
            return self.locks.setdefault(key, threading.Lock())

    def _encode(self, linear: np.ndarray) -> bytes:
        out = io.BytesIO()
        Image.fromarray(self.lut[linear]).save(out, "JPEG", quality=self.hub.config.jpeg_quality)
        with self.hub.changed:
            self.hub.jpegs.append(time.monotonic())
        return out.getvalue()

    def _camera(self, picture: Picture, camera: str) -> np.ndarray:
        size = self.hub.config.size
        return core.unpack_rgb(picture.packed, self.rgb_adr[self.scene.cameras[camera]], size, size)

    def _tile(self, picture: Picture, tile: int) -> np.ndarray:
        """The overhead camera at the mosaic's tile size, scaled while still linear."""
        size = self.hub.config.size
        if tile == size:
            return self._camera(picture, "static")
        start = self.rgb_adr[self.scene.cameras["static"]]
        # Pillow reads the packed pixels itself: a third cheaper than unpacking with numpy first, same pixels
        image = Image.frombuffer("RGB", (size, size), picture.packed[start: start + size * size], "raw", "BGRX", 0, 1)
        return np.asarray(image.resize((tile, tile), Image.BILINEAR))

    def wall(self) -> tuple[int, bytes]:
        """The mosaic of every robot's overhead camera at the current wall version."""
        with self._lock(("wall",)):
            with self.hub.changed:
                version, order = self.hub.wall_version, list(self.hub.order)
                pictures = [self.hub.pictures.get(robot) for robot in order]
            cached = self.cache.get(("wall",))
            if cached and cached[0] == version:
                return cached
            started = time.perf_counter()
            size = self.hub.config.size
            if not order:
                image = core.placeholder(2 * size, size, "waiting for robots")
            else:
                layout = core.mosaic_layout(len(order), size)
                tiles = []
                for picture in pictures:
                    tiles.append(None if picture is None else self._tile(picture, layout.tile))
                image = core.compose_mosaic(layout, tiles, order, [bool(p and p.greyed) for p in pictures])
            self.cache[("wall",)] = (version, self._encode(image))
            with self.hub.changed:
                self.hub.wall_ms.append(1e3 * (time.perf_counter() - started))
            return self.cache[("wall",)]

    def robot(self, robot: str, camera: str) -> tuple[int, bytes] | None:
        """One robot's camera at its current version; None when there is no picture of it."""
        key = (robot, camera)
        with self._lock(key):
            with self.hub.changed:
                picture = self.hub.pictures.get(robot)
            if picture is None:
                with self.guard:
                    self.cache.pop(key, None)
                return None
            cached = self.cache.get(key)
            if cached and cached[0] == picture.version:
                return cached
            image = self._camera(picture, camera)
            self.cache[key] = (picture.version, self._encode(core.grey_out(image) if picture.greyed else image))
            return self.cache[key]

    def robot_version(self, robot: str) -> int:
        picture = self.hub.pictures.get(robot)
        return picture.version if picture else -1


INDEX = b"""<!doctype html>
<!-- This project was developed with assistance from AI tools. -->
<html lang="en"><head><meta charset="utf-8"><title>fleet renderer</title>
<style>body{background:#111;color:#ddd;font:14px system-ui,sans-serif;margin:16px}img{max-width:100%;display:block}
table{border-collapse:collapse;margin-top:12px}td,th{padding:2px 12px 2px 0;text-align:left}a{color:#8cf}
.stale{color:#888}</style></head><body>
<img src="wall.mjpg" alt="every robot's overhead camera">
<p id="summary"></p><table id="robots"></table>
<script>
async function tick() {
  try {
    const s = await (await fetch('status')).json();
    document.getElementById('summary').textContent = `${s.phase} | ${s.device} | ${s.robots_live} live, ` +
      `${s.robots_stale} stale | ${s.render_fps} of ${s.fps_target} frames/s | ${s.batch_ms} ms a batch of ` +
      `${s.batch_worlds} at ${s.render_size} px | dropped ${s.dropped.total} | wall ${s.wall_ms} ms`;
    document.getElementById('robots').innerHTML = '<tr><th>robot</th><th>state</th><th>age s</th>' +
      '<th>datagrams/s</th><th>cameras</th></tr>' + s.robots.map(r => `<tr class="${r.state}"><td>${r.id}</td>` +
      `<td>${r.state}</td><td>${r.age_s}</td><td>${r.datagrams_per_s}</td><td><a href="robot/${r.id}/static.mjpg">` +
      `static</a> <a href="robot/${r.id}/wrist.mjpg">wrist</a></td></tr>`).join('');
  } catch (e) { document.getElementById('summary').textContent = 'no answer from /status'; }
}
tick(); setInterval(tick, 2000);
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    """The contract's paths. Read-only, no authentication."""

    server_version = "fleet-renderer"
    timeout = 10   # on every read and write: a client that stops reading a stream is let go
    hub: Hub
    pictures: Pictures | None = None
    stop: threading.Event

    def log_message(self, *args) -> None:
        pass

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, text: str) -> None:
        self._send(code, "text/plain; charset=utf-8", (text + "\n").encode())

    def do_GET(self) -> None:
        target = core.route(self.path)
        try:
            if target is None:
                self._text(404, "not a path of the fleet renderer: / /wall.mjpg /robot/<id>/<static|wrist>.<mjpg|jpg> "
                                "/status /healthz")
            elif target == ("index",):
                self._send(200, "text/html; charset=utf-8", INDEX)
            elif target == ("status",):
                self._send(200, "application/json", json.dumps(self.hub.status(), indent=1).encode())
            elif target == ("healthz",):
                healthy = self.hub.healthy()
                self._text(200 if healthy else 503, "ok" if healthy else f"no frame in the last {HEALTHY_WITHIN_S:g} s "
                                                                         f"({self.hub.phase})")
            elif self.pictures is None:
                self._text(503, f"no pictures yet: {self.hub.phase}")
            elif target[0] == "wall":
                self._picture(target[1], self.pictures.wall, lambda: self.hub.wall_version)
            else:
                _, robot, camera, ext = target
                self._picture(ext, lambda: self.pictures.robot(robot, camera),
                              lambda: self.pictures.robot_version(robot))
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def _picture(self, ext: str, make, current) -> None:
        """One JPEG, or a stream of them: a new part per new version, the last one again every second."""
        made = make()
        if made is None:
            return self._text(404, "no picture of that robot: it has not been heard from, or has left the wall")
        if ext == "jpg":
            return self._send(200, "image/jpeg", made[1])
        with self.hub.changed:
            if self.hub.streams >= self.hub.config.max_streams:
                full = True
            else:
                full, self.hub.streams = False, self.hub.streams + 1
        if full:
            return self._text(503, f"{self.hub.config.max_streams} streams are open already (RENDER_MAX_STREAMS)")
        try:
            self.send_response(200)
            self.send_header("Content-Type", core.MJPEG_CONTENT_TYPE)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while made is not None and not self.stop.is_set():
                self.wfile.write(core.mjpeg_part(made[1]))
                self.wfile.flush()
                self.hub.wait_for(current, made[0], STREAM_REPEAT_S)
                made = make()
        finally:
            with self.hub.changed:
                self.hub.streams -= 1


def listen_for_states(hub: Hub, sock: socket.socket, stop: threading.Event) -> None:
    """The UDP listener: every datagram is judged at once and only the latest state per robot is kept."""
    sock.settimeout(1.0)
    while not stop.is_set():
        try:
            # one byte more than the contract allows is enough to see that a datagram is too long
            data, _ = sock.recvfrom(core.MAX_DATAGRAM + 1)
        except TimeoutError:
            continue
        hub.datagram(data)


def render_loop(hub: Hub, stop: threading.Event) -> None:
    """Ticks on wall time; a tick that overruns is followed by the next one at once, never by a backlog."""
    from mjw_batch import BatchRenderer

    config = hub.config
    scene = core.SceneMap.from_json(json.loads((config.scene_dir / "scene_map.json").read_text()))
    started = time.monotonic()
    renderer = BatchRenderer(config.scene_dir / "scene.xml", config.max_robots, config.size, config.shadows,
                             config.device)
    if renderer.nq != scene.nq:
        raise RuntimeError(f"scene.xml has nq={renderer.nq}, scene_map.json says {scene.nq}: not from one build")
    rest = renderer.qpos[0].copy()
    renderer.render(1)   # compiles or loads every kernel before the first robot is drawn
    with hub.changed:
        hub.device_name, hub.phase, hub.last_frame = renderer.device_name, "rendering", time.monotonic()
    Handler.pictures = Pictures(hub, scene, renderer.rgb_adr)
    print(f"ready after {time.monotonic() - started:.1f} s on {renderer.device_name}: {config.max_robots} worlds, "
          f"{config.size} px, {config.fps:g} frames/s, shadows {config.shadows}", flush=True)

    period = 1.0 / config.fps
    next_tick = next_log = time.monotonic()
    while not stop.is_set():
        now = time.monotonic()
        with hub.changed:
            hub.table.expire(now)
            views = hub.table.views(now)
        live = [view for view in views if view.live]
        packed = None
        if live or now - hub.last_frame >= IDLE_RENDER_S:
            for index, view in enumerate(live):
                core.assemble_qpos(renderer.qpos[index], view.state, scene)
            if not live:
                renderer.qpos[0] = rest
            began = time.perf_counter()
            packed = renderer.render(max(1, len(live)))
            took = time.perf_counter() - began
            with hub.changed:
                hub.last_frame = time.monotonic()
                hub.batches.append((hub.last_frame, took, len(live)))
        hub.publish(views, live, packed)

        now = time.monotonic()
        if now >= next_log:
            s = hub.status()
            print(f"{s['robots_live']} live, {s['robots_stale']} stale | {s['render_fps']} frames/s, {s['batch_ms']} ms "
                  f"a batch | dropped {s['dropped']['total']} | {s['streams']} streams, {s['jpegs_per_s']} jpeg/s",
                  flush=True)
            next_log = now + LOG_EVERY_S
        next_tick += period
        if next_tick < now:
            with hub.changed:
                hub.overruns += 1
            next_tick = now
        stop.wait(next_tick - now)


def selftest(config: Config) -> None:
    """Every dependency once, small: bake check, a partial batch, a state drawn, a mosaic encoded."""
    from mjw_batch import BatchRenderer

    scene = core.SceneMap.from_json(json.loads((config.scene_dir / "scene_map.json").read_text()))
    renderer = BatchRenderer(config.scene_dir / "scene.xml", 2, 64, config.shadows, config.device)
    renderer.selftest()
    hub = Hub(Config(**{**config.__dict__, "size": 64, "max_robots": 2}))
    doc = {"v": 1, "robot": "r00", "t": 0.0, "q": dict.fromkeys(core.ARM_JOINTS, 0.2),
           "cubes": {name: [0.15, 0.1 * i, 0.41, 1, 0, 0, 0] for i, name in enumerate(core.CUBES)}}
    hub.datagram(json.dumps(doc).encode())
    views = hub.table.views(time.monotonic())
    core.assemble_qpos(renderer.qpos[0], views[0].state, scene)
    hub.publish(views, views, renderer.render(1))
    _, jpeg = Pictures(hub, scene, renderer.rgb_adr).wall()
    if not jpeg.startswith(b"\xff\xd8") or renderer.nq != scene.nq:
        raise RuntimeError("the mosaic is not a JPEG, or scene.xml and scene_map.json are not from one build")
    print(f"self-test passed on {renderer.device_name}: nq {renderer.nq}, cameras {renderer.camera_names}, "
          f"mosaic {len(jpeg)} bytes")


def main() -> None:
    """Start the listeners, render until SIGTERM. Any failure ends the process: the unit restarts it."""
    try:
        config = Config.from_env()
    except ValueError as err:
        sys.exit(f"fleet-renderer: {err}")
    if sys.argv[1:] == ["--selftest"]:
        return selftest(config)
    hub, stop = Hub(config), threading.Event()
    Handler.hub, Handler.stop = hub, stop
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    states = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    states.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    try:
        states.bind(config.state_addr)
        httpd = ThreadingHTTPServer(config.http_addr, Handler)
    except OSError as err:
        sys.exit(f"fleet-renderer: cannot listen on udp {config.state_addr[0]}:{config.state_addr[1]} and tcp "
                 f"{config.http_addr[0]}:{config.http_addr[1]} ({err}). RENDER_STATE_ADDR and RENDER_HTTP_ADDR name "
                 f"an address of this machine and free ports; 127.0.0.1 for a test")
    httpd.daemon_threads = True

    def guarded(work, *args) -> None:
        try:
            work(*args)
        except Exception as err:   # a listener that died silently would leave a wall of stale tiles
            print(f"fleet-renderer: {work.__name__} failed: {err!r}", flush=True)
            os._exit(1)

    threading.Thread(target=guarded, args=(listen_for_states, hub, states, stop), daemon=True).start()
    threading.Thread(target=guarded, args=(httpd.serve_forever,), daemon=True).start()
    print(f"states on udp {config.state_addr[0]}:{config.state_addr[1]}, pictures on http "
          f"{config.http_addr[0]}:{config.http_addr[1]}, device {config.device}: warming up", flush=True)
    try:
        render_loop(hub, stop)
    except Exception as err:
        with hub.changed:
            hub.phase = f"failed: {err!r}"
        print(f"fleet-renderer: the render loop failed: {err!r}", flush=True)
        sys.exit(1)
    httpd.shutdown()


if __name__ == "__main__":
    main()
