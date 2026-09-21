# This project was developed with assistance from AI tools.
"""Spec section 3: sources/eval_source.py, plus the section 5 payload helper and the section 4 store contract."""
import copy
import importlib
import json
import os
from datetime import datetime, timezone

import pytest

CANDIDATE = "act-v2-ft160"
INCUMBENT = "upstream-act-teacher"
REPORT_NAME = "eval_report.json"
CANDIDATE_NAME = f"eval-{CANDIDATE}.json"
INCUMBENT_NAME = f"eval-{INCUMBENT}.json"

# (seed, task_success, cubes_placed), deliberately not in seed order.
CANDIDATE_EPISODES = [
    (1008, False, 0),  # broken
    (1000, True, 3),  # both pass
    (1005, True, 3),  # fixed
    (1002, False, 1),  # broken
    (1006, True, 3),  # candidate only
    (1001, True, 3),  # fixed
    (1004, True, 3),  # fixed (incumbent's sensor reading was null)
    (1003, False, 2),  # both fail
]
INCUMBENT_EPISODES = [
    (1007, True, 3),  # incumbent only
    (1004, False, None),  # failure with unreadable ground truth
    (1000, True, 3),
    (1008, True, 3),
    (1001, False, 2),
    (1003, False, 1),
    (1005, False, 0),
    (1002, True, 3),
]
EXPECTED_FIXED = [1001, 1004, 1005]
EXPECTED_BROKEN = [1002, 1008]
EXPECTED_N_PAIRED = 7


@pytest.fixture
def es():
    """The module under test, imported per test so each one reports on its own."""
    return importlib.import_module("eval_dashboard.sources.eval_source")


def harness_record(label, outcomes, timestamp="2026-09-20T01:00:00Z"):
    """One per-policy harness record in the shape the pipeline writes."""
    episodes = [
        {
            "index": index,
            "seed": seed,
            "task_success": success,
            "cubes_placed": cubes,
            "avg_smoothness": 0.0123,
            "steps": 412,
            "duration_s": 41.2,
            "early_stopped": success,
            "goal_accepted": True,
        }
        for index, (seed, success, cubes) in enumerate(outcomes)
    ]
    successes = sum(1 for _, success, _ in outcomes if success)
    return {
        "model_version": f"eval-{label}",
        "policy_path": f"/models/{label}",
        "timestamp": timestamp,
        "eval_config": {"episodes": len(episodes), "seed_base": 1000},
        "aggregate": {"n": len(episodes), "successes": successes, "success_rate": successes / len(episodes), "mean_cubes": 2.0},
        "episodes": episodes,
    }


def eval_report(run_id="r1", timestamp="2026-09-20T01:02:03Z", **overrides):
    """The pipeline's report; its counts deliberately differ from the small episode fixture."""
    report = {
        "run_id": run_id,
        "candidate": CANDIDATE,
        "incumbent": INCUMBENT,
        "n_paired": 360,
        "candidate_success_rate": 0.925,
        "incumbent_success_rate": 0.8194,
        "delta": 0.1056,
        "fixed": 57,
        "broken": 19,
        "net": 38,
        "sign_test_p": 0.0,
        "candidate_mean_cubes": 2.9,
        "incumbent_mean_cubes": 2.7,
        "rule": "promote iff net > 0 and p < 0.05 (D022)",
        "verdict": "PASS",
        "timestamp": timestamp,
    }
    report.update(overrides)
    return report


def run_objects(run_id="r1", timestamp="2026-09-20T01:02:03Z"):
    """The three objects of one promotion run, keyed by object name."""
    return {
        REPORT_NAME: eval_report(run_id, timestamp),
        CANDIDATE_NAME: harness_record(CANDIDATE, CANDIDATE_EPISODES),
        INCUMBENT_NAME: harness_record(INCUMBENT, INCUMBENT_EPISODES),
    }


def read_named(reader, run_id, filename):
    """Read one object; the spec does not say whether `name` carries the .json suffix, so try both."""
    found = reader.read_json(run_id, filename)
    if found is None and filename.endswith(".json"):
        found = reader.read_json(run_id, filename[: -len(".json")])
    return found


class MemoryReader:
    """Minimal reader: just the two methods the spec names."""

    def __init__(self, runs):
        self._runs = runs

    def list_runs(self):
        return [run_id for run_id, objects in self._runs.items() if REPORT_NAME in objects]

    def read_json(self, run_id, name):
        objects = self._runs.get(run_id, {})
        if name in objects:
            return objects[name]
        return objects.get(f"{name}.json")


def write_run(root, run_id, objects, mtime=None):
    """Write one run directory; optionally pin every mtime in it."""
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, body in objects.items():
        path = run_dir / name
        path.write_text(json.dumps(body))
        if mtime is not None:
            os.utime(path, (mtime, mtime))
    if mtime is not None:
        os.utime(run_dir, (mtime, mtime))
    return run_dir


