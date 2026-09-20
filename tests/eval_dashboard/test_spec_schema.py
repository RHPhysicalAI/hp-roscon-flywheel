# This project was developed with assistance from AI tools.
"""Spec section 2: harness records in schema.py."""
import pytest

from eval_dashboard import schema

CANDIDATE = "act-v2-ft160"
RECORD_TIMESTAMP = "2026-09-20T01:00:00Z"

TODAYS_KEYS = (
    "episode_id",
    "model_version",
    "has_failure",
    "task_success",
    "cubes_placed",
    "avg_smoothness",
    "scene",
    "timestamp",
    "curation_verdict",
    "rollout_steps",
    "rollout_duration_s",
)


def item(seed, task_success=True, cubes_placed=3, **extra):
    """One harness episode item in the shape the pipeline writes."""
    base = {
        "index": 0,
        "seed": seed,
        "task_success": task_success,
        "cubes_placed": cubes_placed,
        "avg_smoothness": 0.0123,
        "steps": 412,
        "duration_s": 41.2,
        "early_stopped": True,
        "goal_accepted": True,
    }
    base.update(extra)
    return base


def harness_record(episodes, label=CANDIDATE, timestamp=RECORD_TIMESTAMP):
    """One per-policy harness record in the shape the pipeline writes."""
    successes = sum(1 for e in episodes if isinstance(e, dict) and e.get("task_success") is True)
    return {
        "model_version": f"eval-{label}",
        "policy_path": f"/models/{label}",
        "timestamp": timestamp,
        "eval_config": {"episodes": len(episodes), "seed_base": 1000},
        "aggregate": {"n": len(episodes), "successes": successes, "success_rate": 0.5, "mean_cubes": 2.0},
        "episodes": episodes,
    }


def by_seed(exploded):
    return {r["seed"]: r for r in exploded}


# --- clean_model_version ---------------------------------------------------


def test_clean_model_version_strips_leading_eval_prefix():
    """A leading eval- is removed from the label."""
    assert schema.clean_model_version("eval-act-v2-ft160") == "act-v2-ft160"


def test_clean_model_version_leaves_unprefixed_label_unchanged():
    """A label without the prefix comes back as it was."""
    assert schema.clean_model_version("upstream-act-teacher") == "upstream-act-teacher"


def test_clean_model_version_strips_only_one_prefix():
    """Only one leading eval- is removed when the label starts with two."""
    assert schema.clean_model_version("eval-eval-teacher") == "eval-teacher"


def test_clean_model_version_ignores_eval_that_is_not_leading():
    """An eval- that is not at the start of the label is left alone."""
    assert schema.clean_model_version("act-eval-v2") == "act-eval-v2"


def test_clean_model_version_ignores_other_case():
    """Only the literal lower-case prefix is stripped."""
    assert schema.clean_model_version("EVAL-act-v2") == "EVAL-act-v2"


def test_clean_model_version_empty_string_unchanged():
    """An empty label stays empty."""
    assert schema.clean_model_version("") == ""


# --- explode_harness_record ------------------------------------------------


def test_explode_yields_one_raw_record_per_item():
    """Every valid episode item becomes one raw record."""
    record = harness_record([item(1000), item(1001, False, 1), item(1002, False, None)])
    assert len(schema.explode_harness_record(record)) == 3


def test_explode_uses_clean_model_version():
    """Exploded records carry the label without the eval- prefix."""
    exploded = schema.explode_harness_record(harness_record([item(1000), item(1001)]))
    assert {r["model_version"] for r in exploded} == {CANDIDATE}


def test_explode_builds_episode_id_from_policy_and_seed():
    """episode_id is '<clean model_version>-seed<seed>'."""
    exploded = by_seed(schema.explode_harness_record(harness_record([item(1000), item(1017)])))
    assert exploded[1000]["episode_id"] == "act-v2-ft160-seed1000"
    assert exploded[1017]["episode_id"] == "act-v2-ft160-seed1017"


def test_explode_episode_ids_differ_between_policies_for_the_same_seed():
    """Two policies evaluated on one seed get different episode ids."""
    candidate = schema.explode_harness_record(harness_record([item(1000)], label=CANDIDATE))
    incumbent = schema.explode_harness_record(harness_record([item(1000)], label="upstream-act-teacher"))
    assert candidate[0]["episode_id"] != incumbent[0]["episode_id"]
    assert incumbent[0]["episode_id"] == "upstream-act-teacher-seed1000"


def test_explode_sets_integer_seed_and_eval_origin():
    """Each exploded record has its integer seed and origin 'eval'."""
    exploded = schema.explode_harness_record(harness_record([item(1000), item(1001)]))
    assert sorted(r["seed"] for r in exploded) == [1000, 1001]
    assert all(isinstance(r["seed"], int) and not isinstance(r["seed"], bool) for r in exploded)
    assert all(r["origin"] == "eval" for r in exploded)


