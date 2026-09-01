# Phase 0 — Desktop Foundation

**Goal:** SNO running on the Ubuntu desktop, RTX 5090 schedulable as a GPU resource, resource
headroom quantified. This phase answers the first-order question: can the flywheel's platform
layer and GPU workloads coexist on one node?

## Desktop specs

- **CPU:** Intel i9-13900K (x86_64, 24 cores / 32 threads)
- **RAM:** 128 GB
- **GPU:** NVIDIA RTX 5090 (discrete PCIe, own VRAM)
- **OS:** Ubuntu (version TBD — confirm on first SSH)
- **Network:** on Jeremy's local network

## Step 1 — Inventory the desktop

SSH to the desktop and confirm:

- [ ] OS version and kernel (`uname -a`, `cat /etc/os-release`)
- [ ] NVIDIA driver and CUDA version (`nvidia-smi`, `nvcc --version`)
- [ ] GPU details (`nvidia-smi -q | head -40`)
- [ ] Free disk space (`df -h` — SNO needs ~100 GB for images + etcd + OCP data, plus storage
  for models, rosbags, episodes, MinIO data)
- [ ] IOMMU status (`dmesg | grep -i iommu` — needed for GPU passthrough if we go that route)
- [ ] Existing GPU processes (`nvidia-smi` — anything running that would conflict?)
- [ ] Available memory layout (`free -h`)
- [ ] Network config (`ip addr` — SNO needs a stable IP)

## Step 2 — Stand up Single-Node OpenShift (SNO)

Options for bare-metal SNO on Ubuntu/x86_64:

### Decision: SNO in a KVM VM with GPU passthrough (Ubuntu host preserved)

Ubuntu stays. SNO runs in a KVM VM with the RTX 5090 passed through via VFIO.

- Keep Ubuntu as the host OS
- Create a KVM VM with sufficient resources (16+ cores, 64+ GB RAM, 200+ GB disk)
- VFIO passthrough the RTX 5090 to the VM so SNO sees real GPU
- Install SNO inside the VM via Assisted Installer (pull secret from console.redhat.com)
- VFIO passthrough of the 5090 should work — it's a discrete PCIe GPU with proper IOMMU groups
  (unlike the Thor's integrated GPU which can't be passed through)

**Fallback:** if SNO overhead inside the VM leaves insufficient resources for workloads, fall
back to MicroShift running directly on Ubuntu (lighter weight, less representative of the
"OpenShift platform" story, but functional).

**Verify after install:**
- [ ] `oc get nodes` — single node, Ready
- [ ] `oc get co` — all cluster operators Available
- [ ] `oc get pods -A` — no crashlooping system pods

## Step 3 — GPU Operator

Install the NVIDIA GPU Operator via OperatorHub:

- [ ] GPU Operator installed and pods running
- [ ] `oc get node -o json | jq '.items[].status.allocatable'` — shows `nvidia.com/gpu: 1`
- [ ] Smoke test: run a `cuda-vectoradd` pod, confirm it exits 0

## Step 4 — Baseline resource budget

With SNO + GPU Operator running (no workloads), measure:

- [ ] `oc adm top nodes` — CPU and memory consumed by the platform
- [ ] Remaining CPU/RAM available for workloads
- [ ] GPU memory consumed by the Operator driver pods vs. total VRAM
- [ ] Document the contention picture

**Decision gate:** if SNO leaves < 64 GB RAM free for workloads, fall back to MicroShift and
document the reasoning.

## Exit criteria

- [ ] SNO (or MicroShift) running on the desktop
- [ ] `nvidia.com/gpu: 1` schedulable
- [ ] Resource budget documented
- [ ] Decision on SNO vs. MicroShift recorded with rationale

## What comes next (Phase 1)

Deploy the thor-testing hub-plane manifests (Argo CD, MinIO, Kafka, RHTAS, observability) onto
the running cluster. See `desktop-fury-buildout-plan.md` for full Phase 1-4 details.

## Reference

- thor-testing DECISIONS.md (~D037) — solved aarch64/Blackwell gotchas, many applicable to GPU
  Operator / container runtime on this desktop
- thor-testing DEPLOYMENT_GUIDE.md — the original two-tier deployment; we're collapsing to one
- concept-flywheel-on-fury.md — the single-box architecture we're proving
