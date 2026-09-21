<!-- This project was developed with assistance from AI tools. -->
# Bill of Materials

Every component this project runs, where it runs, and what it is built from. The linked manifests
are authoritative. Everything below is on **one machine**: an HP ZGX Fury workstation (NVIDIA GB300,
aarch64) running RHEL 10.2.

| Where | What |
|---|---|
| The GPU host | four tenants, one per MIG slice of the one GPU; two metrics exporters; the VMs below |
| A VM on the host | the hub: single-node Red Hat OpenShift 4.22 (arm64) |
| Twelve micro-VMs on the host | the robots: RHEL image mode devices managed by Red Hat Edge Manager |
| The hub, as pods | the platform, the flywheel's services, the robots' physics worlds, the pages |

Red Hat Edge Manager manages 13 devices: the GPU host itself and the twelve robots.

## The GPU host: four tenants

MIG layout `9,19,19,19` (`tools/host/fury/mig-config.sh`), switched and supervised by `fury-mode`
(`tools/host/fury/fury-mode.sh`). The units are podman quadlets in `tools/host/fury/flywheel/`.

| Slice | Tenant | Unit | Built from / image | Operator page |
|---|---|---|---|---|
| `0:0` (3g.126gb) | coding assistant: a Qwen coder model (`RedHatAI/Qwen3-Coder-Next-NVFP4`) served by vLLM, OpenAI-style API with tool calling | `llm-assistant.container` | a vLLM image, pinned by digest in the unit | `tools/host/fury/63-assistant-install.sh` (header); the first smoke test: `tools/host/fury/61-rhaiis-smoke.md` |
| `0:1` (1g.31gb) | robot zero: the signed ACT policy, **delivered by Red Hat Edge Manager** (Fleet `act-inference`) and placed on this slice by the device's `gpu_device` label | the Fleet's quadlet application, plus `robot-zero-sim`, `robot-zero-frames`, `robot-zero-episodes`, `robot-zero-emitter` (no GPU) | the signed runtime and model images pinned in `gitops/rhem/fleet-act-inference.yaml`; the signed sim image for the world | `tools/host/fury/74-robot-zero.md` |
| `0:2` (1g.31gb) | training tenant: real ACT fine-tunes in rounds; nothing from them is promoted | `training-tenant.container` | the signed runtime image | `tools/host/fury/72-training-tenant.md` |
| `0:3` (1g.31gb) | fleet rendering tenant: ray-traces every robot's two cameras with CUDA (MuJoCo-Warp), serves the fleet wall | `fleet-renderer.container` | `docker/Dockerfile.fleet-renderer` over `src/fleet-renderer/`, built on the host | `tools/host/fury/73-fleet-renderer.md` |

Beside the tenants, without a slice of their own:

| Unit | Role | Operator page |
|---|---|---|
| `dcgm-exporter.container` | GPU and MIG metrics for the hub's dashboard | `tools/host/fury/70-dcgm.md` |
| `tenant-metrics.container` | the training tenant's loss and step rate, the renderer's frame rate (`src/tenant-metrics/`) | `tools/host/fury/75-tenant-metrics.md` |

The host is also the enrolled device: `tools/host/fury/40-device-provision.sh` installs the flightctl
agent 1.3.0, podman and the NVIDIA CDI configuration, and `tools/host/fury/41-device-enroll.md` is the
enrolment. The units of flywheel mode are installed too: the flywheel's own sim, which renders its
cameras on the whole GPU, and the recording coordinator (`so-arm-sim`, `act-coordinator`) refuse to
start while MIG is on; with them, the pipeline's host-side runner and evaluation rig
(`flywheel-runner`, `flywheel-eval`, `tools/host/fury/51-eval-rig.md`). `fury-mode flywheel` is for
off-stage work and is not part of a showing.

## The robots: twelve micro-VMs