def test_explode_copies_outcome_fields_from_the_item():
    """task_success, cubes_placed and avg_smoothness come from the item."""
    record = harness_record([item(1000, True, 3, avg_smoothness=0.0042), item(1001, False, 1, avg_smoothness=0.0009)])
    exploded = by_seed(schema.explode_harness_record(record))
    assert exploded[1000]["task_success"] is True
    assert exploded[1000]["cubes_placed"] == 3
    assert exploded[1000]["avg_smoothness"] == 0.0042
    assert exploded[1001]["task_success"] is False
    assert exploded[1001]["cubes_placed"] == 1
    assert exploded[1001]["avg_smoothness"] == 0.0009


def test_explode_keeps_null_cubes_placed_as_none():
    """A null cubes_placed stays None rather than becoming zero."""
    exploded = schema.explode_harness_record(harness_record([item(1004, False, None)]))
    assert len(exploded) == 1
    assert exploded[0]["cubes_placed"] is None
    assert exploded[0]["task_success"] is False


def test_explode_nests_steps_and_duration_under_rollout():
    """Flat steps and duration_s move into a rollout dict."""
    exploded = schema.explode_harness_record(harness_record([item(1000, steps=412, duration_s=41.2)]))
    assert exploded[0]["rollout"] == {"steps": 412, "duration_s": 41.2}


def test_explode_rollout_has_only_the_keys_the_item_has():
    """A rollout key is present only when the item carries that field."""
    steps_only = item(1000)
    del steps_only["duration_s"]
    duration_only = item(1001)
    del duration_only["steps"]
    exploded = by_seed(schema.explode_harness_record(harness_record([steps_only, duration_only])))
    assert exploded[1000]["rollout"] == {"steps": 412}
    assert exploded[1001]["rollout"] == {"duration_s": 41.2}


def test_explode_item_without_steps_or_duration_has_no_rollout_keys():
    """An item with neither field yields no steps or duration_s key."""
    bare = item(1000)
    del bare["steps"]
    del bare["duration_s"]
    exploded = schema.explode_harness_record(harness_record([bare]))
    rollout = exploded[0].get("rollout") or {}
    assert "steps" not in rollout
    assert "duration_s" not in rollout


def test_explode_uses_record_level_timestamp():
    """Every exploded record carries the record-level timestamp."""
    exploded = schema.explode_harness_record(harness_record([item(1000), item(1001)], timestamp="2026-09-21T10:00:00Z"))
    assert [r["timestamp"] for r in exploded] == ["2026-09-21T10:00:00Z"] * 2


def test_explode_timestamp_may_be_none():
    """A null record-level timestamp is carried through as None."""
    exploded = schema.explode_harness_record(harness_record([item(1000)], timestamp=None))
    assert exploded[0]["timestamp"] is None


def test_explode_record_without_timestamp_key_gives_none():
    """A record with no timestamp key yields a None timestamp."""
    record = harness_record([item(1000)])
    del record["timestamp"]
    exploded = schema.explode_harness_record(record)
    assert exploded[0].get("timestamp") is None


def test_explode_has_no_curation_verdict_key():
    """Exploded records never carry a curation_verdict key."""
    exploded = schema.explode_harness_record(harness_record([item(1000), item(1001, False, 1)]))
    assert all("curation_verdict" not in r for r in exploded)


@pytest.mark.parametrize("bad_seed", [None, "1000", True, False, 1000.5])
def test_explode_skips_items_without_an_integer_seed(bad_seed):
    """A seed that is not an integer (bool included) drops the item."""
    record = harness_record([item(bad_seed), item(1001)])
    exploded = schema.explode_harness_record(record)
    assert [r["seed"] for r in exploded] == [1001]


def test_explode_skips_items_with_no_seed_key():
    """An item with no seed key at all is skipped."""
    seedless = item(1000)
    del seedless["seed"]
    exploded = schema.explode_harness_record(harness_record([seedless, item(1001)]))
    assert [r["seed"] for r in exploded] == [1001]


def test_explode_skips_items_without_task_success_key():
    """An item with no task_success key is skipped."""
    unscored = item(1000)
    del unscored["task_success"]
    exploded = schema.explode_harness_record(harness_record([unscored, item(1001, False, 2)]))
    assert [r["seed"] for r in exploded] == [1001]


@pytest.mark.parametrize("episodes", ["absent", None, {"seed": 1000}])
def test_explode_without_an_episodes_list_returns_empty(episodes):
    """A record whose episodes is missing or not a list explodes to []."""
    record = harness_record([])
    if episodes == "absent":
        del record["episodes"]
    else:
        record["episodes"] = episodes
    assert schema.explode_harness_record(record) == []


def test_explode_empty_episodes_list_returns_empty():
    """An empty episodes list explodes to []."""
    assert schema.explode_harness_record(harness_record([])) == []


def test_explode_does_not_mutate_the_input_record():
    """Exploding is pure: the harness record is left as it was."""
    record = harness_record([item(1000), item(1001, False, None)])
    before = repr(record)
    schema.explode_harness_record(record)
    assert repr(record) == before


# --- normalize -------------------------------------------------------------


