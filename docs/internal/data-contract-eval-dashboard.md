# Data contract for the eval dashboard (Olga, APPENG-6295)

Everything the read-only eval dashboard needs, where it lives, and what the fields mean. All of it
is produced by the flywheel itself; nothing here is hand-made.

## Where

| what | where | access |
|---|---|---|
| Per-episode records, **curated** (passed the curator: 3/3 cubes) | MinIO `episodes-curated/<model_version>/<episode_id>.json` | S3 `http://10.0.0.49:30900`, read-only creds in Secret `minio-olga-readonly-credentials` (ns `minio`) |
| Per-episode records, **rejected** | MinIO `episodes-rejected/<model_version>/<episode_id>.json` (mirrored every 5 min by the `rejected-mirror` CronJob) | same |
| Live stream of curated records | Kafka `episode-manifests` (`10.0.0.49:30903`, PLAINTEXT) — `{episode_id, s3_uri, score, model_version, …}` | same host/port |
| Assembled training datasets | MinIO `episodes-data/<model_version>/<repo_id>.tar.gz` + Kafka `dataset-manifests` | same |
| Promotion evidence per pipeline run | MinIO `episodes-data/eval/<run_id>/eval_report.json` (+ the two raw harness records) | same |
| Frozen Phase-3 evidence (for the booth, no cluster needed) | repo `docs/eval-records/phase3-ladder/*.json`; table via `src/eval-report/ladder_report.py`; chart `docs/internal/phase3-ladder.html` | git |
| Promotions | GitHub PRs on `RHPhysicalAI/hp-roscon-flywheel` titled `Promote <candidate> (…)`; Rekor entries at `rekor-server-trusted-artifact-signer.apps.sno-flywheel.local` | git / cluster route |

## Lineage (`model_version`) — the grouping key

Every episode is stamped with the policy that produced it (published by the coordinator co-located
with the served policy, so a swap re-labels immediately). Values so far:

| model_version | meaning | when |
|---|---|---|
| `upstream-act-teacher` | v1 — the upstream ACT policy (`francocipollone/rospai_act_sim_arm101_place_cubes_on_tray`), collecting under one-random-cube | 2026-09-04 → 09-08 12:16 |
| `act-v2-ft160` | v2 — the teacher fine-tuned on 160 of its own curated successes, promoted via PR #1 | 2026-09-08 12:16 → |
| (next) `act-v2-ft160-ft<N>-<stamp>` | v3 candidates — fine-tuned from v2 on v2's successes | when the trigger fires at 160 new curated |

Older labels (`soarm-act-10ep-10k`, `soarm-act-v1`) are Phase-2 baselines; group them as
"pre-teacher". **Treat any `eval-` prefix as non-production and exclude it.** Until 2026-09-08 the eval
harness let the emitter POST its episodes to the curator, so 135 `eval-*` objects sat in both
buckets; they were deleted that day, the harness no longer signals the emitter, and the curator
now discards `eval-*` labels at the door — but a defensive filter in the dashboard costs nothing.

**Success rate by lineage = curated / (curated + rejected)** — the loop's *operational* rate. Note
it is lower than the policy's clean-start rate (the eval harness homes the arm each episode; the
loop does not, and before 2026-09-08 failures could chain — see D021). Use the eval records for
apples-to-apples policy comparison; use the MinIO stream for "what the fleet is doing right now".

## Schemas

**Episode record** (curator contract; `docs/internal/THOR-TESTING-REUSE.md`):
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
- `dataset_path` points at the raw MCAP bag on the desktop host (not in the hub).

**Eval harness record** (`docs/eval-records/*.json`, and `episodes-data/eval/<run_id>/eval-<mv>.json`):
```json
{"model_version": "...", "policy_path": "...", "timestamp": "...",
 "eval_config": {"episodes": 100, "seed_base": 1000, "randomize_only": "cube_medium", "random_radius": "0.03", "episode_len_s": 60.0, ...},
 "aggregate": {"n": 100, "successes": 86, "success_rate": 0.86, "mean_cubes": 2.73, "cubes_hist": {"0":1,"1":11,"2":2,"3":86}, "mean_smoothness": 0.0048},
 "episodes": [{"index": 0, "seed": 1000, "cubes_placed": 3, "task_success": true, "steps": 1968, "avg_smoothness": 0.003, "duration_s": 44.6, "early_stopped": true}, ...]}
```
Same `seed` ⇒ identical cube layout across policies — pair on it.

**Promotion report** (`episodes-data/eval/<run_id>/eval_report.json`):
```json
{"run_id": "...", "candidate": "act-v2-ft160", "incumbent": "upstream-act-teacher", "n_paired": 100,
 "candidate_success_rate": 0.86, "incumbent_success_rate": 0.73, "delta": 0.13,
 "fixed": 20, "broken": 7, "net": 13, "sign_test_p": 0.0192,
 "candidate_mean_cubes": 2.73, "incumbent_mean_cubes": 2.51,
 "rule": "promote iff net > 0 and p < 0.05 (D022)", "verdict": "PASS", "timestamp": "..."}
```

**Dataset manifest** (Kafka `dataset-manifests`): `{dataset_id, s3_uri, model_version, num_episodes,
num_frames, fps, robot_type, size_bytes, vcodec, episode_ids[], timestamp}`.

## The numbers the dashboard should be able to reproduce

From `docs/eval-records/phase3-ladder/` (run `python3 src/eval-report/ladder_report.py docs/eval-records/phase3-ladder --baseline eval-teacher-v1 --rungs eval-ft-20ep:20,eval-ft-40ep:40,eval-ft-80ep:80,eval-ft-160ep:160`):

| fine-tuned on | success | paired vs teacher (fixed/broken) |
|---|---|---|
| 0 (teacher) | 73% (N=100) | — |
| 20 | 60% | 7 / 14 |
| 40 | 56% | 6 / 15 |
| 80 | 76% | 8 / 7 |
| 160 (= v2) | 86% (N=100) | 20 / 7, p = 0.019 |

Off-box copies of the datasets and checkpoints: private Hugging Face repos under `jeremyary/`
(`soarm-flywheel-*`, `soarm-act-*`) — ask for access.
