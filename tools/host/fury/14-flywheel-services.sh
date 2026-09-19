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
# once the host is enrolled the policy is the Fleet's quadlet, not ours
if [[ -s /etc/flightctl/config.yaml ]]; then
    install -m 0644 "$here"/flywheel/so-arm-sim.container "$here"/flywheel/act-coordinator.container /etc/containers/systemd/
    echo "enrolled device: act-inference.container left out, the policy comes from RHEM"
else
    install -m 0644 "$here"/flywheel/*.container        /etc/containers/systemd/
fi
install -m 0644 "$here/flywheel/disk-guard.service"     /etc/systemd/system/
install -m 0644 "$here/flywheel/fury-flywheel.target"   /etc/systemd/system/
restorecon -R /usr/local/sbin /etc/containers/systemd /etc/systemd/system/disk-guard.service /etc/systemd/system/fury-flywheel.target
mkdir -p /data/flywheel/bags /data/flywheel/episodes /data/flywheel/eval

# first, so that nothing below can leave systemd looking at stale unit files
systemctl daemon-reload

# A container that systemd started carries its unit's name in a label and is left alone: this script
# installs, it does not stop services. Only a hand-started leftover would collide with a unit's name.
for c in act-coordinator act-inference so-arm-sim; do
    podman container exists "$c" || continue
    unit=$(podman inspect --format '{{index .Config.Labels "PODMAN_SYSTEMD_UNIT"}}' "$c" 2>/dev/null || true)
    if [[ -n $unit && $unit != '<no value>' ]]; then
        echo "$c is running from $unit - restart that unit to pick up what was just installed"
    else
        podman rm -f -t 10 "$c" >/dev/null 2>&1 || true
        echo "removed hand-started $c"
    fi
done

systemctl list-unit-files 'so-arm-sim*' 'act-*' 'disk-guard*' 'fury-flywheel*' --no-pager
# full path: sudo's secure_path on RHEL leaves out /usr/local/sbin
/usr/local/sbin/fury-mode status
if [[ -s /etc/flightctl/config.yaml ]]; then
    echo "next: sudo systemctl restart so-arm-sim.service   (the RHEM-managed policy follows the sim)"
else
    echo "next: fury-mode flywheel   (no sudo in front - it asks for it itself; sudo cannot find /usr/local/sbin)"
fi
