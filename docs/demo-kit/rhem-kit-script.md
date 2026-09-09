<!-- This project was developed with assistance from AI tools. -->
# Contingency kit — recording script for Beats 5 and 6 on the RHEM path (Phase 4 item 2 / 4.5 G-prep)

The operator records; nothing here is captured by an agent. One session, ~30 minutes, produces
every Beat 5/6 clip the Short Cut needs plus the rollback and negative-trust clips. It *is* the
one Full Live rehearsal of the RHEM promotion cycle (BUILD-PLAN Phase 4.5 exit line), filmed.
Commands are the ones in `docs/DEMO_RUNBOOK.md` (all run 2026-09-09); timings are D069/D074.

Clips are named as listed. Keep the raw clip and a trimmed cut of each; copies on the presenting
laptop **and** a USB stick (binary, not git — list them in `README.md` when captured).

## Pre-conditions (T-30 min) — all required before pressing record

| # | condition | how to check |
|---|---|---|
| 1 | Fleet serves **`act-v2-ft160` @ `bdb513ca…`**, device `UpToDate` / `Healthy` (today's state after PR #3) | runbook § *Prerequisites* state check; `flightctl get device/<name> -o json \| jq .status.applicationsSummary` |
| 2 | **A promotion PR is open and unmerged** for candidate `act-v2-ft160-rhem` (D067): produced by the runbook's Full Live Part 3 trigger (~4 min trigger-to-PR with the registry step). Head `promote/act-v2-ft160-rhem-<run8>`; two files, five lines; body carries the evidence table, the signed digest, `Fleet:` URL and `Rollback:` line | `gh pr list -R RHPhysicalAI/hp-roscon-flywheel` shows exactly one OPEN PR; `gh pr view <n> --json files` lists `gitops/rhem/fleet-act-inference.yaml` + `gitops/flywheel/manifest-consumer.yaml` |
| 3 | The PR's Rekor index is known (PR body / `docs/eval-records`-style capture, or the registry row's `rekor_index`) | `curl -sk …/api/v1/log/entries?logIndex=<n>` returns the entry |
| 4 | **Loop running** via `~/run-coordinator.sh` (`IMAGE=<the Fleet's runtime digest> MODEL_VERSION=act-v2-ft160`), **disk guard armed** (`pgrep -f disk-guard.sh`), > 100 GB free under `~/flywheel-data/bags` | `docker ps` shows `act-coordinator` Up; `docker logs act-coordinator \| grep Observed` reads `act-v2-ft160` |
| 5 | Host runner resident (`pgrep -af host_runner`) — only needed to *produce* the PR in (2) | runbook *Failure recovery* row if not |
| 6 | Mac `/etc/hosts` resolves `ui.flightctl…`, `flywheel-rest…`, `rekor-search-ui-…`, `rekor-server-…`, `minio-console-…` to `10.0.0.49`; RHEM UI **logged in** (OpenShift OAuth); Fleet page open at `https://ui.flightctl.apps.sno-flywheel.local/devicemanagement/fleets/act-inference`; device page open on the **Applications** tab | both pages render without a login prompt |
| 7 | flightctl port-forward loop alive on the desktop (`~/flightctl-pf.log`); `flightctl get devices` answers (a `connection refused 127.0.0.1:3443` means the loop is re-establishing — retry in 5 s) | — |
| 8 | Two host terminals: **T1** with the device watch pre-filled (below), **T2** free; phone with the GitHub PR open (the merge is done on the phone) | — |
| 9 | Screen recorder tested (1080p, system audio off, cursor on); a clock visible in frame (menu-bar clock or `date` in T2) so the timings can be read back | 10 s test clip plays |

T1 pre-fill (from the merge to Healthy, ~2–3 min; D070: `updated` goes `UpToDate` before the app is `Healthy`):
```bash
watch -n 5 '~/.local/bin/flightctl get device/s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg -o json | jq -c ".status.config.renderedVersion,.status.updated.status,.status.applicationsSummary.status,.status.applications[0].volumes[0].reference"'
```

