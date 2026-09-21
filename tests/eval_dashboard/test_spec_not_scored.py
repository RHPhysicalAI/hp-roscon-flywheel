# This project was developed with assistance from AI tools.
"""Spec section 7: episodes that are not a verdict on the policy."""
import pytest

from eval_dashboard import aggregate, schema
from eval_dashboard.main import Store

NONE_NOT_SCORED = {"sensor_unavailable": 0, "rollout_incomplete": 0}


def ep(episode_id, task_success, cubes_placed, rollout_status=None, model_version="v1", avg_smoothness=0.005, **extra):
    """A normalized record as the store and aggregate see it."""
    record = {
        "episode_id": episode_id,
        "model_version": model_version,
        "has_failure": False,
        "task_success": task_success,
        "cubes_placed": cubes_placed,
        "avg_smoothness": avg_smoothness,
        "scene": "place_cubes_on_tray",
        "timestamp": "2026-09-20T01:00:00Z",
        "curation_verdict": None,
        "rollout_steps": 100,
        "rollout_duration_s": 10.0,
        "seed": None,
        "origin": "live",
        "rollout_status": rollout_status,
    }
    record.update(extra)
    return record


def mixed_population():
    """Two successes, one scored failure, one sensor fault, one unfinished rollout."""
    return [
        ep("a", True, 3, "ok", avg_smoothness=0.004),
        ep("b", True, 3, None, avg_smoothness=0.006),
        ep("c", False, 1, "ok", avg_smoothness=0.001),
        ep("d", False, None, "ok", avg_smoothness=0.001),
        ep("e", False, 0, "timeout", avg_smoothness=0.001),
    ]


# --- not_scored_reason -----------------------------------------------------


def test_reason_is_none_for_a_plain_success():
    """A success is scored."""
    assert aggregate.not_scored_reason(ep("a", True, 3, "ok")) is None


def test_reason_is_none_for_a_success_with_null_cubes():
    """A success is never excluded, even with unreadable ground truth."""
    assert aggregate.not_scored_reason(ep("a", True, None, "ok")) is None


def test_reason_is_none_for_a_success_with_an_unfinished_rollout():
    """A success is never excluded, even when the rollout status is not ok."""
    assert aggregate.not_scored_reason(ep("a", True, 3, "timeout")) is None


def test_reason_sensor_unavailable_for_a_failure_with_null_cubes():
    """A failure with cubes_placed None is a sensor fault."""
    assert aggregate.not_scored_reason(ep("a", False, None, "ok")) == "sensor_unavailable"


def test_reason_sensor_unavailable_wins_over_rollout_incomplete():
    """With both conditions present the sensor reason is reported."""
    assert aggregate.not_scored_reason(ep("a", False, None, "timeout")) == "sensor_unavailable"


@pytest.mark.parametrize("status", ["timeout", "aborted", "error", "incomplete"])
def test_reason_rollout_incomplete_for_a_failure_with_a_non_ok_status(status):
    """A failure whose rollout_status is a non-empty string other than 'ok' is unfinished."""
    assert aggregate.not_scored_reason(ep("a", False, 1, status)) == "rollout_incomplete"


@pytest.mark.parametrize("status", ["ok", "", None])
def test_reason_is_none_for_a_scored_failure(status):
    """A failure with readable cubes and an ok, empty or absent status is a real failure."""
    assert aggregate.not_scored_reason(ep("a", False, 1, status)) is None


def test_reason_is_none_for_a_failure_with_zero_cubes():
    """Zero cubes placed is a reading, not a missing one."""
    assert aggregate.not_scored_reason(ep("a", False, 0, "ok")) is None


def test_reason_tolerates_a_record_without_the_rollout_status_key():
    """A record normalized before the change, with no rollout_status key, is scored."""
    record = ep("a", False, 1)
    del record["rollout_status"]
    assert aggregate.not_scored_reason(record) is None


# --- aggregate -------------------------------------------------------------


