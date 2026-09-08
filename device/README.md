<!-- This project was developed with assistance from AI tools. -->
# Device plane — RHEM-managed inference device (D024)

The ACT policy container runs on an RHEM (flightctl 1.3) managed device. On the desktop that
device is a RHEL 10 KVM VM running the policy on **CPU**; on the Fury the RHEL 10.2 host itself
is the device and runs it on the GPU. One provisioning script serves both; the per-site
differences live in device labels that the Fleet template reads.

| | Desktop stand-in (`act-device`) | Fury |
|---|---|---|
| Device | KVM VM on the desktop, bridged on `br0` | the RHEL 10.2 host |
| Arch | x86_64 (`arch=amd64`) | aarch64 (`arch=arm64`) |
| Policy device | `cpu` | `cuda` (CDI) |
| zenoh router | host sim, `10.0.0.48:7447` | host sim, `<fury-ip>:7447` |
| Hub | SNO VM `10.0.0.49` (`*.flightctl.apps.sno-flywheel.local`) | Fury SNO |

## VM sizing (desktop)

8 vCPU / 16 GiB / 60 GB qcow2, `--cpu host-passthrough`, virtio disk + NIC, bridged on `br0`
(same LAN as the SNO VM, so it reaches the host zenoh router and the SNO NodePorts), serial
console only, autostart. The CPU spike (`docs/eval-records/cpu-spike.md`) shows one ACT forward
pass takes ~75 ms on 8 P-core threads against a 1000 ms budget, so this size is comfortable;
`VCPU_CPUSET=0,2,4,6,8,10,12,14` pins the vCPUs to one thread per P-core if latency ever needs it.

Current instance (2026-09-08): `act-device`, **10.0.0.51** (DHCP), RHEL 10.2 (kernel
6.12.0-211.7.3.el10_2), root grown to 60 GB, user `jary` with the desktop's `~/.ssh/id_rsa.pub`
and passwordless sudo, provisioned and registered, agent installed and enabled but not started
(waiting for the hub). `<memoryBacking>` (memfd, shared) is defined and the guest fstab entry for the
bags share is in place; the share itself waits on the host `virtiofsd` package (see D043 below).

## What the operator supplies

1. **RHEL 10.2 KVM Guest Image** (needs a Red Hat login in a browser):
   https://access.redhat.com/downloads/content/rhel → RHEL 10.2 → *KVM Guest Image* (x86_64),
   saved on the desktop as `/home/jary/images/rhel-10.2-x86_64-kvm.qcow2` (or set `BASE_IMG`).
   `create-vm.sh` refuses to run without it and prints this instruction.
2. **Registration input** — an activation key from console.redhat.com, as a root-only file with
   two shell lines, `ORG_ID=...` and `ACTIVATION_KEY=...` (desktop copy:
   `/home/jary/activation-key`, mode 600). `provision.sh --env-file <path>` sources it (or
   `/root/activation-key` if present); `RHSM_USER`/`RHSM_PASS` in the environment are the
   alternative. Values are never printed; registration output is redacted.
3. The **flightctl CLI 1.3.0** logged in to the hub, wherever `enroll.sh` runs (Mac or desktop).

## Order of operations

```bash
# 1. desktop: create + boot the VM (idempotent; exits 2 with instructions if the qcow2 is missing)
device/vm/create-vm.sh
virsh -c qemu:///system domifaddr act-device --source agent      # bridged: DHCP from the LAN

# 2. copy the script and the key to the VM; provision as root; the key is shredded on the VM
scp device/provision.sh /home/jary/activation-key jary@<vm-ip>:/tmp/
ssh jary@<vm-ip> 'sudo install -o root -g root -m 0600 /tmp/activation-key /root/activation-key && shred -u /tmp/activation-key
  && sudo RHEM_HUB_IP=10.0.0.49 bash /tmp/provision.sh --env-file /root/activation-key
  && sudo shred -u /root/activation-key'

# 3. enroll + approve with labels (flightctl CLI logged in; hub must be up)
device/enroll.sh jary@<vm-ip>                          # desktop defaults
SITE=fury GPU=nvidia POLICY_DEVICE=cuda ZENOH_ROUTER=<fury-ip> device/enroll.sh jary@<fury>
```

