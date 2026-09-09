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
| Episode recording (LeRobot format) | `pai_data_collection` | ⚠️ exists upstream, **not wired into our loop** |
| Quality gate | `curator` (+ HTTP receiver) | ✅ ours |
| Success metric | `src/episode-emitter/task_eval.py` | ✅ fixed today (D016) |
| Training | `lerobot-train` in `act-inference:latest` | ✅ proven |
| Episode lifecycle / phasing | `src/inference-coordinator/coordinator.py` | ✅ ours (now with early-stop) |
| Human-correction alternative | `pai_leader_teleop`, `pai_phone_teleop`, `feetech_ros2_driver` | ✅ available if we prefer DAgger |

## ⚠️ The gap that blocks *any* real loop

**Our flywheel currently curates *metadata*, not *trainable data*.** `episode_emitter.py` emits a
JSON record — `task_success`, `cubes_placed`, `smoothness`, `steps` — and the sync-agent ships that
to MinIO/Kafka. There are **no observation frames or action vectors** in it. All training to date
has used the upstream HuggingFace dataset
(`francocipollone/rospai_sim_arm101_place_cubes_on_tray`), not anything the flywheel produced.

So the loop today is: *sim → score → store score*. To train on flywheel-generated episodes at all —
whether for a real bootstrap **or** for the D015 dataset-size ladder using self-generated data — we
must add **LeRobot-format episode capture** (camera frames + joint actions per timestep) alongside
the scoring path, and have the curator gate *those* datasets into MinIO.

`pai_data_collection` is the upstream package that already records in this format and is the natural
thing to wire in rather than write from scratch.

## Honest ceiling and effort

- **Ceiling:** the vision policy chases the privileged planner — improvement is real and measurable
  but bounded by expert quality. That is fine and standard (this is policy distillation).
- **Effort:** the expert script + LeRobot capture wiring is the bulk of it; the curator, metric,
  training, and randomization already exist.
- **Scope call:** this is **more than the ROSCon demo requires.** D015 stands — the staged v1→v2
  proves the governed pipeline, which is the actual product story. Treat this as the post-deadline
  upgrade that makes the flywheel claim literally true, or as the story we *describe* on stage as
  the natural extension.
