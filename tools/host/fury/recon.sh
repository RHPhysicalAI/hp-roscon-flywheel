#!/bin/bash
# Read-only look at the things that need root to see: who holds the GPU, firewalld zones,
# what owns port 53. Changes nothing.
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
mkdir -p "$(dirname "$0")/log"
exec > >(tee "$(dirname "$0")/log/$(basename "$0" .sh).log") 2>&1

date -u
echo "## gpu holders";  fuser -v /dev/nvidia* 2>&1
echo "## firewalld";    firewall-cmd --get-default-zone; firewall-cmd --get-active-zones
for z in public trusted libvirt libvirt-routed; do
    echo "# $z"; firewall-cmd --zone="$z" --list-all 2>&1
done
echo "# policies: $(firewall-cmd --get-policies)"
echo "## nft";          nft list ruleset | grep -E '^table |chain ts-'
echo "## nm dns";       grep -rnE '^(dns|rc-manager)' /etc/NetworkManager/NetworkManager.conf /etc/NetworkManager/conf.d/ 2>/dev/null
echo "## port 53";      ss -lntup | grep -E ':53\s'
echo "## containers";   podman ps -a
echo "## cdi";          ls -l /etc/cdi /var/run/cdi 2>&1