# --- paired_outcomes -------------------------------------------------------


def seed_records(outcomes):
    return [{"seed": seed, "task_success": success} for seed, success, _ in outcomes]


def test_paired_outcomes_counts_pairs_by_seed(es):
    """Only seeds present on both sides are paired."""
    result = es.paired_outcomes(seed_records(CANDIDATE_EPISODES), seed_records(INCUMBENT_EPISODES))
    assert result["n_paired"] == EXPECTED_N_PAIRED


def test_paired_outcomes_fixed_is_incumbent_fail_candidate_pass(es):
    """fixed lists seeds the incumbent failed and the candidate passed."""
    result = es.paired_outcomes(seed_records(CANDIDATE_EPISODES), seed_records(INCUMBENT_EPISODES))
    assert result["fixed"] == EXPECTED_FIXED


def test_paired_outcomes_broken_is_incumbent_pass_candidate_fail(es):
    """broken lists seeds the incumbent passed and the candidate failed."""
    result = es.paired_outcomes(seed_records(CANDIDATE_EPISODES), seed_records(INCUMBENT_EPISODES))
    assert result["broken"] == EXPECTED_BROKEN


def test_paired_outcomes_counts_both_pass_and_both_fail(es):
    """both_pass and both_fail are integer counts."""
    result = es.paired_outcomes(seed_records(CANDIDATE_EPISODES), seed_records(INCUMBENT_EPISODES))
    assert result["both_pass"] == 1
    assert result["both_fail"] == 1


def test_paired_outcomes_categories_sum_to_n_paired(es):
    """The four outcome categories partition the paired seeds."""
    result = es.paired_outcomes(seed_records(CANDIDATE_EPISODES), seed_records(INCUMBENT_EPISODES))
    total = len(result["fixed"]) + len(result["broken"]) + result["both_pass"] + result["both_fail"]
    assert total == result["n_paired"]


def test_paired_outcomes_ignores_seeds_on_one_side_only(es):
    """A seed only one policy ran appears in no list."""
    result = es.paired_outcomes(seed_records(CANDIDATE_EPISODES), seed_records(INCUMBENT_EPISODES))
    assert 1006 not in result["fixed"] + result["broken"]
    assert 1007 not in result["fixed"] + result["broken"]


def test_paired_outcomes_seed_lists_are_ascending_whatever_the_input_order(es):
    """Seed lists are sorted ascending regardless of input order."""
    candidate = list(reversed(seed_records(CANDIDATE_EPISODES)))
    incumbent = seed_records(INCUMBENT_EPISODES)
    result = es.paired_outcomes(candidate, incumbent)
    assert result["fixed"] == sorted(result["fixed"]) == EXPECTED_FIXED
    assert result["broken"] == sorted(result["broken"]) == EXPECTED_BROKEN


def test_paired_outcomes_argument_order_decides_direction(es):
    """Swapping the arguments swaps fixed and broken."""
    result = es.paired_outcomes(seed_records(INCUMBENT_EPISODES), seed_records(CANDIDATE_EPISODES))
    assert result["fixed"] == EXPECTED_BROKEN
    assert result["broken"] == EXPECTED_FIXED


def test_paired_outcomes_with_no_records(es):
    """Empty inputs give zero pairs and empty lists."""
    result = es.paired_outcomes([], [])
    assert result["n_paired"] == 0
    assert result["fixed"] == []
    assert result["broken"] == []
    assert result["both_pass"] == 0
    assert result["both_fail"] == 0


def test_paired_outcomes_with_no_overlap(es):
    """Disjoint seed sets give zero pairs."""
    result = es.paired_outcomes([{"seed": 1, "task_success": True}], [{"seed": 2, "task_success": False}])
    assert result["n_paired"] == 0
    assert result["fixed"] == []
    assert result["broken"] == []


def test_paired_outcomes_accepts_normalized_records(es):
    """Full normalized records work as well as the two-key minimum."""
    from eval_dashboard import schema

    def normalized(label, outcomes):
        return [schema.normalize(raw) for raw in schema.explode_harness_record(harness_record(label, outcomes))]

    result = es.paired_outcomes(normalized(CANDIDATE, CANDIDATE_EPISODES), normalized(INCUMBENT, INCUMBENT_EPISODES))
    assert result["n_paired"] == EXPECTED_N_PAIRED
    assert result["fixed"] == EXPECTED_FIXED
    assert result["broken"] == EXPECTED_BROKEN


# --- paired_payload (section 5) --------------------------------------------

REPORT_SCALARS = (
    "run_id",
    "candidate",
    "incumbent",
    "n_paired",
    "fixed",
    "broken",
    "net",
    "sign_test_p",
    "verdict",
    "rule",
    "candidate_success_rate",
    "incumbent_success_rate",
    "delta",
    "timestamp",
)


