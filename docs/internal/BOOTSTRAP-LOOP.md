# Bootstrap Loop — a *real* self-improving flywheel for SO-ARM (Phase 3+ option)

Optional upgrade path beyond the staged v1→v2 proof in `BUILD-PLAN.md` Phase 3.
Rationale and the ACT-vs-RL decision that frames this: `DECISIONS.md` **D015**.

## The principle

Bootstrapping only works if the training data contains **capability the current policy lacks**.
That capability enters from exactly one of three places:

| Engine | New capability comes from | Practicality here |
|---|---|---|
| **Privileged expert** | A controller that knows more than the policy (ground-truth state) | **Best fit** — assets already exist |
| **Human correction (DAgger)** | A person teleoperating out of failures | Credible, but labor per round |
| **RL fine-tune on a BC prior** | Reward-driven exploration | Deepest rabbit hole; post-ROSCon |

If the answer is "the policy's own successes," it is **not** bootstrapping — a policy's successes are
already inside its competence, so retraining on them reinforces what it can already do and plateaus.
That is the honest limit of the staged demo.

---

## The privileged-expert loop (recommended if we go real)

A teacher with **perfect state** solves randomized scenes and generates demonstrations; the
deployable policy learns to do the same thing **from cameras only**. The flywheel manufactures its
own ever-harder training data with no human in the round.

```
randomize scene ─► privileged expert solves it ─► record episode (frames+actions)
      ▲                  (ground-truth poses + IK/MoveIt)             │
      │                                                              ▼
   curriculum ◄── eval gate ◄── train ACT ◄── curated LeRobot dataset ◄── curator
   (widen)        (promote                    (grows each round)        (quality gate)
                   if better)
```

1. **Randomize** the scene — `src/sim-reset/sim_reset.py` already does cube position + yaw
   (`RANDOM_RADIUS`, `RANDOM_YAW_DEG`, `RANDOMIZE_ONLY`). Extend toward textures/lighting for
   vision robustness.
2. **Privileged expert solves it.** It reads exact cube poses from `/world/pai_world/pose/info`
   (the topic the fixed scorer uses — see D016) and plans a pick-and-place per cube. It succeeds
   because it has perfect state; the learned policy never gets that state.
3. **Record the episode** as frames + actions (see the gap below — this is the missing piece).
4. **Curator** filters for success + smoothness — already built, and the success signal is now
   trustworthy (D016).
5. **Train ACT** on the accumulated curated dataset (`lerobot-train`, as in D014).
6. **Eval gate** on a held-out randomized scene set; promote only if success rate improves.
7. **Curriculum**: widen randomization as the policy improves. The expert still solves it (it has
   ground truth), producing fresh *frontier* data. Loop.

## Component mapping — what already exists

| Need | Asset | Status |
|---|---|---|
| Ground-truth cube poses | `/world/pai_world/pose/info` | ✅ working (D016) |
| Arm motion planning / IK | `so_arm100_moveit_config`, `pai_teleop_ik` | ✅ in upstream `ws_pai` |
| Scene randomization | `src/sim-reset/sim_reset.py` | ✅ ours |
| Episode recording (LeRobot format) | Rosetta `episode_recorder` → per-episode MCAP bag → `port_bags` (Phase 2.5, D018) | ✅ wired — every rollout is recorded and curated bags port to LeRobot v2 |
| Dataset assembly from the hub | `src/dataset-assembler/assemble_dataset.py --from-minio` (D019) | ✅ ours |
| Quality gate | `curator` (+ HTTP receiver) | ✅ ours |
| Success metric | `src/episode-emitter/task_eval.py` | ✅ fixed today (D016) |
| Training | `lerobot-train` in `act-inference:latest` | ✅ proven |
| Episode lifecycle / phasing | `src/inference-coordinator/coordinator.py` | ✅ ours (now with early-stop) |
| Human-correction alternative | `pai_leader_teleop`, `pai_phone_teleop`, `feetech_ros2_driver` | ✅ available if we prefer DAgger |

## The gap that used to block any real loop — closed

When this was written the flywheel curated *metadata*, not *trainable data*, and all training used
the upstream HuggingFace dataset. That is no longer true: Phase 2.5 (D017–D019) records every
rollout as an MCAP bag, ports curated bags to LeRobot v2, ships them to MinIO with a Kafka
manifest, and assembles training sets from the hub; Phase 3 (D020–D022) fine-tuned v2 on 160
flywheel-captured successes and promoted it through the governed pipeline. The data path a real
bootstrap needs exists and is exercised.

What remains is the bootstrap itself:

1. **The privileged expert** — a scripted pick-and-place that reads exact cube poses from
   `/world/pai_world/pose/info` and plans per cube with `so_arm100_moveit_config` / `pai_teleop_ik`.
   This is the bulk of the work and the only genuinely new robotics code.
2. **A held-out randomized eval set** — the D020 harness with a seed range never used for training
   data, so promotion measures generalization rather than memorization of the expert's scenes.
3. **A curriculum controller** — widen `RANDOM_RADIUS` / `RANDOM_YAW_DEG` / `RANDOMIZE_ONLY` as the
   held-out success rate rises; the expert keeps solving the wider distribution, producing frontier
   data.

## Honest ceiling and effort

- **Ceiling:** the vision policy chases the privileged planner — improvement is real and measurable
  but bounded by expert quality. That is fine and standard (this is policy distillation).
- **Effort:** the expert script is most of it; recording, curation, assembly, training, the eval
  harness, and randomization already exist.
- **Scope call (operator, 2026-09-09): post-ROSCon.** D015 stands — the staged v1→v2 proves the
  governed pipeline, which is the product story, and the runbook already says "retrained on the
  curated episodes the loop captured," never "keeps improving on its own." Building this before
  the booth would compete with the kit recording and the Fury bring-up on the same sim host for
  no narrative gain. It is the first upgrade after the event, and the honest Q&A answer until
  then: one round of self-improvement is proven; the next step is a privileged expert, designed,
  with every prerequisite built.
