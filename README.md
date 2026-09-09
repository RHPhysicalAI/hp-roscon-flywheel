# Physical AI Edge Flywheel

A governed, self-improving lifecycle for robot manipulation policies — simulate, curate, retrain,
evaluate, sign, promote, and deliver to a managed edge device — running end to end on a single
HP ZGX Fury (GB300) for HP's booth at ROSCon 2026.

> [!NOTE]
> This project was developed with assistance from AI tools.

## Overview

A SO-ARM101 robot arm in simulation runs a learned manipulation policy. Every rollout is recorded
and scored against ground truth. Only successful episodes become training data. When enough of
them accumulate, the platform fine-tunes the policy on its own curated successes, evaluates the
candidate against the incumbent on identical scenes, and refuses to promote unless the improvement
is statistically real. What passes is packaged as an OCI image, signed into a transparency log,
recorded in a model registry, and promoted through a pull request that a human merges. Red Hat
Edge Manager then rolls the new model to the device fleet; each device verifies the signature and
transparency-log entry before it will pull the image. The improved policy is back in simulation,
collecting data for the next round.

The policy and simulation are the ROS community's upstream work. This project adds the layer
around them: the governed path from *"the robot got better"* to *"a fleet of robots got better,
provably, with a human approval gate and a cryptographic audit trail."*

## How the loop works

| Stage | What happens | Where |
|---|---|---|
| 1. Generate | The arm attempts a place-cubes-on-tray task under randomized scenes; each rollout is recorded as a ROS bag | Gazebo + coordinator (sim host) |
| 2. Curate | Task success is scored from the simulator's own object poses, not a vision heuristic; failures are rejected and their frames discarded | Curator |
| 3. Ship | Curated episodes are converted to LeRobot datasets and stored with a manifest on Kafka | Sync agent → MinIO / Kafka |
| 4. Trigger | At 160 new curated successes for the live lineage, a training run starts automatically | Manifest consumer → OpenShift AI pipeline |
| 5. Train & gate | The incumbent policy is fine-tuned on its own curated data, then both are evaluated on 100 identical seeded scenes; promotion requires net improvement with paired sign-test *p* < 0.05 | Pipeline + host runner |
| 6. Package & sign | The checkpoint is packaged as a multi-arch OCI "ModelCar" and signed with cosign, with a Rekor transparency-log entry | Pipeline → Quay, Trusted Artifact Signer |
| 7. Register & promote | A Model Registry record binds digest, dataset, eval metrics, and Rekor index; a pull request edits the Edge Manager Fleet in Git | Pipeline → Model Registry, GitHub |
| 8. Roll out | On merge, Edge Manager renders the Fleet and the device pulls the image — after verifying its signature and log entry | Red Hat Edge Manager → device |

A detailed walk-through, including the one deliberately time-compressed step and how it is
disclosed, is in [`docs/DATA-FLOW.md`](docs/DATA-FLOW.md).

## Results

Measured on the development system; every number links to its evidence record.

- **Self-improvement is real:** fine-tuning the upstream policy on 160 of its own curated successes
  raised task success from **73% to 86%** on 100 fixed seeded scenes (20 scenes fixed, 7 broken,
  paired sign test *p* = 0.019). Fine-tuning on 20 or 40 episodes made the policy *worse* — which
  is why the evaluation gate exists. [`docs/eval-records/phase3-ladder/`](docs/eval-records/phase3-ladder/)
- **Promotion is fast and hands-off:** merge to device serving the new model in ~2 min 10 s, healthy
  at ~2 min 42 s, with no one touching the device. [`docs/eval-records/promotion-2.md`](docs/eval-records/promotion-2.md)
- **Rollback is symmetric:** a `git revert` returns the previous version in ~1 min 30 s with no
  image re-pull; both digests stay in device storage. Same record.
- **Trust is enforced on the device, not just documented:** an unsigned image and an image signed
  with the right key but never logged in Rekor are both rejected at pull time.
  [`docs/eval-records/negative-trust-tests.md`](docs/eval-records/negative-trust-tests.md)
- **Runtime images are built and signed in-cluster for both `linux/amd64` and `linux/arm64`**, with
  a transparency-log entry per manifest. [`docs/eval-records/runtime-image-tekton.md`](docs/eval-records/runtime-image-tekton.md)

## Architecture

Three planes, one box.

- **Hub** — Single-Node OpenShift running Argo CD, Red Hat OpenShift AI (Data Science Pipelines,
  Model Registry), Red Hat Trusted Artifact Signer (cosign + Rekor), Red Hat Edge Manager,
  OpenShift Pipelines, MinIO, and Kafka. Everything on the hub is delivered from Git.
- **Device** — a Red Hat Edge Manager–managed RHEL 10 endpoint running only the served policy: the
  runtime image plus the ModelCar as an image volume, both delivered by the Fleet and verified
  against a pinned signing key and Rekor public key before pull.
