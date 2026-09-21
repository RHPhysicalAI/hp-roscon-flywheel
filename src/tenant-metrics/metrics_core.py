# This project was developed with assistance from AI tools.
"""Pure helpers of the tenant metrics exporter: lerobot's log lines, the round ledger, the renderer's status, Prometheus text."""

# What the training tenant leaves behind (tools/host/fury/flywheel/training-tenant.sh) and how it reads:
#   round-000025-teacher-lr1e-5-s1000/train.log   everything lerobot printed, progress bar included. The bar
#       rewrites its line with carriage returns, and a loss line lands on the end of one:
#         Training:  12%|#1  | 1099/9000 [01:52<13:27,  9.78step/s]INFO ... step:1K smpl:9K ... loss:0.064 ...
#       lerobot abbreviates step: above 999 (1K for 1100), so the exact step is the bar's, never that field.
#   rounds.jsonl                                  one JSON line per finished round, failed ones included
# Nothing here opens a file or a socket, and nothing here raises on input it does not understand: a field that is
# not there is None, a line that is not one is skipped.

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

_ROUND_DIR = re.compile(r"round-(\d{6})-([A-Za-z0-9.]+)-lr([0-9][0-9.eE+-]*)-s(\d{1,9})")
_PROGRESS = re.compile(r"Training:\s*\d+%\|[^|\r\n]*\|\s*(\d{1,12})/(\d{1,12})\s*\[")
_NUMBER = r"(-?[0-9][0-9.]*(?:[eE][+-]?[0-9]+)?)"
_BIG = re.compile(r"(?:^|\s)step:([0-9][0-9.]*)([KMBTQ]?)(?=\s|$)")
_SUFFIX = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12, "Q": 1e15}
# a line longer than this is not one of lerobot's; it is cut, not parsed whole
MAX_LINE_CHARS = 2000


@dataclass(frozen=True)
class RoundDir:
    """One round directory's name, taken apart."""

    number: int
    start: str
    lr: str
    seed: str


@dataclass(frozen=True)
class LossSample:
    """One of lerobot's loss lines; every value is a mean over the steps since the line before."""

    loss: float | None
    learning_rate: float | None
    update_s: float | None
    data_s: float | None
    rough_step: float | None

    @property
    def steps_per_second(self) -> float | None:
        """Steps a second from the seconds a step took computing and waiting for data."""
        if self.update_s is None or self.data_s is None or self.update_s + self.data_s <= 0:
            return None
        return 1.0 / (self.update_s + self.data_s)


@dataclass(frozen=True)
class Ledger:
    """What rounds.jsonl says: how many rounds ended well or badly, and the last one that ended well."""

    finished: int = 0
    failed: int = 0
    last_round: int | None = None
    last_final_loss: float | None = None


@dataclass(frozen=True)
class Sample:
    """One line of the exposition."""

    name: str
    value: float
    labels: tuple[tuple[str, str], ...] = ()


def _number(value: object) -> float | None:
    """A finite float from a JSON number, None from anything else (booleans and strings included)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _parse_float(text: str) -> float | None:
    """A finite float from a log field's text, None when it is not one."""
    try:
        return _number(float(text))
    except ValueError:
        return None


def _field(line: str, key: str) -> float | None:
    """The value after 'key:' in a log line."""
    match = re.search(r"(?:^|\s)" + re.escape(key) + ":" + _NUMBER + r"(?=\s|$)", line)
    return _parse_float(match.group(1)) if match else None


def parse_round_dir(name: str) -> RoundDir | None:
    """A round directory's name as its parts, None for any other name."""
    match = _ROUND_DIR.fullmatch(name) if isinstance(name, str) else None
    if not match:
        return None
    return RoundDir(int(match.group(1)), match.group(2), match.group(3), match.group(4))


def newest_round(names: Iterable[str]) -> tuple[str, RoundDir] | None:
    """The directory name with the highest round number, and its parts."""
    best: tuple[str, RoundDir] | None = None
    for name in names:
        parsed = parse_round_dir(name)
        if parsed and (best is None or parsed.number > best[1].number):
            best = (name, parsed)
    return best


def log_lines(text: str) -> list[str]:
    """A log's text as lines, a carriage return ending one like a newline does."""
    return [row[:MAX_LINE_CHARS] for row in text.replace("\r", "\n").split("\n") if row]


