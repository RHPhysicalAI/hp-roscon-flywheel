# Build Plan — Desktop-as-Fury Flywheel

## Goal

Prove the complete Physical AI Edge Flywheel with SO-ARM101 end-to-end on the Ubuntu desktop
(single-box, loop closed), so the Fury phase is pure aarch64 scale-up, not debugging.

The flywheel the brief promises is a **real** one: the sim generates episodes, the curator keeps
the good ones, training runs **on those**, the result is signed and promoted, and the arm gets
better. Phases 0–2 built the plumbing and the producer; Phase 2.5 makes the data loop real;
Phase 3 closes it with governance; Phase 3+ makes the improvement autonomous.

---

## Status snapshot

| Phase | State | Reference |
|---|---|---|
| 0 — Desktop foundation | **Done** (VFIO passthrough deferred) | D001–D006 |
| 1 — Hub plane on SNO | **Done** | D007–D012 |
| 2 — SO-ARM producer | **Done** | D013, D014, D016 |
| 2.5 — Close the data loop | **Complete** — steps 1–6 done & verified; only raw-in-hub archival remains, deferred to Fury (D019) | D017–D019 |
| 3 — Training + close the loop | **Complete** (2026-09-08) — eval harness (D020), self-improvement proof (D021: 73% → 86%, p=0.019), governed pipeline through RHOAI/RHTAS with PR #1 merged and swapped, loop closed on v2; static chart shipped | D015, D020–D022 |
| 3+ — Bootstrap loop | **Post-ROSCon** — designed, prerequisites complete (recording + trainable data landed in Phase 2.5, flywheel-data training proven in Phase 3), not scheduled; the remaining work is the privileged expert, a held-out randomized eval set, and the curriculum controller (operator decision 2026-09-09) | `BOOTSTRAP-LOOP.md` |
| 4 — Demo hardening + Fury prep | **In progress** — item 1 done (2026-09-08): `docs/DEMO_RUNBOOK.md`, Short Cut + Full Live, every screen verified live (D023) | D023 |
| 4.5 — RHEM device plane | **In progress** — A–F, G-prep done; full-project review, hardening and deployment done (D113–D130); kit recording + rehearsal (operator), then Fury on site | D024–D120 |

---

## Phase 0 — Desktop Foundation ✅

**Goal:** SNO running in a KVM VM on the Ubuntu desktop with resource headroom quantified.

Delivered: SNO (OCP 4.17.56) in a 16 vCPU / 72 GiB KVM VM, bridged networking, resource baseline
recorded. GPU passthrough was **deliberately deferred** (D001): it would make the desktop headless
and kill the active session, and no Phase 0–1 workload needed the GPU. The GPU question was later
answered a different way — see Phase 2 (D013).

### Exit criteria
- [x] SNO running in KVM VM on the desktop
- [ ] RTX 5090 passed through and `nvidia.com/gpu: 1` schedulable — *deferred (D001), superseded by the host GPU split (D013)*
- [ ] `cuda-vectoradd` pod exits 0 — *deferred with the above*
- [x] Resource budget documented in DECISIONS.md (D003)
- [x] Network: VM accessible from the Mac for `oc` / dashboard access (D006)

---

## Phase 1 — Port the Hub Plane onto SNO ✅

**Goal:** thor-testing flywheel plumbing round-trips on x86 SNO with a smoke-test producer.

Delivered: MinIO, Kafka (KRaft), curator, sync-agent, dashboard, Argo CD, Tempo, Perses on SNO.
Dummy episodes verified end-to-end (D010). OSD/multi-cluster manifests dropped (D007); MinIO on
emptyDir for now (D009); Perses trace panels deferred (D011). Full inventory in D012.

### Exit criteria
- [x] Hub-plane services running on SNO
- [x] Dummy episodes flow through curator -> sync-agent -> MinIO -> Kafka
- [x] Dashboard accessible
- [x] All OSD-specific assumptions identified and resolved

---

## Phase 2 — Build the SO-ARM Producer ✅

**Goal:** SO-ARM101 Gazebo sim generates curator-compatible episodes; curator sorts good/bad;
curated data reaches MinIO + Kafka.

Delivered (D013, D016):
- **Desktop GPU split** — sim runs as a host container; the ACT policy runs on the host RTX 5090
  in `act-inference`; the two are bridged by zenoh (`rmw_zenoh_cpp`). This replaced the VFIO plan.
- **Episode lifecycle** — `inference-coordinator` phases reset → start → policy window → cancel →
  end, and now **ends early** once 3/3 cubes are placed and the arm has settled.
- **Ground-truth task success** — `task_eval.py` reads cube poses from `/world/pai_world/pose/info`
  (the `gz model` path was blind; D016). Task success is a **hard gate** in the curator.
- **Scene randomization** — `sim_reset.py` randomizes cube position/yaw (`RANDOM_RADIUS`,
  `RANDOMIZE_ONLY`), cubes-only reset (world reset kills the controllers).
- **Camera streaming** — host MJPEG bridge to the browser.
- **Weak baseline policies** — an episodes × steps sweep of deliberately undertrained ACT
  checkpoints (D014); the 40 ep × 40k run lands 2/3 consistently.

### Exit criteria
- [x] SO-ARM sim generates episodes in the curator-compatible schema
- [x] Curator sorts good/bad correctly (real success signal as of D016)
- [x] Curated data reaches MinIO + Kafka
- [x] Arm visible in a browser

### Known divergence carried into Phase 2.5
The emitter writes **score metadata only**. The contract's `rosbag_path` — the pointer to the
full rollout data the brief says training consumes — is never populated, and all training to date
has used the upstream HuggingFace corpus. The loop today is *sim → score → store score*. See D017.

---

## Phase 2.5 — Close the Data Loop

