#!/bin/bash
# One GPU, two ways to use it, never both at once:
#
#   fury-mode flywheel   MIG off. The sim renders its cameras on the GPU (OpenGL only exists without MIG)
#                        and shares it with policy serving and training, the way the desktop did.
#   fury-mode tenants    MIG on, 3g + 1g + 1g + 1g. Isolated slices for compute tenants; no graphics.
#   fury-mode status
#
# The GPU has to be idle to change MIG mode, so a switch stops the robot loop first. The choice is
# written to /etc/sysconfig/mig-config, so a reboot comes back in the same mode.
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

cfg=/etc/sysconfig/mig-config
loop=(act-coordinator.service act-inference.service so-arm-sim.service)
die() { echo "fury-mode: $*" >&2; exit 1; }
mig() { nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader; }

drain() {
    systemctl disable --now fury-flywheel.target 2>/dev/null
    systemctl stop "${loop[@]}" 2>/dev/null
    local busy; busy=$(nvidia-smi --query-compute-apps=pid,name --format=csv,noheader)
    [[ -z $busy ]] || die "the gpu is still in use, stop this first: $busy"
}

status() {
    echo "mig mode:  $(mig)    configured layout: $(sed -n 's/^MIG_LAYOUT=//p' "$cfg" 2>/dev/null)"
    nvidia-smi -L | sed 's/ (UUID.*//'
    for u in fury-flywheel.target "${loop[@]}" disk-guard.service; do
        printf '%-28s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null)"
    done
    echo "bags: $(find /data/flywheel/bags -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)   /data free: $(df -h --output=avail /data | tail -1 | tr -d ' ')"
}

case ${1:-status} in
flywheel)
    drain
    echo 'MIG_LAYOUT=none' > "$cfg"
    systemctl restart mig-config.service       || die "mig-config failed: journalctl -u mig-config"
    systemctl restart nvidia-cdi-refresh.service
    [[ $(mig) == Disabled ]]                   || die "MIG is still on"
    systemctl enable --now fury-flywheel.target
    status
    ;;
tenants)
    drain
    echo 'MIG_LAYOUT=9,19,19,19' > "$cfg"
    systemctl restart mig-config.service       || die "mig-config failed: journalctl -u mig-config"
    systemctl restart nvidia-cdi-refresh.service
    n=$(nvidia-ctk cdi list 2>/dev/null | grep -c 'nvidia.com/gpu=0:[0-9]' || true)
    [[ $n -eq 4 ]]                             || die "wanted 4 slices in the cdi spec, found $n"
    status
    ;;
status) status ;;
*) echo "usage: fury-mode flywheel|tenants|status" >&2; exit 1 ;;
esac
