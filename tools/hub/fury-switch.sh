#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# One command for the presenter to move the machine between its two modes, from a laptop:
#
#   fury-switch.sh tenants     take the policy out of service through RHEM, MIG on, then the coding assistant up
#                              on its slice (act 2). The assistant loads for minutes; progress is shown.
#   fury-switch.sh flywheel    take the assistant out of service, MIG off, sim back up, then the policy back in
#                              service (act 1)
#   fury-switch.sh status      what RHEM and the host say right now
#
# Why two systems are involved: both containers belong to RHEM (flightctl app stop|start is a per-device
# override that survives rollouts), the GPU mode and the sim belong to the host (fury-mode). MIG only changes
# while nothing holds the GPU, so both applications are stopped before the host step and one is started after it.
# The host step asks for the sudo password once, on this terminal.
#
# The assistant is only handled when the device carries the llm_gpu_device label (tools/host/fury/41-device-enroll.md,
# section 8). Without it the Fleet renders a placeholder for that application, and this script leaves it alone.
# The assistant is stopped before it is started even in the direction where it should be down already: the agent
# acts on a CHANGE of the desired state, so a start on top of "running" (never stopped through RHEM, or skipped
# by the unit's ExecCondition= while MIG was off) would do nothing.
#
#   FURY_SSH        ssh target of the host, user@host                       (required)
#   FLIGHTCTL       flightctl binary, logged in to this hub                  (default: flightctl, then ~/.local/bin/flightctl)
#   DEVICE_ALIAS    the alias label the host was approved with               (default: fury-host)
#   APP             the Fleet application that serves the policy             (default: act-inference)
#   ASSISTANT_APP   the Fleet application that serves the coding assistant   (default: llm-assistant)
#   ASSISTANT_URL   where this laptop reaches the assistant; only used to show /health while waiting
#                                                                            (default: http://<host of FURY_SSH>:8000)
#   ASSISTANT_WAIT  seconds to wait for the assistant to report healthy      (default: 1500; its health check allows 20 min)
set -euo pipefail

