# hp-roscon-flywheel

Physical AI Edge Flywheel demo for HP's ROSCon 2026 booth on the ZGX Fury (GB300). Single-box
governed lifecycle: SO-ARM101 sim in Gazebo -> curate -> train -> sign -> GitOps promote ->
blue/green swap -> loop closes.

## Project context

Read these files in order for full context:
1. `PROJECT-BRIEF.md` — what, why, architecture, hardware, constraints
2. `BUILD-PLAN.md` — phased execution plan (Phase 0-4) with exit criteria
3. `THOR-TESTING-REUSE.md` — what copies from thor-testing, what adapts, known gotchas

When decisions are made during execution, record them in `DECISIONS.md` using the same
numbered format as thor-testing's decision log (D001, D002, ...).

## Key facts

- **Desktop (dev stand-in):** i9-13900K / RTX 5090 / 128 GB / Ubuntu / x86_64 / 10.0.0.41
  SSH as `jary`. Ubuntu stays — SNO runs in a KVM VM with GPU passthrough.
- **Target (demo):** HP ZGX Fury GB300 / Grace Blackwell / 748 GB / RHEL 10.2 / aarch64
- **Reuse source:** `~/redhat/git/thor-testing` — gitops/, tekton/, pipeline/ are the reusable
  manifests. Do NOT modify that repo; copy/adapt into this one.
- **Upstream sim:** `github.com/ros-physical-ai/demos` — SO-ARM101, LeRobot ACT, Rosetta, Pixi
- **Platform:** Single-Node OpenShift (SNO) in a KVM VM preferred. MicroShift on bare Ubuntu
  is the fallback if SNO overhead is too heavy.

## Rules

- This is a build project. Prefer working code and tested manifests over planning docs.
- The desktop is x86_64; the Fury is aarch64. Build multi-arch from the start where possible.
  Document every x86-specific assumption.
- Do not modify `~/redhat/git/thor-testing`. Copy manifests into this repo and adapt them here.
- The RTX 5090 is the only GPU. Do not assume MIG capability (desktop GPU, not data center).
- `.plans/` is gitignored — use it for scratch notes, working drafts, and artifacts that aren't
  ready for the repo. Move things to tracked files when they're solid.
