<!-- This project was developed with assistance from AI tools. -->
# tools/ — the scripts that build, operate and show the system

Two groups matter, and they run on two different machines. Every script's header comment says what it does,
what it needs, what it refuses and whether it is a one-time bring-up tool or a between-showings tool; read it
before running anything.

| Directory | Runs where | As whom |
|---|---|---|
| [`hub/`](hub/) | a laptop with access to the hub | an `oc` login (`KUBECONFIG`), and for the fleet, robot zero and promotion scripts a logged-in `flightctl`. Nothing runs as root on the laptop |
| [`host/fury/`](host/fury/README.md) | the GPU host itself | they re-run themselves under `sudo` and change the host |

The split is deliberate: the GPU host holds no hub credential, and a laptop holds no root on the host. A step that
needs both is two commands on two machines (removing a robot for good, for example). Where a hub-side script has
to act on the host, it goes over ssh to the target named in `FURY_SSH` and asks for the sudo password on the
terminal.

## `hub/` - laptop side, against the hub

| Group | Scripts | What for |
|---|---|---|
| Image builds (one-time bring-up) | `build-eval-dashboard.sh`, `build-dev-workspace.sh`, `build-fleet-world.sh`; shared body `lib-build-image.sh` | build an image with the hub's own signed Tekton pipeline - clone, build, push, cosign sign and verify against the hub's transparency log - and print the digest and where to pin it |
| Signing and Secrets (one-time) | `cosign-image.sh`, `create-cosign-secrets.sh`, `create-model-registry-db-secret.sh` | sign an existing image with the hub's key through the cluster's own Task; create the hand-managed Secrets without ever printing a value |
| The pipeline | `upload-pipeline.sh`, `start-promotion-run.sh` | upload the compiled pipeline as a new version; start one promotion run by hand, the way the trigger would |
| The promotion beat (between showings) | `reset-promotion.sh`, `reopen-promotion.sh`; shared rule `lib-promotion.sh` | put both Fleets back to the model before the last promotion; re-propose the same signed model in a new pull request that says what it is. Both refuse while a rollout is in progress |
| The fleet | `fleet-enrol-config.sh`, `fleet-approve.sh`, `fleet-status.sh`, `fleet-worlds-scale.sh` | make the robots' short-lived enrolment config and hand it to the host; approve the enrolment requests that really are fleet VMs, with their labels; the fleet as Edge Manager sees it, decommissioning, stale requests; size the robots' worlds on the hub |
| Robot zero and the GPU mode | `robot-zero.sh`, `fury-switch.sh` | place the host's policy on its MIG slice through the device's label, reset or inspect robot zero; move the machine between its two GPU modes in the one safe order |
| At demo time | `training-watch.sh`, `verify-signed.sh` | the training tenant's output, formatted for a projector; verify the pinned model image against the signing key and the transparency log, as every device does, and print its log entry's address. Both read and change nothing |
| `manual/` | three `EndpointSlice` manifests | applied by hand once per hub: Argo CD does not manage that kind (`argocd/README.md`) |

## `host/fury/` - on the GPU host, with sudo

Written for one particular machine; [`host/fury/README.md`](host/fury/README.md) says why they must not be run on
anything else. The operator copies them to a working directory on the host and runs them there. The number is the
order of bring-up:

| Prefix | Stage | Scripts |
|---|---|---|
| `0x` | host preparation | journal, console boot, the data disk, packages, MIG (`04-mig.sh`, with `mig-config.sh` and `mig-config.service` for every boot), a slice smoke test, the VM network, DNS and tailnet route (`06-network.sh`, `dnsmasq-fury.conf`, `fury-net.xml`), a network probe |
| `1x` | the flywheel's pieces on the host | first CUDA, the sim image build and smoke test, first inference, the robot loop as systemd units with `fury-mode` (`14-flywheel-services.sh`), the camera port for the hub |
| `3x` | the hub VM | a full-size guest pre-test, host preparation, the agent-based single-node install (`32-sno-install.sh`, templates in `sno/`) |
| `4x` | the host as an Edge Manager device | `40-device-provision.sh`, then the operator's steps in `41-device-enroll.md` |
| `5x` | the training pipeline's host side | the runner and the evaluation rig as units, seeding the incumbent's checkpoint, staging a promotion, checking a model image under the device's own signature policy |
| `6x` | the coding assistant | model fetch, the inference server smoke test, the assistant as a service on slice `0:0`, its port for the hub |
| `7x` | the tenants and their numbers | GPU metrics (`70`), the training tenant (`72`), the fleet's rendering tenant (`73`), robot zero (`74`), tenant metrics (`75`), the image mode spike (`79`) |
| `8x` | the robots | the golden image (`80`), scale, removal and the enrolment config's home (`81`), reading an unreachable robot's disk (`82`) |

Without a number: `fury-mode.sh` (installed as `fury-mode`: `flywheel`, `tenants`, `zero`, `status` - the one
owner of the GPU's mode and of every tenant's unit), `recon.sh` and `recon2.sh` (read-only looks that need root),
`copy-vmedia.sh`. `flywheel/` holds the podman quadlets and units of every tenant and of the robot loop, `fleet/`
the robots' OS image, `rhtas-arm64/` the transparency log's arm64 rebuild and bring-up.

**Operator pages.** A step that a person carries out, judges or repairs has a page next to its script, with the
same number: `41-device-enroll.md`, `51-eval-rig.md`, `61-rhaiis-smoke.md`, `70-dcgm.md`, `72-training-tenant.md`,
`73-fleet-renderer.md`, `74-robot-zero.md`, `75-tenant-metrics.md`. Their command blocks are meant to be pasted as
they are: no comments inside them, no placeholders. The robots' runbook is
[`docs/internal/FLEET-VMS.md`](../docs/internal/FLEET-VMS.md), section 15; the demo's own runbooks are
[`docs/FURY-DEMO.md`](../docs/FURY-DEMO.md) and [`docs/FURY-DEMO-LITE.md`](../docs/FURY-DEMO-LITE.md).

## The rest

| Path | What |
|---|---|
| `host/disk-guard.sh` | the disk guard that parks the recording loop before a disk fills (installed by `14-flywheel-services.sh`) |
| `fleet/extract_trajectories.py` | turns a dataset's recorded actions into the motion file the robots' worlds replay |
| `ci/check-ai-marker.sh` | fails when a tracked source file lacks the AI-assistance marker |
| `lint/` | the documentation lint |
