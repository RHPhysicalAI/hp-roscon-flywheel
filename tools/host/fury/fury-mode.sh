#!/bin/bash
# One GPU, two ways to use it, never both at once:
#
#   fury-mode flywheel   MIG off. The sim renders its cameras on the GPU (OpenGL only exists without MIG)
#                        and shares it with policy serving and training, the way the desktop did.
#   fury-mode tenants    MIG on, 3g + 1g + 1g + 1g. Isolated slices for compute tenants; no graphics.
#                        Starts the coding assistant on the large slice if its unit is installed.
#   fury-mode status
#
# The GPU has to be idle to change MIG mode, so a switch stops the robot loop first. The choice is
# written to /etc/sysconfig/mig-config, so a reboot comes back in the same mode.
#
# Once the host is enrolled the policy is not this script's to stop: it is the Fleet's quadlet, run by
# flightctl-agent. It is taken out of service from the hub (flightctl app stop - an override that
# survives rollouts), and this script only refuses to go on while it is up.
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

cfg=/etc/sysconfig/mig-config
loop=(flywheel-runner.service act-coordinator.service act-inference.service so-arm-sim.service)   # act-inference.service: before enrolment only
assistant=llm-assistant.service          # act 2's tenant on slice 0:0 - a host unit of ours, stopped for every switch
policy='act-inference-*-act-inference.service'                             # after: <app id>-<quadlet>, named by the agent
telemetry=dcgm-exporter.service          # runs in both modes (70-dcgm.sh). Not installed is fine: every call below is quiet
die() { echo "fury-mode: $*" >&2; exit 1; }
mig() { nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader; }
policy_state() { systemctl list-units --all --no-legend --plain "$policy" | awk '{print $3 "/" $4}'; }

drain() {
    # first, so that a refusal leaves the sim and the recorder running. A loaded but idle policy holds
    # the device nodes without showing up as a compute process.
    case $(policy_state) in
        ''|inactive/*|failed/*) ;;
        *) die "the RHEM-managed policy is up and holds the gpu. From the laptop:
    flightctl app stop device/<name> --name act-inference --yes
then run this again. Hub unreachable: sudo systemctl stop '${policy%-act-inference.service}-flightctl-quadlet-app.target'" ;;
    esac
    # hours of work would go with the stop below: make the operator end a run on purpose
    if pgrep -f 'lerobot-train|assemble_dataset' >/dev/null || podman pod exists eval-rig 2>/dev/null; then
        die "a training run or an eval is in progress (journalctl -u flywheel-runner -n 5). Wait for it, or end it yourself:
    sudo systemctl stop flywheel-runner.service flywheel-eval.service"
    fi
    systemctl disable --now fury-flywheel.target 2>/dev/null
    systemctl stop "${loop[@]}" "$assistant" 2>/dev/null
    # DCGM holds the driver open without being a compute process, and MIG mode does not change under a client.
    # From here on it comes back on every way out, a refusal included.
    systemctl stop "$telemetry" 2>/dev/null
    trap 'systemctl start --no-block "$telemetry" 2>/dev/null' EXIT
    local busy; busy=$(nvidia-smi --query-compute-apps=pid,name --format=csv,noheader)
    [[ -z $busy ]] || die "the gpu is still in use, stop this first: $busy"
}

status() {
    echo "mig mode:  $(mig)    configured layout: $(sed -n 's/^MIG_LAYOUT=//p' "$cfg" 2>/dev/null)"
    nvidia-smi -L | sed 's/ (UUID.*//'
    for u in fury-flywheel.target "${loop[@]}" "$assistant" "$telemetry" disk-guard.service flightctl-agent.service; do
        printf '%-28s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null)"
    done
    printf '%-28s %s\n' "policy (rhem-managed)" "$(policy_state)"
    echo "bags: $(find /data/flywheel/bags -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)   /data free: $(df -h --output=avail /data | tail -1 | tr -d ' ')"
}

case ${1:-status} in
flywheel)
    drain
    echo 'MIG_LAYOUT=none' > "$cfg"
    systemctl restart mig-config.service       || die "mig-config failed: journalctl -u mig-config"
    systemctl restart nvidia-cdi-refresh.service
    [[ $(mig) == Disabled ]]                   || die "MIG is still on"
    systemctl start "$telemetry" 2>/dev/null   # only now: it enumerates the gpu, or its instances, once at start
    systemctl enable --now fury-flywheel.target
    status
    if [[ -s /etc/flightctl/config.yaml ]]; then echo "next, from the laptop: flightctl app start device/<name> --name act-inference --yes"; fi
    ;;
tenants)
    drain
    echo 'MIG_LAYOUT=9,19,19,19' > "$cfg"
    systemctl restart mig-config.service       || die "mig-config failed: journalctl -u mig-config"
    systemctl restart nvidia-cdi-refresh.service
    n=$(nvidia-ctk cdi list 2>/dev/null | grep -c 'nvidia.com/gpu=0:[0-9]' || true)
    [[ $n -eq 4 ]]                             || die "wanted 4 slices in the cdi spec, found $n"
    systemctl start "$telemetry" 2>/dev/null   # only now: it enumerates the gpu, or its instances, once at start
    if systemctl cat "$assistant" >/dev/null 2>&1; then
        # --no-block: the model loads for minutes, and the unit reports itself through its health check
        systemctl start --no-block "$assistant"
        echo "the coding assistant is starting on slice 0:0 - minutes, not seconds: journalctl -fu ${assistant%.service}"
    fi
    status
    ;;
status) status ;;
*) echo "usage: fury-mode flywheel|tenants|status" >&2; exit 1 ;;
esac
