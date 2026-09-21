#!/bin/bash
# The smallest proof that RHEL image mode works on THIS host before the fleet's base image is built on it (D163
# addendum): pull the RHEL 10 bootc base and bootc-image-builder, build a trivial derived image, turn it into a
# qcow2, boot it once as a micro-VM on fury-net, and report page size, `bootc status` and time to reachable.
# What it finds out: the builder on a 64k-page aarch64 host kernel with SELinux enforcing, and a 4k-page bootc
# guest in the fleet's VM shape (UEFI, host-passthrough, no balloon, 2 vCPU / 3 GiB).
#
#   ./79-bootc-spike.sh up <ssh-public-key-file>    about 10-15 min, most of it the two pulls and the disk build
#   ./79-bootc-spike.sh status
#   ./79-bootc-spike.sh remove                      the VM, the files and the image this script made - nothing else
#
# Registry login: the pull secret the operator keeps at /root/sno-install/pull-secret is handed to podman by PATH
# (--authfile). This script never reads, prints, copies or moves it. Both repositories, the tag 10.2 and arm64
# builds of each were confirmed in the public Red Hat catalog API on 2026-09-20 (newest build 2026-09-17).
# NOT verified: that this pull secret is entitled to them, and anything about running them here - hence the spike.
# The address is fleet-vm-32's (10.20.0.52); 81-fleet-scale.sh refuses a VM whose address answers, so `remove` first.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
case ${1:-} in
up) [[ -n ${2:-} && -z ${3:-} ]] || { echo "usage: ${0##*/} up <ssh-public-key-file>" >&2; exit 1; }
    [[ -f $2 ]] || { echo "${0##*/}: no such file: $2" >&2; exit 1; }
    if grep -q 'PRIVATE KEY' "$2" || ! grep -qE '^(ssh-(ed25519|rsa)|ecdsa-sha2-[a-z0-9-]+) [A-Za-z0-9+/=]+' "$2"; then
        echo "${0##*/}: $2 is not an ssh PUBLIC key (pass the .pub file)" >&2; exit 1
    fi
    # the key line is written into a TOML string
    if head -1 "$2" | grep -q '["\\]'; then echo "${0##*/}: the key's comment holds a quote or a backslash - remove it from the .pub file" >&2; exit 1; fi ;;
status|remove) [[ -z ${2:-} ]] || { echo "usage: ${0##*/} $1   (takes no argument)" >&2; exit 1; } ;;
*) echo "usage: ${0##*/} up <ssh-public-key-file> | status | remove" >&2; exit 1 ;;
esac
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

name='bootc-spike'
base=registry.redhat.io/rhel10/rhel-bootc:10.2
bib=registry.redhat.io/rhel10/bootc-image-builder:10.2
auth=/root/sno-install/pull-secret
img=localhost/bootc-spike:latest
pool=/data/libvirt/images                # where this host keeps VM disks (32-sno-install.sh:22)
disk=$pool/$name.qcow2
out=$pool/$name-build                    # bootc-image-builder's output directory, emptied and removed again
addr=10.20.0.52
mac=52:54:00:14:01:20
ctx=
die() { echo "${0##*/}: $*" >&2; exit 1; }
cleanup() {   # the build context, file by file; then wait for tee, or sudo eats the last lines
    if [[ -n $ctx ]]; then rm -f "$ctx/Containerfile" "$ctx/spike.nmconnection" "$ctx/config.toml"; rmdir "$ctx" 2>/dev/null || true; fi
    exec >&- 2>&-; wait
}
trap cleanup EXIT
defined() { virsh dominfo "$name" >/dev/null 2>&1; }
date -u

case $1 in
status)
    if defined; then echo "$name: $(virsh domstate "$name")"; else echo "$name: not defined"; fi
    ls -l "$disk" 2>/dev/null || echo "no $disk"
    podman image exists "$img" && echo "image $img exists" || echo "no image $img"
    exit 0 ;;