def small_outcomes():
    return {"n_paired": EXPECTED_N_PAIRED, "fixed": EXPECTED_FIXED, "broken": EXPECTED_BROKEN, "both_pass": 1, "both_fail": 1}


def test_paired_payload_has_every_named_key(es):
    """The payload carries all the scalar keys and both seed lists."""
    payload = es.paired_payload(eval_report(), small_outcomes())
    for key in REPORT_SCALARS + ("fixed_seeds", "broken_seeds"):
        assert key in payload, key


def test_paired_payload_scalars_are_the_report_verbatim(es):
    """Every scalar is the report's own value."""
    report = eval_report()
    payload = es.paired_payload(report, small_outcomes())
    for key in REPORT_SCALARS:
        assert payload[key] == report[key], key


def test_paired_payload_never_recomputes_counts_from_outcomes(es):
    """fixed, broken and n_paired stay the report's numbers when outcomes disagree."""
    payload = es.paired_payload(eval_report(), small_outcomes())
    assert payload["fixed"] == 57
    assert payload["broken"] == 19
    assert payload["n_paired"] == 360
    assert payload["net"] == 38


def test_paired_payload_seed_lists_come_from_outcomes(es):
    """fixed_seeds and broken_seeds are the outcomes' seed lists."""
    payload = es.paired_payload(eval_report(), small_outcomes())
    assert payload["fixed_seeds"] == EXPECTED_FIXED
    assert payload["broken_seeds"] == EXPECTED_BROKEN


def test_paired_payload_keeps_a_zero_p_value(es):
    """A sign_test_p of 0.0 is reported as 0.0, not dropped."""
    payload = es.paired_payload(eval_report(sign_test_p=0.0), small_outcomes())
    assert payload["sign_test_p"] == 0.0
    assert payload["sign_test_p"] is not None


def test_paired_payload_keeps_policy_labels_as_the_report_has_them(es):
    """candidate and incumbent are not cleaned for display."""
    report = eval_report(candidate="eval-act-v2-ft160", incumbent="eval-upstream-act-teacher")
    payload = es.paired_payload(report, small_outcomes())
    assert payload["candidate"] == "eval-act-v2-ft160"
    assert payload["incumbent"] == "eval-upstream-act-teacher"


def test_paired_payload_passes_a_fail_verdict_through(es):
    """A FAIL verdict and negative net are reported as the report has them."""
    report = eval_report(verdict="FAIL", net=-4, fixed=3, broken=7, delta=-0.0111, sign_test_p=0.3438)
    payload = es.paired_payload(report, small_outcomes())
    assert payload["verdict"] == "FAIL"
    assert payload["net"] == -4
    assert payload["delta"] == -0.0111
    assert payload["sign_test_p"] == 0.3438


def test_paired_payload_does_not_mutate_its_inputs(es):
    """Assembling the payload leaves the report and outcomes untouched."""
    report, outcomes = eval_report(), small_outcomes()
    report_before, outcomes_before = copy.deepcopy(report), copy.deepcopy(outcomes)
    es.paired_payload(report, outcomes)
    assert report == report_before
    assert outcomes == outcomes_before


# --- EvalSource.load -------------------------------------------------------


def test_load_returns_the_four_named_keys(es):
    """load() returns run_id, report, records and policies."""
    loaded = es.EvalSource(MemoryReader({"r1": run_objects()})).load()
    for key in ("run_id", "report", "records", "policies"):
        assert key in loaded, key


def test_load_returns_none_when_there_is_no_run(es):
    """With no run available load() returns None."""
    assert es.EvalSource(MemoryReader({})).load() is None


def test_load_picks_the_newest_run_by_default(es):
    """Without a pinned id the first run the reader lists is loaded."""
    reader = MemoryReader({"r-new": run_objects("r-new"), "r-old": run_objects("r-old")})
    loaded = es.EvalSource(reader).load()
    assert loaded["run_id"] == "r-new"
    assert loaded["report"]["run_id"] == "r-new"


def test_load_honours_a_pinned_run_id(es):
    """A run_id given to the constructor is loaded instead of the newest."""
    reader = MemoryReader({"r-new": run_objects("r-new"), "r-old": run_objects("r-old")})
    loaded = es.EvalSource(reader, run_id="r-old").load()
    assert loaded["run_id"] == "r-old"
    assert loaded["report"]["run_id"] == "r-old"


def test_load_accepts_run_id_positionally(es):
    """run_id is the constructor's second positional parameter."""
    reader = MemoryReader({"r-new": run_objects("r-new"), "r-old": run_objects("r-old")})
    assert es.EvalSource(reader, "r-old").load()["run_id"] == "r-old"


