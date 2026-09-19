# Fury execution plan — flywheel + four GPU tenants on one HP ZGX Fury

<!-- This project was developed with assistance from AI tools. -->

Written 2026-09-18 from a full sweep of the repo plans, a read-only inventory of the real
machine, and NVIDIA primary sources. This supersedes the Fury-specific parts of
`docs/FURY-SETUP.md` where they conflict (called out inline). `docs/internal/DECISIONS.md`
stays the decision log — record new decisions there as D139+.

**Goal.** Stand the governed flywheel up on the Fury, then show the machine's power: the GPU
partitioned into four isolated tenants running simultaneously, a large model served on one of
them, a fleet of managed devices on the same box, and real GB300 training numbers. HP's
priority is the power of the system; Red Hat's is that every tenant runs on the governed
platform. Nothing in this plan involves any machine other than the Fury (and laptops as
clients). Isaac Sim is out: NVIDIA does not support it on DGX Station GB300 and MIG on this
silicon has no graphics profiles.

---

## 1. The machine, as found (2026-09-18)

| | |
|---|---|
| Chassis / OS | NVIDIA DGX_Station_GB300 (HP ZGX Fury), RHEL 10.2, kernel `6.12.0-211.56.1.el10_2.aarch64+64k` (**64 KiB pages**, switched from 4k the same day) |
| GPU | GB300, driver 610.57.04, CUDA 13.4, cuDNN 9.26, NCCL 2.31; 250 GB HBM; persistence on; **MIG supported, currently disabled**; fabricmanager fails (expected, single GPU) |
| CPU / RAM | 72× Neoverse-V2, 492 GB; `/dev/kvm` present; 40 IOMMU groups |
| Disks | `nvme2n1` (root LVM: `/` 70 GB, swap 32 GB) + `nvme3n1` → `/home` 3.6 TB; **`nvme1n1` 3.7 TB blank**; **`nvme0n1` 3.7 TB ext4 labelled `models`, already in use for something else — do not touch without asking** |
| Network | one lab uplink on `eno3` (address held by the operator); two 400 GbE ports unconnected; on the operator's tailnet with Tailscale SSH, MagicDNS on, traffic relayed; NTP synced |
| Software | podman 5.8.2 (rootless storage in `/home`); **no libvirt / qemu-kvm / virt-install**; **no nvidia-container-toolkit, no CDI spec**; GDM + GNOME running (workstation image); SELinux enforcing; firewalld on; hostname unset |
| Egress | the registries, mirrors and package sources this plan needs are reachable from the machine (dnf resolves aarch64 AppStream) |
| Account | one shared login account; **every privileged step is run by the operator**; the machine is shared with partner staff, and the NVIDIA stack was installed the same day |

**State at 2026-09-18 21:05 UTC (after the operator's first pass):** MIG mode **Enabled** (current and
pending) with **no GPU instances created yet**; `sleep/suspend/hibernate/hybrid-sleep.target` **masked**;
`gdm` **stopped** but the default target is still `graphical.target` (so set `multi-user.target` and
`disable gdm` remain to do); hostname still unset; `nvidia-fabricmanager` still enabled; no
`nvidia-container-toolkit`, no CDI spec, no MIG boot-persistence unit, no `/data`, no libvirt. Phase 0
therefore starts at step 1 with the suspend items already done, and step 4 starts at `-cgi`.

**Re-check 2026-09-18 21:10 UTC (read-only, before Phase 0; details in D139).** Unchanged: kernel, MIG
Enabled with no instances, sleep targets masked, default target still `graphical.target`, hostname
unset, `nvidia-fabricmanager` enabled + failed, no toolkit / CDI / libvirt / `/data`, sudo still needs a
password, up since 18:47 UTC with no reboot. Corrections and new facts:

- `gdm` is now **disabled** as well as stopped; the last suspend attempt was 20:21:41 UTC, none since the masks.
- The lab uplink's prefix was first recorded wrongly (corrected; the address itself is not kept in this repo). `nvidia-smi` reports CUDA UMD 13.3.
- **NVMe names are enumeration order, not identity.** The two 3.7 TB disks are the same model; blank =
  one by its serial number (today `nvme1n1`), `models` by its own (today `nvme0n1`). Phase 0
  step 2 addresses the blank disk by `/dev/disk/by-id` and refuses to run if it finds any signature.
- `sda` is a 2.7 GB USB "File-Stor Gadget" carrying the RHEL 10.2 BaseOS ISO — BMC virtual media; leave it.
- Already installed: `dnsmasq` 2.90, `jq`, `semanage`. Still missing: libvirt, qemu-kvm, virt-install,
  tmux, skopeo, nvidia-container-toolkit.
- **`xt_mark` is missing on the running 64k kernel** (`kernel-64k-modules-extra` is not installed; only
  the 4k `kernel-modules-extra-211.49.1` is). tailscaled's health check already reports its `ts-forward`
  MARK rule failing, so the subnet router in step 6 would not forward. Fix added to step 3.
- `/etc/resolv.conf` is owned by Tailscale (`100.100.100.100`). With split DNS pointing
  `sno-flywheel.local` back at this host, dnsmasq must answer that zone authoritatively or unknown names
  loop. Guard added to step 6, along with `api-int` (the SNO node needs it).
