# This project was developed with assistance from AI tools.
"""Blind-spec tests for src/host-runner/host_runner.py (docker/inprocess + script/request modes)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

# the ROS / workspace preamble every stage script starts with (the spec fixes the string, not its name)
PREFIX = "source /opt/ros/$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash; "

# ---------------------------------------------------------------- shared fixtures


@pytest.fixture
def runner(load_runner):
    """A freshly imported host_runner module with default environment (docker/script mode)."""
    return load_runner()


@pytest.fixture
def guard_real_sleep(monkeypatch):
    """Fail the test if real time.sleep is ever invoked instead of an injected fake."""

    def _boom(*_a, **_kw):
        raise AssertionError("real time.sleep was called; the injected `sleep` seam was bypassed")

    monkeypatch.setattr(time, "sleep", _boom)


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


# ---------------------------------------------------------------- configuration read at import


def test_import_fails_when_minio_access_key_missing(load_runner):
    """Module refuses to import (SystemExit) when MINIO_ACCESS_KEY is unset."""
    with pytest.raises(SystemExit):
        load_runner(MINIO_ACCESS_KEY=None)


def test_import_fails_when_minio_secret_key_missing(load_runner):
    """Module refuses to import (SystemExit) when MINIO_SECRET_KEY is unset."""
    with pytest.raises(SystemExit):
        load_runner(MINIO_SECRET_KEY=None)


def test_import_fails_when_both_minio_keys_missing(load_runner):
    """Module refuses to import (SystemExit) when both MinIO keys are unset."""
    with pytest.raises(SystemExit):
        load_runner(MINIO_ACCESS_KEY=None, MINIO_SECRET_KEY=None)


def test_fly_defaults_to_home_flywheel_data(runner):
    """FLY defaults to ~/flywheel-data when FLYWHEEL_DATA is not set."""
    assert runner.FLY == Path.home() / "flywheel-data"
    assert isinstance(runner.FLY, Path)


def test_fly_overridden_by_flywheel_data_env(load_runner):
    """FLYWHEEL_DATA env var overrides the FLY module attribute."""
    runner = load_runner(FLYWHEEL_DATA="/custom/fly-root")
    assert runner.FLY == Path("/custom/fly-root")


def test_teacher_hf_default_preserved(runner):
    """TEACHER_HF keeps today's hardcoded upstream snapshot path when TEACHER_PATH is unset."""
    expected = (
        "/root/.cache/huggingface/hub/models--francocipollone--"
        "rospai_act_sim_arm101_place_cubes_on_tray/snapshots/4c2bdba206dccc382dbf80d48e15b3d754102df6"
    )
    assert runner.TEACHER_HF == expected


def test_teacher_hf_overridden_by_teacher_path_env(load_runner):
    """TEACHER_PATH env var overrides the TEACHER_HF module constant."""
    runner = load_runner(TEACHER_PATH="/custom/teacher/dir")
    assert runner.TEACHER_HF == "/custom/teacher/dir"


def test_host_data_root_defaults_to_empty_string(runner):
    """HOST_DATA_ROOT defaults to empty string when unset."""
    assert runner.HOST_DATA_ROOT == ""


def test_host_data_root_overridden_by_env(load_runner):
    """HOST_DATA_ROOT env var is exposed verbatim as the module attribute."""
    runner = load_runner(HOST_DATA_ROOT="/mnt/nas/flywheel")
    assert runner.HOST_DATA_ROOT == "/mnt/nas/flywheel"


def test_eval_timeout_and_poll_defaults(runner):
    """EVAL_TIMEOUT_S defaults to 21600 and EVAL_POLL_S to 15 when unset."""
    assert runner.EVAL_TIMEOUT_S == 21600
    assert runner.EVAL_POLL_S == 15


def test_eval_timeout_and_poll_overridden_by_env(load_runner):
    """EVAL_TIMEOUT_S and EVAL_POLL_S env vars override their module defaults."""
    runner = load_runner(EVAL_TIMEOUT_S="100", EVAL_POLL_S="2")
    assert runner.EVAL_TIMEOUT_S == 100
    assert runner.EVAL_POLL_S == 2


def test_runner_mode_default_attribute_is_docker(runner):
    """RUNNER_MODE module attribute defaults to 'docker' (best-guess attribute name; see report)."""
    assert runner.RUNNER_MODE == "docker"


