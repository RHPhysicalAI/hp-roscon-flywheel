# This project was developed with assistance from AI tools.
"""The exporter's file and socket edges: bounded reads, a renderer that is away or misbehaves, and what /metrics serves."""
import json
import os
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import exporter
import pytest

LOSS = ("Training:   2%|          | 200/9000 [00:22<14:33, 10.08step/s]INFO 2026-09-20 22:06:17 ot_train.py:451 "
        "step:200 smpl:2K ep:1 epch:0.01 loss:0.129 grdn:7.782 lr:1.0e-05 updt_s:0.092 data_s:0.008\n")


def serve(handler_class):
    """A server on a free loopback port, in a thread; returns it and its base URL."""
    server = HTTPServer(("127.0.0.1", 0), handler_class)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def answering(body: bytes, code: int = 200, delay_s: float = 0.0):
    """A handler class that answers every GET with this body."""

    class Handler(BaseHTTPRequestHandler):
        """One canned answer."""

        def do_GET(self):
            """Send the canned answer."""
            time.sleep(delay_s)
            self.send_response(code)
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, format, *args):
            """Quiet."""

    return Handler


@pytest.fixture
def train_dir(tmp_path):
    """A tenant output directory with two rounds, a ledger and the things that are not rounds."""
    old = tmp_path / "round-000024-v2-lr3e-5-s2000"
    new = tmp_path / "round-000025-teacher-lr1e-5-s1000"
    for d in (old, new, tmp_path / "cache"):
        d.mkdir()
    (old / "train.log").write_text("step:9K loss:0.059 updt_s:0.092 data_s:0.009\n")
    (new / "train.log").write_text("x" * 100_000 + "\n" + LOSS)
    (tmp_path / "rounds.jsonl").write_text(json.dumps({"round": 24, "ok": True, "last_loss": 0.059}) + "\n")
    return tmp_path


def test_tail_is_bounded(tmp_path):
    """Only the end of a large file is read, and the caller learns it was cut."""
    path = tmp_path / "big.log"
    path.write_bytes(b"a" * 5000 + b"END")
    text, mtime, cut = exporter.read_tail(str(path), 100)
    assert len(text) == 100 and text.endswith("END") and cut and mtime is not None
    assert exporter.read_tail(str(path), 10_000)[2] is False


def test_tail_of_what_is_not_a_readable_file(tmp_path):
    """A missing file, a directory and a link are all (None, None, False)."""
    (tmp_path / "real").write_text("x")
    (tmp_path / "link").symlink_to(tmp_path / "real")
    for name in ("missing", ".", "link"):
        assert exporter.read_tail(str(tmp_path / name), 100) == (None, None, False)


def test_round_names_leave_out_files_and_links(train_dir):
    """Only real directories are candidates for the current round."""
    (train_dir / "round-000099-v2-lr1e-5-s1000").symlink_to(train_dir / "cache")
    assert sorted(exporter.round_names(str(train_dir))) == ["cache", "round-000024-v2-lr3e-5-s2000",
                                                            "round-000025-teacher-lr1e-5-s1000"]
    assert exporter.round_names(str(train_dir / "nowhere")) == []


def test_status_from_a_renderer():
    """A JSON object comes back as a document."""
    server, url = serve(answering(b'{"render_fps": 15.0, "robots_live": 13}'))
    try:
        assert exporter.fetch_status(url + "/status") == {"render_fps": 15.0, "robots_live": 13}
    finally:
        server.shutdown()