def test_aggregate_leaves_not_scored_out_of_the_counts():
    """episode_count and success_count cover scored records only."""
    stats = aggregate.aggregate(mixed_population())["v1"]
    assert stats.episode_count == 3
    assert stats.success_count == 2


def test_aggregate_rate_and_interval_use_scored_records_only():
    """The rate and its Wilson interval are computed over scored records."""
    stats = aggregate.aggregate(mixed_population())["v1"]
    assert stats.success_rate == pytest.approx(2 / 3)
    assert stats.success_ci == aggregate.wilson_ci(2, 3)


def test_aggregate_counts_not_scored_by_reason():
    """not_scored reports how many records each reason excluded."""
    stats = aggregate.aggregate(mixed_population())["v1"]
    assert stats.not_scored == {"sensor_unavailable": 1, "rollout_incomplete": 1}


def test_aggregate_leaves_not_scored_out_of_the_cube_histogram():
    """An unfinished rollout's cube count is not in the histogram or the mean."""
    stats = aggregate.aggregate(mixed_population())["v1"]
    assert stats.cubes_placed == {0: 0, 1: 1, 2: 0, 3: 2}
    assert stats.avg_cubes_placed == pytest.approx(7 / 3)


def test_aggregate_cube_bins_reconcile_once_sensor_faults_are_excluded():
    """With the null-cubes failure left out, the histogram sums to episode_count."""
    stats = aggregate.aggregate(mixed_population())["v1"]
    assert stats.cube_bins_reconcile is True


def test_aggregate_smoothness_ignores_not_scored_records():
    """Smoothness figures come from the scored successes only."""
    stats = aggregate.aggregate(mixed_population())["v1"]
    assert stats.smoothness_n == 2
    assert stats.mean_smoothness == pytest.approx(0.005)
    assert sum(stats.smoothness_hist.values()) == 2


def test_aggregate_not_scored_has_both_keys_when_nothing_is_excluded():
    """Both reasons are present, at zero, for a fully scored version."""
    stats = aggregate.aggregate([ep("a", True, 3, "ok"), ep("b", False, 1, "ok")])["v1"]
    assert stats.not_scored == NONE_NOT_SCORED
    assert stats.episode_count == 2


def test_version_stats_default_not_scored_has_both_keys():
    """A fresh VersionStats starts with both reasons at zero."""
    assert aggregate.VersionStats().not_scored == NONE_NOT_SCORED


def test_version_stats_not_scored_is_not_shared_between_instances():
    """Each VersionStats owns its own not_scored dict."""
    first, second = aggregate.VersionStats(), aggregate.VersionStats()
    first.not_scored["sensor_unavailable"] += 1
    assert second.not_scored == NONE_NOT_SCORED


def test_aggregate_keeps_a_success_with_null_cubes():
    """A success with unreadable cubes still counts as an episode and a success."""
    stats = aggregate.aggregate([ep("a", True, None, "ok"), ep("b", True, 3, "ok")])["v1"]
    assert stats.episode_count == 2
    assert stats.success_count == 2
    assert stats.not_scored == NONE_NOT_SCORED


def test_aggregate_version_with_only_not_scored_records_still_appears():
    """A version whose records are all not scored is present with episode_count 0."""
    records = [ep("a", False, None, "ok", model_version="v9"), ep("b", False, None, None, model_version="v9")]
    stats = aggregate.aggregate(records)
    assert "v9" in stats
    assert stats["v9"].episode_count == 0
    assert stats["v9"].success_count == 0
    assert stats["v9"].success_rate is None
    assert stats["v9"].success_ci == (None, None)
    assert stats["v9"].not_scored == {"sensor_unavailable": 2, "rollout_incomplete": 0}