- Decision 5's fallback is cheap: NVIDIA's precompiled `kmod-nvidia-open` is installed for both page
  sizes of both kernel versions, and the 4k `211.49.1` kernel is still installed. `dnf-plugin-nvidia`
  filters kernel updates that lack a precompiled kmod.

**Known fault (now mitigated by the masks):** the GNOME greeter requests suspend every 15 idle minutes
(`sleep-inactive-ac-timeout=900`); the NVIDIA driver refuses (`nv_pmops_suspend returns -5`), the
attempt fails, consoles freeze briefly and tailscaled rebinds. Fixed in Phase 0 step 1.

---

## 2. Decisions (locked)

| # | Decision | Consequence |
|---|---|---|
| 1 | **MIG on, four tenants: `3g.126gb + 1g.31gb + 1g.31gb + 1g.31gb`** (profile IDs `9,19,19,19`) | Everything GPU-side is built against slices from day one. Names drift across driver branches — always use IDs |
| 2 | **GPU stays on the host; the SNO VM gets no GPU.** No vfio-pci, no nvidia blacklist | D001/D013 "VFIO is the eventual path" is superseded (D024/D025 already moved serving to the device, which on the Fury *is* the host) |
| 3 | **No Isaac Sim / Isaac Lab** (unsupported on GB300 aarch64; no `+gfx` MIG profiles; headless still initializes RTX) | Simulation stays Gazebo on CPU; "parallel sims" means Gazebo replicas on cores and training sweeps on 1g slices |
| 4 | **No other machines.** Fleet scaling = VMs on the Fury; no desktop cross-site rollout | Rollout batch 1 (`site=desktop`) must be retargeted to Fury-local canary devices |
| 5 | **Kernel: stay on 64k unless the arm64 runtime image or CUDA fails on it** (Phase 1 proves it before anything is built on top) | Switching = `dnf install kernel` (4k) + reboot; BMC available |
| 6 | **Storage:** `nvme1n1` → `/data` (ext4): rootful container storage, libvirt images, flywheel data | Root is 70 GB; the coordinator refuses to start under 100 GB free |
| 7 | **VM network `10.20.0.0/24`** (libvirt `fury-net`, routed, gateway `10.20.0.1`); SNO static `10.20.0.10`; fleet VMs `10.20.0.21+` | Never `10.0.0.0/24` — the desktop advertises it to the tailnet and Olga's contract pins `10.0.0.49` |
| 8 | **Cluster stays `sno-flywheel.local`**; DNS via **dnsmasq on the host**, served to VMs and to the tailnet via Tailscale split DNS | Keeps ~a dozen hardcoded names valid; no `/etc/hosts` on laptops |
| 9 | **Git branch `fury`** off `desktop-gpu-split`; the 9 Argo/ResourceSync files re-pointed to it | Fury IPs never land on the desktop's branch |
| 10 | **Fresh trust root on the Fury; re-sign everything there** (fresh cosign key, fresh Rekor/Fulcio, native arm64 Tekton build, one fresh promotion). **BLOCKER — needs the operator's call before Phase 3:** RHTAS ships **no arm64 images** through 1.4.3, operator included (verified 2026-09-18; brim `notes/rhtas-and-rhem-ui-no-arm64-images.md`), so `gitops/operators-config/securesign.yaml` cannot deploy on the aarch64 SNO. Options: (a) **upstream Sigstore (Rekor + Fulcio + CT log) in the Fury cluster** — consistent with "no other machines," costs the "Red Hat Trusted Artifact Signer" product name in the demo narrative (say "Sigstore, the upstream of RHTAS"); (b) keep RHTAS on the desktop x86 SNO as the central signing/Rekor service over the tailnet — violates decision 4; (c) sign with cosign keys only, no transparency log — weakens the trust story the negative tests prove. Raise a product ask to the RHTAS team either way | The desktop's Rekor key is embedded in the Fleet's `rekor.pub`; pinned images would fail verification on any fresh trust root |
| 10b | **RHEM UI 1.3.0 is amd64-only** (same note). The flightctl API/agent are arm64 | Phase 3 exit "RHEM UI up" needs either a multi-arch dev tag override for the UI image or the UI run off-cluster (e.g. as a container on a laptop pointed at the Fury API) |
| 11 | **`device/enroll.sh` expects passwordless sudo** on the device; asked of the machine's owner | Until then every privileged step is run by the human |
| 12 | **GDM off, `multi-user.target`, sleep targets masked** | Frees the GPU for MIG changes and kills the suspend loop |
| 13 | **GR00T N1.6 fine-tune is an optional "GR00T day"**: reslice to `3g + 2g + 1g` (IDs `9,14,19`) for the run, reslice back after | Reslicing takes a minute with the GPU idle; itself demo-worthy |

## 3. Tenant map

| Slice (creation order → CDI name) | Profile | Tenant | Runs as |
|---|---|---|---|
| `nvidia.com/gpu=0:0` | 3g.126gb | **T1 — large-model inference**: Red Hat AI Inference Server (vLLM) serving a robotics coding assistant (`gpt-oss-120b` MXFP4 — NIM-validated on GB300 — or `RedHatAI/Qwen3-Coder-Next-NVFP4`) | rootful podman quadlet on the host |
| `nvidia.com/gpu=0:1` | 1g.31gb | **T2 — flywheel policy serving** (the RHEM-managed device workload) | Fleet quadlet, `AddDevice=nvidia.com/gpu=0:1` |
| `nvidia.com/gpu=0:2` | 1g.31gb | **T3 — flywheel training + eval** (host runner: ACT fine-tune, paired eval) | host runner container, this slice |
| `nvidia.com/gpu=0:3` | 1g.31gb | **T4 — parallel sweeps / spare** (training sweeps, second eval, DCGM headroom) | ad hoc |