def test_load_of_a_pinned_run_that_does_not_exist_is_none(es):
    """A pinned run that has not appeared yet loads as None."""
    reader = MemoryReader({"r-new": run_objects("r-new")})
    assert es.EvalSource(reader, run_id="r-not-yet").load() is None


def test_load_report_is_the_report_object(es):
    """report is eval_report.json as read."""
    loaded = es.EvalSource(MemoryReader({"r1": run_objects()})).load()
    assert loaded["report"] == eval_report()


def test_load_policies_hold_clean_labels(es):
    """policies names the candidate and incumbent without the eval- prefix."""
    loaded = es.EvalSource(MemoryReader({"r1": run_objects()})).load()
    assert loaded["policies"] == {"candidate": CANDIDATE, "incumbent": INCUMBENT}


def test_load_records_cover_both_policies(es):
    """records holds every episode of both harness records."""
    records = es.EvalSource(MemoryReader({"r1": run_objects()})).load()["records"]
    assert len(records) == len(CANDIDATE_EPISODES) + len(INCUMBENT_EPISODES)
    assert sorted(r["seed"] for r in records if r["model_version"] == CANDIDATE) == sorted(s for s, _, _ in CANDIDATE_EPISODES)
    assert sorted(r["seed"] for r in records if r["model_version"] == INCUMBENT) == sorted(s for s, _, _ in INCUMBENT_EPISODES)


def test_load_records_put_the_candidate_first(es):
    """All candidate records come before any incumbent record."""
    records = es.EvalSource(MemoryReader({"r1": run_objects()})).load()["records"]
    labels = [r["model_version"] for r in records]
    assert labels == [CANDIDATE] * len(CANDIDATE_EPISODES) + [INCUMBENT] * len(INCUMBENT_EPISODES)


def test_load_records_are_normalized(es):
    """records are normalize() output: flat rollout fields, seed, eval origin."""
    records = es.EvalSource(MemoryReader({"r1": run_objects()})).load()["records"]
    for record in records:
        assert record["origin"] == "eval"
        assert isinstance(record["seed"], int)
        assert record["rollout_steps"] == 412
        assert record["rollout_duration_s"] == 41.2
        assert record["has_failure"] is False
        assert record["episode_id"] == f"{record['model_version']}-seed{record['seed']}"


def test_load_keeps_a_null_cubes_failure(es):
    """A failure with cubes_placed null is kept, with None cubes."""
    records = es.EvalSource(MemoryReader({"r1": run_objects()})).load()["records"]
    sensor = [r for r in records if r["model_version"] == INCUMBENT and r["seed"] == 1004]
    assert len(sensor) == 1
    assert sensor[0]["cubes_placed"] is None
    assert sensor[0]["task_success"] is False


def test_load_drops_items_normalize_refuses(es):
    """Items normalize() rejects do not reach records."""
    objects = run_objects()
    objects[CANDIDATE_NAME]["episodes"].append(
        {"index": 90, "seed": 1090, "task_success": True, "cubes_placed": 7, "avg_smoothness": 0.01, "steps": 10, "duration_s": 1.0}
    )
    objects[CANDIDATE_NAME]["episodes"].append(
        {"index": 91, "seed": 1091, "task_success": None, "cubes_placed": 3, "avg_smoothness": 0.01, "steps": 10, "duration_s": 1.0}
    )
    records = es.EvalSource(MemoryReader({"r1": objects})).load()["records"]
    assert all(r is not None for r in records)
    seeds = {r["seed"] for r in records if r["model_version"] == CANDIDATE}
    assert 1090 not in seeds
    assert 1091 not in seeds
    assert len(records) == len(CANDIDATE_EPISODES) + len(INCUMBENT_EPISODES)


def test_load_complete_run_reports_nothing_missing(es):
    """A run with all three objects has no missing names."""
    loaded = es.EvalSource(MemoryReader({"r1": run_objects()})).load()
    assert not loaded.get("missing")


def test_load_with_a_missing_incumbent_record_returns_what_it_has(es):
    """Without the incumbent's record load() still returns the report and candidate records."""
    objects = run_objects()
    del objects[INCUMBENT_NAME]
    loaded = es.EvalSource(MemoryReader({"r1": objects})).load()
    assert loaded is not None
    assert loaded["run_id"] == "r1"
    assert loaded["report"] == eval_report()
    assert {r["model_version"] for r in loaded["records"]} == {CANDIDATE}
    assert len(loaded["records"]) == len(CANDIDATE_EPISODES)


def test_load_names_the_missing_harness_record(es):
    """missing lists exactly the absent object, identifiable by its policy."""
    objects = run_objects()
    del objects[INCUMBENT_NAME]
    loaded = es.EvalSource(MemoryReader({"r1": objects})).load()
    assert isinstance(loaded["missing"], list)
    assert len(loaded["missing"]) == 1
    assert INCUMBENT in loaded["missing"][0]


