# Bill of Materials

Every component this project runs, which machine it runs on, and what it's built from. Verified
against the live desktop stand-in as of this writing; the manifest paths are the source of truth
going forward — if this table and the manifests ever disagree, trust the manifests and treat this
page as due for an update.

## Hub (Single-Node OpenShift)

Delivered by 8 Argo CD Applications, each pruning to exactly one directory under `gitops/`. Bootstrap order and dependencies: [`argocd/README.md`](../argocd/README.md).

| Argo app | Manifests | Namespace | What it delivers |
|---|---|---|---|
| `storage` | `gitops/storage/` | `local-path-storage` | Default StorageClass (local-path provisioner) |
| `operators` | `gitops/operators/` | `openshift-operators` | OLM Subscriptions: OpenShift Pipelines, RHOAI, RHTAS |
| `operators-config` | `gitops/operators-config/` | `redhat-ods-operator` + others | `DataScienceCluster`, `DataSciencePipelinesApplication`, `Securesign`, `ModelRegistry` CRs |
| `minio` | `gitops/minio/` | `minio` | Object storage for curated episodes, datasets, checkpoints |
| `flywheel` | `gitops/flywheel/` | `flywheel` | Curator, sync-agent, Kafka, manifest-consumer, dashboard, the sim's in-cluster manifest (vestigial on the desktop — see note below) |
| `observability` | `gitops/observability/` | `observability` | Perses dashboards, Tempo datasource (Perses/Tempo operators themselves are hand-installed, not under this app — see `argocd/README.md`) |
| `rhem` | `gitops/rhem/` (via a Helm OCI chart) | `flightctl` | Red Hat Edge Manager (flightctl 1.3) — hub components; the `Fleet`/`Catalog` objects themselves are rendered separately via `rhem/bootstrap/` (they're flightctl API objects, not k8s CRs) |
| `tekton` | `gitops/tekton/` | `flywheel` | OpenShift Pipelines Tasks/Pipeline for the multi-arch runtime-image build + sign |

Key platform components inside those namespaces:

| Component | Role | Version / image |
|---|---|---|
| Argo CD (OpenShift GitOps) | Delivers everything above | operator-managed |
| Kafka (KRaft, single broker) | Episode manifests, dataset manifests, training triggers | Strimzi image, see `gitops/flywheel/edge-kafka.yaml` |
| MinIO | Curated episodes, datasets, checkpoints, eval reports | see `gitops/minio/minio.yaml` |
| RHOAI Data Science Pipelines | Runs `pipeline/act_flywheel_pipeline.py` | RHOAI operator-managed |
| RHTAS (cosign + Rekor) | Signs and logs every promoted model image | `gitops/operators-config/securesign.yaml` |
| Model Registry | One durable record per promoted candidate | `gitops/operators-config/model-registry.yaml`, MariaDB-backed |
| Red Hat Edge Manager (flightctl 1.3) | Fleet + Catalog, device rollout | `argocd/rhem-app.yaml`, chart `quay.io/flightctl/charts` |
| OpenShift Pipelines (Tekton) | Builds + signs the multi-arch runtime image | `gitops/tekton/runtime-image-pipeline.yaml` |

## The training/promotion pipeline

Defined in [`pipeline/act_flywheel_pipeline.py`](../pipeline/act_flywheel_pipeline.py), run by RHOAI Data Science Pipelines. Stages, in order: `trigger_and_wait` → `eval_gate` → `package_modelcar` → `sign_modelcar` → `register_model` → `open_promotion_pr` → `record_pr_url`. See [`docs/DATA-FLOW.md`](DATA-FLOW.md) for what each stage does.

## Sim / producer

Runs as host containers alongside the box hosting the GPU (desktop today, the Fury host once ported) — not in the cluster. Built from [`docker/Dockerfile`](../docker/Dockerfile) ("Based on upstream `ros-physical-ai/demos` Dockerfile. Adds rmw_zenoh_cpp, the episode-emitter node, flywheel volume mounts").

| Container | Role | Notes |
|---|---|---|
| `so-arm-sim` | Gazebo + SO-ARM101, camera bridge, episode emitter, sim reset | image tag `sim-only`, built locally on the host — not currently pushed to a registry (see `gitops/flywheel/so-arm-sim.yaml`'s comment; the in-cluster copy of this manifest is scaled to 0 replicas and is not what actually runs) |
| `pose-ui` | Rest-pose picker / live joint + camera view for the operator | `src/pose-ui/pose_ui.py` |

The **coordinator** (episode lifecycle, ground-truth scoring, recording) and the **host runner**
(the desktop's GPU-outside-the-cluster shim for the pipeline's train/eval stages, `src/host-runner/host_runner.py`) run directly on the host, not containerized — started via `tools/host/run-coordinator.sh` and a resident Python process respectively.

## The managed device

Runs **one** container, delivered as an image volume by the RHEM Fleet — not built or started by hand. Built from [`docker/Dockerfile.gpu-inference`](../docker/Dockerfile.gpu-inference), by OpenShift Pipelines (Tekton), multi-arch (amd64 + arm64), signed and referenced by digest in `gitops/rhem/fleet-act-inference.yaml`.

| | Desktop stand-in | Fury |
|---|---|---|
| What the device *is* | Separate RHEL 10 KVM VM (`act-device`) | The Fury host itself |
| Served on | CPU | GPU |
| Enrolled via | `device/enroll.sh` | `device/provision.sh` (arch-neutral; installs the flightctl agent, podman, NVIDIA CDI) |

## What runs where — quick reference

| Role | Desktop stand-in (today) | Fury (target) |
|---|---|---|
| Hub (SNO) | KVM VM on the desktop | fresh KVM VM on the Fury host |
| Managed device | separate RHEL 10 VM | the Fury host itself |
| Sim + coordinator + host runner | desktop host, direct on the GPU | Fury host, direct on the GPU |
| Presenting laptop | operator's laptop — browser, phone, `ssh`/`gh` client; no cluster access of its own | same role, same machine, at the venue |

## Credentials and secrets

None of the values below are ever written into git. What exists, where, and how to (re)create it
is documented in [`argocd/README.md`](../argocd/README.md)'s "Hand-created Secrets" table:
MinIO root credentials, hub S3 credentials, the cosign signing key, a GitHub token for opening
promotion PRs, and the Model Registry database password. Each is created once, by hand, before the
Argo app that consumes it first syncs.

## A note on drift

This page reflects a point-in-time check against the running desktop system. Two things worth
knowing if you're reading this expecting it to be perfectly current: the `minio` Argo app can show
`OutOfSync` between image-pin updates and MinIO's own Job objects reconciling (a Kubernetes Job's
pod template is immutable post-creation, so a pinned-image update to an already-completed Job needs
that Job deleted before it reapplies cleanly — see `DECISIONS.md` for the specific incident); and
`so-arm-sim`'s in-cluster manifest is intentionally not what's live (see the sim/producer table
above). Both are documented, not hidden — if something here looks stale, `argocd/README.md` and
`DECISIONS.md` are the sources of truth to re-check against.