def test_eval_mode_default_attribute_is_script(runner):
    """EVAL_MODE module attribute defaults to 'script' (best-guess attribute name; see report)."""
    assert runner.EVAL_MODE == "script"


# ---------------------------------------------------------------- build_stage_cmd


def test_build_stage_cmd_docker_mode_minimal(runner):
    """docker mode with no gpus/extra builds the exact documented argv."""
    fly, home = Path("/data/fly"), Path("/home/op")
    cmd = runner.build_stage_cmd("echo hi", mode="docker", image="act-inference:latest", fly=fly, home=home)
    assert cmd == [
        "docker", "run", "--rm", "--network", "host", "--entrypoint", "bash", "--shm-size=2g",
        "-v", f"{home}/.cache/huggingface:/root/.cache/huggingface",
        "-v", f"{fly}:/flywheel",
        "-e", "MINIO_ACCESS_KEY", "-e", "MINIO_SECRET_KEY",
        "act-inference:latest", "-lc", PREFIX + "echo hi",
    ]


def test_build_stage_cmd_docker_mode_with_gpus(runner):
    """docker mode inserts --gpus all right before the image when gpus=True."""
    fly, home = Path("/data/fly"), Path("/home/op")
    cmd = runner.build_stage_cmd("train", mode="docker", image="img:tag", fly=fly, home=home, gpus=True)
    assert cmd[-5:] == ["--gpus", "all", "img:tag", "-lc", PREFIX + "train"]
    assert "--gpus" not in cmd[:-5]


def test_build_stage_cmd_docker_mode_with_extra_and_gpus(runner):
    """docker mode splices `extra` in before --gpus and the image/script tail."""
    fly, home = Path("/data/fly"), Path("/home/op")
    extra = ("-v", "/host/incumbent:/incumbent:ro")
    cmd = runner.build_stage_cmd("eval", mode="docker", image="img:tag", fly=fly, home=home, gpus=True, extra=extra)
    assert cmd == [
        "docker", "run", "--rm", "--network", "host", "--entrypoint", "bash", "--shm-size=2g",
        "-v", f"{home}/.cache/huggingface:/root/.cache/huggingface",
        "-v", f"{fly}:/flywheel",
        "-e", "MINIO_ACCESS_KEY", "-e", "MINIO_SECRET_KEY",
        "-v", "/host/incumbent:/incumbent:ro",
        "--gpus", "all",
        "img:tag", "-lc", PREFIX + "eval",
    ]


def test_build_stage_cmd_inprocess_mode_ignores_image_gpus_extra(runner):
    """inprocess mode returns exactly ['bash','-lc', PREFIX+script], ignoring image/gpus/extra."""
    cmd = runner.build_stage_cmd(
        "echo hi", mode="inprocess", image="should-be-ignored", fly=Path("/x"), home=Path("/y"),
        gpus=True, extra=("-v", "should/also/be:ignored"),
    )
    assert cmd == ["bash", "-lc", PREFIX + "echo hi"]


def test_build_stage_cmd_unknown_mode_raises(runner):
    """An unrecognized mode raises ValueError."""
    with pytest.raises(ValueError):
        runner.build_stage_cmd("echo hi", mode="ssh", image="img", fly=Path("/x"), home=Path("/y"))


def test_prefix_constant_value(runner):
    """PREFIX matches the exact ROS/workspace sourcing preamble used by every stage script."""
    assert runner.ROS_SETUP == PREFIX


# ---------------------------------------------------------------- incumbent_policy_path


def test_incumbent_policy_path_hf_in_docker_mode(runner):
    """'HF' incumbent resolves to the teacher path with no extra mount, in docker mode."""
    assert runner.incumbent_policy_path("HF", mode="docker", teacher="/teacher/path") == ("/teacher/path", ())


def test_incumbent_policy_path_hf_in_inprocess_mode(runner):
    """'HF' incumbent resolves to the teacher path with no extra mount, in inprocess mode too."""
    assert runner.incumbent_policy_path("HF", mode="inprocess", teacher="/teacher/path") == ("/teacher/path", ())


