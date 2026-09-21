#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# One command for the presenter to move the machine between its two modes, from a laptop:
#
#   fury-switch.sh tenants     take the policy out of service through RHEM, MIG on, then robot zero: RHEM places
#                              the policy on slice 0:1 and starts it there (act 2)
#   fury-switch.sh flywheel    stop the policy and take its placement away, MIG off, sim back up, then put the
#                              policy back in service on the whole GPU (act 1)
#   fury-switch.sh status      what RHEM and the host say right now
#
# Why two systems are involved: the policy container belongs to RHEM (flightctl app stop|start is a
# per-device override that survives rollouts, and the device's gpu_device label is where it runs), the GPU
# mode, the sim and the other tenants belong to the host (fury-mode). MIG only changes with no client on the
# GPU, so in both directions the policy is stopped first and started last, and its placement is changed only
# while it is stopped: tools/hub/robot-zero.sh holds that half (D164). Robot zero alone, without a mode
# switch, is that script too.
# The host step asks for the sudo password once, on this terminal.
#
#   FURY_SSH       ssh target of the host, user@host                       (required)
#   FLIGHTCTL      flightctl binary, logged in to this hub                  (default: flightctl, then ~/.local/bin/flightctl)
#   DEVICE_ALIAS   the alias label the host was approved with               (default: fury-host)
#   APP            the Fleet application                                    (default: act-inference)
set -euo pipefail

die() { echo "${0##*/}: $*" >&2; exit 1; }
: "${FURY_SSH:?set FURY_SSH=user@host (the ssh target of the machine)}"
fc=${FLIGHTCTL:-$(command -v flightctl || echo "$HOME/.local/bin/flightctl")}
alias_label=${DEVICE_ALIAS:-fury-host}
app=${APP:-act-inference}
[[ -x $fc ]] || die "no flightctl at $fc"
command -v jq >/dev/null || die "jq is missing"

devices=$("$fc" get devices -l "alias=$alias_label" -o json 2>/dev/null) ||
    die "flightctl is not logged in to the hub. Log in (oc login as a real user, then: flightctl login <api url> --token \"\$(oc whoami -t)\") and run this again"
dev=$(jq -r '[.items[].metadata.name] | join(" ")' <<<"$devices")
[[ -n $dev && $dev != *' '* ]] || die "expected exactly one device with alias=$alias_label, found: '${dev:-none}'"

app_status() {
    "$fc" get "device/$dev" -o json |
        jq -r --arg a "$app" '[(.status.applications[]? | select(.name == $a) | .status), .status.applicationsSummary.status] | join(" / ")'
}
wait_for() {  # pattern seconds
    local s i
    for ((i = 0; i < $2; i += 5)); do
        s=$(app_status)
        if [[ $s =~ $1 ]]; then echo "  $app: $s"; return 0; fi
        sleep 5
    done
    echo "  $app: $s (still, after $2 s)"; return 1
}
on_host() { ssh -t "$FURY_SSH" /usr/local/sbin/fury-mode "$1"; }
zero="$(dirname "$0")/robot-zero.sh"
[[ -f $zero ]] || die "no robot-zero.sh next to this script"

case ${1:-} in
tenants)
    echo "1/3  RHEM: stop $app on $alias_label"
    "$fc" app stop "device/$dev" --name "$app" --yes
    wait_for '^(Stopped|Completed|Unknown)' 90 || echo "  carrying on: the host refuses to switch while the policy is up, so nothing can go wrong here"
    echo "2/3  host: MIG on, the tenants up"
    on_host tenants
    echo "3/3  robot zero: the policy onto slice 0:1"
    # the machine is in tenants mode whatever happens here; the policy stays stopped unless its placement is rendered
    bash "$zero" up || die "the machine IS in tenants mode and every other tenant is up; robot zero's policy alone is not. When the cause above is fixed:  FURY_SSH=$FURY_SSH $zero up"
    ;;
flywheel)
    echo "1/3  RHEM: stop $app on $alias_label and take its slice away"
    bash "$zero" down || die "nothing was switched: the host is still in the mode it was in"
    echo "2/3  host: MIG off, sim up"
    on_host flywheel
    echo "3/3  RHEM: start $app on $alias_label"
    "$fc" app start "device/$dev" --name "$app" --yes
    wait_for '^Running / Healthy' 360 || die "$app did not report Running / Healthy - look at the device in the RHEM UI"
    ;;
status)
    echo "RHEM   $alias_label  $app: $(app_status)"
    on_host status
    ;;
*)  echo "usage: FURY_SSH=user@host ${0##*/} tenants | flywheel | status" >&2; exit 1 ;;
esac
