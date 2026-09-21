#!/bin/bash
# Slice the GPU (3g + 1g + 1g + 1g) and make the layout come back on its own after a reboot.
#
# The CDI spec is left to the toolkit's own nvidia-cdi-refresh unit, which writes
# /var/run/cdi/nvidia.yaml. That directory outranks /etc/cdi, so a second hand-written spec
# there would just be shadowed - or worse, a stale boot-time one would shadow ours.
#
# Does not reboot. Does nothing if something is computing on the GPU.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

die() { echo "${0##*/}: $*" >&2; exit 1; }

busy=$(nvidia-smi --query-compute-apps=pid,name --format=csv,noheader)
[[ -z $busy ]] || die "gpu is in use: $busy"

install -m 0755 "$here/mig-config.sh" /usr/local/sbin/mig-config.sh
install -m 0644 "$here/mig-config.service" /etc/systemd/system/mig-config.service
[[ -e /etc/sysconfig/mig-config ]] || echo 'MIG_LAYOUT=9,19,19,19' > /etc/sysconfig/mig-config
restorecon /usr/local/sbin/mig-config.sh /etc/systemd/system/mig-config.service /etc/sysconfig/mig-config

systemctl daemon-reload
systemctl enable mig-config.service
systemctl restart mig-config.service
systemctl restart nvidia-cdi-refresh.service

nvidia-smi
nvidia-ctk cdi list
ls -l /etc/cdi /var/run/cdi 2>&1 || true

n=$(nvidia-ctk cdi list | grep -c 'nvidia.com/gpu=0:[0-9]' || true)
[[ $n -eq 4 ]] || die "wanted 4 slices in the cdi spec, found $n"

# left over from 02: fsck wants lost+found labelled, and policy only knows the standard mountpoints
semanage fcontext -l -C | grep '^/data/lost' >/dev/null || semanage fcontext -a -t lost_found_t '/data/lost\+found(/.*)?'
restorecon -R /data/lost+found