`provision.sh` (root, arch-neutral, idempotent): register (skipped if already registered) →
`dnf install podman` and assert ≥ 5.5 (image volumes) → flightctl EPEL10 repo →
`dnf install --setopt=install_weak_deps=False flightctl-agent-1.3.0-1.el10` (pinned) →
`systemctl enable flightctl-agent` (**not** started; the enrollment config comes first) →
`/var/lib/act-inference/{data,bags}` → `/etc/hosts` entries for `api.`/`agent-api.`/`ui.`
`$RHEM_HUB_DOMAIN` when `RHEM_HUB_IP` is set → GPU branch (auto on `nvidia-smi`, or
`--gpu`/`--no-gpu`). It writes no trust files: `policy.json`, `registries.d`, `cosign.pub`,
`rekor.pub` all come from the Fleet.

`enroll.sh` (where flightctl is logged in): `flightctl certificate request --signer
flightctl.io/enrollment --output embedded` → `/etc/flightctl/config.yaml` on the device (0600) →
`systemctl enable --now flightctl-agent` → waits for the new pending enrollment request →
`flightctl approve -l fleet=… -l site=… … enrollmentrequest/<name>` → prints the device. `--reset`
wipes `/var/lib/flightctl` first for a re-enrollment. Labels default to the desktop VM:

| label | desktop | Fury | note |
|---|---|---|---|
| `fleet` | `act-inference` | `act-inference` | Fleet selector |
| `site` | `desktop` | `fury` | rollout batch order |
| `gpu` | `none` | `nvidia` | templates the CDI line in the quadlet |
| `policy_device` | `cpu` | `cuda` | `POLICY_DEVICE` env for the entrypoint |
| `arch` | `amd64` | `arm64` | from the device's `uname -m` |
| `zenoh_router` | `10.0.0.48` | `<fury-ip>` | host part only — see flags below |
| `zenoh_port` | `7447` | `7447` | port part |

## Fury deltas

- Run `provision.sh` on the host itself (aarch64); `--gpu` is auto-detected from `nvidia-smi`
  and installs `nvidia-container-toolkit-1.20.0` from
  `https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo`
  (stable channel, `repo_gpgcheck=1`; 1.20.0 is the newest for both x86_64 and aarch64 on
  2026-09-08 — bump `NVIDIA_CTK_VERSION` deliberately), then `nvidia-ctk cdi generate
  --output=/etc/cdi/nvidia.yaml`. The NVIDIA driver must already be present.
- `RHEM_HUB_IP` is the Fury SNO's address; `RHEM_HUB_DOMAIN` stays
  `flightctl.apps.sno-flywheel.local` unless the Fury SNO is named differently.
- Enroll with `SITE=fury GPU=nvidia POLICY_DEVICE=cuda ZENOH_ROUTER=<fury-ip>`; `arch=arm64` is
  derived.
- No VM step; `create-vm.sh`, `bags-share.sh` and cloud-init are desktop-only.
- No virtiofs share: `/var/lib/act-inference/bags` is local disk. `provision.sh` finds no `bags`
  tag, labels the directory `container_file_t`, and the same Fleet volume line applies (D043).

## Bags over virtiofs (D043)

**Why.** The VM has a 60 GB disk and an episode bag is ~1.3 GB, so recording into the VM would fill
it in ~40 episodes. The host-side flow (`tools/host/assemble_all.sh` ports curated successes into a
LeRobot dataset; `tools/host/prune_bags.py --yes` deletes only the *ported* class) reads
`~/flywheel-data/bags` on the desktop, and the curated JSON's `dataset_path` is `bags/<sec>_<nsec>`
relative to the container's `/data` (`docs/data-contract-eval-dashboard.md`). Sharing the host
directory into the VM keeps every one of those contracts intact; the Fleet mounts it at the same
in-container path the host `docker run` uses (`/data/bags`).

**How (desktop).** `create-vm.sh` defines it at creation (`BAGS_DIR`, default
`/home/jary/flywheel-data/bags`; tag `BAGS_TAG=bags`); `bags-share.sh` adds it to an existing VM.
Both need only the libvirt group on the host and both refuse to run without the host `virtiofsd`
package:

```bash
sudo apt install virtiofsd          # once, on the desktop (1.10.0-1ubuntu0.1 on Ubuntu 24.04)
device/vm/bags-share.sh             # define <memoryBacking memfd/shared> + <filesystem virtiofs>, cold restart
# guest: re-run provision.sh, or by hand
sudo mount /var/lib/act-inference/bags && findmnt -t virtiofs
```

`provision.sh` detects the tag (`/sys/fs/virtiofs/*/tag`), writes the fstab line
`bags /var/lib/act-inference/bags virtiofs defaults,nofail,context=system_u:object_r:container_file_t:s0 0 0`
and mounts it; without the tag (Fury) it does nothing but label the local directory. The
`context=` option is what lets the rootful quadlet write there under SELinux Enforcing — libvirt's
virtiofsd has no xattr support, so the Fleet's bags volume carries no `:z`.