def test_aggregate_not_scored_is_counted_per_version():
    """One version's excluded records do not show up on another."""
    records = [
        ep("a", False, None, "ok", model_version="v1"),
        ep("b", True, 3, "ok", model_version="v1"),
        ep("c", False, 2, "timeout", model_version="v2"),
        ep("d", False, 2, "ok", model_version="v2"),
    ]
    stats = aggregate.aggregate(records)
    assert stats["v1"].not_scored == {"sensor_unavailable": 1, "rollout_incomplete": 0}
    assert stats["v2"].not_scored == {"sensor_unavailable": 0, "rollout_incomplete": 1}
    assert stats["v1"].episode_count == 1
    assert stats["v2"].episode_count == 1
    assert stats["v2"].success_rate == 0.0


def test_a_sensor_fault_no_longer_lowers_the_rate():
    """One success plus one sensor-fault failure reads 100 %, not 50 %."""
    stats = aggregate.aggregate([ep("a", True, 3, "ok"), ep("b", False, None, "ok")])["v1"]
    assert stats.success_rate == 1.0


# --- Store.snapshot --------------------------------------------------------


def test_snapshot_adds_not_scored_to_each_version_block():
    """Every version block carries not_scored with both keys."""
    store = Store()
    store.merge(mixed_population() + [ep("f", True, 3, "ok", model_version="v2")])
    versions = store.snapshot()["versions"]
    assert versions["v1"]["not_scored"] == {"sensor_unavailable": 1, "rollout_incomplete": 1}
    assert versions["v2"]["not_scored"] == NONE_NOT_SCORED


def test_snapshot_top_level_episode_count_is_records_held():
    """The top-level count still includes not-scored records; the version's does not."""
    store = Store()
    store.merge(mixed_population())
    snapshot = store.snapshot()
    assert snapshot["episode_count"] == 5
    assert snapshot["versions"]["v1"]["episode_count"] == 3
    assert snapshot["versions"]["v1"]["success_count"] == 2


def test_snapshot_version_with_only_not_scored_records():
    """A version with nothing scored appears in the snapshot with a null rate."""
    store = Store()
    store.merge([ep("a", False, None, "ok", model_version="v9")])
    block = store.snapshot()["versions"]["v9"]
    assert block["episode_count"] == 0
    assert block["success_rate"] is None
    assert block["not_scored"] == {"sensor_unavailable": 1, "rollout_incomplete": 0}


def test_snapshot_counts_a_raw_record_with_an_unfinished_rollout():
    """A raw record with rollout.status set flows through normalize into not_scored."""
    raw = {
        "episode_id": "ep-timeout",
        "model_version": "v1",
        "task_success": False,
        "cubes_placed": 1,
        "rollout": {"steps": 900, "duration_s": 90.0, "status": "timeout"},
    }
    finished = {**raw, "episode_id": "ep-finished", "rollout": {"steps": 400, "duration_s": 40.0, "status": "ok"}}
    store = Store()
    store.merge([schema.normalize(raw), schema.normalize(finished)])
    block = store.snapshot()["versions"]["v1"]
    assert block["not_scored"] == {"sensor_unavailable": 0, "rollout_incomplete": 1}
    assert block["episode_count"] == 1
    assert block["success_rate"] == 0.0


def test_snapshot_counts_an_exploded_null_cubes_failure():
    """A harness episode with cubes_placed null is a sensor fault in the snapshot."""
    record = {
        "model_version": "eval-upstream-act-teacher",
        "timestamp": "2026-09-20T01:00:00Z",
        "episodes": [
            {"index": 0, "seed": 1000, "task_success": True, "cubes_placed": 3, "avg_smoothness": 0.01, "steps": 400, "duration_s": 40.0},
            {"index": 1, "seed": 1004, "task_success": False, "cubes_placed": None, "avg_smoothness": 0.01, "steps": 900, "duration_s": 90.0},
        ],
    }
    store = Store()
    store.merge(schema.normalize(raw) for raw in schema.explode_harness_record(record))
    snapshot = store.snapshot()
    block = snapshot["versions"]["upstream-act-teacher"]
    assert snapshot["episode_count"] == 2
    assert block["episode_count"] == 1
    assert block["success_rate"] == 1.0
    assert block["not_scored"] == {"sensor_unavailable": 1, "rollout_incomplete": 0}
