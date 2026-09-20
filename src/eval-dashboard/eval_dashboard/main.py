# This project was developed with assistance from AI tools.
"""Entrypoint: wires a configured source to one in-memory population and a
read-only Flask API/UI.

One view, per `model_version`, fed by whichever source is active:

- SOURCE_MODE=files -- a directory of episode JSON records (RECORDS_DIR).
- SOURCE_MODE=live -- Kafka+MinIO curated episodes, merged with the MinIO
  rejected bucket (Kafka never notifies for rejects).
- SOURCE_MODE=eval -- one promotion run's paired evaluation: the pipeline's
  report plus both policies' harness records (EVAL_DIR, or the evaluation
  bucket). No Kafka.

Persists nothing of its own: rebuilt from source on every process start, so
a restart or a fresh file-directory replay reproduces the same numbers.
"""
from __future__ import annotations

import logging
import os
import pathlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

import boto3
import yaml

from . import aggregate, schema
from .sources.eval_source import DirEvalReader, EvalSource, S3EvalReader, paired_block
from .sources.file_source import FileSource
from .sources.kafka_source import KafkaSource
from .sources.minio_source import MinioSource, uri_in_buckets
from .web.app import create_app

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("eval_dashboard")

REJECTED_POLL_SECONDS = 30
DEFAULT_VERSIONS_FILE = "/app/config/versions.yaml"
VERSIONS_EXAMPLE_NAME = "versions.example.yaml"


class Store:
    """Dedupes by episode_id. An identical re-list is a no-op; two records
    with the same episode_id but different scored fields are quarantined and
    excluded from aggregates. Injected demo failures (`has_failure`) are
    dropped before aggregate.py sees them."""

    def __init__(self, version_meta: dict[str, dict] | None = None, origin: str = ""):
        self._lock = threading.Lock()
        self._by_id: dict[str, dict] = {}
        self._dropped_failure_ids: set[str] = set()
        self._quarantined: set[str] = set()
        self._version_meta = version_meta or {}
        self.origin = origin
        self.dropped_has_failure = 0
        self.dropped_invalid = 0
        self.duplicate_episode_ids = 0
        self.conflicting_episode_ids = 0
        self.kafka_skipped_other_bucket = 0
        self.rejected_last_checked: float | None = None
        self._seed_complete = True

    def count_kafka_skip(self) -> None:
        with self._lock:
            self.kafka_skipped_other_bucket += 1

    def merge(self, records: Iterable[dict]) -> None:
        with self._lock:
            for r in records:
                if not r:
                    self.dropped_invalid += 1
                    continue
                if schema.validate_normalized(r):
                    self.dropped_invalid += 1
                    continue
                if r.get("has_failure"):
                    eid = r["episode_id"]
                    # Idempotent: the 30s rejected-bucket poll re-feeds the
                    # same objects; counting each pass would inflate the
                    # integrity panel.
                    if eid not in self._dropped_failure_ids:
                        self._dropped_failure_ids.add(eid)
                        self.dropped_has_failure += 1
                    continue
                eid = r["episode_id"]
                if eid in self._quarantined:
                    continue
                existing = self._by_id.get(eid)
                if existing is not None:
                    if not schema.records_conflict(existing, r):
                        continue
                    self._quarantined.add(eid)
                    del self._by_id[eid]
                    self.conflicting_episode_ids += 1
                    if existing.get("curation_verdict") != r.get("curation_verdict"):
                        self.duplicate_episode_ids += 1
                    continue
                self._by_id[eid] = r

    def replace(self, records: Iterable[dict]) -> None:
        """Rebuild from scratch. File-mode poll uses this so a restart and
        a later rescan of the same directory produce the same counters."""
        materialized = [r for r in records if r]
        with self._lock:
            self._by_id = {}
            self._dropped_failure_ids = set()
            self._quarantined = set()
            self.dropped_has_failure = 0
            self.dropped_invalid = 0
            self.duplicate_episode_ids = 0
            self.conflicting_episode_ids = 0
        self.merge(materialized)

    def episodes(self, model_version: str | None = None) -> list[dict]:
        with self._lock:
            records = list(self._by_id.values())
        if model_version:
            records = [r for r in records if r["model_version"] == model_version]
        return records

    def snapshot(self) -> dict:
        with self._lock:
            records = list(self._by_id.values())
            dropped = self.dropped_has_failure
            duplicates = self.duplicate_episode_ids
            rejected_last_checked = self.rejected_last_checked
            origin = self.origin
            seed_complete = self._seed_complete
            kafka_skipped = self.kafka_skipped_other_bucket
        stats = aggregate.aggregate(records)
        versions = {}
        for v, s in stats.items():
            ci_low, ci_high = s.success_ci
            # The versions file always wins; a size is read off the name only for a version it omits.
            if v in self._version_meta:
                meta = self._version_meta[v] or {}
            else:
                meta = {"size": infer_size(v)}
            versions[v] = {
                "episode_count": s.episode_count,
                "success_count": s.success_count,
                "success_rate": s.success_rate,
                "success_rate_incomplete": s.success_rate_incomplete,
                "success_ci": [ci_low, ci_high],
                "avg_cubes_placed": s.avg_cubes_placed,
                "mean_smoothness": s.mean_smoothness,
                "smoothness_n": s.smoothness_n,
                "cubes_placed": s.cubes_placed,
                "smoothness_hist": s.smoothness_hist,
                "cube_bins_reconcile": s.cube_bins_reconcile,
                "success_matches_full_cubes": s.success_matches_full_cubes,
                "not_scored": s.not_scored,
                "dataset_size": meta.get("size"),
                "parent": meta.get("parent"),
            }
        return {
            "origin": origin,
            "episode_count": len(records),
            "dropped_has_failure": dropped,
            "dropped_invalid": self.dropped_invalid,
            "duplicate_episode_ids": duplicates,
            "conflicting_episode_ids": self.conflicting_episode_ids,
            "kafka_skipped_other_bucket": kafka_skipped,
            "rejected_last_checked": rejected_last_checked,
            "seed_complete": seed_complete,
            "smoothness_bin_edges": aggregate.SMOOTHNESS_BINS,
            "versions": versions,
        }


