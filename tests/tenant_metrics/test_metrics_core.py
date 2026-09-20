# This project was developed with assistance from AI tools.
"""lerobot's log lines, the round ledger and the renderer's status as series, and what happens to input that is none of them."""
import json

import metrics_core as core
import pytest

# copied from a round's train.log on the host: the progress bar and the loss line share a line
EARLY = ("Training:   2%|▏         | 200/9000 [00:22<14:33, 10.08step/s]INFO 2026-09-20 22:06:17 ot_train.py:451 "
         "step:200 smpl:2K ep:1 epch:0.01 loss:0.129 grdn:7.782 lr:1.0e-05 updt_s:0.092 data_s:0.008")
LATE = ("Training:  12%|█▏        | 1099/9000 [01:52<13:27,  9.78step/s]INFO 2026-09-20 21:52:28 ot_train.py:451 "
        "step:1K smpl:9K ep:6 epch:0.04 loss:0.064 grdn:6.307 lr:3.0e-05 updt_s:0.094 data_s:0.005")
BAR = "Training:  13%|█▎        | 1150/9000 [01:57<13:20,  9.80step/s]"
ROUND = core.RoundDir(25, "teacher", "1e-5", "1000")


def by_name(samples):
    """Samples as a name -> value mapping."""
    return {s.name: s.value for s in samples}


def test_loss_line_fields():
    """Every field the dashboard shows comes out of a real line."""
    sample = core.parse_loss_line(EARLY)
    assert (sample.loss, sample.learning_rate, sample.update_s, sample.data_s) == (0.129, 1.0e-05, 0.092, 0.008)
    assert sample.steps_per_second == pytest.approx(10.0)


def test_abbreviated_step_is_only_a_rough_step():
    """Above 999 lerobot prints step:1K, which is kept apart from the exact step."""
    assert core.parse_loss_line(LATE).rough_step == 1000.0
    assert core.parse_loss_line(EARLY).rough_step == 200.0


@pytest.mark.parametrize("line", ["", "Training:   4%|    | 336/9000 [00:36<15:06,  9.56step/s]",
                                  "INFO End of training", "loss:", "step:x loss:y", "step:1K loss:nan updt_s:inf",
                                  "\x00\xff loss: step:", "loss:" * 5000])
def test_lines_that_are_not_loss_lines(line):
    """Anything else is None, never an exception."""
    assert core.parse_loss_line(line) is None


def test_non_finite_values_are_dropped_not_the_line():
    """A nan loss leaves the timing fields standing."""
    sample = core.parse_loss_line("step:300 loss:nan lr:1.0e-05 updt_s:0.1 data_s:0.1")
    assert sample.loss is None and sample.steps_per_second == pytest.approx(5.0)


def test_steps_per_second_needs_both_timings():
    """One timing alone gives no rate, and a zero sum does not divide."""
    assert core.parse_loss_line("step:1 loss:0.1 updt_s:0.1").steps_per_second is None
    assert core.parse_loss_line("step:1 loss:0.1 updt_s:0 data_s:0").steps_per_second is None


def test_carriage_returns_end_lines():
    """The progress bar's rewrites become lines of their own."""
    lines = core.log_lines(EARLY + "\r" + BAR + "\r\n" + LATE + "\n")
    assert lines == [EARLY, BAR, LATE]


def test_progress_is_the_newest_bar():
    """The exact step is the last bar's, wherever the last loss line is."""
    assert core.last_progress([EARLY, LATE, BAR]) == (1150, 9000)
    assert core.last_progress([BAR, LATE]) == (1099, 9000)


@pytest.mark.parametrize("line", ["Resolving data files: 100%|###| 161/161 [00:00<00:00]", "Training: 5%| 9/0 [",
                                  "Training:  50%|##| 9001/9000 [", "no bar here"])
def test_progress_ignores_other_bars_and_impossible_ones(line):
    """Only lerobot's training bar counts, and only a step within its total."""
    assert core.last_progress([line]) is None


@pytest.mark.parametrize("name, parts", [
    ("round-000025-teacher-lr1e-5-s1000", core.RoundDir(25, "teacher", "1e-5", "1000")),
    ("round-000102-v2-lr3e-5-s2000", core.RoundDir(102, "v2", "3e-5", "2000")),
])
def test_round_directory_names(name, parts):
    """The tenant's directory names come apart into round, start, rate and seed."""
    assert core.parse_round_dir(name) == parts


