# This project was developed with assistance from AI tools.
"""Spec section 8: eval-origin records are complete by construction."""
from eval_dashboard import aggregate, schema
from eval_dashboard.main import Store


def ep(episode_id, origin, task_success=True, curation_verdict="pass", model_version="v1"):
    """A normalized record with an explicit origin."""
    return {
        "episode_id": episode_id,
        "model_version": model_version,
        "has_failure": False,
        "task_success": task_success,
        "cubes_placed": 3 if task_success else 1,
        "avg_smoothness": 0.005,
        "scene": "place_cubes_on_tray",
        "timestamp": "2026-09-20T01:00:00Z",
        "curation_verdict": curation_verdict,
        "rollout_steps": 100,
        "rollout_duration_s": 10.0,
        "seed": 1000,
        "origin": origin,
        "rollout_status": None,
    }


def all_pass_harness_record(label="act-v2-ft160", n=6):
    """A harness record in which every episode succeeded."""
    return {
        "model_version": f"eval-{label}",
        "policy_path": f"/models/{label}",
        "timestamp": "2026-09-20T01:00:00Z",
        "eval_config": {"episodes": n, "seed_base": 1000},
        "aggregate": {"n": n, "successes": n, "success_rate": 1.0, "mean_cubes": 3.0},
        "episodes": [
            {"index": i, "seed": 1000 + i, "task_success": True, "cubes_placed": 3, "avg_smoothness": 0.004, "steps": 400, "duration_s": 40.0}
            for i in range(n)
        ],
    }


def test_live_pass_only_population_is_still_flagged():
    """Live records with passes and no reject are still flagged incomplete."""
    stats = aggregate.aggregate([ep(f"e{i}", "live") for i in range(5)])["v1"]
    assert stats.success_rate_incomplete is True


def test_eval_origin_pass_only_population_is_not_flagged():
    """Eval-origin records are never flagged, even carrying only pass verdicts."""
    stats = aggregate.aggregate([ep(f"e{i}", "eval") for i in range(5)])["v1"]
    assert stats.success_rate_incomplete is False
    assert stats.success_rate == 1.0


def test_eval_origin_without_verdicts_is_not_flagged():
    """Eval-origin records with no verdict at all show their rate."""
    stats = aggregate.aggregate([ep(f"e{i}", "eval", curation_verdict=None) for i in range(5)])["v1"]
    assert stats.success_rate_incomplete is False
    assert stats.success_rate == 1.0


def test_live_population_with_both_verdicts_is_not_flagged():
    """Live records with a reject among them are complete, as before."""
    records = [ep("a", "live"), ep("b", "live", task_success=False, curation_verdict="reject")]
    assert aggregate.aggregate(records)["v1"].success_rate_incomplete is False


def test_origin_is_judged_per_version():
    """An eval-origin version beside a pass-only live version: only the live one is flagged."""
    records = [ep(f"e{i}", "eval", model_version="act-v2-ft160") for i in range(4)]
    records += [ep(f"l{i}", "live", model_version="upstream-act-teacher") for i in range(4)]
    stats = aggregate.aggregate(records)
    assert stats["act-v2-ft160"].success_rate_incomplete is False
    assert stats["upstream-act-teacher"].success_rate_incomplete is True


def test_exploded_perfect_run_shows_its_rate_in_the_snapshot():
    """A 100 % eval run reaches the snapshot with its rate and no incomplete flag."""
    store = Store()
    store.merge(schema.normalize(raw) for raw in schema.explode_harness_record(all_pass_harness_record()))
    block = store.snapshot()["versions"]["act-v2-ft160"]
    assert block["episode_count"] == 6
    assert block["success_rate"] == 1.0
    assert block["success_rate_incomplete"] is False


def test_snapshot_flag_for_eval_origin_records_with_pass_verdicts():
    """The snapshot flag is off for eval-origin records and on for live ones."""
    store = Store()
    store.merge([ep(f"e{i}", "eval", model_version="act-v2-ft160") for i in range(3)])
    store.merge([ep(f"l{i}", "live", model_version="upstream-act-teacher") for i in range(3)])
    versions = store.snapshot()["versions"]
    assert versions["act-v2-ft160"]["success_rate_incomplete"] is False
    assert versions["act-v2-ft160"]["success_rate"] == 1.0
    assert versions["upstream-act-teacher"]["success_rate_incomplete"] is True
