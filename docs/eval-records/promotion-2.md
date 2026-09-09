<!-- This project was developed with assistance from AI tools. -->
# Promotion 2 — first RHEM Fleet promotion: `act-v2-ft160` → `act-v2-ft160-rhem` (Phase 4.5 D1, D025)

**Date:** 2026-09-09 (run 06:53–06:57 CDT). **State at hand-off: PR #2 open, NOT merged.** The
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
