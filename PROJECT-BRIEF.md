# HP ROSCon Flywheel — Physical AI Edge Flywheel on the GB300

## What this project is

A demo for **HP's booth at ROSCon 2026** running on a single **HP ZGX Fury (GB300)** machine.
One box runs the complete Physical AI lifecycle: simulate a robot arm doing a task, curate the
good data, retrain a better policy, sign it, promote it through GitOps, swap it in live — the
arm gets visibly better. Loop closes.

**Red Hat's part:** the governed lifecycle (signing, GitOps, trust chain, OpenShift platform).
**HP's part:** the hardware. **NVIDIA's part:** the model. **ROS community's part:** the sim.

## Origin

This project reuses ~70% of the `thor-testing` Physical AI Edge Flywheel (Jeremy Ary's prior
project, demo-ready as of 2026-08-19). The adaptation: collapse the two-tier topology
(Thor edge device + OSD-on-AWS hub) onto a single GB300 running SNO, and swap the data producer
from a Cosmos3 world-model generator to the ROS community's **SO-ARM101** robot arm in Gazebo.

The plumbing (curator, sync-agent, Kafka, Tekton build+sign, blue/green promotion, dashboard,
observability) is reused. The producer and training step are new.

## Architecture

```
[SO-ARM101 in Gazebo]  ← runs ACT policy, generates rollout episodes
        |
        v
/data/episodes/raw/*.json
        |
[curator]              ← scores quality (task success, action smoothness)
        |         \
   curated/      rejected/
        |
[sync-agent]           ← uploads to MinIO + publishes manifest to Kafka
        |
[KFP training pipeline] ← LeRobot ACT fine-tune on curated data
        |
eval → package modelcar (crane append) → cosign sign (RHTAS) → promotion PR
        |
[Argo CD blue/green]   ← merges, flips service selector, new policy goes live
        |
sim picks up v2 policy → better rollouts → loop closes
```

### What runs on the single box (SNO)

- **Hub/platform plane:** Single-Node OpenShift hosting Argo CD, MinIO, Kafka (AMQ Streams),
  RHTAS/sigstore trust plane, KServe, Perses/Tempo observability, dashboard
- **Device plane (simulated):** SO-ARM101 in Gazebo, serving an ACT policy, generating episodes
- **Data plane (the flywheel):** sim → curator → sync-agent → Kafka → training → sign → promote
- **Model plane:** ACT policies packaged as signed KServe modelcar OCI images, blue/green swapped

## Key repos

| Repo | Role |
|---|---|
| **This repo** (`hp-roscon-flywheel`) | The single-box flywheel build |
| `~/redhat/git/thor-testing` | Source of reusable plumbing (gitops/, tekton/, pipeline/) |
| `github.com/ros-physical-ai/demos` | Upstream SO-ARM101 sim, LeRobot, Rosetta, pre-trained ACT policy |
| `github.com/RHPhysicalAI/gz-camera-stream` | Gazebo camera streaming plugin (C++) |
| `github.com/RHPhysicalAI/rhork` | Web viewer for Gazebo streams + Helm charts |

## Hardware

**Target:** HP ZGX Fury (GB300) — Grace Blackwell, 748 GB unified memory, RHEL 10.2.
Remote SSH access targeted Sept 20-25 (Rick).

**Development stand-in:** Ubuntu desktop — i9-13900K, RTX 5090, 128 GB RAM. x86_64, not
aarch64. Answers the topology/contention question (does the whole flywheel collapse onto one GPU
node?) but not architecture-specific questions (those are Fury-phase).

**Nano (GB10):** Temporary testing box only. Stock Ubuntu. Not NVIDIA-blessed for RHEL. Used for
aarch64 container correctness testing once Docker access lands (Carlos). Not part of the demo.

## Constraints

- **Reuse thor-testing, don't rebuild** (Kelly's directive)
- **Single machine, no cloud backend** — everything self-contained on the Fury
- **HP wants limited technical scope** — first ROSCon, tight and reliable, ~5-min booth demo
- **Honest framing** — device is simulated; pipeline is exactly what runs to real fleets
- **HP scope stops at ROSCon** — GTC Berlin is separate (Staer)
- **ZGX toolkit de-prioritized by Kelly** — do not design around it
- **Thor is not available** as a validation box (Jeremy's property, running xlerobot)

## Jira

- Epic: APPENG-6058 (HP GB300 / RHEL AI Partnership)
- Olga's task: APPENG-6261 (SO-ARM sim + Gazebo streaming evaluation)

## Key people

- **Jeremy Ary** — project lead, built thor-testing
- **Kelly Switt** — decision-maker for demo direction
- **Leonardo Rossetti** — Fedora SIG, ROS community liaison
- **Sayan Paul** — platform build vision, LeRobot/sim integration
- **Olga Lavtar** — SO-ARM sim + streaming evaluation
- **Rick Gosalvez (HP)** — Fury access coordination
- **Manny (HP)** — on-site at ROSCon booth

## Planning artifacts

Detailed planning docs live in `~/.config/opencode/brim/.plans/hp-gb300-roscon-demo-research/`:
- `concept-flywheel-on-fury.md` — authoritative concept doc (post-pivot, 2026-08-26)
- `desktop-fury-buildout-plan.md` — phased build plan (this project's execution plan)
- `07-thor-testing-jumpstart.md` — what reuses from thor-testing
- `olga-task-brief.md` — project context guide for Olga
