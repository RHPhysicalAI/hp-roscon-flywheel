# Physical AI Edge Flywheel — Demo Runbook (SO-ARM101 on RHEM)

> Re-skinned from `thor-testing/DEMO_RUNBOOK.md` (same six-beat arc, same two cuts), rewritten for the
> RHEM device plane in Phase 4.5 G-prep. Every URL and command below was run against the live desktop
> system on **2026-09-09** unless marked **[not run today]** or **[not rehearsed]**. The narration
> claims only what D020–D023 and D066–D096 record. Phase 4 item 1 + Phase 4.5 G.

## Plan of record (operator, 2026-09-08)

**Nothing live is promised at the booth.** When the Fury arrives the first job is standing the
flywheel up on it; anything beyond that (a training run on the GB300, a coding agent, NVIDIA's
playbooks for the box) is stretch, time permitting. So the demo must stand on **contingency
recordings and durable artifacts** that convey the narrative and the results with no cluster, no
sim and no network — and every live element is a bonus layered on top, never a dependency.

- **Short Cut (~4–5 min) — the plan of record.** The pinned governed run (run `192f3ec5`, PR #2)
  and the pre-loaded v1 vs v2 comparison, with the RHEM screens (Fleet page, device Applications
  tab, console log, registry row, Catalog graph) in their steady state. Nothing is started; you walk
  through what the loop already produced. Every screen has a recorded/artifact stand-in
  (§ *Contingency kit*; recording script `docs/demo-kit/rhem-kit-script.md`), so the same cut plays
  from the kit alone if the desktop, the Fury or the venue link is down.
- **Full Live (~10–12 min) — conditional.** Run it only if the flywheel is standing on the box you
  present from **and** the whole cycle has been rehearsed there that day. It is the sim and curation
  live, then a **live pipeline run** that gates, packages, signs, registers and opens a promotion PR,
  which you merge on stage, and **RHEM rolls it to the device** in about two minutes. The candidate
  is pre-trained and pre-evaluated (D023) — see *The one honest shortcut*. If any precondition is
  missing, present the Short Cut and say so.

---

## The story you're telling (read first)

> **"A governed, self-improving lifecycle for Physical AI, built on Red Hat's platform."**
>
> A SO-ARM101 in simulation runs a trained manipulation policy. Every rollout is recorded and
> scored on ground truth; only the successes are kept and shipped to the hub. When enough of them
> accumulate, the platform retrains **that same policy on its own curated successes**, measures the
> candidate against the incumbent on identical scenes, and refuses to promote unless the gain is
> real. What passes is packaged, signed into a transparency log, recorded in a model registry, and
> promoted through a pull request that a human merges. **Red Hat Edge Manager rolls the new model to
> the device fleet**, the device verifies the signature before it will even pull the image, and the
> better policy is back in the sim collecting for the next round. **The policy is upstream's; the
> loop that makes it improvable, measurable and governed is the story.**
>
> Six beats: **(1)** the sim, running a trained policy → **(2)** the curator is watching →
> **(3)** training started from the curated data, here's the pipeline → **(4)** v1 vs v2, you can
> see it get better → **(5)** promotion: signed, GitOps PR, RHEM Fleet rollout → **(6)** the loop
> closes on a managed device, and it is the same governed pipeline you'd run to a fleet.

### What is true (the facts the narration is allowed to claim)

| claim | evidence |
|---|---|
| v1 = the upstream ACT teacher as shipped; v2 = the same weights fine-tuned on 160 of its own curated successes (D021) | `docs/eval-records/phase3-ladder/`, HF `jeremyary/soarm-act-v2-ft160` |
| **73% → 86%** on 100 identical seeded scenes; paired **20 fixed / 7 broken**, net +13, sign-test **p = 0.019**; mean cubes 2.51 → 2.73 | `python3 src/eval-report/ladder_report.py …` (§ Reference) |
| Those headline numbers were measured with the rosetta client at chunking **30 / 0.95**; the deployed configuration is **100 / 0.5** (D058/D059). A re-measure at the deployed setting is pending — say so if asked, don't imply the numbers were taken on today's config | D059; `docs/eval-records/phase3-ladder/` |
| Fine-tuning on **20 or 40** successes made the policy **worse** (60%, 56%); 80 broke even (76%) — the eval gate exists because of this | same table |
| The governed pipeline ran end to end unattended on the RHEM path: trigger → gate PASS → `crane append` → `cosign sign` (**Rekor index 4**) → **PR #2** (two files, five lines) → merged by a human → RHEM rolled it to the device with no one on the device: **serving at +2:10, Healthy at +2:42** (D068–D070) | run `192f3ec5…` in DSP, `docs/demo-kit/run-192f3ec5-*`, `docs/demo-kit/pr2.md`, `docs/eval-records/promotion-2.md` |
| Rollback is `git revert` + merge, symmetric: **serving at +1:28, Healthy at +1:59, nothing re-pulled** (image volume `reclaimPolicy: Retain`) — PR #3 (D071, D074) | `docs/eval-records/promotion-2.md` § D3 |
| Every promotion writes one **Model Registry** row per candidate (digest, dataset URI, paired-eval metrics, Rekor index, PR URL, DSP run id) and appends the version to the **RHEM Catalog** graph in the same commit (D083, D090, D095) | `docs/eval-records/model-registry.md`, `docs/eval-records/catalog.md` |
| The device's `policy.json` rejects an unsigned image **and** an image signed with the right key but never logged in Rekor; the logged one pulls (D091) | `docs/eval-records/negative-trust-tests.md` |
| The desktop's managed device is a RHEL 10 VM serving ACT on **CPU** and it passes the D024 gate: p95 forward 184 ms (limit 1000), p99 command gap 30 ms with 0.34 % over 40 ms (restated criterion, D096), **18/20** on the D020 seeds vs the GPU's 17/20 (D065) | `docs/eval-records/cpu-spike.md` |
| Round B is autonomous: the consumer counts the live lineage's curated successes and fires at 160; the candidate is fine-tuned **from the incumbent's own checkpoint**, gated against it, and lands on the Fleet through the same PR (D066) | `gitops/flywheel/manifest-consumer.yaml` (`INCUMBENT`, `COLLECTOR`, `INCUMBENT_CHECKPOINT`) |

**Not built (don't claim it):** the bootstrap loop (privileged expert, curriculum — Phase 3+), the
eval dashboard (separate owner; Beat 4 uses the static chart), the Short Cut recording (Phase 4
item 2, in progress), a GPU device on the desktop (the stand-in is CPU; the Fury is the GPU
device). The Catalog is **v1alpha1** — say so. Say "retrained on the curated episodes the loop
captured", not "keeps improving on its own forever".

### The one honest shortcut

Fine-tuning on 160 episodes takes ~25 min on the RTX 5090, and the paired eval gate is
**100 seeded episodes per policy** at up to 60 s each — over an hour per candidate. Nobody watches
that. So in both cuts the **checkpoint and the two N=100 eval records are real artifacts from the
run that already happened** (2026-09-05/08), and everything downstream of them — the gate
decision, the packaging, the signature, the transparency-log entry, the registry row, the PR, the
merge, the rollout — is either the pinned run (Short Cut) or **runs live** (Full Live; the host
runner recognises the candidate name, reuses the checkpoint and records, and the run takes
~4 minutes trigger-to-PR, as run `9015ecd4` did). Nothing is faked; it is time-compressed. D023.

One more honest line: v2 (`act-v2-ft160`) is **already live** on the device. The RHEM promotion
therefore ships v2's weights under a new name, **`act-v2-ft160-rhem`** (D067) — a declared
re-release, gated against the teacher exactly as v2 was. What the Fleet flips on stage is
`act-v2-ft160 → act-v2-ft160-rhem`; what the *gate* compares is teacher vs fine-tuned. Say it once
if asked; never call it v3.

### Device vs. hub — say it once

The **device** is what RHEM manages: on the desktop a RHEL 10 KVM VM (`act-device`, CPU) that runs
only the policy role — the runtime image, the ModelCar as an image volume, the lineage label and a
health check, all delivered by the Fleet (D024, D057). On the Fury the RHEL 10 host itself is the
device and serves on the GPU. The **hub** is the SNO: RHEM (flightctl 1.3), RHOAI pipelines and
Model Registry, RHTAS Rekor, Argo. The **sim harness** — Gazebo, the camera bridge, the coordinator
that runs episodes, records bags and resets cubes, and the train/eval host runner — runs next to
the simulator on the host (`tools/host/run-coordinator.sh`), because sim reset and the cube judge
use Gazebo transport, which is host-local. Nothing about the promotion path differs between the
two boxes; § *On the Fury* lists what does.

**What to say about the Fury if asked:** the results shown were produced on the desktop stand-in;
the Fury work is, in order, (1) stand the same flywheel up on the GB300, (2) if time allows, use the
box for what the desktop can't — a larger training run, a coding agent, NVIDIA's playbooks for the
system. Don't promise (2) on stage; if it happened, it has its own artifact in the kit.

---

## Contingency kit — what backs each beat when nothing is live

The kit lives in `docs/demo-kit/` (text artifacts committed; recordings and screenshots to be
captured per `docs/demo-kit/rhem-kit-script.md`) plus the repo files listed. **Rule:** if a beat's
live screen is not up 10 minutes before you start, present its kit item and do not try to fix it
on stage.

| beat | live screen | kit item (exists) | to capture (item 2) |
|---|---|---|---|
| 1 | camera stream | — | 30–60 s clip of the arm placing cubes (v2), overhead + wrist |
| 2 | dashboard | `docs/internal/data-contract-eval-dashboard.md` (what the stream contains) | 60 s screen recording of the dashboard with pass/reject rows landing; MinIO console screenshot of `episodes-curated/act-v2-ft160/` |
| 3 | runner log, KFP task list | `docs/demo-kit/run-192f3ec5-task-states.txt`, `docs/demo-kit/run-192f3ec5-host-runner.log` (run 6's files stay as history) | screenshot of the terminal; a live run recording from the kit session |
| 4 | static chart | `docs/internal/phase3-ladder.html`, `docs/eval-records/phase3-ladder/`, `src/eval-report/ladder_report.py`, published chart https://claude.ai/code/artifact/84a1ec60-403d-4a34-ba3f-a0cbb69e5e71 | PNG export of the chart for slides |
| 5 | PR #2, Rekor UI, RHEM Fleet page | `docs/demo-kit/pr2.md`, `docs/demo-kit/rekor-entry-4.json`, `docs/eval-records/promotion-2.md` (the rollout, second by second) | clips **5a–5e** in the kit script: PR *Files changed*, Rekor entry, the merge, the Fleet rollout with the device tab in frame |
| 6 | device Applications tab, console log, dashboard, registry, Catalog | `docs/eval-records/promotion-2.md` § *Lineage downstream*, `docs/eval-records/model-registry.md`, `docs/eval-records/catalog.md` | clips **6a–6f**: Applications tab, `Published model_version:`, badge + bar reset + first re-stamped rows, registry JSON, Catalog graph |
| Q&A | rollback, negative trust | `docs/eval-records/promotion-2.md` § D3, `docs/eval-records/negative-trust-tests.md` | clips **R1–R2**, **N1** |
| all | — | HP status brief https://claude.ai/code/artifact/5359ed44-5028-4ba5-b7bf-d13914e4d0cc; private HF copies of datasets and checkpoints (`jeremyary/soarm-*`) | **the full Short Cut screen recording** (non-negotiable, Phase 4 item 2) |

Keep the recording and the screenshots on the presenting laptop **and** on a USB stick; the venue
network is not part of the plan.

---

## Topology cheat-sheet (verified 2026-09-09)

Three machines: the **desktop** (the dev stand-in, running the sim, the RHEM hub, and the device
VM), the **presenting laptop** (the operator's machine at the booth — browser, phone, `ssh`/`gh`
client; no cluster access or kubeconfig of its own), and the **Fury** (the target hardware, out of
scope until the on-site window). Commands below are run from the presenting laptop unless a step
says otherwise.

| screen | where it runs | URL / command from the presenting laptop |
|---|---|---|
| Overhead camera (MJPEG) | host `so-arm-sim` container, port 8081 | `http://10.0.0.48:8081/static` (wrist: `/wrist`, `/health`) |
| Rest-pose picker (both cams + live joints) | host `pose-ui` container | `http://10.0.0.48:8090/` |
| Operational dashboard (Beat 2, Beat 6 badge) | SNO, NodePort | `http://10.0.0.49:30801` (`/api/status` for JSON) |
| MinIO console | SNO route | `https://minio-console-minio.apps.sno-flywheel.local` |
| DSP (KFP) API | SNO, via port-forward on the host | `oc port-forward -n flywheel svc/ds-pipeline-dspa 8888:8888` → `https://localhost:8888` + SA token |
| Host runner / poller logs | host | `~/host-runner.log`, `~/pipeline-run.log` |
| Static chart (Beat 4) | repo, local file | `open docs/internal/phase3-ladder.html` |
| Promotion PR / rollback PR | GitHub | https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/2 · https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/3 |
| Rekor search UI / API | SNO routes (RHTAS) | `https://rekor-search-ui-trusted-artifact-signer.apps.sno-flywheel.local/?logIndex=4` · `https://rekor-server-trusted-artifact-signer.apps.sno-flywheel.local/api/v1/log/entries?logIndex=4` |
| **RHEM UI** — Fleet page | SNO route (flightctl) | `https://ui.flightctl.apps.sno-flywheel.local/devicemanagement/fleets/act-inference` (OpenShift OAuth login) |
| **RHEM UI** — device page, *Applications* tab | same | `https://ui.flightctl.apps.sno-flywheel.local/devicemanagement/devices/s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg` |
| **RHEM UI** — Catalog (v1alpha1) | same | `https://ui.flightctl.apps.sno-flywheel.local/catalog` → `physical-ai-models` → `soarm-act` |
| `flightctl` CLI | **desktop**, `~/.local/bin/flightctl`, through a `while true` port-forward loop on 3443 (`~/flightctl-pf.log`) | every `flightctl` line below runs over `ssh -n jary@10.0.0.48 '…'` |
| Model Registry REST | SNO route (OAuth proxy; needs a bearer token) | `https://flywheel-rest.apps.sno-flywheel.local/api/model_registry/v1alpha3/…` — the Beat 6 curl below |
| Argo CD | SNO route | `https://openshift-gitops-server-openshift-gitops.apps.sno-flywheel.local` |

Constants: host `jary@10.0.0.48` (always `ssh -n` for one-liners; `ssh … 'bash -s' <<'EOF'` for
scripts, and then inner `ssh` needs `-n`). SNO node `10.0.0.49`. Device VM `10.0.0.51`.
`KUBECONFIG=~/sno-flywheel/auth/kubeconfig` **on the host** (the presenting laptop has no kubeconfig for this
cluster — every `oc` below runs over ssh). Pipeline id `99ec0aab-51fb-412e-bd2f-47bc6a0d3e3d`,
experiment `flywheel-promotions`. Pinned run **`192f3ec5-c0f2-459d-8e84-6e8e32e90b35`** (opened
PR #2; registry proof run `9015ecd4-5524-45cf-b6c7-f045a17860bc` opened PR #4, closed unmerged).
Device `s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg` (alias `act-device.localdomain`),
container `act-inference-128875-act-inference` (the name survives rollouts, D071). Live modelcar
`quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b`
(`act-v2-ft160`); PR #2's promoted modelcar `…@sha256:1375d0bcc2c7c81867365b55a08bdd5fa03bf31d20cc7bde04044fdcf1a0784e`;
runtime image (Tekton, multi-arch) `quay.io/jary/soarm-flywheel@sha256:02e66d895ed4ba328aa43263561027c18406d774887f465ab7acbd81e4c42d08`.

### Known screen artifacts (narrate, don't debug)

- **The Fleet banner goes green ~30 s before the app is Healthy** (D070: rollout success is counted
  on `UpToDate`, not on application health). Show the device's **Applications tab** (or
  `applicationsSummary` in the terminal) for "serving", not the Fleet banner. The tab's *Healthy*
  is `healthcheck.sh`'s verdict, which cannot see a wedged action server (D113): a green tab with
  an arm that isn't moving means read the coordinator log, not the tab.
- **The dashboard badge follows the hub, not the device.** It reads the `manifest-consumer`'s
  `COLLECTOR`, which Argo syncs ~1–3 min after the device is already serving the new version
  (D072). The device's own answer is the console log (`Published model_version:`). If the badge
  reads `soarm-act-v1` the G-prep ConfigMap has not rolled yet — it is not the lineage (D073).
- **The trigger bar reads `0 / 160` right after a promotion** — by design: it counts curated
  successes of the *live* lineage since the consumer's last rollout, which is what the trigger
  counts. Never press **Clear Data** during a demo.
- **The registry row and the Catalog head node carry PR #4's digest (`18cc4412…`), not PR #2's.**
  One registry row per candidate, refreshed by the latest run; the multi-arch modelcar is not
  byte-reproducible run to run (D090). Say "one row per candidate, the latest run"; don't compare
  digests on stage.
- **The stand-in device serves on CPU.** Its command cadence has a 0.34 % residual of gaps over
  40 ms (restated criterion p99 ≤ 40 ms / ≤ 1 %, D096 — PASS) and it scores 18/20 on the D020
  seeds. If asked: "the desktop stand-in is CPU; the Fury serves on the GPU with the same Fleet."
- **`flightctl` says `connection refused 127.0.0.1:3443`** for a few seconds now and then — the
  desktop port-forward loop is re-establishing. Re-run the command.

---

## Prerequisites (both cuts)

1. **Presenting laptop `/etc/hosts`** must resolve the routes. Present today: dashboard, gitops-server, console,
   oauth, perses, tempo, sim-cameras. **Add** (one line, all → the node; the `flightctl` and
   `flywheel-rest` names were missing on 2026-09-09):
   ```
   10.0.0.49 ui.flightctl.apps.sno-flywheel.local api.flightctl.apps.sno-flywheel.local flywheel-rest.apps.sno-flywheel.local rekor-search-ui-trusted-artifact-signer.apps.sno-flywheel.local rekor-server-trusted-artifact-signer.apps.sno-flywheel.local minio-console-minio.apps.sno-flywheel.local ds-pipeline-dspa-flywheel.apps.sno-flywheel.local
   ```
2. `ssh -n jary@10.0.0.48 true` works; `gh auth status` is logged in; this repo is checked out on
   the presenting laptop on `desktop-gpu-split` (for the chart and the report script). Log in to the RHEM UI
   during setup (OpenShift OAuth, kubeadmin) — the Fleet and device pages are Beat 5/6.
3. **State check** — run this and read it against the expected block below:
   ```bash
   ssh -n jary@10.0.0.48 'docker ps --format "{{.Names}}  {{.Status}}" | grep -E "^(so-arm-sim|pose-ui|act-coordinator) ";
     ~/.local/bin/flightctl get devices;
     ~/.local/bin/flightctl get fleet act-inference -o yaml | grep -E "templateVersion|MODEL_VERSION:|soarm-act-modelcar@";
     ~/.local/bin/flightctl get resourcesync;
     pgrep -af "host_runner|disk-guard" | grep -v pgrep | awk "{print \$3}";
     echo "bags: $(ls ~/flywheel-data/bags | wc -l)  free: $(df -h / | awk "NR==2{print \$4}")";
     export KUBECONFIG=~/sno-flywheel/auth/kubeconfig;
     oc get applications.argoproj.io -n openshift-gitops -o custom-columns=APP:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status'
   gh pr list -R RHPhysicalAI/hp-roscon-flywheel --state all
   curl -s http://10.0.0.49:30801/api/status | python3 -c 'import sys,json; s=json.load(sys.stdin); print(s["model_version"], s["trigger"])'
   ```
   Expected (Short Cut): `so-arm-sim`, `pose-ui` **Up** (`act-coordinator` Up only if the loop is
   running — it must be started with `~/run-coordinator.sh` and never runs unattended); the device
   `Online UpToDate Healthy`; Fleet `templateVersion: v7`, `MODEL_VERSION: act-v2-ft160`, modelcar
   `bdb513ca…`; both ResourceSyncs `Accessible True / Synced True`; `host_runner.py` **and**
   `disk-guard.sh` resident; bags < 330 and free > 100 GB; **eight** Argo apps Synced/Healthy;
   PRs #1–#3 MERGED, #4 CLOSED, none OPEN; dashboard `model_version` `act-v2-ft160`.
   Full Live expects the same state — see § *Reset to start state*.
4. **Credentials you may need on screen** (read them on the host, never paste them into a doc):
   - RHEM UI / Argo: *Log in via OpenShift* as kubeadmin (`~/sno-flywheel/auth/kubeadmin-password`
     on the host), or Argo `admin` with `oc extract secret/openshift-gitops-cluster -n openshift-gitops --keys=admin.password --to=-`.
   - MinIO console: `oc extract secret/minio-credentials -n minio --to=-` (`root-user`/`root-password`).
   - Model Registry: a runner SA token, minted on the host inside the Beat 6 curl (10 min lifetime).
5. **Do not** start the in-cluster `so-arm-sim` Deployment (it is intentionally 0 on the desktop; the
   sim is the host container). **Never run two coordinators** (both drive `/run_policy`, D057):
   `run-coordinator.sh` replaces a container of the same name; the D020 eval is `MODE=eval` on the
   same script with the loop stopped first.

---

## Short Cut — pinned run, pre-loaded comparison (~4–5 min)

### Setup — tabs, left to right (2 min)

1. **Camera** `http://10.0.0.48:8081/static` — Beat 1 (full-screen the tab).
2. **Dashboard** `http://10.0.0.49:30801` — Beat 1 cutaway, Beat 2, Beat 6 badge.
3. **Terminal on the host** (`ssh jary@10.0.0.48`) — Beat 3, Beat 5, Beat 6.
4. **Chart** `open docs/internal/phase3-ladder.html` from the repo — Beat 4.
5. **PR #2** https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/2 — Beat 5.
6. **Rekor UI** `https://rekor-search-ui-trusted-artifact-signer.apps.sno-flywheel.local/?logIndex=4` — Beat 5.
7. **RHEM Fleet page** `https://ui.flightctl.apps.sno-flywheel.local/devicemanagement/fleets/act-inference` — Beat 5.
8. **RHEM device page, Applications tab** (URL in the cheat-sheet) — Beat 6.
9. **Catalog** `https://ui.flightctl.apps.sno-flywheel.local/catalog` → `physical-ai-models` → `soarm-act` — Beat 6.

Pre-fill the terminal (tab 3) so Beat 3 is one keypress:
```bash
grep -A5 "192f3ec5" ~/host-runner.log | head -6; echo; tail -n 1 ~/pipeline-run.log | cut -c1-160
```

### Beat 1 — "Here's the sim: SO-ARM placing cubes, running a trained policy" (~45 s)

**Screen:** tab 1, the overhead camera stream. Cutaway: the dashboard's *Sim cameras* card
(overhead + wrist), or `http://10.0.0.48:8090/` for the live joint readout. (The arm only moves
while the loop runs — start it before the show with `~/run-coordinator.sh`, § *Failure recovery*.)

**Say:**
> "This is a SO-ARM101 in Gazebo, on a desktop standing in for the ZGX Fury, running an ACT
> manipulation policy: pick three cubes, place them on the tray. The green cube lands somewhere
> different every episode — that's the condition the policy was weak on. The policy is being
> **served from a managed edge device** — Red Hat Edge Manager put it there — and what you're
> watching is not the policy that shipped; it's **the second version, which this loop trained and
> promoted.** Hold that thought."

**If it breaks:** stream blank → `ssh -n jary@10.0.0.48 'curl -s -m 3 http://127.0.0.1:8081/health'`
(200 = bridge up; reload the tab). Bridge dead but sim up → the pose UI (tab 8090) has its own copy
of both streams. Arm frozen → check the loop is running (`docker ps | grep act-coordinator`) and
the device is Healthy (state check); restart the loop per § *Failure recovery*. Sim dead (no
`Early stop`/`Resetting cubes` lines in `docker logs --since 5m act-coordinator`) → **[not
rehearsed]** `docker restart so-arm-sim; sleep 60` then restart the loop (~2 min), otherwise skip
to Beat 2 and narrate over the dashboard's last frames.

### Beat 2 — "The curator is watching: this is the curation stream" (~75 s)

**Screen:** tab 2, the dashboard. Point at the *latest episode* card (rollout status, steps,
duration, **task success, cubes placed, smoothness, curation verdict**) and the curation log
below it filling with `pass` / `reject` rows, then at the progress bar (**n / 160 curated episodes
sent to hub** — the live lineage's count since the last promotion). Optional cutaway: MinIO console
→ bucket `episodes-curated` → prefix `act-v2-ft160/` growing. Terminal alternative for the count
**[not run today]**:
```bash
ssh -n jary@10.0.0.48 'docker run --rm --network host -v ~/count_curated.py:/c.py --entrypoint python3 act-inference:latest /c.py'   # prints: curated rejected (teacher lineage)
```

**Say:**
> "Every rollout is recorded — a full ROS bag — and scored on **ground truth**: the simulator's
> own cube poses, not a vision guess. Three cubes on the tray and a clean trajectory, it passes and
> the recording is ported to a LeRobot dataset and shipped to the hub with a manifest on Kafka,
> **stamped with the policy that produced it** — the label the device itself publishes. Anything
> less is rejected: its metadata is kept, its frames are deleted, and it never becomes training
> data. This bar is the trigger: at 160 new curated successes the platform starts a retrain on its
> own. That's the flywheel — the robot is generating its own training data and quality-gating it."

**If it breaks:** dashboard stale → `curl -s http://10.0.0.49:30801/api/status | python3 -m json.tool | head -30`
(if the JSON moves, reload the page; if not, `oc delete pod -n flywheel -l app=dashboard` on the
host, ~40 s with the pip install — **[not run today]**). Dashboard dead → the MinIO console is the
screen (objects have timestamps).

### Beat 3 — "Training started from the curated data: here's the pipeline" (~45 s)

**Screen:** tab 3, the host terminal, showing the pinned run. Press the pre-filled command; you get
the runner's six lines for run `192f3ec5` (trigger received → checkpoint exists → both N=100
records reused → `PASS — upstream-act-teacher 0.73 -> act-v2-ft160-rhem 0.86, fixed 20 broken 7
p=0.0192`) and the poller's final `FINAL SUCCEEDED | … eval-gate=SUCCEEDED …` line. To show the run
as the platform sees it (task list with timestamps, 3 m 45 s trigger-to-PR):
```bash
ssh jary@10.0.0.48 'bash -s' <<'EOF'
export KUBECONFIG=~/sno-flywheel/auth/kubeconfig
pgrep -f "port-forward -n flywheel svc/ds-pipeline-dspa" >/dev/null || { setsid nohup oc port-forward -n flywheel svc/ds-pipeline-dspa 8888:8888 </dev/null >/tmp/pf.log 2>&1 & sleep 4; }
KFP_TOKEN=$(oc create token manifest-consumer -n flywheel --duration=1h) RUN=192f3ec5-c0f2-459d-8e84-6e8e32e90b35 ~/venv-runner/bin/python - <<'PY'
import os, kfp, warnings; warnings.filterwarnings("ignore")
c = kfp.Client(host="https://localhost:8888", existing_token=os.environ["KFP_TOKEN"], verify_ssl=False)
r = c.get_run(os.environ["RUN"]); print(r.display_name, r.state, r.created_at, "->", r.finished_at)
for t in sorted((t for t in r.run_details.task_details if t.display_name and not t.display_name.endswith("driver") and t.display_name not in ("executor",) and not t.display_name.startswith("root")), key=lambda t: str(t.start_time)):
    print(f"  {t.display_name:22s} {t.state:10s} {str(t.start_time)[11:19]} -> {str(t.end_time)[11:19]}")
PY
EOF
```
(Run `9015ecd4…` shows the same list plus `register-model` and `record-pr-url`, 4 m 10 s.)

**Say:**
> "When the trigger fired, a **Red Hat OpenShift AI pipeline** took over: assemble the curated
> set from the hub, fine-tune the *incumbent* policy on it — LeRobot ACT, starting from its own
> weights, about two epochs — then the step that matters: **the eval gate.** Candidate and
> incumbent each run the same hundred seeded scenes, paired scene by scene, and the pipeline
> refuses to continue unless the candidate fixes more than it breaks with p below 0.05. Training
> takes half an hour, so I'm showing you the real run, not making you watch it. It passed: 73 to
> 86 percent, twenty scenes fixed, seven broken."

**If it breaks:** port-forward flaky → `tail ~/pipeline-run.log` is the same information. Pods as
evidence: `oc get pods -n flywheel | grep promotion` **[not run today]**. There is **no graphical
run view** on this cluster (the RHOAI dashboard component is `Removed` in the DSC); the terminal is
the screen.

### Beat 4 — "Model improvement: v1 vs v2 side-by-side" (~60 s) — *two screens and a fallback*

**Screen:** tab 4, `docs/internal/phase3-ladder.html` — success rate vs. curated-dataset size, with the
teacher baseline. This is the **Phase 3 static chart** (BUILD-PLAN Beat 4 fallback) and today it
is the primary, because the eval dashboard (separate owner, `docs/internal/data-contract-eval-dashboard.md`)
is not built. When it lands it takes this slot **and must run from the frozen records** in
`docs/eval-records/phase3-ladder/` — at the booth there is no cluster and no Kafka. Terminal
alternative (reproduces every number from those files):
```bash
python3 src/eval-report/ladder_report.py docs/eval-records/phase3-ladder --baseline eval-teacher-v1 --rungs eval-ft-20ep:20,eval-ft-40ep:40,eval-ft-80ep:80,eval-ft-160ep:160
```

**Say:** point at exactly three things.
> "Same policy, same hundred scenes, the only variable is how much of its own curated data it was
> fine-tuned on. **Twenty or forty episodes made it worse** — it broke scenes the original solved.
> Eighty broke even. **A hundred and sixty is a clean gain: 73 to 86 percent, twenty fixed against
> seven broken, p of 0.019.** Two lessons: more curated data, same policy, measurably better — and
> a flywheel *without* that gate would have shipped the 40-episode model and made the fleet worse.
> The gate isn't decoration; it's what keeps a bad retrain off the robot."

**If it breaks:** the chart is a local file; if the browser can't open it, the table above is the
same evidence, and PR #2's body (Beat 5) carries the headline row.

### Beat 5 — "Promotion: signed, GitOps PR, RHEM Fleet rollout" (~75 s)

**Screen:** tab 5, PR #2. Scroll: the title (`Promote act-v2-ft160-rhem (73% -> 86%)`), the
evidence table and the signed digest in the body, the `Fleet:` link and the `Rollback:` line, then
*Files changed*: **two files, five lines** — the Fleet gets the new modelcar digest and
`MODEL_VERSION`; the trigger gets `INCUMBENT`, `COLLECTOR`, `INCUMBENT_CHECKPOINT` — **one commit**.
Then tab 6, the Rekor UI: entry **log index 4**, kind `hashedrekord`, integrated 2026-09-09 — the
transparency-log record of that signature. Optional live verify on the host (port-forward to
`svc/rekor-server` on 8090 is resident):
```bash
ssh -n jary@10.0.0.48 'SIGSTORE_REKOR_PUBLIC_KEY=~/rekor-live.pub ~/bin/cosign verify --key ~/cosign/cosign.pub --rekor-url http://localhost:8090 quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b 2>&1 | grep -E "^  - "'
#   -> claims validated; existence in the transparency log verified offline; signature verified
```
Then tab 7, the RHEM Fleet page `act-inference`: template version `v7`, rollout `Inactive`,
batch 1 `100% success`, the device row `UpToDate` / `Healthy`. Terminal equivalent:
```bash
ssh -n jary@10.0.0.48 '~/.local/bin/flightctl get fleet act-inference -o yaml | grep -E "templateVersion|batchNumber|MODEL_VERSION:|soarm-act-modelcar@" ; ~/.local/bin/flightctl get device/s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg -o json | jq -c ".status.applicationsSummary,.status.updated"'
ssh -n jary@10.0.0.48 '~/.local/bin/flightctl get events --limit 8'      # the rollout, as RHEM logged it
```

**Say:**
> "The gate passed, so the pipeline packaged the checkpoint as an OCI model image, **signed it**
> with cosign, and wrote the signature into the cluster's own transparency log — Red Hat Trusted
> Artifact Signer, that's Rekor entry four. It recorded the candidate in the model registry, and
> then it opened **this pull request**: one commit, two files. The first is the **Edge Manager
> Fleet** — the model digest and the version label; the second points the trigger at the new
> lineage. Nobody hand-carried a file. The last gate is a human: merging this is the approval —
> I did it from my phone. Git is the control plane; Edge Manager picks up the commit and rolls the
> Fleet: **about two minutes from merge to the device serving the new model.** One thing to know
> when you watch it: the Fleet banner goes green about thirty seconds before the application is
> actually healthy, so look at the device's Applications tab, not the banner. And on the device the
> model is **verified against the signing key and the transparency log before it will even pull**.
> An unsigned model, or one signed but never logged, simply won't run."

Optional Q&A line, terminal (the strings are verbatim from the device, D091; **not re-run on the
device today** — `docs/eval-records/negative-trust-tests.md`):
```bash
ssh -n jary@10.0.0.48 '~/.local/bin/flightctl console device/s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg --notty -- sudo -n podman pull quay.io/jary/soarm-flywheel:negtest-notlog-2026-09-09'
#   -> Error: … Source image rejected: missing dev.sigstore.cosign/bundle annotation   (signed with the right key, never logged in Rekor; exit 125)
ssh -n jary@10.0.0.48 '~/.local/bin/flightctl console device/s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg --notty -- sudo -n podman pull quay.io/jary/soarm-flywheel@sha256:06413b09f79dd1186d7b199ffa56b01d56c12f50ed2a4671fb299cac297a66e3'
#   -> Error: … Source image rejected: A signature was required, but no signature exists   (unsigned; exit 125)
```
Never sign `negtest-notlog-2026-09-09` into Rekor and never move a tag onto it — its only value is
that it fails.

**If it breaks:** GitHub unreachable → `gh pr view 2 -R RHPhysicalAI/hp-roscon-flywheel` in the
terminal, or `docs/demo-kit/pr2.md`. Rekor UI blank (usually a missing hosts entry for
`rekor-server-…` — the UI calls it from the browser) → the API URL in the cheat-sheet returns the
raw entry, or `docs/demo-kit/rekor-entry-4.json`. RHEM UI login fails → the two `flightctl` lines
above are the same screen.

### Beat 6 — "The loop closes on a managed device: the same governed pipeline you'd run to a fleet" (~60 s)

**Screen:** tab 8, the device page, *Applications* tab: `act-inference` **Running 1/1**, Healthy,
the volume reference `…soarm-act-modelcar@sha256:bdb513ca…`. Then tab 3, the host terminal — the
device's own word for what it is serving:
```bash
ssh -n jary@10.0.0.48 '~/.local/bin/flightctl get device/s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg -o json | jq -c ".status.applicationsSummary,.status.updated,.status.applications[0].volumes"'
ssh -n jary@10.0.0.48 '~/.local/bin/flightctl console device/s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg --notty -- sudo -n podman logs --tail 20 act-inference-128875-act-inference | grep "Published model_version"'
#   -> [model_version_publisher]: Published model_version: act-v2-ft160
```
Then the registry row (route + token minted on the host; prints the fields, never the token):
```bash
ssh -n jary@10.0.0.48 'export KUBECONFIG=~/sno-flywheel/auth/kubeconfig; TOK=$(oc create token pipeline-runner-dspa -n flywheel --duration=10m);
  curl -sk --resolve flywheel-rest.apps.sno-flywheel.local:443:10.0.0.49 -H "Authorization: Bearer $TOK" https://flywheel-rest.apps.sno-flywheel.local/api/model_registry/v1alpha3/model_versions | python3 -c "import sys,json; [print(v[\"name\"], \"|\", v[\"description\"], \"|\", {k:(v[\"customProperties\"][k].get(\"string_value\") or v[\"customProperties\"][k].get(\"int_value\") or v[\"customProperties\"][k].get(\"double_value\")) for k in (\"fixed\",\"broken\",\"sign_test_p\",\"rekor_index\",\"pr_url\",\"dsp_run_id\")}) for v in json.load(sys.stdin)[\"items\"]]";
  curl -sk --resolve flywheel-rest.apps.sno-flywheel.local:443:10.0.0.49 -H "Authorization: Bearer $TOK" https://flywheel-rest.apps.sno-flywheel.local/api/model_registry/v1alpha3/model_artifacts | python3 -c "import sys,json; [print(a[\"name\"], a[\"uri\"]) for a in json.load(sys.stdin)[\"items\"]]"'
#   -> act-v2-ft160-rhem | upstream-act-teacher 0.73 -> act-v2-ft160-rhem 0.86, net +13, p=0.0192 | {fixed 20, broken 7, p 0.0192, rekor_index 8, pr_url …/pull/4, dsp_run_id 9015ecd4-…}
#      soarm-act quay.io/jary/soarm-act-modelcar@sha256:18cc4412…
```
Then tab 9, the Catalog: `physical-ai-models` → `soarm-act` → versions `2.0.0-ft160` →
`2.0.0-ft160-rhem` (`replaces`). Terminal equivalent:
```bash
ssh -n jary@10.0.0.48 '~/.local/bin/flightctl get catalogitem soarm-act --catalog physical-ai-models -o yaml | grep -E "version:|container:|replaces:"'
```
Back to tab 2: the dashboard badge reads **`act-v2-ft160`** — the hub's view of the lineage — and
the newest log rows are that lineage's episodes.

**Say:**
> "After the merge nobody touched the device. Edge Manager rendered the Fleet, the device pulled
> the signed model image — verifying it first — restarted the policy container, and published its
> new version label on the robot's own topic; that's the arm you saw in Beat 1. Its episodes now
> flow under that lineage, the trigger is counting them, and at 160 the pipeline fires again: the
> next candidate fine-tuned *from this one*, gated *against this one*, promoted through the same
> PR — with no one in the loop after the merge. The **model registry** holds the durable record:
> digest, dataset, the paired-eval numbers, the Rekor index, the PR. The **Edge Manager catalog**
> — alpha API today — shows the version graph beside the Fleet: the Fleet pins the digest, the
> catalog shows what replaced what. And if the new version misbehaves, rollback is a `git revert`
> and a merge: **ninety seconds**, and nothing is re-pulled, the previous image is still on the
> device. That is the lifecycle: generate, curate, retrain, prove, sign, register, promote, roll
> out — governed end to end, on one device or a fleet."

Stop recording.

---

## Full Live — live pipeline on a pre-trained candidate (~10–12 min) — *conditional*

**Preconditions, all required:** the flywheel is standing on the box you present from (desktop
today; the Fury only once its port is done); the run → merge → rollout → rollback cycle below was
rehearsed on that box the same day (the kit recording session, `docs/demo-kit/rhem-kit-script.md`,
is that rehearsal); the contingency kit is open in a second window. Otherwise present the Short
Cut. Nothing in the booth plan depends on this cut.

**What is live:** the sim and curation; the pipeline run (trigger → gate → package → sign →
register → PR), which produces a **new Rekor entry**, a refreshed **registry row** and a **new PR
number**; your merge; the RHEM rollout; the badge flip; the re-stamped first episodes. **What is
pre-baked:** the v2 checkpoint (`~/flywheel-data/train/act-v2-ft160-rhem` → `act-v2-ft160`) and
the two N=100 eval records (`~/flywheel-data/eval/eval-act-v2-ft160-rhem.json`,
`eval-upstream-act-teacher.json`) — the runner reuses them when the candidate is named
`act-v2-ft160-rhem` (D023, D067). Timing from runs `192f3ec5` / `9015ecd4` and D069:

| step | wall time |
|---|---|
| trigger-and-wait (runner: reuse checkpoint + records, upload report) | ~60 s |
| eval-gate | ~10 s |
| package-modelcar (`crane append` → quay, both arches) | ~40 s |
| sign-modelcar (cosign + Rekor, recursive) | ~20 s |
| register-model | ~15 s |
| open-promotion-pr + record-pr-url | ~40 s |
| **trigger → PR** | **~4 min** (pod scheduling between steps) |
| merge → ResourceSync poll → device serving | **~2 min** (1:28 best, 2:10 measured, ~3 worst) |
| → device Healthy | +30 s |
| → Argo syncs the consumer (badge, trigger lineage) | ~3 min after the merge |

> **The arm pauses for about a minute during the gate step** if the loop is running: the host
> runner parks the collection loop before the eval (the harness and the loop both drive
> `/run_policy`), finds the records, and restores it. Narrate it: *"the harness just took the
> arm to score the candidate — it's reusing today's hundred-scene records."* (Run `192f3ec5` ran with
> the loop already stopped; the pause is **[not rehearsed]** with the RHEM device.)

### Reset to start state — T-30 min

The start state **is today's state**: Fleet `MODEL_VERSION: act-v2-ft160` @ `bdb513ca…` (after
PR #3), no open PR, dashboard badge `act-v2-ft160`. If a previous rehearsal left
`act-v2-ft160-rhem` live, roll it back exactly as PR #3 did (rehearsed 2026-09-09, D074) — from the
**desktop** (its `gh` login can open PRs; the presenting laptop's PAT cannot):
```bash
ssh jary@10.0.0.48 'bash -s' <<'EOF'
cd ~/redhat/git/hp-roscon-flywheel && git checkout desktop-gpu-split && git pull --ff-only
M=$(gh pr view <n> --repo RHPhysicalAI/hp-roscon-flywheel --json mergeCommit --jq .mergeCommit.oid)
git checkout -b rollback/act-v2-ft160-rhem-$(date +%H%M) && git revert -m 1 --no-edit "$M"
git diff --stat HEAD~1   # expect exactly gitops/rhem/fleet-act-inference.yaml + gitops/flywheel/manifest-consumer.yaml, 5 lines back
git push -u origin HEAD && gh pr create --repo RHPhysicalAI/hp-roscon-flywheel --base desktop-gpu-split --fill
EOF
# merge it (phone or `gh pr merge <n> --merge`), then watch — nothing to apply by hand (ResourceSync ~2 min, no re-pull, Argo re-syncs the consumer ~3 min):
ssh -n jary@10.0.0.48 '~/.local/bin/flightctl get device/s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg -o json | jq -c ".status.applicationsSummary,.status.applications[0].volumes"'
```
Verify with the state check. Head branches auto-delete on merge (D089 applied by the operator), so
the PR step's `promote/<candidate>-<run8>` branch never collides (D087).

### Pre-demo (5 min before)

Tabs as in the Short Cut plus **tab 10: GitHub PR list**
(https://github.com/RHPhysicalAI/hp-roscon-flywheel/pulls) — the new PR appears there. Two host
terminals: **T1** with the device watch pre-filled (Part 5), **T2** for the trigger and the poller.
Start the loop (`IMAGE=<the Fleet's runtime digest> MODEL_VERSION=act-v2-ft160 ~/run-coordinator.sh`,
guard armed). Confirm the state check shows `act-v2-ft160` live and the PR list has no open PR.

### Part 1 — The platform + Beat 1 (~1.5 min)

RHEM Fleet page (tab 7) and Argo (eight apps Synced — "everything on this hub, operators
included, is delivered from Git; the device is enrolled in Edge Manager"). Then Beat 1 as in the
Short Cut, but the line ends: *"…this is what the device is serving right now. Let's watch the
platform promote the next version to it."*

### Part 2 — Beat 2 live (~2 min)

As in the Short Cut. Let two or three episodes land while you talk; point at a **reject** row
("one cube, the green one fumbled — that's exactly the failure mode the gate protects against")
and a **pass** row.

### Part 3 — Beat 3 live: trigger the pipeline (~4 min, talk over it)

In **T2**, submit the run exactly as the manifest-consumer would (same API, same parameters), then
poll it:
```bash
ssh jary@10.0.0.48 'bash -s' <<'EOF'
export KUBECONFIG=~/sno-flywheel/auth/kubeconfig
pgrep -f "port-forward -n flywheel svc/ds-pipeline-dspa" >/dev/null || { setsid nohup oc port-forward -n flywheel svc/ds-pipeline-dspa 8888:8888 </dev/null >/tmp/pf.log 2>&1 & sleep 4; }
TOK=$(oc create token manifest-consumer -n flywheel --duration=1h)
PID=99ec0aab-51fb-412e-bd2f-47bc6a0d3e3d
VID=$(curl -sk -H "Authorization: Bearer $TOK" "https://localhost:8888/apis/v2beta1/pipelines/$PID/versions?sort_by=created_at%20desc&page_size=1" | python3 -c 'import sys,json; print(json.load(sys.stdin)["pipeline_versions"][0]["pipeline_version_id"])')
RUN=$(curl -sk -X POST -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" https://localhost:8888/apis/v2beta1/runs \
  -d "{\"display_name\":\"promote-act-v2-ft160-rhem-live-$(date +%H%M)\",\"pipeline_version_reference\":{\"pipeline_id\":\"$PID\",\"pipeline_version_id\":\"$VID\"},\"runtime_config\":{\"parameters\":{\"candidate\":\"act-v2-ft160-rhem\",\"incumbent\":\"upstream-act-teacher\",\"collector\":\"upstream-act-teacher\",\"incumbent_checkpoint\":\"hf\"}}}" \
  | python3 -c 'import sys,json; r=json.load(sys.stdin); print(r["run_id"])')
echo "run $RUN"; ~/poll_run.sh $RUN & tail -f ~/pipeline-run.log
EOF
```
**[not submitted today]** — these are the parameters runs `192f3ec5` and `9015ecd4` were submitted
with on 2026-09-09 (`docs/eval-records/promotion-2.md`, `model-registry.md`); the latest pipeline
version (`v-202609091125-catalog`) adds `register-model`, `record-pr-url` and the Catalog append.
Rehearse once in the kit session before recording.

**Screen:** T2 (poller) and the runner log. **Say** the Beat 3 line as the states advance; when
`eval-gate=SUCCEEDED` appears: *"73 to 86, twenty fixed, seven broken, p 0.019 — the gate is
open."* When `sign-modelcar=SUCCEEDED`: flip to the Rekor UI, search the **new** log index (the
tree was at 14 on 2026-09-09; the run adds three entries — index manifest + two per-arch — and the
PR body names the first). Do Beat 4 (chart) while packaging, signing and registering run — it is
the natural place for "why the gate exists".

**If it breaks:** `trigger-and-wait` FAILED → read `~/host-runner.log` (the runner's message is
the pipeline's); the usual cause is the runner not resident — restart it per § *Failure recovery*
(source `~/.minio-env` first). `open-promotion-pr` FAILED with 403 → the token scope (ops
gotcha 8). Fall back to the **Short Cut Beat 3–5** on run `192f3ec5` and PR #2 — the artifacts
are identical in kind.

### Part 4 — Beat 5 live: merge (~1.5 min)

Tab 10: the new PR `Promote act-v2-ft160-rhem (73% -> 86%)` is open. Show the body and the **two**
files changed (`gitops/rhem/fleet-act-inference.yaml`: digest + `MODEL_VERSION`;
`gitops/flywheel/manifest-consumer.yaml`: lineage; plus `gitops/rhem-catalog/catalogitem-soarm-act.yaml`
if the Catalog seam appended a version — three files then, D095). **Merge it on the phone** (or
`gh pr merge <n> -R RHPhysicalAI/hp-roscon-flywheel --merge`), say the time out loud. Say the
Beat 5 line.

### Part 5 — Beat 6 live: the rollout lands (~3 min)

Nothing to compress: ResourceSync polls ~2 min, everything after it is seconds (D069). **T1**:
```bash
ssh -n jary@10.0.0.48 'watch -n 5 "~/.local/bin/flightctl get device/s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg -o json | jq -c .status.config.renderedVersion,.status.updated.status,.status.applicationsSummary.status,.status.applications[0].volumes[0].reference"'
```
Expected: `renderedVersion` +1 and `UpToDate` at about +1:30–2:00 from the merge, the volume
reference flips to the new digest, `Healthy` at about +2:00–2:45, the console log
`Published model_version: act-v2-ft160-rhem`. Tab 7: the Fleet page shows the rollout — **the
banner goes green ~30 s before the app is Healthy (D070): show tab 8, the Applications tab.**
Then restart the loop under the new label (`docker stop act-coordinator; IMAGE=… MODEL_VERSION=act-v2-ft160-rhem ~/run-coordinator.sh`,
D073 — the emitter already stamps the device's label; this only silences the coordinator's
mismatch warning). Tab 2: at ~+3 min Argo syncs the consumer, the badge flips to `act-v2-ft160-rhem`
and the bar resets to `0 / 160`; the first re-stamped reject lands ~1 min into the loop, the first
pass ~2.5 min (D073). Say the Beat 6 line. Stop recording.

### After the show

Roll back exactly as § *Reset to start state* (the revert PR; ~1.5–2 min to serving, no re-pull)
so the system is back in its normal state (`act-v2-ft160` live, the consumer counting it), or leave
`-rhem` live deliberately and note it in the state check. Stop the loop (`docker stop act-coordinator`).

---

## Reference: the numbers & screens behind the beats

*Not narrated; for follow-ups and to set the chart up correctly.*

From `docs/eval-records/phase3-ladder/` via `ladder_report.py` (all rungs fine-tuned from the
teacher's weights, ~2 epochs, LR 1e-5; eval = seeds 1000–1049, +1050–1099 for the endpoints):

| curated successes | N | success | mean cubes | 0/1/2/3 | fixed / broken vs v1 | net | sign p |
|---|---|---|---|---|---|---|---|
| 0 (teacher, v1) | 100 | **73%** | 2.51 | 1/20/6/73 | — | — | — |
| 20 | 50 | 60% | 2.28 | 0/16/4/30 | 7 / 14 | −7 | 0.19 |
| 40 | 50 | 56% | 2.32 | 0/12/10/28 | 6 / 15 | −9 | 0.08 |
| 80 | 50 | 76% | 2.64 | 0/6/6/38 | 8 / 7 | +1 | 1.00 |
| **160 (= v2)** | 100 | **86%** | 2.73 | 1/11/2/86 | **20 / 7** | **+13** | **0.019** |

- **The gate rule** (D022): candidate vs incumbent on the same 100 seeds; promote iff
  `fixed − broken > 0` **and** paired sign-test `p < 0.05`. Plain-English restatement for stage:
  *"at least ten points better, and it must not break more than it fixes."* Both pass v2; both
  reject 20/40/80.
- **Why paired, why 100:** at N=50 the +12 was p≈0.11; paired at N=100, p=0.019. The eval homes
  the arm each episode; the loop does not, so the loop's curated/rejected ratio (~35–38% for the
  teacher) understates the policy's clean-start rate (73%). Don't mix the two numbers.
- **Scene condition:** one random cube (`cube_medium`, radius 3 cm, yaw ±180°), 60 s window,
  success = 3/3 by ground-truth pose; smoothness = mean |Δjoint| between `/joint_states` samples.
- **Rollout timings, as measured (D069, D074):** promotion merge → `ResourceSyncCommitDetected`
  +1:34 (worst case, one poll) → device spec +1:43 → container recreated +1:53 → `UpToDate` +1:54
  → `Published model_version` +2:10 → `FleetRolloutCompleted` +2:13 → `DeviceApplicationHealthy`
  +2:42 → Argo consumer sync +3:11. Rollback: +0:51 / +1:28 / +1:59 / +2:16. Every hop after
  the ResourceSync detection matched to the second; the spread is the poll phase.
- **Likely questions:** *"Is the improvement just more training?"* — No: same recipe at 20/40
  got worse; the variable is the amount of the policy's own curated data. *"Does the device verify
  the Rekor entry?"* — Yes: the Fleet writes `policy.json` (`sigstoreSigned` + `keyPath` +
  `rekorPublicKeyPath`) and `registries.d`; podman checks the signature and its Rekor SET at pull
  (D091: unsigned → `A signature was required, but no signature exists`; signed-but-unlogged →
  `missing dev.sigstore.cosign/bundle annotation`). *"Why is the device on CPU?"* — the desktop's
  device is a VM stand-in for the Fury host; it passes the D024 gate (p95 184 ms, p99 gap 30 ms,
  18/20) and the Fury serves the same Fleet on the GPU. *"What does the Fleet's green banner
  mean?"* — spec delivered (`UpToDate`), not app healthy; the Applications tab is "serving"
  (D070). *"Why does the registry digest differ from the Fleet's?"* — one row per candidate,
  refreshed by the latest run; the multi-arch modelcar is not byte-reproducible (D090).
  *"Where are the datasets?"* — MinIO `episodes-data/` and private HF `jeremyary/soarm-flywheel-*`
  / `soarm-act-*`, LeRobot-native.

## What's real (know this if asked)

**Real, running, genuine:** Gazebo SO-ARM101 with the upstream LeRobot ACT policy (`francocipollone/…`)
· ground-truth scoring and per-episode MCAP recording · curator → MinIO/Kafka with lineage ·
LeRobot fine-tune from the incumbent's weights · seeded N=100 paired eval · RHOAI Data Science
Pipelines run · multi-arch `crane` modelcar · `cosign` v2.6.5 + RHTAS Rekor, recursive · Model
Registry row · GitOps PR with evidence · RHEM Fleet rollout to an enrolled device with node-side
signature + Rekor enforcement · `git revert` rollback with no re-pull · runtime image built and
signed in-cluster by Tekton for amd64 + arm64.

**Pre-baked / shimmed:** the checkpoint and eval records in the Full Live are from the real
run earlier that day (the runner reuses them — ~2 h of compute compressed); the RHEM candidate is
v2's weights re-released as `act-v2-ft160-rhem` (D067); the desktop's device is a CPU VM (the sim,
camera bridge and coordinator stay on the host GPU box); the sim is a simulation — no physical arm;
the Catalog API is v1alpha1. Be upfront about all of it.

## On the Fury — the device is the host

The Fleet is the same file; the difference is where the device is and what its labels say.

| | desktop (this runbook) | Fury (on site, Phase 4.5 G) |
|---|---|---|
| managed device | RHEL 10 KVM VM `act-device` (8 vCPU, no GPU) enrolled from `device/enroll.sh` with `site=desktop gpu=none policy_device=cpu zenoh_router=10.0.0.48 zenoh_port=7447` | **the RHEL 10 host itself**, `device/provision.sh` on aarch64 (installs podman, the flightctl agent, `nvidia-container-toolkit` + `nvidia-ctk cdi generate`), enrolled with `site=fury gpu=nvidia arch=arm64 policy_device=cuda zenoh_router=<fury-ip> zenoh_port=7447` |
| what the Fleet renders from the labels | CPU branch (thread caps), `POLICY_DEVICE=cpu`, batch 1 of the rollout sequence | `AddDevice=nvidia.com/gpu=all` (CDI), `POLICY_DEVICE=cuda`, batch 2 (`site=fury` after `site=desktop`) |
| runtime image | Tekton multi-arch digest, amd64 platform resolves | same digest, arm64 platform resolves (`docs/eval-records/runtime-image-tekton.md`) |
| sim + camera bridge + coordinator | host Docker (`so-arm-sim`, `pose-ui`; `tools/host/run-coordinator.sh` with `ENGINE=docker`) | host **podman** from the arm64 images; `ENGINE=podman tools/host/run-coordinator.sh` (SELinux `:z` handled by the script) — **[not rehearsed]** |
| hub | SNO VM on the desktop | fresh SNO 4.19+ on the Fury: `argocd/*-app.yaml` + `rhem/bootstrap/*` applied by hand (`argocd/README.md`, `rhem/bootstrap/README.md`); device pulls from quay.io, no mirror |
| Beat 1 | camera bridge `10.0.0.48:8081`; dashboard hard-codes that host | bridge on the Fury host; dashboard camera host must become configurable (`TODO` in `dashboard.yaml`) |
| Beat 3 | pipeline `mode=desktop`: the host runner trains/evals on the desktop GPU | same runner on the Fury GPU (or `mode=cluster` if the GPU is given to the SNO — not planned) |
| Beats 5–6 | identical: PR → ResourceSync → Fleet rollout → device Healthy → registry + Catalog | identical; the rollout sequence would reach `site=fury` only after `site=desktop` succeeds if both devices are enrolled in one hub |

Fury prerequisites still open: `ENGINE=podman` run of the coordinator and the arm64 sim image
**[not rehearsed]**; the aarch64 Blackwell CUDA path of the runtime image is built but has not
served a policy yet.

## Failure recovery

| problem | fix |
|---|---|
| Camera stream blank | `curl -s -m 3 http://10.0.0.48:8081/health`; pose UI `:8090` has its own streams; last resort **[not rehearsed]** `docker restart so-arm-sim; sleep 60` then restart the loop |
| Arm frozen, no `Early stop`/`Resetting cubes` in `docker logs --since 5m act-coordinator` | the loop is off or the device unhealthy: state check; restart the loop (below); if the device app is not Healthy, `flightctl get events --limit 10` says why |
| **Loop (re)start** | on the host, disk guard first if not resident: `nohup ~/disk-guard.sh >/dev/null 2>&1 &`; then `IMAGE=quay.io/jary/soarm-flywheel@sha256:02e66d895ed4ba328aa43263561027c18406d774887f465ab7acbd81e4c42d08 MODEL_VERSION=<the Fleet's MODEL_VERSION> ~/run-coordinator.sh` (the script refuses without the guard or with < 100 GB free; the last loop ran on the interim digest `2ad1fb1c…` — first run with the Tekton digest **[not run today]**). Stop with `docker stop act-coordinator`. Never two coordinators (D057) |
| Bags ≥ 330 or free < 100 GB (guard parks the loop) | port + prune first (`~/assemble_all.sh` pattern, `tools/host/prune_bags.py --yes`), then restart the loop |
| **Host runner not resident** (`pgrep -af host_runner` empty; `trigger-and-wait` FAILED) | on the host: `set -a; source ~/.minio-env; set +a; nohup ~/venv-runner/bin/python ~/host_runner.py </dev/null >> ~/host-runner.log 2>&1 &` (the runner reads the MinIO credentials from the environment) **[not run today — the runner was resident]** |
| Dashboard not updating | `/api/status` moving? reload; else `oc delete pod -n flywheel -l app=dashboard` (host) **[not run today]** |
| Dashboard badge disagrees with the device | the badge is the hub's view (`manifest-consumer` `COLLECTOR`, Argo-synced ~3 min after the merge); the device's is `flightctl console … podman logs … \| grep Published`; `soarm-act-v1` means the G-prep ConfigMap has not rolled |
| `flightctl`: `connection refused 127.0.0.1:3443` | the desktop port-forward loop is re-establishing (`tail ~/flightctl-pf.log`); retry in 5 s |
| Fleet rollout not starting after a merge | `flightctl get resourcesync` (both `Synced True`; `observedCommit` must be the merge) and `flightctl get events --limit 10`; a ResourceSync poll is ~2 min — wait one before debugging |
| Device app `Degraded` after a rollout | expected for ~30 s between `UpToDate` and `Healthy` (D070); if it persists, `flightctl console … podman logs --tail 50 act-inference-128875-act-inference` |
| DSP port-forward dies | plain `http://` on 8888 kills it (ops gotcha 5) — always `https://`; `pkill -f "port-forward -n flywheel svc/ds-pipeline-dspa"` and re-run |
| `open-promotion-pr` FAILED | 403 → token scope (`github-token` Secret, ops gotcha 8); a ref collision is handled since D087 (per-run branch, force-move) |
| Rekor UI shows nothing | browser can't resolve `rekor-server-…` → hosts entry; API URL in the cheat-sheet as fallback |
| Catastrophic (no desktop, no Fury, no link) | present from the **contingency kit** (§ above): the Short Cut screen recording plus the per-beat artifacts in `docs/demo-kit/`. Until the recording exists (Phase 4 item 2), the text artifacts, the chart, PR #2 and `promotion-2.md` carry the results |