Verify the index→profile mapping with `nvidia-smi -L` and `nvidia-ctk cdi list` after creation;
do not assume it. vLLM rejects MIG UUIDs in `CUDA_VISIBLE_DEVICES` — give the container the
slice via CDI and leave `CUDA_VISIBLE_DEVICES` unset. PyTorch (host runner) accepts
`CUDA_VISIBLE_DEVICES=MIG-<uuid>`.

---

## 4. Phases

Each phase has an exit criterion. Do not start the next phase on a red exit. Record what was
learned in `DECISIONS.md` as you go.

### Phase 0 — Host preparation (all privileged)

1. **Stop the suspend loop and free the GPU**
   ```bash
   sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
   sudo systemctl set-default multi-user.target && sudo systemctl disable --now gdm
   sudo systemctl disable --now nvidia-fabricmanager
   ```
   The hostname stays unset for now (D139 addendum): nothing in the plan reads it and the tailnet name
   is pinned in Tailscale's own prefs. If it is wanted for how the device reads in the RHEM UI, set it
   just before Phase 4 enrollment, with HP's agreement, together with an `/etc/hosts` entry.
2. **Storage** — address the blank disk by identity, never by `nvmeXn1` (the `models` disk is the
   same model and the names can swap across a reboot). `wipefs -n` must print nothing first.
   ```bash
   D=/dev/disk/by-id/nvme-<model>_<serial-of-the-blank-disk>; sudo wipefs -n "$D"   # expect no output
   sudo mkfs.ext4 -L data "$D"
   sudo mkdir -p /data && sudo chattr +i /data   # a missed mount must fail loudly, not fill the 70 GB root (D137)
   echo 'LABEL=data /data ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
   sudo systemctl daemon-reload && sudo mount /data && findmnt /data
   sudo mkdir -p /data/containers /data/libvirt/images /data/flywheel
   # rootful podman storage → /data (bind keeps SELinux labels sane); nofail so a lost disk cannot drop boot to emergency
   sudo cp -a /var/lib/containers/. /data/containers/
   echo '/data/containers /var/lib/containers none bind,nofail,x-systemd.requires-mounts-for=/data 0 0' | sudo tee -a /etc/fstab
   sudo systemctl daemon-reload && sudo mount /var/lib/containers
   sudo semanage fcontext -a -e /var/lib/containers /data/containers
   sudo semanage fcontext -a -t virt_image_t "/data/libvirt(/.*)?"
   sudo restorecon -R /data /var/lib/containers
   ```
3. **Packages**
   ```bash
   sudo dnf install -y libvirt qemu-kvm virt-install libvirt-client dnsmasq tmux skopeo jq
   # xt_mark for tailscaled's subnet-router rules (absent on the 64k kernel without this)
   sudo dnf install -y "kernel-64k-modules-extra-$(uname -r | sed 's/\.aarch64+64k$//')" && sudo modprobe xt_mark
   # Red Hat ships the toolkit in the (already enabled) RHEL 10 Supplementary repo — no NVIDIA repo needed
   sudo dnf install -y nvidia-container-toolkit-1.20.0   # same version device/provision.sh pins
   # modular libvirt daemons (the RHEL 10 default); the monolithic libvirtd stays disabled
   for d in qemu network nodedev nwfilter secret storage interface; do sudo systemctl enable --now virt${d}d.socket; done
   ```
4. **MIG — enable, slice, persist** (GPU must be idle: `nvidia-smi --query-compute-apps=pid --format=csv` empty)
   ```bash
   sudo nvidia-smi -i 0 -mig 1                 # Hopper+: no reset needed; if "pending", nothing else may hold the GPU
   sudo nvidia-smi mig -cgi 9,19,19,19 -C      # 3g.126gb + 3× 1g.31gb, default compute instances
   nvidia-smi -L                               # record the four MIG UUIDs
   sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml && nvidia-ctk cdi list
   ```
   If `-mig 1` says "In use by another client": `sudo fuser -v /dev/nvidia*`, stop what it lists
   (desktop, services, containers), retry. If it shows **Pending**: wait and re-check, or reboot
   (NVIDIA's DGX Station MIG playbook, `nvidia/station-mig/README.md` in
   `github.com/NVIDIA/dgx-spark-playbooks`; it also mandates profile IDs over names).
   **CDI spec ownership (found 2026-09-18 after step 3):** Red Hat's toolkit build ships and enables
   `nvidia-cdi-refresh.{path,service}`, which writes `/var/run/cdi/nvidia.yaml` at boot — and
   `/var/run/cdi` outranks `/etc/cdi`. A hand-written `/etc/cdi/nvidia.yaml` would be shadowed by a
   boot-time spec generated *before* slicing. So `mig-config.service` orders itself
   `Before=nvidia-cdi-refresh.service` and no `/etc/cdi` file is written; after a manual reslice run
   `systemctl restart nvidia-cdi-refresh`. Scripts: `tools/host/fury/04-mig.sh`, `mig-config.{sh,service}`.
   MIG mode does **not** persist across reboot or driver reload on Hopper+. Install
   `/usr/local/sbin/mig-config.sh` (enable if disabled; create `9,19,19,19` if no instances;
   regenerate CDI) and a `mig-config.service` (`After=nvidia-persistenced.service`,
   `Before=virtqemud.service flightctl-agent.service`) that runs it. Test with a reboot.
