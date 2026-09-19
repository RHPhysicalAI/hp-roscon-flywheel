#!/bin/bash
# Put the robot loop under systemd instead of hand-started containers: sim and policy as quadlets
# behind one target, the recorder as a unit that cannot run without the disk guard, and fury-mode
# to move the GPU between "flywheel" (MIG off) and "tenants" (MIG on).
#
# Installs only. Nothing starts and the MIG state does not change until:  sudo fury-mode flywheel
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

die() { echo "${0##*/}: $*" >&2; exit 1; }
for f in fury-mode.sh mig-config.sh disk-guard.sh flywheel/so-arm-sim.container flywheel/act-inference.container \
         flywheel/act-coordinator.container flywheel/disk-guard.service flywheel/fury-flywheel.target; do
    [[ -f $here/$f ]] || die "missing $f next to this script"
done

# let quadlet check the container files before anything is installed
QUADLET_UNIT_DIRS=$here/flywheel /usr/libexec/podman/quadlet -dryrun >/dev/null || die "quadlet rejected the container files"

install -m 0755 "$here/fury-mode.sh"   /usr/local/sbin/fury-mode
install -m 0755 "$here/mig-config.sh"  /usr/local/sbin/mig-config.sh
install -m 0755 "$here/disk-guard.sh"  /usr/local/sbin/disk-guard.sh
install -m 0644 "$here"/flywheel/*.container            /etc/containers/systemd/
install -m 0644 "$here/flywheel/disk-guard.service"     /etc/systemd/system/
install -m 0644 "$here/flywheel/fury-flywheel.target"   /etc/systemd/system/
restorecon -R /usr/local/sbin /etc/containers/systemd /etc/systemd/system/disk-guard.service /etc/systemd/system/fury-flywheel.target
mkdir -p /data/flywheel/bags /data/flywheel/episodes /data/flywheel/eval

# leftovers from the hand-run trials would collide with the units' container names
for c in act-coordinator act-inference so-arm-sim; do
    if podman container exists "$c"; then podman stop -t 10 "$c" >/dev/null; podman rm "$c" >/dev/null; echo "removed hand-started $c"; fi
done

systemctl daemon-reload
systemctl list-unit-files 'so-arm-sim*' 'act-*' 'disk-guard*' 'fury-flywheel*' --no-pager
# full path: sudo's secure_path on RHEL leaves out /usr/local/sbin
/usr/local/sbin/fury-mode status
echo "next: fury-mode flywheel   (no sudo in front - it asks for it itself; sudo cannot find /usr/local/sbin)"
