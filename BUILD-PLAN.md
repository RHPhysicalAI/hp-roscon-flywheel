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
| 3 — Training + close the loop | **In progress** — eval harness (D020) + self-improvement proof (D021: 160-success fine-tune 86% vs 74%) done; governed pipeline / blue-green / static chart next | D015, D020, D021 |
| 3+ — Bootstrap loop | Not started | `BOOTSTRAP-LOOP.md` |
| 4 — Demo hardening + Fury prep | Not started | — |

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
- [ ] Curated training corpus exists in MinIO, produced entirely through the flywheel
- [ ] Training pipeline runs end-to-end from MinIO data
- [ ] v2 policy signed, promoted via GitOps, blue/green swapped
- [x] v1 vs v2 improvement demonstrable on the fixed eval set — D021 round 2: fine-tuning the teacher on 160 of its own curated successes → **86% vs 74%** on 50 identical seeds (8 fixed / 2 broken); 20/40 *degrade* it, which is the eval-gate's justification. N=100 confirmation in progress.
- [ ] Full loop closes (sim -> record -> curate -> train -> sign -> promote -> sim)

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

1. **Adapt the demo runbook** — re-skin the 6-beat narrative for SO-ARM:
   - Beat 1: "Here's the sim — SO-ARM placing cubes, running a trained policy"
   - Beat 2: "The curator is watching — this is the curation stream"
   - Beat 3: "Training started from the curated data — here's the pipeline"
   - Beat 4: "Model improvement — v1 vs v2 side-by-side"
   - Beat 5: "Promotion — signed, GitOps PR, blue/green swap"
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

2. **Record a fallback run** — clean end-to-end captured on the desktop for venue-link /
   Fury-slip insurance. Non-negotiable.

3. **Multi-arch image prep:** every custom image builds for `linux/amd64` and `linux/arm64`.
   Document every x86-specific assumption. The GPU inference image (PyTorch cu130) on aarch64
   Blackwell is the highest-risk item. The eval dashboard is a plain-Python container and should
   be trivially multi-arch — or it runs on a laptop at the booth; either is acceptable.

4. **Fury porting checklist:**
   - Container stack builds/runs on aarch64 Blackwell
   - Full flywheel loop at small scale
   - SO-ARM sim + camera stream on aarch64
   - ACT policy serves on aarch64
   - All x86 assumptions resolved

### Exit criteria
- [ ] Demo-ready on desktop with runbook
- [ ] Fallback recording captured
- [ ] arm64 images build
- [ ] Fury porting checklist written

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
| Dataset assembler: `lerobot-train` local-root vs. a synthetic `repo_id` | 2.5 | Resolved — local root via `port_bags --root`; `lerobot-train --dataset.root=<dir>` (D018) |
| Eval-gate threshold (success-rate delta) for promotion | 3 | Open |
| Expert grasp planning: MoveIt vs. direct IK for the SO-ARM gripper | 3+ | Open |
| Curriculum schedule — what signal widens randomization | 3+ | Open |
| aarch64 build of the cu130 PyTorch inference image | 4 | Open |
