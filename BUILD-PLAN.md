# Build Plan — Desktop-as-Fury Flywheel

## Goal

Prove the complete Physical AI Edge Flywheel with SO-ARM101 end-to-end on the Ubuntu desktop
(single-box, loop closed), so the Fury phase (Sept 20-25) is pure aarch64 scale-up, not debugging.

---

## Phase 0 — Desktop Foundation

**Goal:** SNO running in a KVM VM on the Ubuntu desktop, RTX 5090 passed through via VFIO and
schedulable as a GPU resource, resource headroom quantified.

### Desktop specs

- **CPU:** Intel i9-13900K (x86_64, 24 cores / 32 threads)
- **RAM:** 128 GB
- **GPU:** NVIDIA RTX 5090 (discrete PCIe, own VRAM)
- **OS:** Ubuntu (exact version TBD — confirm on first SSH)
- **Network:** 10.0.0.41 on Jeremy's local network, SSH as `jary`

### Step 1 — Inventory

SSH to `jary@10.0.0.41` and confirm:

- OS version and kernel (`uname -a`, `cat /etc/os-release`)
- NVIDIA driver and CUDA version (`nvidia-smi`, `nvcc --version`)
- GPU details (`nvidia-smi -q | head -40`)
- Free disk space (`df -h` — need ~300 GB: ~100 GB for SNO VM, ~100 GB for container images
  and models, ~100 GB headroom for episodes/MinIO/rosbags)
- IOMMU status (`dmesg | grep -i iommu`, check `/proc/cmdline` for `intel_iommu=on`)
- Existing GPU processes (`nvidia-smi` — anything GPU-bound that would conflict?)
- Memory layout (`free -h`)
- Network config (`ip addr` — need a stable IP; bridge config for KVM)
- KVM readiness (`kvm-ok` or `lsmod | grep kvm`, check if libvirt/qemu already installed)

### Step 2 — VFIO GPU passthrough setup

The RTX 5090 is a discrete PCIe GPU — VFIO passthrough should work cleanly unlike the Thor's
integrated GPU.

- Identify GPU PCI address (`lspci | grep -i nvidia`, `lspci -nn | grep -i nvidia` for vendor:device IDs)
- Check IOMMU group isolation (`find /sys/kernel/iommu_groups/ -type l` — the GPU's group should
  contain only the GPU and its audio function, no other devices)
- If IOMMU is not enabled: add `intel_iommu=on iommu=pt` to kernel boot args (GRUB), reboot
- Bind the GPU to `vfio-pci` (via `/etc/modprobe.d/vfio.conf` with the vendor:device IDs, and
  add `vfio-pci` to `/etc/modules-load.d/`)
- Blacklist `nvidia`/`nouveau` from binding the GPU on host boot (the GPU goes to the VM, not
  the host)
- **Important:** if Jeremy uses the 5090 for anything on the Ubuntu host currently (display,
  xlerobot, CUDA work), this will take the GPU away from the host. Confirm this is acceptable
  before proceeding. If the desktop has integrated Intel graphics, the host can use those for
  display while the 5090 goes to the VM.

### Step 3 — Create KVM VM for SNO

Install KVM/libvirt/QEMU if not already present:
```
sudo apt install qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst
```

Create the VM:
- **CPU:** 16+ vCPUs (host-passthrough mode)
- **RAM:** 72 GB (leaves ~56 GB for host + other processes — adjust based on inventory)
- **Disk:** 250 GB qcow2 (thin provisioned)
- **Network:** bridged to the host's network so the VM gets its own IP on 10.0.0.x
- **GPU:** RTX 5090 passed through via VFIO (`--host-device` in virt-install, or XML `<hostdev>`)

Install SNO via Assisted Installer:
- Generate discovery ISO from console.redhat.com (Jeremy provides pull secret)
- Boot the VM from the ISO
- Configure: single-node, static IP on the bridge, cluster name, base domain
- Let it install RHCOS + SNO
- After install: `export KUBECONFIG=<path>` and verify

### Step 4 — GPU Operator

Install NVIDIA GPU Operator via OperatorHub on the SNO cluster:

- The GPU Operator installs its own driver stack inside the cluster — the VFIO-passed GPU
  will be picked up as a bare PCIe device and the Operator will install drivers in-cluster