def parse_loss_line(line: str) -> LossSample | None:
    """A loss line's values, None when the line is not one."""
    if "loss:" not in line or "step:" not in line:
        return None
    rough = _BIG.search(line)
    rough_step = _parse_float(rough.group(1)) if rough else None
    if rough and rough_step is not None:
        rough_step *= _SUFFIX[rough.group(2)]
    sample = LossSample(_field(line, "loss"), _field(line, "lr"), _field(line, "updt_s"), _field(line, "data_s"),
                        rough_step)
    return sample if sample.loss is not None or sample.update_s is not None else None


def last_loss_sample(lines: Sequence[str]) -> LossSample | None:
    """The newest loss line among these."""
    for line in reversed(lines):
        sample = parse_loss_line(line)
        if sample:
            return sample
    return None


def last_progress(lines: Sequence[str]) -> tuple[int, int] | None:
    """The progress bar's newest 'step of steps', which is exact where the loss line's step is not."""
    for line in reversed(lines):
        found = _PROGRESS.findall(line)
        if found:
            step, total = int(found[-1][0]), int(found[-1][1])
            if 0 <= step <= total and total > 0:
                return step, total
    return None


def read_ledger(lines: Iterable[str]) -> Ledger:
    """Count the ledger's rounds and keep the last one that ended well; a line that is not a round is skipped."""
    finished = failed = 0
    last_round: int | None = None
    last_loss: float | None = None
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("ok"), bool):
            continue
        if not row["ok"]:
            failed += 1
            continue
        finished += 1
        number = row.get("round")
        last_round = number if isinstance(number, int) and not isinstance(number, bool) else None
        last_loss = _number(row.get("last_loss"))
    return Ledger(finished, failed, last_round, last_loss)


def training_samples(round_dir: RoundDir | None, lines: Sequence[str], fresh: bool, ledger: Ledger) -> list[Sample]:
    """The training tenant's series: the ledger's always, the round's name while there is one, its numbers only while its log moves."""
    out = [Sample("tenant_training_up", 1.0 if fresh else 0.0),
           Sample("tenant_training_rounds_finished", float(ledger.finished)),
           Sample("tenant_training_rounds_failed", float(ledger.failed))]
    if ledger.last_round is not None:
        out.append(Sample("tenant_training_last_round_number", float(ledger.last_round)))
    if ledger.last_final_loss is not None:
        out.append(Sample("tenant_training_last_round_final_loss", ledger.last_final_loss))
    if round_dir is None:
        return out
    labels = (("round", str(round_dir.number)), ("start", round_dir.start), ("lr", round_dir.lr),
              ("seed", round_dir.seed))
    out.append(Sample("tenant_training_round", float(round_dir.number)))
    out.append(Sample("tenant_training_round_info", 1.0, labels))
    if not fresh:
        return out
    sample = last_loss_sample(lines)
    progress = last_progress(lines)
    if progress:
        out.append(Sample("tenant_training_step", float(progress[0])))
        out.append(Sample("tenant_training_round_steps", float(progress[1])))
    elif sample and sample.rough_step is not None:
        out.append(Sample("tenant_training_step", sample.rough_step))
    if sample:
        for name, value, tags in (("tenant_training_loss", sample.loss, labels),
                                  ("tenant_training_steps_per_second", sample.steps_per_second, ()),
                                  ("tenant_training_update_seconds", sample.update_s, ()),
                                  ("tenant_training_data_wait_seconds", sample.data_s, ()),
                                  ("tenant_training_learning_rate", sample.learning_rate, ())):
            if value is not None:
                out.append(Sample(name, value, tags))
    return out


# the renderer's /status field, the series it becomes, and what the value is multiplied by
_RENDERER_FIELDS = (("render_fps", "tenant_renderer_render_fps", 1.0),
                    ("fps_target", "tenant_renderer_fps_target", 1.0),
                    ("robots_live", "tenant_renderer_robots_live", 1.0),
                    ("robots_stale", "tenant_renderer_robots_stale", 1.0),
                    ("batch_ms", "tenant_renderer_batch_seconds", 0.001))