remove)
    if defined; then virsh destroy "$name" 2>/dev/null || true; virsh undefine --nvram "$name"; fi
    rm -f "$disk" "$out/qcow2/disk.qcow2" "$out/manifest-qcow2.json"
    if [[ -d $out ]]; then rmdir "$out/qcow2" 2>/dev/null || true; rmdir "$out" || die "$out is not empty - the builder left something this script does not know. Look, then delete by hand:  ls -la $out $out/*"; fi
    if podman image exists "$img"; then podman rmi "$img"; fi
    echo "removed the VM, $disk, $out and $img. Kept on purpose (the real image build needs them): $base and $bib"
    exit 0 ;;
esac

# ---- up: every check before anything is pulled or created
key=$(head -1 "$2")
[[ $(uname -m) == aarch64 ]] || die "written for the aarch64 host, this is $(uname -m)"
for c in podman virt-install virsh; do command -v "$c" >/dev/null || die "$c is missing - ./03-packages.sh"; done
[[ -s $auth ]] || die "no pull secret at $auth (the file 32-sno-install.sh used). Put it back root-only, or log in by hand:  sudo podman login registry.redhat.io"
[[ $(virsh net-info fury-net 2>/dev/null | awk '/^Active:/ {print $2}') == yes ]] || die "fury-net is not active - ./06-network.sh"
if defined || [[ -e $disk || -e $out ]]; then die "a previous run is still here.  ./79-bootc-spike.sh remove  first"; fi
if ping -c1 -W1 "$addr" >/dev/null 2>&1; then die "something already answers on $addr (fleet-vm-32?). Free the address first"; fi
avail=$(df -BG --output=avail /data | tail -1 | tr -dc 0-9)
[[ $avail -ge 130 ]] || die "/data has ${avail} GB free; the coordinator stops under 100 GB (FURY-PLAN decision 6) and this needs about 15"
echo "host: $(uname -r), page size $(getconf PAGESIZE), SELinux $(getenforce), podman $(podman version -f '{{.Client.Version}}')"

t0=$(date +%s)
podman pull -q --authfile "$auth" "$base" || die "could not pull $base. 'unauthorized' means this pull secret is not entitled to it: log in with an account that is ( sudo podman login registry.redhat.io ) and run this again - podman then finds that login by itself"
podman pull -q --authfile "$auth" "$bib"  || die "could not pull $bib (same repair as above)"
echo "pulled both in $(( $(date +%s) - t0 )) s"
# Does a container on this registered host see RHEL's repositories through the host's entitlement
# (/usr/share/containers/mounts.conf -> /run/secrets)? If yes, the real image build needs no activation key. Not fatal.
repos=$(podman run --rm --pull=never "$base" dnf -q repolist --enabled 2>&1 | tail -6 || true)
echo "$repos"          # repository ids, no secret: the evidence is in the paste whichever way the line below reads it
if grep -q 'baseos' <<<"$repos"; then echo "RESULT  RHEL repositories are visible inside a container: builds here are entitled through the host"
else echo "RESULT  no RHEL BaseOS repository seen inside a container - the image build may need its own registration"; fi

# ---- the derived image: a marker, passwordless sudo for the throwaway user, a static address (fury-net has no
# DHCP, fury-net.xml). The user and the key are the builder's job (config.toml), which also makes the home directory.
ctx=$(mktemp -d)
printf '[connection]\nid=spike\ntype=ethernet\nautoconnect=true\n[ipv4]\nmethod=manual\naddress1=%s/24,10.20.0.1\ndns=10.20.0.1;\n[ipv6]\nmethod=disabled\n' "$addr" > "$ctx/spike.nmconnection"
cat > "$ctx/Containerfile" <<EOF
# This project was developed with assistance from AI tools.
FROM $base
RUN echo "bootc spike, built $(date -u +%FT%TZ) on a $(getconf PAGESIZE)-byte-page host" > /usr/share/bootc-spike-marker && \\
    echo '%wheel ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/90-spike && chmod 0440 /etc/sudoers.d/90-spike