def test_load_with_both_harness_records_missing(es):
    """A report on its own loads with no records and two missing names."""
    loaded = es.EvalSource(MemoryReader({"r1": {REPORT_NAME: eval_report()}})).load()
    assert loaded["report"] == eval_report()
    assert loaded["records"] == []
    assert len(loaded["missing"]) == 2


# --- section 4: the store holds one run's records --------------------------


def test_store_holds_both_policies_for_shared_seeds(es):
    """Candidate and incumbent records for one seed do not collide in the store."""
    from eval_dashboard.main import Store

    records = es.EvalSource(MemoryReader({"r1": run_objects()})).load()["records"]
    store = Store()
    store.replace(records)
    snapshot = store.snapshot()
    assert snapshot["episode_count"] == len(records)
    assert snapshot["conflicting_episode_ids"] == 0
    assert snapshot["dropped_invalid"] == 0
    assert set(snapshot["versions"]) == {CANDIDATE, INCUMBENT}


def test_store_replace_swaps_one_run_for_another(es):
    """Replacing with a newer run leaves only that run's records."""
    from eval_dashboard.main import Store

    newer = {
        REPORT_NAME: eval_report("r2", candidate="act-v3-ft320"),
        "eval-act-v3-ft320.json": harness_record("act-v3-ft320", [(2000, True, 3), (2001, False, 1)]),
        INCUMBENT_NAME: harness_record(INCUMBENT, [(2000, True, 3), (2001, True, 3)]),
    }
    reader = MemoryReader({"r2": newer, "r1": run_objects("r1")})

    store = Store()
    store.replace(es.EvalSource(reader, run_id="r1").load()["records"])
    store.replace(es.EvalSource(reader).load()["records"])

    snapshot = store.snapshot()
    assert snapshot["episode_count"] == 4
    assert set(snapshot["versions"]) == {"act-v3-ft320", INCUMBENT}
    assert {r["seed"] for r in store.episodes()} == {2000, 2001}


# --- DirEvalReader ---------------------------------------------------------


def test_dir_reader_lists_runs_that_have_a_report(es, tmp_path):
    """Only run directories containing eval_report.json are listed."""
    write_run(tmp_path, "r1", run_objects("r1"))
    write_run(tmp_path, "r-no-report", {CANDIDATE_NAME: harness_record(CANDIDATE, CANDIDATE_EPISODES)})
    (tmp_path / "notes.txt").write_text("not a run")
    assert es.DirEvalReader(str(tmp_path)).list_runs() == ["r1"]


def test_dir_reader_empty_root_has_no_runs(es, tmp_path):
    """An empty root lists no runs and loads as None."""
    reader = es.DirEvalReader(str(tmp_path))
    assert reader.list_runs() == []
    assert es.EvalSource(reader).load() is None


def test_dir_reader_orders_newest_first_by_report_timestamp(es, tmp_path):
    """Runs are ordered by the report's timestamp field, newest first."""
    for run_id, stamp in (("m-run", "2026-09-18T01:02:03Z"), ("a-run", "2026-09-20T01:02:03Z"), ("z-run", "2026-09-19T01:02:03Z")):
        write_run(tmp_path, run_id, run_objects(run_id, stamp), mtime=1_790_000_000)
    assert es.DirEvalReader(str(tmp_path)).list_runs() == ["a-run", "z-run", "m-run"]


def test_dir_reader_prefers_report_timestamp_over_mtime(es, tmp_path):
    """The timestamp field decides the order even when mtimes say otherwise."""
    write_run(tmp_path, "older", run_objects("older", "2026-09-10T00:00:00Z"), mtime=1_790_000_900)
    write_run(tmp_path, "newer", run_objects("newer", "2026-09-20T00:00:00Z"), mtime=1_790_000_100)
    assert es.DirEvalReader(str(tmp_path)).list_runs() == ["newer", "older"]


def test_dir_reader_falls_back_to_mtime_without_a_timestamp(es, tmp_path):
    """Reports with no timestamp field are ordered by file mtime."""
    for run_id, mtime in (("b-run", 1_790_000_100), ("c-run", 1_790_000_300), ("a-run", 1_790_000_200)):
        objects = run_objects(run_id)
        del objects[REPORT_NAME]["timestamp"]
        write_run(tmp_path, run_id, objects, mtime=mtime)
    assert es.DirEvalReader(str(tmp_path)).list_runs() == ["c-run", "a-run", "b-run"]


def test_dir_reader_reads_the_report(es, tmp_path):
    """read_json returns a run's report as a dict."""
    write_run(tmp_path, "r1", run_objects("r1"))
    assert read_named(es.DirEvalReader(str(tmp_path)), "r1", REPORT_NAME) == eval_report("r1")