- **Simulation** — Gazebo with the SO-ARM101 arm, a camera bridge, and the episode coordinator and
  recorder, colocated with the simulator because scene reset and scoring use Gazebo transport.

| | Development system | HP ZGX Fury |
|---|---|---|
| Hub | SNO in a KVM VM | SNO in a KVM VM on the Fury |
| Device | RHEL 10 VM, policy on CPU | The Fury host itself, policy on the GB300 GPU |
| Simulation | Host containers (x86_64 GPU) | Host containers (aarch64) |

Component diagram and per-component detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Exact images, chart versions, and ports: [`docs/BILL-OF-MATERIALS.md`](docs/BILL-OF-MATERIALS.md).

## Platform

| Component | Role | Version |
|---|---|---|
| Red Hat OpenShift (Single Node) | Hub platform | 4.19 |
| OpenShift GitOps (Argo CD) | Delivers every hub component from Git | — |
| Red Hat OpenShift AI | Training pipeline (Data Science Pipelines) and Model Registry | 2.25 |
| Red Hat Trusted Artifact Signer | Signing and Rekor transparency log | cosign v2.6.5 |
| Red Hat Edge Manager | Fleet management and model delivery to the device | flightctl 1.3.0 |
| OpenShift Pipelines (Tekton) | Multi-arch runtime image build and sign | — |
| RHEL | Managed device operating system | 10.2 |
| MinIO, Kafka | Episode storage and manifests | — |

## Built on

- [`ros-physical-ai/demos`](https://github.com/ros-physical-ai/demos) — SO-ARM101, Gazebo
  simulation, LeRobot ACT policy, and the Rosetta ROS 2 ↔ LeRobot bridge
- [LeRobot](https://github.com/huggingface/lerobot) — policy training and dataset format
- ROS 2 with `rmw_zenoh` middleware
- Hardware: HP ZGX Fury (GB300, Grace Blackwell, aarch64); development on an x86_64 workstation
  with an NVIDIA RTX 5090

## Repository layout

| Path | Contents |
|---|---|
| `argocd/` | Argo CD Applications that bootstrap the hub, with the bring-up order |
| `gitops/` | Everything the hub runs: operators, pipeline, RHEM Fleet and Catalog, Tekton, observability |
| `rhem/bootstrap/` | The one-time Edge Manager objects (`Repository`, `ResourceSync`, `Catalog`) |
| `pipeline/` | The OpenShift AI pipeline: assemble → train → gate → package → sign → register → promote |
| `device/` | Device provisioning and enrollment for RHEL 10 (VM stand-in and the Fury host) |
| `docker/` | The simulation image and the multi-arch inference runtime image |
| `src/` | Coordinator, episode emitter, ground-truth scorer, camera bridge, dataset assembler, host runner |
| `tools/` | Host-side operational scripts (collection loop, disk guard, bag retention) |
| `docs/` | Architecture, data flow, bill of materials, Fury bring-up, demo runbook, evaluation records |

## Documentation

| Document | Purpose |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Component diagram and topology |
| [`docs/DATA-FLOW.md`](docs/DATA-FLOW.md) | Episode-to-promotion data flow, stage by stage |
| [`docs/BILL-OF-MATERIALS.md`](docs/BILL-OF-MATERIALS.md) | What runs where, built from what |
| [`docs/FURY-SETUP.md`](docs/FURY-SETUP.md) | Bring-up guide for the HP ZGX Fury |
| [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md) | Booth runbook: screens, narration, failure recovery |
| [`argocd/README.md`](argocd/README.md) | Hub bootstrap order and hand-created secrets |
| [`device/README.md`](device/README.md) | Device provisioning and enrollment |
| [`docs/eval-records/`](docs/eval-records/) | Evidence behind every number above |

Engineering history — the phase plan, the full decision log, and the original brief — is kept
under [`docs/internal/`](docs/internal/) for reference.

## Deploying

1. **Hub:** install Single-Node OpenShift 4.19+, then apply the Argo CD Applications in the order
   in [`argocd/README.md`](argocd/README.md). Hand-created secrets are listed there.
2. **Edge Manager:** apply [`rhem/bootstrap/`](rhem/bootstrap/) once; the Fleet and Catalog are
   then reconciled from Git.
3. **Device:** run [`device/provision.sh`](device/provision.sh) on the RHEL 10 host and enroll it
   with [`device/enroll.sh`](device/enroll.sh); labels select the CPU or GPU branch of the Fleet.
4. **Fury specifics:** [`docs/FURY-SETUP.md`](docs/FURY-SETUP.md).

## Status

As of 2026-09-09:

- Loop closed end to end on the development system: curated data → fine-tune → paired eval gate →
  signed ModelCar → Model Registry → pull request → Edge Manager rollout → verified pull → rollback.
- Multi-arch runtime image built and signed in-cluster; the device serves from that digest.
- Remaining before the booth: recording the contingency kit (a recorded fallback for every beat)
  and bringing the system up on the Fury hardware.