die() { echo "${0##*/}: $*" >&2; exit 1; }
: "${FURY_SSH:?set FURY_SSH=user@host (the ssh target of the machine)}"
fc=${FLIGHTCTL:-$(command -v flightctl || echo "$HOME/.local/bin/flightctl")}
alias_label=${DEVICE_ALIAS:-fury-host}
app=${APP:-act-inference}
assistant=${ASSISTANT_APP:-llm-assistant}
assistant_url=${ASSISTANT_URL:-http://${FURY_SSH#*@}:8000}
assistant_wait=${ASSISTANT_WAIT:-1500}
[[ -x $fc ]] || die "no flightctl at $fc"
command -v jq >/dev/null || die "jq is missing"

devices=$("$fc" get devices -l "alias=$alias_label" -o json 2>/dev/null) ||
    die "flightctl is not logged in to the hub. Log in (oc login as a real user, then: flightctl login <api url> --token \"\$(oc whoami -t)\") and run this again"
dev=$(jq -r '[.items[].metadata.name] | join(" ")' <<<"$devices")
[[ -n $dev && $dev != *' '* ]] || die "expected exactly one device with alias=$alias_label, found: '${dev:-none}'"
slice=$(jq -r '.items[0].metadata.labels.llm_gpu_device // empty' <<<"$devices")

app_status() {  # app
    "$fc" get "device/$dev" -o json |
        jq -r --arg a "$1" '[(.status.applications[]? | select(.name == $a) | .status), .status.applicationsSummary.status] | join(" / ")'
}
# the application is in what the Fleet rendered for this device (false while the Fleet change has not synced)
in_spec() { "$fc" get "device/$dev" -o json | jq -e --arg a "$1" 'any((.spec.applications[]?, .status.applications[]?); .name == $a)' >/dev/null; }
health() { command -v curl >/dev/null && printf '   %s/health: HTTP %s' "$assistant_url" "$(curl -s -o /dev/null -m 3 -w '%{http_code}' "$assistant_url/health" || true)"; }
wait_for() {  # app pattern seconds [seconds between progress lines]
    local s t0=$SECONDS next=$((SECONDS + ${4:-0}))
    while :; do
        s=$(app_status "$1") || s='no answer from the hub'
        if [[ $s =~ $2 ]]; then echo "  $1: $s   (after $((SECONDS - t0)) s)"; return 0; fi
        ((SECONDS - t0 < $3)) || break
        if [[ -n ${4:-} ]] && ((SECONDS >= next)); then
            printf '  .. %4d s   %s: %s%s\n' "$((SECONDS - t0))" "$1" "$s" "$(health || true)"
            next=$((SECONDS + $4))
        fi
        sleep 5
    done
    echo "  $1: $s (still, after $3 s)"; return 1
}
on_host() { ssh -t "$FURY_SSH" /usr/local/sbin/fury-mode "$1"; }

# true when this device serves the assistant; says why not otherwise
serves_assistant() {
    if [[ -z $slice ]]; then
        echo "  $assistant: not handled - $alias_label has no llm_gpu_device label (tools/host/fury/41-device-enroll.md, section 8)"
        return 1
    fi
    if ! in_spec "$assistant"; then
        echo "  $assistant: not handled - the Fleet on this hub does not deliver it to $alias_label (yet)"
        return 1
    fi
}
stop_assistant() {
    "$fc" app stop "device/$dev" --name "$assistant" --yes
    # strictly Stopped: that status only appears once the agent has seen the stop, which is what makes the next start a change
    wait_for "$assistant" '^Stopped' 180 ||
        echo "  carrying on: the host refuses to switch while anything holds the gpu, so nothing can go wrong here"
}

case ${1:-} in
tenants)
    echo "1/3  RHEM: stop $app on $alias_label"
    "$fc" app stop "device/$dev" --name "$app" --yes
    wait_for "$app" '^(Stopped|Completed|Unknown)' 90 || echo "  carrying on: the host refuses to switch while the policy is up, so nothing can go wrong here"
    serving=no
    if serves_assistant; then serving=yes; stop_assistant; fi
    echo "2/3  host: MIG on"
    on_host tenants
    if [[ $serving == yes ]]; then
        echo "3/3  RHEM: start $assistant on $alias_label, slice $slice - weights, compile and warm-up took 5 min 46 s cold when measured"
        "$fc" app start "device/$dev" --name "$assistant" --yes
        wait_for "$assistant" '^Running / Healthy' "$assistant_wait" 30 ||
            die "$assistant did not report Running / Healthy. On the host: sudo journalctl -u 'llm-assistant-*-llm-assistant.service' -n 60. A unit that was skipped or has hit its start limit is started again with: flightctl app restart device/$dev --name $assistant --yes"
        echo "  try it: curl -s $assistant_url/v1/models"
    else
        echo "3/3  RHEM: nothing to start"
    fi
    ;;
flywheel)
    echo "1/3  RHEM: stop $assistant on $alias_label"
    if serves_assistant; then stop_assistant; fi
    echo "2/3  host: MIG off, sim up"
    on_host flywheel
    echo "3/3  RHEM: start $app on $alias_label"
    "$fc" app start "device/$dev" --name "$app" --yes
    wait_for "$app" '^Running / Healthy' 360 || die "$app did not report Running / Healthy - look at the device in the RHEM UI"
    ;;
status)
    echo "RHEM   $alias_label  $app: $(app_status "$app")"
    if [[ -n $slice ]]; then
        echo "RHEM   $alias_label  $assistant: $(app_status "$assistant")   slice $slice$(health || true)"
    else
        echo "RHEM   $alias_label  $assistant: $(app_status "$assistant")   (no llm_gpu_device label: the placeholder)"
    fi
    on_host status
    ;;
*)  echo "usage: FURY_SSH=user@host ${0##*/} tenants | flywheel | status" >&2; exit 1 ;;
esac