5. **Smoke each slice** (proves CDI + MIG + aarch64 CUDA + 64k pages in one go)
   ```bash
   for i in 0 1 2 3; do sudo podman run --rm --device nvidia.com/gpu=0:$i nvcr.io/nvidia/cuda:13.0.0-base-ubi9 nvidia-smi -L; done
   ```
6. **Network + DNS**
   - libvirt network `fury-net`: `<forward mode='route'/>`, bridge `virbr-fury`, `10.20.0.1/24`, no libvirt DHCP (static guests). Outbound from guests uses the host's existing firewalld masquerade.
   - dnsmasq on the host, listening on `10.20.0.1` and `tailscale0`: `address=/api.sno-flywheel.local/10.20.0.10`, `address=/api-int.sno-flywheel.local/10.20.0.10`, `address=/.apps.sno-flywheel.local/10.20.0.10`, `local=/sno-flywheel.local/` (authoritative — the host's resolver is MagicDNS, which split-DNS points back here, so an unanswered name would loop), forward everything else upstream. Open DNS on the `trusted` (tailscale0) and libvirt zones.
   - Tailscale: `sudo tailscale set --advertise-routes=10.20.0.0/24`; approve the route; admin console → DNS → split DNS `sno-flywheel.local` → the Fury's tailnet address. Also uncomment the `autoApprovers` and subnet grants in the tailnet policy for `10.20.0.0/24`.
   - Verify forwarding tailnet→`10.20.0.0/24` actually passes (route mode avoids libvirt's NAT reject rules; if `fury-net` ends up NAT, add an nft accept for `tailscale0 → virbr-fury`).

**Exit:** four slices each run `nvidia-smi -L` from a container; reboot restores MIG + CDI; suspend never fires; `/data` mounted; a laptop on the tailnet resolves `api.sno-flywheel.local` to `10.20.0.10` (nothing answers yet — that's fine).

**Phase 0 closed 2026-09-18 (D141).** All five exit items met: each slice runs `nvidia-smi -L` from a confined container; a reboot restored MIG and the CDI spec with identical MIG UUIDs; no suspend attempt since the masks; `/data` mounted and back after reboot; a Mac on the tailnet resolves `api`, `api-int` and `*.apps.sno-flywheel.local` to `10.20.0.10`. Scripts: `tools/host/fury/`.

### Phase 1 — Prove the arm64 flywheel pieces on the host (before any cluster)

> **Order corrected 2026-09-18:** the eval smoke in step 1 needs a Zenoh router and a sim, and only the sim
> image's entrypoint starts a router — so the working order is **1a** CUDA on a slice from the runtime image
> (`tools/host/fury/10-cuda.sh`), **2** sim build, **1b** policy on `0:1` + `MODE=eval` smoke, then **3–4**.
> **1a passed:** arm64 `torch 2.9.1+cu130` on `0:1` (`GB300 MIG 1g.31gb`, 31 GiB), page size 65536, matmul and a
> pinned-memory copy OK, container SELinux-confined with `container_use_devices` off. The pinned modelcar
> digest is an amd64-only image; it mounts fine as a data volume (podman warns about the platform).

1. **Runtime image on 64k pages, on a slice.** Pull the arm64 platform of the signed runtime
   digest (`gitops/rhem/fleet-act-inference.yaml` pins it) and run `ROLE=policy` on
   `nvidia.com/gpu=0:1` with `POLICY_DEVICE=cuda`; then a `MODE=eval` smoke. This is the first
   time aarch64 CUDA serves a request and the first time anything runs on 64k pages. If it
   fails on pages (jemalloc/tcmalloc/pinned-memory errors are the tell), decide kernel 4k now
   (decision 5) — before building anything else.
2. **Sim image — does not exist for arm64 anywhere.** `docs/FURY-SETUP.md:50-53` is wrong:
   Tekton builds only the runtime image. Build natively:
   `sudo podman build -f docker/Dockerfile -t localhost/soarm-sim:arm64 .` (ROS 2 kilted +
   Gazebo + upstream demos via colcon). Time it; push to quay as `soarm-flywheel:sim-arm64`
   once green. Then Gazebo headless smoke: scene loads, camera topic publishes (Ogre/EGL on
   aarch64 is untested — if rendering fails, try `LIBGL_ALWAYS_SOFTWARE=1`/Mesa llvmpipe; the
   sim is CPU anyway).
3. **Coordinator with `ENGINE=podman`** (`tools/host/run-coordinator.sh`, never run for real;
   SELinux `:z` path). Point `DATA_DIR=/data/flywheel` (there is no `FLYWHEEL_DATA`; `disk-guard.sh` reads `BAGS_DIR`); run
   `disk-guard.sh` with `ENGINE=podman` — its default is `docker`, under which it never stops anything here.
4. **Local loop without the cluster:** sim + camera bridge + coordinator + a locally served
   policy (as its own container on `0:1` with the coordinator separate — the topology the device will
   have; `ROLE=all` was a single-GPU desktop shape and is not used on the Fury) → episodes land in
   `/data/flywheel`, scored by ground truth.

**Exit:** ≥10 curated episodes recorded on the Fury by the arm64 stack, policy served from slice 0:1.

### Phase 2 — SNO hub VM

- VM: 32 vCPU, 128 GiB, 600 GB qcow2 on `/data/libvirt/images`, UEFI (aarch64 default), NIC on `fury-net`, static `10.20.0.10`, DNS `10.20.0.1`.
- Install: agent-based, **OpenShift ≥ 4.19 aarch64** (RHEM 1.3's chart needs kube ≥ 1.32 — don't override). `openshift-install` + `oc` aarch64 binaries from `mirror.openshift.com/pub/openshift-v4/aarch64/clients/ocp/`. `install-config`: `controlPlane.architecture: arm64`, single node, `baseDomain: local`, `metadata.name: sno-flywheel`, the pull secret (human supplies). `agent-config`: `rendezvousIP: 10.20.0.10` — **must equal the address the node gets** (D005 lost an install to this). Boot the VM from the generated ISO.
- After install: `oc` from the host and from laptops (tailnet DNS), `kubeadmin` kept with the operator, kubeconfig on the host under `/root`.

**Exit:** `oc get nodes` Ready from a laptop over Tailscale; console at `console-openshift-console.apps.sno-flywheel.local`.

**Phase 2 closed 2026-09-19 (D145, D146):** OpenShift 4.22.13 arm64, single node, 4k-page guest; API and console reachable from a laptop over the tailnet.

### Phase 3 — Hub bootstrap on the `fury` branch

> **Closed 2026-09-19 (D147, D148).** Eight Argo Applications Synced/Healthy on arm64; Rekor + Trillian rebuilt from source; fresh cosign key; first native build signed (16 min 47 s); Fleet flipped to the new trust root; `rhem/bootstrap` applied. Differences from the steps below: RHTAS is not installed through OLM, the modelcar was co-signed rather than re-packaged, and the first build is arm64-only.

1. `git checkout -b fury desktop-gpu-split`. Re-point `targetRevision` in `argocd/*-app.yaml` (7 files) and `rhem/bootstrap/resourcesync*.yaml` (2 files) to `fury`.
2. Fury values in tracked files: `gitops/flywheel/edge-kafka.yaml` advertised listener → `10.20.0.10:30903`; dashboard `CAMERA_HOST` → the Fury host's `fury-net` address `10.20.0.1` (env, D124); Fleet template (see 4).
3. Follow `argocd/README.md` bootstrap table in order (10 apps) with the 8 hand-created Secrets first. **Fresh cosign keypair** on the Fury; its `cosign.pub` goes into the Fleet's inline trust files. Securesign creates fresh Rekor/Fulcio — after it's up, copy the new Rekor public key into `gitops/tekton/rekor-public-key.yaml` and the Fleet's inline `rekor.pub`.
4. **Fleet template changes** (`gitops/rhem/fleet-act-inference.yaml`): `AddDevice=nvidia.com/gpu=0:1` for the `gpu=nvidia` branch (not `=all` — `all` hands every slice to the container); rollout `BatchSequence` retargeted: batch 1 = `role=canary` (one fleet VM), batch 2 = `site=fury`; keep `successThreshold: 100%`. Add a `gpu_device` label so the device chooses its CDI device: `all` by default, a slice's `MIG-<uuid>` name on a partitioned host (index names such as `0:1` contain `:`, which a flightctl label value may not).
5. Tekton: run the runtime-image pipeline **natively on aarch64** (the arm64 leg took 53 min under qemu on the desktop; measure native). The `qemu-binfmt` DaemonSet exists for the amd64 leg. Sign into the Fury's Rekor. Re-package + sign the current best modelcar the same way (or let Phase 5's promotion produce it).
6. `rhem/bootstrap/README.md`: the four `flightctl apply` objects, logged in as a real user (SA tokens map to no org).

**Exit:** all Argo apps Synced/Healthy; RHEM UI up at `ui.flightctl.apps.sno-flywheel.local`; a runtime image and a modelcar signed by the Fury's own trust root, Rekor index recorded.

### Phase 4 — Enroll the Fury host as the GPU device

> **Closed 2026-09-19 (D150, D152).** Done with `tools/host/fury/40-device-provision.sh` and `41-device-enroll.md`, not the two `device/` scripts named below (wrong for this host: NVIDIA repo, static `/etc/cdi`, `/etc/hosts`, passwordless sudo). The policy serves on the whole GPU (MIG off, no `gpu_device` label), not on slice `0:1`; the pull default is label-driven; mode switches use `flightctl app stop|start` around `fury-mode`.

- `device/provision.sh` with `RHEM_HUB_IP=10.20.0.10`: it installs `flightctl-agent-1.3.0-1.el10` (aarch64 pin — verify the rpm exists on `rpm.flightctl.io` first) and re-runs `nvidia-ctk cdi generate` — harmless now that MIG is already on. Check `rpm -qf /etc/containers/policy.json` diffs *before* the Fleet overwrites the file (D042).
- `device/enroll.sh` with labels `site=fury gpu=nvidia arch=arm64 policy_device=cuda zenoh_router=10.20.0.1` (plus `gpu_device=MIG-<uuid>` when serving from a slice).
- The `ROLE=all` collision (D132): once the device serves, never run a second `/run_policy` on the host; local eval uses `ROLE=coordinator` against the device, or stop `flightctl-agent` + the quadlet first.

**Exit:** device `Healthy` in RHEM, quadlet serving on slice `0:1`, sim episodes stamped with the Fleet's model version.

### Phase 5 — The flywheel end to end on the Fury

Collection → 160-success threshold → pipeline (assemble → fine-tune on slice `0:2` → paired eval → package → sign → register → PR) → human merge → RHEM rollout → device verifies signature + Rekor → serving. Measure merge→serving. Record the run as the Fury promotion record in `docs/eval-records/`. This run is also **B3 (GB300 training numbers)** — capture epoch time and batch size vs the desktop's.

**Exit:** one promotion produced, signed and rolled out entirely on the Fury; the record written.

**RHOAI in this phase (operator's check, 2026-09-19 — "make sure we are properly using RHOAI features").** On this
hub the DataScienceCluster has `dashboard`, `aipipelines` and `modelregistry` Managed and everything else Removed.
What the flywheel uses, and what Phase 5 has to show working on RHOAI 3.5 rather than assume:
- **Data Science Pipelines:** the promotion is a KFP pipeline on the DSPA (`act-flywheel-promotion`, uploaded
  2026-09-19 with `tools/hub/upload-pipeline.sh`); runs are started by the manifest consumer through the DSP API.
  Exit adds: the run is visible with its step graph, parameters and artifacts in the RHOAI dashboard.
- **Model Registry:** the pipeline registers every candidate with its dataset, eval report, image digest and Rekor
  index. The client is pinned at `model-registry==0.3.11` for the 2.25-era API and has only ever seen a 401 from the
  3.5 registry behind kube-rbac-proxy — **to verify before the first run**, and to fix if the API moved. Exit adds:
  the promoted version is visible in the dashboard's registry view, with its lineage properties.
- **Not used, deliberately:** RHOAI model serving (KServe) — the cluster VM has no GPU (decision 2), so the policy
  is served on the device through RHEM and the act 2 model by Red Hat AI Inference on the host; workbenches, Ray /
  Training Operator / Kueue (training runs on the host GPU, orchestrated by the pipeline), TrustyAI, Feast.
  Two more RHOAI pieces were added to the plan by the operator on 2026-09-19 - Phase 5b below.

### Phase 5b — More of RHOAI on stage (added 2026-09-19)

After Phase 5 has produced one promotion, because both pieces read what a promotion writes.

1. **A workbench that opens the eval report.** `workbenches` goes to Managed in the DataScienceCluster; one small
   notebook image (CPU only - the cluster VM has no GPU, and nothing here needs one) with a notebook that reads
   `s3://episodes-data/eval/<run_id>/eval_report.json` and the two per-policy records from the hub's MinIO and
   shows the paired result: success rates, fixed / broken / net, the sign test, per-seed table. The data
   connection is the existing `hub-credentials` Secret. It is the human's view of the gate before the merge.
2. **The registry-to-catalog link as a beat.** The pipeline already registers the candidate in the Model Registry
   and generates the RHEM catalog item from that entry; on stage it is shown as one line of lineage: registry
   version (dataset, eval report, image digest, Rekor index) -> catalog item -> the Fleet's pinned digest -> the
   device's verified pull. Work: confirm the registry step on RHOAI 3.5 (Phase 5), make sure the catalog item
   carries a link back to the registry version, and write the beat into the runbook.

**Exit:** the eval report of a real run open in a workbench from the RHOAI dashboard; one promoted version followed
from the registry view to the RHEM catalog to the device, by clicking, with no terminal.

### Phase 6 — Tenant T1: large-model inference on RHAIIS

> **Status 2026-09-19 (D154, D156).** The model serves on this GPU with tool calling (about 140 tokens/s single stream, MIG off, beside the flywheel). The host service for tenants mode is built and not yet run (`63-assistant-install.sh`, `flywheel/llm-assistant.container`, started by `fury-mode tenants` on slice `0:0`). Still to do: the run on the slice, exposure beyond loopback, the isolation test (step 4).

1. Verify RHAIIS has an aarch64 image: `skopeo inspect --raw docker://registry.redhat.io/rhaii/vllm-cuda-rhel9:<tag> | jq '.manifests[].platform'` (**the namespace is `rhaii/` from 3.4 on**; `rhaiis/` stops at 3.3 — catalog, 2026-09-19. Newest arm64: `3.5.1`, manifest list `sha256:c056e61672b6aea489ad5dde0bd2f8497230f5333e87f7cf6c494eba3bfdc808`; `3.4.4` is the release line the model card was validated on). If not, fallback is upstream vLLM aarch64 (`nvcr.io/nvidia/vllm:<tag>`) — note the story changes from "RHAIIS" to "vLLM on RHEL".
2. Mount the `models` disk **after asking what's on it** (or use `/data/models`); pull the model to it.
3. Quadlet on the host: `AddDevice=nvidia.com/gpu=0:0`, `--gpu-memory-utilization` sized to 126 GB, no `CUDA_VISIBLE_DEVICES`. Expose on a route or a NodePort-style host port reachable over the tailnet; a minimal chat UI (any OpenAI-compatible client) for the booth.
4. Prove isolation: run the flywheel training on `0:2` while the LLM serves; show latency unchanged.

**Exit:** the assistant answers a robotics-code question from slice `0:0` while T2/T3 are busy.

### Phase 7 — Fleet scaling on the box

- RHEL 10 aarch64 KVM guest image (human downloads from access.redhat.com) + cloud-init; N=4–6 VMs, 6 vCPU / 12 GiB each on `fury-net` (`10.20.0.21+`), registered via activation key.
- `device/enroll.sh` each with `site=fury gpu=none policy_device=cpu`, one of them `role=canary`. They run the CPU branch (184 ms p95 proven on the desktop).
- One promotion rolls canary → fleet; RHEM UI shows the fan-out; each device's own signature/Rekor verification.

**Exit:** N+1 devices Healthy in one Fleet; a rollout observed batch by batch.

### Phase 8 — Making the tenants visible

- **DCGM exporter** on the host (verify an aarch64/sbsa image; run with `--device nvidia.com/gpu=all`, port 9400), scraped by the SNO Prometheus/COO stack, Perses panel: per-MIG-instance utilization and memory. `nvidia-smi` in a terminal is the fallback.
- **T4:** 2–3 concurrent ACT training/eval jobs on `0:3` (MPS on top of MIG if they contend) — the "parallel sweeps" beat.
- Gazebo replicas: 2–4 sim instances on cores feeding the curator concurrently (mind the `/run_policy` rule — replicas drive the device's policy through the coordinator).

**Exit:** one screen shows four slices busy with four different things.

### Phase 8b — Self-contained: both acts with the uplink down (added 2026-09-19, D151)

**When:** after Phases 4 and 5 work connected — taking the internet away while a new platform is still being
brought up doubles the unknowns in every failure. Its registry step may be pulled forward: it also makes every
rollout local-speed. Before Phase 10, which then rehearses in this mode.

**Scope:** the *running* demo — collect, train, package, sign, PR, merge, rollout, serve, the mode switch and
act 2 — with no route off the machine. **Not in scope:** rebuilding images from upstream (apt, rosdep, PyPI,
GitHub), installing operators, first pulls. Those are done connected, ahead of time.

What the running demo reaches today, and where each goes:

| Reaches out to | For | Becomes |
|---|---|---|
| `quay.io` | runtime image and modelcar push, signatures, every device pull | the cluster's internal registry, signed under the route name devices pull by; CA delivered by the Fleet; anonymous pull or a credential through RHEM's secret config; `policy.json` / `registries.d` re-pointed. **Spike first:** does the integrated registry take cosign's signature attachments |
| GitHub (git) | Argo `repoURL`, flightctl `Repository`, Tekton clone | an in-cluster Gitea or Forgejo (arm64 image to confirm) as the working remote; GitHub stays the public mirror, synced when online |
| GitHub (API) | the promotion PR (`PyGithub`) and the merge click | a provider switch in `open_promotion_pr`; the PR is merged in the local UI |
| GitHub releases | `cosign` and `crane`, downloaded at **every** pipeline run (Tekton Task and KFP) | baked into the task/component images |
| PyPI | six of seven KFP components `pip install` at start | baked into the component image |
| pytorch.org / Hugging Face | possibly backbone weights at training start (unverified) | pre-seeded cache, offline switches set; found by the first offline training run |
| Hugging Face, `registry.redhat.io` | the act 2 model and the serving image | fetched ahead (`tools/host/fury/60-model-fetch.sh`; the image by digest) |
| Tailscale control plane / relay | the presenting laptop's path to the machine | a direct LAN path: the host's dnsmasq and the `10.20.0.0/24` route offered on a wired interface |

**Exit:** with egress blocked on the host's firewall (every VM routes through it, so one rule is an air gap): one
full promotion from collection to a device serving the new model, one `fury-mode` switch in each direction, and
act 2 serving — then the rule is removed and the mirrors catch up.

### Phase 9 — (optional) GR00T day

Stop T3/T4 tenants, `nvidia-smi mig -dci -dgi`, `-cgi 9,14,19 -C` (3g + 2g + 1g), regenerate CDI, re-point the Fleet's slice label if serving moves. Run NVIDIA's GR00T N1.6 playbook recipe (3B VLA, LIBERO) on the 2g; pre-bake a checkpoint as the fallback. Reslice back afterwards.

### Phase 10 — Rehearsal

Full run of the booth narrative on the Fury: flywheel beats, tenant view, fleet rollout, LLM. Capture recordings from the Fury for the contingency kit.

---

## 5. Unknowns to retire early (in the order they bite)

1. arm64 runtime image + CUDA on 64k pages (Phase 1.1) — kernel decision hinges on it. **Retired 2026-09-19:** torch/CUDA/pinned memory and the whole ROS 2 + Zenoh + lerobot policy stack run on 64k pages, SELinux-confined, on a slice. Stay on the 64k kernel.
2. Native sim image build time and Gazebo rendering on aarch64 (Phase 1.2). **Half retired 2026-09-19:** the native build takes **6 min 4 s** (26 colcon packages in 1 min 38 s; 4.96 GB image) against ~40 min for the same legs under emulation. Built from upstream demos `4d3564c` (HEAD at build time — the Dockerfile does not pin it) with unpinned pip (`opencv-python-headless 5.0.0.93`, `numpy 2.5.3`). Rendering: correct on CPU (Mesa) but 1.66 Hz — see unknown 10 and D142.
3. ~~`flightctl-agent-1.3.0-1.el10.aarch64` on `rpm.flightctl.io`; `nvidia-container-toolkit` aarch64 on RHEL 10 (Phase 0.3 / 4).~~ **Retired 2026-09-18:** both rpms are in their repos' aarch64 metadata (`rpm.flightctl.io/epel/10/aarch64` has `1.3.0-1.el10`; `nvidia-container-toolkit-1.20.0-1`, the version `device/provision.sh` pins, is in NVIDIA's `stable/rpm/aarch64` and — better — in the already-enabled RHEL 10 Supplementary repo). Install-time proof still comes in Phase 0.3 / 4.
4. aarch64 availability (Phase 3) — **retired 2026-09-19 except RHTAS (D147):** on the 4.22 arm64 hub, OpenShift GitOps 1.21, Pipelines 1.24, the Cluster Observability Operator, Tempo, RHOAI 3.5 (dashboard behind the Gateway API included), MinIO, Kafka, Perses and RHEM 1.3 with its product UI all install and run; all eight Argo Applications are Synced/Healthy. Two amd64-only images had to be swapped (the RHEM UI and the chart's CLI image — D146/D147). **RHTAS still has no arm64 server images**, so Rekor + Trillian were rebuilt from the midstream source (`tools/host/fury/rhtas-arm64/`): built in under five minutes, Ready on the hub, a cosign sign/verify round trip passes (D147 addendum). The upstream `rekor` chart stays the fallback and was not needed.
5. ~~KVM/UEFI aarch64 guest on Grace (Phase 2).~~ **Retired 2026-09-19 (D146):** a 32 vCPU / 128 GiB UEFI guest with a 4k-page kernel runs on the 64k-page host; the hub installed in one pass.
6. ~~Empty/retargeted rollout batches (Phase 3.4).~~ **Retired on paper 2026-09-19 (D148):** the Fleet's batches are `role=canary` (limit 1), then `site=fury`, then flightctl's implicit last batch; its documentation expects batches that match nothing, and the development stand-in ran with one for weeks. Proof on this hub: the first enrolled device meets an empty canary batch (Phase 4).
7. ~~RHAIIS aarch64 image (Phase 6.1).~~ **Retired on paper 2026-09-19:** RHAIIS 3.5 `rhaii/vllm-cuda-rhel9` (not `rhaiis/`, which stops at 3.3) is published for arm64 and GB300/AArch64/CUDA 13 is in Red Hat's supported configurations (D143). Proof is pulling and serving it in Phase 6.
8. DCGM exporter aarch64 (Phase 8).
9. ~~*(added by the 21:10 re-check)* Tailscale subnet routing on the 64k kernel once `xt_mark` is loadable (Phase 0.6).~~ **Retired 2026-09-18:** with `kernel-64k-modules-extra` the `ts-forward` MARK rule installs; `10.20.0.0/24` is advertised and approved, a stand-in guest at `10.20.0.99` on `virbr-fury` answered pings from a laptop, and the laptop resolves the cluster names through split DNS.
10. *(2026-09-19, D142)* **Gazebo cannot render on the GPU while MIG is on, and on CPU it is too slow for the policy.** With MIG off: two full-rate sims. Open: the demo structure (two GPU modes vs CUDA-rendered cameras under MIG).
11. ~~*(2026-09-19)* Does NVIDIA Warp (CUDA-only ray casting) run inside a MIG slice on this box.~~ **Retired 2026-09-19:** yes — 2,096 two-camera 640x480 frame pairs/s on a 1g.31gb slice (6,449 on the whole GPU), toy scene; D142 addendum.
12. *(2026-09-19)* If 11 passes: MuJoCo-Warp renderer speed and look on the real scene in a 1g slice, and how far the current policy is from those pixels. **Speed retired 2026-09-19:** 152 frame pairs/s with shadows on a 1g.31gb slice for the real 322k-face scene (D143 addendum); the look differs from Gazebo's, so the policy gap is still open.
13. *(2026-09-19, D143)* SNO guest on a 64k-page RHEL 10 ARM host: Red Hat's ARM 64 virtualization rules want matching page sizes (`kernelType: 64k-pages` for RHCOS) and list only RHEL guests as supported — find out in Phase 2 before building on the VM.

Status is tracked here: strike an item through with the date and the evidence when it is retired.

## 6. Inputs a human supplies

RHEL activation key + org id (root-only env file, shredded after — D036); OpenShift pull secret; quay robot creds; GitHub fine-grained token; cosign key passphrase; kubeadmin password (from the install); Tailscale route approval + split DNS in the admin console; RHEL 10 aarch64 guest image; from the machine's owner: whether passwordless sudo is possible, and what the `models` disk is for.

## 7. Working agreements on the box

- The machine and its login account are shared: announce in the channel before reboots, kernel changes, storage changes, or MIG toggles.
- Never paste secrets into a session or a file in git; never store passwords in brim.
- The BMC is the recovery path for reboots and network mistakes (address held by the operator — this repo is public, so it is not recorded here).
- Tailscale SSH sessions are logged by tailscaled — that's fine, just know it.
- Commit on `fury`; add `D139+` entries to `DECISIONS.md` for every decision above that gets exercised or changed; keep `docs/FURY-SETUP.md` corrected as reality lands.