def renderer_samples(status: object) -> list[Sample]:
    """The rendering tenant's series from its /status document; anything but a JSON object means it is not up."""
    if not isinstance(status, Mapping):
        return [Sample("tenant_renderer_up", 0.0)]
    out = [Sample("tenant_renderer_up", 1.0)]
    for key, name, scale in _RENDERER_FIELDS:
        value = _number(status.get(key))
        if value is not None:
            out.append(Sample(name, value * scale))
    return out


_STATUS_LINE = re.compile(rb"HTTP/1\.[01] (\d{3})(?: [^\r\n]*)?\r?\n")
_CONTENT_LENGTH = re.compile(rb"(?im)^content-length:[ \t]*(\d{1,12})[ \t]*\r?$")


def _split_response(raw: bytes) -> tuple[int | None, int | None, bytes | None]:
    """An HTTP/1.x response so far as status, declared length and body; the body is None until the headers are in."""
    line = _STATUS_LINE.match(raw)
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        return (int(line.group(1)) if line else None), None, None
    length = _CONTENT_LENGTH.search(head)
    return (int(line.group(1)) if line else None), (int(length.group(1)) if length else None), body


def response_done(raw: bytes) -> bool:
    """True once nothing more is needed from the peer: not a 200, or every byte its Content-Length promised."""
    status, length, body = _split_response(raw)
    if body is None:
        return False
    return status != 200 or (length is not None and len(body) >= length)


def response_body(raw: bytes) -> bytes | None:
    """The body of a complete 200 response, cut to its Content-Length; None for anything else."""
    status, length, body = _split_response(raw)
    if status != 200 or body is None or (length is not None and len(body) < length):
        return None
    return body if length is None else body[:length]


def parse_status(body: bytes) -> object:
    """The renderer's /status body as a document, None when it is not JSON."""
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return None


_FAMILIES: dict[str, str] = {
    "tenant_training_up": "1 while the training tenant's current round log is being written, 0 otherwise",
    "tenant_training_rounds_finished": "Rounds in the ledger that ended well",
    "tenant_training_rounds_failed": "Rounds in the ledger that failed",
    "tenant_training_last_round_number": "Number of the last round that ended well",
    "tenant_training_last_round_final_loss": "Last loss of the last round that ended well",
    "tenant_training_round": "Number of the newest round directory",
    "tenant_training_round_info": "The newest round's configuration: starting checkpoint, learning rate, seed",
    "tenant_training_step": "Step the current round has reached",
    "tenant_training_round_steps": "Steps in the current round",
    "tenant_training_loss": "Loss, mean over the last logging interval",
    "tenant_training_steps_per_second": "1 / (update seconds + data wait seconds)",
    "tenant_training_update_seconds": "Seconds a step spent computing, mean over the last logging interval",
    "tenant_training_data_wait_seconds": "Seconds a step waited for data, mean over the last logging interval",
    "tenant_training_learning_rate": "Learning rate of the last logged step",
    "tenant_renderer_up": "1 when the rendering tenant answered /status with a JSON object, 0 otherwise",
    "tenant_renderer_render_fps": "Frames a second the renderer draws",
    "tenant_renderer_fps_target": "Frames a second the renderer is set to draw",
    "tenant_renderer_robots_live": "Robots whose state is arriving",
    "tenant_renderer_robots_stale": "Robots whose state stopped arriving",
    "tenant_renderer_batch_seconds": "Seconds one batch of all live robots took to render",
}


def _escape(value: str) -> str:
    """A label value as the text format wants it; a carriage return has no escape there and is dropped."""
    return value.replace("\r", "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def exposition(samples: Iterable[Sample]) -> str:
    """Prometheus text format 0.0.4: known families only, grouped, all gauges."""
    by_name: dict[str, list[Sample]] = {}
    for sample in samples:
        if sample.name in _FAMILIES and math.isfinite(sample.value):
            by_name.setdefault(sample.name, []).append(sample)
    rows: list[str] = []
    for name, group in by_name.items():
        rows.append(f"# HELP {name} {_FAMILIES[name]}")
        rows.append(f"# TYPE {name} gauge")
        for sample in group:
            tags = ",".join(f'{key}="{_escape(val)}"' for key, val in sample.labels)
            rows.append(f"{name}{{{tags}}} {sample.value!r}" if tags else f"{name} {sample.value!r}")
    return "\n".join(rows) + "\n"
