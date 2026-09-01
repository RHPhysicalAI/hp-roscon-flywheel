# Decisions Log

## D001 — Defer VFIO GPU passthrough to Phase 2-3

**Date:** 2026-09-01
**Context:** Phase 0 calls for VFIO passthrough of the RTX 5090 into the SNO VM so the GPU
Operator can expose `nvidia.com/gpu: 1`. However, Phases 0-1 require no GPU workloads — the
hub plane (Argo CD, MinIO, Kafka, RHTAS, observability) is entirely CPU-bound. VFIO passthrough
would make the desktop headless (Intel iGPU is disabled in BIOS, no display output without the
5090) and kill the active desktop session (Xorg, GNOME, Steam, VS Code, etc.).

**Decision:** Stand up SNO without GPU passthrough. Defer VFIO binding until Phase 2-3 when
training/inference workloads actually need GPU inside the cluster. The desktop stays fully
functional as a daily-use machine in the meantime.

**Trade-off:** Phase 0 exit criteria for `nvidia.com/gpu: 1` schedulable and `cuda-vectoradd`
smoke test are deferred. Everything else — SNO running, network accessible, resource baseline —
is met without the GPU.

**Revisit:** When Phase 2 (SO-ARM producer) or Phase 3 (LeRobot ACT training) needs GPU
scheduling inside OpenShift. At that point: add `intel_iommu=on iommu=pt` to GRUB, bind
`vfio-pci` for `10de:2b85` (VGA) and `10de:22e8` (Audio) in IOMMU group 18, reboot headless,
pass GPU to VM.

---

## D002 — Desktop IP changed from 10.0.0.4 to 10.0.0.48 after bridge setup

**Date:** 2026-09-01
**Context:** Creating the `br0` network bridge for KVM VM bridged networking required moving
`eno2` under the bridge. The bridge got a new DHCP lease at `.48` instead of the original `.4`.

**Decision:** Accept the DHCP-assigned IP. The desktop is now at `10.0.0.48` and the SNO VM
is at `10.0.0.49`. Both are DHCP — consider setting DHCP reservations on the router if IP
stability becomes important.

**Impact:** SSH config and any scripts referencing the desktop IP need to use `.48` (or the
Tailscale IP `100.76.33.18` which is stable).

---

## D003 — SNO VM resource allocation

**Date:** 2026-09-01
**Context:** Desktop has 125 GiB RAM, i9-13900K (24 cores / 32 threads). Need to size the
SNO VM large enough for the full flywheel workload while keeping the host functional.

**Decision:**
- **vCPUs:** 16 (host-passthrough) — leaves 16 threads for host desktop use
- **RAM:** 72 GiB (73728 MiB) — leaves ~53 GiB for host
- **Disk:** 250 GB qcow2 thin-provisioned on root NVMe (1.1 TB free)
- **Network:** bridged to `br0`, VM at 10.0.0.49 via DHCP

**Measured baseline (SNO idle, no workloads):**
- Platform CPU: 1264m / 15500m allocatable (8%)
- Platform memory: 12677 MiB / ~71 GiB allocatable (17%)
- Remaining for workloads: ~14 vCPUs, ~59 GiB RAM
- Host memory with VM running: 93 GiB available out of 125 GiB

**Decision gate check:** 59 GiB available inside the VM for workloads, well above the 64 GB
threshold in the build plan. No need to increase VM allocation or fall back to MicroShift.

---

## D004 — OCP version: 4.17.56

**Date:** 2026-09-01
**Context:** Matched the existing `oc` 4.17.11 client on the desktop. Used stable-4.17 channel.

**Decision:** OCP 4.17.56 installed via agent-based installer. RHCOS
417.94.202607240132-0, Kubernetes v1.30.14, CRI-O 1.30.14.

---

## D005 — Agent-based installer rendezvousIP must match VM's actual IP

**Date:** 2026-09-01
**Context:** First install attempt used `rendezvousIP: 10.0.0.50` in the agent-config, but
DHCP assigned the VM `10.0.0.49`. The assisted-service tried to bind to `.50`, which didn't
exist on the node, causing the agent to fail with "Connection refused" on port 8090.

**Decision:** Regenerated the ISO with `rendezvousIP: 10.0.0.49` matching the DHCP-assigned
address. The MAC address `52:54:00:f1:00:01` gets the same DHCP lease consistently, so this
works. For future reference: if using DHCP with the agent-based installer, the rendezvousIP
must match whatever the node actually gets.

---

## D006 — DNS: /etc/hosts for API access

**Date:** 2026-09-01
**Context:** The SNO cluster API is at `api.sno-flywheel.local:6443`. No DNS server on the
local network resolves this.

