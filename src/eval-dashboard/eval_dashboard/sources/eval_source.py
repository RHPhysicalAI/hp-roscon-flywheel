# This project was developed with assistance from AI tools.
"""Reads one promotion run: the pipeline's paired report and both harness records.

A run is three JSON objects under `<run_id>/`: `eval_report.json` plus one
`eval-<policy>.json` per policy. The report is the pipeline's result and is
shown verbatim; the harness records supply the per-episode evidence and the
seed lists behind the report's fixed/broken counts.

Read-only, like every other source: a directory is only read, and the S3
reader calls nothing but ListObjectsV2 and GetObject.
"""
from __future__ import annotations

import json
import logging
import pathlib
from datetime import datetime, timezone

from eval_dashboard import schema

log = logging.getLogger("eval_dashboard.eval")

REPORT_NAME = "eval_report.json"

# Report fields passed through untouched: the pipeline's result is
# authoritative and is never recomputed for display.
PAIRED_REPORT_FIELDS = (
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


def record_name(policy: str) -> str:
    """Object name of a policy's harness record within a run."""
    return f"{schema.EVAL_PREFIX}{schema.clean_model_version(policy)}.json"


def _success_by_seed(records) -> dict:
    return {r["seed"]: bool(r["task_success"]) for r in records if r.get("seed") is not None and "task_success" in r}


def paired_outcomes(candidate_records, incumbent_records) -> dict:
    """Pair two policies' episodes by seed; seeds present on one side only are ignored."""
    candidate = _success_by_seed(candidate_records)
    incumbent = _success_by_seed(incumbent_records)
    shared = sorted(candidate.keys() & incumbent.keys())
    return {
        "n_paired": len(shared),
        "fixed": [s for s in shared if candidate[s] and not incumbent[s]],
        "broken": [s for s in shared if incumbent[s] and not candidate[s]],
        "both_pass": sum(1 for s in shared if candidate[s] and incumbent[s]),
        "both_fail": sum(1 for s in shared if not candidate[s] and not incumbent[s]),
    }


def paired_payload(report: dict, outcomes: dict) -> dict:
    """The /api/paired body (without `available`): report scalars verbatim plus the seed lists."""
    payload = {name: report.get(name) for name in PAIRED_REPORT_FIELDS}
    payload["fixed_seeds"] = list(outcomes.get("fixed") or [])
    payload["broken_seeds"] = list(outcomes.get("broken") or [])
    return payload


def _as_object(body) -> dict | None:
    try:
        parsed = json.loads(body)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _epoch(timestamp) -> float | None:
    if not isinstance(timestamp, str) or not timestamp:
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class DirEvalReader:
    """Runs as sub-directories of `root`, or `root` itself when it holds a report."""

    def __init__(self, root: str):
        self.root = pathlib.Path(root)

    def _is_single_run(self) -> bool:
        return (self.root / REPORT_NAME).is_file()

    def _single_run_id(self) -> str:
        return self.root.resolve().name

    def _run_dir(self, run_id: str) -> pathlib.Path:
        if self._is_single_run() and run_id == self._single_run_id():
            return self.root
        return self.root / run_id

    def list_runs(self) -> list[str]:
        if self._is_single_run():
            return [self._single_run_id()]
        if not self.root.is_dir():
            return []
        dated = []
        for report_path in self.root.glob(f"*/{REPORT_NAME}"):
            if not report_path.is_file():
                continue
            run_id = report_path.parent.name
            report = self.read_json(run_id, REPORT_NAME) or {}
            when = _epoch(report.get("timestamp"))
            dated.append((when if when is not None else report_path.stat().st_mtime, run_id))
        return [run_id for _, run_id in sorted(dated, reverse=True)]

    def read_json(self, run_id: str, name: str) -> dict | None:
        path = self._run_dir(run_id) / name
        try:
            # run_id and name come from configuration and from a report's
            # labels; neither may reach outside the configured directory.
            if not path.resolve().is_relative_to(self.root.resolve()):
                return None
            return _as_object(path.read_text())
        except OSError:
            return None


class S3EvalReader:
    """Runs as `<prefix><run_id>/` in one bucket, read through an injected boto3 S3 client."""

    def __init__(self, client, bucket: str = "episodes-data", prefix: str = "eval/"):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix if not prefix or prefix.endswith("/") else prefix + "/"

    def list_runs(self) -> list[str]:
        dated = []
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": self.prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kwargs)
            for obj in page.get("Contents") or []:
                run_id, _, name = obj["Key"][len(self.prefix):].partition("/")
                if run_id and name == REPORT_NAME:
                    dated.append((obj.get("LastModified"), run_id))
            token = page.get("NextContinuationToken")
            if not page.get("IsTruncated") or not token:
                break
        # Undated reports sort last; ties fall back to the run id so the order is stable.
        dated.sort(key=lambda pair: (pair[0] is not None, pair[0] if pair[0] is not None else 0, pair[1]), reverse=True)
        return [run_id for _, run_id in dated]

    def read_json(self, run_id: str, name: str) -> dict | None:
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=f"{self.prefix}{run_id}/{name}")["Body"].read()
        except Exception:
            return None
        return _as_object(body)


class EvalSource:
    """Loads the pinned run, or the newest one, from a reader."""

    def __init__(self, reader, run_id: str | None = None):
        self.reader = reader
        self.run_id = run_id or None

    def load(self) -> dict | None:
        run_id = self.run_id
        if run_id is None:
            runs = self.reader.list_runs()
            if not runs:
                return None
            run_id = runs[0]
        report = self.reader.read_json(run_id, REPORT_NAME)
        if not isinstance(report, dict):
            return None

        records: list[dict] = []
        missing: list[str] = []
        # Candidate first: callers and the page rely on that order.
        for role in ("candidate", "incumbent"):
            policy = report.get(role)
            name = record_name(policy) if isinstance(policy, str) and policy else None
            harness = self.reader.read_json(run_id, name) if name else None
            if not isinstance(harness, dict):
                # A report that names no policy has no object to look for; say which role.
                missing.append(name or role)
                continue
            for raw in schema.explode_harness_record(harness):
                normalized = schema.normalize(raw)
                if normalized:
                    records.append(normalized)

        loaded = {
            "run_id": run_id,
            "report": report,
            "records": records,
            "policies": {
                "candidate": schema.clean_model_version(report.get("candidate")),
                "incumbent": schema.clean_model_version(report.get("incumbent")),
            },
        }
        if missing:
            loaded["missing"] = missing
        return loaded


def paired_block(loaded: dict) -> dict:
    """The paired payload for one `EvalSource.load()` result."""
    policies = loaded["policies"]

    def of(policy):
        return [r for r in loaded["records"] if r["model_version"] == policy]

    outcomes = paired_outcomes(of(policies["candidate"]), of(policies["incumbent"]))
    return paired_payload(loaded["report"], outcomes)
