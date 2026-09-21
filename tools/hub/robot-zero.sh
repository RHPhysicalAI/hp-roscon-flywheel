#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Robot zero from a laptop (D164): the GPU host's own policy - the Fleet's signed act-inference application - placed
# on MIG slice 0:1 by RHEM and serving there in tenants mode. Placement is the device's gpu_device label: the Fleet's
# quadlet renders AddDevice=nvidia.com/gpu=<gpu_device>, and "all" when the label is absent (flywheel mode).
#
#   robot-zero.sh up       tenants mode: name slice 0:1 in the label, wait until RHEM has rendered it, start the policy
#   robot-zero.sh down     stop the policy and take the label away again - what a switch to flywheel mode needs first
#   robot-zero.sh reset    restart robot zero's world, frames, episode loop and episode reporter on the host (asks for
#                          the sudo password once, on this terminal); the policy follows its world. No other tenant
#                          is touched
#   robot-zero.sh status   RHEM's view, the host's units, the renderer's view of r00. No sudo, nothing changes
#
# The order is the point. A label change re-renders the quadlet and the agent restarts a running policy at once, so
# the policy is stopped BEFORE the label changes and started only AFTER the device's spec shows the device it is
# meant to get: it never runs on "all" while MIG is on (it would take the assistant's slice), and never names a slice
# while MIG is off (no such device). tools/hub/fury-switch.sh calls up and down around the host's mode switch.
# The host holds no hub credential (D150), so the label is set from here; the host side - fury-mode - only reads
# what the agent rendered and says when it does not fit the mode.
#
#   FURY_SSH       ssh target of the host, user@host                       (required)
#   FLIGHTCTL      flightctl binary, logged in to this hub                  (default: flightctl, then ~/.local/bin/flightctl)
#   DEVICE_ALIAS   the alias label the host was approved with               (default: fury-host)
#   APP            the Fleet application                                    (default: act-inference)
set -euo pipefail

die() { echo "${0##*/}: $*" >&2; exit 1; }
case ${1:-} in
up|down|reset|status) ;;
*) echo "usage: FURY_SSH=user@host ${0##*/} up | down | reset | status" >&2; exit 1 ;;
esac
verb=$1
: "${FURY_SSH:?set FURY_SSH=user@host (the ssh target of the machine)}"
fc=${FLIGHTCTL:-$(command -v flightctl || echo "$HOME/.local/bin/flightctl")}
alias_label=${DEVICE_ALIAS:-fury-host}
app=${APP:-act-inference}
units=(robot-zero-sim.service robot-zero-frames.service robot-zero-episodes.service robot-zero-emitter.service)
[[ -x $fc ]] || die "no flightctl at $fc"
command -v jq >/dev/null || die "jq is missing"
tmp=''
trap '[[ -z $tmp ]] || { rm -f "$tmp/device.json"; rmdir "$tmp"; }' EXIT

devices=$("$fc" get devices -l "alias=$alias_label" -o json 2>/dev/null) ||
    die "flightctl is not logged in to the hub. Log in (oc login as a real user, then: flightctl login <api url> --token \"\$(oc whoami -t)\") and run this again"
dev=$(jq -r '[.items[].metadata.name] | join(" ")' <<<"$devices")
[[ -n $dev && $dev != *' '* ]] || die "expected exactly one device with alias=$alias_label, found: '${dev:-none}'"

