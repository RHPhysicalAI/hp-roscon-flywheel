#!/bin/bash
# The fleet's golden image: one RHEL image mode (bootc) disk that every fleet VM is a copy-on-write clone of
# (docs/internal/FLEET-VMS.md, D163 addendum). Three steps, no guest involved: fill a small image store with the
# policy's two images, build fleet/Containerfile on top of the RHEL 10 bootc base, turn the result into a qcow2
# with bootc-image-builder. 79-bootc-spike.sh is the proof that the base and the builder behave on this host.
#
#   ./80-fleet-golden.sh build     first time 20-30 min (4.5 GiB from quay, then the build); a rebuild about 10
#   ./80-fleet-golden.sh status    the golden image, its clones, the OS image, what is in the image store
#   ./80-fleet-golden.sh remove    the golden image and the OS image - refused while any fleet VM exists
#
# Needs, next to this script: the directory fleet/ (Containerfile, flightctl.repo, RPM-GPG-KEY-flightctl), and
# fleet-robots.yaml (a copy of gitops/rhem/fleet-robots.yaml.draft) - the two digests to embed are read from it,
# so the image and the Fleet cannot disagree.
#
# No secret is involved any more. No activation key: a rootful build on this registered host sees RHEL's
# repositories through the host's own subscription, and clones are never registered. No enrolment config: late
# binding, 81-fleet-scale.sh hands it to each clone. The registry login is the operator's pull secret, given to
# podman by PATH (--authfile); this script never reads, prints, copies or moves it.
#
# Two podman stores of this script's own, both under /data/libvirt/fleet and both kept between builds:
#   imagestore   the two images to embed. Their pulls run under this host's policy.json - the Fleet's - so both
#                are signature- and Rekor-verified HERE, once; a clone that runs them from the embedded store
#                does not check them again. It is also what spares a rebuild the 4 GiB download.
#   buildstore   the bootc base and the OS image. bootc-image-builder runs --privileged with SELinux confinement
#                off and needs the store that holds the image: it gets this one, not the shared host's whole
#                rootful store with everybody else's images in it. (79-bootc-spike.sh, which has already run,
#                used the documented form with the main store.) Read-only is tried first; the builder mounts the
#                image, which writes under the store, so read-write is the expected outcome - on a store that
#                holds nothing but this script's own two images.
# `remove` leaves both; emptying them is one command each, printed by `status`.
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
store=$pool/imagestore                  # the two images, in an overlay store of their own
storerun=/run/fleet-imagestore
build=$pool/buildstore                  # the bootc base and the OS image: the only store the privileged builder sees
buildrun=/run/fleet-buildstore
base=registry.redhat.io/rhel10/rhel-bootc:10.2
bib=registry.redhat.io/rhel10/bootc-image-builder:10.2
auth=/root/sno-install/pull-secret
os=localhost/fleet-os:latest
agent_ver=1.3.0-1.el10
disk_gib=20
fleet=$here/fleet-robots.yaml
[[ -f $fleet ]] || fleet=$here/../../../gitops/rhem/fleet-robots.yaml.draft
cfg=

die() { echo "${0##*/}: $*" >&2; exit 1; }
cleanup() { if [[ -n $cfg ]]; then rm -f "$cfg"; fi; exec >&- 2>&-; wait; }     # then wait for tee, or sudo eats the last lines
trap cleanup EXIT
sp() { podman --root "$store" --runroot "$storerun" "$@"; }                      # podman on the image store
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
    if [[ -d $store ]]; then
        echo "image store $store:"; sp images --digests --format '    {{.Repository}}@{{.Digest}}  {{.Size}}' || true
        echo "    to empty it (the next build downloads 4.5 GiB again):  sudo podman --root $store --runroot $storerun rmi --all"
    fi
    exit 0 ;;
remove)
    n=$(clones | wc -l)
    [[ $n -eq 0 ]] || die "$n fleet VM disk(s) still depend on the golden image. First:  ./81-fleet-scale.sh destroy-all"
    rm -f "$golden" "$out/qcow2/disk.qcow2" "$out/manifest-qcow2.json"
    if [[ -d $out ]]; then rmdir "$out/qcow2" 2>/dev/null || true; rmdir "$out" || die "$out is not empty - the builder left something this script does not know. Look, then delete by hand:  ls -la $out $out/*"; fi
    if [[ -d $build ]] && bp image exists "$os"; then bp rmi "$os"; fi
    echo "removed the golden image and $os. Kept: the image store ($store), the base image in $build, and $bib - a rebuild needs all three"
    exit 0 ;;
esac

# ---- build: every check before anything is pulled or created
[[ $(uname -m) == aarch64 ]] || die "written for the aarch64 host, this is $(uname -m)"
for c in podman qemu-img; do command -v "$c" >/dev/null || die "$c is missing - ./03-packages.sh"; done
for f in Containerfile flightctl.repo RPM-GPG-KEY-flightctl; do
    [[ -f $here/fleet/$f ]] || die "no fleet/$f next to this script - copy the whole directory tools/host/fury/fleet/ here"
