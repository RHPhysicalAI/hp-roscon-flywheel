# HP ROSCon Flywheel

A demo for HP's booth at **ROSCon 2026**, running the complete **Physical AI Edge Flywheel** on a
single **HP ZGX Fury (GB300, Grace Blackwell, aarch64)**. One box runs the whole governed
lifecycle: a robot arm learns in simulation, only its good rollouts become training data, the
platform retrains and re-evaluates the policy against its own predecessor, the result is signed
and promoted through GitOps, and a managed edge device pulls and verifies it before it will run —
the same pipeline that would ship a model to a real fleet, not a simplified stand-in for one.

**Red Hat's part** is the governed lifecycle: GitOps, signing and transparency-log enforcement,
the OpenShift platform, and fleet delivery. **HP's part** is the hardware. **NVIDIA's part** is
the model. **The ROS community's part** is the simulation the whole thing is built on. See
[`PROJECT-BRIEF.md`](PROJECT-BRIEF.md) for the full breakdown of who owns what.

## The pitch, in one loop

```
sim (SO-ARM101 in Gazebo) → curator (ground-truth scoring) → hub storage (MinIO/Kafka)
   → training pipeline (fine-tune → paired eval gate) → package + sign (cosign/Rekor)
   → GitOps promotion PR → human merge → Red Hat Edge Manager rolls it to the device
   → device verifies the signature before it will even pull the image
   → the improved policy is back in the sim, generating the next round's data
```

Every stage is real, not staged for the demo — see [`docs/DATA-FLOW.md`](docs/DATA-FLOW.md) for
the full walk-through, including the one place a step is deliberately time-compressed (and why
that's disclosed, not hidden).

## Why this exists, and why it's built this way

Red Hat's value in a physical-AI stack isn't the model or the arm — plenty of projects have both.
It's proving a *governed* path from "the robot got better" to "a fleet of robots got better,
provably, with a human approval gate and a cryptographic trail." This project deliberately builds
on the ROS community's own upstream SO-ARM101 example (`github.com/ros-physical-ai/demos`) rather
than a bespoke robot, because for a ROSCon audience specifically, that's the credible move: Red Hat
adds the layer the upstream project doesn't have — signed artifacts, managed fleet delivery, a
path to real scale — instead of claiming someone else's simulation work as its own.

The project also reuses roughly 70% of a prior internal flywheel build
(`thor-testing`) rather than starting over; see
[`THOR-TESTING-REUSE.md`](THOR-TESTING-REUSE.md) for exactly what carried over and what didn't.

## Architecture, at a glance

- **The hub** — Single-Node OpenShift running Argo CD (GitOps), Red Hat OpenShift AI (training
  pipelines), Red Hat Trusted Artifact Signer (cosign + Rekor transparency log), Red Hat Edge
  Manager (fleet management), a Model Registry, and the MinIO/Kafka data plane.
- **The device** — a managed edge endpoint running only the served policy, delivered as a signed
  OCI image volume by the Edge Manager Fleet. On the development stand-in this is a small RHEL 10
  VM running the policy on CPU; on the Fury, the physical host itself *is* the device, serving on
  the GPU.
- **The sim/producer** — Gazebo running the SO-ARM101 arm, a camera bridge, and a coordinator that
  drives episodes, scores them on ground truth, and hands the good ones to the hub.

Full diagram and component-by-component detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
What's actually deployed, where, and from what image/chart version:
[`docs/BILL-OF-MATERIALS.md`](docs/BILL-OF-MATERIALS.md).

## Finding your way around this repo

| Document | What it's for |
|---|---|
| [`PROJECT-BRIEF.md`](PROJECT-BRIEF.md) | Original intent, hardware, constraints, and who's involved |
| [`BUILD-PLAN.md`](BUILD-PLAN.md) | The phased execution plan, current status, and exit criteria per phase |
| [`DECISIONS.md`](DECISIONS.md) | The full engineering decision log — every non-obvious call, why it was made, and what it cost. The most detailed record of *how* this was built |
| [`THOR-TESTING-REUSE.md`](THOR-TESTING-REUSE.md) | What was reused from the prior project vs. rebuilt from scratch, and why |
| [`BOOTSTRAP-LOOP.md`](BOOTSTRAP-LOOP.md) | Design for the not-yet-built next phase: a fully autonomous improvement loop with no human-provided data |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System architecture and component diagram |
| [`docs/DATA-FLOW.md`](docs/DATA-FLOW.md) | The episode-to-promotion data flow in detail |
| [`docs/BILL-OF-MATERIALS.md`](docs/BILL-OF-MATERIALS.md) | Every component, which box it runs on, and what it's built from |
| [`docs/FURY-SETUP.md`](docs/FURY-SETUP.md) | Bring-up guide for standing this up on the physical Fury hardware |
| [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md) | The actual booth script: what to show, say, and do if something breaks |
| [`gitops/`](gitops/), [`argocd/`](argocd/) | Everything the hub runs, delivered by GitOps |
| [`pipeline/`](pipeline/) | The training/eval/sign/promotion pipeline definition |
| [`src/`](src/), [`docker/`](docker/) | The sim, coordinator, and inference application code and images |

## Status

As of this writing, the project has closed its self-improvement loop end to end on the desktop
stand-in — a policy fine-tuned on its own curated successes measurably beats its predecessor
(73% → 86% on a fixed 100-scene paired evaluation, p = 0.019), and that result has been promoted
through the full governed path: signed, registered, opened as a GitOps pull request, merged, and
rolled out to a Red Hat Edge Manager-managed device that verified the signature before pulling it.
Rollback (a `git revert`) has been rehearsed and is symmetric. Multi-arch container builds (x86_64
and aarch64) run through OpenShift Pipelines with a transparency-log entry per build.

What's left before the booth is almost entirely non-technical: recording the contingency kit (a
video fallback for every beat of the demo, in case nothing can run live at the venue) and standing
the same system up on the physical Fury hardware once it arrives. See `BUILD-PLAN.md`'s status
table for the authoritative, currently-maintained picture.