def test_dir_reader_reads_a_harness_record(es, tmp_path):
    """read_json returns a run's harness record as a dict."""
    write_run(tmp_path, "r1", run_objects("r1"))
    found = read_named(es.DirEvalReader(str(tmp_path)), "r1", CANDIDATE_NAME)
    assert found == harness_record(CANDIDATE, CANDIDATE_EPISODES)


def test_dir_reader_missing_object_is_none(es, tmp_path):
    """read_json returns None for an object that is not there."""
    objects = run_objects("r1")
    del objects[INCUMBENT_NAME]
    write_run(tmp_path, "r1", objects)
    reader = es.DirEvalReader(str(tmp_path))
    assert read_named(reader, "r1", INCUMBENT_NAME) is None
    assert read_named(reader, "no-such-run", REPORT_NAME) is None


def test_dir_reader_non_object_json_is_none(es, tmp_path):
    """read_json returns None for JSON that is not an object."""
    write_run(tmp_path, "r1", run_objects("r1"))
    (tmp_path / "r1" / CANDIDATE_NAME).write_text("[1, 2, 3]")
    assert read_named(es.DirEvalReader(str(tmp_path)), "r1", CANDIDATE_NAME) is None


def test_dir_reader_root_that_is_itself_a_run(es, tmp_path):
    """A root containing eval_report.json is one run named after the directory."""
    root = write_run(tmp_path, "run-20260920", run_objects("run-20260920"))
    reader = es.DirEvalReader(str(root))
    assert reader.list_runs() == ["run-20260920"]
    assert read_named(reader, "run-20260920", REPORT_NAME) == eval_report("run-20260920")


def test_eval_source_loads_from_a_directory(es, tmp_path):
    """EvalSource over DirEvalReader loads the newest run end to end."""
    write_run(tmp_path, "older", run_objects("older", "2026-09-10T00:00:00Z"))
    write_run(tmp_path, "newer", run_objects("newer", "2026-09-20T00:00:00Z"))
    loaded = es.EvalSource(es.DirEvalReader(str(tmp_path))).load()
    assert loaded["run_id"] == "newer"
    assert loaded["report"] == eval_report("newer", "2026-09-20T00:00:00Z")
    assert len(loaded["records"]) == len(CANDIDATE_EPISODES) + len(INCUMBENT_EPISODES)
    assert loaded["policies"] == {"candidate": CANDIDATE, "incumbent": INCUMBENT}
    assert not loaded.get("missing")


def test_eval_source_loads_a_pinned_run_from_a_directory(es, tmp_path):
    """A pinned run id is loaded from a directory of several runs."""
    write_run(tmp_path, "older", run_objects("older", "2026-09-10T00:00:00Z"))
    write_run(tmp_path, "newer", run_objects("newer", "2026-09-20T00:00:00Z"))
    loaded = es.EvalSource(es.DirEvalReader(str(tmp_path)), run_id="older").load()
    assert loaded["run_id"] == "older"
    assert loaded["report"]["timestamp"] == "2026-09-10T00:00:00Z"


def test_eval_source_loads_a_single_run_root(es, tmp_path):
    """A root that is itself a run loads under the directory's name."""
    root = write_run(tmp_path, "run-20260920", run_objects("run-20260920"))
    loaded = es.EvalSource(es.DirEvalReader(str(root))).load()
    assert loaded["run_id"] == "run-20260920"
    assert len(loaded["records"]) == len(CANDIDATE_EPISODES) + len(INCUMBENT_EPISODES)


def test_eval_source_reports_missing_from_a_directory(es, tmp_path):
    """A directory run lacking one harness record loads with a missing entry."""
    objects = run_objects("r1")
    del objects[CANDIDATE_NAME]
    write_run(tmp_path, "r1", objects)
    loaded = es.EvalSource(es.DirEvalReader(str(tmp_path))).load()
    assert {r["model_version"] for r in loaded["records"]} == {INCUMBENT}
    assert len(loaded["missing"]) == 1
    assert CANDIDATE in loaded["missing"][0]


# --- S3EvalReader ----------------------------------------------------------


class FakeBody:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