def load_version_meta(path: str) -> dict[str, dict]:
    """Each entry is either a plain int (`version: 20`, no known parent -- a
    baseline or a version whose lineage isn't documented) or a dict
    (`version: {size: 20, parent: eval-teacher-v1}`) when the fine-tune
    parent is known. Both shapes coexist in the same file."""
    p = pathlib.Path(path)
    if not p.exists():
        # An image ships only the example; a deployment mounts the real file.
        p = p.with_name(VERSIONS_EXAMPLE_NAME)
        if not p.exists():
            return {}
    raw = yaml.safe_load(p.read_text()) or {}
    meta: dict[str, dict] = {}
    for version, entry in raw.items():
        if isinstance(entry, dict):
            meta[version] = {"size": entry.get("size"), "parent": entry.get("parent")}
        else:
            meta[version] = {"size": entry, "parent": None}
    return meta


def infer_size(model_version: str) -> int | None:
    """Training-set size read off a `-ft<N>` name, for versions the versions file omits."""
    # The last match: a candidate is named <collector>-ft<N>-<stamp>, and the collector's own name may carry one too.
    found = re.findall(r"-ft(\d+)", model_version) if isinstance(model_version, str) else []
    return int(found[-1]) if found else None


def watch_files(store: Store, directory: str, transform=lambda records: records) -> None:
    interval = float(os.environ.get("FILE_POLL_SECONDS", "5"))
    if interval <= 0:
        return

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                store.replace(transform(FileSource(directory).read()))
            except Exception:
                log.exception("file poll failed for %s", directory)

    threading.Thread(target=loop, daemon=True, name=f"watch:{directory}").start()


def run_live(store: Store) -> None:
    """`schema.apply_lineage_rules` is applied to every path here (initial
    listing, the rejected-bucket poll, and each Kafka-triggered fetch) --
    the client-side guard against eval-* contamination sitting in Kafka's
    append-only history, and the pre-teacher lineage grouping."""
    minio = MinioSource(
        endpoint=os.environ["S3_ENDPOINT"],
        access_key=os.environ["S3_ACCESS_KEY"],
        secret_key=os.environ["S3_SECRET_KEY"],
    )
    curated_bucket = os.environ.get("S3_CURATED_BUCKET", "episodes-curated")
    rejected_bucket = os.environ.get("S3_REJECTED_BUCKET", "episodes-rejected")

    store._seed_complete = False

    def seed_from_minio() -> None:
        log.info("seeding from %s + %s (background)", curated_bucket, rejected_bucket)
        t0 = time.time()
        try:

            def load_bucket(bucket: str) -> list[dict]:
                return list(minio.list_bucket(bucket))

            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="seed") as pool:
                curated_fut = pool.submit(load_bucket, curated_bucket)
                rejected_fut = pool.submit(load_bucket, rejected_bucket)
                store.merge(schema.apply_lineage_rules(curated_fut.result()))
                store.merge(schema.apply_lineage_rules(rejected_fut.result()))
            store.rejected_last_checked = time.time()
            log.info(
                "MinIO seed complete: %d episode(s) in %.1fs",
                len(store.episodes()),
                time.time() - t0,
            )
        except Exception:
            log.exception("MinIO seed failed")
        finally:
            store._seed_complete = True

    threading.Thread(target=seed_from_minio, daemon=True, name="minio-seed").start()

    def poll_rejected() -> None:
        # Rejected episodes never get a Kafka notification (only sync-agent
        # publishes, and only for curation passes), so this is the only way
        # the store ever sees them after startup. The mirror job that
        # populates episodes-rejected runs every 5 minutes upstream, so a
        # 30s poll here is frequent enough not to be the bottleneck -- the
        # UI surfaces `rejected_last_checked` so a stale success rate is
        # visible rather than silently trusted.
        while True:
            time.sleep(REJECTED_POLL_SECONDS)
            try:
                store.merge(schema.apply_lineage_rules(minio.list_bucket(rejected_bucket)))
                store.rejected_last_checked = time.time()
            except Exception:
                log.exception("episodes-rejected poll failed")

    threading.Thread(target=poll_rejected, daemon=True).start()

    def consume_kafka() -> None:
        kafka = KafkaSource(
            bootstrap_servers=os.environ["KAFKA_BOOTSTRAP"],
            topic=os.environ.get("KAFKA_TOPIC", "episode-manifests"),
        )
        for notification in kafka.read():
            merge_manifest(store, minio, notification, (curated_bucket, rejected_bucket))

    threading.Thread(target=consume_kafka, daemon=True).start()


