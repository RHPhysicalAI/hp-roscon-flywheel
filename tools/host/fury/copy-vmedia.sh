#!/bin/bash
# Keep a copy of the installer image that is attached through the BMC's virtual media. If it is
# ever detached there is no putting it back from outside the lab.
#
# The device is flaky - the kernel resets it over and over during boot - so don't trust the read:
# count USB resets around it, insist on the full size, and show what the image actually is.
#
#   ./copy-vmedia.sh           # copy to /data/iso/<label>.iso
#   ./copy-vmedia.sh verify    # read the device a second time and compare with the copy
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

die() { echo "${0##*/}: $*" >&2; exit 1; }
resets() { journalctl -k -b --no-pager | grep -c 'reset high-speed USB' || true; }

src=$(lsblk -dno PATH,TRAN,MODEL | awk '$2 == "usb" && /File-Stor/ {print $1}')
[[ -n $src && $(wc -l <<<"$src") -eq 1 ]] || die "wanted one virtual-media disk, found: ${src:-none}"
findmnt /data >/dev/null || die "/data is not mounted"

label=$(lsblk -dno LABEL "$src")
dst=/data/iso/${label:-vmedia}.iso
size=$(blockdev --getsize64 "$src")
echo "$src -> $dst ($((size / 1024 / 1024)) MiB)"

if [[ ${1:-} == verify ]]; then
    [[ -f $dst ]] || die "$dst is not there yet"
    before=$(resets)
    cmp "$src" "$dst" && echo "device and copy are identical"
    echo "usb resets during the read: $(( $(resets) - before ))"
    exit
fi

[[ ! -e $dst ]] || die "$dst already exists"
mkdir -p /data/iso
before=$(resets)
date -u
dd if="$src" of="$dst.part" bs=4M iflag=direct status=progress
date -u
[[ $(stat -c %s "$dst.part") -eq $size ]] || die "short read, leaving $dst.part"
mv "$dst.part" "$dst"
echo "usb resets during the copy: $(( $(resets) - before ))"
sha256sum "$dst" | tee "$dst.sha256"

# stock image or somebody's custom one?
m=$(mktemp -d)
mount -o loop,ro "$dst" "$m"
ls -la "$m"
head -20 "$m/.treeinfo" 2>/dev/null || true
cat "$m/media.repo" 2>/dev/null || true
find "$m" -maxdepth 2 \( -name '*.cfg' -o -name 'ks*' -o -iname '*nvidia*' -o -iname '*dgx*' \) 2>/dev/null | head
umount "$m"
rmdir "$m"
