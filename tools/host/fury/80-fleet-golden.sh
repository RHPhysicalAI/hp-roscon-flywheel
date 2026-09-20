#!/bin/bash
# The fleet's golden image: one RHEL image mode (bootc) disk that every fleet VM is a copy-on-write clone of
# (docs/internal/FLEET-VMS.md, D163 addendum). Two steps, no guest involved: build fleet/Containerfile on top of
# the RHEL 10 bootc base, turn the result into a qcow2 with bootc-image-builder. 79-bootc-spike.sh is the proof
# that the base and the builder behave on this host. The image holds the OS, the agent, podman, cloud-init and a
# firewall - no application image: every robot pulls the policy's images itself and verifies them itself.
#
#   ./80-fleet-golden.sh build     about 10-15 min
#   ./80-fleet-golden.sh status    the golden image, its clones, the build store, leftovers of the first attempt
#   ./80-fleet-golden.sh remove    the golden image and the OS image - refused while any fleet VM exists
#
# Needs, next to this script: the directory fleet/ (Containerfile, flightctl.repo, RPM-GPG-KEY-flightctl).
#
# No secret is involved any more. No activation key: a rootful build on this registered host sees RHEL's
# repositories through the host's own subscription, and clones are never registered. No enrolment config: late
# binding, 81-fleet-scale.sh hands it to each clone. The registry login is the operator's pull secret, given to
# podman by PATH (--authfile); this script never reads, prints, copies or moves it.
#
# A podman store of this script's own, /data/libvirt/fleet/buildstore, kept between builds: the bootc base and
# the OS image. bootc-image-builder runs --privileged with SELinux confinement off and needs the store that holds
# the image: it gets this one, not the shared host's whole rootful store with everybody else's images in it.
# (79-bootc-spike.sh, which has already run, used the documented form with the main store.) Read-only is tried
# first; the builder mounts the image, which writes under the store, so read-write is the expected outcome - on
# a store that holds nothing but this script's own two images. `remove` leaves it; `status` says how to empty it.
#
# Tried and dropped (2026-09-20): embedding the policy's two images in the OS image, from a second store
# (/data/libvirt/fleet/imagestore). The runtime image has whiteouts - an upper layer deletes files of a lower one -
# and a container build cannot carry those; this script's guard stopped the first build for exactly that. It
# also moved the signature check off the devices. Local speed is the hub mirror's job (FURY-PLAN Phase 8b). If
# that store is still on disk from the failed run (about 4.5 GiB), `status` prints how to remove it; `build`
# ignores it.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
case ${1:-} in
build|status|remove) [[ -z ${2:-} ]] || { echo "usage: ${0##*/} $1   (takes no argument)" >&2; exit 1; } ;;
*) echo "usage: ${0##*/} build | status | remove" >&2; exit 1 ;;
esac
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1
# One lock for 80 and 81: other people hold sessions on this host, and either script's failure path removes files
# the other may just have made. `status` only reads and does not take it.
if [[ $1 != status ]]; then
    exec 9>/run/fleet-stage-a.lock
    flock -n 9 || { echo "${0##*/}: another 80-/81-fleet run is in progress - wait for it. Who:  sudo fuser -v /run/fleet-stage-a.lock" >&2; exit 1; }
fi

pool=/data/libvirt/fleet
golden=$pool/fleet-golden.qcow2
out=$pool/fleet-golden-build            # bootc-image-builder's output directory, emptied and removed again
oldstore=$pool/imagestore               # left by the dropped embedding attempt; never used, only reported
build=$pool/buildstore                  # the bootc base and the OS image: the only store the privileged builder sees
buildrun=/run/fleet-buildstore
base=registry.redhat.io/rhel10/rhel-bootc:10.2
bib=registry.redhat.io/rhel10/bootc-image-builder:10.2
auth=/root/sno-install/pull-secret
os=localhost/fleet-os:latest
agent_ver=1.3.0-1.el10
# Root filesystem, thin. A robot pulls its images itself: the runtime image is about 10.7 GB unpacked, and while it
# is being pulled its 4.2 GiB compressed layer sits in /var/tmp - which on a bootc guest is the root filesystem, as
# is /var/lib/containers. About 16 GB at the peak, beside the OS: 20 GiB was too tight.
disk_gib=40
cfg=

