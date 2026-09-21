<!-- This project was developed with assistance from AI tools. -->
# Data contract for the eval dashboard

Everything the read-only eval dashboard needs, where it lives, and what the fields mean. All of it
is produced by the flywheel itself; nothing here is hand-made.

## Where

| what | where | access |
|---|---|---|
| Per-episode records, **curated** (passed the curator: 3/3 cubes) | object storage, `episodes-curated/<model_version>/<episode_id>.json` | S3 `http://minio.minio.svc:9000` in the cluster (NodePort `10.20.0.10:30900`), read-only creds in Secret `minio-eval-readonly-credentials` (ns `minio`; the same Secret in ns `flywheel` for the in-cluster instances) |
| Per-episode records, **rejected** | object storage, `episodes-rejected/<model_version>/<episode_id>.json` (mirrored every 5 min by the `rejected-mirror` CronJob) | same |
| Live stream of curated records | Kafka `episode-manifests` (`edge-kafka.flywheel.svc:9092` in the cluster, NodePort `10.20.0.10:30903`, PLAINTEXT) — `{episode_id, s3_uri, score, model_version, …}` | same host/port |
| Assembled training datasets | object storage, `episodes-data/<model_version>/<repo_id>.tar.gz` + Kafka `dataset-manifests` | same |
| Promotion evidence per pipeline run | object storage, `episodes-data/eval/<run_id>/eval_report.json` (+ the two raw harness records) | same |
| Promotions | GitHub PRs on `RHPhysicalAI/hp-roscon-flywheel` titled `Promote <candidate> (…)`; Rekor entries at `rekor-server-trusted-artifact-signer.apps.sno-flywheel.local` | git / cluster route |

## Lineage (`model_version`) — the grouping key

Every episode is stamped with the policy that produced it (published by the coordinator co-located
with the served policy, so a swap re-labels immediately). Values so far:

| model_version | meaning |
|---|---|
| `upstream-act-teacher` | v1 — the upstream ACT policy, collecting under one-random-cube |
| `act-v2-ft160` | v2 — the teacher fine-tuned on 160 of its own curated successes; the promoted model |
| (next) `act-v2-ft160-ft<N>-<stamp>` | v3 candidates — fine-tuned from v2 on v2's successes, when the trigger fires at 160 new curated |

**Treat any `eval-` prefix as non-production and exclude it.** The eval harness does not signal the
emitter, and the curator discards `eval-*` labels at the door — but a defensive filter in the
dashboard costs nothing.

**Success rate by lineage = curated / (curated + rejected)** — the loop's *operational* rate. Note
it is lower than the policy's clean-start rate (the eval harness homes the arm each episode; the
loop does not). Use the eval records for
apples-to-apples policy comparison; use the object storage stream for "what the fleet is doing right now".

## Schemas

**Episode record** (the curator's contract):
```json
{"episode_id": "uuid4", "timestamp": "ISO-8601 UTC", "scene": "place_cubes_on_tray",
 "model_version": "act-v2-ft160", "has_failure": false,
 "rollout": {"status": "ok|truncated", "steps": 1785, "duration_s": 37.5},
 "task_success": true, "cubes_placed": 3, "avg_smoothness": 0.0044,
 "dataset_path": "bags/<sec>_<nsec>",
 "curation_score": 0.8, "curation_verdict": "pass|reject", "curation_reason": "..."}
```
- `cubes_placed` is the **peak** count during the episode (0–3); `task_success` ⇔ 3.
- `avg_smoothness` = mean absolute per-joint delta between consecutive `/joint_states` samples (lower
  = smoother). Typical: 0.004–0.006 for successful episodes.
- `dataset_path` points at the raw MCAP bag on the host that ran the sim (not in the hub).

**Eval harness record** (`episodes-data/eval/<run_id>/eval-<mv>.json`):
```json
{"model_version": "...", "policy_path": "...", "timestamp": "...",
 "eval_config": {"episodes": 360, "seed_base": 1000, "randomize_only": "cube_medium", "random_radius": "0.03", "episode_len_s": 60.0, ...},
 "aggregate": {"n": 360, "successes": 333, "success_rate": 0.925, "mean_cubes": ..., "cubes_hist": {"0": ..., "1": ..., "2": ..., "3": 333}, "mean_smoothness": ...},
 "episodes": [{"index": 0, "seed": 1000, "cubes_placed": 3, "task_success": true, "steps": 1968, "avg_smoothness": 0.003, "duration_s": 44.6, "early_stopped": true}, ...]}
```
Same `seed` ⇒ identical cube layout across policies — pair on it.

**Promotion report** (`episodes-data/eval/<run_id>/eval_report.json`):
```json
{"run_id": "...", "candidate": "act-v2-ft160", "incumbent": "upstream-act-teacher", "n_paired": 360,
 "candidate_success_rate": 0.925, "incumbent_success_rate": 0.8194, "delta": 0.1056,
 "fixed": 57, "broken": 19, "net": 38, "sign_test_p": 0.0,
 "candidate_mean_cubes": ..., "incumbent_mean_cubes": ...,
 "rule": "promote iff net > 0 and p < 0.05 (D022)", "verdict": "PASS", "timestamp": "..."}
```

**Dataset manifest** (Kafka `dataset-manifests`): `{dataset_id, s3_uri, model_version, num_episodes,
num_frames, fps, robot_type, size_bytes, vcodec, episode_ids[], timestamp}`.

## The numbers the paired page has to reproduce

The governed run's paired evaluation, 360 identical seeded scenes (seeds 1000-1359), as its
`eval_report.json` states them - the page shows the report verbatim and never recomputes it:

| | successes | rate |
|---|---|---|
| incumbent `upstream-act-teacher` | 295 of 360 | 81.9% |
| candidate `act-v2-ft160` | 333 of 360 | 92.5% |

Fixed 57, broken 19, net +38, sign test p < 0.0001 (written as `0.0`: the report rounds to four
places), verdict PASS.

Off-box copies of the datasets and checkpoints: private Hugging Face repos under the project lead's account
(`soarm-flywheel-*`, `soarm-act-*`) — ask for access.
