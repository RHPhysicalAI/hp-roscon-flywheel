<!-- This project was developed with assistance from AI tools. -->
# gitops/ — what the hub runs, by directory

A directory here is the source of exactly one renderer, by path. Nine are rendered by Argo CD: each
`argocd/*-app.yaml` Application points at one sub-directory, with `prune: true` and `selfHeal: true`, so the
cluster equals the directory. Two are rendered by Red Hat Edge Manager itself. Nothing renders `gitops/` as a
whole, which is why this page can live here. Bootstrap order, hand-created Secrets and the objects Argo CD will
not manage: [`argocd/README.md`](../argocd/README.md).

**Do not move or rename these directories**: the running system follows them by path.

## Rendered by Argo CD

| Directory | Argo CD Application | What it deploys |
|---|---|---|
| `storage/` | `storage` (`argocd/storage-app.yaml`) | the node-local storage provisioner and the default StorageClass `local-path` |
| `operators/` | `operators` (`argocd/operators-app.yaml`) | OLM Subscriptions: Red Hat OpenShift Pipelines and Red Hat OpenShift AI - [`operators/README.md`](operators/README.md) |
| `operators-config/` | `operators-config` (`argocd/operators-config-app.yaml`) | what turns those operators on: the `DataScienceCluster`, the pipelines server, the Model Registry and its database - [`operators-config/README.md`](operators-config/README.md) |
| `minio/` | `minio` (`argocd/minio-app.yaml`) | object storage for episodes, datasets, checkpoints and evaluation records, and the read-only user the evaluation pages use |
| `flywheel/` | `flywheel` (`argocd/flywheel-app.yaml`) | the flywheel's hub side: the curator, sync agent, Kafka, the training trigger (`manifest-consumer.yaml`), the live lane's curator, the flywheel page, the three evaluation pages, and the Services and Routes in front of the GPU host's assistant, cameras and fleet wall - [`flywheel/README.md`](flywheel/README.md) |
| `observability/` | `observability` (`argocd/observability-app.yaml`) | the monitoring stack, the scrapes of the GPU host's exporters, Perses and the GPU tenants dashboard |
| `tekton/` | `tekton` (`argocd/tekton-app.yaml`) | the Tasks and the Pipeline that build and sign this project's images, and the transparency log's public key. `*.example.yaml` is excluded from the sync: the PipelineRun template is started by hand |
| `devspaces/` | `devspaces` (`argocd/devspaces-app.yaml`) | the `CheCluster` behind the coding tenant's workspace |
| `fleet-worlds/` | `fleet-worlds` (`argocd/fleet-worlds-app.yaml`) | the robots' worlds: namespace `fleet` and `StatefulSet/world`, one physics-only sim pod per robot. The app ignores `replicas`: `tools/hub/fleet-worlds-scale.sh` sets the number |

Red Hat Edge Manager's own hub components are an Argo CD Application too (`rhem`, `argocd/rhem-app.yaml`), but
its source is Red Hat's Helm chart, not a directory here.

## Rendered by Red Hat Edge Manager, not by Argo CD

Fleets and catalog items are Edge Manager API objects, not Kubernetes objects. Edge Manager has its own GitOps
loop for them - a `Repository` and two `ResourceSync`s, applied once from [`rhem/bootstrap/`](../rhem/bootstrap/README.md).

| Directory | ResourceSync | What it holds |
|---|---|---|
| `rhem/` | `rhem-fleets` (`rhem/bootstrap/resourcesync.yaml`) | the two Fleets: `act-inference` (the GPU host) and `robots` (the twelve micro-VM robots) - [`rhem/README.md`](rhem/README.md) |
| `rhem-catalog/` | `rhem-catalog` (`rhem/bootstrap/resourcesync-catalog.yaml`) | the catalog item that records the promoted model versions as a version graph |

A sync renders **every** `.yaml`, `.yml` and `.json` file in its directory as a resource, and deletes what it
owns once the file is gone: add no other file to these two. The model digest and `MODEL_VERSION` in the Fleet
files, and the catalog's version list, are edited by promotion pull requests, not by hand.

## Not rendered

`edge-workloads/` holds one Namespace manifest that no Application points at.
