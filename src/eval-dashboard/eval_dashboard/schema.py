# This project was developed with assistance from AI tools.
"""Normalizes episode/manifest records from any source into one shape.

Every EpisodeSource funnels its raw JSON through `normalize()` before
handing records to the store/aggregate.py. That's what guarantees the file,
Kafka+MinIO, and MinIO-listing paths produce identical numbers for
identical underlying data -- there is exactly one place that knows what a
record looks like.

`dataset_path` is present on real records but intentionally unused here.
The Kafka manifest's reduced field set (sync-agent publishes a subset of
the full episode JSON) is tolerated -- missing optional fields become None.

The evaluation harness writes one record per policy with its episodes
inline; `explode_harness_record()` turns that into the raw per-episode
shape, so those go through `normalize()` like everything else.
"""
from __future__ import annotations

REQUIRED = ("episode_id", "model_version")
CUBE_BUCKETS = (0, 1, 2, 3)
EVAL_PREFIX = "eval-"

# Fields compared when deciding whether two records with the same episode_id
# are an idempotent re-list or a true conflict.
CONFLICT_FIELDS = ("model_version", "task_success", "cubes_placed", "has_failure", "curation_verdict")


def as_bool(value, default: bool = False) -> bool:
    """JSON booleans, 0/1, and the strings some exporters emit."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def conflict_tuple(record: dict) -> tuple:
    return tuple(record.get(f) for f in CONFLICT_FIELDS)


def records_conflict(a: dict, b: dict) -> bool:
    return conflict_tuple(a) != conflict_tuple(b)


def validate_normalized(record: dict) -> str | None:
    """Return an error string when a normalized record must be excluded."""
    if not record.get("episode_id"):
        return "missing episode_id"
    if not record.get("model_version"):
        return "missing model_version"
    if not isinstance(record.get("task_success"), bool):
        return "invalid task_success"
    if not isinstance(record.get("has_failure"), bool):
        return "invalid has_failure"
    cubes = record.get("cubes_placed")
    if cubes is not None:
        if isinstance(cubes, bool) or not isinstance(cubes, int) or cubes not in CUBE_BUCKETS:
            return "invalid cubes_placed"
    return None


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def clean_model_version(label: str) -> str:
    """Strip the one leading `eval-` the harness puts on a record's label."""
    if isinstance(label, str) and label.startswith(EVAL_PREFIX):
        return label[len(EVAL_PREFIX):]
    return label


def explode_harness_record(record: dict) -> list[dict]:
    """One raw per-episode record, in the shape `normalize()` takes, per scored harness episode."""
    if not isinstance(record, dict) or not isinstance(record.get("episodes"), list):
        return []
    model_version = clean_model_version(record.get("model_version"))
    if not model_version or not isinstance(model_version, str):
        return []
    exploded = []
    for item in record["episodes"]:
        if not isinstance(item, dict) or not _is_int(item.get("seed")) or "task_success" not in item:
            continue
        seed = item["seed"]
        exploded.append(
            {
                "model_version": model_version,
                # The policy is part of the id: the store de-duplicates by id across versions.
                "episode_id": f"{model_version}-seed{seed}",
                "seed": seed,
                "origin": "eval",
                "task_success": item["task_success"],
                "cubes_placed": item.get("cubes_placed"),
                "avg_smoothness": item.get("avg_smoothness"),
                "rollout": {k: item[k] for k in ("steps", "duration_s") if k in item},
                "timestamp": record.get("timestamp"),
            }
        )
    return exploded


def normalize(record: dict) -> dict | None:
    if not isinstance(record, dict) or not record.get("episode_id") or not record.get("model_version"):
        return None

    task_success_raw = record.get("task_success")
    if task_success_raw is None:
        return None
    task_success = as_bool(task_success_raw, False)

    has_failure_raw = record.get("has_failure")
    has_failure = as_bool(has_failure_raw, False) if has_failure_raw is not None else False

    cubes = record.get("cubes_placed")
    if cubes is not None:
        if isinstance(cubes, bool) or not isinstance(cubes, int) or cubes not in CUBE_BUCKETS:
            return None

    seed = record.get("seed")
    rollout = record.get("rollout")

    normalized = {
        "episode_id": record["episode_id"],
        "model_version": record["model_version"],
        "has_failure": has_failure,
        "task_success": task_success,
        "cubes_placed": cubes,
        "avg_smoothness": record.get("avg_smoothness"),
        "scene": record.get("scene"),
        "timestamp": record.get("timestamp"),
        "curation_verdict": record.get("curation_verdict"),
        "rollout_steps": (record.get("rollout") or {}).get("steps") if isinstance(record.get("rollout"), dict) else record.get("rollout_steps"),
        "rollout_duration_s": (record.get("rollout") or {}).get("duration_s") if isinstance(record.get("rollout"), dict) else record.get("rollout_duration_s"),
        "rollout_status": rollout.get("status") if isinstance(rollout, dict) else record.get("rollout_status"),
        "seed": seed if _is_int(seed) else None,
        "origin": "eval" if record.get("origin") == "eval" else "live",
    }
    if record.get("raw_model_version"):
        normalized["raw_model_version"] = record["raw_model_version"]
    if validate_normalized(normalized):
        return None
    return normalized


# Phase-2 baselines, predating the upstream-act-teacher lineage. Per
# data-contract-eval-dashboard.md: "group them as pre-teacher."
LEGACY_PRE_TEACHER_VERSIONS = {"soarm-act-10ep-10k", "soarm-act-v1"}


def apply_lineage_rules(records):
    """Applied to every record on every path (file replay and live).

    - Drops any model_version starting with "eval-": a 2026-09-08 bug let
      eval-harness test runs leak into production MinIO/Kafka. MinIO was
      scrubbed, but Kafka's episode-manifests topic is append-only, so
      replaying from the earliest offset still surfaces the old ones --
      this is the client-side filter the data contract calls for.
    - Rewrites the two known Phase-2 tags to "pre-teacher", preserving the
      original under `raw_model_version` so the episode-evidence table can
      still show what actually produced each row.
    """
    for r in records:
        if not r:
            continue
        mv = r["model_version"]
        if mv.startswith("eval-"):
            continue
        if mv in LEGACY_PRE_TEACHER_VERSIONS:
            r = {**r, "model_version": "pre-teacher", "raw_model_version": mv}
        yield r
