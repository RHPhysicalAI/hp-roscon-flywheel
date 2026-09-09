# Standing This Up on the Fury

A pre-flight guide for whoever has hands-on access to the physical HP ZGX Fury and is bringing the
flywheel up on it for the first time. What has been exercised on aarch64 hardware is called out
explicitly.

## The hardware

HP ZGX Fury — Grace Blackwell (GB300), 748 GB unified memory, aarch64, RHEL 10.2.

## The one thing to understand before anything else: the device *is* the host

Everywhere else this project runs (the development stand-in), the "managed device" that Red Hat
Edge Manager controls is a separate small VM, and the hub (SNO) is a second VM on the same
physical box. On the Fury, that separation collapses: **the Fury's own RHEL 10.2 host is the
managed device**, and the SNO hub runs in a VM on that same host. Everything downstream of that —
the Fleet, the promotion pipeline, the trust chain — is unchanged; only what the device labels
resolve to and where the GPU is actually reachable from changes. Full comparison:

| | Desktop stand-in | Fury |
|---|---|---|
| Managed device | separate RHEL 10 KVM VM, no GPU | **the Fury host itself** |
| Enrollment script | `device/enroll.sh` | `device/provision.sh` (also installs `nvidia-container-toolkit` + `nvidia-ctk cdi generate`) |
| Device labels | `site=desktop gpu=none policy_device=cpu` | `site=fury gpu=nvidia arch=arm64 policy_device=cuda` |
| What the Fleet renders | CPU branch, thread caps, rollout batch 1 | `AddDevice=nvidia.com/gpu=all` (CDI), `POLICY_DEVICE=cuda`, rollout batch 2 |
| Runtime image | same multi-arch digest, amd64 platform resolves | same digest, **arm64** platform resolves |
| Sim + coordinator engine | host Docker | host **podman** (`ENGINE=podman tools/host/run-coordinator.sh`) |
| Hub | SNO VM on the desktop | fresh SNO 4.19+ VM on the Fury |

`docs/DEMO_RUNBOOK.md`'s "On the Fury" table is the fuller version of this comparison, including
the exact commands that differ per beat of the demo.

## Bring-up sequence

1. **Hub first.** Install a fresh SNO 4.19 or later (RHEM 1.3's Helm chart requires
   `kubeVersion >= 1.32`; do not work around this with an override — it hides a real
   incompatibility). Apply the bootstrap Applications by hand, in the documented order:
   [`argocd/README.md`](../argocd/README.md)'s bootstrap table, then
   [`rhem/bootstrap/README.md`](../rhem/bootstrap/README.md) for the Fleet/Catalog
   `Repository`/`ResourceSync` objects (these are flightctl API objects, not Kubernetes resources
   Argo can apply directly).

2. **Provision and enroll the device** (the Fury host itself): `device/provision.sh` — arch-neutral,
   already written to run on aarch64 (installs `subscription-manager`, podman ≥ 5.5, the flightctl
   EPEL10 repo, the `flightctl-agent` package, and on the Fury specifically the NVIDIA container
   toolkit + CDI generation). Enroll and approve with the Fury labels above. Trust files
   (`policy.json`, `cosign.pub`, `rekor.pub`) come from the Fleet automatically on enrollment, not
   from the provisioning script.

3. **Sim + camera bridge + coordinator** run as host **podman** containers built from the arm64
   images (the same Tekton multi-arch build that already produces the amd64 images used on the
   desktop). `tools/host/run-coordinator.sh` already supports `ENGINE=podman` and handles the
   SELinux `:z` relabeling podman needs that Docker doesn't.

4. **The device pulls from quay.io directly — no mirror.** Confirm outbound registry access exists
   before enrollment; there's no fallback path built for a disconnected or mirrored registry.

5. **Verify, then hand it back to the promotion pipeline.** Once the device shows
   `applicationsSummary: Healthy` and the sim loop is producing curated episodes stamped with the
   Fleet's model version, the rest of the system — training, eval-gate, signing, promotion,
   rollout — is identical to what already runs on the desktop. Nothing about the governed path
   itself needs Fury-specific changes.

## What's verified on aarch64 already, and what isn't

Verified, not just designed:
- The multi-arch runtime image builds and signs cleanly for arm64 through the same OpenShift
  Pipelines pipeline that builds amd64 (`docs/eval-records/runtime-image-tekton.md`) — the arm64
  leg runs under qemu emulation on the desktop's amd64 build node and takes materially longer
  (tens of minutes vs. single-digit minutes for amd64); expect the *first* Fury-native build (no
  emulation) to be faster, not slower, but this hasn't been measured yet.
- `torch` and its CUDA build for aarch64/Blackwell: aarch64 wheels exist, but only the base
  `torch` package carries the `+cu130` suffix on arm64 (torchvision/torchaudio don't) — the build
  accounts for this with per-architecture pins in `docker/Dockerfile.gpu-inference`.
- RHEL 10.2's AppStream repo ships a podman version (5.8.2) that satisfies the image-volume
  feature RHEM 1.3 needs.

Explicitly **not yet rehearsed** — treat these as the first things to check on-site, not
assumptions to build on:
- The `ENGINE=podman` path for `run-coordinator.sh` has never been run for real (SELinux relabeling
  logic is written, not exercised).
- The aarch64 Blackwell CUDA path has never actually served an inference request — the runtime
  image builds and is signed for arm64, but nothing has run it on real Grace Blackwell silicon yet.
- A from-scratch SNO bring-up applying all bootstrap Applications by hand, back to back, has not
  been rehearsed end to end.

## Known gaps worth planning around

- The dashboard's camera-stream host is hardcoded; it needs to become configurable before Beat 1
  works unmodified on the Fury (a `TODO` in `gitops/flywheel/dashboard.yaml`).

## If something breaks

`docs/DEMO_RUNBOOK.md`'s "Failure recovery" table covers the operational failure modes already
seen on the development stand-in (loop restart, dashboard hangs, port-forward drops, rollout
stalls). Most of it transfers directly — the underlying components are identical, only their
physical location changes.