def test_incumbent_policy_path_docker_mode_non_hf(runner):
    """A concrete incumbent path mounts read-only at /incumbent in docker mode."""
    result = runner.incumbent_policy_path("/host/ckpt", mode="docker", teacher="/teacher/path")
    assert result == ("/incumbent", ("-v", "/host/ckpt:/incumbent:ro"))


def test_incumbent_policy_path_inprocess_mode_non_hf(runner):
    """A concrete incumbent path passes through unchanged with no mount in inprocess mode."""
    result = runner.incumbent_policy_path("/host/ckpt", mode="inprocess", teacher="/teacher/path")
    assert result == ("/host/ckpt", ())


# ---------------------------------------------------------------- eval_paths


def test_eval_paths_layout(tmp_path, runner):
    """eval_paths returns (request, done, result) Paths in the documented layout."""
    fly = tmp_path / "fly"
    req, done, result = runner.eval_paths(fly, "act-v2-ft160")
    assert req == fly / "eval" / "requests" / "act-v2-ft160.json"
    assert done == fly / "eval" / "requests" / "act-v2-ft160.done"
    assert result == fly / "eval" / "act-v2-ft160.json"
    assert all(isinstance(p, Path) for p in (req, done, result))


# ---------------------------------------------------------------- to_host_path / from_host_path


def test_to_host_path_maps_path_under_fly(tmp_path, runner):
    """A path under fly maps to the same relative path under host_root."""
    fly = tmp_path / "flywheel-data"
    host_root = "/mnt/nas/flywheel"
    path = str(fly / "eval" / "run1" / "report.json")
    expected = str(Path(host_root) / "eval" / "run1" / "report.json")
    assert runner.to_host_path(path, fly=fly, host_root=host_root) == expected


def test_to_host_path_leaves_hf_unchanged(tmp_path, runner):
    """'HF' is always returned unchanged."""
    fly = tmp_path / "flywheel-data"
    assert runner.to_host_path("HF", fly=fly, host_root="/mnt/nas/flywheel") == "HF"


def test_to_host_path_leaves_modelcar_unchanged(tmp_path, runner):
    """'modelcar' is always returned unchanged."""
    fly = tmp_path / "flywheel-data"
    assert runner.to_host_path("modelcar", fly=fly, host_root="/mnt/nas/flywheel") == "modelcar"


def test_to_host_path_leaves_path_outside_fly_unchanged(tmp_path, runner):
    """A path that is not under fly is returned unchanged."""
    fly = tmp_path / "flywheel-data"
    outside = "/some/unrelated/place/file.json"
    assert runner.to_host_path(outside, fly=fly, host_root="/mnt/nas/flywheel") == outside


def test_to_host_path_sibling_directory_with_shared_prefix_is_not_treated_as_inside(tmp_path, runner):
    """A sibling dir whose name merely starts with fly's name is not mistaken for being under fly."""
    fly = tmp_path / "flywheel-data"
    sibling = str(tmp_path / "flywheel-data-backup" / "file.json")
    assert runner.to_host_path(sibling, fly=fly, host_root="/mnt/nas/flywheel") == sibling


def test_to_host_path_identity_when_host_root_empty(tmp_path, runner):
    """Everything is returned unchanged when host_root is the empty string, even paths under fly."""
    fly = tmp_path / "flywheel-data"
    path = str(fly / "eval" / "run1" / "report.json")
    assert runner.to_host_path(path, fly=fly, host_root="") == path


def test_from_host_path_maps_back_under_fly(tmp_path, runner):
    """A path under host_root maps back to the same relative path under fly (inverse of to_host_path)."""
    fly = tmp_path / "flywheel-data"
    host_root = "/mnt/nas/flywheel"
    fly_path = str(fly / "eval" / "run1" / "report.json")
    host_path = str(Path(host_root) / "eval" / "run1" / "report.json")
    assert runner.from_host_path(host_path, fly=fly, host_root=host_root) == fly_path


def test_from_host_path_leaves_path_outside_host_root_unchanged(tmp_path, runner):
    """A path not under host_root is returned unchanged."""
    fly = tmp_path / "flywheel-data"
    outside = "/not/under/host_root/file.json"
    assert runner.from_host_path(outside, fly=fly, host_root="/mnt/nas/flywheel") == outside