die() { echo "${0##*/}: $*" >&2; exit 1; }
cleanup() { if [[ -n $cfg ]]; then rm -f "$cfg"; fi; exec >&- 2>&-; wait; }     # then wait for tee, or sudo eats the last lines
trap cleanup EXIT
bp() { podman --root "$build" --runroot "$buildrun" "$@"; }                      # podman on the build store
clones() { local f; for f in "$pool"/fleet-vm-[0-9][0-9].qcow2; do [[ -f $f ]] && echo "${f##*/}"; done; true; }
date -u

case $1 in
status)
    if [[ -f $golden ]]; then
        ls -l --time-style=long-iso "$golden"
        qemu-img info "$golden" | grep -E '^(virtual size|disk size)'
        echo "clones backed by it: $(clones | wc -l)"
        older=$(find "$pool" -maxdepth 1 -name 'fleet-vm-[0-9][0-9].qcow2' ! -newer "$golden" -print | head -1)
        [[ -z $older ]] || echo "WARNING: $golden is newer than ${older##*/}. A backing file that changes under its clones corrupts them:  ./81-fleet-scale.sh destroy-all, then rebuild"
    else
        echo "no golden image at $golden - ./80-fleet-golden.sh build"
    fi
    if [[ -d $build ]]; then
        echo "build store $build:"; bp images --format '    {{.Repository}}:{{.Tag}}  {{.Size}}  built {{.CreatedSince}}' || true
        echo "    to empty it:  sudo podman --root $build --runroot $buildrun rmi --all"
    else echo "no build store yet"; fi
    if [[ -d $oldstore ]]; then
        # Literal paths on purpose: these lines are pasted as root, and nothing in them may depend on a variable.
        echo "LEFTOVER: $oldstore ($(du -sh "$oldstore" 2>/dev/null | cut -f1)) - the image store of the dropped embedding attempt. Nothing uses it."
        echo "  To remove it, three commands. The second one unmounts podman's overlay directory, which it leaves mounted on"
        echo "  itself; it does nothing if 'findmnt' finds no such mount:"
        echo "    sudo podman --root /data/libvirt/fleet/imagestore --runroot /run/fleet-imagestore rmi --all"
        echo "    findmnt /data/libvirt/fleet/imagestore/overlay && sudo umount /data/libvirt/fleet/imagestore/overlay"
        echo "    sudo find /data/libvirt/fleet/imagestore -xdev -depth -delete"
    fi
    exit 0 ;;
remove)
    n=$(clones | wc -l)
    [[ $n -eq 0 ]] || die "$n fleet VM disk(s) still depend on the golden image. First:  ./81-fleet-scale.sh destroy-all"
    rm -f "$golden" "$out/qcow2/disk.qcow2" "$out/manifest-qcow2.json"
    if [[ -d $out ]]; then rmdir "$out/qcow2" 2>/dev/null || true; rmdir "$out" || die "$out is not empty - the builder left something this script does not know. Look, then delete by hand:  ls -la $out $out/*"; fi
    if [[ -d $build ]] && bp image exists "$os"; then bp rmi "$os"; fi
    echo "removed the golden image and $os. Kept: the base image in $build, and $bib - a rebuild needs both"
    exit 0 ;;
esac

# ---- build: every check before anything is pulled or created
[[ $(uname -m) == aarch64 ]] || die "written for the aarch64 host, this is $(uname -m)"
for c in podman qemu-img; do command -v "$c" >/dev/null || die "$c is missing - ./03-packages.sh"; done
for f in Containerfile flightctl.repo RPM-GPG-KEY-flightctl; do
    [[ -f $here/fleet/$f ]] || die "no fleet/$f next to this script - copy the whole directory tools/host/fury/fleet/ here"
