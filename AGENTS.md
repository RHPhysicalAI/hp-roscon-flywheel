# hp-roscon-flywheel

Physical AI Edge Flywheel demo for HP's ROSCon 2026 booth on the ZGX Fury (GB300). Single-box
governed lifecycle: SO-ARM101 sim in Gazebo → curate → train → sign → GitOps promote → blue/green
swap → loop closes.

## Project context

Read these files in order for full context:
1. `PROJECT-BRIEF.md` — what this project is, architecture, constraints, key repos
2. `PHASE0-PLAN.md` — current phase: desktop foundation (SNO + GPU on Ubuntu desktop)
3. Planning artifacts in `~/.config/opencode/brim/.plans/hp-gb300-roscon-demo-research/`:
   - `concept-flywheel-on-fury.md` — authoritative concept doc
   - `desktop-fury-buildout-plan.md` — full 4-phase build plan
   - `07-thor-testing-jumpstart.md` — what reuses from thor-testing

## Key facts

- **Desktop (dev stand-in):** i9-13900K / RTX 5090 / 128 GB / Ubuntu / x86_64
- **Target (demo):** HP ZGX Fury GB300 / Grace Blackwell / 748 GB / RHEL 10.2 / aarch64
- **Reuse source:** `~/redhat/git/thor-testing` — gitops/, tekton/, pipeline/ are the reusable
  manifests. Do NOT modify that repo; copy/adapt into this one.
- **Upstream sim:** `github.com/ros-physical-ai/demos` — SO-ARM101, LeRobot ACT, Rosetta, Pixi
- **Platform:** Single-Node OpenShift (SNO) preferred. MicroShift is the fallback if SNO
  overhead is too heavy for the desktop.

## Rules

- This is a build project. Prefer working code and tested manifests over planning docs.
- Record architectural decisions in `DECISIONS.md` (create when needed, same format as
  thor-testing's decision log).
- The desktop is x86_64; the Fury is aarch64. Build multi-arch from the start where possible.
  Document every x86-specific assumption.
- Do not modify `~/redhat/git/thor-testing`. Copy manifests into this repo and adapt them here.
- The RTX 5090 is the only GPU. Do not assume MIG capability (desktop GPU, not data center).