def test_from_host_path_identity_when_host_root_empty(tmp_path, runner):
    """Everything is returned unchanged when host_root is the empty string."""
    fly = tmp_path / "flywheel-data"
    path = "/mnt/nas/flywheel/eval/run1/report.json"
    assert runner.from_host_path(path, fly=fly, host_root="") == path


def test_to_then_from_host_path_round_trips(tmp_path, runner):
    """to_host_path followed by from_host_path returns the original fly-rooted path."""
    fly = tmp_path / "flywheel-data"
    host_root = "/mnt/nas/flywheel"
    original = str(fly / "eval" / "run1" / "report.json")
    host_path = runner.to_host_path(original, fly=fly, host_root=host_root)
    assert runner.from_host_path(host_path, fly=fly, host_root=host_root) == original


# ---------------------------------------------------------------- write_eval_request


def test_write_eval_request_writes_expected_keys_and_returns_request_path(tmp_path, runner):
    """A valid request writes exactly the four documented keys and returns eval_paths()[0]."""
    fly = tmp_path / "fly"
    result_path = runner.write_eval_request(fly, "act-v2-ft160", "s3://bucket/key", seed_base=1000, n=50)
    expected_path = runner.eval_paths(fly, "act-v2-ft160")[0]
    assert result_path == expected_path
    data = json.loads(result_path.read_text())
    assert data == {"model_version": "act-v2-ft160", "checkpoint": "s3://bucket/key", "seed_base": 1000, "n": 50}


def test_write_eval_request_creates_directory_atomically_with_no_leftover_files(tmp_path, runner):
    """After a clean write, the requests dir contains exactly the one new <mv>.json (no temp files)."""
    fly = tmp_path / "fly"
    runner.write_eval_request(fly, "act-v2-ft160", "ckpt", seed_base=0, n=1)
    requests_dir = fly / "eval" / "requests"
    assert [p.name for p in requests_dir.iterdir()] == ["act-v2-ft160.json"]


def test_write_eval_request_removes_stale_done_and_taken_markers(tmp_path, runner):
    """A stale <mv>.done and <mv>.json.taken are removed, leaving only the fresh <mv>.json."""
    fly = tmp_path / "fly"
    mv = "act-v2-ft160"
    req_path, done_path, _ = runner.eval_paths(fly, mv)
    taken_path = req_path.parent / f"{req_path.name}.taken"
    req_path.parent.mkdir(parents=True)
    done_path.write_text('{"status": "ok"}')
    taken_path.write_text("")

    runner.write_eval_request(fly, mv, "ckpt", seed_base=0, n=1)

    assert not done_path.exists()
    assert not taken_path.exists()
    assert [p.name for p in req_path.parent.iterdir()] == [f"{mv}.json"]


@pytest.mark.parametrize(
    "bad_mv",
    ["../x", "has space", "", "a" * 65, ".leadingdot"],
    ids=["path-traversal", "space", "empty", "too-long", "leading-dot"],
)
def test_write_eval_request_rejects_unsafe_model_version_and_writes_nothing(tmp_path, runner, bad_mv):
    """An unsafe model_version raises ValueError and creates no files at all."""
    fly = tmp_path / "fly"
    with pytest.raises(ValueError):
        runner.write_eval_request(fly, bad_mv, "ckpt", seed_base=0, n=1)
    assert not (fly / "eval").exists()


@pytest.mark.parametrize(
    "good_mv",
    [
        "upstream-act-teacher",
        "act-v2-ft160",
        "act-v2-ft160-ft160-202609191200",
        "550e8400-e29b-41d4-a716-446655440000",
    ],
)
def test_write_eval_request_accepts_valid_model_versions(tmp_path, runner, good_mv):
    """Every model_version format seen in production is accepted."""
    fly = tmp_path / "fly"
    path = runner.write_eval_request(fly, good_mv, "ckpt", seed_base=0, n=1)
    assert path.exists()


@pytest.mark.parametrize("bad_n", [0, 501], ids=["too-low", "too-high"])
def test_write_eval_request_rejects_n_out_of_range(tmp_path, runner, bad_n):
    """n outside [1, 500] raises ValueError and writes nothing."""
    fly = tmp_path / "fly"
    with pytest.raises(ValueError):
        runner.write_eval_request(fly, "act-v2-ft160", "ckpt", seed_base=0, n=bad_n)
    assert not (fly / "eval").exists()