- Verify: `oc get node -o json | jq '.items[].status.allocatable'` shows `nvidia.com/gpu: 1`
- Smoke test: run a `cuda-vectoradd` pod, confirm exit 0

### Step 5 — Baseline resource budget

With SNO + GPU Operator running (no workloads):

- `oc adm top nodes` — CPU and memory consumed by platform
- GPU memory consumed by Operator driver pods vs. total VRAM
- Document the contention picture in DECISIONS.md

**Decision gate:** if < 64 GB RAM remains available for workloads inside the VM, consider either
increasing VM RAM allocation (if host can spare it) or falling back to MicroShift on bare Ubuntu.

### Exit criteria

- [ ] SNO running in KVM VM on the desktop
- [ ] RTX 5090 passed through and `nvidia.com/gpu: 1` schedulable
- [ ] `cuda-vectoradd` pod exits 0
- [ ] Resource budget documented in DECISIONS.md
- [ ] Network: VM accessible from Jeremy's Mac for `oc` / dashboard access

---

## Phase 1 — Port the Hub Plane onto SNO

**Goal:** thor-testing flywheel plumbing round-trips on x86 SNO with a smoke-test producer.

1. **Copy manifests** from `~/redhat/git/thor-testing/gitops/` into this repo's `gitops/`
   directory. See `THOR-TESTING-REUSE.md` for what copies and what adapts.

2. **Deploy hub-plane services** to SNO:
   - Argo CD (GitOps operator)
   - MinIO (object storage)
   - Kafka via AMQ Streams (`edge-kafka.yaml`)
   - RHTAS / sigstore trust plane (cosign keypair, Rekor)
   - KServe
   - Perses + Tempo + OTel collector (`observability/`)
   - Flywheel namespace + services: curator, sync-agent, dashboard (`flywheel/`)

3. **Adapt OSD-AWS-hub -> single-node deltas:**
   - Drop RHACM + cluster-proxy tunnel — Argo syncs locally; no edge<->hub tunnel needed
   - Drop `flightctl-agent` (was an edge-device concern)
   - Update OCP route / domain references to the VM's hostname / `*.apps.<cluster>.<domain>`

4. **Smoke test** with a minimal producer (write dummy episode JSON to the raw dir):
   - Curator passes/rejects episodes
   - Sync-agent uploads curated episodes to MinIO
   - Kafka manifest published
   - Dashboard shows episode counters
   - Confirm nothing is OSD-cluster-specific that breaks on bare-metal SNO

5. **Document decisions** in DECISIONS.md.

### Exit criteria

- [ ] Hub-plane services running on SNO
- [ ] Dummy episodes flow through curator -> sync-agent -> MinIO -> Kafka
- [ ] Dashboard accessible
- [ ] All OSD-specific assumptions identified and resolved

---

## Phase 2 — Build the SO-ARM Producer

**Goal:** SO-ARM101 Gazebo sim generates curator-compatible episodes; curator sorts good/bad;
curated data reaches MinIO + Kafka.

1. **Get SO-ARM running:** Pull `github.com/ros-physical-ai/demos`, run the SO-ARM101
   "place cubes on tray" in Gazebo with the pre-trained ACT policy and pre-recorded rosbags.
   Understand what a rollout produces (joint states, task success, action chunks, timing).

2. **Containerize** the SO-ARM sim as a Deployment replacing `robot-sim`:
   - ROS 2 + Gazebo + SO-ARM URDF/mesh + pre-trained ACT policy
   - GPU access via NVIDIA device plugin
   - `/data/episodes/raw` volume mount (same hostPath as existing plumbing)

3. **Write the episode-emitter adapter:** each ACT rollout -> episode JSON matching the
   curator contract defined in `THOR-TESTING-REUSE.md`. Include the `FAILURE_RATE` injection
   mechanism.

4. **Retune curator thresholds** for ACT rollout quality signals. Add task-success gate.

5. **Verify the full data path:** sim rollout -> episode JSON -> curator sorts -> curated
   episodes in MinIO -> Kafka manifest published.

6. **Gazebo visualization:** get the sim observable in a browser (Olga evaluating
   gz-camera-stream + rhork — APPENG-6261; integrate her findings here).

