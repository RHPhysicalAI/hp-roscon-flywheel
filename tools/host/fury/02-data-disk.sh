#!/bin/bash
# Format the blank 4T nvme as /data and move rootful container storage onto it (root is 70G).
#
# There are two identical 4T drives and the other one already holds somebody's data, and nvmeXn1
# names are just probe order. So don't take a device name: find the one whole disk with no
# partitions and no signature, show it, and ask.
#
#   ./02-data-disk.sh            # pick the blank disk
#   ./02-data-disk.sh /dev/...   # or name it; the same checks still apply
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
mkdir -p "$(dirname "$0")/log"
exec > >(tee -a "$(dirname "$0")/log/$(basename "$0" .sh).log") 2>&1

die() { echo "${0##*/}: $*" >&2; exit 1; }

blank() {
    local sig
    [[ -b $1 ]] || return 1
    [[ $(lsblk -n "$1" | wc -l) -eq 1 ]] || return 1
    sig=$(wipefs -n "$1") || return 1      # a probe that fails is not "blank"
    [[ -z $sig ]]
}

if [[ $# -gt 0 ]]; then
    dev=$(readlink -f "$1")
    blank "$dev" || die "$dev is not blank, leaving it alone"
else
    cand=()
    for d in /dev/nvme?n1; do
        if blank "$d"; then cand+=("$d"); fi
    done
    [[ ${#cand[@]} -eq 1 ]] || die "wanted exactly one blank nvme, got: ${cand[*]:-none}"
    dev=${cand[0]}
fi

(( $(lsblk -bdno SIZE "$dev") > 3 * 10**12 )) || die "$dev is not one of the 4T drives"
! findmnt /data >/dev/null                    || die "/data is already mounted"
! grep -qE '[[:space:]]/data[[:space:]]' /etc/fstab || die "/data is already in fstab"
[[ -z $(podman ps -q) ]]                      || die "rootful containers are running"

lsblk -do NAME,SIZE,MODEL,SERIAL,FSTYPE,LABEL "$dev"
read -rp "mkfs.ext4 on $dev? [y/N] " ok
[[ $ok == y ]] || die "aborted"

mkfs.ext4 -m 1 -L data "$dev"
udevadm settle

# immutable mountpoint: if the mount is ever missing, writes fail instead of landing on /
mkdir -p /data
chattr +i /data
echo 'LABEL=data  /data  ext4  defaults,nofail  0 2' >> /etc/fstab
systemctl daemon-reload
mount /data
[[ $(findmnt -no SOURCE /data) == "$dev" ]] || die "/data did not come from $dev"

mkdir -p /data/containers /data/libvirt/images /data/flywheel

# bind rather than graphroot= so everything keeps its /var/lib/containers labels
cp -a /var/lib/containers/. /data/containers/
echo '/data/containers  /var/lib/containers  none  bind,nofail,x-systemd.requires-mounts-for=/data  0 0' >> /etc/fstab
systemctl daemon-reload
mount /var/lib/containers

semanage fcontext -a -e /var/lib/containers /data/containers
semanage fcontext -a -t virt_image_t '/data/libvirt(/.*)?'
semanage fcontext -a -t lost_found_t '/data/lost\+found(/.*)?'
restorecon -R /data

findmnt /data
findmnt /var/lib/containers
df -h /data
ls -ldZ /data /data/* /var/lib/containers
