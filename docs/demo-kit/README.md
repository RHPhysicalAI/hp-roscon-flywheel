# Contingency kit — artifacts that back the demo when nothing is live

Plan of record (D023, operator 2026-09-08): the demo must play from recordings and durable artifacts
alone. Text artifacts are committed here; recordings and screenshots are Phase 4 item 2 and are kept
on the presenting laptop and a USB stick (too large / binary for git — list them here when captured).

| file | backs | what it is |
|---|---|---|
| `run6-task-states.txt` | Beat 3 | DSP run `07aaf328…` task list with timestamps (trigger → PR in 3 min), from the KFP API |
| `run6-host-runner.log` | Beat 3 | the host runner's lines for run 6: trigger received, checkpoint + N=100 records reused, gate PASS 0.73 → 0.86 |
| `run6-pipeline-run.log` | Beat 3 | the poller's per-30 s task states for run 6 |
| `pr1.md` | Beat 5 | PR #1 title, merge time, files changed, body (evidence table + signed digest) |
| `rekor-entry-1.json` | Beat 5 | Rekor log index 1 (RHTAS, `hashedrekord`) — the transparency-log record of the modelcar signature |
| `run6-swap-agent.log` | Beat 6 | the swap agent verifying the signature, exporting `models/act`, recreating the policy container as `act-v2-ft160` |

Elsewhere in the repo: `docs/phase3-ladder.html` + `docs/eval-records/phase3-ladder/` +
`src/eval-report/ladder_report.py` (Beat 4, reproduces every number), `git show 1eab082:gitops/act-serving/service.yaml`
(the merged flip; the path is retired, D025), `docs/data-contract-eval-dashboard.md` (what Beat 2's stream contains).

Still to capture (item 2): the full Short Cut screen recording; arm clip (Beat 1); dashboard
recording + MinIO screenshot (Beat 2); terminal screenshot (Beat 3); chart PNG (Beat 4); PR
*Files changed*, Rekor UI and Argo screenshots (Beat 5); badge flip + first v2 episode (Beat 6).
