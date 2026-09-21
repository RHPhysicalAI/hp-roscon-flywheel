# This project was developed with assistance from AI tools.
"""Tenant metrics exporter: the training tenant's numbers from its own files and the rendering tenant's from /status, as Prometheus text."""

# Runs as flywheel/tenant-metrics.container on the GPU host: no GPU, no credentials, a read-only root, and the
# training tenant's output directory mounted read-only. Everything it takes in is bounded - a log's tail, the
# ledger's tail, a capped /status body under a deadline - and nothing it reads can end it: what cannot be read
# becomes an `up 0` or a missing series. Whoever reaches the port - the hub, the fleet's VMs - cannot hold it
# either: a few handler threads at most, each request cut at a wall-clock deadline however slowly it drips, and
# the one outbound fetch under a wall-clock deadline from its first byte to its last.

from __future__ import annotations

import os
import signal
import socket
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import metrics_core as core

LOG_TAIL_BYTES = 64 * 1024          # a loss line every 100 steps, a few kB of progress bar between two of them
LEDGER_TAIL_BYTES = 4 * 1024 * 1024  # about 300 bytes a round: years of rounds
STATUS_MAX_BYTES = 256 * 1024       # /status is a few kB with a full fleet
MAX_DIR_ENTRIES = 4096
STATUS_HEAD_BYTES = 16 * 1024      # status line and headers, on top of the body's cap
STATUS_TIMEOUT_S = 1.5              # wall clock, connect to last byte
CACHE_S = 2.0
MAX_HANDLERS = 8                    # requests in flight; one more is refused with 503
REQUEST_DEADLINE_S = 8.0            # wall clock for one request, longer than the hub's 5 s scrape timeout


def parse_addr(addr: str) -> tuple[str, int]:
    """Split 'host:port' into its parts, ValueError when it is not one."""
    host, sep, port = addr.rpartition(":")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"expected host:port, got {addr!r}")
    return host, int(port)


def read_tail(path: str, limit: int) -> tuple[str | None, float | None, bool]:
    """The last `limit` bytes of a regular file as text, its modification time, and whether the text starts mid-file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None, None, False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, None, False
        os.lseek(fd, max(0, info.st_size - limit), os.SEEK_SET)
        data = os.read(fd, limit)
    except OSError:
        return None, None, False
    finally:
        os.close(fd)
    return data.decode("utf-8", errors="replace"), info.st_mtime, info.st_size > limit


def round_names(root: str) -> list[str]:
    """Names of the real directories under the tenant's output, links left out."""
    names: list[str] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if len(names) >= MAX_DIR_ENTRIES:
                    break
                try:
                    if entry.is_dir(follow_symlinks=False):
                        names.append(entry.name)
                except OSError:
                    continue
    except OSError:
        pass
    return names


def fetch_status(url: str) -> object:
    """The renderer's /status document, None when it does not answer in time, answers too much, or not with JSON."""
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname:
        return None
    # HTTP/1.0 over a plain socket: no chunked answers, and every wait - connect, status line, headers, body - is
    # cut by the one deadline. A library's timeout is per read, and a peer that drips bytes never reaches it.
    deadline = time.monotonic() + STATUS_TIMEOUT_S
    request = f"GET {parts.path or '/'} HTTP/1.0\r\nHost: {parts.hostname}\r\nConnection: close\r\n\r\n".encode("ascii", "replace")
    raw = b""
    try:
        with socket.create_connection((parts.hostname, parts.port or 80), timeout=STATUS_TIMEOUT_S) as sock:
            sock.sendall(request)
            while not core.response_done(raw):
                left = deadline - time.monotonic()
                if left <= 0 or len(raw) > STATUS_MAX_BYTES + STATUS_HEAD_BYTES:
                    return None
                sock.settimeout(left)
                chunk = sock.recv(16384)
                if not chunk:
                    break
                raw += chunk
    except (OSError, ValueError):
        return None
    body = core.response_body(raw)
    return core.parse_status(body) if body is not None and len(body) <= STATUS_MAX_BYTES else None


