#!/bin/bash
# Console boot, no suspend, no fabric manager (single GPU, it only ever fails here).
# The desktop greeter asks for suspend every 15 idle minutes and the nvidia driver refuses,
# which stalls consoles and bounces the VPN. A display server also pins the GPU against MIG changes.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
mkdir -p "$(dirname "$0")/log"
exec > >(tee -a "$(dirname "$0")/log/$(basename "$0" .sh).log") 2>&1

systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
systemctl set-default multi-user.target
systemctl disable --now gdm
systemctl disable --now nvidia-fabricmanager
systemctl reset-failed nvidia-fabricmanager || true

systemctl get-default
systemctl is-enabled gdm nvidia-fabricmanager suspend.target || true
