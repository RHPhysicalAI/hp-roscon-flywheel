#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Create the desktop stand-in device VM (D024): RHEL 10 KVM guest image + cloud-init,
# bridged on br0 next to the SNO VM. Runs unprivileged on the desktop (libvirt group).
#
# D043: the host's bag directory ($BAGS_DIR) is shared into the VM over virtiofs (tag $BAGS_TAG), so
# the recorder in the quadlet writes bags where the host-side assemble/prune flow expects them. That
# needs the host `virtiofsd` package: libvirtd launches it as root and its AppArmor profile only
# allows /usr/{lib,lib/qemu,libexec}/virtiofsd. For a VM that already exists use vm/bags-share.sh.
#
# Prerequisite the operator supplies: the RHEL 10.2 KVM Guest Image (qcow2) at $BASE_IMG.
# It needs a Red Hat login: https://access.redhat.com/downloads/content/rhel  ->  "Red Hat
# Enterprise Linux 10.2 KVM Guest Image" (x86_64). Registration happens later, in provision.sh.
set -euo pipefail

VM_NAME="${VM_NAME:-act-device}"
VCPUS="${VCPUS:-8}"
MEMORY_MIB="${MEMORY_MIB:-16384}"
DISK_GB="${DISK_GB:-60}"
BASE_IMG="${BASE_IMG:-/home/jary/images/rhel-10.2-x86_64-kvm.qcow2}"
POOL="${POOL:-images}"                        # libvirt pool at /var/lib/libvirt/images
BRIDGE="${BRIDGE:-br0}"
OSINFO="${OSINFO:-rhel10.1}"                  # newest rhel10 entry in virt-install 4.1.0's osinfo db
SSH_PUBKEY_FILE="${SSH_PUBKEY_FILE:-$HOME/.ssh/id_rsa.pub}"
VCPU_CPUSET="${VCPU_CPUSET:-}"                # e.g. 0,2,4,6,8,10,12,14 to pin to P-cores; empty = unpinned
BAGS_DIR="${BAGS_DIR-/home/jary/flywheel-data/bags}"   # D043 virtiofs share of the host bag dir; empty = no share
BAGS_TAG="${BAGS_TAG:-bags}"                  # mount tag the guest fstab refers to (provision.sh)
CONNECT="qemu:///system"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLOUD_INIT_DIR="${HERE}/cloud-init"

log() { printf '[create-vm] %s\n' "$*"; }
die() { printf '[create-vm] ERROR: %s\n' "$*" >&2; exit 1; }
virsh_() { virsh -c "$CONNECT" "$@"; }

for tool in virsh virt-install qemu-img python3; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool not found on PATH"
done

if [[ ! -f "$BASE_IMG" ]]; then
  cat >&2 <<MSG
[create-vm] base image not found: ${BASE_IMG}

  Download the RHEL 10.2 KVM Guest Image (needs a Red Hat login in a browser):
    https://access.redhat.com/downloads/content/rhel  ->  RHEL 10.2  ->  "KVM Guest Image" (x86_64)
  then place it at exactly:
    ${BASE_IMG}
  (or point BASE_IMG at wherever you saved it) and re-run this script.
MSG
  exit 2
fi
[[ -r "$SSH_PUBKEY_FILE" ]] || die "ssh public key not found: ${SSH_PUBKEY_FILE}"

VIRTIOFSD=""
if [[ -n "$BAGS_DIR" ]]; then
  for c in /usr/libexec/virtiofsd /usr/lib/qemu/virtiofsd; do
    [[ -x "$c" ]] && { VIRTIOFSD="$c"; break; }
  done
  if [[ -z "$VIRTIOFSD" ]]; then
    cat >&2 <<MSG
[create-vm] virtiofsd not found on the host; the D043 bags share needs it. libvirtd runs it as root under
  an AppArmor profile that only allows /usr/{lib,lib/qemu,libexec}/virtiofsd, so a copy elsewhere does
  not work. Install the package and re-run:
    sudo apt install virtiofsd
  or create the VM without the share:  BAGS_DIR= $0
MSG
    exit 2
  fi
  [[ -d "$BAGS_DIR" ]] || die "BAGS_DIR is not a directory: ${BAGS_DIR}"
fi

if virsh_ dominfo "$VM_NAME" >/dev/null 2>&1; then
  log "VM '${VM_NAME}' already exists: $(virsh_ domstate "$VM_NAME")"
  log "to rebuild: virsh -c ${CONNECT} destroy ${VM_NAME}; virsh -c ${CONNECT} undefine ${VM_NAME} --remove-all-storage"
  log "to add the D043 bags share to this VM: $(dirname "${BASH_SOURCE[0]}")/bags-share.sh"
  exit 0