def test_write_eval_request_rejects_negative_seed_base(tmp_path, runner):
    """A negative seed_base raises ValueError and writes nothing."""
    fly = tmp_path / "fly"
    with pytest.raises(ValueError):
        runner.write_eval_request(fly, "act-v2-ft160", "ckpt", seed_base=-1, n=1)
    assert not (fly / "eval").exists()


@pytest.mark.parametrize("boundary_n", [1, 500])
def test_write_eval_request_accepts_boundary_n_values(tmp_path, runner, boundary_n):
    """n=1 and n=500 are both accepted (inclusive boundaries)."""
    fly = tmp_path / "fly"
    path = runner.write_eval_request(fly, "act-v2-ft160", "ckpt", seed_base=0, n=boundary_n)
    assert json.loads(path.read_text())["n"] == boundary_n


# ---------------------------------------------------------------- wait_eval_done


def test_wait_eval_done_ok_reads_result_from_explicit_path(tmp_path, runner, guard_real_sleep):
    """status=ok with a set `result` path reads and returns that file's parsed JSON."""
    fly = tmp_path / "fly"
    mv = "eval-cand"
    _, done_path, _ = runner.eval_paths(fly, mv)
    custom_result = fly / "eval" / "custom-report.json"
    expected = {"aggregate": {"success_rate": 0.8, "mean_cubes": 3.0}, "episodes": []}
    _write_json(custom_result, expected)
    _write_json(done_path, {"status": "ok", "result": str(custom_result), "error": None})

    def fail_sleep(*_a, **_kw):
        raise AssertionError("sleep should not be called when .done already exists")

    result = runner.wait_eval_done(fly, mv, timeout_s=10, poll_s=1, sleep=fail_sleep, now=lambda: 0.0)
    assert result == expected


def test_wait_eval_done_ok_falls_back_to_eval_paths_result(tmp_path, runner, guard_real_sleep):
    """status=ok with result=null falls back to reading eval_paths(...)[2]."""
    fly = tmp_path / "fly"
    mv = "eval-cand"
    _, done_path, result_path = runner.eval_paths(fly, mv)
    expected = {"aggregate": {"success_rate": 0.6, "mean_cubes": 1.9}, "episodes": []}
    _write_json(result_path, expected)
    _write_json(done_path, {"status": "ok", "result": None, "error": None})

    def fail_sleep(*_a, **_kw):
        raise AssertionError("sleep should not be called when .done already exists")

    result = runner.wait_eval_done(fly, mv, timeout_s=10, poll_s=1, sleep=fail_sleep, now=lambda: 0.0)
    assert result == expected


def test_wait_eval_done_failed_status_raises_with_error_text(tmp_path, runner, guard_real_sleep):
    """status=failed raises RuntimeError whose message contains the .done file's error text."""
    fly = tmp_path / "fly"
    mv = "eval-cand"
    _, done_path, _ = runner.eval_paths(fly, mv)
    _write_json(done_path, {"status": "failed", "result": None, "error": "harness exploded: boom"})

    def fail_sleep(*_a, **_kw):
        raise AssertionError("sleep should not be called when .done already exists")

    with pytest.raises(RuntimeError, match="harness exploded: boom"):
        runner.wait_eval_done(fly, mv, timeout_s=10, poll_s=1, sleep=fail_sleep, now=lambda: 0.0)


def test_wait_eval_done_unparsable_done_raises_runtime_error(tmp_path, runner, guard_real_sleep):
    """An unparsable .done file raises RuntimeError rather than propagating a JSON error."""
    fly = tmp_path / "fly"
    mv = "eval-cand"
    _, done_path, _ = runner.eval_paths(fly, mv)
    done_path.parent.mkdir(parents=True)
    done_path.write_text("{not valid json")

    def fail_sleep(*_a, **_kw):
        raise AssertionError("sleep should not be called when .done already exists")

    with pytest.raises(RuntimeError):
        runner.wait_eval_done(fly, mv, timeout_s=10, poll_s=1, sleep=fail_sleep, now=lambda: 0.0)