def merge_manifest(store: Store, minio, notification: dict, buckets: Iterable[str]) -> None:
    """Fetch and merge the episode a Kafka manifest points at, if it sits in a configured bucket."""
    s3_uri = notification.get("s3_uri") if isinstance(notification, dict) else None
    if not s3_uri:
        return
    # The topic is shared and append-only: a manifest may name a bucket this
    # instance was never pointed at. Those are counted, not fetched.
    if not uri_in_buckets(s3_uri, buckets):
        store.count_kafka_skip()
        return
    try:
        record = minio.get_by_uri(s3_uri)
    except Exception:
        log.exception("failed to resolve %s from MinIO", s3_uri)
        return
    if record:
        store.merge(schema.apply_lineage_rules([record]))


class PairedResult:
    """The shown run's paired block, kept beside the store (which holds only that run's records)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._payload: dict | None = None
        self.shown: tuple | None = None

    def set(self, shown: tuple, payload: dict) -> None:
        with self._lock:
            self.shown = shown
            self._payload = payload

    def get(self) -> dict | None:
        with self._lock:
            return self._payload


def build_eval_reader():
    """A directory reader when EVAL_DIR is set, else the evaluation bucket over S3."""
    eval_dir = os.environ.get("EVAL_DIR")
    if eval_dir:
        return DirEvalReader(eval_dir)
    absent = [name for name in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY") if not os.environ.get(name)]
    if absent:
        raise ValueError(f"SOURCE_MODE=eval needs EVAL_DIR, or S3_ENDPOINT + S3_ACCESS_KEY + S3_SECRET_KEY (unset: {', '.join(absent)})")
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
    )
    return S3EvalReader(
        client,
        bucket=os.environ.get("EVAL_BUCKET", "episodes-data"),
        prefix=os.environ.get("EVAL_PREFIX", "eval/"),
    )


def refresh_eval(store: Store, source: EvalSource, paired: PairedResult) -> bool:
    """Show the pinned or newest run; replaces the store only when what is shown would change."""
    loaded = source.load()
    if loaded is None:
        return False
    # A run can be listed before all three objects have landed, so a change
    # in what is missing counts as a change, not only a new run id.
    shown = (loaded["run_id"], loaded["report"].get("timestamp"), tuple(loaded.get("missing", ())))
    if shown == paired.shown:
        return False
    store.replace(loaded["records"])
    store.origin = f"eval:{loaded['run_id']}"
    paired.set(shown, paired_block(loaded))
    if loaded.get("missing"):
        log.warning("run %s is incomplete, missing %s", loaded["run_id"], loaded["missing"])
    log.info("showing run %s: %d episode(s)", loaded["run_id"], len(loaded["records"]))
    return True


def run_eval(store: Store, paired: PairedResult) -> None:
    source = EvalSource(build_eval_reader(), os.environ.get("EVAL_RUN_ID") or None)
    interval = float(os.environ.get("EVAL_POLL_SECONDS", "30"))

    def check() -> None:
        try:
            refresh_eval(store, source, paired)
        except Exception:
            log.exception("evaluation run check failed")

    # A storage outage at start must not keep the page (and the readiness probe) from coming up.
    check()
    if paired.shown is None:
        log.info("no evaluation run found yet")
    if interval <= 0:
        return

    def loop() -> None:
        while True:
            time.sleep(interval)
            check()

    threading.Thread(target=loop, daemon=True, name="eval-poll").start()


def main() -> None:
    version_meta = load_version_meta(os.environ.get("VERSIONS_FILE", DEFAULT_VERSIONS_FILE))
    store = Store(version_meta)
    paired = PairedResult()

    source_mode = os.environ.get("SOURCE_MODE", "files")
    records_dir = os.environ.get("RECORDS_DIR", "/records")

    if source_mode == "live":
        store.origin = "live:kafka+minio"
        run_live(store)
    elif source_mode == "files":
        if pathlib.Path(records_dir).exists():
            store.origin = f"files:{pathlib.Path(records_dir).resolve()}"
            store.replace(schema.apply_lineage_rules(FileSource(records_dir).read()))
            watch_files(store, records_dir, transform=schema.apply_lineage_rules)
        else:
            log.info("no episode files at RECORDS_DIR=%s", records_dir)
    elif source_mode == "eval":
        run_eval(store, paired)
    else:
        raise ValueError(f"Unknown SOURCE_MODE={source_mode!r}, expected live|files|eval")

    app = create_app(store, source_mode, paired_provider=paired.get if source_mode == "eval" else None)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