done
command -v flock >/dev/null || die "flock is missing (util-linux)"
[[ -s $auth ]]     || die "no pull secret at $auth. Put it back root-only, or log in by hand ( sudo podman login registry.redhat.io ) and run this again"
[[ ! -e $golden ]] || die "$golden already exists. To rebuild:  ./81-fleet-scale.sh destroy-all && ./80-fleet-golden.sh remove"
[[ ! -e $out ]]    || die "a previous build left $out behind:  ./80-fleet-golden.sh remove"
compgen -G '/etc/pki/entitlement/*.pem' >/dev/null || die "this host has no RHEL entitlement certificate, so dnf inside the build would find no repositories. Register the host first:  sudo subscription-manager register"
avail=$(df -BG --output=avail /data | tail -1 | tr -dc 0-9)
[[ $avail -ge 130 ]] || die "/data has ${avail} GB free; the coordinator stops under 100 GB (FURY-PLAN decision 6) and a build needs about 15"
[[ ! -d $oldstore ]] || echo "note: $oldstore is still there from the dropped embedding attempt - not used; './80-fleet-golden.sh status' says how to remove it"

# ---- 1. the OS image, built in the build store. label=disable: that store sits under /data/libvirt, whose files
# are virt_image_t - a confined build container may not be able to run from it, and relabelling it would undo
# what restorecon expects there.
t0=$(date +%s)
install -d -m 0750 -o root -g qemu "$pool"; restorecon "$pool"
install -d -m 0700 "$build"
bp pull -q --authfile "$auth" "$base" >/dev/null || die "could not pull $base. 'unauthorized' means this pull secret is not entitled to it:  sudo podman login registry.redhat.io  with an account that is, then run this again"
podman pull -q --authfile "$auth" "$bib" >/dev/null || die "could not pull $bib (same repair as above)"      # the builder itself runs from the host's store
bp build --pull=never --security-opt label=disable \
    --build-arg "BASE=$base" --build-arg "AGENT_VER=$agent_ver" -t "$os" -f "$here/fleet/Containerfile" "$here/fleet" ||
    die "the image build failed - the lines above say where. 'no repositories' or 404s from dnf: the host's subscription is not reaching the build ( sudo subscription-manager status ). A failed signature or key check: the vendored key no longer matches what signs the agent - stop and find out why. Nothing was created but cached layers in $build"
echo "OS image built in $(( $(date +%s) - t0 )) s: $(bp images --format '{{.Size}}' "$os")"

# ---- 2. the disk. As Red Hat's image-mode guide runs the builder, except for WHICH store it is handed (the
# header says why) and no ':Z' on that mount (unconfined_t makes relabelling unnecessary).
cfg=$(mktemp)
printf '[[customizations.filesystem]]\nmountpoint = "/"\nminsize = "%s GiB"\n' "$disk_gib" > "$cfg"
mkdir "$out"
t1=$(date +%s)
builder() {  # $1 = ro | rw: how the builder sees the build store - never the host's main store
    podman run --rm --privileged --pull=never --security-opt label=type:unconfined_t \
        -v "$build":/var/lib/containers/storage:"$1" \
        -v "$cfg":/config.toml:ro -v "$out":/output \
        "$bib" --type qcow2 --config /config.toml "$os"
}
if ! builder ro; then
    echo "---- the builder did not manage on a READ-ONLY store (expected: it mounts the image, and that writes under the store)."
    echo "---- Trying again read-write. What it can write to is $build only: this script's own base and OS image."
    rm -f "$out/qcow2/disk.qcow2" "$out/manifest-qcow2.json"; rmdir "$out/qcow2" 2>/dev/null || true
    builder rw || die "bootc-image-builder failed read-only AND read-write - keep the output above. Then:  ./80-fleet-golden.sh remove   (it clears $out)"
fi
[[ -s $out/qcow2/disk.qcow2 ]] || die "the builder ended without $out/qcow2/disk.qcow2. What it left:  ls -la $out $out/*   then  ./80-fleet-golden.sh remove"
mv "$out/qcow2/disk.qcow2" "$golden"
rm -f "$out/manifest-qcow2.json"
rmdir "$out/qcow2" "$out" || echo "warning: $out is not empty - look at it, './80-fleet-golden.sh remove' refuses to guess" >&2
chown root:qemu "$golden"; chmod 0440 "$golden"; restorecon "$golden"
echo "golden image ready in $(( $(date +%s) - t1 )) s (read-only from here on - every clone is backed by it):"
qemu-img info "$golden" | grep -E '^(virtual size|disk size)'
echo "next:  ./81-fleet-scale.sh 1      then, from the laptop,  tools/hub/fleet-approve.sh --dry-run"