class Collector:
    """Gathers every series, at most once per CACHE_S however often it is asked."""

    def __init__(self, train_dir: str, status_url: str, stale_s: float) -> None:
        self.train_dir = train_dir
        self.status_url = status_url
        self.stale_s = stale_s
        self._text = ""
        self._at = float("-inf")
        self._lock = threading.Lock()

    def training(self) -> list[core.Sample]:
        """The training tenant's series from its newest round's log and the ledger."""
        ledger_text, _, cut = read_tail(os.path.join(self.train_dir, "rounds.jsonl"), LEDGER_TAIL_BYTES)
        rows = (ledger_text or "").split("\n")
        if cut:
            rows = rows[1:]     # the tail began inside a line
        ledger = core.read_ledger(rows)
        newest = core.newest_round(round_names(self.train_dir))
        if newest is None:
            return core.training_samples(None, [], False, ledger)
        text, mtime, _ = read_tail(os.path.join(self.train_dir, newest[0], "train.log"), LOG_TAIL_BYTES)
        fresh = mtime is not None and time.time() - mtime <= self.stale_s
        return core.training_samples(newest[1], core.log_lines(text or ""), fresh, ledger)

    def text(self) -> str:
        """The exposition, from the cache while it is young; one collection at a time whoever asks."""
        with self._lock:
            now = time.monotonic()
            if now - self._at >= CACHE_S:
                samples = self.training() + core.renderer_samples(fetch_status(self.status_url))
                self._text, self._at = core.exposition(samples), now
            return self._text


def make_handler(collector: Collector) -> type[BaseHTTPRequestHandler]:
    """A request handler bound to this collector."""

    class Handler(BaseHTTPRequestHandler):
        """GET /metrics and /healthz; anything else is 404."""

        timeout = 5     # one read from a client that says nothing

        def setup(self) -> None:
            """Start the request's wall clock: a client that drips a byte inside every read timeout is cut too."""
            super().setup()
            self._cut = threading.Timer(REQUEST_DEADLINE_S, self._hang_up)
            self._cut.daemon = True
            self._cut.start()

        def _hang_up(self) -> None:
            """End the connection under the handler: its blocked read returns and the thread is free."""
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        def finish(self) -> None:
            """Stop the clock with the request."""
            self._cut.cancel()
            try:
                super().finish()
            except OSError:
                pass    # the peer, or the clock, closed it first

        def do_GET(self) -> None:
            """Answer one request."""
            path = self.path.split("?", 1)[0]
            if path == "/metrics":
                try:
                    body = collector.text().encode()
                except Exception as err:  # noqa: BLE001 - one bad collection must not end the exporter
                    print(f"collection failed: {err!r}", flush=True)
                    self.send_error(500)
                    return
                self._send(200, body, "text/plain; version=0.0.4; charset=utf-8")
            elif path == "/healthz":
                self._send(200, b"ok\n", "text/plain; charset=utf-8")
            else:
                self.send_error(404)

        def _send(self, code: int, body: bytes, kind: str) -> None:
            """A whole response with its length."""
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            """Quiet: a scrape every ten seconds is not news."""

    return Handler


class Server(ThreadingHTTPServer):
    """A thread per request, MAX_HANDLERS of them at most; the accepting thread never waits for a peer."""

    daemon_threads = True
    _BUSY = b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"

    def __init__(self, addr: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(addr, handler)
        self._slots = threading.BoundedSemaphore(MAX_HANDLERS)

    def process_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        """Hand the request to a thread when one is free, refuse it at once when none is."""
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(0)
                request.send(self._BUSY)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        """Give the slot back when the request ends, however it ends."""
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request: socket.socket, client_address: tuple[str, int]) -> None:
        """One line for a request that ended badly, not a traceback per dropped connection."""
        print(f"request from {client_address[0]} ended: {sys.exc_info()[1]!r}", flush=True)


def main() -> int:
    """Read the environment, bind the one address, serve until stopped."""
    try:
        host, port = parse_addr(os.environ.get("METRICS_ADDR", ""))
        stale_s = float(os.environ.get("TRAINING_STALE_S", "120"))
    except ValueError as err:
        print(f"METRICS_ADDR / TRAINING_STALE_S: {err}", flush=True)
        return 2
    collector = Collector(os.environ.get("TENANT_TRAIN_DIR", "/tenant-train"),
                          os.environ.get("RENDER_STATUS_URL", "http://10.20.0.1:9702/status"), stale_s)
    try:
        server = Server((host, port), make_handler(collector))
    except OSError as err:
        print(f"cannot listen on {host}:{port}: {err}", flush=True)
        return 1

    def stop(_signum: int, _frame: object) -> None:
        """Leave serve_forever."""
        raise SystemExit(0)

    # pid 1 of a container gets no default action for a signal: without these a stop would wait for the kill
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"listening on {host}:{port}; training from {collector.train_dir}, rendering from {collector.status_url}",
          flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
