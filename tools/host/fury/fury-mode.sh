#!/bin/bash
# One GPU, two ways to use it, never both at once:
#
#   fury-mode flywheel   MIG off. The sim renders its cameras on the GPU (OpenGL only exists without MIG)
#                        and shares it with policy serving and training, the way the desktop did.
#   fury-mode tenants    MIG on, 3g + 1g + 1g + 1g. Isolated slices for compute tenants; no graphics.
#                        Starts the coding assistant on the large slice, the training tenant on 0:2, the
#                        fleet's renderer on 0:3 and robot zero's host side (its physics-only world, its
#                        frames, its episode loop), each if its unit is installed.
#   fury-mode zero       tenants mode only: restart robot zero's host side and nothing else
#   fury-mode status
#
# The GPU has to be idle to change MIG mode, so a switch stops the robot loop first. The choice is
# written to /etc/sysconfig/mig-config, so a reboot comes back in the same mode.
#
# Once the host is enrolled the policy is not this script's to stop: it is the Fleet's quadlet, run by
# flightctl-agent. It is taken out of service from the hub (flightctl app stop - an override that
# survives rollouts), and this script only refuses to go on while it is up. One case is not refused:
# tenants to tenants with the policy on a slice (robot zero, D164). MIG does not change then, so the
# policy may stay where RHEM put it while the host's own tenants are bounced around it.
# Which device the policy gets is RHEM's too (the device's gpu_device label, set and cleared from the
# laptop by tools/hub/robot-zero.sh and fury-switch.sh - no hub credential lives here). This script
# reads what the agent rendered and says when it does not fit the mode.
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"