@pytest.mark.parametrize("name", ["cache", "rounds.jsonl", "round-25-teacher-lr1e-5-s1000", "round-000025-",
                                  "round-000025-te\"acher-lr1e-5-s1000", "round-000025-teacher-lr1e-5-s1000/x", ""])
def test_names_that_are_not_rounds(name):
    """Nothing but the exact pattern becomes a label."""
    assert core.parse_round_dir(name) is None


def test_newest_round_is_by_number_not_by_order():
    """The highest number wins among whatever else is in the directory."""
    names = ["round-000024-v2-lr3e-5-s2000", "cache", "round-000025-teacher-lr1e-5-s1000", "round-000023-v2-lr1e-5-s1000"]
    assert core.newest_round(names)[0] == "round-000025-teacher-lr1e-5-s1000"
    assert core.newest_round(["cache"]) is None


def test_ledger_counts_and_keeps_the_last_good_round():
    """Failed rounds are counted apart and never become the last finished round."""
    rows = [json.dumps({"round": 23, "ok": True, "last_loss": 0.088}),
            json.dumps({"round": 24, "ok": True, "last_loss": 0.059}),
            json.dumps({"round": 25, "ok": False, "last_loss": None})]
    assert core.read_ledger(rows) == core.Ledger(2, 1, 24, 0.059)


def test_ledger_survives_rubbish():
    """Lines that are not rounds are skipped; a null loss stays None."""
    rows = ["", "{", "[1, 2]", '{"ok": "yes"}', "\x00", '{"round": 7, "ok": true, "last_loss": null}',
            '{"round": true, "ok": true, "last_loss": "0.1"}']
    assert core.read_ledger(rows) == core.Ledger(2, 0, None, None)
    assert core.read_ledger([]) == core.Ledger()


def test_training_series_while_a_round_runs():
    """A moving log gives the round's numbers, the exact step from the bar."""
    got = by_name(core.training_samples(ROUND, [EARLY, LATE, BAR], True, core.Ledger(24, 0, 24, 0.059)))
    assert got["tenant_training_up"] == 1.0
    assert (got["tenant_training_step"], got["tenant_training_round_steps"]) == (1150.0, 9000.0)
    assert got["tenant_training_loss"] == 0.064 and got["tenant_training_learning_rate"] == 3.0e-05
    assert got["tenant_training_update_seconds"] == 0.094 and got["tenant_training_data_wait_seconds"] == 0.005
    assert got["tenant_training_steps_per_second"] == pytest.approx(1 / 0.099)
    assert (got["tenant_training_round"], got["tenant_training_rounds_finished"]) == (25.0, 24.0)
    assert got["tenant_training_last_round_final_loss"] == 0.059


def test_loss_carries_the_round_and_its_configuration():
    """The loss series is labelled, so each round draws its own line."""
    loss = [s for s in core.training_samples(ROUND, [EARLY], True, core.Ledger()) if s.name == "tenant_training_loss"]
    assert loss[0].labels == (("round", "25"), ("start", "teacher"), ("lr", "1e-5"), ("seed", "1000"))


def test_stale_log_publishes_no_round_numbers():
    """With the tenant stopped the loss goes absent instead of flat; the ledger's facts stay."""
    got = by_name(core.training_samples(ROUND, [EARLY], False, core.Ledger(3, 1, 3, 0.07)))
    assert got["tenant_training_up"] == 0.0 and got["tenant_training_round"] == 25.0
    assert "tenant_training_loss" not in got and "tenant_training_step" not in got
    assert got["tenant_training_rounds_failed"] == 1.0


def test_rough_step_is_the_fallback_without_a_bar():
    """A log without the progress bar still has a step, to the precision lerobot printed."""
    line = "INFO step:3K smpl:24K loss:0.05 updt_s:0.09 data_s:0.01"
    assert by_name(core.training_samples(ROUND, [line], True, core.Ledger()))["tenant_training_step"] == 3000.0


