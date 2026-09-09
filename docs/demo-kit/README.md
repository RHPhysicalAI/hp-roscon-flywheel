# Contingency kit — artifacts that back the demo when nothing is live

Plan of record (D023, operator 2026-09-08): the demo must play from recordings and durable artifacts
alone. Text artifacts are committed here; recordings and screenshots are Phase 4 item 2 and are kept
on the presenting laptop and a USB stick (too large / binary for git — list them here when captured).

**Beats 5 and 6 are recorded on the RHEM path** (Phase 4.5 sequencing note; D024): the script the
operator follows, clip by clip, is [`rhem-kit-script.md`](rhem-kit-script.md). The pre-RHEM Beat 5/6
artifacts below are kept as history of the retired path (D025/D075) and are **not** the demo's
screens any more.

| file | backs | what it is |
|---|---|---|
| `run-192f3ec5-task-states.txt` | Beat 3 | DSP run `192f3ec5…` task list with timestamps (trigger → PR in 3 m 45 s) — the run that opened PR #2 |
| `run-192f3ec5-host-runner.log` | Beat 3 | the host runner's lines for that run: trigger received, checkpoint + both N=100 records reused, gate PASS 0.73 → 0.86 |
| `pr2.md` | Beat 5 | PR #2 title, merge time, the two files changed (Fleet + consumer, D066), body (evidence table, signed digest, Fleet URL, rollback line); rollout timings as measured |
| `rekor-entry-4.json` | Beat 5 | Rekor log index 4 (RHTAS, `hashedrekord`) — the transparency-log record of PR #2's modelcar signature |
| `archive-pre-rhem/run6-task-states.txt`, `archive-pre-rhem/run6-host-runner.log`, `archive-pre-rhem/run6-pipeline-run.log` | Beat 3 (history) | run 6 (`07aaf328…`), the first governed run, pre-RHEM; the pipeline shape is the same minus `register-model` |
| `archive-pre-rhem/pr1.md`, `archive-pre-rhem/rekor-entry-1.json` | superseded (Beat 5, pre-RHEM) | PR #1 flipped the retired `gitops/act-serving/` Deployment pair; Rekor index 1 is its signature. History only |
| `archive-pre-rhem/run6-swap-agent.log` | superseded (Beat 6, pre-RHEM) | the retired host swap agent (D025/D075). History only |

Elsewhere in the repo: `docs/eval-records/promotion-2.md` (PR #2 rollout + PR #3 rollback, second by
second), `docs/eval-records/model-registry.md` and `docs/eval-records/catalog.md` (Beat 6's registry row
and version graph), `docs/eval-records/negative-trust-tests.md` (the exact failure strings, D091),
`docs/internal/phase3-ladder.html` + `docs/eval-records/phase3-ladder/` + `src/eval-report/ladder_report.py`
(Beat 4, reproduces every number), `docs/internal/data-contract-eval-dashboard.md` (what Beat 2's stream contains).

Still to capture (item 2): the full Short Cut screen recording; arm clip (Beat 1); dashboard
recording + MinIO screenshot (Beat 2); terminal screenshot (Beat 3); chart PNG (Beat 4); **the
RHEM clip set in `rhem-kit-script.md`** (Beats 5/6, rollback, negative trust) — the old Beat 5/6 list
(PR #1 *Files changed*, Argo `act-serving`, swap-agent badge flip) is superseded.
