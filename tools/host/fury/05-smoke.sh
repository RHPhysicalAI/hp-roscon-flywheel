#!/bin/bash
# Each slice, from a container: proves MIG + CDI + arm64 CUDA userspace + the 64k kernel together.
# Run it before the reboot and again after - the second run is the "did it all come back" check.
#
# Containers are left confined. If SELinux keeps them off the device nodes, turn on
# container_use_devices and say so, rather than dropping labels for the container.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

img=nvcr.io/nvidia/cuda:13.0.0-base-ubi9
die() { echo "${0##*/}: $*" >&2; exit 1; }
slice() { podman run --rm --device "nvidia.com/gpu=$1" "$img" nvidia-smi -L; }

date -u
uptime -s
systemctl get-default
systemctl is-active mig-config.service nvidia-cdi-refresh.service || true
findmnt -no SOURCE,TARGET /data               || die "/data is not mounted"
findmnt -no SOURCE,TARGET /var/lib/containers || die "/var/lib/containers is not on /data"
nvidia-smi --query-gpu=mig.mode.current --format=csv,noheader
nvidia-smi -L
nvidia-ctk cdi list

podman pull -q "$img"
podman info --format 'graphroot {{.Store.GraphRoot}}'

if ! slice 0:0; then
    echo "confined container could not reach the gpu - enabling container_use_devices"
    setsebool -P container_use_devices on
    slice 0:0 || die "still failing; see: ausearch -m avc -ts recent"
fi
getsebool container_use_devices

for i in 0 1 2 3; do
    echo "## 0:$i"
    out=$(slice 0:$i)
    echo "$out"
    [[ $(grep -c 'MIG ' <<<"$out") -eq 1 ]] || die "slice 0:$i should see exactly one MIG device"
done

echo "## tailscale health"
tailscale status | sed -n '/Health check/,$p'
echo "## suspend attempts this boot"
journalctl -b -q --no-pager -g 'PM: suspend entry' | tail -3 || true