@pytest.mark.parametrize("handler", [answering(b"not json"), answering(b"{}", code=503),
                                     answering(b"[" + b"1," * (exporter.STATUS_MAX_BYTES // 2) + b"1]"),
                                     answering(b"{}", delay_s=exporter.STATUS_TIMEOUT_S + 1.0)])
def test_status_from_a_renderer_that_misbehaves(handler):
    """Not JSON, not 200, too large and too slow all read as down."""
    server, url = serve(handler)
    try:
        assert exporter.fetch_status(url + "/status") is None
    finally:
        server.shutdown()


def dripping(payload: bytes, every_s: float):
    """A raw server on a free loopback port that sends this one byte at a time; returns its socket and URL."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def run() -> None:
        try:
            peer, _ = listener.accept()
            with peer:
                peer.recv(4096)     # the request: closing over unread bytes would be a reset, not an end
                for i in range(len(payload)):
                    peer.sendall(payload[i:i + 1])
                    time.sleep(every_s)
        except OSError:
            pass

    threading.Thread(target=run, daemon=True).start()
    return listener, f"http://127.0.0.1:{listener.getsockname()[1]}/status"


@pytest.mark.parametrize("payload", [b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{}",
                                     b"HTTP/1.0 200 OK\r\n\r\n" + b'{"render_fps": 15.0}' * 40])
def test_status_from_a_renderer_that_drips(payload):
    """Status line, headers or body a byte at a time, each inside any read timeout: down, at the wall-clock deadline."""
    listener, url = dripping(payload, every_s=0.2)
    t0 = time.monotonic()
    try:
        assert exporter.fetch_status(url) is None
    finally:
        listener.close()
    assert time.monotonic() - t0 < exporter.STATUS_TIMEOUT_S + 1.0


def test_status_without_a_content_length():
    """A body that ends with the connection is taken whole."""
    listener, url = dripping(b'HTTP/1.0 200 OK\r\n\r\n{"robots_live": 3}', every_s=0.0)
    try:
        assert exporter.fetch_status(url) == {"robots_live": 3}
    finally:
        listener.close()


def test_status_with_nobody_listening():
    """A refused connection is down, not an exception; so is a URL that is not http."""
    probe = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = probe.server_address[1]
    probe.server_close()
    assert exporter.fetch_status(f"http://127.0.0.1:{port}/status") is None
    assert exporter.fetch_status("file:///etc/hostname") is None


def test_metrics_end_to_end(train_dir):
    """The newest round's numbers and the renderer's, over HTTP, from a 100 kB log."""
    renderer, url = serve(answering(b'{"render_fps": 14.9, "fps_target": 15.0, "robots_live": 13, "robots_stale": 1, "batch_ms": 50}'))
    collector = exporter.Collector(str(train_dir), url + "/status", stale_s=120)
    server, base = serve(exporter.make_handler(collector))
    try:
        with urllib.request.urlopen(base + "/metrics", timeout=5) as resp:
            assert resp.headers["Content-Type"].startswith("text/plain; version=0.0.4")
            text = resp.read().decode()
        with urllib.request.urlopen(base + "/healthz", timeout=5) as resp:
            assert resp.status == 200
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/rounds.jsonl", timeout=5)
    finally:
        server.shutdown()
        renderer.shutdown()
    assert 'tenant_training_loss{round="25",start="teacher",lr="1e-5",seed="1000"} 0.129\n' in text
    assert "tenant_training_up 1.0\n" in text and "tenant_training_step 200.0\n" in text
    assert "tenant_training_rounds_finished 1.0\n" in text and "tenant_training_last_round_final_loss 0.059\n" in text
    assert "tenant_renderer_up 1.0\n" in text and "tenant_renderer_robots_live 13.0\n" in text


def get(base: str, path: str = "/metrics") -> int:
    """The status code of one GET."""
    try:
        with urllib.request.urlopen(base + path, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code


def stalled(base: str) -> socket.socket:
    """A client that sends a request line and no blank line, then says nothing."""
    host, port = base.rsplit("/", 1)[1].split(":")
    sock = socket.create_connection((host, int(port)), timeout=5)
    sock.sendall(b"GET /metrics HTTP/1.0\r\n")
    return sock


@pytest.fixture
def exporter_server(train_dir):
    """The exporter's own server class on a free loopback port."""
    collector = exporter.Collector(str(train_dir), "http://127.0.0.1:1/status", stale_s=120)
    server = exporter.Server(("127.0.0.1", 0), exporter.make_handler(collector))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_a_stalled_client_does_not_stop_the_next_one(exporter_server):
    """A request without its blank line holds one thread, and a second client still gets 200."""
    held = stalled(exporter_server)
    try:
        time.sleep(0.2)
        assert get(exporter_server) == 200
    finally:
        held.close()


def test_more_clients_than_threads_are_refused_not_queued(exporter_server):
    """With every handler thread held the next client gets 503 at once, and a freed thread serves again."""
    held = [stalled(exporter_server) for _ in range(exporter.MAX_HANDLERS)]
    try:
        time.sleep(0.3)
        t0 = time.monotonic()
        assert get(exporter_server) == 503
        assert time.monotonic() - t0 < 2.0
    finally:
        for sock in held:
            sock.close()
    time.sleep(0.5)
    assert get(exporter_server) == 200


def test_a_dripping_client_is_cut_at_the_wall_clock(train_dir, monkeypatch):
    """A byte inside every read timeout does not keep a thread: the request's own clock ends it."""
    monkeypatch.setattr(exporter, "REQUEST_DEADLINE_S", 0.6)
    collector = exporter.Collector(str(train_dir), "http://127.0.0.1:1/status", stale_s=120)
    server = exporter.Server(("127.0.0.1", 0), exporter.make_handler(collector))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    sock = socket.create_connection(server.server_address, timeout=5)
    t0 = time.monotonic()
    try:
        with pytest.raises(OSError):
            for _ in range(40):
                sock.sendall(b"X")
                time.sleep(0.1)
        assert time.monotonic() - t0 < 3.0
    finally:
        sock.close()
        server.shutdown()
        server.server_close()


def test_stopped_tenant_and_absent_renderer(train_dir):
    """An old log is up 0 without round numbers, and a renderer that is away is up 0."""
    log = train_dir / "round-000025-teacher-lr1e-5-s1000" / "train.log"
    os.utime(log, (time.time() - 600, time.time() - 600))
    text = exporter.Collector(str(train_dir), "http://127.0.0.1:1/status", stale_s=120).text()
    assert "tenant_training_up 0.0\n" in text and "tenant_training_loss" not in text
    assert "tenant_training_round 25.0\n" in text and "tenant_renderer_up 0.0\n" in text


def test_empty_directory_still_answers(tmp_path):
    """Before the tenant ever ran there is still a well-formed page."""
    text = exporter.Collector(str(tmp_path), "http://127.0.0.1:1/status", stale_s=120).text()
    assert "tenant_training_up 0.0\n" in text and "tenant_training_rounds_finished 0.0\n" in text


def test_collections_are_cached(train_dir):
    """Asking twice within the cache time reads the files once."""
    collector = exporter.Collector(str(train_dir), "http://127.0.0.1:1/status", stale_s=120)
    first = collector.text()
    (train_dir / "rounds.jsonl").write_text("")
    assert collector.text() is first


@pytest.mark.parametrize("addr", ["", "10.20.0.1", ":9401", "10.20.0.1:0", "10.20.0.1:99999", "10.20.0.1:http"])
def test_listen_addresses_that_are_not_one(addr):
    """The listen address has to be host:port with a real port."""
    with pytest.raises(ValueError):
        exporter.parse_addr(addr)


def test_listen_address():
    """host:port comes apart."""
    assert exporter.parse_addr("10.20.0.1:9401") == ("10.20.0.1", 9401)