def test_wait_eval_done_polls_via_injected_sleep_until_done_appears(tmp_path, runner, guard_real_sleep):
    """wait_eval_done polls using only the injected sleep seam until .done shows up."""
    fly = tmp_path / "fly"
    mv = "eval-cand"
    _, done_path, result_path = runner.eval_paths(fly, mv)
    done_path.parent.mkdir(parents=True)
    expected = {"aggregate": {"success_rate": 0.7, "mean_cubes": 2.0}, "episodes": []}
    _write_json(result_path, expected)

    calls = {"n": 0}
    appear_on = 3

    def fake_sleep(seconds):
        calls["n"] += 1
        assert seconds == 5
        if calls["n"] >= appear_on:
            done_path.write_text(json.dumps({"status": "ok", "result": None, "error": None}))
        if calls["n"] > 10:
            raise AssertionError("polling loop did not terminate once .done appeared")

    result = runner.wait_eval_done(fly, mv, timeout_s=999999, poll_s=5, sleep=fake_sleep, now=lambda: 0.0)

    assert result == expected
    assert 1 <= calls["n"] <= 10


def test_wait_eval_done_times_out_when_done_never_appears(tmp_path, runner, guard_real_sleep):
    """wait_eval_done raises TimeoutError, driven purely by the injected fake clock, if .done never appears."""
    fly = tmp_path / "fly"
    mv = "eval-cand"

    now_values = iter([0, 0, 100])

    def fake_now():
        try:
            return next(now_values)
        except StopIteration:
            return 100

    sleep_calls = []

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) > 10:
            raise AssertionError("polling loop did not respect the deadline")

    with pytest.raises(TimeoutError):
        runner.wait_eval_done(fly, mv, timeout_s=10, poll_s=1, sleep=fake_sleep, now=fake_now)


# ---------------------------------------------------------------- _require_safe (preserved)


@pytest.mark.parametrize(
    "value",
    [
        "upstream-act-teacher",
        "act-v2-ft160",
        "act-v2-ft160-ft160-202609191200",
        "550e8400-e29b-41d4-a716-446655440000",
    ],
)
def test_require_safe_accepts_known_good_values(runner, value):
    """_require_safe accepts every token format actually seen in production."""
    assert runner._require_safe("field", value) == value


@pytest.mark.parametrize(
    "value",
    ["a/b", "a;b", "a b", "$(id)", "", "-abc", "a" * 65],
    ids=["slash", "semicolon", "space", "cmd-subst", "empty", "leading-hyphen", "too-long"],
)
def test_require_safe_rejects_unsafe_values(runner, value):
    """_require_safe rejects path separators, shell metacharacters, empty, and over-length values."""
    with pytest.raises(ValueError):
        runner._require_safe("field", value)


def test_require_safe_accepts_max_length_boundary(runner):
    """A 64-character value (the max allowed) is accepted."""
    value = "a" * 64
    assert runner._require_safe("field", value) == value


# ---------------------------------------------------------------- paired_report (preserved)


def _record(success_rate, mean_cubes, episodes):
    return {"aggregate": {"success_rate": success_rate, "mean_cubes": mean_cubes}, "episodes": episodes}


def test_paired_report_six_fixed_zero_broken_is_pass(runner):
    """6 fixed / 0 broken gives the hand-computed p=0.0312 and a PASS verdict."""
    inc = _record(0.0, 0.5, [{"seed": s, "task_success": False} for s in range(6)])
    cand = _record(1.0, 2.5, [{"seed": s, "task_success": True} for s in range(6)])
    rep = runner.paired_report("run-1", "cand-mv", "inc-mv", cand, inc)
    assert rep["fixed"] == 6
    assert rep["broken"] == 0
    assert rep["net"] == 6
    assert rep["n_paired"] == 6
    assert rep["sign_test_p"] == 0.0312
    assert rep["verdict"] == "PASS"


def test_paired_report_three_fixed_one_broken_is_fail(runner):
    """3 fixed / 1 broken gives the hand-computed p=0.625 and a FAIL verdict (net>0 but p not <0.05)."""
    inc_episodes = [{"seed": s, "task_success": False} for s in range(3)] + [{"seed": 3, "task_success": True}]
    cand_episodes = [{"seed": s, "task_success": True} for s in range(3)] + [{"seed": 3, "task_success": False}]
    inc = _record(0.25, 0.5, inc_episodes)
    cand = _record(0.75, 1.5, cand_episodes)
    rep = runner.paired_report("run-2", "cand-mv", "inc-mv", cand, inc)
    assert rep["fixed"] == 3
    assert rep["broken"] == 1
    assert rep["net"] == 2
    assert rep["n_paired"] == 4
    assert rep["sign_test_p"] == 0.625
    assert rep["verdict"] == "FAIL"