### Exit criteria

- [ ] SO-ARM sim generates episodes in the curator-compatible schema
- [ ] Curator sorts good/bad correctly
- [ ] Curated data reaches MinIO + Kafka
- [ ] Arm visible in a browser (via streaming or alternative)

---

## Phase 3 — Training + Close the Loop

**Goal:** LeRobot ACT fine-tune pipeline closes the loop; v2 policy demonstrably better; signed
and promoted via GitOps.

1. **Build the LeRobot ACT fine-tune KFP pipeline:**
   - Input: curated episodes from MinIO
   - Train: `lerobot.scripts.train` with ACT config on the RTX 5090
   - Eval: gate on task-success-rate improvement vs. v1
   - Package: `crane append` to build a signed KServe modelcar OCI image
   - Sign: cosign v2.4.1 with RHTAS keypair (reuse Tekton sign task)
   - Promotion PR: same mechanism as thor-testing

2. **Wire blue/green hot-swap** of the served ACT policy. Mirror the `vllm-cosmos3`
   service-selector-flip pattern.

3. **Produce v1->v2 comparison artifact:** record before/after task-success rates, smoothness
   distributions, RViz captures. This is the "you can see it get better" demo moment.

4. **Close the loop:** v2 policy in sim produces better rollouts that score higher in the
   curator, feeding a healthy flywheel visible in the dashboard.

### Exit criteria

- [ ] Training pipeline runs end-to-end
- [ ] v2 policy signed, promoted via GitOps, blue/green swapped
- [ ] v1 vs v2 improvement demonstrable
- [ ] Full loop closes (sim -> curate -> train -> sign -> promote -> sim)

---

## Phase 4 — Demo Hardening + Fury Prep

**Goal:** Demo-ready on desktop; arm64 images build; Fury porting checklist ready.

1. **Adapt the demo runbook** — re-skin the 6-beat narrative for SO-ARM:
   - Beat 1: "Here's the sim — SO-ARM placing cubes, running a trained policy"
   - Beat 2: "The curator is watching — this is the curation stream"
   - Beat 3: "Training started from the curated data — here's the pipeline"
   - Beat 4: "Model improvement — v1 vs v2 side-by-side"
   - Beat 5: "Promotion — signed, GitOps PR, blue/green swap"
   - Beat 6: "The loop closes — same governed pipeline you'd run to a real fleet"
   - Short Cut (~4-5 min): pinned run, pre-loaded v1/v2 comparison
   - Full Live (~10-12 min): live training + promotion

2. **Record a fallback run** — clean end-to-end captured on the desktop for venue-link /
   Fury-slip insurance. Non-negotiable.

3. **Multi-arch image prep:** ensure every custom image builds for `linux/amd64` and
   `linux/arm64`. Document every x86-specific assumption.

4. **Fury porting checklist:**
   - Container stack builds/runs on aarch64 Blackwell
   - Full flywheel loop at small scale
   - SO-ARM sim + RViz on aarch64
   - ACT policy serves on aarch64
   - All x86 assumptions resolved

### Exit criteria

- [ ] Demo-ready on desktop with runbook
- [ ] Fallback recording captured
- [ ] arm64 images build
- [ ] Fury porting checklist written

---

## Open questions (resolve during execution)

| Question | Phase | Notes |
|---|---|---|
| Desktop IOMMU / VFIO group cleanliness for the 5090 | 0 | If dirty group, need ACS override patch |
| SNO VM resource allocation vs. host headroom | 0 | How much RAM/CPU to give the VM vs. keep for host |
| Does Jeremy use the 5090 on the host currently? | 0 | VFIO takes it away from the host |
| Which OSD-hub manifests need rework vs. drop cleanly | 1 | RHACM / cluster-proxy are the main suspects |
| SO-ARM episode data shape (exact fields, success signal) | 2 | Depends on what the sim/policy actually output |
| Gazebo streaming approach (gz-camera-stream or alternative) | 2 | Olga evaluating (APPENG-6261) |
| LeRobot ACT eval metric / promotion gate threshold | 3 | Task-success-rate delta likely |
| zenoh middleware inside OpenShift pod network | 2 | Does rmw_zenoh work inside the cluster? |
