#!/bin/bash
# Before any pull secret is involved: does a full-size KVM guest come up on this box at all?
# Boots the public RHCOS live ISO (a 4k-page aarch64 kernel - the same one the OpenShift installer
# boots) as a throwaway 32 vCPU / 128 GiB guest on fury-net. This host runs a 64k-page kernel and
# Red Hat's ARM virtualization notes want matching page sizes, so this is the cheap way to find
# out whether the mismatch matters here, along with UEFI, GIC, virtio, routing, DNS and NTP from
# a guest.
#
#   ./30-vm-pretest.sh up [4.22]   download that release's live ISO if needed, show the XML's key parts, start the guest
#   ./30-vm-pretest.sh down [4.22] destroy it and delete its disk and the ISO
#
# Then: sudo virsh console rhcos-pretest   (logs in as core on its own; leave with Ctrl-])
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

name=rhcos-pretest
pool=/data/libvirt/images
ver=${2:-4.22}
iso=$pool/rhcos-live-$ver.aarch64.iso
disk=$pool/$name.qcow2
url=https://mirror.openshift.com/pub/openshift-v4/aarch64/dependencies/rhcos/$ver/latest/rhcos-live-iso.aarch64.iso
die() { echo "${0##*/}: $*" >&2; exit 1; }

case ${1:-} in
up)
    # awk reads to the end: `grep -q` quits at the match, virsh dies of SIGPIPE, and pipefail calls that a failure
    [[ $(virsh net-info fury-net 2>/dev/null | awk '/^Active:/ {print $2}') == yes ]] || die "fury-net is not active"
    virsh dominfo "$name" >/dev/null 2>&1 && die "$name already exists - '$0 down' first"
    free -g | awk '/^Mem:/ {print "host memory: " $7 " GiB available"; if ($7 < 150) exit 1}' || die "not enough free memory for a 128 GiB guest"
    numactl -H 2>/dev/null | head -3

    [[ -s $iso ]] || curl -fL --progress-bar -o "$iso" "$url" || die "could not download the live ISO"
    ls -lh "$iso"

    os=generic
    for o in linux2024 linux2022; do osinfo-query os short-id="$o" 2>/dev/null | grep -q "$o" && { os=$o; break; }; done

    # shellcheck disable=SC2054  # the commas belong to virt-install, not to the array
    args=(--connect qemu:///system --name "$name" --osinfo "$os"
          --machine virt --cpu host-passthrough --vcpus 32,sockets=1,cores=32,threads=1
          --memory 131072 --memballoon none --boot uefi --tpm none
          --disk "path=$disk,size=20,format=qcow2,bus=virtio,cache=none,io=native,discard=unmap,boot.order=1"
          --controller type=scsi,model=virtio-scsi
          --disk "path=$iso,device=cdrom,bus=scsi,readonly=on,boot.order=2"
          --network network=fury-net,model=virtio,mac=52:54:00:14:00:62
          --graphics none --console pty,target.type=serial --rng /dev/urandom
          --import --noautoconsole)

    echo "## what libvirt will build"
    virt-install "${args[@]}" --print-xml --dry-run | grep -E '<gic|<loader|<nvram|<boot order|secure|<cpu |<memballoon|machine=' || die "virt-install rejected the definition"
    virt-install "${args[@]}" || die "guest did not start"
    sleep 20
    virsh domstate "$name"
    journalctl -u virtqemud --since -2min --no-pager -p warning | tail -5
    cat <<'EOT'

next, in the guest:   sudo virsh console rhcos-pretest      (Ctrl-] to leave)
give it a minute to boot, press Enter, then paste this as one line:

grep -E '^(PRETTY_NAME|OPENSHIFT_VERSION|RHEL_VERSION)=' /etc/os-release; uname -r; getconf PAGESIZE; nproc; free -g | head -2; sudo nmcli con mod 'Wired connection 1' ipv4.method manual ipv4.addresses 10.20.0.98/24 ipv4.gateway 10.20.0.1 ipv4.dns 10.20.0.1 && sudo nmcli con up 'Wired connection 1'; getent hosts api.sno-flywheel.local; getent hosts nowildcard.sno-flywheel.local || echo "no wildcard at the zone apex: good"; curl -sI https://quay.io | head -1; podman run --rm registry.access.redhat.com/ubi9/ubi-minimal getconf PAGESIZE; sudo dd if=/dev/zero of=/dev/vda bs=1M count=4096 oflag=direct 2>&1 | tail -1; chronyc -n sources | tail -4
EOT
    ;;
down)
    virsh destroy "$name" 2>/dev/null
    virsh undefine --nvram "$name" 2>/dev/null
    rm -f "$disk" "$iso"
    virsh list --all
    ;;
*)  echo "usage: $0 up|down" >&2; exit 1 ;;
esac