COPY --chmod=0600 spike.nmconnection /etc/NetworkManager/system-connections/spike.nmconnection
EOF
printf '[[customizations.user]]\nname = "spike"\nkey = "%s"\ngroups = ["wheel"]\n' "$key" > "$ctx/config.toml"
t1=$(date +%s)
podman build -q --pull=never -t "$img" "$ctx" || die "podman build failed - the lines above say why; nothing but the two pulled images exists yet"
mkdir "$out"
# As Red Hat's image-mode guide runs it, minus ':Z' on the storage mount: relabelling the whole of this shared
# host's container storage for one container would be wrong, and label=type:unconfined_t makes it unnecessary.
# NOTE (after review, and after this spike had run): the privileged builder is handed the host's whole rootful
# store here. 80-fleet-golden.sh does not repeat that - it gives the builder a dedicated store of its own.
podman run --rm --privileged --pull=never --security-opt label=type:unconfined_t \
    -v "$(podman info -f '{{.Store.GraphRoot}}')":/var/lib/containers/storage \
    -v "$ctx/config.toml":/config.toml:ro -v "$out":/output \
    "$bib" --type qcow2 --config /config.toml "$img" ||
    die "bootc-image-builder failed - THIS is a finding: keep the output above. Then  ./79-bootc-spike.sh remove"
[[ -s $out/qcow2/disk.qcow2 ]] || die "the builder ended without $out/qcow2/disk.qcow2. What it left:  ls -laR $out   then  ./79-bootc-spike.sh remove"
mv "$out/qcow2/disk.qcow2" "$disk"
chown root:qemu "$disk"; chmod 0660 "$disk"; restorecon "$disk"
echo "image + disk built in $(( $(date +%s) - t1 )) s:"; qemu-img info "$disk" | grep -E '^(virtual size|disk size)'

# ---- boot it in the fleet's VM shape (32-sno-install.sh:77-92, docs/internal/FLEET-VMS.md)
# shellcheck disable=SC2054  # the commas belong to virt-install, not to the array
args=(--connect qemu:///system --name "$name" --osinfo rhel10.2
      --machine virt --cpu host-passthrough --vcpus 2 --memory 3072 --memballoon none --boot uefi --tpm none
      --numatune 0
      --disk "path=$disk,format=qcow2,bus=virtio,cache=none,io=native,discard=unmap"
      --network "network=fury-net,model=virtio,mac=$mac"
      --graphics none --console pty,target.type=serial --rng /dev/urandom
      --import --noautoconsole)
t2=$(date +%s)
virt-install "${args[@]}" >/dev/null || die "virt-install failed:  sudo journalctl -u virtqemud -n 30   then  ./79-bootc-spike.sh remove"
up=
for ((i = 0; i < 150; i++)); do
    if timeout 2 bash -c "exec 3<>/dev/tcp/$addr/22 && head -c 4 <&3" 2>/dev/null | grep -q SSH; then up=$(( $(date +%s) - t2 )); break; fi
    sleep 2
done
[[ -n $up ]] || die "no ssh on $addr after 5 minutes (guest: $(virsh domstate "$name")). Look at it:  sudo virsh console $name  (Ctrl-] leaves). A login prompt there means it booted and the network did not come up"
echo "RESULT  ssh answers ${up} s after virt-install started"

# ---- inside the guest, as the person who ran this (their private key is next to the public one, or it is not ours to find)
priv=${2%.pub}
if [[ -n ${SUDO_USER:-} && $priv != "$2" && -f $priv ]]; then
    sudo -u "$SUDO_USER" ssh -i "$priv" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "spike@$addr" \
        'echo "RESULT  guest page size $(getconf PAGESIZE), kernel $(uname -r)"; cat /usr/share/bootc-spike-marker; systemd-analyze | head -1; getenforce; sudo bootc status' ||
        echo "ssh as spike@$addr failed - try it by hand with the command below"
else
    echo "now, with the private key of $2:"
    echo "  ssh spike@$addr 'getconf PAGESIZE; uname -r; cat /usr/share/bootc-spike-marker; systemd-analyze | head -1; getenforce; sudo bootc status'"
fi
echo "paste everything above back. When done:  ./79-bootc-spike.sh remove"