## Recording order

### Before the merge (static clips, ~5 min)

| clip | screen | must show | duration |
|---|---|---|---|
| **5a-pr** | GitHub PR, *Conversation* then *Files changed* | title `Promote act-v2-ft160-rhem (73% -> 86%)`; the evidence table (73% → 86%, 20/7, p = 0.0192); the signed digest; the `Fleet:` URL and the `Rollback:` line; then the two-file diff — Fleet: modelcar digest + `MODEL_VERSION`; consumer: `INCUMBENT`, `COLLECTOR`, `INCUMBENT_CHECKPOINT` | 30–40 s |
| **5b-rekor** | Rekor search UI `…/?logIndex=<n>` | kind `hashedrekord`, the integrated time, the log index. Fallback: the API URL's JSON in a tab | 15 s |
| **5b2-verify** (optional) | T2 | the runbook's `cosign verify … --rekor-url http://localhost:8090 <image@digest>` three `- ` lines (claims validated, tlog existence verified, key verified) | 15 s |
| **6d-registry** | T2 or a browser tab | Model Registry `GET …/model_versions` for `act-v2-ft160-rhem` with `candidate_success_rate`, `fixed/broken/net`, `sign_test_p`, `rekor_index`, `pr_url`, `dsp_run_id`; `GET …/model_artifacts` with the digest `uri`. **Say on film or in the caption:** one registry row per candidate, refreshed by the latest run — its digest is that run's, not necessarily the Fleet's pin (D090) | 20 s |
| **6e-catalog** | RHEM UI `/catalog` → `physical-ai-models` → `soarm-act`; then T2 `flightctl get catalogitem soarm-act --catalog physical-ai-models -o yaml` | the version graph `2.0.0-ft160` → `2.0.0-ft160-rhem` (`replaces`), the `container: sha256:…` references. Caption: *v1alpha1; the Fleet pins the digest, the Catalog shows the version graph* | 20 s |
| **N1-negtrust** | T2 | `flightctl console device/<name> --notty -- sudo -n podman pull quay.io/jary/soarm-flywheel:negtest-notlog-2026-09-09` → `Source image rejected: missing dev.sigstore.cosign/bundle annotation`; then `… podman pull quay.io/jary/soarm-flywheel@sha256:06413b09f79dd1186d7b199ffa56b01d56c12f50ed2a4671fb299cac297a66e3` → `Source image rejected: A signature was required, but no signature exists` (D091; exit 125 both). Nothing lands on the device | 30 s |

### The promotion (one continuous recording, ~4 min raw → trim to ~60 s)

Layout: RHEM **Fleet page** left, **T1 watch** right, phone in hand.

