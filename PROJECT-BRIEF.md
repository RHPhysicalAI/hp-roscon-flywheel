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
project, demo-ready as of 2026-08-19, `~/redhat/git/thor-testing`). The adaptation: collapse
the two-tier topology (Thor edge device + OSD-on-AWS hub) onto a single GB300 running SNO, and
swap the data producer from a Cosmos3 world-model generator to the ROS community's **SO-ARM101**
robot arm in Gazebo. See `THOR-TESTING-REUSE.md` for the detailed reuse map.

## Architecture

```
[SO-ARM101 in Gazebo]  <- runs ACT policy, generates rollout episodes
        |
        v
/data/episodes/raw/*.json
        |
[curator]              <- scores quality (task success, action smoothness)
        |         \
   curated/      rejected/
        |
[sync-agent]           <- uploads to MinIO + publishes manifest to Kafka
        |
[KFP training pipeline] <- LeRobot ACT fine-tune on curated data
        |
eval -> package modelcar (crane append) -> cosign sign (RHTAS) -> promotion PR
        |
[Argo CD blue/green]   <- merges, flips service selector, new policy goes live
        |
sim picks up v2 policy -> better rollouts -> loop closes
```

### What runs on the single box (SNO)

- **Hub/platform plane:** Single-Node OpenShift hosting Argo CD, MinIO, Kafka (AMQ Streams),
  RHTAS/sigstore trust plane, KServe, Perses/Tempo observability, dashboard.
- **Device plane (simulated):** SO-ARM101 in Gazebo, serving an ACT policy, generating episodes.
- **Data plane (the flywheel):** sim -> curator -> sync-agent -> Kafka -> training -> sign -> promote.
- **Model plane:** ACT policies packaged as signed KServe modelcar OCI images, blue/green swapped.

## The simulated robot: SO-ARM101

The sim is based on the ROS community's own upstream Physical AI examples at
`github.com/ros-physical-ai/demos` (org-level, Apache-2.0). Building on this rather than
creating our own is a deliberate credibility move for a ROSCon audience — Red Hat adds the layer
the repo lacks (signed artifacts, fleet delivery, path-to-scale).

What the upstream provides:
- **SO-ARM101** robot arm on ROS 2 doing a "place cubes on tray" task
- Gazebo and MuJoCo simulation environments
- Full **Record -> Train -> Deploy** pipeline via **LeRobot** + **Rosetta** (ROS 2 <-> LeRobot bridge)
- **60 pre-recorded rosbags + a trained ACT policy** — run a full rollout in minutes
- **No GPU needed for the sim itself** (GPU only for ML train/infer) — booth-friendly
- Uses **zenoh** (`rmw_zenoh`) as ROS 2 middleware; **Pixi** for env/deps
- Ships an **MCP interface** (AI agent can drive the robot)

### ACT (Action Chunking with Transformers)

The policy architecture used by the SO-ARM upstream:
- **Input:** camera frame + arm's current joint positions
- **Output:** a chunk of N future joint commands (e.g. 16 timesteps ahead)
- "Chunking" = predicting multiple future steps at once, producing smoother motion
- Trained via **LeRobot** (`lerobot.scripts.train`)
- **Rosetta** translates between ROS 2 topics and LeRobot's episode/dataset format

## Hardware

**Target (demo):** HP ZGX Fury (GB300) — Grace Blackwell, 748 GB unified memory, aarch64,
RHEL 10.2. Remote SSH access targeted Sept 20-25 (Rick Gosalvez, HP).

**Development stand-in:** Ubuntu desktop — i9-13900K, RTX 5090, 128 GB RAM, x86_64.
At 10.0.0.41 on Jeremy's local network (SSH as `jary`). Ubuntu stays as the host OS; SNO runs
in a KVM VM with the RTX 5090 passed through via VFIO. This answers the topology/contention
question (does the whole flywheel collapse onto one GPU node?) but not architecture-specific
questions (aarch64/Blackwell issues surface on the Fury, not here).

**Nano (GB10):** Temporary aarch64 testing box only. Stock Ubuntu (RHEL not NVIDIA-blessed on
GB10). Used for container correctness testing once Docker access lands (Carlos, over Tailscale).
Not part of the demo.

**Thor:** Jeremy's personal Jetson AGX Thor dev kit, currently running xlerobot on Ubuntu.
NOT available as a validation box — do not plan around reflashing it.

## Constraints

- **Reuse thor-testing, don't rebuild** (Kelly's directive).
- **Single machine, no cloud backend** — everything self-contained.
- **HP wants limited technical scope** — first ROSCon, tight and reliable, ~5-min booth demo.
- **Honest framing** — device is simulated; pipeline is exactly what runs to real fleets.
- **HP scope stops at ROSCon** — GTC Berlin is a separate effort (Staer).
- **ZGX toolkit de-prioritized by Kelly** — do not design around it.
- **Desktop is x86_64; Fury is aarch64** — build multi-arch from the start.

## Out of scope until Fury phase (Sept 20-25)

- Cosmos 3 at size (Super tier / 1T-param claim) — needs GB300's 748 GB.
- Real training throughput numbers — needs GB300 GPU.
- MIG concurrency view — GB300-only capability; desktop GPU has no MIG.
- RHOAI NVFP4 coding-assistant flex beat — Fury flex, not core commitment.
- Large world model alongside sim+train — Fury flex, not core commitment.
- aarch64-specific NVIDIA driver / CUDA path issues — surface on Fury by definition.

## Key people

- **Jeremy Ary** — project lead, built thor-testing
- **Kelly Switt** — decision-maker for demo direction
- **Leonardo Rossetti** — Fedora SIG, ROS community liaison
- **Sayan Paul** — platform build vision, LeRobot/sim integration
- **Olga Lavtar** — SO-ARM sim + streaming evaluation (APPENG-6261)
- **Rick Gosalvez (HP)** — Fury access coordination
- **Manny (HP)** — on-site at ROSCon booth

## Jira

- Epic: APPENG-6058 (HP GB300 / RHEL AI Partnership)
- Olga's task: APPENG-6261 (SO-ARM sim + Gazebo streaming evaluation)