def live_raw(**overrides):
    raw = {
        "episode_id": "ep-live-1",
        "model_version": "upstream-act-teacher",
        "task_success": True,
        "cubes_placed": 3,
        "avg_smoothness": 0.005,
        "has_failure": False,
        "curation_verdict": "pass",
    }
    raw.update(overrides)
    return raw


def test_normalize_keeps_todays_output_keys():
    """Every key normalize produced before the change is still produced."""
    normalized = schema.normalize(live_raw())
    assert all(key in normalized for key in TODAYS_KEYS)


def test_normalize_adds_the_three_new_keys():
    """seed, origin and rollout_status are always present in the output."""
    normalized = schema.normalize(live_raw())
    assert "seed" in normalized
    assert "origin" in normalized
    assert "rollout_status" in normalized


def test_normalize_live_record_defaults():
    """A record with none of the new fields gets None, 'live', None."""
    normalized = schema.normalize(live_raw())
    assert normalized["seed"] is None
    assert normalized["origin"] == "live"
    assert normalized["rollout_status"] is None


def test_normalize_carries_integer_seed():
    """An integer seed on the raw record reaches the output."""
    assert schema.normalize(live_raw(seed=1000))["seed"] == 1000


def test_normalize_non_integer_seed_becomes_none():
    """A seed that cannot be an integer is reported as None."""
    assert schema.normalize(live_raw(seed="not-a-seed"))["seed"] is None


def test_normalize_origin_eval_when_raw_says_so():
    """A raw origin of 'eval' is kept."""
    assert schema.normalize(live_raw(origin="eval"))["origin"] == "eval"


@pytest.mark.parametrize("raw_origin", ["live", "files", "", None])
def test_normalize_any_other_origin_is_live(raw_origin):
    """Any raw origin other than 'eval' normalizes to 'live'."""
    assert schema.normalize(live_raw(origin=raw_origin))["origin"] == "live"


def test_normalize_rollout_status_from_rollout_dict():
    """rollout['status'] is used when rollout is a dict."""
    normalized = schema.normalize(live_raw(rollout={"steps": 50, "duration_s": 5.0, "status": "timeout"}))
    assert normalized["rollout_status"] == "timeout"
    assert normalized["rollout_steps"] == 50


def test_normalize_rollout_status_from_flat_field():
    """The flat rollout_status is used when there is no rollout dict."""
    assert schema.normalize(live_raw(rollout_status="aborted"))["rollout_status"] == "aborted"


def test_normalize_rollout_dict_without_status_gives_none():
    """A rollout dict with no status, and no flat field, yields None."""
    assert schema.normalize(live_raw(rollout={"steps": 50}))["rollout_status"] is None


def test_normalize_of_exploded_record_matches_the_spec_sentence():
    """normalize(explode(r)[0]) has rollout_steps, rollout_duration_s, seed and origin 'eval'."""
    record = harness_record([item(1000, True, 3, steps=412, duration_s=41.2)])
    normalized = schema.normalize(schema.explode_harness_record(record)[0])
    assert normalized["rollout_steps"] == 412
    assert normalized["rollout_duration_s"] == 41.2
    assert normalized["seed"] == 1000
    assert normalized["origin"] == "eval"


def test_normalize_of_exploded_record_keeps_identity_and_outcome():
    """The normalized exploded record keeps id, label, outcome and timestamp."""
    record = harness_record([item(1004, False, None)])
    normalized = schema.normalize(schema.explode_harness_record(record)[0])
    assert normalized["episode_id"] == "act-v2-ft160-seed1004"
    assert normalized["model_version"] == CANDIDATE
    assert normalized["task_success"] is False
    assert normalized["cubes_placed"] is None
    assert normalized["timestamp"] == RECORD_TIMESTAMP
    assert normalized["curation_verdict"] is None
    assert normalized["rollout_status"] is None


def test_normalize_of_exploded_record_is_valid_for_the_store():
    """A normalized exploded record passes the store's validation."""
    record = harness_record([item(1000), item(1004, False, None)])
    for raw in schema.explode_harness_record(record):
        assert schema.validate_normalized(schema.normalize(raw)) is None


# --- lineage rule ----------------------------------------------------------


def test_lineage_rule_still_drops_live_eval_prefixed_records():
    """A live record whose label starts with eval- is still dropped."""
    records = [schema.normalize(live_raw(model_version="eval-teacher-v1")), schema.normalize(live_raw(episode_id="ep-2"))]
    kept = list(schema.apply_lineage_rules(records))
    assert [r["model_version"] for r in kept] == ["upstream-act-teacher"]


def test_lineage_rule_does_not_touch_exploded_records():
    """Exploded harness records pass the lineage rule unchanged."""
    record = harness_record([item(1000), item(1001, False, 1), item(1004, False, None)])
    normalized = [schema.normalize(raw) for raw in schema.explode_harness_record(record)]
    kept = list(schema.apply_lineage_rules(normalized))
    assert len(kept) == 3
    assert {r["model_version"] for r in kept} == {CANDIDATE}
    assert all("raw_model_version" not in r for r in kept)