| Piece | What | Where |
|---|---|---|
| OS image | RHEL image mode: `registry.redhat.io/rhel10/rhel-bootc:10.2` plus the flightctl agent, podman, cloud-init and a firewall; no application image | `tools/host/fury/fleet/Containerfile` |
| Disk | built once with `rhel10/bootc-image-builder:10.2`; every robot is a copy-on-write clone (2 vCPU, 3 GiB, 40 GiB thin) | `tools/host/fury/80-fleet-golden.sh`, `tools/host/fury/81-fleet-scale.sh` |
| Workload | the same signed policy and model on CPU, plus a Zenoh router, delivered by Fleet `robots`; each robot pulls and verifies its own images | `gitops/rhem/fleet-robots.yaml` |
| Enrolment, approval, status | from a laptop | `tools/hub/fleet-enrol-config.sh`, `tools/hub/fleet-approve.sh`, `tools/hub/fleet-status.sh` |

Design and bring-up runbook: [`docs/internal/FLEET-VMS.md`](internal/FLEET-VMS.md).

## The hub (single-node OpenShift 4.22)

Four operators come from [`argocd/bootstrap-operators.yaml`](../argocd/bootstrap-operators.yaml)
(OpenShift GitOps, Cluster Observability, Tempo, OpenShift Dev Spaces). Everything else is delivered by
ten Argo CD Applications, each pruning to exactly one source. Bootstrap order and dependencies:
[`argocd/README.md`](../argocd/README.md); the directories: [`gitops/README.md`](../gitops/README.md).

| Argo app | Source | Namespace | What it delivers |
|---|---|---|---|
| `storage` | `gitops/storage/` | `local-path-storage` | default StorageClass (local-path provisioner) |
| `operators` | `gitops/operators/` | `openshift-operators`, `redhat-ods-operator` | OLM Subscriptions: OpenShift Pipelines, OpenShift AI 3.5 (channel `stable-3.5`) |
| `operators-config` | `gitops/operators-config/` | several | `DataScienceCluster`, `DataSciencePipelinesApplication`, the Model Registry |
| `minio` | `gitops/minio/` | `minio` | object storage for curated episodes, datasets, checkpoints and evaluation records |
| `flywheel` | `gitops/flywheel/` | `flywheel` | curator, sync agent, Kafka, the training trigger, the live lane's curator, the pages below, the Services in front of the host's assistant, cameras and fleet wall |
| `observability` | `gitops/observability/` | `observability` | monitoring stack, scrape configs for the host's exporters, Perses and the GPU tenants dashboard |
| `rhem` | Red Hat's Helm chart `redhat-rhem` 1.3.0 from `https://charts.openshift.io` | `flightctl` | Red Hat Edge Manager 1.3.0 - the hub side. The `Fleet` and `CatalogItem` objects are not Kubernetes objects: Edge Manager renders them itself from `gitops/rhem/` and `gitops/rhem-catalog/` (`rhem/bootstrap/`) |
| `tekton` | `gitops/tekton/` | `flywheel` | the Tasks and Pipeline that build and sign this project's images in the cluster |
| `devspaces` | `gitops/devspaces/` | `openshift-devspaces` | the `CheCluster` behind the coding workspace |
| `fleet-worlds` | `gitops/fleet-worlds/` | `fleet` | the robots' worlds: one physics-only sim pod per robot (`StatefulSet/world`, sized with `tools/hub/fleet-worlds-scale.sh`) |

Key platform components:

