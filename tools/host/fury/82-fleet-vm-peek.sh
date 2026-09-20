#!/bin/bash
# Look inside a fleet VM that cannot be reached - no network, no console login by design, and RHEL's guest agent
# does not run commands - by reading its disk from the host. Read-only for the VM: its disk is copied aside
# (sparse, a few GB, while the VM keeps running), the copy is mounted read-only, and what explains a first boot is
# printed: the network files cloud-init wrote, NetworkManager's settings, cloud-init's log, the journal.
#
#   ./82-fleet-vm-peek.sh <NN>        e.g. 01 for fleet-vm-01
#
# Never printed: /etc/flightctl (the enrolment key lives there). The copy holds that key too, so it sits in the
# root-only pool directory and is removed again on the way out, whatever happens.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ ${1:-} =~ ^[0-9]{2}$ ]] || { echo "usage: ${0##*/} <NN>   (two digits, e.g. 01)" >&2; exit 1; }
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

pool=/data/libvirt/fleet
n=fleet-vm-$1
disk=$pool/$n.qcow2
raw=$pool/.$n-peek.raw
mnt=
loop=
die() { echo "${0##*/}: $*" >&2; exit 1; }
cleanup() {
    if [[ -n $mnt ]]; then umount "$mnt" 2>/dev/null || true; rmdir "$mnt" 2>/dev/null || true; fi
    if [[ -n $loop ]]; then losetup -d "$loop" 2>/dev/null || true; fi
    rm -f "$raw"
    exec >&- 2>&-; wait     # then wait for tee, or sudo eats the last lines
}
trap cleanup EXIT
date -u
[[ -f $disk ]] || die "no $disk - which VMs exist:  ./81-fleet-scale.sh status"
for c in qemu-img losetup blkid journalctl; do command -v "$c" >/dev/null || die "$c is missing"; done

# -U: read the disk although the running VM holds it. The copy can be a moment behind; for logs that is fine.
( umask 077; qemu-img convert -U -O raw "$disk" "$raw" )
loop=$(losetup -f --show -r -P "$raw")
mnt=$(mktemp -d)
root=
for p in "$loop"p*; do
    case $(blkid -o value -s TYPE "$p" 2>/dev/null || true) in
        xfs)  opts=ro,norecovery ;;
        ext4) opts=ro,noload ;;
        *)    continue ;;
    esac
    mount -o "$opts" "$p" "$mnt" 2>/dev/null || continue
    if [[ -d $mnt/ostree/deploy ]]; then root=$p; break; fi
    umount "$mnt"
done
[[ -n $root ]] || die "found no partition with /ostree/deploy on it:  lsblk -f $loop"
dep=$(find "$mnt"/ostree/deploy/*/deploy -mindepth 1 -maxdepth 1 -type d | head -1)
var=$(find "$mnt"/ostree/deploy -mindepth 2 -maxdepth 2 -type d -name var | head -1)
echo "root filesystem $root, deployment ${dep#"$mnt"}, var ${var#"$mnt"}"

section() { echo; echo "==== $*"; }
section "network files cloud-init or anyone wrote (/etc/NetworkManager/system-connections)"
ls -la "$dep/etc/NetworkManager/system-connections/" 2>&1 || true
for f in "$dep"/etc/NetworkManager/system-connections/*; do
    [[ -f $f ]] || continue
    echo "--- ${f##*/}"; cat "$f"
done
section "other places a renderer may have written to"
ls -la "$dep/etc/sysconfig/network-scripts/" 2>&1 | head -8 || true
ls -la "$dep/etc/netplan/" "$dep/etc/systemd/network/" 2>&1 | head -8 || true

section "NetworkManager settings"
ls "$dep/etc/NetworkManager/conf.d/" "$dep/usr/lib/NetworkManager/conf.d/" 2>&1 || true
grep -rHE 'no-auto-default|unmanaged|plugins|managed' "$dep/etc/NetworkManager/NetworkManager.conf" \
    "$dep/etc/NetworkManager/conf.d/" "$dep/usr/lib/NetworkManager/conf.d/" 2>/dev/null || echo "(nothing about auto-default, plugins or unmanaged devices)"

section "cloud-init settings"
if [[ -e $dep/etc/cloud/cloud-init.disabled ]]; then echo "/etc/cloud/cloud-init.disabled exists: the first boot's runcmd ran to its end"; else echo "no /etc/cloud/cloud-init.disabled: runcmd did not finish"; fi
ls "$dep/etc/cloud/cloud.cfg.d/" 2>&1 || true
grep -nE -A4 '^network:|renderers|^system_info:|distro:' "$dep/etc/cloud/cloud.cfg" 2>/dev/null | head -30 || true

section "cloud-init.log - datasource, network, warnings (no file contents are logged there)"
if [[ -f $var/log/cloud-init.log ]]; then
    grep -iE 'nocloud|datasource|network|render|nmconnection|WARN|ERROR|Traceback|Exception|running module|finish' "$var/log/cloud-init.log" |
        grep -viE 'flightctl|BEGIN|PRIVATE' | tail -70 | cut -c1-240
else echo "no $var/log/cloud-init.log - cloud-init never ran, or /var/log is not where this script looks:"; ls "$var/log" 2>&1 | head -20; fi

section "cloud-init-output.log, last lines"
tail -25 "$var/log/cloud-init-output.log" 2>/dev/null | grep -viE 'BEGIN|PRIVATE' | cut -c1-240 || echo "(none)"

section "journal: NetworkManager and cloud-init, first boot"
if [[ -d $var/log/journal ]]; then
    journalctl -D "$var/log/journal" --no-pager -o short-monotonic -u NetworkManager -u cloud-init-local -u cloud-init 2>/dev/null |
        grep -iE 'enp1s0|nic0|cloud-init|keyfile|connection|manag|device|error|fail|warn' | head -60 | cut -c1-240
else echo "no persistent journal on this disk ($var/log/journal)"; fi

section "the device agent: is its enrolment config there (name, mode and size only), and what does it say"
ls -laZ "$dep/etc/flightctl/" 2>&1 | cut -c1-200 || true
if [[ -d $var/log/journal ]]; then
    journalctl -D "$var/log/journal" --no-pager -o short-monotonic -u flightctl-agent 2>/dev/null |
        grep -viE 'BEGIN|PRIVATE' | tail -40 | cut -c1-260
    echo "--- denials and failed units"
    journalctl -D "$var/log/journal" --no-pager -o short-monotonic 2>/dev/null |
        grep -E 'avc:  denied|Failed to start|failed with result|Dependency failed' | tail -15 | cut -c1-260
fi
echo
echo "done - the copy of the disk is removed on the way out"