class FakeS3Client:
    """Implements only list_objects_v2 and get_object, keyword-only like the real client."""

    def __init__(self, page_size=1000):
        self._objects = {}
        self._failing = {}
        self._page_size = page_size
        self.list_calls = []
        self.get_calls = []

    def put(self, bucket, key, body, last_modified):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self._objects[(bucket, key)] = (data, last_modified)

    def fail_on(self, key, error):
        self._failing[key] = error

    def list_objects_v2(self, **kwargs):
        self.list_calls.append(dict(kwargs))
        bucket, prefix = kwargs["Bucket"], kwargs.get("Prefix", "")
        keys = sorted(k for (b, k) in self._objects if b == bucket and k.startswith(prefix))
        start = 0
        if "ContinuationToken" in kwargs:
            token = kwargs["ContinuationToken"]
            if not isinstance(token, str):
                raise TypeError("ContinuationToken must be a string")
            start = int(token.split("-")[1])
        page = keys[start : start + self._page_size]
        truncated = start + self._page_size < len(keys)
        response = {"IsTruncated": truncated, "KeyCount": len(page), "Name": bucket, "Prefix": prefix}
        if page:
            response["Contents"] = [
                {"Key": k, "LastModified": self._objects[(bucket, k)][1], "Size": len(self._objects[(bucket, k)][0])} for k in page
            ]
        if truncated:
            response["NextContinuationToken"] = f"tok-{start + self._page_size}"
        return response

    def get_object(self, **kwargs):
        self.get_calls.append(dict(kwargs))
        bucket, key = kwargs["Bucket"], kwargs["Key"]
        if key in self._failing:
            raise self._failing[key]
        if (bucket, key) not in self._objects:
            raise KeyError(f"NoSuchKey: {key}")
        return {"Body": FakeBody(self._objects[(bucket, key)][0])}


def stamp(day, hour=1):
    return datetime(2026, 9, day, hour, 2, 3, tzinfo=timezone.utc)


def put_run(client, run_id, objects, last_modified, bucket="episodes-data", prefix="eval/", harness_last_modified=None):
    for name, body in objects.items():
        when = last_modified if name == REPORT_NAME or harness_last_modified is None else harness_last_modified
        client.put(bucket, f"{prefix}{run_id}/{name}", body, when)


def test_s3_reader_defaults_to_the_pipeline_bucket_and_prefix(es):
    """With no overrides the listing uses bucket episodes-data and prefix eval/."""
    client = FakeS3Client()
    put_run(client, "r1", run_objects("r1"), stamp(20))
    assert es.S3EvalReader(client).list_runs() == ["r1"]
    assert client.list_calls
    assert all(call["Bucket"] == "episodes-data" for call in client.list_calls)
    assert all(call["Prefix"].startswith("eval/") for call in client.list_calls)


def test_s3_reader_honours_bucket_and_prefix(es):
    """A configured bucket and prefix are what the client is asked for."""
    client = FakeS3Client()
    put_run(client, "r1", run_objects("r1"), stamp(20), bucket="other-bucket", prefix="runs/")
    put_run(client, "elsewhere", run_objects("elsewhere"), stamp(21))
    reader = es.S3EvalReader(client, bucket="other-bucket", prefix="runs/")
    assert reader.list_runs() == ["r1"]
    assert read_named(reader, "r1", REPORT_NAME) == eval_report("r1")
    assert all(call["Bucket"] == "other-bucket" for call in client.list_calls + client.get_calls)
    assert "runs/r1/eval_report.json" in [call["Key"] for call in client.get_calls]


def test_s3_reader_lists_only_runs_with_a_report(es):
    """A prefix holding harness records but no report is not a run."""
    client = FakeS3Client()
    put_run(client, "r1", run_objects("r1"), stamp(20))
    put_run(client, "r-no-report", {CANDIDATE_NAME: harness_record(CANDIDATE, CANDIDATE_EPISODES)}, stamp(21))
    assert es.S3EvalReader(client).list_runs() == ["r1"]


def test_s3_reader_empty_listing_has_no_runs(es):
    """A listing with no Contents key yields no runs and loads as None."""
    reader = es.S3EvalReader(FakeS3Client())
    assert reader.list_runs() == []
    assert es.EvalSource(reader).load() is None


def test_s3_reader_orders_newest_first_by_report_last_modified(es):
    """Runs are ordered by the report object's LastModified, newest first."""
    client = FakeS3Client()
    put_run(client, "m-run", run_objects("m-run", "2026-09-18T01:02:03Z"), stamp(18))
    put_run(client, "a-run", run_objects("a-run", "2026-09-20T01:02:03Z"), stamp(20))
    put_run(client, "z-run", run_objects("z-run", "2026-09-19T01:02:03Z"), stamp(19))
    assert es.S3EvalReader(client).list_runs() == ["a-run", "z-run", "m-run"]


def test_s3_reader_orders_by_the_report_object_not_its_neighbours(es):
    """A late harness upload does not make an older run the newest."""
    client = FakeS3Client()
    put_run(client, "older", run_objects("older", "2026-09-10T01:02:03Z"), stamp(10), harness_last_modified=stamp(25))
    put_run(client, "newer", run_objects("newer", "2026-09-20T01:02:03Z"), stamp(20), harness_last_modified=stamp(20))
    assert es.S3EvalReader(client).list_runs() == ["newer", "older"]


