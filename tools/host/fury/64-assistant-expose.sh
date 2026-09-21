#!/bin/bash
# Let the hub reach the coding assistant's API on this host. One-time bring-up.
#
# The assistant listens on this host's hub-side address, 10.20.0.1:8000 (llm-assistant.container). This lists
# that port in the libvirt-to-host policy so that guests - the hub - may connect. On the hub it is Service
# assistant.flywheel.svc (gitops/flywheel/assistant.yaml), used by developer workspaces; there is no Route to it.
#
# Who reaches 10.20.0.1:8000 then: the guests (the hub), through the policy entry. Not the lab uplink. Peers on
# the operator's tailnet reach it with or without the entry, because this host routes 10.20.0.0/24 for the tailnet
# - accepted: it is the admin network. The API has no key: whoever reaches the port can use the model.
#
#   ./64-assistant-expose.sh open      8000/tcp in the libvirt-to-host policy, now and after a reboot
#   ./64-assistant-expose.sh status    the policy, and whether the assistant answers on that address
#   ./64-assistant-expose.sh close     close the port
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
port=8000/tcp
die() { echo "${0##*/}: $*" >&2; exit 1; }
is_open() { firewall-cmd -q "$@" --policy=$policy --query-port=$port; }
zone_ok() { [[ $(firewall-cmd --get-zone-of-interface=virbr-fury 2>/dev/null || true) == libvirt-routed ]]; }

command -v firewall-cmd >/dev/null || die "firewall-cmd is missing"
firewall-cmd --info-policy=$policy >/dev/null 2>&1 || die "no firewalld policy $policy - is this the host 06-network.sh set up?"
date -u

# A first version forwarded a systemd socket on 8001 to loopback; SELinux refuses systemd that bind. Take it out.
if [[ -e /etc/systemd/system/llm-assistant-proxy.socket || -e /etc/systemd/system/llm-assistant-proxy.service ]]; then
    systemctl disable --now llm-assistant-proxy.socket 2>/dev/null || true
    for u in llm-assistant-proxy.socket llm-assistant-proxy.service; do
        [[ ! -e /etc/systemd/system/$u ]] || unlink "/etc/systemd/system/$u"
    done
    systemctl daemon-reload
    systemctl reset-failed llm-assistant-proxy.socket 2>/dev/null || true
    echo "removed the earlier socket forwarder"
fi
for perm in "" --permanent; do
    # shellcheck disable=SC2086
    if firewall-cmd -q $perm --policy=$policy --query-port=8001/tcp; then firewall-cmd -q $perm --policy=$policy --remove-port=8001/tcp; fi
done

case $1 in
open)
    zone_ok || die "virbr-fury is not in zone libvirt-routed - ./06-network.sh first"
    grep -q -- '--host 10.20.0.1' /etc/containers/systemd/llm-assistant.container 2>/dev/null ||
        die "the installed assistant still listens on loopback. In order:  ./63-assistant-install.sh   then  sudo systemctl restart llm-assistant  (about three minutes to load)   then this again"
    # Runtime and permanent, and no --reload: a reload throws away runtime-only state, and the zone binding of the
    # hub's bridge is libvirt's runtime state - with the hub VM running.
    is_open             || firewall-cmd --policy=$policy --add-port=$port
    is_open --permanent || firewall-cmd --permanent --policy=$policy --add-port=$port
    zone_ok || die "virbr-fury left its zone:  sudo virsh net-destroy fury-net && sudo virsh net-start fury-net  - with the hub VM shut down"
    echo "$port open in policy $policy (guests to this host)"
    ;;
close)
    ! is_open             || firewall-cmd --policy=$policy --remove-port=$port
    ! is_open --permanent || firewall-cmd --permanent --policy=$policy --remove-port=$port
    echo "closed $port in $policy"
    ;;
esac
printf '%-30s runtime %s, permanent %s\n' "$port in $policy" "$(is_open && echo open || echo closed)" "$(is_open --permanent && echo open || echo closed)"
printf '%-30s %s   (000 while the model loads, or in flywheel mode)\n' "assistant on 10.20.0.1:8000" "$(curl -s -o /dev/null -m 4 -w '%{http_code}' http://10.20.0.1:8000/v1/models || true)"
