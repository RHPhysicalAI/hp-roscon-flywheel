#!/bin/bash
# Virtualization stack, the container toolkit, and modules-extra for the running kernel.
#
# nvidia-container-toolkit comes from RHEL supplementary, same place as the driver - no nvidia repo.
# modules-extra is there for xt_mark: without it tailscaled can't install its forwarding rules
# and the box can't act as a subnet router.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
mkdir -p "$(dirname "$0")/log"
exec > >(tee -a "$(dirname "$0")/log/$(basename "$0" .sh).log") 2>&1

kver=$(uname -r)
case $kver in
    *+64k) extra=kernel-64k-modules-extra-${kver%.aarch64+64k} ;;
    *)     extra=kernel-modules-extra-${kver%.aarch64} ;;
esac

dnf install -y libvirt qemu-kvm virt-install libvirt-client dnsmasq tmux skopeo jq \
    "$extra" nvidia-container-toolkit-1.20.0
modprobe xt_mark

# modular daemons; leave the monolithic libvirtd off
for d in qemu network nodedev nwfilter secret storage interface; do
    systemctl enable --now "virt${d}d.socket"
done

rpm -q libvirt qemu-kvm virt-install nvidia-container-toolkit "$extra"
lsmod | grep -w xt_mark
virsh -c qemu:///system version
virsh net-list --all
nvidia-ctk --version
systemctl list-unit-files 'nvidia-cdi*' --no-pager || true