done
command -v flock >/dev/null || die "flock is missing (util-linux)"
[[ -f $fleet ]]    || die "no fleet-robots.yaml next to this script - copy gitops/rhem/fleet-robots.yaml.draft here as fleet-robots.yaml"
[[ -s $auth ]]     || die "no pull secret at $auth. Put it back root-only, or log in by hand ( sudo podman login registry.redhat.io ) and run this again"
[[ ! -e $golden ]] || die "$golden already exists. To rebuild:  ./81-fleet-scale.sh destroy-all && ./80-fleet-golden.sh remove"
[[ ! -e $out ]]    || die "a previous build left $out behind:  ./80-fleet-golden.sh remove"
compgen -G '/etc/pki/entitlement/*.pem' >/dev/null || die "this host has no RHEL entitlement certificate, so dnf inside the build would find no repositories. Register the host first:  sudo subscription-manager register"
runtime=$(sed -n 's/^ *Image=\(quay\.io\/jary\/soarm-flywheel@sha256:[0-9a-f]\{64\}\).*/\1/p' "$fleet" | head -1)
car=$(sed -n 's/^ *Image=\(quay\.io\/jary\/soarm-act-modelcar@sha256:[0-9a-f]\{64\}\).*/\1/p' "$fleet" | head -1)
[[ -n $runtime && -n $car ]] || die "could not read the runtime and modelcar digests out of $fleet"
avail=$(df -BG --output=avail /data | tail -1 | tr -dc 0-9)
[[ $avail -ge 150 ]] || die "/data has ${avail} GB free; the coordinator stops under 100 GB (FURY-PLAN decision 6) and a build needs about 40"
echo "embedding    $runtime"; echo "             $car"

# ---- 1. the image store. A store of its own, made by podman natively on /data: an overlay store cannot be
# created inside a build container (overlay on overlay), and copying two images out of the host's big store by
# hand would mean writing podman's metadata ourselves.
install -d -m 0750 -o root -g qemu "$pool"; restorecon "$pool"
install -d -m 0700 "$store"
for ref in "$runtime" "$car"; do
    if sp image exists "$ref"; then echo "in the image store already: ${ref##*/}"
    else sp pull -q "$ref" >/dev/null || die "could not pull $ref into the image store. 'A signature was required' means this digest is not signed under this hub's key - a Fleet or pipeline problem, not a host one"; fi
done
# Anything else in there is a stale model or runtime from an earlier Fleet: 4 GB the image should not carry.
# Compared by image ID, not by digest: the runtime image is named by its manifest LIST digest
# (fleet-act-inference.yaml:235), and `podman images` would show the arm64 instance's digest instead.
keep=$(sp image inspect --format '{{.Id}}' "$runtime" "$car") || die "the image store does not list both images after pulling them:  sudo podman --root $store --runroot $storerun images --digests"
while read -r id name; do
    grep -qx "$id" <<<"$keep" || { echo "dropping stale $name from the image store"; sp rmi "$id" >/dev/null; }
done < <(sp images --no-trunc --format '{{.ID}} {{.Repository}}' | sed 's/^sha256://')
# What a Containerfile cannot carry faithfully. backingFsBlockDev is podman's own scratch device node, remade on
# demand. A whiteout (a 0:0 character device: "this file was deleted by an upper layer") would be lost in the
# copy and deleted files would reappear in the embedded image - neither image has one today (the runtime image
# is a single layer, D151), so finding one is a reason to stop, not to work around.
rm -f "$store/overlay/backingFsBlockDev"
special=$(find "$store/overlay" "$store/overlay-images" "$store/overlay-layers" \( -type c -o -type b -o -type p -o -type s \) -print | head -3)
[[ -z $special ]] || die "the image store holds special files that a build cannot copy:
$special
An image with whiteouts cannot be embedded this way. Use the fallback instead (docs/internal/FLEET-VMS.md, 'Images'): clones pull from the hub's registry"

# ---- 2. the OS image, built in the build store. label=disable: the image store sits under /data/libvirt
# (virt_image_t), which a confined build container may not read, and relabelling it would undo what restorecon
# expects there.
t0=$(date +%s)
install -d -m 0700 "$build"
bp pull -q --authfile "$auth" "$base" >/dev/null || die "could not pull $base. 'unauthorized' means this pull secret is not entitled to it:  sudo podman login registry.redhat.io  with an account that is, then run this again"
podman pull -q --authfile "$auth" "$bib" >/dev/null || die "could not pull $bib (same repair as above)"      # the builder itself runs from the host's store
bp build --pull=never --security-opt label=disable -v "$store":/mnt/imagestore:ro \
    --build-arg "BASE=$base" --build-arg "AGENT_VER=$agent_ver" -t "$os" -f "$here/fleet/Containerfile" "$here/fleet" ||
    die "the image build failed - the lines above say where. 'no repositories' or 404s from dnf: the host's subscription is not reaching the build ( sudo subscription-manager status ). A failed signature or key check: the vendored key no longer matches what signs the agent - stop and find out why. Nothing was created but cached layers in $build"
echo "OS image built in $(( $(date +%s) - t0 )) s: $(bp images --format '{{.Size}}' "$os")"

# ---- 3. the disk. As Red Hat's image-mode guide runs the builder, except for WHICH store it is handed (the
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
