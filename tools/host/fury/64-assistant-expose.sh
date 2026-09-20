#!/bin/bash
# Let the hub reach the coding assistant's API on this host. One-time bring-up.
#
# The API stays on loopback (llm-assistant.container). This adds one listener on the host's hub-side address,
# 10.20.0.1:8001, forwarded to it by systemd (llm-assistant-proxy.socket/.service), and lists that port in the
# libvirt-to-host policy so that guests - the hub - may connect. On the hub it is Service assistant.flywheel.svc
# (gitops/flywheel/assistant.yaml), used by the dashboard's backend and by workspaces; there is no Route to it.
#
# Who reaches 10.20.0.1:8001 then: the guests (the hub), through the policy entry. Not the lab uplink. Peers on
# the operator's tailnet reach it with or without the entry, because this host routes 10.20.0.0/24 for the tailnet
# - accepted: it is the admin network. The API has no key: whoever reaches the port can use the model.
#
#   ./64-assistant-expose.sh open      install the listener, open 8001/tcp to the guests, now and after a reboot
#   ./64-assistant-expose.sh status    listener, policy, and whether the assistant answers through it
#   ./64-assistant-expose.sh close     remove the listener and close the port
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
port=8001/tcp
units=(llm-assistant-proxy.socket llm-assistant-proxy.service)
die() { echo "${0##*/}: $*" >&2; exit 1; }
is_open() { firewall-cmd -q "$@" --policy=$policy --query-port=$port; }
zone_ok() { [[ $(firewall-cmd --get-zone-of-interface=virbr-fury 2>/dev/null || true) == libvirt-routed ]]; }
answer() { curl -s -o /dev/null -m 4 -w '%{http_code}' "http://$1/v1/models" || true; }

command -v firewall-cmd >/dev/null || die "firewall-cmd is missing"
firewall-cmd --info-policy=$policy >/dev/null 2>&1 || die "no firewalld policy $policy - is this the host 06-network.sh set up?"
[[ -x /usr/lib/systemd/systemd-socket-proxyd ]] || die "systemd-socket-proxyd is missing"
date -u

case $1 in
open)
    zone_ok || die "virbr-fury is not in zone libvirt-routed - ./06-network.sh first"
    for u in "${units[@]}"; do
        [[ -f $here/flywheel/$u ]] || die "missing flywheel/$u next to this script"
        install -m 0644 -o root -g root "$here/flywheel/$u" "/etc/systemd/system/$u"
    done
    systemctl daemon-reload
    systemctl enable --now llm-assistant-proxy.socket || die "the listener did not start: journalctl -u llm-assistant-proxy.socket -n 20   Then: ./${0##*/} close"
    # Runtime and permanent, and no --reload: a reload throws away runtime-only state, and the zone binding of the
    # hub's bridge is libvirt's runtime state - with the hub VM running.
    is_open             || firewall-cmd --policy=$policy --add-port=$port
    is_open --permanent || firewall-cmd --permanent --policy=$policy --add-port=$port
    zone_ok || die "virbr-fury left its zone:  sudo virsh net-destroy fury-net && sudo virsh net-start fury-net  - with the hub VM shut down"
    echo "listener on 10.20.0.1:8001 -> 127.0.0.1:8000; $port open in policy $policy (guests to this host)"
    ;;
close)
    systemctl disable --now llm-assistant-proxy.socket 2>/dev/null || true
    systemctl stop llm-assistant-proxy.service 2>/dev/null || true
    for u in "${units[@]}"; do [[ ! -f /etc/systemd/system/$u ]] || unlink "/etc/systemd/system/$u"; done
    systemctl daemon-reload
    ! is_open             || firewall-cmd --policy=$policy --remove-port=$port
    ! is_open --permanent || firewall-cmd --permanent --policy=$policy --remove-port=$port
    echo "removed the listener and closed $port in $policy"
    ;;
esac
printf '%-30s %s\n' "llm-assistant-proxy.socket" "$(systemctl is-active llm-assistant-proxy.socket 2>/dev/null || true)"
printf '%-30s runtime %s, permanent %s\n' "$port in $policy" "$(is_open && echo open || echo closed)" "$(is_open --permanent && echo open || echo closed)"
printf '%-30s %s\n' "assistant on loopback" "$(answer 127.0.0.1:8000)"
printf '%-30s %s   (000 with the assistant down is expected: flywheel mode)\n' "assistant through the listener" "$(answer 10.20.0.1:8001)"