**Ownership.** libvirt launches virtiofsd as root (`--sandbox namespace`), so files the guest's root
writes land as `root:root` on the host — exactly what the host `docker run` produces today, and the
host flow already runs inside root containers.

**Why the package is mandatory (verified 2026-09-08).** libvirtd's enforced AppArmor profile only
allows `/usr/{lib,lib64,lib/qemu,libexec}/virtiofsd` (`/etc/apparmor.d/usr.sbin.libvirtd:97`); a copy
of the binary under `/home` dies at start. An unprivileged socket-mode daemon (`--sandbox=none`,
`<source socket>`) passes DAC but is refused by the per-VM QEMU profile. Neither is fixable without
either sudo or dropping the VM's AppArmor label, so the package is the fix.

**Port-as-you-go (host).** `tools/host/after_assemble.sh` waits for `assemble_all.sh`, then runs
`prune_bags.py --yes` and appends the result (each deleted bag with size) to `~/prune-dryrun.txt`.
MinIO credentials come from `~/.minio-env` (0600, `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`); the script
refuses to run without them. The host copies live in `~/` on the desktop; `tools/host/` is the
versioned source.

## Flags verified (2026-09-08)

| Flag | Result |
|---|---|
| podman ≥ 5.5 on RHEL 10.2 (image volumes) | **Yes — podman 5.8.2** installed from `rhel-10-for-x86_64-appstream-rpms` on the VM. (Before the VM existed, the public proxy — CentOS Stream 10 AppStream repodata, `mirror.stream.centos.org/10-stream/AppStream/x86_64/os/` — listed podman 6.0.0–6.1.0 epoch 7; CS10 runs ahead of RHEL 10.2, so it was only a lower-bound indicator.) `provision.sh` asserts the version at run time. |
| flightctl-agent 1.3.0 still hard-requires greenboot? | **No.** `rpm:requires` of `flightctl-agent-1.3.0-1.el10` (rpm.flightctl.io/epel/10 primary.xml, both arches): `/bin/sh`, `/usr/bin/bash`, `flightctl-selinux = 1.3.0`, `jq`, `sudo`, `libresolv`, glibc ≥ 2.34. `flightctl-greenboot` is only a *Recommends*, so `--setopt=install_weak_deps=False` leaves it out — confirmed on the VM: `greenboot` and `flightctl-greenboot` are not installed. (1.2.0 needed thor-testing's greenboot sideload; 1.3.0 does not.) |
| Label value `zenoh_router=10.0.0.48:7447` | **Rejected — `:` is not a legal label-value character.** flightctl 1.3.0 validates labels server-side with Kubernetes `IsValidLabelValue` (`internal/util/validation/validation.go:91`, called from `api/core/v1beta1/validation.go` for `metadata.labels` and the approval `labels`): ≤ 63 chars, alphanumeric at both ends, `[-A-Za-z0-9_.]` inside. The OpenAPI (`additionalProperties: string`) and the CLI (`-l key=value`, `internal/cli/approve.go:62`) do not check, so the request would 400. Split into `zenoh_router=10.0.0.48` + `zenoh_port=7447`; `enroll.sh` validates every value against the rule locally. Approve syntax per `docs/user/using/managing-devices.md`: `flightctl approve -l k=v -l k=v enrollmentrequest/<name>` (`--replace-labels` to drop agent-provided labels). |
| `osinfo` variant for RHEL 10.2 | `virt-install 4.1.0` on the desktop knows `rhel10.0` and `rhel10.1` only; `rhel10.1` is used (`OSINFO` overrides). |

## Files

| Path | Purpose |
|---|---|
| `provision.sh` | root, arch-neutral, idempotent device provisioning (VM + Fury) |
| `enroll.sh` | enrollment certificate → device → approve with labels |
| `vm/create-vm.sh` | desktop VM from the RHEL 10.2 guest image + cloud-init, with the D043 virtiofs bags share |
| `vm/bags-share.sh` | add the D043 bags share to an existing VM (define + cold restart) |
| `vm/cloud-init/{user-data,meta-data}` | operator user, ssh key placeholder, grow root |
| `spike/bench_cpu_forward.py` | CPU forward-latency benchmark (D024 criterion 1) |

Staged copies on the desktop live under `/home/jary/act-device/` (not a git checkout). The host-side
bag flow scripts are under `tools/host/` in this repo.
