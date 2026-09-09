<!-- This project was developed with assistance from AI tools. -->
# Promotion 2 — first RHEM Fleet promotion: `act-v2-ft160` → `act-v2-ft160-rhem` (Phase 4.5 D1, D025)

**Date:** 2026-09-09 (run 06:53–06:57 CDT; merged 12:04:27Z; D2 rollout + rollback rehearsal 12:04–12:21Z, section at the end). **State at hand-off: PR #2 merged and rolled out; rollback PR #3 open, NOT merged.** The
operator merges (Gate 3); D2 records what happens after.

## What was run

| | |
|---|---|
| PR | https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/2 — `Promote act-v2-ft160-rhem (73% -> 86%)`, head `promote/act-v2-ft160-rhem`, base `desktop-gpu-split`, one commit `fd14b8f7` |
| Files in the PR | `gitops/rhem/fleet-act-inference.yaml` (2 lines: modelcar digest, `MODEL_VERSION`) and `gitops/flywheel/manifest-consumer.yaml` (3 lines: `INCUMBENT`, `COLLECTOR`, `INCUMBENT_CHECKPOINT`) — nothing else |
| DSP run | `192f3ec5-c0f2-459d-8e84-6e8e32e90b35`, pipeline `99ec0aab-51fb-412e-bd2f-47bc6a0d3e3d`, version `v-202609090650-rhem` (`940ef682-9565-4b72-b6a0-10bf56bb2cba`) — `trigger-and-wait`, `eval-gate`, `package-modelcar`, `sign-modelcar`, `open-promotion-pr` all SUCCEEDED; run SUCCEEDED 06:57 |
| Run parameters | `candidate=act-v2-ft160-rhem incumbent=upstream-act-teacher collector=upstream-act-teacher incumbent_checkpoint=hf` (D023's parameters; the candidate is v2's weights re-released under a name distinct from the version the Fleet already serves — see the D1 decision note) |
| Gate | PASS on the reused N=100 records: `upstream-act-teacher` 0.73 → `act-v2-ft160-rhem` 0.86, fixed 20 / broken 7, net +13, p = 0.0192 |
| Modelcar | `quay.io/jary/soarm-act-modelcar@sha256:1375d0bcc2c7c81867365b55a08bdd5fa03bf31d20cc7bde04044fdcf1a0784e` (crane append, `linux/amd64`, base `ubi9/ubi-micro:9.8-1787778798`) |
| Rekor | log index **4**, `hashedrekord` 0.0.1, integratedTime 1788954952 (= 2026-09-09T11:55:52Z), tree size after = 5, logID `37f4fa09cc7f385b…` |
| cosign verify (desktop, `~/bin/cosign` v2.6.5) | `SIGSTORE_REKOR_PUBLIC_KEY=~/rekor-live.pub cosign verify --key ~/cosign/cosign.pub --rekor-url http://localhost:8090 <image@digest>` (port-forward to `svc/rekor-server` in `trusted-artifact-signer`; no `--insecure-ignore-tlog`): claims validated, transparency-log existence verified offline (bundle logIndex 4), signature verified against the key |
| Device pull under `policy.json` | on the VM (10.0.0.51, rootful podman 5.8.2, `sigstoreSigned` + `rekorPublicKeyPath` for `quay.io/jary/soarm-act-modelcar`): `sudo podman pull quay.io/jary/soarm-act-modelcar@sha256:1375d0bc…` → "Storing signatures", image id `46c6257a557a…`, 230 MB. Both digests (`bdb513ca…` live, `1375d0bc…` promoted) now sit in the device's storage, so the RHEM rollout's pre-pull is a no-op and the rollback never re-pulls |
| Host loop | `act-coordinator` stayed stopped the whole time (the runner's `loop_park()` only looks for a docker container named `act-inference`, which has been stopped since the C2 cut-over). The host runner was restarted for this run (`nohup ~/venv-runner/bin/python ~/host_runner.py …`, pid 1201467) and is left resident |

## Device before the merge

| field | value |
|---|---|
| ResourceSync `rhem-fleets` `status.observedCommit` | `4defa219ac020035ca0e189ca839d2bc0bcddc4c` (tip of `desktop-gpu-split` before the merge) |
| Fleet `act-inference` annotations | `fleet-controller/templateVersion: v4`, `batchNumber: "3"`, `rolloutApproved: "true"` (`rolloutApprovalMethod: automatic`), last batch report `batch 1 … successful 1 failed 0` |
| Fleet conditions | `Valid: True`; `RolloutInProgress`-style condition `Inactive / False` |
| Device `s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg` | `device-controller/renderedVersion: "4"`, `status.updated: UpToDate ("Updated to desired renderedVersion: 4")`, `applicationsSummary: Healthy`, `Online` |
| Container on the VM | `act-inference-128875-act-inference` Up (healthy), env `ROLE=policy MODEL_VERSION=act-v2-ft160`, volume `systemd-act-inference-128875-models` (driver image) |
| Published line | `[model_version_publisher]: Published model_version: act-v2-ft160` |

## What to watch after the merge (operator, from the phone / a laptop)

All of these are read-only. `flightctl` on the desktop is `~/.local/bin/flightctl` (through the
port-forward loop on 3443, `~/flightctl-pf.log`); the UI is
`https://ui.flightctl.apps.sno-flywheel.local` (resolve to 10.0.0.49) → Fleets → `act-inference`
(`/devicemanagement/fleets/act-inference`) and Devices → the VM → Applications tab.

1. **ResourceSync picks up the merge (~1 min).** `flightctl get resourcesync rhem-fleets -o yaml` →
   `status.observedCommit` becomes the **merge commit of PR #2** (not `4defa219…`), and the
   `ResourceParsed`/`Synced` conditions stay `True / success`.
2. **Fleet re-templated.** `flightctl get fleet act-inference -o yaml` →
   `fleet-controller/templateVersion` **v4 → v5**, `spec.template.spec.applications[0].envVars.MODEL_VERSION: act-v2-ft160-rhem`,
   the `models.volume` inline content carries `@sha256:1375d0bc…`; the rollout condition flips from
   `Inactive` to active for the `site=desktop` batch (`successThreshold: 100%`, `defaultUpdateTimeout: 30m`),
   then `lastBatchCompletionReport` shows `successful: 1, failed: 0` and `batchNumber` advances.
   The `site=fury` batch has no device yet and completes trivially.
3. **Device updates.** `flightctl get device s28p3s5… -o yaml` → `renderedVersion` **4 → 5** (annotation and
   `status.config.renderedVersion`), `status.updated.status` goes `OutOfDate` → `Updating` → `UpToDate`
   with `message: 'Updated to desired renderedVersion: 5'`; `applicationsSummary.status` may show
   `Degraded`/`Unknown` for up to the health start period (`HealthStartPeriod=240s`, policy load on CPU)
   then returns to **Healthy**. The device does not pull anything new (the digest is already in local
   storage — table above); it re-creates `models.volume` and restarts the container.
4. **The container publishes the new lineage.** On the VM (`ssh jary@10.0.0.51`, or `flightctl console`):
   `sudo podman ps` shows a **new** container name (`act-inference-<newid>-act-inference`) and
   `sudo podman logs <name> | grep 'Published model_version'` →
   `Published model_version: act-v2-ft160-rhem`; `sudo podman inspect <name> --format '{{.Config.Env}}'`
   carries `MODEL_VERSION=act-v2-ft160-rhem`; `/healthcheck.sh` passes once `/flywheel/model_version`
   equals it (`(healthy)` in `podman ps`).
5. **Lineage downstream (needs the loop, D2).** Start the host loop with `~/run-coordinator.sh`
   (disk guard is armed); its log shows `Observed model_version: act-v2-ft160-rhem`; the curator writes
   `episodes-curated/act-v2-ft160-rhem/…`; the `manifest-consumer` pod restarts on the ConfigMap/Deployment
   change (Argo `flywheel` app) and logs `threshold=160 … pipeline=set` counting the new `COLLECTOR`.

## Rollback (rehearsal is D2's second half; not executed here)

```bash
# 1. revert the merge commit of PR #2 on desktop-gpu-split (mainline 1 = the branch side)
cd ~/redhat/git/hp-roscon-flywheel && git checkout desktop-gpu-split && git pull --ff-only
M=$(gh pr view 2 --repo RHPhysicalAI/hp-roscon-flywheel --json mergeCommit --jq .mergeCommit.oid)
git revert -m 1 --no-edit "$M"
git diff --stat HEAD~1   # expect exactly the two PR files, 5 lines back
git push origin desktop-gpu-split          # or open a PR from a branch and merge it from the phone
# 2. watch the same fields: observedCommit = the revert commit; templateVersion v5 -> v6;
#    device renderedVersion 5 -> 6; container restarts with Published model_version: act-v2-ft160
#    and NO "Copying blob" / pull in the agent log or podman events (image volume reclaimPolicy: Retain,
#    bdb513ca… still in storage)
ssh jary@10.0.0.51 'sudo podman events --since 10m --filter event=pull 2>/dev/null | tail; sudo podman images --digests | grep modelcar'
```

Rollback is `git revert` + merge (D025). The pipeline does nothing on rollback; the
`manifest-consumer` lineage reverts with the same commit, so the trigger counts `act-v2-ft160` again.

## Pipeline change that produced this PR

`pipeline/act_flywheel_pipeline.py` `open_promotion_pr`: two regexes on the Fleet
(`(soarm-act-modelcar)@sha256:[0-9a-f]{64}` → digest; `^(\s+MODEL_VERSION:\s*)\S+` → candidate),
three on `manifest-consumer.yaml` (`INCUMBENT`, `COLLECTOR` → candidate; `INCUMBENT_CHECKPOINT` → the
run's `checkpoint_uri`), match counts asserted `(1, 1, 3)` before any write; one commit, branch
`promote/<candidate>`, PR body = evidence table + signed digest + Fleet URL + rollback. No
`gitops/act-serving/*` edit remains. Ride-alongs: arch-derived crane/cosign download URLs, `PY_IMG`
pinned to `ubi9/python-312:9.8-1788919789`, modelcar base pinned to `ubi9/ubi-micro:9.8-1787778798`
(pipeline param `modelcar_base`). Regexes unit-tested offline against the live files before upload.

## D2 — what happened after the merge (2026-09-09, 12:04–12:21Z)

PR #2 merged by the operator at **12:04:27Z**, merge commit `4ce6a8a550768dd7889185bca3dac067a035bdf7`.
No human touched the device. Times are UTC from `flightctl get events`, the agent journal and
`podman events` on the VM, and the ROS log clock (`1788955597.789` = 12:06:37.8Z).

| Z | +merge | hop | evidence |
|---|---|---|---|
| 12:04:01 | −0:26 | RS `rhem-fleets` polled the branch **before** the merge: `ResourceSyncCommitDetected 1ae104b7…` | events |
| 12:04:27 | 0:00 | merge | `gh pr view 2 --json mergedAt` |
| 12:06:01 | +1:34 | RS: `ResourceSyncCommitDetected 4ce6a8a5…`; Fleet `ResourceUpdated (spec.template)`; `TemplateVersion` created; `FleetRolloutStarted` — all in the same second | events; `observedCommit: 4ce6a8a5…`, `templateVersion: v5` |
| 12:06:10 | +1:43 | `FleetRolloutBatchDispatched`; Device `ResourceUpdated (spec)`; `DeviceContentUpdating … renderedVersion: 5`; agent `New spec version received: 4 -> 5` (12:06:10.129) | events; `journalctl -u flightctl-agent` |
| 12:06:20.4–20.98 | +1:53 | container `died` → `remove`; `volume remove` + `volume create systemd-act-inference-128875-models` (20.64 / 20.83); container `create` → `init` → `start` (20.89 / 20.95 / 20.98); agent `Removed quadlet application` / `Started quadlet application` / `Spec reconciliation complete: current version 5` | `podman events`; agent journal |
| 12:06:20.99 | +1:54 | `DeviceContentUpToDate` (`Updated to desired renderedVersion: 5`); `DeviceApplicationDegraded: Not started: act-inference` | events; `updated.status: UpToDate` |
| 12:06:37.8 | +2:10 | **`Published model_version: act-v2-ft160-rhem`** | `podman logs` line 29 |
| 12:06:40 | +2:13 | `FleetRolloutBatchCompleted batch 1 … 100%`; `FleetRolloutCompleted`; `RolloutInProgress: Inactive` again — **before** the app is healthy | events; Fleet conditions |
| 12:07:02 | +2:35 | first `health_status` event on the new container id (`b1583a30…`); `podman ps` `(healthy)` | `podman events` |
| 12:07:09 | +2:42 | **`DeviceApplicationHealthy`** — `applicationsSummary: Healthy`, `applications[0]: Running 1/1`, volume reference `@sha256:1375d0bc…` | events; device JSON |
| 12:07:38 | +3:11 | Argo `flywheel` auto-sync on `4ce6a8a5` (no refresh; previous reconcile 12:03:13Z) | `operationState.startedAt` |
| 12:07:40 | +3:13 | `manifest-consumer` new ReplicaSet `77fc96cbfb`, pod `-j2v9l` started, env `INCUMBENT=COLLECTOR=act-v2-ft160-rhem`, `INCUMBENT_CHECKPOINT=…/act-v2-ft160-rhem/…`; log `[consumer] threshold=160 cooldown=3600s pipeline=set` | `oc get rs/pods/deploy`, `oc logs` |
| 12:09:17 | +4:50 | loop started: `IMAGE=quay.io/jary/soarm-flywheel@sha256:2ad1fb1c… MODEL_VERSION=act-v2-ft160-rhem ~/run-coordinator.sh` (guard pid 1136821 armed, 262 GB free); coordinator `Observed model_version: act-v2-ft160-rhem` on the first episode | `docker logs act-coordinator` |
| 12:10:04 | +5:37 | first re-stamped object: `episodes-rejected/act-v2-ft160-rhem/8143aeff-….json` (`rollout-error(truncated)`, first cold episode) | MinIO listing |
| 12:11:53 | +7:26 | **first curated re-stamped episode** `episodes-curated/act-v2-ft160-rhem/6992b04a-….json` — `model_version: act-v2-ft160-rhem`, `verdict: pass`, 3/3 cubes, `dataset_path: bags/1788955880_371180261`; same second on Kafka `episode-manifests` p0@481 | MinIO object; `kafka-console-consumer` with `print.timestamp` |
| 12:21:08 | +16:41 | loop stopped (`docker stop act-coordinator`), 243 GB free, guard still armed | — |

**Merge → device serving the new version: 2 min 10 s. Merge → Healthy: 2 min 42 s.** The only real wait
is the ResourceSync poll (~2 min cadence; this merge landed 26 s after a poll, so it saw the worst
case); everything after `ResourceSyncCommitDetected` is seconds.

### Evidence excerpts

`flightctl get devices` (12:07Z+):
```
NAME                                                   ALIAS                   OWNER                SYSTEM  UPDATED   APPLICATIONS
s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg   act-device.localdomain  Fleet/act-inference  Online  UpToDate  Healthy
```

`flightctl get device/<name> -o json | jq .status.applicationsSummary,.status.updated` (+ `.status.applications`):
```json
{"info": "Device's application workloads are healthy.", "status": "Healthy"}
{"info": "Device was updated to the fleet's latest device spec.", "status": "UpToDate"}
[{"appType":"quadlet","name":"act-inference","ready":"1/1","restarts":0,"status":"Running",
  "volumes":[{"name":"models.volume","reference":"quay.io/jary/soarm-act-modelcar@sha256:1375d0bcc2c7c81867365b55a08bdd5fa03bf31d20cc7bde04044fdcf1a0784e"}]}]
```
Annotations: `device-controller/renderedVersion: "5"`, `fleet-controller/renderedTemplateVersion: v5`; condition
`Updating: False / Updated / "Updated to desired renderedVersion: 5"` at 12:06:20.966Z. Fleet annotations after:
`templateVersion: v5`, `deployingTemplateVersion: v5`, `batchNumber: "3"` (unchanged — it is the sequence position),
`lastBatchCompletionReport: batch 1 … successful 1 failed 0 timedOut 0`.

`flightctl console device/<name> --notty -- sudo -n podman logs --tail 20 act-inference-128875-act-inference` (tail):
```
[INFO] [launch]: All log files can be found below /root/.ros/log/2026-09-09-12-06-22-849618-act-device.localdomain-120
[rosetta_client_node-1] [INFO] [1788955585.709687519] [rosetta_client]: Configured: contract=/tmp/so_arm101_inference.yaml, model=/modelcar/models/act
[rosetta_client_node-1] INFO 2026-09-09 12:06:28 y_server.py:430 PolicyServer started on 127.0.0.1:8080
[rosetta_client_node-1] [INFO] [1788955588.277468664] [rosetta_client]: Activated and ready for policy execution
[INFO] [1788955597.789089998] [model_version_publisher]: Published model_version: act-v2-ft160-rhem
[inference] Publishing model_version for the emitter...
```

`flightctl console … -- sudo -n sh -c '…'` on the device:
```
podman exec … /healthcheck.sh   -> healthy: model_version=act-v2-ft160-rhem action=/run_policy   (rc=0)
podman inspect … -> started=2026-09-09 12:06:20.949159903 +0000 UTC health=healthy failing=0 ; MODEL_VERSION=act-v2-ft160-rhem
/etc/containers/systemd/act-inference/act-inference-128875-models.volume -> Image=quay.io/jary/soarm-act-modelcar@sha256:1375d0bc…
journalctl -u flightctl-agent --since 12:04 | grep -ciE 'copying blob|pulling|pulled' -> 0 pull lines  (only the 6 reconcile lines above)
podman events --since 12:04 --filter event=pull ->
  2026-09-09 12:06:20.868931783 +0000 UTC image pull b21e9348… quay.io/jary/soarm-flywheel@sha256:2ad1fb1c…   (runtime image, local resolve 17 ms before `container create`; NO modelcar pull event)
podman images --digests | grep modelcar ->
  quay.io/jary/soarm-act-modelcar  <none>  sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b  e97419105416  13 days ago  230 MB
  quay.io/jary/soarm-act-modelcar  <none>  sha256:1375d0bcc2c7c81867365b55a08bdd5fa03bf31d20cc7bde04044fdcf1a0784e  46c6257a557a  13 days ago  230 MB
```
No network pull for `1375d0bc…` (pre-pulled in D1); both digests retained (`reclaimPolicy: Retain`). The container
keeps its name and gets a new id — the "new container name" expectation in the watch list above was wrong.

### Lineage downstream

MinIO (`aws --endpoint-url http://10.0.0.49:30900 s3 ls --recursive`, objects per prefix):

| prefix | 12:09:10Z (loop start) | 12:21:30Z (loop stopped) |
|---|---|---|
| `episodes-curated/act-v2-ft160/` | 214 | 214 |
| `episodes-curated/act-v2-ft160-rhem/` | 0 | **1** |
| `episodes-rejected/act-v2-ft160/` | 104 | 104 |
| `episodes-rejected/act-v2-ft160-rhem/` | 0 | **13** |

`manifest-consumer`: the consumer prints nothing per manifest (only `threshold=…` at start and the trigger event), so
"count advances" is evidenced by Kafka, not a log line. Topic `episode-manifests` carries curated passes only; the one
`-rhem` manifest is `p0@481` at 12:11:53Z (`"model_version": "act-v2-ft160-rhem"`; p0@478–480 are `act-v2-ft160` from
00:37–00:39Z). Consumer group `manifest-consumer` after: `p0 482/482 lag 0, p1 516/516 lag 0, p2 497/497 lag 0` — it
consumed offset 481 (`pending` 0 → 1 in-memory for `COLLECTOR=act-v2-ft160-rhem`). Startup line captured:
`[consumer] threshold=160 cooldown=3600s pipeline=set` (pod `manifest-consumer-77fc96cbfb-j2v9l`, 12:07:40Z).

Dashboard `http://10.0.0.49:30801/api/status`: `counts.rejected` 101 → 113 and `counts.sent` 195 → 196 during the loop,
but its top-level `model_version` reads `soarm-act-v2` (a static Phase 3 label), `counts.curated` reads 0, and
`trigger.triggered: true` is stale from PR #1's 160-trigger — the dashboard does **not** show the new lineage
(open item; the lineage screens are MinIO prefixes + `flightctl console`).

### Rollback rehearsal — PR #3 open, NOT merged

`git revert -m 1 4ce6a8a5…` on `rollback/act-v2-ft160-rhem` (from `desktop-gpu-split`) → commit `ff39942`,
`2 files changed, 5 insertions(+), 5 deletions(-)` — exactly PR #2's lines back. Pushed; PR
**https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/3** into `desktop-gpu-split` (opened with the desktop's
`gh` login — the Mac's fine-grained PAT can push but returns 403 on `createPullRequest`). The body carries the revert
table, the `podman images --digests` output above as the Retain evidence, and the expected transitions
(RS → v5→v6 → rv 5→6 → `Published model_version: act-v2-ft160`, no modelcar pull). The operator merges (Gate 3);
D3 then verifies the no-pull rollback and retires `act-serving`/`swap-agent` (D025).

### Loop invocation note

`~/run-coordinator.sh` defaults `MODEL_VERSION=act-v2-ft160`; after a promotion it must be passed explicitly
(`MODEL_VERSION=act-v2-ft160-rhem`) so it equals the device's value (D057 healthcheck contract). Open item: derive the
default from the Fleet file or make it required like `IMAGE`.