def test_no_round_directory_at_all():
    """Before the tenant ever ran there is an up 0 and the empty ledger."""
    got = by_name(core.training_samples(None, [], False, core.Ledger()))
    assert got == {"tenant_training_up": 0.0, "tenant_training_rounds_finished": 0.0, "tenant_training_rounds_failed": 0.0}


def test_renderer_series():
    """The five fields, the batch time in seconds."""
    status = {"render_fps": 15.0, "fps_target": 15.0, "robots_live": 13, "robots_stale": 0, "batch_ms": 56.66,
              "phase": "rendering", "robots": [{"id": "r00"}]}
    got = by_name(core.renderer_samples(status))
    assert got == {"tenant_renderer_up": 1.0, "tenant_renderer_render_fps": 15.0, "tenant_renderer_fps_target": 15.0,
                   "tenant_renderer_robots_live": 13.0, "tenant_renderer_robots_stale": 0.0,
                   "tenant_renderer_batch_seconds": pytest.approx(0.05666)}


@pytest.mark.parametrize("status", [None, [], "up", 3, b""])
def test_renderer_down_or_not_an_object(status):
    """Anything but a JSON object is up 0 and nothing else."""
    assert by_name(core.renderer_samples(status)) == {"tenant_renderer_up": 0.0}


def test_renderer_fields_of_the_wrong_type_are_left_out():
    """null, booleans, strings and infinities do not become samples."""
    status = {"render_fps": None, "fps_target": True, "robots_live": "13", "robots_stale": float("inf"), "batch_ms": 4}
    assert by_name(core.renderer_samples(status)) == {"tenant_renderer_up": 1.0, "tenant_renderer_batch_seconds": 0.004}


@pytest.mark.parametrize("body", [b"", b"{", b"\xff\xfe", b"<html>"])
def test_status_bodies_that_are_not_json(body):
    """A body that does not parse is None, which reads as down."""
    assert core.parse_status(body) is None


OK = b"HTTP/1.0 200 OK\r\nServer: x\r\nContent-Length: 4\r\n\r\n"


@pytest.mark.parametrize("raw, done, body", [
    (b"", False, None), (b"HTTP/1.0 200", False, None), (OK[:-2], False, None),
    (OK + b"{}", False, None), (OK + b"{}{}", True, b"{}{}"), (OK + b"{}{}extra", True, b"{}{}"),
    (b"HTTP/1.1 200 OK\r\ncontent-length:2\r\n\r\n{}", True, b"{}"),
    (b"HTTP/1.0 200 OK\r\n\r\n{}", False, b"{}"),
    (b"HTTP/1.0 503 Busy\r\nContent-Length: 2\r\n\r\n", True, None), (b"junk\r\n\r\n{}", True, None),
])
def test_http_responses_in_pieces(raw, done, body):
    """Whether the fetch may stop reading, and what it may hand on: only a whole 200."""
    assert core.response_done(raw) is done
    assert core.response_body(raw) == body


def test_exposition_format():
    """HELP and TYPE once per family, labels quoted and escaped, unknown names dropped."""
    text = core.exposition([core.Sample("tenant_training_up", 1.0),
                            core.Sample("tenant_training_loss", 0.064, (("round", "25"), ("start", 'a"b\\c\nd'))),
                            core.Sample("made_up_total", 1.0), core.Sample("tenant_training_step", float("nan"))])
    assert text.endswith("\n") and "made_up_total" not in text and "tenant_training_step" not in text
    assert "# TYPE tenant_training_up gauge\ntenant_training_up 1.0\n" in text
    assert 'tenant_training_loss{round="25",start="a\\"b\\\\c\\nd"} 0.064\n' in text
    assert text.count("# HELP tenant_training_loss ") == 1
    assert core.exposition([core.Sample("tenant_training_up", 1.0, (("x", "a\rb"),))]).endswith('{x="ab"} 1.0\n')


def test_every_series_the_core_makes_has_a_family():
    """Nothing the collectors return is silently dropped by the exposition."""
    samples = core.training_samples(ROUND, [EARLY, BAR], True, core.Ledger(1, 1, 1, 0.1)) + core.renderer_samples(
        {"render_fps": 1, "fps_target": 1, "robots_live": 1, "robots_stale": 1, "batch_ms": 1})
    text = core.exposition(samples)
    assert all(f"\n{s.name}" in "\n" + text for s in samples)