cfg=/etc/sysconfig/mig-config
loop=(flywheel-runner.service act-coordinator.service act-inference.service so-arm-sim.service)   # act-inference.service: before enrolment only
assistant=llm-assistant.service          # act 2's tenant on slice 0:0 - a host unit of ours, stopped for every switch
tenant=training-tenant.service           # act 2's tenant on slice 0:2 - the same (72-training-tenant-install.sh)
renderer=fleet-renderer.service          # act 2's tenant on slice 0:3 - the same (73-fleet-renderer-install.sh)
# robot zero's host side (74-robot-zero-install.sh): no gpu in any of them. The first is the handle - it wants the
# other two and they are bound to it. Robot zero's gpu tenant, on slice 0:1, is the policy below.
zero=(robot-zero-sim.service robot-zero-frames.service robot-zero-episodes.service)
policy='act-inference-*-act-inference.service'                             # after: <app id>-<quadlet>, named by the agent
telemetry=dcgm-exporter.service          # runs in both modes (70-dcgm.sh). Not installed is fine: every call below is quiet
die() { echo "fury-mode: $*" >&2; exit 1; }
# This runs under ssh -t from a laptop, where Ctrl-C arrives as a signal: leave the way the EXIT trap would, and say
# so - a switch cut short can leave telemetry stopped, or robot zero's world down beside a policy that is up.
interrupted() {
    trap - EXIT INT TERM
    systemctl start --no-block "$telemetry" 2>/dev/null
    echo "fury-mode: interrupted - check: fury-mode status" >&2
    exit 130
}
trap interrupted INT TERM
mig() { nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader; }
policy_state() { systemctl list-units --all --no-legend --plain "$policy" | awk '{print $3 "/" $4}'; }
# The CDI device the agent's quadlet hands the policy: all, or MIG-<uuid> once the device carries gpu_device.
# Exactly one quadlet is expected. Two application directories (the agent mid-rollout) or two AddDevice= lines
# give "ambiguous", which fits no mode below: the placement is reported as wrong and a policy is never kept.
policy_device() {
    local f found
    found=$(for f in /etc/containers/systemd/*/${policy%.service}.container; do
                [[ -f $f ]] && sed -n 's|^AddDevice=nvidia\.com/gpu=||p' "$f"
            done)
    case $(grep -c . <<<"$found") in
        0) ;;
        1) echo "$found" ;;
        *) echo ambiguous ;;
    esac
}
# Does that device fit the mode the gpu is in? Said in status, and after a switch.
placement() {
    local d s; d=$(policy_device)
    case "$(mig)/$d" in
        */'')            echo "no policy quadlet on this host" ;;
        */ambiguous)     echo "more than one policy quadlet or AddDevice= line under /etc/containers/systemd (the agent in the middle of an update?) - look again in a minute: fury-mode status" ;;
        Enabled/MIG-*)   s=$(nvidia-smi -L | grep -F "UUID: $d)" | sed -E 's/^ *MIG +([^ ]+) +Device +([0-9]+):.*/slice 0:\2 (\1)/')
                         case $s in
                             'slice 0:1 '*) echo "$d = $s" ;;
                             '')            echo "$d - NOT a slice of this gpu. From the laptop:  tools/hub/robot-zero.sh up" ;;
                             *)             echo "$d - that is $s, NOT robot zero's 0:1. From the laptop:  tools/hub/robot-zero.sh up" ;;
                         esac ;;
        Enabled/*)       echo "$d - no slice named yet: started like this it would take the assistant's. Keep it stopped until, from the laptop:  tools/hub/robot-zero.sh up" ;;
        Disabled/MIG-*)  echo "$d - WRONG while MIG is off (no such device: it cannot start). From the laptop:  tools/hub/robot-zero.sh down" ;;
        *)               echo "$d" ;;
    esac
}
# A lerobot-train or an assembler that is NOT the training tenant's: the governed runner's, or somebody's by hand.
# Told apart by the unit that owns the process - quadlet keeps a container's processes in its unit's cgroup,
# .../training-tenant.service/libpod-payload-<id> - not by the command line, which the tenant shares. A process
# that cannot be placed counts as governed: the switch is refused, never a run lost.
governed_run() {
    local p cg
    for p in $(pgrep -f 'lerobot-train|assemble_dataset'); do
        cg=$(cat "/proc/$p/cgroup" 2>/dev/null) || continue        # gone since pgrep looked
        [[ $cg == *"/$tenant/"* ]] || { echo "pid $p in ${cg##*:}"; return 0; }
    done
    return 1
}

drain() {  # the mode being switched to
    local keep='' p
    # first, so that a refusal leaves the sim and the recorder running. A loaded but idle policy holds
    # the device nodes without showing up as a compute process.
    case $(policy_state) in
        ''|inactive/*|failed/*) ;;
        *)  # Robot zero: tenants to tenants, the policy on its slice of this gpu. MIG stays as it is, so the policy stays.
            # Not with the flywheel's sim up or restarting under MIG (it cannot be, by its own start condition): that
            # has to be stopped, and stopping it takes the policy along (below) - so then the policy is not kept.
            if [[ $1 == tenants && $(mig) == Enabled && $(placement) == MIG-*' = slice 0:1 '* &&
                  $(systemctl is-active so-arm-sim.service 2>/dev/null) =~ ^(inactive|failed)$ ]]; then
                keep=yes
            else
                die "the RHEM-managed policy is up and holds the gpu. From the laptop:
    tools/hub/robot-zero.sh down        (or: flightctl app stop device/<name> --name act-inference --yes)
then run this again - tools/hub/fury-switch.sh does both in the right order.
Hub unreachable: sudo systemctl stop '${policy%-act-inference.service}-flightctl-quadlet-app.target'"
            fi ;;
    esac
    # hours of work would go with the stop below: make the operator end a run on purpose. The training tenant's
    # rounds are not that - minutes each, a showcase that starts over - and the stop below takes them.
    local run
    if run=$(governed_run) || podman pod exists eval-rig 2>/dev/null; then
        die "a training run or an eval is in progress${run:+ ($run)} (journalctl -u flywheel-runner -n 5). Wait for it, or end it yourself:
    sudo systemctl stop flywheel-runner.service flywheel-eval.service"
    fi
    if [[ -n $keep ]]; then
        # Two stops are left out, and only those two. The policy is PartOf= so-arm-sim.service, which is PartOf= the
        # flywheel's target, and systemd hands a stop job down that chain when it builds the transaction, whether or
        # not the unit being stopped is running (transaction.c: UNIT_ATOM_PROPAGATE_STOP is walked for every new stop
        # job). Stopping either would take the kept policy down. Neither can be up here: the target went down with
        # the switch that turned MIG on, and the sim was just seen inactive. Every other unit of the loop - the
        # recorder above all, which must never meet robot zero's router - is stopped as always; none of them has a
        # part that is the policy.
        for p in "${loop[@]}"; do [[ $p == so-arm-sim.service ]] || systemctl stop "$p" 2>/dev/null; done
        systemctl stop "${zero[@]}" "$assistant" "$tenant" "$renderer" 2>/dev/null
    else
        systemctl disable --now fury-flywheel.target 2>/dev/null
        systemctl stop "${zero[@]}" "${loop[@]}" "$assistant" "$tenant" "$renderer" 2>/dev/null
    fi
    # DCGM holds the driver open without being a compute process, and MIG mode does not change under a client.
    # From here on it comes back on every way out, a refusal included.
    systemctl stop "$telemetry" 2>/dev/null
    trap 'systemctl start --no-block "$telemetry" 2>/dev/null' EXIT
    local busy; busy=$(nvidia-smi --query-compute-apps=pid,name --format=csv,noheader)
    if [[ -n $keep ]]; then
        # the policy that stays is the one client allowed: told apart by the unit that owns the process, as above
        busy=$(while IFS=, read -r p rest; do
                   p=${p//[!0-9]/}
                   [[ -n $p ]] || continue
                   grep -qs -- '/act-inference-[^/]*-act-inference\.service/' "/proc/$p/cgroup" || echo "$p,$rest"
               done <<<"$busy")
        echo "the RHEM-managed policy stays up on its slice: MIG does not change"
    fi
    [[ -z $busy ]] || die "the gpu is still in use, stop this first: $busy"
}

status() {
    echo "mig mode:  $(mig)    configured layout: $(sed -n 's/^MIG_LAYOUT=//p' "$cfg" 2>/dev/null)"
    nvidia-smi -L | sed 's/ (UUID.*//'
    for u in fury-flywheel.target "${loop[@]}" "$assistant" "$tenant" "$renderer" "${zero[@]}" "$telemetry" disk-guard.service flightctl-agent.service; do
        printf '%-28s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null)"
    done
    printf '%-28s %s\n' "policy (rhem-managed)" "$(policy_state)"
    printf '%-28s %s\n' "policy placement (rhem's)" "$(placement)"
    echo "bags: $(find /data/flywheel/bags -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)   /data free: $(df -h --output=avail /data | tail -1 | tr -d ' ')"
}

