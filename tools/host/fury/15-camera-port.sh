#!/bin/bash
# Let the hub reach the sim's camera bridge on this host. One-time bring-up.
#
# The flywheel dashboard is served over https, and a browser will not load an http stream into an https page, so
# the two camera streams go through the hub's router (gitops/flywheel/so-arm-sim.yaml: Route sim-cameras ->
# 10.20.0.1:8081). Guests may not open connections to this host except on listed ports (policy libvirt-to-host,
# 06-network.sh); this adds the bridge's port to that list.
#
# Who reaches 10.20.0.1:8081 then: the guests (the hub), through this entry. Not the lab uplink: the entry opens
# nothing in the uplink's zone, and the lab network has no route to 10.20.0.1. Peers on the operator's tailnet reach
# it with or without the entry, because this host routes 10.20.0.0/24 for the tailnet - accepted: it is the admin
# network.
#
#   ./15-camera-port.sh open      8081/tcp in the libvirt-to-host policy, now and after a reboot
#   ./15-camera-port.sh status    what is open, and whether the bridge answers
#   ./15-camera-port.sh close     take it out again
#
# This project was developed with assistance from AI tools.
set -euo pipefail
case ${1:-} in
open|status|close) [[ $EUID -eq 0 ]] || exec sudo "$0" "$@" ;;
*) echo "usage: ${0##*/} open | status | close" >&2; exit 1 ;;
esac
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1
# Under sudo the terminal can go away before tee has written the last lines - wait for it on the way out.
trap 'exec >&- 2>&-; wait' EXIT

policy=libvirt-to-host
port=8081/tcp
die() { echo "${0##*/}: $*" >&2; exit 1; }
is_open() { firewall-cmd -q "$@" --policy=$policy --query-port=$port; }
zone_ok() { [[ $(firewall-cmd --get-zone-of-interface=virbr-fury 2>/dev/null || true) == libvirt-routed ]]; }

command -v firewall-cmd >/dev/null || die "firewall-cmd is missing"
firewall-cmd --info-policy=$policy >/dev/null 2>&1 || die "no firewalld policy $policy - is this the host 06-network.sh set up?"
date -u

case $1 in
open)
    zone_ok || die "virbr-fury is not in zone libvirt-routed - ./06-network.sh first"
    # Runtime and permanent, and no --reload: a reload throws away runtime-only state, and the zone binding of the
    # hub's bridge is libvirt's runtime state - with the hub VM running.
    is_open             || firewall-cmd --policy=$policy --add-port=$port
    is_open --permanent || firewall-cmd --permanent --policy=$policy --add-port=$port
    zone_ok || die "virbr-fury left its zone:  sudo virsh net-destroy fury-net && sudo virsh net-start fury-net  - with the hub VM shut down"
    echo "firewalld: $port open in policy $policy (guests to this host). The lab uplink is not opened; tailnet peers reach 10.20.0.1 with or without it (accepted: the admin network)"
    ;;
close)
    ! is_open             || firewall-cmd --policy=$policy --remove-port=$port
    ! is_open --permanent || firewall-cmd --permanent --policy=$policy --remove-port=$port
    echo "closed $port in $policy"
    ;;
esac
printf '%-24s runtime %s, permanent %s\n' "$port in $policy" "$(is_open && echo open || echo closed)" "$(is_open --permanent && echo open || echo closed)"
printf '%-24s %s\n' "bridge on 10.20.0.1:8081" "$(curl -s -o /dev/null -m 3 -w '%{http_code}' http://10.20.0.1:8081/static || true)"
