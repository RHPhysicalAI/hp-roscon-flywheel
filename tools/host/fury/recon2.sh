#!/bin/bash
# Read-only look before the network step, now that libvirt is installed: its firewalld zones,
# the default network it created, tailscale's forwarding rules, and who answers DNS. Changes nothing.
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
mkdir -p "$(dirname "$0")/log"
exec > >(tee "$(dirname "$0")/log/$(basename "$0" .sh).log") 2>&1

date -u
echo "## libvirt networks";  virsh net-list --all; virsh net-dumpxml default 2>&1
echo "## firewalld zones";   firewall-cmd --get-active-zones
for z in libvirt libvirt-routed; do echo "# $z"; firewall-cmd --zone="$z" --list-all 2>&1; done
echo "## firewalld policies"
for p in $(firewall-cmd --get-policies); do
    echo "# $p"; firewall-cmd --info-policy="$p" 2>&1 | grep -E 'priority|target|ingress-zones|egress-zones|masquerade' | sed 's/^/   /'
done
echo "## nft tables";        nft list tables
echo "## tailscale forward"; iptables -S ts-forward 2>&1; iptables -t nat -S ts-postrouting 2>&1
echo "## tailscale prefs";   tailscale debug prefs | grep -E 'AdvertiseRoutes|RouteAll|CorpDNS|NoSNAT|NetfilterMode'
echo "## port 53";           ss -lntup | grep -E ':53\s'
echo "## resolv.conf";       ls -l /etc/resolv.conf; grep -v '^#' /etc/resolv.conf
echo "## nm";                nmcli -t -f NAME,DEVICE,TYPE con show --active; ls /run/NetworkManager/*resolv* 2>&1
echo "## forwarding";        sysctl net.ipv4.ip_forward net.ipv4.conf.all.rp_filter net.ipv4.conf.tailscale0.rp_filter
echo "## dnsmasq";           systemctl is-enabled dnsmasq; ls /etc/dnsmasq.d/ 2>&1; grep -vE '^\s*(#|$)' /etc/dnsmasq.conf