**Goal:** the flywheel moves **training data**, not just scores. Every rollout is recorded in a
trainable format, gated by the curator, stored in MinIO, and consumable by `lerobot-train` — so
"we retrain on the curated episodes" is literally true. This restores the original design intent
of the episode contract (`THOR-TESTING-REUSE.md`) and is the prerequisite for both Phase 3 and 3+.

1. **Record each episode in LeRobot format**, aligned to the coordinator's `start`/`end` signals
   on `/flywheel/episode_control`: camera frames + joint states + commanded actions per timestep.
   Wire the upstream recorder (`pai_data_collection`, or Rosetta's recording path) rather than
   writing one. Target the LeRobot v2 layout (parquet + video per episode) so `lerobot-train`
   loads it directly.

2. **Populate the contract.** Replace the never-populated `rosbag_path` with a `dataset_path`
   pointing at the recorded episode. Keep the lightweight JSON metadata the curator scores on.

3. **Sync-agent ships the data.** Upload the recorded episode alongside the curated JSON —
   `episodes-data/<model_version>/<episode_id>/` in MinIO — and carry the data URI in the Kafka
   manifest. Rejected episodes keep their metadata; decide whether their frames are retained.

4. **Dataset assembler.** A step that pulls curated episode shards from MinIO (filtered by
   `model_version`, `scene`, and curator score) into a local LeRobot dataset root the trainer
   consumes. This is the seam between the data plane and training.

5. **Model-version lineage.** `MODEL_VERSION` must reflect the policy actually running whenever
   `act-inference` is swapped. It is stale today (D016) and becomes load-bearing here.

6. **Storage.** MinIO is already on a PVC (`minio-data`, 50Gi — D009 resolved), so the corpus
   persists; size up before uploading raw bags. Bag retention is handled on the producer side:
   the coordinator prunes a rollout's bag unless it reached 3/3, so only curated episodes persist
   (D018 prune). *Still to do: ship the curated **bag data** (not just JSON) to MinIO — the bags
   live on the host while the sync-agent runs in-cluster, so this needs a host-side upload path.*

7. **Verify the loop end-to-end:** policy runs → episode recorded → curator passes it → uploaded →
   assembled → `lerobot-train` trains on it → checkpoint loads in `act-inference`.

### Exit criteria
- [x] Every rollout produces a recorded episode (per-episode MCAP bag) referenced from its JSON
  record via `dataset_path`; curated bags port to LeRobot v2 on assembly (D018 steps 2–4)
- [x] Curated episode **data** lands in MinIO **with a manifest on Kafka** — the ported LeRobot
  dataset (the trainable form, D019) is uploaded to `episodes-data/<model_version>/<repo_id>.tar.gz`,
  and the assembler publishes a dataset manifest (s3_uri, model_version, num_episodes/frames, fps,
  size, episode_ids, timestamp) to the `dataset-manifests` topic. Both verified end-to-end.
- [x] A training dataset can be assembled from MinIO curated episodes — `--from-minio` pulls the
  curated selection from `episodes-curated` and ports it (raw frames transit the host by design,
  D019). The result is the same LeRobot format proven trainable in step 5.
- [x] `lerobot-train` trains a checkpoint from flywheel-captured data and it runs in the sim
  (ACT 5000 steps on 4 curated episodes → checkpoint loads in `act-inference`, drives the arm)
- [x] `MODEL_VERSION` lineage is correct on every emitted episode — the coordinator (co-located
  with the served policy) publishes the label latched on `/flywheel/model_version`; the emitter
  adopts it, so a checkpoint swap on `act-inference` alone re-labels every episode (verified)

---

## Phase 3 — Training + Close the Loop

**Goal:** the governed pipeline closes the loop on flywheel-captured data: assemble → train →
eval-gate → package → sign → promote → blue/green swap, and v2 is demonstrably better than v1.

Method decisions: **D015** (stay with ACT, no RL pivot) and **D021** (revises D015's proof). The
proof is **self-improvement**: v1 = the teacher policy as shipped, v2 = the *same* policy fine-tuned
on its own curated successes under the one-random-cube condition, compared on the fixed seeded eval
(D020). Under that condition the teacher succeeds ~38% — real headroom — and curation is a filter
that shifts the policy toward the behaviors that work on the hard cases (self-imitation /
rejection-sampling fine-tuning). The earlier from-scratch dataset-size ladder was tried and retired:
it reads as distillation ("the good policy trained a worse copy of itself"), not the flywheel.

1. **Generate the curated corpus through the flywheel.** Run the strong upstream policy in the
   sim under randomization; the curator gates its rollouts; Phase 2.5 records and stores them.
   The upstream policy is the *teacher* — its successes are real, high-quality demonstrations
   captured by the loop. (Phase 3+ replaces the teacher with a privileged expert.)

2. **Eval harness.** A fixed N episodes per policy against a **fixed, repeatable scene set**
   (randomization off, or a fixed seed list). Record success rate (3/3), partial-placement
   distribution, and mean smoothness. The scorer and emitter already produce all of it.

3. **Self-improvement round (D021).** Eval the teacher (v1). Fine-tune it from its own weights
   (`lerobot-train --policy.path=<teacher>`) on the assembled curated successes; eval the result (v2)
   with the identical seeds. Promote only on measured improvement. Iterate — run v2 in the loop,
   curate *its* successes, fine-tune → v3 — for a rising curve across flywheel rounds. (The
   dataset-size angle survives as a bonus: fine-tune on 10 vs 40 successes → bigger gain.)

4. **KFP training pipeline** (shape from thor-testing, code new): assemble dataset → `lerobot-train`
   → eval-gate on success-rate improvement vs. the incumbent → package as a KServe modelcar with
   `crane append` → cosign-sign (v2.4.1, RHTAS) → open a promotion PR.
   **Design decided in D022** (2026-09-08): RHOAI DSP/KFP runtime, RHTAS signing, paired N=100
   eval gate (net > 0, p < 0.05), trigger at 160 new curated successes; built for the in-cluster-GPU
   target with the desktop's host-GPU pieces as marked shims.

5. **Blue/green hot-swap** of the served ACT policy, mirroring the `vllm-cosmos3` service-selector
   flip. GPU deployments use `Recreate`, never `RollingUpdate`.

6. **v1 → v2 comparison artifact:** success rate vs. dataset size, before/after video, smoothness
   distributions. The "you can see it get better" moment. The read-only **eval dashboard**
   (separate repo, owned outside the pipeline) renders this live; a **static chart generated
   from the episode records is the fallback** so the artifact never depends on the dashboard
   existing.

7. **Close the loop:** the promoted v2 runs in the sim, its rollouts flow through the curator, the
   dashboard shows the healthier stream.

### Exit criteria
- [x] Curated training corpus exists in MinIO, produced entirely through the flywheel — `flywheel-teacher-all-2026-09-08` (450 episodes) + per-episode records; v2's corpus accumulating
- [x] Training pipeline runs end-to-end from MinIO data — D022 run 6: trigger → assemble/train (host shim, MinIO in/out) → paired eval gate → crane package → cosign+Rekor sign → promotion PR, unattended
- [x] v2 policy signed (Rekor index 1), promoted via GitOps (PR #1 merged), blue/green swapped (Argo → green; host swap agent recreated `act-inference` as `act-v2-ft160` after cosign verify)
- [x] v1 vs v2 improvement demonstrable on the fixed eval set — D021 round 2: fine-tuning the teacher on 160 of its own curated successes → **86% vs 74%** on 50 identical seeds (8 fixed / 2 broken); 20/40 *degrade* it, which is the eval-gate's justification. **Confirmed at N=100: 73% → 86%, 20 fixed / 7 broken, p = 0.019.**
- [x] Full loop closes (sim -> record -> curate -> train -> sign -> promote -> sim) — v2 is running in the sim and its curated rollouts land under `act-v2-ft160/`; round B fires on its own at 160 (D022)

---

## Phase 3+ — Bootstrap Loop (autonomous improvement)

**Goal:** the loop improves **without a human or a fixed teacher in the round** — new capability
enters from a privileged expert that knows more than the policy, and the deployable vision policy
chases it across a widening scene distribution. Design and rationale: `BOOTSTRAP-LOOP.md`.

Depends on Phase 2.5 (recording) and Phase 3 (pipeline). Only genuinely bootstrapping variants
belong here; retraining on the policy's own successes is not one of them (D015).

1. **Privileged expert.** A scripted pick-and-place that reads exact cube poses from
   `/world/pai_world/pose/info` and plans with MoveIt / IK (`so_arm100_moveit_config`,
   `pai_teleop_ik`). It succeeds because it has ground-truth state the policy never sees.

2. **Expert-driven data generation.** Run the expert under randomization; record via Phase 2.5;
   curate. This replaces the upstream policy as the source of demonstrations and can produce
   frontier data the upstream policy cannot.

3. **Distillation.** Train ACT on the expert corpus (Phase 3 pipeline). Eval on a **held-out
   randomized** scene set; promote only if success improves.

4. **Curriculum controller.** Widen the scene distribution as success rises — larger
   `RANDOM_RADIUS`, all-cube randomization, distractors, lighting/texture variation. The expert
   still solves it; the policy has to catch up. Loop.

5. **Alternative frontier sources** (optional, same loop): human corrections via leader-arm
   teleop (`pai_leader_teleop`, `feetech_ros2_driver`) — DAgger-style; and, later, RL fine-tuning
   on the BC prior using the task-success reward.

### Exit criteria
- [ ] Expert reliably solves randomized scenes from ground-truth state
- [ ] Policy success on held-out randomized scenes rises across autonomous rounds
- [ ] No human-generated or fixed-teacher data in the promoted checkpoint's corpus
- [ ] Curriculum widens without manual intervention

---

## Phase 4 — Demo Hardening + Fury Prep

**Goal:** demo-ready on the desktop; arm64 images build; Fury porting checklist ready.

**Sequencing on the Fury (operator, 2026-09-08).** When the box arrives the first and only committed
job is to **stand the flywheel up on it** (items 3–4). Anything further — a training run sized for
the GB300, a coding agent on the box, NVIDIA's playbooks for the system — is **stretch, time
permitting**, and none of it is promised for the booth. Nothing live is guaranteed at the venue:
the demo must be able to run entirely from **contingency recordings and durable artifacts** that
convey the narrative and the results (`docs/DEMO_RUNBOOK.md` § *Contingency kit*, `docs/demo-kit/`).
Live elements are layered on top of that, never depended on.

1. **Adapt the demo runbook** ✅ — `docs/DEMO_RUNBOOK.md` (2026-09-08, D023). Re-skin the 6-beat narrative for SO-ARM:
   - Beat 1: "Here's the sim — SO-ARM placing cubes, running a trained policy"
   - Beat 2: "The curator is watching — this is the curation stream"
   - Beat 3: "Training started from the curated data — here's the pipeline"
   - Beat 4: "Model improvement — v1 vs v2 side-by-side"
   - Beat 5: "Promotion — signed, GitOps PR, RHEM Fleet rollout" (was "blue/green swap" until D024/D025)
   - Beat 6: "The loop closes — same governed pipeline you'd run to a real fleet"
   - Short Cut (~4-5 min): pinned run, pre-loaded v1/v2 comparison
   - Full Live (~10-12 min): live training + promotion
   The narration must match what was actually built: Phase 2.5/3 done → "retrained on the
   curated episodes the loop captured"; Phase 3+ done → "the loop keeps improving on its own."

   **Beat 4 has two screens and a fallback.** The existing operational dashboard is Beat 2 (the
   live curation stream); the eval dashboard is Beat 4 (aggregate v1 vs v2). Keep them separate.
   At the booth there is no cluster and no live Kafka, so the eval dashboard must run from a
   **frozen dataset** — the Phase 3 ladder's episode records handed over as files — and the
   Short Cut plays from those. If the dashboard isn't ready or breaks, Beat 4 falls back to the
   static chart from Phase 3 step 6.

   **Done:** every beat has screen / command / spoken line / fallback, plus the desktop-vs-Fury
   difference per beat. Full Live = live pipeline + merge + swap on a pre-trained candidate (training
   and the N=100 gate take ~2 h — D023); reset-to-v1 procedure included. The end-to-end Full Live
   cycle is marked *not rehearsed* and belongs to item 2's recording session.

2. **Record the contingency kit** — the plan of record, not insurance: the full Short Cut screen
   recording on the desktop plus the per-beat clips and screenshots listed in the runbook's
   *Contingency kit* table (text artifacts already in `docs/demo-kit/`). Copies on the presenting
   laptop and a USB stick. Non-negotiable. The Full Live cycle (reset → run → merge → swap) gets
   its one rehearsal in this session and stays conditional (D023).
   **Script ready (Phase 4.5 G-prep):** recording is the operator's.

3. **Multi-arch image prep:** every custom image builds for `linux/amd64` and `linux/arm64`.
   Document every x86-specific assumption. The GPU inference image (PyTorch cu130) on aarch64
   Blackwell is the highest-risk item. The eval dashboard is a plain-Python container and should
   be trivially multi-arch — or it runs on a laptop at the booth; either is acceptable.
   Runtime images build and sign in-cluster via Tekton (OpenShift Pipelines), multi-arch, with a
   Rekor entry per manifest (D028).

4. **Fury porting checklist:**
   - Container stack builds/runs on aarch64 Blackwell
   - Full flywheel loop at small scale
   - SO-ARM sim + camera stream on aarch64
   - ACT policy serves on aarch64
   - All x86 assumptions resolved
   - ✅ Node-side signature enforcement applied, not just documented: `policy.json` `sigstoreSigned`
     + `registries.d` `use-sigstore-attachments: true` — written to the device by the RHEM Fleet's
     inline config (`gitops/rhem/fleet-act-inference.yaml`) and proven by the negative-trust tests
     (D091). The pre-RHEM `gitops/act-serving/README.md` that only described it is retired (D075).

5. **Structured promotion record.** ✅ **Done (2026-09-09, Phase 4.5 E1).** Today the join across model version, modelcar digest,
   dataset URI, eval report, and Rekor index exists only in the PR body and git history
   (`open_promotion_pr` in `pipeline/act_flywheel_pipeline.py`; `eval_report.json` carries no
   digest or dataset URI). Add a pipeline step that writes one record per candidate binding all
   five, into Kubeflow Model Registry on RHOAI (add the `modelregistry` component to
   `gitops/operators-config/dsc.yaml`). The PR body stays as the human-readable view.
   Motivation: RHEM engineering is building an RHOAI-registry → RHEM-catalog bridge so Fleet
   rollout policies can carry promoted models; a registry record is the handoff point. Also
   answers the thor-testing gap where MLflow registry entries were hand-backfilled, never
   pipeline-emitted.

6. **Stretch (only after 3–4 are green on the Fury):** a GB300-sized training run, a coding agent
   on the box, or an NVIDIA playbook for the system — each with its own artifact for the kit if it
   happens. Not on the critical path; not in the narration unless done.

### Exit criteria
- [ ] Demo runs from the contingency kit alone (recording + artifacts), no cluster or link needed
- [ ] Demo-ready on desktop with runbook — *runbook done (D023); "demo-ready" waits on one full rehearsal of the Full Live cut (item 2)*
- [ ] Contingency kit recorded (Short Cut recording + per-beat clips/screenshots)
- [x] arm64 images build — Tekton builds and signs both arches with a Rekor entry per manifest (D101–D112, `docs/eval-records/runtime-image-tekton.md`)
- [x] Fury porting checklist written — `docs/FURY-SETUP.md` (D120); the aarch64 GPU serving path is built but not yet exercised on hardware
- [x] Promotion record emitted to a registry for at least one promoted candidate — Model Registry row for `act-v2-ft160-rhem` (D090, `docs/eval-records/model-registry.md`)

---

## Phase 4.5 — RHEM Device Plane

**Goal:** the model plane runs on RHEM (flightctl 1.3) the way the product intends — hub under
GitOps, a real managed device, Fleet-delivered runtime + ModelCar as an image volume, rollout
policy, device health, a registry-backed promotion record, and node-side signature + Rekor
enforcement — and the fidelity drift inventoried in D026 is repaired on the way. Decisions:
D024 (topology), D025 (act-serving retired), D026 (drift), D027 (Catalog seam), D028 (Tekton).
On the desktop a RHEL 10 KVM VM stands in for the device and runs ACT on CPU; the sim, camera
bridge and host runner stay on the host GPU. On the Fury the RHEL 10.2 host *is* the device.

**Sequencing (Sept 8 → Fury window Sept 20–25).** The contingency kit is recorded **once, on the
RHEM path** (operator, 2026-09-08): no recording of the pre-RHEM version. Until Phase 4.5-D is
green, the fallback is the Phase 3 durable artifacts already in `docs/demo-kit/`.

| Days | Work | Why |
|---|---|---|
| 0–2 | **A** hub (incl. SNO upgrade) ∥ **B** device VM + CPU spike | Neither touches the running host `act-inference` |
| 2–4 | **C** Fleet app; cut over host → VM | First point the loop changes |
| 4–5 | **D** promotion rewrite; one promotion end to end | Proves Beat 5/6 on RHEM |
| 5–7 | **F** Tekton multi-arch runtime image; **E1** Model Registry | arm64 runtime image is a Fury prerequisite; closes Phase 4 item 5 |
| 7–9 | **E2** Catalog (stretch); runbook rewrite; **Phase 4 item 2: record the contingency kit**; rehearsal | |

**G-prep done 2026-09-09 (D097+):** runbook rewritten for Beats 5/6 on RHEM (Short Cut = run
`192f3ec5` / PR #2; Full Live from today's state; Fury table = device-is-the-host); dashboard badge
sourced from the consumer's `COLLECTOR` (a3cd943); `docs/demo-kit/rhem-kit-script.md` clip list —
operator records; one full rehearsal pending.
| 10–12 | Buffer; **F** ride-alongs (Argo apps, secrets, arch-derived binaries, docs) | |
| Sept 20–25 | **G** Fury | Stand the flywheel up; everything else stretch (Phase 4 sequencing note) |

OTel and AMQ Streams are post-ROSCon deferrals (D026), not part of this phase.

A. **Hub: RHEM 1.3 on SNO under GitOps** (D024)
   - Upgrade SNO 4.17 → 4.18 → 4.19 (two hops, VM snapshot first; `oc get csv -A` for RHOAI /
     RHTAS / Pipelines / GitOps compat before starting). The chart's `kubeVersion >= 1.32` is a
     hard prerequisite; no `helm.kubeVersion` override.
   - `argocd/rhem-app.yaml`: Helm OCI source `quay.io/flightctl/charts`, chart `flightctl` 1.3.0,
     ns `flightctl`, UI on a Route; Argo repo Secret with `enableOCI: "true"` documented in
     `argocd/README.md`.
   - `rhem/bootstrap/{repository,resourcesync}.yaml` (`path: gitops/rhem`), applied once with
     `flightctl apply`; RBAC check with `flightctl login` — if 403, port thor D005 to
     `gitops/rhem-config/rbac.yaml`. `/etc/hosts` entries for the new routes (D006).
   - **Exit (met 2026-09-08):** UI route serves; `flightctl get resourcesync` Synced (Fleet
     `act-inference` rendered VALID); DSPA + `ds-pipeline-dspa` healthy after the upgrade (no run
     submitted — it would open a PR).

B. **Desktop stand-in device: RHEL 10 VM + CPU spike** (D024)
   - `device/provision.sh`, arch-neutral, runs on the VM and the Fury: `subscription-manager`,
     podman ≥ 5.5, flightctl EPEL10 repo, `flightctl-agent-1.3.0*` package mode; Fury only:
     `nvidia-container-toolkit` + `nvidia-ctk cdi generate`. Trust files come from the Fleet, not
     the script.
   - VM: 8 vCPU / 16 GiB / 60 GB, bridged like SNO so it reaches host zenoh `10.0.0.48:7447` and
     the SNO NodePorts. Enroll (thor `DEPLOYMENT_GUIDE.md:160-181`) and approve with labels
     `fleet=act-inference site=desktop gpu=none policy_device=cpu zenoh_router=10.0.0.48 zenoh_port=7447`.
   - CPU spike (1 d, **gates C**): p95 forward latency, `ros2 topic hz` on the commanded-action
     topic, 20-seed D020 eval vs GPU v2. Pass: p95 < 0.5 × (`n_action_steps`/50 s), no gap > 40 ms,
     success within 10 points of 86%. Fallbacks: raise `n_action_steps` → lower RTF → VFIO last.
     Record in `docs/eval-records/cpu-spike.md`.
   - **Exit (met 2026-09-09):** device Online with labels (2026-09-08); spike record: part 1 PASS (p95 83 ms host cgroup / 184 ms in-guest vs 1000 ms), part 2 PASS under the restated percentile criterion (p99 ≤ 40 ms, ≤ 1 % over; measured p99 29.9 ms, 0.34 % over — D096), part 3 PASS 18/20 vs GPU 17/20 (D065) — CPU stand-in accepted.

C. **Fleet-delivered application** (D024, D026)
   - Image: `POLICY_DEVICE` env replaces the hard-coded `policy_device:=cuda`
     (`docker/inference-entrypoint.sh`); new `docker/healthcheck.sh` checks `/flywheel/model_version`
     equals `$MODEL_VERSION` and the action server is up (`--start-period=240s`). Built + signed by
     Tekton (F), referenced by digest. Interim amd64 image built + signed on the host (D045) is
     pinned until F's Tekton build replaces it.
   - `gitops/rhem/fleet-act-inference.yaml`: selector `fleet=act-inference`; BatchSequence
     `[site=desktop, site=fury]`, `successThreshold: 100%`, `defaultUpdateTimeout: 30m`; inline
     config writes `policy.json` (sigstoreSigned, `keyPath` + `rekorPublicKeyPath`),
     `registries.d`, `cosign.pub`, `rekor.pub`, and `/etc/act-inference/env` templated from labels;
     one quadlet app `act-inference` with the modelcar as an image volume (`reclaimPolicy: Retain`),
     `Network=host`, GPU line templated on the `gpu` label, `HealthCmd`. Rootful for the demo.
   - Cut-over: `docker stop act-inference` on the host; bag-watchdog semantics move to
     `/var/lib/act-inference`.
   - Desktop only: bags land on the host over virtiofs (D043) so `assemble_all.sh`/`prune_bags.py`
     keep working; port-as-you-go prune after each assembly.
   - **Superseded (C3, D057+):** the device runs the policy role only; coordinator + recorder + sim
     reset run beside the sim on the host (`tools/host/run-coordinator.sh`), bags stay on the host
     disk, and `tools/host/disk-guard.sh` must be armed before the loop runs.
   - **Exit (met 2026-09-09 except the last):** `applicationsSummary: Healthy` (device policy role, renderedVersion 4, signed image Rekor index 3); curator receives `act-v2-ft160` episodes from the VM with real verdicts and non-null `dataset_path` (curated 194→214, rejected 58→104 on 2026-09-08 23:54–00:44Z); unsigned tag fails to pull (C-prep); signed-without-tlog also fails (D091); `--insecure-ignore-tlog` removal → item D.

D. **Promotion path rewrite** (D025)
   - `open_promotion_pr` becomes a two-regex edit of the Fleet (digest + `MODEL_VERSION`); the same
     commit bumps `COLLECTOR`/`INCUMBENT` in `gitops/flywheel/manifest-consumer.yaml`; PR body adds
     the rollback command and the Fleet URL.
   - **D1 done 2026-09-09:** `open_promotion_pr` rewritten (D066+), pipeline `v-202609090650-rhem`,
     PR #2 open with the two-file diff, modelcar signed (Rekor index 4) and pulled on the device
     under `policy.json`; merge is the operator's (Gate 3).
   - **D2 done 2026-09-09:** merge of PR #2 → ResourceSync (+1:34) → device rv5 →
     `Published model_version: act-v2-ft160-rhem` (+2:10) → Healthy (+2:42) → Argo auto-synced the
     consumer (+3:11) → first re-stamped curated episode (+7:26); no modelcar re-pull (Retain);
     rollback PR #3 open for the operator (D069–D073).
   - **D3 done 2026-09-09:** PR #3 (revert) merged → device back on `act-v2-ft160` at +1:28, Healthy
     +1:59, no re-pull; `gitops/act-serving/`, `argocd/act-serving-app.yaml`, `src/swap-agent/` and
     the Argo app retired (D025 executed); runbook procedures no longer bypass the transparency log
     (D074+).
   - Retire `gitops/act-serving/`, `argocd/act-serving-app.yaml`, `src/swap-agent/` after the first
     RHEM promotion. Demo screens: RHEM UI rollout + device Applications tab; `flightctl get
     fleet/device`; `flightctl console` tailing `podman logs` for `Published model_version:`.
   - **Exit (met 2026-09-09):** PR #2 merged → VM serving the new version with no human on the device
     (+2:10); rollback (`git revert -m 1`, PR #3) rehearsed: previous version serving at +1:28, no
     re-pull (Retain).

E. **Model Registry (E1) + Catalog (E2, stretch)** (D027)
   - E1: `modelregistry` Managed in `gitops/operators-config/dsc.yaml`; `ModelRegistry` CR + MariaDB
     in `gitops/operators-config/model-registry.yaml`; KFP `register_model` between sign and PR
     carrying digest, dataset URI, eval numbers, Rekor index, PR URL. Closes Phase 4 item 5.
   - **E1 done 2026-09-09 (D081–D090):** `ModelRegistry/flywheel` under Argo; pipeline registers each
     candidate before the PR and fills `pr_url` after (idempotent on version name); run `9015ecd4` →
     registry shows `soarm-act` / `act-v2-ft160-rhem` with digest `18cc4412…`, eval metrics, Rekor
     index 8, PR #4. Closes Phase 4 item 5.
   - E2: `rhem/bootstrap/catalog.yaml` + `gitops/rhem/catalogitem-soarm-act.yaml`; pipeline
     `append_catalog_version(...)` as a removable seam; Fleet pins `catalogItemRef.version`.
   - **E2 done 2026-09-09 (D092+):** `Catalog/physical-ai-models` (v1alpha1) + `ResourceSync/rhem-catalog`
     (type catalog, `gitops/rhem-catalog/`) + `CatalogItem soarm-act` with digest-form references (flag
     closed) and SemVer-mapped versions; `append_catalog_version` seam in the pipeline
     (`v-202609091125-catalog`), no run. Version graph matches the Fleet's pin.
   - **Exit:** the registry shows the promoted version with digest + metrics for at least one
     candidate; if E2 lands, the CatalogItem version graph matches the Fleet's pin.

F. **Fidelity repairs, pre-Fury** (D026, D028)
   - Tekton: `gitops/tekton/{buildah-cross-arch-task,cosign-sign-task,runtime-image-pipeline,
     qemu-binfmt}.yaml`; `pipeline` SA on the privileged SCC (thor D009); multi-arch manifest
     signed with `--tlog-upload=true`. Fallback: native `podman build` on the Fury.
   - KFP hygiene: `platform.machine()`-derived crane/cosign URLs; pinned `ubi9/python-312` and
     `ubi-micro`; `platform` as a list.
   - GitOps completeness: commit `argocd/{flywheel,minio,observability}-app.yaml`; `prune: true`;
     MinIO root creds out of `gitops/flywheel/hub-credentials.yaml`; `gitops/operators/README.md`
     (RHEM + Model Registry rows, Tekton/KServe claims) and `PROJECT-BRIEF.md` "AMQ Streams" fixed.
   - **F-GitOps done 2026-09-09 (D077+):** `argocd/{flywheel,minio,observability}-app.yaml`
     committed; `prune: true` on all 8 apps (8 apps = 8 files); MinIO root creds out of git
     (hand-created Secrets, documented); KFP `platform` list → OCI index + `cosign sign
     --recursive`; `COSIGN_PASSWORD` from the `cosign-signing-key` Secret; operators README (RHEM,
     Model Registry rows, KServe unused) and PROJECT-BRIEF AMQ Streams wording fixed.
   - **F-Tekton done 2026-09-09 (D101+):** `gitops/tekton/` under Argo app `tekton`; PipelineRun
     `runtime-image-a4` built both arches (arm64 53 min under qemu, amd64 6 min), manifest list
     `3d67f424…` signed `--recursive` (Rekor 11/12/13), verified on exit code, Fleet re-pinned
     (9e982c0), device rv7 Healthy.
   - **Exit (met 2026-09-09):** `tkn pipelinerun` builds both arches with a Rekor entry;
     `argocd app list` count equals files in `argocd/`, every app Synced with `prune: true`; no
     `minioadmin` in git.

G. **Fury port + runbook** (on site, Sept 20–25)
   - `device/provision.sh` on aarch64 + `nvidia-ctk cdi generate`; enroll with Fury labels
     (`site=fury gpu=nvidia arch=arm64 policy_device=cuda`); sim + camera bridge as host podman
     containers from the arm64 images; fresh SNO 4.19+ with `argocd/*-app.yaml` +
     `rhem/bootstrap/*` applied by hand. Device pulls from quay.io; no mirror.
   - `docs/DEMO_RUNBOOK.md`: Beat 5 = PR + Rekor + RHEM Fleet rollout; Beat 6 = device Applications
     tab + `flightctl console`; replace the "On GB10/GB300 Fury" table and the "Argo Degraded"
     known-artifact line. Re-record the Beat 5/6 clips on the desktop **before travel**.
   - **Exit:** the flywheel stands on the Fury with the host enrolled and the Fleet Healthy.

### Flags to verify (first apply)
- ~~podman ≥ 5.5 on RHEL 10.2 (image volumes)~~ **Verified 2026-09-08:** RHEL 10.2 AppStream ships podman 5.8.2 (D035)
- quadlet `.container` referencing an app-level image volume by name — **documented** in flightctl 1.3.0 `managing-devices.md` (`Volume=my-data:/mnt/models/gpt2`); still to confirm on the VM. **New caution:** the docs describe app-level image volumes as OCI *artifacts* whose layers are copied out as files by `org.opencontainers.image.title`; our modelcar is a container image on a `ubi-micro` base. If artifact semantics mangle it, fall back to a quadlet `.volume` with `Driver=image` (loses `catalogItemRef`, keeps the digest pin) or repackage the modelcar as an artifact
- ~~Go-template `if` inside inline config content~~ **Verified 2026-09-08 (docs):** `if`/`else`/`else if`/`with` supported, `range` not; placeholders allowed in inline config content/path, inline application content/path, env var values, and application-volume image *tag* only (we pin digests, so no templating there)
- ~~CatalogItem `references` in digest form — **docs say "tag or digest"** (v1alpha1, `managing-catalogs.md`); confirm on first apply (E2)~~ **Verified 2026-09-09:** digest form accepted verbatim; versions must be SemVer (D092+)
- ~~`torch==2.9.1+cu130` aarch64 wheels (Phase 4 open question) — unverified~~ **Verified
  2026-09-09:** aarch64 wheels exist; only torch carries `+cu130` on arm64 → per-arch pins (D101+)
- ~~RHOAI on SNO ships the `modelregistry` component~~ **Verified 2026-09-08:** RHOAI 2.25.11 DSC lists `modelregistry` (currently `Removed`)
- **New (B):** label values may not contain `:` (k8s `IsValidLabelValue` in flightctl 1.3.0) → labels are `zenoh_router=10.0.0.48` + `zenoh_port=7447` (D033); `flightctl-agent-1.3.0-1.el10` no longer requires greenboot (only Recommends `flightctl-greenboot`)
- ~~**New (C0b):** `virtiofsd` must be installed from the distro package on the desktop (AppArmor pins the path) — D046; bag recording on the VM is off until then (D044)~~ — no longer needed after the C3 role split

### Exit criteria
- [x] `flightctl get devices` shows the desktop VM Online, labels correct, `applicationsSummary: Healthy` (2026-09-08 22:52Z, device s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg)
- [x] Sim loop running against the VM: curator stamps episodes with the Fleet's `MODEL_VERSION`,
  `episodes-curated/<mv>/` fills, `manifest-consumer` count advances (2026-09-09: curator stamps `act-v2-ft160`, `episodes-curated/act-v2-ft160/` 194→214; manifest-consumer count to be confirmed in D)
- [x] A DSP run on a pre-trained candidate (D023 path) opens a PR editing the Fleet (+ CatalogItem
  if E2); Model Registry shows the version with digest + metrics (met 2026-09-09, E1); merge →
  ResourceSync Synced → RHEM rollout completes → VM container restarts with the new
  `Published model_version:` → episodes re-stamp (2026-09-09: PR #2, Rekor index 4; Model Registry
  part → E1)
- [x] Negative test: an unsigned tag fails with a signature error; a tag signed *without*
  `--tlog-upload` also fails on the VM (Rekor SET enforced) (2026-09-09, D091, docs/eval-records/negative-trust-tests.md)
- [x] Rollback: `git revert`, merge → previous version serving, no re-pull (Retain) (2026-09-09, PR #3)
- [x] Tekton: runtime image built for both arches, Rekor entry created, `crane manifest` shows both
  platforms, the Fleet references its digest (2026-09-09, D101+, docs/eval-records/runtime-image-tekton.md)
- [x] `grep -r insecure-ignore-tlog` returns nothing; `argocd app list` count equals files in
  `argocd/`; every Argo app Synced with `prune: true` — tlog bypass gone from code/config/procedures
  (D3); Argo app count/prune → F-GitOps (2026-09-09: tlog bypass gone from code/config/procedures —
  D3; 8 Argo apps = 8 files, all Synced with prune: true — F-GitOps)
- [ ] Contingency kit recorded on the RHEM path (Phase 4 item 2); one full rehearsal of the Full Live cut on RHEM — kit script ready, recording + rehearsal pending (operator)

### Carry-overs (as of 2026-09-09 — every item mirrored in brim's inbox)

Closed 2026-09-09 by the operator: PR #4 closed unmerged; `cosign.password` patched; auto-delete head branches enabled and stale branches removed; 54 lingering KFP pods removed.

Closed 2026-09-09 (D113): lineage-aware prune, Fleet-following coordinator default, consumer
`pending=` line, eval-gate's `goal_accepted` blindness (the `aggregate.success_rate` half only —
`healthcheck.sh`'s wedged-server detection stays open, see row below).

| Item | Owner | Status |
|---|---|---|
| sdb1 4.5 TB partition mount + relocate flywheel-data | operator | deferred — loop is demo-scoped, disk guard + port-as-you-go bound the bags |
| DSP API accepted an empty bearer token via port-forward to the service port — confirm the route enforces OAuth / consider a NetworkPolicy | operator | **closed (D114)** — Route confirmed 403 on empty token; the raw 8888 backend is genuinely unauthenticated but a NetworkPolicy can't gate `oc port-forward` (bypasses the SDN); RBAC already restricts `pods/portforward` in `flywheel` to cluster-admin only (verified) — accepted as bounded |
| Multus stale-token fault after the 4.19 upgrade (new pods failed `Unauthorized` until the multus pod was recreated) — watch for recurrence | operator | planned |
| Perses/Tempo come from hand-installed COO 1.5.2 + tempo-operator, not `gitops/operators/` — add Subscriptions or record as deferral | — | deferred (D113) — `argocd/README.md` row 6 already documents the gap; no Subscription manifests against unverified operator versions this pass |
| runbook runner-restart line must `set -a; source ~/.minio-env; set +a` | — | **done (3095ced)** |
| dashboard `model_version` badge stuck at `soarm-act-v1` after act-serving retired — re-source from `/flywheel/model_version` | — | **done (a3cd943)** |
| `prune_bags.py` lineage-aware scan across all `episodes-curated/<mv>/` prefixes and manifests | — | **done (D113)** — host copy synced |
| `run-coordinator.sh` default `MODEL_VERSION` must follow the Fleet | — | **done (D113)** — host copy synced |
| `consumer.py` per-manifest `pending=<n>` log line | — | **done (D113)** — lands on next Argo sync |
| `healthcheck.sh` cannot see a wedged action server | F/G | planned — needs a liveness-probe or heartbeat design, not a same-pass fix (D113) |
| Deploy the review's source fixes (C3/C10/C12 sim image; C11/C12 runtime image) | — | **done (D129–D130)** — sim image rebuilt and swapped; runtime rebuilt in one clean Tekton run, re-pinned, device rv8 Healthy; 10-min validation loop clean |
| Port + prune bags before the next longer loop (319 bags, 110 G free; guard parks at 330 / 100 G) | operator | planned |
| `aggregate.success_rate` should exclude `goal_accepted: false` episodes | F/G | **done (D113)** — not live until the next Tekton runtime-image build + sign + Fleet re-pin (`coordinator.py` is baked into the image) |
| OTel re-emission and AMQ Streams | — | post-ROSCon deferrals (D026) |
| Fury on site: `device/provision.sh` on aarch64 + CDI, enroll with Fury labels, fresh SNO 4.19+ with `argocd/*-app.yaml` + `rhem/bootstrap/*` | G | Sept 20–25 |
| Presenting laptop `/etc/hosts`: add `ui.flightctl…` and `flywheel-rest…` | operator, sudo | **done (2026-09-09)** — verified resolving via `ping`/`dscacheutil` |

---

## Open questions

| Question | Phase | Status / notes |
|---|---|---|
| Desktop IOMMU / VFIO for the 5090 | 0 | Superseded — host GPU split (D013) |
| SNO VM allocation vs host headroom | 0 | Resolved (D003) |
| Which OSD-hub manifests rework vs drop | 1 | Resolved (D007) |
| SO-ARM episode data shape / success signal | 2 | Resolved — cube poses from `pose/info` (D016) |
| Gazebo streaming approach | 2 | Resolved — host MJPEG camera bridge |
| zenoh middleware across the VM boundary | 2 | Resolved — client mode to in-sim router (D013) |
| `pai_data_collection` trigger interface — can it start/stop on our `episode_control` signals? | 2.5 | Resolved — it's a *contract*, not a recorder; `rosetta episode_recorder_node` records via a `RecordEpisode` action, `port_bags` → LeRobot (D018) |
| LeRobot v2 shard layout and per-episode storage volume in MinIO | 2.5 | Resolved — hub stores the ported LeRobot dataset as one tarball (~4.5 MB/ep), not raw bags; raw bags stay on host (D019) |
| Retain frames for rejected episodes, or metadata only? | 2.5 | Resolved — metadata only: the coordinator prunes a rollout's bag at episode end unless it reached 3/3 (curated); rejected episodes keep their JSON, not their frames (D018, prune commit) |
| Model-plane delivery via RHEM Fleet/Catalog instead of an Argo selector flip? | 4.5 | **In progress — Phase 4.5 (D024).** thor-testing used RHEM for enrollment + OS plane only (no Fleet CR or CatalogItem was ever written; model + runtime went Argo → MicroShift via ACM cluster-proxy). RHEM engineering (Assaf, 2026-09-08) has a Fleet → runtime + OCI ModelCar → edge VM flow proven and is bridging RHOAI registry metadata into the catalog (D027). Now in ROSCon scope: Beats 5/6 run on RHEM, desktop VM as the stand-in device. |
| Model deltas | 4 / post-ROSCon | Open — post-ROSCon. Modelcar is a single `crane append` layer; every promotion is a full-layer pull. Irrelevant at ACT checkpoint sizes, matters for Cosmos-class artifacts (thor-testing D029: 20.6 GB layer, ~10 min). Base-weights + adapter layering would give OCI-level dedup. |
| Dataset assembler: `lerobot-train` local-root vs. a synthetic `repo_id` | 2.5 | Resolved — local root via `port_bags --root`; `lerobot-train --dataset.root=<dir>` (D018) |
| Eval-gate threshold (success-rate delta) for promotion | 3 | Resolved — paired on fixed seeds, N=100: promote iff net fixed−broken > 0 and sign-test p < 0.05 (D022) |
| Expert grasp planning: MoveIt vs. direct IK for the SO-ARM gripper | 3+ | Open |
| Curriculum schedule — what signal widens randomization | 3+ | Open |
| aarch64 build of the cu130 PyTorch inference image | 4 | Resolved 2026-09-09 (Phase 4.5 F, D101+) |
| CPU ACT inference latency in the desktop device VM | 4.5 | Open — spike in B gates C (D024); fallbacks: raise `n_action_steps`, lower RTF, VFIO last |