**Decision:** Added `10.0.0.49 api.sno-flywheel.local` to `/etc/hosts` on the desktop.
Same entry needed on any machine that wants `oc` access (e.g. Jeremy's Mac).

**Future:** If a wildcard is needed for routes (`*.apps.sno-flywheel.local`), either add
individual `/etc/hosts` entries per route or set up a lightweight DNS (dnsmasq) on the desktop.

---

## D007 — Dropped OSD/multi-cluster manifests for SNO

**Date:** 2026-09-01
**Context:** thor-testing used a two-tier topology (MicroShift-on-Thor as edge, OSD-on-AWS as
hub). Three manifest files assumed that topology and have no equivalent on single-node SNO.

**Dropped:**
- `flywheel/mirrormaker2.yaml` — bridged edge Kafka to hub OSD fleet Kafka over TLS
- `hub-training/manifest-consumer.yaml` — hub-side Kafka consumer that triggered training via
  Data Science Pipelines on the OSD hub
- `edge-workloads/smoke-test.yaml` — ACM/cluster-proxy delivery canary for thor

**Adapted:**
- `sync-agent.yaml` / `dreamer.yaml` — S3_ENDPOINT changed from OSD MinIO route to local
  `http://minio.minio.svc:9000`
- `dashboard.yaml` — kubeconfig fallback path changed from MicroShift to SNO
  (`/etc/kubernetes/kubeconfig`)

**New:**
- `minio/minio.yaml` — local MinIO deployment (was external on OSD in thor-testing)
- `flywheel/hub-credentials.yaml` — S3 credentials for local MinIO

---

## D008 — SELinux hostPath labeling on RHCOS

**Date:** 2026-09-01
**Context:** Flywheel services (curator, sync-agent) use hostPath volumes at `/var/lib/episodes`.
On RHCOS with SELinux enforcing, containers get MCS labels (e.g. `s0:c27,c9`) but files created
by the `core` user via SSH have no MCS categories (`s0`), causing permission denied errors even
with world-readable file permissions.

**Resolution:** Run `sudo chcon -Rt svirt_sandbox_file_t /var/lib/episodes/` on the node to
label the directory tree for container access. Must be re-applied if new directories are created
directly on the host.

**Future:** When the SO-ARM producer writes episodes from inside a pod, the files will inherit
the container's SELinux context automatically. This is only an issue for host-created test files.

---

## D009 — MinIO uses emptyDir (non-persistent) for Phase 1

**Date:** 2026-09-01
**Context:** The MinIO hostPath at `/var/lib/minio` failed with permission errors (same SELinux
issue as D008). Switched to emptyDir for fast iteration.

**Trade-off:** MinIO data is lost on pod restart. Acceptable for Phase 1 smoke testing. Switch
to a PVC or properly-labeled hostPath when data persistence matters (Phase 2+).

---

## D010 — Phase 1 smoke test results

**Date:** 2026-09-01
**Context:** Injected two dummy episodes matching the curator contract from THOR-TESTING-REUSE.md.

**Results:**
- Episode `smoke-test-001` (clean, task_success=true, smoothness=0.05):
  PASS score=0.800 -> curated -> uploaded to MinIO `episodes-curated/soarm-act-v1/smoke-test-001.json`
  -> manifest published to Kafka `episode-manifests` -> moved to `sent/`
- Episode `smoke-test-002` (has_failure=true):
  REJECT score=0.000 ("injected-failure") -> moved to `rejected/`

**Conclusion:** Full data path works end-to-end on SNO: sim -> curator -> sync-agent -> MinIO +
Kafka. All OSD-specific assumptions resolved. No code changes needed to curator or sync-agent
logic — only endpoint configuration.

---

## D011 — Perses TraceQuery panels incompatible with COO-managed Perses

**Date:** 2026-09-01
**Context:** The thor-testing Perses dashboard (`edge-flywheel-dashboard.yaml`) uses 5 panels
with `TraceQuery` type to query Tempo trace data. The Cluster Observability Operator's bundled
Perses frontend only ships with `TimeSeriesQuery` panel support — no trace panel plugin.

**Symptom:** All panels show "panel does not support queries of type 'TraceQuery'. Supported
types: TimeSeriesQuery."

**Decision:** Accept for now. The Tempo backend is working (Perses proxy returns 200 on all
trace queries), and the Jaeger UI route provides direct trace browsing. The dashboard panels
will need to be rebuilt for SO-ARM trace format in Phase 2 anyway — do that work then, either
as TimeSeriesQuery panels or by installing a standalone Perses with the trace plugin.

---

## D012 — Phase 1 complete: full service inventory

**Date:** 2026-09-01

**Services running on SNO (OCP 4.17.56):**

| Namespace | Service | Purpose |
|---|---|---|
| `minio` | MinIO | S3-compatible object store for episodes |
| `flywheel` | edge-kafka | KRaft single-broker Kafka |
| `flywheel` | curator | Episode quality scoring |
| `flywheel` | sync-agent | S3 upload + Kafka manifest publish |
| `flywheel` | dashboard | Flask UI (NodePort 30801 + Route) |
| `openshift-gitops` | Argo CD (8 pods) | GitOps delivery (no apps configured yet) |
| `observability` | Tempo (TempoMonolithic) | Distributed tracing backend |
| `observability` | Perses | Observability dashboard UI |

**Verified:** dummy episodes flow end-to-end through the pipeline.
**Desktop IP:** 10.0.0.48. **SNO VM IP:** 10.0.0.49.
**Not yet deployed:** robot-sim (replaced by SO-ARM in Phase 2), dreamer (needs GPU/Cosmos3),
vllm-cosmos3 blue/green (replaced by ACT serving in Phase 3), RHTAS/sigstore (Phase 3).
