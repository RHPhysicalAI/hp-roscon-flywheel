# This project was developed with assistance from AI tools.
"""tools/hub/training-watch.sh: what it makes of the journal's lines, with whatever awk this machine has, and its refusals."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "hub" / "training-watch.sh"

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("awk")), reason="needs bash and awk")

# as `journalctl -o cat` gives them through a terminal: CR LF, and an empty line after every entry
OPEN = "[tenant] round 25: from the teacher checkpoint, lr 1e-5, seed 1000, 9000 steps -> round-000025-teacher-lr1e-5-s1000/"
LOSS = ("Training:  12%|█▏        | 1099/9000 [01:52<13:27,  9.78step/s]INFO 2026-09-20 21:52:28 ot_train.py:451 "
        "step:1K smpl:9K ep:6 epch:0.04 loss:0.064 grdn:6.307 lr:3.0e-05 updt_s:0.094 data_s:0.005")
DONE = ('[tenant] round 24 done in 15 min 18 s: {"round": 24, "dir": "round-000024-v2-lr3e-5-s2000", "ok": true, '
        '"steps_per_s": 9.804, "last_loss": 0.059, "updt_s": 0.0922}')


def watch(*args: str, lines: tuple[str, ...] = (), env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run the script with these lines on its standard input."""
    text = "".join(line + "\r\n\r\n" for line in lines)
    return subprocess.run(["bash", str(SCRIPT), *args], input=text, capture_output=True, text=True, timeout=30,
                          check=False, env={**os.environ, "NO_COLOR": "1", **(env or {})})


def test_a_loss_line_is_trimmed_to_what_matters():
    """The exact step from the progress bar, the loss, steps a second and the data wait; nothing else of the line."""
    out = watch("--stdin", lines=(LOSS,)).stdout
    assert out.count("\n") == 1
    assert "step  1100 / 9000" in out and " 12%" in out and "loss 0.064" in out
    assert "10.1 steps/s" in out and "data wait 0.005 s" in out
    assert "grdn" not in out and "ot_train" not in out and "\r" not in out


def test_round_boundaries_are_called_out():
    """A round's opening line becomes a heading, once, and its end a summary from the ledger line."""
    out = watch("--stdin", lines=(OPEN, OPEN, LOSS, DONE)).stdout
    assert out.count("==== training tenant, round 25 ====") == 1
    assert "from the teacher checkpoint, lr 1e-5, seed 1000, 9000 steps" in out and "round-000025" not in out
    assert "round 24 done in 15 min 18 s   final loss 0.059   9.804 steps/s" in out and "{" not in out


def test_a_loss_line_without_a_bar_still_shows():
    """Without the progress bar the step is the one lerobot printed, and a missing timing is a question mark."""
    out = watch("--stdin", lines=("INFO step:3K smpl:24K loss:0.05 updt_s:0.09",)).stdout
    assert "step 3K" in out and "loss 0.05" in out and "? steps/s" in out


def test_what_is_not_understood_passes_through():
    """A traceback, a failure and the tenant's other lines are shown, and the end-of-training line is dropped."""
    lines = ("Traceback (most recent call last):", "[tenant] round 26 FAILED (lerobot-train exit 1 after 12 s).",
             "[tenant] removed round-000021-teacher-lr1e-5-s2000 (keeping the last 3 rounds)", "INFO End of training")
    out = watch("--stdin", lines=lines).stdout
    assert all(line in out for line in lines[:3]) and "End of training" not in out


def test_a_stopped_tenant_and_an_unreadable_journal_name_the_way_on():
    """Both say what to run on the host."""
    out = watch("--stdin", lines=("[watch] unit: inactive",)).stdout
    assert "not running (inactive)" in out and "sudo systemctl start training-tenant.service" in out
    out = watch("--stdin", lines=("Hint: You are currently not seeing messages from other users and the system.",)).stdout
    assert "sudo usermod -aG systemd-journal" in out
    assert watch("--stdin", lines=("[watch] unit: active",)).stdout == ""


def test_raw_shows_the_lines_untouched():
    """--raw keeps lerobot's own line, less the terminal's line ends."""
    out = watch("--stdin", "--raw", lines=(OPEN, LOSS)).stdout
    assert out == OPEN + "\n" + LOSS + "\n"


def test_terminal_control_sequences_do_not_reach_the_projector():
    """A clear-screen in front of a loss line, a title sequence and colours are taken out; the tab and the text stay."""
    out = watch("--stdin", lines=("\033[2Jloss:0.06 step:100", "plain \033]0;title\007 text\twith \033[31mred\033[0m \x08\x7f.")).stdout
    assert "\033" not in out and "\007" not in out and "\x08" not in out and "\x7f" not in out
    assert "step 100" in out and "loss 0.06" in out
    assert "plain ]0;title text\twith red ." in out


def test_raw_leaves_control_sequences_alone():
    """--raw is the journal as it is, escape sequences included."""
    assert watch("--stdin", "--raw", lines=("\033[2Jloss:0.06 step:100",)).stdout == "\033[2Jloss:0.06 step:100\n"


@pytest.mark.parametrize("target", ["-oProxyCommand=id", "host-without-user", "user@", "user@-host", "user@host;id", "a b@host"])
def test_a_target_that_is_not_user_at_host_is_refused(target):
    """What is handed to ssh is a login and a host name, never an option or a command."""
    done = watch(env={"FURY_SSH": target})
    assert done.returncode == 1 and "is not user@host" in done.stderr


def test_a_failure_of_either_side_of_the_pipe_is_reported():
    """The script looks at the formatter's status as well as the follower's."""
    text = SCRIPT.read_text()
    assert 'ends=("${PIPESTATUS[@]}")' in text and "${ends[0]}" in text and "${ends[1]}" in text


def test_refusals_say_what_to_do():
    """No target, an unknown flag and --local off the host each end with 1 and the way on."""
    done = watch(env={"FURY_SSH": ""})
    assert done.returncode == 1 and "FURY_SSH=user@host" in done.stderr and "--local" in done.stderr
    done = watch("--follow")
    assert done.returncode == 1 and "usage:" in done.stderr
    if not shutil.which("journalctl"):
        done = watch("--local")
        assert done.returncode == 1 and "FURY_SSH=user@host" in done.stderr


def test_the_awk_program_has_no_apostrophe():
    """An apostrophe in a comment of the program ends the shell quote, and awk then waits for ever."""
    text = SCRIPT.read_text()
    program = text[text.index("    awk -v B="):text.index("\n    '\n}")].split("'", 1)[1]
    assert "'" not in program
