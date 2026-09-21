#!/bin/bash
# What the host needs before the hub can be installed: nmstatectl (the installer shells out to it to
# turn the static network config into NetworkManager files and fails without it), and a forward and
# reverse DNS record for the node itself. Guests reach public NTP through the uplink, so the host does
# not need to serve time.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

die() { echo "${0##*/}: $*" >&2; exit 1; }
answer() { dig +short +time=3 +tries=1 @10.20.0.1 "$@" | tail -1; }

command -v nmstatectl >/dev/null || dnf install -y nmstate
nmstatectl --version

install -m 0644 "$here/dnsmasq-fury.conf" /etc/dnsmasq.d/fury.conf
restorecon /etc/dnsmasq.d/fury.conf
dnsmasq --test
systemctl restart dnsmasq

[[ $(answer master-0.sno-flywheel.local) == 10.20.0.10 ]]      || die "no A record for the node"
[[ $(answer -x 10.20.0.10) == master-0.sno-flywheel.local. ]]  || die "no PTR record for the node"
[[ $(answer api.sno-flywheel.local) == 10.20.0.10 ]]           || die "api record lost"
[[ -z $(answer nowildcard.sno-flywheel.local) ]]               || die "the zone answers names it should not"

# nothing else may hold the node's address, and the pre-test guest must be gone
if ping -c1 -W1 10.20.0.10 >/dev/null 2>&1; then die "something already answers on 10.20.0.10"; fi
if virsh dominfo rhcos-pretest >/dev/null 2>&1; then die "the pre-test guest still exists: ./30-vm-pretest.sh down"; fi

firewall-cmd --zone=public --query-masquerade
free -g | awk '/^Mem:/ {print "memory available: " $7 " GiB"}'
df -h /data | tail -1
echo "host is ready for the install"