| clip | what happens | must show | duration |
|---|---|---|---|
| **5c-merge** | press **Merge** on the phone; say the time | the merge confirmation with its timestamp (phone screen recording, or the laptop's PR page refreshed) | 15 s |
| **5d-fleet** | wait; ~1–2 min after the merge (one ResourceSync poll) the Fleet page changes: template version +1, rollout in progress, batch 1, then *rollout completed* | the rollout starting, the batch, the **banner going green ~30 s before** the device's app is Healthy (D070) — keep the device *Applications* tab or T1 in the same frame so that gap is on film | 2–3 min raw |
| **5e-watch** | T1, same take | `renderedVersion` +1 → `updated: UpToDate` → `applicationsSummary: Healthy`; the volume reference becomes the new digest | same take |

Expected from the merge (D069): `UpToDate` ≈ +1:55, `Published model_version` ≈ +2:10, `Healthy` ≈ +2:40; worst case +3 with the poll phase.

### After the merge (~8 min)

| clip | screen | must show | duration |
|---|---|---|---|
| **6a-device** | RHEM UI device page, *Applications* tab (refresh) | `act-inference` `Running 1/1`, status Healthy, the volume reference `…soarm-act-modelcar@sha256:<new digest>` | 20 s |
| **6b-console** | T2 | `flightctl console device/<name> --notty -- sudo -n podman logs --tail 20 act-inference-128875-act-inference \| grep 'Published model_version'` → `Published model_version: act-v2-ft160-rhem` | 15 s |
| — | T2 | restart the loop under the new label so the coordinator stops warning: `docker stop act-coordinator; IMAGE=<runtime digest> MODEL_VERSION=act-v2-ft160-rhem ~/run-coordinator.sh` (D073) | — |
| **6c-dashboard** | `http://10.0.0.49:30801` | the badge flips to **`act-v2-ft160-rhem`** and the bar resets to **0 / 160** when Argo syncs the consumer (~1–3 min after the merge, D072); then the first `-rhem` rows land (first reject ≈ +1 min of loop, first pass ≈ +2.5 min, D073) and the bar reads 1 / 160 | 45 s (cut from ~5 min) |
| **6f-minio** (optional) | MinIO console → `episodes-curated` | the new prefix `act-v2-ft160-rhem/` with its first object | 15 s |

### The rollback (~4 min raw → trim to ~30 s)

From the **desktop** (its `gh` login can open PRs; the Mac's PAT cannot, D071):
```bash
cd ~/redhat/git/hp-roscon-flywheel && git checkout desktop-gpu-split && git pull --ff-only
M=$(gh pr view <n> --repo RHPhysicalAI/hp-roscon-flywheel --json mergeCommit --jq .mergeCommit.oid)
git checkout -b rollback/act-v2-ft160-rhem-$(date +%H%M) && git revert -m 1 --no-edit "$M"
git diff --stat HEAD~1      # exactly the two PR files, 5 lines back
git push -u origin HEAD && gh pr create --repo RHPhysicalAI/hp-roscon-flywheel --base desktop-gpu-split --fill
```

| clip | screen | must show | duration |
|---|---|---|---|
| **R1-revert-pr** | the revert PR | title `Revert "Promote act-v2-ft160-rhem …"`, the same two files, 5 lines back | 15 s |
| **R2-rollback** | Fleet page + T1, same layout as 5d/5e; merge on the phone | template version +1 again; `Published model_version: act-v2-ft160` back in the console log; `Healthy`; **no pull**: `flightctl console … -- sudo -n podman events --since 10m --filter event=pull` shows no modelcar pull, `… podman images --digests \| grep modelcar` lists both digests (Retain, D071/D074) | 2–3 min raw |

Expected (D074): serving at ≈ +1:30, Healthy ≈ +2:00, worst case ~3 with the poll.

### Wrap-up (T+0)

1. `docker stop act-coordinator` (the loop never runs unattended); guard stays armed.
2. State check from the runbook: Fleet back at `act-v2-ft160` @ `bdb513ca…`, device Healthy, dashboard badge `act-v2-ft160`, no open PR.
3. Restart the loop only if a full Short Cut recording follows.
4. Copy the clips to the laptop and the USB stick; fill in the table in `README.md` (file names, durations).

## What each clip backs in the Short Cut

| beat | live screen | clip | text artifact already in git |
|---|---|---|---|
| 5 | PR #2 page | 5a-pr | `pr2.md` |
| 5 | Rekor UI, log index 4 | 5b-rekor, 5b2-verify | `rekor-entry-4.json` |
| 5 | RHEM Fleet page (rollout) | 5c-merge, 5d-fleet, 5e-watch | `docs/eval-records/promotion-2.md` (the D2 timeline) |
| 6 | device Applications tab + console log | 6a-device, 6b-console | same |
| 6 | dashboard badge / bar / first re-stamped rows | 6c-dashboard, 6f-minio | same (§ *Lineage downstream*) |
| 6 | Model Registry, Catalog | 6d-registry, 6e-catalog | `docs/eval-records/model-registry.md`, `docs/eval-records/catalog.md` |
| Q&A | rollback, negative trust | R1, R2, N1 | `docs/eval-records/promotion-2.md` (D3), `docs/eval-records/negative-trust-tests.md` |