device() { "$fc" get "device/$dev" -o json; }
app_status() {  # of a device document on stdin
    jq -r --arg a "$app" '[(.status.applications[]? | select(.name == $a) | .status), .status.applicationsSummary.status] | join(" / ")'
}
label() { jq -r '.metadata.labels.gpu_device // ""'; }
# The device the policy's quadlet asks for in the spec RHEM rendered for this device from the Fleet's template.
wanted_device() {
    jq -r --arg a "$app" '.spec.applications[]? | select(.name == $a) | .inline[]? | select(.path | endswith(".container")) | .content' |
        sed -n 's|^AddDevice=nvidia\.com/gpu=||p' | head -1
}
# True once the agent reports the version the hub last rendered: what is on the host is what the spec says.
applied() {
    jq -e '.status.updated.status == "UpToDate" and
           (.status.config.renderedVersion // "a") == (.metadata.annotations["device-controller/renderedVersion"] // "b")' >/dev/null
}
wait_app() {  # pattern seconds
    local s='' i
    for ((i = 0; i < $2; i += 5)); do
        s=$(device | app_status) || s='(the hub did not answer)'     # a read that fails is a reason to look again, not to stop half way
        if [[ $s =~ $1 ]]; then echo "  $app: $s"; return 0; fi
        sleep 5
    done
    echo "  $app: $s (still, after $2 s)"; return 1
}
wait_rendered() {  # the device the quadlet must name, seconds
    local doc got='' i
    for ((i = 0; i < $2; i += 5)); do
        doc=$(device) || doc='{}'
        got=$(wanted_device <<<"$doc") || got=''
        if [[ $got == "$1" ]] && applied <<<"$doc"; then echo "  rendered and on the host: AddDevice=nvidia.com/gpu=$got"; return 0; fi
        sleep 5
    done
    echo "  the device's spec says AddDevice=nvidia.com/gpu=${got:-nothing} and wanted is $1 (still, after $2 s)"; return 1
}
# flightctl 1.3 has no way to change one label: a label change is a write of the whole device document (the way
# tools/host/fury/41-device-enroll.md, section 6, does it by hand). So what is sent is as little as can be, and
# what comes back is checked.
#   Sent: the device as the hub returns it, with the one label changed, WITHOUT everything the hub manages - the
#     status, the annotations (device-controller/renderedVersion is what the "applied" gate above reads: it must
#     only ever be the hub's own), generation, owner, timestamps. The hub leaves a field it is not sent as it is.
#   Kept: metadata.resourceVersion. It is the hub's optimistic lock: with it a device that changed between this
#     read and this write is a refused write (run again), without it the last writer silently wins - and the last
#     writer here carries a whole label map. The spec goes back exactly as it came; the hub refuses a changed one
#     on a Fleet's device.
#   Checked afterwards: the label is what was asked for, and the labels that make this device what it is - the
#     alias this script finds it by, its fleet, its pull_default - are what they were.
# shellcheck disable=SC2016  # $v is jq's, bound with --arg below
label_change='del(.status, .metadata.annotations, .metadata.generation, .metadata.owner, .metadata.creationTimestamp, .metadata.deletionTimestamp)
              | if $v == "" then del(.metadata.labels.gpu_device) else .metadata.labels.gpu_device = $v end'
set_label() {  # value, or nothing to remove the label
    local before sent after l was now
    before=$(device) && [[ -n $before ]] || die "could not read device/$dev from the hub - nothing was changed, the policy stays stopped. Run this again"
    sent=$(jq --arg v "${1:-}" "$label_change" <<<"$before") && [[ -n $sent ]] ||
        die "what the hub returned for device/$dev is not a device document - nothing was changed, the policy stays stopped. Look: $fc get device/$dev -o yaml"
    tmp=$(mktemp -d)
    printf '%s\n' "$sent" > "$tmp/device.json"
    "$fc" apply -f "$tmp/device.json" >/dev/null ||
        die "the hub refused the label change - nothing was changed by it, and the policy stays stopped.
    A conflict means the device changed between the read and the write:  FURY_SSH=$FURY_SSH $0 $verb  again.
    By hand:  $fc edit device/$dev   - under metadata.labels, gpu_device: ${1:-(the line removed)} - then this again"
    rm -f "$tmp/device.json"; rmdir "$tmp"; tmp=''
    after=$(device) && [[ -n $after ]] ||
        die "the label change was sent, but device/$dev could not be read back to check it. The policy stays stopped. Look: $fc get device/$dev -o yaml  - then this again"
    for l in alias fleet pull_default; do
        was=$(jq -r --arg l "$l" '.metadata.labels[$l] // ""' <<<"$before")
        now=$(jq -r --arg l "$l" '.metadata.labels[$l] // ""' <<<"$after")
        [[ $was == "$now" ]] ||
            die "label $l of device/$dev was '${was:-(absent)}' and is '${now:-(absent)}' after the label change. The policy stays stopped.
    Put it back before anything else - without 'fleet' the device leaves its Fleet, without 'pull_default' the host's
    pulls are refused, without 'alias' this script no longer finds the device:
    $fc edit device/$dev   - under metadata.labels,  $l: ${was:-(remove the line)}"
    done
    now=$(label <<<"$after")
    [[ $now == "${1:-}" ]] ||
        die "the hub took the write, but gpu_device is '${now:-(absent)}', not '${1:-(absent)}'. The policy stays stopped. Look: $fc get device/$dev -o yaml  - then this again"
}
stop_app() {
    local s; s=$(device | app_status) || die "could not read device/$dev from the hub - nothing was changed. Run this again"
    if [[ $s =~ ^(Stopped|Completed) ]]; then echo "  $app: $s already"; return 0; fi
    "$fc" app stop "device/$dev" --name "$app" --yes
    wait_app '^(Stopped|Completed|Unknown)' 90 ||
        die "$app did not report Stopped. Nothing else was changed. Look at the device in the RHEM UI, then run this again"
}
on_host() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$FURY_SSH" "$@"; }   # no sudo, no terminal: reading only
# The UUID of MIG device 1 of GPU 0 - slice 0:1, robot zero's (D164). Nothing when MIG is off.
slice_uuid() { on_host nvidia-smi -L | sed -n 's/^ *MIG .* Device  *1: (UUID: \(MIG-[0-9a-f-]*\)).*/\1/p' | head -1; }

case $1 in
up)
    echo "1/4  host: the slice and robot zero's world"
    uuid=$(slice_uuid) || die "could not reach $FURY_SSH over ssh"
    [[ $uuid =~ ^MIG-[0-9a-f-]{36}$ ]] ||
        die "the host has no MIG slice 0:1 - it is not in tenants mode. First: FURY_SSH=$FURY_SSH ${0%/*}/fury-switch.sh tenants   (which ends by running this)"
    echo "  slice 0:1 is $uuid"
    # the policy's health check needs the Zenoh router in robot zero's world: without it the policy is killed and
    # restarted every few minutes, and RHEM shows that
    # a mode switch has only just started it: give it a minute
    for ((i = 0; i < 60; i += 5)); do
        world=$(on_host systemctl is-active "${units[0]}" || true)
        [[ $world == activating ]] || break
        sleep 5
    done
    [[ $world == active ]] ||
        die "${units[0]} is '${world:-unknown}' on the host, and the policy cannot be healthy without its router.
    Installed already:  FURY_SSH=$FURY_SSH $0 reset
    Not installed yet:  on the host, once:  cd ~/flywheel-setup && ./74-robot-zero-install.sh install"
    doc=$(device) || die "could not read device/$dev from the hub - nothing was changed. Run this again"
    if [[ $(label <<<"$doc") == "$uuid" && $(wanted_device <<<"$doc") == "$uuid" ]]; then
        echo "2/4  RHEM: gpu_device=$uuid already"
    else
        echo "2/4  RHEM: stop $app, then gpu_device=$uuid on $alias_label"
        stop_app
        set_label "$uuid"
    fi
    echo "3/4  RHEM: wait for the rendered quadlet"
    wait_rendered "$uuid" 180 ||
        die "RHEM has not rendered the slice into the device's spec, so the policy was NOT started (on 'all' it would take the assistant's slice).
    Look: $fc get device/$dev -o json | jq '.metadata.labels, .status.updated'    - then run this again"
    echo "4/4  RHEM: start $app on slice 0:1"
    "$fc" app start "device/$dev" --name "$app" --yes
    wait_app '^Running / Healthy' 420 ||
        die "$app did not report Running / Healthy (its health check allows itself four minutes). On the host: fury-mode status, sudo journalctl -u 'act-inference-*-act-inference' -n 40"
    echo "robot zero is up: r00 on the wall, the policy on slice 0:1.  $0 status"
    ;;