def test_s3_reader_orders_by_last_modified_not_the_timestamp_field(es):
    """LastModified decides the order even when the reports' timestamp fields disagree."""
    client = FakeS3Client()
    put_run(client, "uploaded-first", run_objects("uploaded-first", "2026-09-25T00:00:00Z"), stamp(10))
    put_run(client, "uploaded-last", run_objects("uploaded-last", "2026-09-01T00:00:00Z"), stamp(20))
    assert es.S3EvalReader(client).list_runs() == ["uploaded-last", "uploaded-first"]


def test_s3_reader_follows_a_truncated_listing(es):
    """Every page of a truncated listing is followed with its continuation token."""
    client = FakeS3Client(page_size=2)
    put_run(client, "r-a", run_objects("r-a"), stamp(18))
    put_run(client, "r-b", run_objects("r-b"), stamp(20))
    put_run(client, "r-c", run_objects("r-c"), stamp(19))
    assert es.S3EvalReader(client).list_runs() == ["r-b", "r-c", "r-a"]
    tokens = {call.get("ContinuationToken") for call in client.list_calls}
    assert {"tok-2", "tok-4", "tok-6", "tok-8"} <= tokens


def test_s3_reader_reads_objects_by_bucket_and_key(es):
    """read_json fetches '<prefix><run_id>/<object>' from the configured bucket."""
    client = FakeS3Client()
    put_run(client, "r1", run_objects("r1"), stamp(20))
    reader = es.S3EvalReader(client)
    assert read_named(reader, "r1", REPORT_NAME) == eval_report("r1")
    assert read_named(reader, "r1", INCUMBENT_NAME) == harness_record(INCUMBENT, INCUMBENT_EPISODES)
    assert {"Bucket": "episodes-data", "Key": "eval/r1/eval_report.json"} in [
        {"Bucket": call["Bucket"], "Key": call["Key"]} for call in client.get_calls
    ]


@pytest.mark.parametrize("error", [RuntimeError("storage unavailable"), KeyError("NoSuchKey"), OSError("connection reset")])
def test_s3_reader_get_object_failure_is_none(es, error):
    """Any exception from get_object makes read_json return None."""
    client = FakeS3Client()
    put_run(client, "r1", run_objects("r1"), stamp(20))
    client.fail_on(f"eval/r1/{CANDIDATE_NAME}", error)
    client.fail_on(f"eval/r1/{CANDIDATE_NAME[: -len('.json')]}", error)
    assert read_named(es.S3EvalReader(client), "r1", CANDIDATE_NAME) is None


def test_s3_reader_missing_object_is_none(es):
    """An object that is not in the bucket reads as None."""
    client = FakeS3Client()
    put_run(client, "r1", {REPORT_NAME: eval_report("r1")}, stamp(20))
    assert read_named(es.S3EvalReader(client), "r1", CANDIDATE_NAME) is None


@pytest.mark.parametrize("body", [b"this is not json", b"[1, 2, 3]", b'"a string"', b"42", b"null", b""])
def test_s3_reader_body_that_is_not_a_json_object_is_none(es, body):
    """A body that does not parse to a JSON object makes read_json return None."""
    client = FakeS3Client()
    put_run(client, "r1", run_objects("r1"), stamp(20))
    client.put("episodes-data", f"eval/r1/{CANDIDATE_NAME}", body, stamp(20))
    assert read_named(es.S3EvalReader(client), "r1", CANDIDATE_NAME) is None


def test_eval_source_loads_from_s3(es):
    """EvalSource over S3EvalReader loads the newest run end to end."""
    client = FakeS3Client(page_size=2)
    put_run(client, "older", run_objects("older"), stamp(10))
    put_run(client, "newer", run_objects("newer"), stamp(20))
    loaded = es.EvalSource(es.S3EvalReader(client)).load()
    assert loaded["run_id"] == "newer"
    assert loaded["report"] == eval_report("newer")
    assert len(loaded["records"]) == len(CANDIDATE_EPISODES) + len(INCUMBENT_EPISODES)
    assert loaded["policies"] == {"candidate": CANDIDATE, "incumbent": INCUMBENT}


def test_eval_source_survives_a_failing_harness_fetch_from_s3(es):
    """A harness record that cannot be fetched is reported missing, not raised."""
    client = FakeS3Client()
    put_run(client, "r1", run_objects("r1"), stamp(20))
    client.fail_on(f"eval/r1/{INCUMBENT_NAME}", RuntimeError("storage unavailable"))
    loaded = es.EvalSource(es.S3EvalReader(client)).load()
    assert loaded["report"] == eval_report("r1")
    assert {r["model_version"] for r in loaded["records"]} == {CANDIDATE}
    assert len(loaded["missing"]) == 1
    assert INCUMBENT in loaded["missing"][0]
