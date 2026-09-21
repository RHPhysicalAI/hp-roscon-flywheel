<!-- This project was developed with assistance from AI tools. -->
# The hub

Single-node Red Hat OpenShift 4.22 in a KVM virtual machine on the workstation: 32 vCPUs, 128 GiB, UEFI. It is the
control point of the system, and everything on it comes from git. Part of the [architecture](../ARCHITECTURE.md).

## What runs on it

| Job | Component |
|---|---|
| Delivery from git | Red Hat OpenShift GitOps (Argo CD) |
| Training and promotion pipeline, model records | Red Hat OpenShift AI 3.5: Data Science Pipelines, Model Registry |
| Image builds and signing | Red Hat OpenShift Pipelines, cosign, a Rekor transparency log |
| Device management | Red Hat Edge Manager 1.3.0 |
| Curation and the trigger | two curators, the sync agent, Kafka, object storage, the training trigger |
| The robots' physics | StatefulSet `world`, one pod per robot |
| The coding workspace | Red Hat OpenShift Dev Spaces |
| Metrics and dashboards | Prometheus from the Cluster Observability Operator, Perses |
| Pages | the flywheel page, three evaluation pages, the Route in front of the fleet wall |

## GitOps, application by application

Each `argocd/*-app.yaml` is applied once by hand. After that Argo CD keeps the cluster equal to the directory, with
prune and self-heal. Order and dependencies: [`argocd/README.md`](../../argocd/README.md).

| Argo CD app | Source | What it delivers |
|---|---|---|
| `storage` | `gitops/storage/` | the node-local storage class |
| `operators` | `gitops/operators/` | OpenShift AI (channel `stable-3.5`) and OpenShift Pipelines |
| `operators-config` | `gitops/operators-config/` | the `DataScienceCluster`, the pipelines server, the Model Registry |
| `minio` | `gitops/minio/` | object storage, and a read-only user for the evaluation pages |
| `flywheel` | `gitops/flywheel/` | both curators, the sync agent, Kafka, the training trigger, the flywheel page, the evaluation pages, the Services and Routes in front of the GPU host's tenants |
| `fleet-worlds` | `gitops/fleet-worlds/` | the robots' physics worlds |
| `observability` | `gitops/observability/` | Prometheus, the scrapes of the GPU host, Perses and the GPU tenants dashboard |
| `rhem` | Helm chart `redhat-rhem` 1.3.0 | Red Hat Edge Manager |
| `tekton` | `gitops/tekton/` | the image build-and-sign pipeline |
| `devspaces` | `gitops/devspaces/` | the Dev Spaces instance |

Four things are applied by hand, once: four operator subscriptions that come first (`argocd/bootstrap-operators.yaml`);
the Secrets, which are never in git; three EndpointSlices that point Services at the GPU host (`tools/hub/manual/`);
and the transparency log (`tools/host/fury/rhtas-arm64/`).

## The Fleets have their own sync

Fleets and catalog items are Edge Manager API objects, not Kubernetes resources. Edge Manager syncs them from git
itself: a `Repository` and two `ResourceSync`s, applied once from `rhem/bootstrap/`, render `gitops/rhem/` (the two
Fleets) and `gitops/rhem-catalog/` (the model's version graph). A sync renders every YAML file in its directory, so
those two directories hold nothing else. More: [`gitops/README.md`](../../gitops/README.md).

## Networks and ports

One routed libvirt network, `fury-net` (`10.20.0.0/24`), without DHCP: the host is `10.20.0.1`, the hub `10.20.0.10`,
robot `fleet-vm-NN` is `10.20.0.(20+NN)`. The host's dnsmasq answers the cluster's names. firewalld's `libvirt-to-host`
policy rejects every connection from a guest to the host that it does not list. Operators reach the network over a
tailnet route.

| Where | Port | What listens | Who reaches it |
|---|---|---|---|
| host `10.20.0.1` | 53 | dnsmasq: the cluster's names | guests, the tailnet, the host |
| host `10.20.0.1` | 8000/tcp | coding assistant API and metrics | workspaces through Service `assistant`; the hub's Prometheus |
| host `10.20.0.1` | 9400/tcp, 9401/tcp | DCGM and tenant metrics exporters | the hub's Prometheus |
| host `10.20.0.1` | 9701/udp | renderer: robot state in | world pods; robot zero's world |
| host `10.20.0.1` | 9702/tcp | renderer: pictures and status | Route `fleet-wall`; robot zero's frame bridge; the metrics exporter |
| host, all addresses | 7447/tcp | robot zero's Zenoh router | local clients |
| hub `10.20.0.10` | 443, 6443 | every Route - pages, Edge Manager, Argo CD, the log - and the OpenShift API | browsers and CLIs over the tailnet; device agents |
| hub `10.20.0.10` | 30812 | `curator-show` | the GPU host only |
| hub `10.20.0.10` | 30802, 30900, 30903 | `curator`, object storage, Kafka | the episode loop and the training runner |
| robot `10.20.0.(20+NN)` | 22, 7447/tcp | sshd; the robot's Zenoh router | 7447 from the hub's address only |

The tenants' listeners bind the host's hub-side address only. The coding assistant's API has no key, so it has a
Service and no Route: its clients are pods. Routes use the cluster's self-signed certificate.
