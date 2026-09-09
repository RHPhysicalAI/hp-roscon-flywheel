#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Add the D043 bags share (host bag directory -> virtiofs tag) to an EXISTING desktop device VM,
# unprivileged (libvirt group): edits the persistent domain XML, then cold-restarts the VM because
# <memoryBacking> cannot change while it runs. create-vm.sh does the same at creation time. Idempotent.
#
# Needs the host `virtiofsd` package: libvirtd launches it as root and its AppArmor profile only allows
# /usr/{lib,lib/qemu,libexec}/virtiofsd, so a binary anywhere else is refused (verified 2026-09-08).
set -euo pipefail

VM_NAME="${VM_NAME:-act-device}"
BAGS_DIR="${BAGS_DIR:-/home/jary/flywheel-data/bags}"
BAGS_TAG="${BAGS_TAG:-bags}"
NO_RESTART="${NO_RESTART:-}"                  # non-empty: define only, restart the VM yourself
CONNECT="qemu:///system"

log() { printf '[bags-share] %s\n' "$*"; }
die() { printf '[bags-share] ERROR: %s\n' "$*" >&2; exit 1; }
virsh_() { virsh -c "$CONNECT" "$@"; }

for tool in virsh python3; do command -v "$tool" >/dev/null 2>&1 || die "$tool not found on PATH"; done
VIRTIOFSD=""
for c in /usr/libexec/virtiofsd /usr/lib/qemu/virtiofsd; do
  [[ -x "$c" ]] && { VIRTIOFSD="$c"; break; }
done
if [[ -z "$VIRTIOFSD" ]]; then
  cat >&2 <<MSG
[bags-share] virtiofsd not found on the host. Install the package, then re-run this script:
    sudo apt install virtiofsd
MSG
  exit 2
fi
[[ -d "$BAGS_DIR" ]] || die "BAGS_DIR is not a directory: ${BAGS_DIR}"
virsh_ dominfo "$VM_NAME" >/dev/null 2>&1 || die "VM '${VM_NAME}' does not exist; create it with device/vm/create-vm.sh"

WORK="$(mktemp -d)"
trap 'rm -f "${WORK}/current.xml" "${WORK}/new.xml"; rmdir "${WORK}" 2>/dev/null || true' EXIT
virsh_ dumpxml --inactive "$VM_NAME" > "${WORK}/current.xml"
CHANGED="$(BAGS_DIR="$BAGS_DIR" BAGS_TAG="$BAGS_TAG" python3 - "${WORK}/current.xml" "${WORK}/new.xml" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
d, t = os.environ["BAGS_DIR"], os.environ["BAGS_TAG"]
s = open(src).read()
changed = []
if "<memoryBacking>" not in s:
    s = s.replace("</currentMemory>",
                  "</currentMemory>\n  <memoryBacking>\n    <source type='memfd'/>\n    <access mode='shared'/>\n  </memoryBacking>", 1)
    changed.append("memoryBacking")
if f"<target dir='{t}'/>" not in s:
    fs = ("    <filesystem type='mount' accessmode='passthrough'>\n"
          "      <driver type='virtiofs'/>\n"
          f"      <source dir='{d}'/>\n"
          f"      <target dir='{t}'/>\n"
          "    </filesystem>\n")
    assert "  </devices>" in s, "no <devices> block"
    s = s.replace("  </devices>", fs + "  </devices>", 1)
    changed.append("filesystem")
open(dst, "w").write(s)
print(",".join(changed) or "none")
PY
)"
if [[ "$CHANGED" == "none" ]]; then
  log "share '${BAGS_TAG}' -> ${BAGS_DIR} already defined on ${VM_NAME}; nothing to do"
  exit 0
fi
virsh_ define "${WORK}/new.xml" >/dev/null
log "defined on ${VM_NAME}: ${CHANGED} (tag '${BAGS_TAG}' -> ${BAGS_DIR}, virtiofsd ${VIRTIOFSD})"

if [[ -n "$NO_RESTART" ]]; then
  log "NO_RESTART set: cold-restart later with  virsh -c ${CONNECT} shutdown ${VM_NAME}  then  virsh -c ${CONNECT} start ${VM_NAME}"
  exit 0
fi
if [[ "$(virsh_ domstate "$VM_NAME")" == "running" ]]; then
  log "cold restart (memoryBacking cannot change live): shutdown"
  virsh_ shutdown "$VM_NAME" >/dev/null
  for _ in $(seq 1 60); do
    [[ "$(virsh_ domstate "$VM_NAME")" == "shut off" ]] && break
    sleep 3
  done
  [[ "$(virsh_ domstate "$VM_NAME")" == "shut off" ]] \
    || die "VM did not shut off in 3 min; run: virsh -c ${CONNECT} destroy ${VM_NAME} && virsh -c ${CONNECT} start ${VM_NAME}"
fi
virsh_ start "$VM_NAME" >/dev/null
log "${VM_NAME} started"
cat <<NEXT
[bags-share] guest side: re-run device/provision.sh (it adds the fstab entry and mounts), or by hand:
  echo '${BAGS_TAG} /var/lib/act-inference/bags virtiofs defaults,nofail,context=system_u:object_r:container_file_t:s0 0 0' | sudo tee -a /etc/fstab
  sudo systemctl daemon-reload && sudo mount /var/lib/act-inference/bags && findmnt -t virtiofs
  sudo touch /var/lib/act-inference/bags/.virtiofs-test    # then on the host: ls -l ${BAGS_DIR}/.virtiofs-test
NEXT