def test_paired_report_zero_fixed_zero_broken_is_fail(runner):
    """0 fixed / 0 broken (candidate and incumbent agree everywhere) gives p=1.0 and FAIL."""
    episodes = [{"seed": s, "task_success": True} for s in range(2)]
    inc = _record(1.0, 3.0, episodes)
    cand = _record(1.0, 3.0, episodes)
    rep = runner.paired_report("run-3", "cand-mv", "inc-mv", cand, inc)
    assert rep["fixed"] == 0
    assert rep["broken"] == 0
    assert rep["net"] == 0
    assert rep["sign_test_p"] == 1.0
    assert rep["verdict"] == "FAIL"


def test_paired_report_n_paired_only_counts_seeds_present_in_both(runner):
    """n_paired excludes seeds that only appear in one of candidate/incumbent."""
    inc = _record(0.5, 1.0, [{"seed": 1, "task_success": False}, {"seed": 2, "task_success": True}])
    cand = _record(0.5, 1.0, [
        {"seed": 1, "task_success": True},
        {"seed": 2, "task_success": True},
        {"seed": 99, "task_success": True},  # not present in incumbent -> excluded
    ])
    rep = runner.paired_report("run-4", "cand-mv", "inc-mv", cand, inc)
    assert rep["n_paired"] == 2


# ---------------------------------------------------------------- mode behaviour


def test_loop_park_calls_subprocess_run_in_script_mode(load_runner, monkeypatch):
    """In the default (script) EVAL_MODE, loop_park() does invoke subprocess.run."""
    runner = load_runner(EVAL_MODE="script")
    calls = []

    class _FakeCompleted:
        stdout = ""

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeCompleted()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner.loop_park()
    assert len(calls) >= 1


def test_loop_park_and_restore_do_not_call_subprocess_run_in_request_mode(load_runner, monkeypatch):
    """In EVAL_MODE=request, neither loop_park() nor loop_restore() invokes subprocess.run."""
    runner = load_runner(EVAL_MODE="request")

    def boom(*_a, **_kw):
        raise AssertionError("subprocess.run must not be called in request mode")

    monkeypatch.setattr(runner.subprocess, "run", boom)
    runner.loop_park()
    runner.loop_restore()


def test_in_image_inprocess_mode_hands_expected_argv_to_sh(load_runner, monkeypatch):
    """RUNNER_MODE=inprocess makes in_image() call sh() with ['bash','-lc', PREFIX+script]."""
    runner = load_runner(RUNNER_MODE="inprocess")
    calls = []

    def fake_sh(cmd, **kwargs):
        calls.append(cmd)
        return "SENTINEL"

    monkeypatch.setattr(runner, "sh", fake_sh)
    result = runner.in_image("echo hi")

    assert calls == [["bash", "-lc", PREFIX + "echo hi"]]
    assert result == "SENTINEL"


def test_reused_dataset_uri_reads_the_note_beside_the_checkpoint(load_runner, tmp_path):
    """A reused checkpoint reports the dataset named in train/<candidate>/dataset_uri.txt."""
    runner = load_runner(FLYWHEEL_DATA=str(tmp_path))
    d = tmp_path / "train" / "act-v2-ft160"
    d.mkdir(parents=True)
    uri = "s3://episodes-data/upstream-act-teacher/flywheel-ladder-160.tar.gz"
    (d / "dataset_uri.txt").write_text(uri + chr(10))
    assert runner.reused_dataset_uri("act-v2-ft160") == uri


def test_reused_dataset_uri_falls_back_when_there_is_no_note(load_runner, tmp_path):
    """Without the note, or with an empty one, the lineage value stays 'reused'."""
    runner = load_runner(FLYWHEEL_DATA=str(tmp_path))
    assert runner.reused_dataset_uri("nothing-here") == "reused"
    d = tmp_path / "train" / "blank"
    d.mkdir(parents=True)
    (d / "dataset_uri.txt").write_text("  " + chr(10))
    assert runner.reused_dataset_uri("blank") == "reused"