case ${1:-status} in
flywheel)
    drain flywheel
    echo 'MIG_LAYOUT=none' > "$cfg"
    systemctl restart mig-config.service       || die "mig-config failed: journalctl -u mig-config"
    systemctl restart nvidia-cdi-refresh.service
    [[ $(mig) == Disabled ]]                   || die "MIG is still on"
    systemctl start "$telemetry" 2>/dev/null   # only now: it enumerates the gpu, or its instances, once at start
    systemctl enable --now fury-flywheel.target
    status
    if [[ -s /etc/flightctl/config.yaml ]]; then
        if [[ $(policy_device) == MIG-* ]]; then
            echo "next, from the laptop: tools/hub/robot-zero.sh down   - the policy's unit still names a slice, and with MIG off that device does not exist"
            echo "then:                  flightctl app start device/<name> --name act-inference --yes     (tools/hub/fury-switch.sh flywheel does both)"
        else
            echo "next, from the laptop: flightctl app start device/<name> --name act-inference --yes"
        fi
    fi
    ;;
tenants)
    drain tenants
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
    if systemctl cat "$tenant" >/dev/null 2>&1; then
        # it ends non-zero when a round fails, so an earlier bad hour can have used up its start limit: a switch
        # always gets a fresh try
        systemctl reset-failed "$tenant" 2>/dev/null
        systemctl start --no-block "$tenant"
        echo "the training tenant is starting on slice 0:2 - loss lines within a few minutes: journalctl -fu ${tenant%.service}"
    fi
    if systemctl cat "$renderer" >/dev/null 2>&1; then
        # a switch always gets a fresh try, as for the tenant
        systemctl reset-failed "$renderer" 2>/dev/null
        systemctl start --no-block "$renderer"
        echo "the fleet renderer is starting on slice 0:3 - its first start on a slice compiles kernels for minutes, later ones load them: journalctl -fu ${renderer%.service}"
    fi
    if systemctl cat "${zero[0]}" >/dev/null 2>&1; then
        # a switch always gets a fresh try, as for the tenant
        systemctl reset-failed "${zero[@]}" 2>/dev/null
        systemctl start --no-block "${zero[@]}"
        echo "robot zero's world, frames and episode loop are starting - r00 is on the wall within a minute: journalctl -fu ${zero[0]%.service}"
        echo "its policy is RHEM's to place on slice 0:1 and start. From the laptop: tools/hub/robot-zero.sh up   (tools/hub/fury-switch.sh tenants ends with it)"
    fi
    status
    ;;
zero)
    # Robot zero's host side alone, for a reset at demo time: no other tenant is touched and MIG is not. The world's
    # unit takes the frames and the episode loop along, and restarts the policy itself if that is running.
    systemctl cat "${zero[0]}" >/dev/null 2>&1 || die "robot zero's units are not installed: cd ~/flywheel-setup && ./74-robot-zero-install.sh install"
    [[ $(mig) == Enabled ]]                    || die "robot zero is a tenants-mode tenant and MIG is off. From the laptop: tools/hub/fury-switch.sh tenants   (here: fury-mode tenants)"
    systemctl reset-failed "${zero[@]}" 2>/dev/null
    systemctl restart "${zero[0]}"             || die "${zero[0]} did not start: journalctl -u ${zero[0]%.service} -n 30"
    systemctl start "${zero[@]}"
    for u in "${zero[@]}"; do printf '%-28s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null)"; done
    printf '%-28s %s\n' "policy (rhem-managed)" "$(policy_state)"
    printf '%-28s %s\n' "policy placement (rhem's)" "$(placement)"
    ;;
status) status ;;
*) echo "usage: fury-mode flywheel|tenants|zero|status" >&2; exit 1 ;;
esac
