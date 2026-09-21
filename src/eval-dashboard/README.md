# Eval dashboard

A small read-only Flask dashboard that compares manipulation policies by `model_version`: success rate with a
Wilson interval, cube-placement distribution, smoothness of successful episodes, a learning curve, and the raw
episode rows behind every number. It holds one in-memory population, rebuilt from its source on every start, and
writes nothing anywhere. One static page, no external assets.

> [!NOTE]
> This project was developed with assistance from AI tools.

## Modes

`SOURCE_MODE` picks where records come from. Every mode feeds the same normalisation and aggregation code, so the
same underlying data gives the same numbers whichever way it arrives.

| Mode | What it shows |
|---|---|
| `files` (default) | A directory of per-episode JSON records, re-read on an interval. No Kafka or object storage needed. |
| `live` | Curated episodes announced on Kafka and fetched from object storage, merged with the rejected bucket (rejects are never announced, so that bucket is polled). Manifests naming any other bucket are counted and not fetched. |
| `eval` | One promotion run's paired evaluation: the pipeline's `eval_report.json` shown as written, plus both policies' harness records as the episode evidence. Shows the newest run, or a pinned one. No Kafka. |

In `eval` mode a run is three objects under `<run_id>/`: `eval_report.json`, `eval-<candidate>.json` and
`eval-<incumbent>.json`. The **Paired result** panel is the report verbatim (fixed, broken, net, sign-test p,
verdict, rule); only the fixed and broken seed lists are derived, by pairing the two records on `seed`.

Two rules apply in every mode:

- A failed episode whose `cubes_placed` is null (the simulator's ground truth could not be read) or whose rollout
  did not finish is not a verdict on the policy. It is left out of the rate and shown on the card as not scored.
- A version that the versions file does not list, but whose name contains `-ft<N>`, is labelled fine-tuned with
  `N` as its training-set size. An entry in the file always wins.

## Configuration

### All modes

| Variable | Default | Meaning |
|---|---|---|
| `SOURCE_MODE` | `files` | `files`, `live` or `eval`. |
| `PORT` | `8080` | Listen port. |
| `VERSIONS_FILE` | `/app/config/versions.yaml` | `model_version` to training-set size map. When absent, `versions.example.yaml` beside it is used. |
| `SOURCE_LABEL` | by mode | Text of the source chip. Defaults: `Saved files`, `Live`, `Paired evaluation`. |
| `OTHER_VIEW_URL`, `OTHER_VIEW_LABEL` | unset | A header link to another instance. Rendered only when both are set. |
| `LIVE_DASHBOARD_URL` | unset | Target of the "Back to Live Flywheel" header link. Hidden when unset. |
| `PAGE_TITLE` | unset | Replaces the page's heading and title, for an instance that has to say what it is. |
| `PAGE_NOTE` | unset | One line shown above everything else. Set as text, never as markup. |
| `PAGE_NOTE_LINK_URL`, `PAGE_NOTE_LINK_LABEL` | unset | One link at the end of the note. Rendered only when both are set and the address is http(s). |

### `files`

| Variable | Default | Meaning |
|---|---|---|
| `RECORDS_DIR` | `/records` | Directory of episode JSON (flat, `<model_version>/<episode_id>.json`, or a JSON array per file). |
| `FILE_POLL_SECONDS` | `5` | Re-read interval. `0` disables polling. |
| `FILES_NEWEST` | `0` | Read only this many of the most recently modified files. `0` reads them all. |
| `FILES_MAX_BYTES` | `0` | Never open a file larger than this. `0` is no limit. |

The two bounds are for a directory something else keeps writing to: files are chosen by `stat` alone, so a full
volume costs a directory walk. A writer that keeps the newest N records of each verdict leaves N passes and N
rejects however the policy does; `FILES_NEWEST` at or below N reads exactly the last episodes judged instead.

### `live`

| Variable | Default | Meaning |
|---|---|---|
| `S3_ENDPOINT` | required | Object storage endpoint, e.g. `http://minio.example:9000`. |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | required | Read-only credentials. |
| `S3_CURATED_BUCKET` | `episodes-curated` | Bucket of curated episodes. |
| `S3_REJECTED_BUCKET` | `episodes-rejected` | Bucket of rejected episodes, polled every 30 s. |
| `KAFKA_BOOTSTRAP` | required | Kafka bootstrap servers, e.g. `kafka.example:9092`. |
| `KAFKA_TOPIC` | `episode-manifests` | Topic of episode manifests. |
| `MINIO_FETCH_WORKERS` | `32` | Concurrent object fetches while seeding. |

### `eval`

Set `EVAL_DIR`, or the three `S3_*` variables. `EVAL_DIR` wins when both are present.

| Variable | Default | Meaning |
|---|---|---|
| `EVAL_DIR` | unset | Directory of runs (`<EVAL_DIR>/<run_id>/eval_report.json`), or a single run directory. |
| `S3_ENDPOINT` | required without `EVAL_DIR` | Object storage endpoint, e.g. `http://minio.example:9000`. |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | required without `EVAL_DIR` | Read-only credentials. |
| `EVAL_BUCKET` | `episodes-data` | Bucket holding the runs. |
| `EVAL_PREFIX` | `eval/` | Key prefix above `<run_id>/`. |
| `EVAL_RUN_ID` | unset | Pin one run. Unset shows the newest run. |
| `EVAL_POLL_SECONDS` | `30` | How often to look for a newer run, or for the pinned run to appear. `0` disables polling. |

Newest means the report's `timestamp` for a directory (file modification time when it has none) and the report
object's `LastModified` for a bucket.

## API

All endpoints are `GET` and read-only.

| Path | Returns |
|---|---|
| `/api/stats` | `source_mode`, `source_label`, the header link and page note settings, and `snapshot` (per-version aggregates plus integrity counters). Suitable as a readiness probe. |
| `/api/episodes` | Normalised episode rows. Repeat `model_version` to select several; `limit` and `offset` page. |
| `/api/paired` | `{"available": false}` outside `eval` mode or before a run is found, else the report's fields plus `fixed_seeds` and `broken_seeds`. |

## Running the tests

From the repository root, in an environment with `requirements-dev.txt` installed:

```sh
python -m pip install -r src/eval-dashboard/requirements-dev.txt
python -m pytest tests/eval_dashboard -q
```

`tests/eval_dashboard/conftest.py` puts this directory on the import path, so no install of the package is needed.

## Running locally against evaluation records

Lay a run out as a directory, then point `eval` mode at it:

```text
runs/
  2026-09-20-a/
    eval_report.json
    eval-act-v2-ft160.json
    eval-upstream-act-teacher.json
```

```sh
cd src/eval-dashboard
SOURCE_MODE=eval EVAL_DIR=/path/to/runs \
  VERSIONS_FILE=config/versions.example.yaml PORT=8080 \
  python -m eval_dashboard.main
```

Then open `http://127.0.0.1:8080/`. `EVAL_DIR` may also be the run directory itself
(`/path/to/runs/2026-09-20-a`). The two harness record names come from the report's `candidate` and `incumbent`
fields; a run whose report is present but a record is not still loads, with what it has.

## Container

The image builds from this directory as its context:

```sh
podman build -t eval-dashboard src/eval-dashboard
```

It runs as the base image's non-root user, listens on 8080 and writes nothing at run time. Mount a real versions
file and set `VERSIONS_FILE` to it; without one the bundled example is used.