fi

FORMAT="$(qemu-img info --output=json "$BASE_IMG" | python3 -c 'import json,sys; print(json.load(sys.stdin)["format"])')"
[[ "$FORMAT" == "qcow2" ]] || die "base image format is ${FORMAT}, expected qcow2"
VOL="${VM_NAME}.qcow2"

# The pool directory is root-owned, so the copy goes through libvirt: create a qcow2 volume,
# stream the base image into it, then grow it to the target size (no backing chain to keep).
if virsh_ vol-info --pool "$POOL" "$VOL" >/dev/null 2>&1; then
  log "volume ${POOL}/${VOL} already exists; reusing"
else
  log "creating ${POOL}/${VOL} (${DISK_GB} GB) from ${BASE_IMG}"
  virsh_ vol-create-as "$POOL" "$VOL" "${DISK_GB}G" --format qcow2 >/dev/null
  virsh_ vol-upload --pool "$POOL" "$VOL" "$BASE_IMG"
  virsh_ pool-refresh "$POOL" >/dev/null
  virsh_ vol-resize --pool "$POOL" "$VOL" "${DISK_GB}G" >/dev/null
fi
log "volume: $(virsh_ vol-info --pool "$POOL" "$VOL" | grep -E 'Capacity|Allocation' | tr -s ' ' | tr '\n' ' ')"

# Render cloud-init with the operator's public key (python keeps the key's characters intact).
WORK="$(mktemp -d)"
trap 'rm -f "${WORK}/user-data" "${WORK}/meta-data"; rmdir "${WORK}" 2>/dev/null || true' EXIT
SSH_PUBKEY="$(head -n1 "$SSH_PUBKEY_FILE")" python3 - "$CLOUD_INIT_DIR/user-data" "$WORK/user-data" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f, open(dst, "w") as out:
    out.write(f.read().replace("__SSH_PUBKEY__", os.environ["SSH_PUBKEY"]))
PY
cp "$CLOUD_INIT_DIR/meta-data" "$WORK/meta-data"

VCPU_ARG="$VCPUS"
[[ -n "$VCPU_CPUSET" ]] && VCPU_ARG="${VCPUS},cpuset=${VCPU_CPUSET}"

# virtiofs needs shared memory backing (memfd); the guest mounts the tag via provision.sh's fstab entry.
SHARE_ARGS=()
if [[ -n "$BAGS_DIR" ]]; then
  SHARE_ARGS+=(--memorybacking source.type=memfd,access.mode=shared
               --filesystem "source=${BAGS_DIR},target=${BAGS_TAG},driver.type=virtiofs,accessmode=passthrough")
fi

log "virt-install ${VM_NAME}: ${VCPUS} vCPU / ${MEMORY_MIB} MiB / ${DISK_GB} GB, bridge ${BRIDGE}, osinfo ${OSINFO}, bags share: ${BAGS_DIR:-none}"
virt-install --connect "$CONNECT" \
  --name "$VM_NAME" \
  --vcpus "$VCPU_ARG" \
  --memory "$MEMORY_MIB" \
  --cpu host-passthrough \
  --osinfo "$OSINFO" \
  --import \
  --disk "vol=${POOL}/${VOL},format=qcow2,bus=virtio" \
  --network "bridge=${BRIDGE},model=virtio" \
  --graphics none \
  --console pty,target_type=serial \
  --cloud-init "user-data=${WORK}/user-data,meta-data=${WORK}/meta-data" \
  --autostart \
  --noautoconsole \
  ${SHARE_ARGS[@]+"${SHARE_ARGS[@]}"}

cat <<NEXT
[create-vm] ${VM_NAME} started. cloud-init takes ~1 min on first boot.
  console   virsh -c ${CONNECT} console ${VM_NAME}      (exit: Ctrl+])
  address   virsh -c ${CONNECT} domifaddr ${VM_NAME} --source arp   (bridged: DHCP from the LAN)
  ssh       ssh jary@<address>
  next      copy provision.sh + the activation-key file (ORG_ID=/ACTIVATION_KEY=) to the VM, then
            on the VM:  sudo RHEM_HUB_IP=<sno-ip> ./provision.sh --env-file /root/activation-key
            (provision.sh mounts virtiofs tag '${BAGS_TAG}' at /var/lib/act-inference/bags when the share exists)
            then from a flightctl-logged-in shell:  device/enroll.sh jary@<address>
NEXT