| Component | Role | Version / source |
|---|---|---|
| Red Hat OpenShift | the hub | 4.22, single node, arm64 (`tools/host/fury/32-sno-install.sh`) |
| OpenShift GitOps (Argo CD) | delivers everything above | operator-managed |
| Red Hat OpenShift AI | Data Science Pipelines runs `pipeline/act_flywheel_pipeline.py`; Model Registry keeps one durable record per promoted candidate | 3.5 (`gitops/operators/rhoai-operator.yaml`, `gitops/operators-config/`) |
| Red Hat Edge Manager | Fleets, catalog, device rollout | 1.3.0, chart `redhat-rhem` (`argocd/rhem-app.yaml`) |
| Red Hat OpenShift Pipelines (Tekton) | builds and signs the runtime, evaluation-page, workspace and world images | `gitops/tekton/runtime-image-pipeline.yaml` |
| Red Hat OpenShift Dev Spaces | the coding tenant's workspace; its agent uses the model on slice `0:0` | `gitops/devspaces/checluster.yaml`, `devfile.yaml`, `src/dev-workspace/` |
| Transparency log | every signature is logged and every device checks the entry. Signing is key-based cosign; the log is Rekor on Trillian, rebuilt for arm64 from the public Red Hat Trusted Artifact Signer 1.4.3 source and run by that operator outside OLM | `tools/host/fury/rhtas-arm64/` |
| Kafka (KRaft, single broker) | episode manifests, dataset manifests, training triggers | `gitops/flywheel/edge-kafka.yaml` |
| Object storage | curated episodes, datasets, checkpoints, evaluation records | `gitops/minio/minio.yaml` |

## The pages

Addresses: [`docs/internal/FURY-URLS.md`](internal/FURY-URLS.md).

| Page | Served by | Built from |
|---|---|---|
| Flywheel page | `gitops/flywheel/dashboard.yaml` | inline in the manifest |
| Paired evaluation; Live episodes (collection); Live episodes - live lane | `gitops/flywheel/eval-dashboard.yaml`, `gitops/flywheel/eval-dashboard-show.yaml` - three instances of one signed image | `src/eval-dashboard/` |
| Fleet wall | the rendering tenant on the host, behind Route `fleet-wall` (`gitops/flywheel/fleet-wall.yaml`) | `src/fleet-renderer/` |
| GPU tenants dashboard | Perses (`gitops/observability/gpu-tenants-dashboard.yaml`) | the two exporters on the host |
| Red Hat Edge Manager, OpenShift console, OpenShift AI, Argo CD, Dev Spaces, the transparency log | the platform | - |

## The training/promotion pipeline

Defined in [`pipeline/act_flywheel_pipeline.py`](../pipeline/act_flywheel_pipeline.py), run by OpenShift AI's Data Science Pipelines. Stages, in order: `trigger_and_wait` → `eval_gate` → `package_modelcar` → `sign_modelcar` → `register_model` → `open_promotion_pr` → `record_pr_url`. See [`docs/DATA-FLOW.md`](DATA-FLOW.md) for what each stage does.

## Images

| Image | Built from | Built by |
|---|---|---|
| runtime image (policy, coordinator, training tenant) | [`docker/Dockerfile.gpu-inference`](../docker/Dockerfile.gpu-inference) | OpenShift Pipelines, multi-arch, signed, pinned by digest in both Fleets |
| sim image (the flywheel's sim, the robots' worlds, robot zero's world) | [`docker/Dockerfile`](../docker/Dockerfile) | `tools/hub/build-fleet-world.sh` - the same signed pipeline |
| model image (ModelCar) | the pipeline's `package_modelcar` and `sign_modelcar` steps | the promotion pipeline; its digest is what a promotion changes |
| evaluation page | `src/eval-dashboard/` | `tools/hub/build-eval-dashboard.sh` |
| coding workspace | `src/dev-workspace/` | `tools/hub/build-dev-workspace.sh` |
| fleet renderer | [`docker/Dockerfile.fleet-renderer`](../docker/Dockerfile.fleet-renderer), `src/fleet-renderer/` | on the host, `tools/host/fury/73-fleet-renderer-install.sh build` |
| robots' OS image | `tools/host/fury/fleet/Containerfile` | on the host, `tools/host/fury/80-fleet-golden.sh` |

## Credentials and secrets

None of the values below are ever written into git. What exists, where, and how to (re)create it
is documented in [`argocd/README.md`](../argocd/README.md)'s "Hand-created Secrets" table:
object storage root credentials, hub S3 credentials, the cosign signing key, a GitHub token for opening
promotion PRs, and the Model Registry database password. Each is created once, by hand, before the
Argo app that consumes it first syncs.