down)
    echo "1/2  RHEM: stop $app on $alias_label"
    stop_app
    doc=$(device) || die "could not read device/$dev from the hub. The policy is stopped; its label was not touched. Run this again"
    if [[ -z $(label <<<"$doc") ]]; then
        echo "2/2  RHEM: no gpu_device label to remove"
    else
        echo "2/2  RHEM: remove gpu_device (the quadlet goes back to the whole gpu, for flywheel mode)"
        set_label
    fi
    wait_rendered all 180 ||
        die "RHEM has not rendered the whole-gpu quadlet yet. The policy is stopped, nothing is at risk; do not start it in flywheel mode before this says so. Run this again"
    echo "the policy is out of service and unplaced. Robot zero's world keeps running on the host until a mode switch; r00 stays on the wall with its arm at rest."
    ;;
reset)
    echo "host: restart robot zero's world, frames, episode loop and episode reporter (the policy follows its world)"
    ssh -t "$FURY_SSH" /usr/local/sbin/fury-mode zero
    ;;
status)
    doc=$(device) || die "could not read device/$dev from the hub"
    echo "RHEM   $alias_label  $app: $(app_status <<<"$doc")"
    echo "RHEM   gpu_device label: $(label <<<"$doc" | sed 's/^$/(none - the whole gpu)/')   quadlet asks for: nvidia.com/gpu=$(wanted_device <<<"$doc")   $(applied <<<"$doc" && echo 'on the host' || echo 'NOT yet applied by the agent')"
    # one ssh, no sudo: unit states, the agent's quadlet, the slice, what the renderer hears from r00
    # shellcheck disable=SC2029  # the unit names are meant to expand here
    on_host "
        echo \"host   mig: \$(nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader)   slice 0:1: \$(nvidia-smi -L | sed -n 's/^ *MIG .* Device  *1: (UUID: \\(MIG-[0-9a-f-]*\\)).*/\\1/p')\"
        for u in ${units[*]}; do printf 'host   %-28s %s\\n' \"\$u\" \"\$(systemctl is-active \"\$u\" 2>/dev/null)\"; done
        systemctl list-units --all --no-legend --plain 'act-inference-*-act-inference.service' | awk '{printf \"host   %-28s %s/%s\\n\", \"policy (rhem-managed)\", \$3, \$4}'
        sed -n 's|^AddDevice=|host   the policy unit names:       |p' /etc/containers/systemd/*/act-inference-*-act-inference.container 2>/dev/null
        curl -s -m 4 http://10.20.0.1:9702/status | python3 -c '
import json, sys
try:
    s = json.load(sys.stdin)
except ValueError:
    print(\"wall   the renderer does not answer on 10.20.0.1:9702\"); sys.exit(0)
r = [x for x in s.get(\"robots\", []) if x.get(\"id\") == \"r00\"]
print(\"wall   r00: %s, last state %s s ago, %s states/s  (%s robots live, %s frames/s)\" % (r[0][\"state\"], r[0][\"age_s\"], r[0][\"datagrams_per_s\"], s.get(\"robots_live\"), s.get(\"render_fps\")) if r else \"wall   r00 is NOT on the wall: its world is not sending (%s robots live)\" % s.get(\"robots_live\"))
'
    " || echo "host   could not reach $FURY_SSH over ssh"
    ;;
esac
