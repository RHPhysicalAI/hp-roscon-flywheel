# Physical AI Edge Flywheel — Demo Runbook (SO-ARM101)

> Re-skinned from `thor-testing/DEMO_RUNBOOK.md` (same six-beat arc, same two cuts). Every URL and
> command below was run against the live desktop system on 2026-09-08 unless marked
> **[not rehearsed]**. The narration claims only what D020–D022 record. Phase 4 item 1.

Two ways to run this:

- **Short Cut (~4–5 min)** — the pinned governed run (run 6, PR #1) and the pre-loaded v1 vs v2
  comparison. Nothing is started; you walk through what the loop already produced while it keeps
  running in the background. Use this for stakeholder walkthroughs. **Start here.**
- **Full Live (~10–12 min)** — the sim and curation live, then a **live pipeline run** that gates,
  packages, signs and opens a promotion PR, which you merge on stage, and the swap lands live.
  The candidate is pre-trained and pre-evaluated (D023) — see *The one honest shortcut*.

---

## The story you're telling (read first)

> **"A governed, self-improving lifecycle for Physical AI, built on Red Hat's platform."**
>
> A SO-ARM101 in simulation runs a trained manipulation policy. Every rollout is recorded and
> scored on ground truth; only the successes are kept and shipped to the hub. When enough of them
> accumulate, the platform retrains **that same policy on its own curated successes**, measures the
> candidate against the incumbent on identical scenes, and refuses to promote unless the gain is
> real. What passes is packaged, signed into a transparency log, and promoted through a pull
> request that a human merges. GitOps flips blue/green and the better policy is back in the sim
> collecting for the next round. **The policy is upstream's; the loop that makes it improvable,
> measurable and governed is the story.**
>
> Six beats: **(1)** the sim, running a trained policy → **(2)** the curator is watching →
> **(3)** training started from the curated data, here's the pipeline → **(4)** v1 vs v2, you can
> see it get better → **(5)** promotion: signed, GitOps PR, blue/green → **(6)** the loop closes,
> and it is the same governed pipeline you'd run to a fleet.

### What is true (the facts the narration is allowed to claim)

| claim | evidence |
|---|---|
| v1 = the upstream ACT teacher as shipped; v2 = the same weights fine-tuned on 160 of its own curated successes (D021) | `docs/eval-records/phase3-ladder/`, HF `jeremyary/soarm-act-v2-ft160` |
| **73% → 86%** on 100 identical seeded scenes; paired **20 fixed / 7 broken**, net +13, sign-test **p = 0.019**; mean cubes 2.51 → 2.73 | `python3 src/eval-report/ladder_report.py …` (§ Reference) |
| Fine-tuning on **20 or 40** successes made the policy **worse** (60%, 56%); 80 broke even (76%) — the eval gate exists because of this | same table |
| The governed pipeline ran end to end unattended: trigger → gate PASS → `crane append` → `cosign sign` (Rekor index 1) → **PR #1** → merged → Argo → swap → v2 live (D022) | run `07aaf328…` in DSP, `~/pipeline-run.log`, PR #1, Rekor entry 1 |
| Round B is autonomous: the consumer counts v2's curated successes and fires at 160; v3 is fine-tuned **from v2**, gated against v2, lands on blue → PR #2 | `gitops/flywheel/manifest-consumer.yaml` (`INCUMBENT=act-v2-ft160`) |

**Not built (don't claim it):** the bootstrap loop (privileged expert, curriculum — Phase 3+), the
eval dashboard (separate owner; Beat 4 uses the static chart), multi-arch images, the fallback
recording. Say "retrained on the curated episodes the loop captured", not "keeps improving on its
own forever".

### The one honest shortcut

Fine-tuning on 160 episodes takes ~25 min on the RTX 5090, and the paired eval gate is
**100 seeded episodes per policy** at up to 60 s each — over an hour per candidate. Nobody watches
that. So in both cuts the **checkpoint and the two N=100 eval records are real artifacts from the
run that already happened** (2026-09-05/08), and everything downstream of them — the gate
decision, the packaging, the signature, the transparency-log entry, the PR, the merge, the sync,
the swap — is either the pinned run (Short Cut) or **runs live** (Full Live; the host runner
recognises the candidate name, reuses the checkpoint and records, and the run takes ~3 minutes
trigger-to-PR, as run 6 did). Nothing is faked; it is time-compressed. Decision: D023.

### Desktop vs. target — say it once

The desktop keeps the GPU **outside** the cluster (D013): the sim and the policy run in Docker on
the host, training/eval run on the host (**host runner**), and the blue/green flip is applied to the
host container by a **swap agent**. Those two are marked `[desktop shim]` and are deleted by the
Fury port; the pipeline, gate, packaging, signing, PR and `gitops/act-serving/` are written for
GB10/GB300 where the GPU is in-cluster. Per-beat differences are in § *On GB10 / GB300 Fury*.

---

## Topology cheat-sheet (verified 2026-09-08)

| screen | where it runs | URL / command from the Mac |
|---|---|---|
| Overhead camera (MJPEG) | host `so-arm-sim` container, port 8081 | `http://10.0.0.48:8081/static` (wrist: `/wrist`, `/health`) |
| Rest-pose picker (both cams + live joints) | host `pose-ui` container | `http://10.0.0.48:8090/` |
| Operational dashboard (Beat 2) | SNO, NodePort | `http://10.0.0.49:30801` (`/api/status` for JSON) |
| MinIO console | SNO route | `https://minio-console-minio.apps.sno-flywheel.local` |
| DSP (KFP) API | SNO, via port-forward on the host | `oc port-forward -n flywheel svc/ds-pipeline-dspa 8888:8888` → `https://localhost:8888` + SA token |
| Host runner / pipeline poll / swap agent logs | host | `~/host-runner.log`, `~/pipeline-run.log`, `~/swap-agent.log` |
| Static chart (Beat 4) | repo, local file | `open docs/phase3-ladder.html` |
| Promotion PR | GitHub | https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/1 |
| Rekor search UI / API | SNO routes (RHTAS) | `https://rekor-search-ui-trusted-artifact-signer.apps.sno-flywheel.local/?logIndex=1` · `https://rekor-server-trusted-artifact-signer.apps.sno-flywheel.local/api/v1/log/entries?logIndex=1` |
| Argo CD | SNO route | `https://openshift-gitops-server-openshift-gitops.apps.sno-flywheel.local` |

Constants: host `jary@10.0.0.48` (always `ssh -n` for one-liners; `ssh … 'bash -s' <<'EOF'` for
scripts, and then inner `ssh` needs `-n`). SNO node `10.0.0.49`. `KUBECONFIG=~/sno-flywheel/auth/kubeconfig`
**on the host** (the Mac has no kubeconfig for this cluster — every `oc` below runs over ssh).
Pipeline id `99ec0aab-51fb-412e-bd2f-47bc6a0d3e3d`, experiment `flywheel-promotions`. Pinned run
`07aaf328-1376-4146-bd4d-8b2a558936db` (run 6). Promoted modelcar
`quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b`.

### Known screen artifacts (narrate, don't debug)

- **Argo `act-serving` shows Degraded** — green's pod is `Pending` because the PR sets
  `replicas: 1` (right for the target) and the desktop has no in-cluster GPU. The swap agent keys on
  the Service colour + digest, not the pod. Line: *"that pending pod is the shape of the real
  deployment; on this desktop the GPU is outside the cluster."*
- **Dashboard model badge reads `soarm-act-v2`** (generic: it maps Service colour green → v2), not
  `act-v2-ft160`. The real label is on the episodes and in `docker inspect`.
- **Dashboard bottom card "Policy comparison v1 vs v2" is empty** (Cosmos-era rollout videos;
  none exist). Keep it below the fold. Beat 4 is not on this dashboard.
- **Dashboard progress bar counts local `sent/` files since the last Clear**; the authoritative
  trigger count is the manifest-consumer's. Never press **Clear Data** during a demo.
- **PR #1 shows two files, not three**: blue was already `replicas: 0` on the desktop, so the blue
  edit was a no-op. The pipeline still writes all three atomically (a second promotion on this
  desktop shows all three).

---

## Prerequisites (both cuts)

1. **Mac `/etc/hosts`** must resolve the routes. Present today: dashboard, gitops-server, console,
   oauth, perses, tempo, sim-cameras. **Add** (one line, all → the node):
   ```
   10.0.0.49 rekor-search-ui-trusted-artifact-signer.apps.sno-flywheel.local rekor-server-trusted-artifact-signer.apps.sno-flywheel.local minio-console-minio.apps.sno-flywheel.local ds-pipeline-dspa-flywheel.apps.sno-flywheel.local
   ```
2. `ssh -n jary@10.0.0.48 true` works; `gh auth status` is logged in; this repo is checked out on
   the Mac on `desktop-gpu-split` (for the chart and the report script).
3. **State check** — run this and read it against the expected block below:
   ```bash
   ssh -n jary@10.0.0.48 'docker ps --format "{{.Names}}  {{.Status}}" | grep -E "^(act-inference|so-arm-sim|pose-ui) ";
     docker inspect act-inference --format "{{range .Config.Env}}{{println .}}{{end}}" | grep ^MODEL_VERSION;
     pgrep -af "host_runner|swap_agent|bag_watchdog" | grep -v pgrep | awk "{print \$3, \$4, \$5}";
     echo "bags: $(ls ~/flywheel-data/bags | wc -l)  free: $(df -h / | awk "NR==2{print \$4}")";
     export KUBECONFIG=~/sno-flywheel/auth/kubeconfig;
     oc get applications.argoproj.io -n openshift-gitops -o custom-columns=APP:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status;
     oc get deploy so-arm-sim -n flywheel -o jsonpath="in-cluster sim replicas: {.spec.replicas}{\"\n\"}"'
   gh pr list -R RHPhysicalAI/hp-roscon-flywheel --state all
   ```
   Expected (Short Cut): `act-inference`, `so-arm-sim`, `pose-ui` all **Up**; `MODEL_VERSION=act-v2-ft160`;
   the three residents (`host_runner.py`, `swap_agent.py --loop 300`, `bag_watchdog.sh`) present;
   bags < 330 and free > 60 GB (the watchdog parks the loop otherwise); seven Argo apps Synced
   (`act-serving` Degraded is expected); in-cluster sim replicas **0**; PR #1 MERGED.
   Full Live expects `MODEL_VERSION=upstream-act-teacher` instead — see § *Reset to start state*.
4. **Credentials you may need on screen** (read them on the host, never paste them into a doc):
   - Argo: user `admin`, password `oc extract secret/openshift-gitops-cluster -n openshift-gitops --keys=admin.password --to=-`
     (or *Log in via OpenShift* as kubeadmin).
   - MinIO console: `oc extract secret/minio-credentials -n minio --to=-` (`root-user`/`root-password`).
5. **Do not** start the in-cluster `so-arm-sim` Deployment (it is intentionally 0 on the desktop; the
   sim is the host container). The dashboard's old Start/Stop button was removed for this reason.

---

## Short Cut — pinned run, pre-loaded comparison (~4–5 min)

### Setup — tabs, left to right (2 min)

1. **Camera** `http://10.0.0.48:8081/static` — Beat 1 (full-screen the tab).
2. **Dashboard** `http://10.0.0.49:30801` — Beat 1 cutaway, Beat 2.
3. **Terminal on the host** (`ssh jary@10.0.0.48`) — Beat 3, Beat 6.
4. **Chart** `open docs/phase3-ladder.html` from the repo — Beat 4.
5. **PR #1** https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/1 — Beat 5.
6. **Rekor UI** `https://rekor-search-ui-trusted-artifact-signer.apps.sno-flywheel.local/?logIndex=1` — Beat 5.
7. **Argo CD** → application `act-serving` — Beat 5/6 (log in during setup).

Pre-fill the terminal (tab 3) so Beat 3 is one keypress:
```bash
tail -n 20 ~/host-runner.log; echo; cat ~/pipeline-run.log | tail -n 3
```

### Beat 1 — "Here's the sim: SO-ARM placing cubes, running a trained policy" (~45 s)

**Screen:** tab 1, the overhead camera stream. Cutaway: the dashboard's *Sim cameras* card
(overhead + wrist), or `http://10.0.0.48:8090/` for the live joint readout.

**Say:**
> "This is a SO-ARM101 in Gazebo, on a desktop standing in for the ZGX Fury, running an ACT
> manipulation policy: pick three cubes, place them on the tray. The green cube lands somewhere
> different every episode — that's the condition the policy was weak on. What you're watching is
> not the policy that shipped; it's **the second version, which this loop trained and promoted
> earlier today.** Hold that thought."

**If it breaks:** stream blank → `ssh -n jary@10.0.0.48 'curl -s -m 3 http://127.0.0.1:8081/health'`
(200 = bridge up; reload the tab). Bridge dead but sim up → the pose UI (tab 8090) has its own copy
of both streams. Sim dead (no `Early stop`/`Resetting cubes` lines in
`docker logs --since 5m act-inference`) → **[not rehearsed]** `docker restart so-arm-sim; sleep 60;
docker restart act-inference pose-ui` (~2 min), otherwise skip to Beat 2 and narrate over the
dashboard's last frames.

### Beat 2 — "The curator is watching: this is the curation stream" (~75 s)

**Screen:** tab 2, the dashboard. Point at the *latest episode* card (rollout status, steps,
duration, **task success, cubes placed, smoothness, curation verdict**) and the curation log
below it filling with `pass` / `reject` rows, then at the progress bar (**n / 160 curated episodes
sent to hub**). Optional cutaway: MinIO console → bucket `episodes-curated` → prefix
`act-v2-ft160/` growing. Terminal alternative for the count:
```bash
ssh -n jary@10.0.0.48 'docker run --rm --network host -v ~/count_curated.py:/c.py --entrypoint python3 act-inference:latest /c.py'   # prints: curated rejected (teacher lineage)
```

**Say:**
> "Every rollout is recorded — a full ROS bag — and scored on **ground truth**: the simulator's
> own cube poses, not a vision guess. Three cubes on the tray and a clean trajectory, it passes and
> the recording is ported to a LeRobot dataset and shipped to the hub with a manifest on Kafka,
> **stamped with the policy that produced it**. Anything less is rejected: its metadata is kept,
> its frames are deleted, and it never becomes training data. This bar is the trigger: at 160 new
> curated successes the platform starts a retrain on its own. That's the flywheel — the robot is
> generating its own training data and quality-gating it."

**If it breaks:** dashboard stale → `curl -s http://10.0.0.49:30801/api/status | python3 -m json.tool | head -30`
(if the JSON moves, reload the page; if not, `oc delete pod -n flywheel -l app=dashboard` on the
host, ~20 s). Dashboard dead → the MinIO console is the screen (objects have timestamps).

### Beat 3 — "Training started from the curated data: here's the pipeline" (~45 s)

**Screen:** tab 3, the host terminal, showing the pinned run. Press the pre-filled command; you get
the runner's log (trigger received → checkpoint → eval records → `PASS — upstream-act-teacher 0.73
-> act-v2-ft160 0.86, fixed 20 broken 7 p=0.0192`) and the poller's final line
(`FINAL SUCCEEDED | … eval-gate=SUCCEEDED … package-modelcar=SUCCEEDED sign-modelcar=SUCCEEDED
open-promotion-pr=SUCCEEDED … run=07aaf328…`). To show the run as the platform sees it (task list
with timestamps, ~3 min trigger-to-PR):
```bash
ssh jary@10.0.0.48 'bash -s' <<'EOF'
export KUBECONFIG=~/sno-flywheel/auth/kubeconfig
pgrep -f "port-forward -n flywheel svc/ds-pipeline-dspa" >/dev/null || { setsid nohup oc port-forward -n flywheel svc/ds-pipeline-dspa 8888:8888 </dev/null >/tmp/pf.log 2>&1 & sleep 4; }
KFP_TOKEN=$(oc create token manifest-consumer -n flywheel --duration=1h) RUN=07aaf328-1376-4146-bd4d-8b2a558936db ~/venv-runner/bin/python - <<'PY'
import os, kfp, warnings; warnings.filterwarnings("ignore")
c = kfp.Client(host="https://localhost:8888", existing_token=os.environ["KFP_TOKEN"], verify_ssl=False)
r = c.get_run(os.environ["RUN"]); print(r.display_name, r.state, r.created_at, "->", r.finished_at)
for t in sorted((t for t in r.run_details.task_details if t.display_name and not t.display_name.endswith("driver") and t.display_name not in ("executor",) and not t.display_name.startswith("root")), key=lambda t: str(t.start_time)):
    print(f"  {t.display_name:22s} {t.state:10s} {str(t.start_time)[11:19]} -> {str(t.end_time)[11:19]}")
PY
EOF
```

**Say:**
> "When the trigger fired, a **Red Hat OpenShift AI pipeline** took over: assemble the curated
> set from the hub, fine-tune the *incumbent* policy on it — LeRobot ACT, starting from its own
> weights, about two epochs — then the step that matters: **the eval gate.** Candidate and
> incumbent each run the same hundred seeded scenes, paired scene by scene, and the pipeline
> refuses to continue unless the candidate fixes more than it breaks with p below 0.05. Training
> takes half an hour, so I'm showing you the real run, not making you watch it. It passed: 73 to
> 86 percent, twenty scenes fixed, seven broken."

**If it breaks:** port-forward flaky → `cat ~/pipeline-run.log` is the same information. Pods as
evidence: `oc get pods -n flywheel | grep promotion-tzzx4`. There is **no graphical run view** on
this cluster (the RHOAI dashboard component is `Removed` in the DSC); the terminal is the screen.

### Beat 4 — "Model improvement: v1 vs v2 side-by-side" (~60 s) — *two screens and a fallback*

**Screen:** tab 4, `docs/phase3-ladder.html` — success rate vs. curated-dataset size, with the
teacher baseline. This is the **Phase 3 static chart** (BUILD-PLAN Beat 4 fallback) and today it
is the primary, because the eval dashboard (separate owner, `docs/data-contract-eval-dashboard.md`)
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
same evidence, and PR #1's body (Beat 5) carries the headline row.

### Beat 5 — "Promotion: signed, GitOps PR, blue/green swap" (~60 s)

**Screen:** tab 5, PR #1. Scroll: the title (`Promote act-v2-ft160 (73% -> 86%)`), the evidence
table and the signed digest in the body, then *Files changed*: green gets the digest +
`MODEL_VERSION` + `replicas: 1`, the Service selector flips to green — **one commit**. Then tab 6,
the Rekor UI: entry **log index 1**, kind `hashedrekord`, integrated 2026-09-08 — the
transparency-log record of that signature. Optional live verify on the host:
```bash
ssh -n jary@10.0.0.48 '~/bin/cosign verify --key ~/cosign/cosign.pub --insecure-ignore-tlog quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b 2>/dev/null | head -3'
```
Then tab 7, Argo `act-serving`: Synced; the Service selects green (see *Known screen artifacts*
for the Degraded/Pending pod line).

**Say:**
> "The gate passed, so the pipeline packaged the checkpoint as an OCI model image, **signed it**
> with cosign, and wrote the signature into the cluster's own transparency log — Red Hat Trusted
> Artifact Signer, that's Rekor entry number one. Then it opened **this pull request**: one commit
> that flips blue to green and carries the evidence in the body. Nobody hand-carried a file.
> The last gate is a human: merging this is the approval. Git is the control plane; Argo syncs it;
> and on the device the model is **verified against the signing key before it loads**. An unsigned
> model simply won't run."

**If it breaks:** GitHub unreachable → `gh pr view 1 -R RHPhysicalAI/hp-roscon-flywheel` in the
terminal. Rekor UI blank (usually a missing hosts entry for `rekor-server-…` — the UI calls it from
the browser) → the API URL in the cheat-sheet returns the raw entry. Argo login fails → skip it;
Beat 6's swap log proves the sync happened.

### Beat 6 — "The loop closes: the same governed pipeline you'd run to a real fleet" (~30 s)

**Screen:** tab 3, the host terminal:
```bash
tail -n 4 ~/swap-agent.log          # live=green model_version=act-v2-ft160 image=sha256:bdb513ca… running=act-v2-ft160 (running)
docker inspect act-inference --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ^MODEL_VERSION
```
Back to tab 2: the dashboard badge reads **v2** and the newest log rows are v2's episodes.

**Say:**
> "After the merge, the swap agent verified the signature, pulled the checkpoint out of the signed
> image and restarted the policy — that's the arm you saw in Beat 1. Its episodes now flow under
> the **v2 lineage**, the trigger is counting them, and at 160 the pipeline fires again: v3
> fine-tuned *from v2*, gated *against v2*, promoted to blue — with no one in the loop after the
> merge. On the Fury the GPU is inside the cluster and the swap is just Argo doing a Recreate
> rollout on one GPU; the pipeline, the gate, the signature and the PR are identical. That is the
> lifecycle: generate, curate, retrain, prove, sign, promote — governed end to end."

Stop recording.

---

## Full Live — live pipeline on a pre-trained candidate (~10–12 min)

**What is live:** the sim and curation; the pipeline run (trigger → gate → package → sign → PR),
which produces a **new Rekor entry** and a **new PR number**; your merge; Argo's sync; the swap
agent's verify + recreate; the badge flip; v2's first episodes. **What is pre-baked:** the v2
checkpoint (`~/flywheel-data/train/act-v2-ft160/`) and the two N=100 eval records
(`~/flywheel-data/eval/eval-act-v2-ft160.json`, `eval-upstream-act-teacher.json`) — the runner
reuses them when the candidate is named `act-v2-ft160` (D023). Timing from run 6:

| step | wall time |
|---|---|
| trigger-and-wait (runner: reuse checkpoint + records, upload report) | ~25 s |
| eval-gate | ~10 s |
| package-modelcar (`crane append` → quay) | ~40 s |
| sign-modelcar (cosign + Rekor) | ~25 s |
| open-promotion-pr | ~25 s |
| **trigger → PR** | **~3 min** (pod scheduling between steps) |
| merge → Argo sync (hard refresh) → swap agent pass → policy reloaded | ~2–3 min |

> **The arm pauses for about a minute during the gate step.** The host runner parks the collection
> loop before the eval (the harness and the loop both drive `/run_policy`), finds the records,
> and restores it. Narrate it: *"the harness just took the GPU to score the candidate — it's
> reusing today's hundred-scene records."*

### Reset to start state — T-30 min **[not rehearsed end to end]**

The demo promotes v2 over v1, so v1 must be live first. This is D022's rollback: the same three
edits reversed, on the branch Argo watches.

```bash
# 0. the stale head branch from PR #1 would make the PR step fail (create_git_ref: reference exists)
gh api -X DELETE repos/RHPhysicalAI/hp-roscon-flywheel/git/refs/heads/promote/act-v2-ft160

# 1. flip act-serving back to blue = upstream-act-teacher (sha256:d5e5897f…, signed) — one commit
cd ~/redhat/git/hp-roscon-flywheel && git checkout desktop-gpu-split && git pull --ff-only
sed -i '' 's/^  replicas: 0 /  replicas: 1 /' gitops/act-serving/deployment.yaml
sed -i '' 's/^  replicas: 1 /  replicas: 0 /' gitops/act-serving/deployment-green.yaml
sed -i '' 's/^    color: green /    color: blue  /' gitops/act-serving/service.yaml
git diff --stat   # expect exactly the three files
git commit -am "demo: reset act-serving to blue (upstream-act-teacher) for the Full Live cut" && git push

# 2. make Argo pick it up now (it polls every ~3 min), then apply it on the desktop now
ssh -n jary@10.0.0.48 'export KUBECONFIG=~/sno-flywheel/auth/kubeconfig; oc patch applications.argoproj.io act-serving -n openshift-gitops --type merge -p "{\"metadata\":{\"annotations\":{\"argocd.argoproj.io/refresh\":\"hard\"}}}"; sleep 20; ~/venv-runner/bin/python ~/swap_agent.py'
#    expect in the output: live=blue model_version=upstream-act-teacher … signature verified … recreated act-inference serving upstream-act-teacher
```

Verify with the state check: `MODEL_VERSION=upstream-act-teacher`, dashboard badge **v1**, arm
still placing cubes (the teacher succeeds ~35% of loop episodes; that's fine, you want reject rows).
The swap agent leaves the previous container parked as `act-inference-pre-upstream-act-teacher-<ts>`;
remove it after the show (`docker rm`). The autonomous round B is paused while v1 collects (the
consumer only counts `act-v2-ft160` episodes); it resumes the moment the demo re-promotes v2.

### Pre-demo (5 min before)

Tabs as in the Short Cut plus **tab 8: GitHub PR list**
(https://github.com/RHPhysicalAI/hp-roscon-flywheel/pulls) — the new PR appears there. Two host
terminals: **T1** `tail -f ~/host-runner.log`, **T2** for the trigger and the poller. Confirm the
state check shows v1 live and PR list has no open PR.

### Part 1 — The platform + Beat 1 (~1.5 min)

Argo (tab 7): seven apps Synced — "everything on this box, operators included, is delivered from
Git." Then Beat 1 as in the Short Cut, but the line ends: *"…this is the **shipped** policy, v1.
Let's watch the platform improve it."*

### Part 2 — Beat 2 live (~2 min)

As in the Short Cut. Let two or three episodes land while you talk; point at a **reject** row
("one cube, the green one fumbled — that's exactly the failure mode we're about to train away")
and a **pass** row.

### Part 3 — Beat 3 live: trigger the pipeline (~3.5 min, talk over it)

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
  -d "{\"display_name\":\"promote-act-v2-ft160-live-$(date +%H%M)\",\"pipeline_version_reference\":{\"pipeline_id\":\"$PID\",\"pipeline_version_id\":\"$VID\"},\"runtime_config\":{\"parameters\":{\"candidate\":\"act-v2-ft160\",\"incumbent\":\"upstream-act-teacher\",\"collector\":\"upstream-act-teacher\",\"incumbent_checkpoint\":\"hf\"}}}" \
  | python3 -c 'import sys,json; r=json.load(sys.stdin); print(r["run_id"])')
echo "run $RUN"; ~/poll_run.sh $RUN & tail -f ~/pipeline-run.log
EOF
```
**[not rehearsed]** — the version lookup and the poller were exercised; the `POST /runs` body is the
consumer's, byte-for-byte in shape, but was not submitted in the rehearsal because the state
was still v2. Rehearse once with the reset above before recording.

**Screen:** T1 (runner) and T2 (poller). **Say** the Beat 3 line as the states advance; when
`eval-gate=SUCCEEDED` appears: *"73 to 86, twenty fixed, seven broken, p 0.019 — the gate is
open."* When `sign-modelcar=SUCCEEDED`: flip to the Rekor UI, search **log index 2** (the new
entry; index 1 is this morning's). Do Beat 4 (chart) while packaging and signing run — it is the
natural place for "why the gate exists".

**If it breaks:** `trigger-and-wait` FAILED → read T1 (the runner's message is the pipeline's);
the usual cause is the runner not resident (`pgrep -af host_runner` — restart with
`nohup ~/venv-runner/bin/python ~/host_runner.py </dev/null >> ~/host-runner.log 2>&1 &`).
`open-promotion-pr` FAILED with 403 or "reference already exists" → the token scope (ops gotcha 8)
or the stale branch (reset step 0). Fall back to the **Short Cut Beat 3–5** on run 6 and PR #1 —
the artifacts are identical in kind.

### Part 4 — Beat 5 live: merge (~1.5 min)

Tab 8: the new PR `Promote act-v2-ft160 (73% -> 86%)` is open. Show the body and the **three**
files changed (blue → 0, green → digest + 1, Service → green). **Merge it** in the browser (or
`gh pr merge <n> -R RHPhysicalAI/hp-roscon-flywheel --merge`). Say the Beat 5 line.

### Part 5 — Beat 6 live: the swap lands (~2.5 min)

Compress Argo's poll and the swap agent's 5-minute loop:
```bash
ssh -n jary@10.0.0.48 'export KUBECONFIG=~/sno-flywheel/auth/kubeconfig; oc patch applications.argoproj.io act-serving -n openshift-gitops --type merge -p "{\"metadata\":{\"annotations\":{\"argocd.argoproj.io/refresh\":\"hard\"}}}"; sleep 20; ~/venv-runner/bin/python ~/swap_agent.py; tail -n 5 ~/swap-agent.log'
```
Expected: `live=green model_version=act-v2-ft160 … signature verified → exported → recreated
act-inference serving act-v2-ft160 (watchdog armed)`. Tab 7: Argo `act-serving` re-synced, Service
→ green. Tab 2: the badge flips to **v2** within a few seconds; the arm resumes after ~1 min
(policy load) and the next log rows carry `act-v2-ft160`. Say the Beat 6 line. Stop recording.

### After the show

The system is back in its normal state (v2 live, round B counting). Clean up:
`ssh -n jary@10.0.0.48 'docker rm $(docker ps -aq -f name=act-inference-pre-)'` and delete the new
`promote/act-v2-ft160` head branch again (reset step 0) so the next promotion can create it.

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
- **Likely questions:** *"Is the improvement just more training?"* — No: same recipe at 20/40
  got worse; the variable is the amount of the policy's own curated data. *"Does cosign verify the
  Rekor entry on the device?"* — Today the swap agent verifies the **key** (`--insecure-ignore-tlog`);
  tlog verification against RHTAS needs its TUF root initialised on the host (follow-up). The Rekor
  UI shows inclusion. *"Why is the green pod Pending?"* — desktop shim; see *Known screen artifacts*.
  *"Where are the datasets?"* — MinIO `episodes-data/` and private HF `jeremyary/soarm-flywheel-*`
  / `soarm-act-*`, LeRobot-native.

## What's real (know this if asked)

**Real, running, genuine:** Gazebo SO-ARM101 with the upstream LeRobot ACT policy (`francocipollone/…`)
· ground-truth scoring and per-episode MCAP recording · curator → MinIO/Kafka with lineage ·
LeRobot fine-tune from the incumbent's weights · seeded N=100 paired eval · RHOAI Data Science
Pipelines run · `crane` modelcar · `cosign` v2.6.5 + RHTAS Rekor · GitOps PR with evidence ·
Argo sync · signature-verified swap.

**Pre-baked / shimmed:** the checkpoint and eval records in the Full Live are from the real
run earlier that day (the runner reuses them — ~2 h of compute compressed); the GPU is outside the
cluster on the desktop (host runner + swap agent); the sim is a simulation — no physical arm; the
dashboard's "Policy comparison" card is an empty Cosmos-era leftover. Be upfront about all of it.

## On GB10 / GB300 Fury — what changes per beat

| beat | desktop (this runbook) | target (GPU in-cluster) |
|---|---|---|
| 1 | sim + policy in host Docker; camera bridge `10.0.0.48:8081`; dashboard hard-codes that host | `so-arm-sim` Deployment `replicas: 1`, camera NodePort **30881**; policy served by the `act-policy` Deployment on the node GPU; dashboard camera host must become configurable (`TODO` in `dashboard.yaml`) |
| 2 | unchanged | unchanged (curator, sync-agent, MinIO, Kafka already in-cluster) |
| 3 | pipeline `mode=desktop`: `trigger_and_wait` hands train/eval to the host runner over Kafka | `mode=cluster`: in-pod train (`nvidia.com/gpu: 1`) and eval components; no runner, no loop pause — but the eval **drives the in-cluster sim**, so the arm visibly runs eval scenes during the gate |
| 4 | frozen files / static chart | identical (booth rule: frozen dataset, no cluster) |
| 5 | modelcar `--platform linux/amd64` to quay; green pod Pending; Argo Degraded | `--platform linux/arm64` to the internal registry; the candidate pod schedules once the incumbent's `Recreate` releases the GPU; Argo Healthy; node `policy.json` + `registries.d` enforce the signature at pull |
| 6 | swap agent recreates the host container | no swap agent: Argo sync → `Recreate` rollout → Service selector flips; `~/*.log` screens become `oc logs`/Argo |

Also multi-arch: `Dockerfile.gpu-inference` (PyTorch cu130) on aarch64 Blackwell is the Phase 4
risk item; the pipeline's `crane`/`cosign` binaries are fetched as `x86_64`/`linux-amd64` in the
components and need the arm64 equivalents.

## Failure recovery

| problem | fix |
|---|---|
| Camera stream blank | `curl -s -m 3 http://10.0.0.48:8081/health`; pose UI `:8090` has its own streams; last resort **[not rehearsed]** `docker restart so-arm-sim; sleep 60; docker restart act-inference pose-ui` |
| Arm frozen, no `Early stop`/`Resetting cubes` in `docker logs --since 5m act-inference` | `docker restart act-inference` (policy reload ~1 min); if the watchdog parked it (bags ≥ 330 or free < 60 GB), port + prune first (`~/assemble_all.sh` pattern, `~/prune_bags.py`), then `~/start_v2_loop.sh` |
| Dashboard not updating | `/api/status` moving? reload; else `oc delete pod -n flywheel -l app=dashboard` (host) |
| Dashboard badge says v1 while v2 is live (or vice versa) | badge = Service colour; `oc get svc act-policy -n flywheel -o jsonpath='{.spec.selector.color}'` vs `docker inspect act-inference … MODEL_VERSION`; run `~/venv-runner/bin/python ~/swap_agent.py` to reconcile |
| DSP port-forward dies | plain `http://` on 8888 kills it (ops gotcha 5) — always `https://`; `pkill -f "port-forward -n flywheel svc/ds-pipeline-dspa"` and re-run |
| Pipeline `trigger-and-wait` FAILED | runner not resident or its message in `~/host-runner.log`; restart the runner; MinIO clock skew after a VM pause → set the node clock (ops extract) |
| `open-promotion-pr` FAILED | 403 → token scope (`github-token` Secret, ops gotcha 8); "reference already exists" → delete `promote/act-v2-ft160` on GitHub |
| Swap agent `REFUSING: signature verification failed` | the digest in `deployment-*.yaml` isn't the one the pipeline signed — check the PR diff; never edit the digest by hand |
| Argo `act-serving` OutOfSync after a manual `oc apply` | selfHeal reverts it (gotcha 9): commit to the branch, hard-refresh |
| Rekor UI shows nothing | browser can't resolve `rekor-server-…` → hosts entry; API URL in the cheat-sheet as fallback |
| Catastrophic | switch to the fallback recording (Phase 4 item 2 — **not yet captured**; until it exists, the Short Cut on the pinned run is the fallback for the Full Live) |
