#!/bin/bash
# VM network, DNS for the cluster names, and the tailnet route to the guests.
#
# The host resolves through its own dnsmasq as well. Two reasons: cluster names keep working
# when the tailnet is down, and /etc/resolv.conf gets one owner instead of NetworkManager and
# tailscale rewriting it in turns. The switch only happens after dnsmasq has been shown to
# answer both a cluster name and an outside name.
#
# Still to do by hand afterwards, in the tailscale admin console: approve the 10.20.0.0/24 route,
# and add split DNS for sno-flywheel.local pointing at this host's tailnet address.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

die() { echo "${0##*/}: $*" >&2; exit 1; }
answer() { dig +short +time=3 +tries=1 "@$1" "$2" | tail -1; }

command -v dig >/dev/null || dnf install -y bind-utils

# libvirt: the stock NAT network goes quiet, fury-net comes up
active() { [[ $(virsh net-info "$1" 2>/dev/null | awk '/^Active:/ {print $2}') == yes ]]; }   # not grep -q: SIGPIPE + pipefail
if active default; then virsh net-destroy default; fi
virsh net-autostart default --disable 2>/dev/null || true
if ! virsh net-info fury-net >/dev/null 2>&1; then virsh net-define "$here/fury-net.xml"; fi
virsh net-autostart fury-net
active fury-net || virsh net-start fury-net
zone=$(firewall-cmd --get-zone-of-interface=virbr-fury)
[[ $zone == libvirt-routed ]] || die "virbr-fury landed in zone '$zone', expected libvirt-routed"

# guests may ask the host for dns; libvirt's policy rejects everything it doesn't list
if ! firewall-cmd --info-policy=libvirt-to-host | grep -qE 'services:.*\bdns\b'; then
    firewall-cmd --permanent --policy=libvirt-to-host --add-service=dns
    firewall-cmd --reload
fi

grep -q '^nameserver' /run/NetworkManager/no-stub-resolv.conf || die "NetworkManager has no upstream resolvers to forward to"
install -m 0644 "$here/dnsmasq-fury.conf" /etc/dnsmasq.d/fury.conf
restorecon /etc/dnsmasq.d/fury.conf
dnsmasq --test
systemctl enable dnsmasq
systemctl restart dnsmasq

for ip in 127.0.0.1 10.20.0.1; do
    [[ $(answer $ip api.sno-flywheel.local) == 10.20.0.10 ]]          || die "dnsmasq@$ip does not answer the cluster name"
    [[ $(answer $ip foo.apps.sno-flywheel.local) == 10.20.0.10 ]]     || die "dnsmasq@$ip does not answer the apps wildcard"
    [[ -n $(answer $ip quay.io) ]]                                    || die "dnsmasq@$ip cannot resolve outside names"
done

# now the host itself: tailscale and NetworkManager both let go of resolv.conf
tailscale set --accept-dns=false
printf '[main]\nrc-manager=unmanaged\n' > /etc/NetworkManager/conf.d/90-resolv-unmanaged.conf
systemctl reload NetworkManager
printf 'nameserver 127.0.0.1\n' > /etc/resolv.conf
restorecon /etc/resolv.conf
getent hosts api.sno-flywheel.local | grep -q '^10\.20\.0\.10' || die "host does not resolve the cluster name"
getent hosts registry.redhat.io >/dev/null                      || die "host does not resolve outside names"

tailscale set --advertise-routes=10.20.0.0/24

virsh net-list --all
ip -br addr show virbr-fury
ss -lntup | grep -E ':53\s'
cat /etc/resolv.conf
tailscale debug prefs | grep -E 'AdvertiseRoutes|CorpDNS' -A1 | grep -v '^--'
echo "now: approve 10.20.0.0/24 and add split dns sno-flywheel.local -> $(tailscale ip -4) in the admin console"
