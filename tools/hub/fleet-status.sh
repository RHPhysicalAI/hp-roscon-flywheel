#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# The fleet tenant as RHEM sees it, and the hub half of scaling down. From a laptop, logged in with flightctl.
#
#   tools/hub/fleet-status.sh                        one table: VM, device, approved?, labels, rendered version, statuses
#   tools/hub/fleet-status.sh decommission 24 23 ..  take these VMs' devices out of RHEM the proper way, then delete them
#   tools/hub/fleet-status.sh decommission all --yes every fleet=robots device; without --yes it lists them and stops
#   tools/hub/fleet-status.sh delete 24 ..           for a device whose VM is already GONE (it can never answer a
#                                                    decommission): delete it. Refused while the device is Online.
#                                                    NOT for a VM that is only shut off: started again, its agent
#                                                    would hold a certificate for a device the hub no longer knows.
#   tools/hub/fleet-status.sh drop-pending           list every pending enrolment request: full name, age, who it says it is
#   tools/hub/fleet-status.sh drop-pending <name>..  delete those requests. fleet-approve.sh never denies anything, so
#                                                    stale and bogus requests stay until this removes them; a long list
#                                                    can push a real clone's request off the hub's first page.
#                                                    Refused for a request that is already approved (that is a device).
#
# A robot that is merely SHUT OFF (tools/host/fury/81-fleet-scale.sh <N> shuts VMs down, it does not delete them)
# stays enrolled: its device stops reporting and the table shows it as "not reporting" - that is what a switched-off
# robot looks like, and it comes back by itself when its VM is started. Nothing to do here for that.
# REMOVING a VM for good is two steps on two machines because the host holds no hub credential (D150): this first
# (the VM must be running - the agent has to answer), then tools/host/fury/81-fleet-scale.sh remove <NN> on the host. Decommission is the agent wiping its own management
# certificate and key on the hub's request; the device ends as Decommissioned and only then is it deleted
# (flightctl v1.3.0 managing-devices.md, "Decommissioning should be performed before deleting a device").
# Removing the VM first leaves a device that stays Disconnected in the fleet view until `delete`.
#
#   FLIGHTCTL    flightctl binary, logged in to this hub     (default: flightctl, then ~/.local/bin/flightctl)
set -euo pipefail

die() { echo "${0##*/}: $*" >&2; exit 1; }
usage() { die "usage: ${0##*/} [decommission <NN>... | decommission all --yes | delete <NN>... | drop-pending [<name>...]]"; }
verb=${1:-table}
case $verb in
table) [[ -z ${2:-} ]] || usage ;;
decommission|delete)
    [[ -n ${2:-} ]] || usage
    if [[ $verb == decommission && $2 == all ]]; then
        [[ $# -eq 2 || ( $# -eq 3 && $3 == --yes ) ]] || usage
    else
        for a in "${@:2}"; do [[ $a =~ ^[0-9]{1,2}$ ]] || usage; done
    fi ;;
drop-pending)
    for a in "${@:2}"; do [[ $a =~ ^[a-z0-9][a-z0-9.-]{0,252}$ ]] || die "'$a' is not a request name - run '${0##*/} drop-pending' for the list"; done ;;
*) usage ;;
esac
fc=${FLIGHTCTL:-$(command -v flightctl || echo "$HOME/.local/bin/flightctl")}
[[ -x $fc ]] || die "no flightctl at $fc"
command -v jq >/dev/null || die "jq is missing"
devs=$("$fc" get devices -l fleet=robots --limit 0 -o json 2>/dev/null) ||
    die "flightctl is not logged in to the hub. Log in (oc login as a real user, then: flightctl login <api url> --token \"\$(oc whoami -t)\") and run this again"

if [[ $verb == table ]]; then
    ers=$("$fc" get enrollmentrequests --limit 0 -o json)
    # rows are collected first: inside a pipeline a failing jq would only lose rows, here it stops the script
    # shellcheck disable=SC2016  # the $variables are jq's
    rows=$(jq -r '.items | sort_by(.metadata.labels.alias // "") | .[] | . as $d
            | [ (.metadata.labels.alias // "-"), .metadata.name[0:12], "yes",
                ([.metadata.labels | to_entries[] | select(.key | IN("role", "threads", "zenoh_router", "pull_default")) | "\(.key)=\(.value)"] | join(",") | if . == "" then "-" else . end),
                (.status.config.renderedVersion // "-"),
                ((.status.lifecycle.status // "") as $l | if $l == "Decommissioning" or $l == "Decommissioned" then $l
                  else ((.status.summary.status // "-") | if . == "Unknown" or . == "PoweredOff" then "not-reporting" else . end) end),
                (.status.updated.status // "-"),
                ([.status.applications[]? | "\(.name):\(.status) \(.ready)"] | join(" ") | if . == "" then ($d.status.applicationsSummary.status // "-") else . end)
              ] | @tsv' <<<"$devs")
    pending=$(jq -r '.items[] | select(.status.approval == null)
            | (.spec.deviceStatus.systemInfo.hostname // "-") as $h
            | select(($h | test("^fleet-vm-[0-9]{2}$")) or (.spec.labels.enrol // "") == "fleet-vm")
            | [$h, .metadata.name[0:12], "PENDING", "-", "-", "-", "-", "-"] | @tsv' <<<"$ers")
    { printf 'VM\tDEVICE\tAPPROVED\tLABELS\tRENDERED\tDEVICE\tUPDATE\tAPPLICATION\n'
      [[ -z $rows ]] || printf '%s\n' "$rows"
      [[ -z $pending ]] || printf '%s\n' "$pending"
    } | column -t -s$'\t'
    jq -r '[.items[] | select((.status.lifecycle.status // "") != "Decommissioned")] as $d
        | ([$d[] | select((.status.summary.status // "Unknown") | . == "Unknown" or . == "PoweredOff")] | length) as $off
        | "\($d | length) devices in fleet=robots: \(($d | length) - $off) reporting, \($off) not reporting (a shut-off VM - still enrolled, back when it is started), \([$d[] | select(.status.applicationsSummary.status == "Healthy")] | length) with healthy applications, canary: \([$d[] | select(.metadata.labels.role == "canary") | .metadata.labels.alias] | join(",") | if . == "" then "NONE - the next fleet-approve.sh pass appoints one" else . end)"' <<<"$devs"
    "$fc" get fleet/robots -o json 2>/dev/null | jq -r '"Fleet robots: \([.status.conditions[]? | "\(.type)=\(.status)"] | join(" "))"' ||
        echo "Fleet robots does not exist on the hub yet (gitops/rhem/fleet-robots.yaml.draft is not live)"
    exit 0
fi

if [[ $verb == drop-pending ]]; then
    ers=$("$fc" get enrollmentrequests --limit 0 -o json) || die "could not list enrolment requests"
    if [[ $# -eq 1 ]]; then
        rows=$(jq -r '.items[] | select(.status.approval == null)
            | [.metadata.name, ((.metadata.creationTimestamp // "?") | .[0:19]), (.spec.deviceStatus.systemInfo.hostname // "-"),
               (.spec.deviceStatus.systemInfo.netIpDefault // "-"), ((.spec.labels // {}) | to_entries | map("\(.key)=\(.value)") | join(",") | if . == "" then "-" else . end)] | @tsv' <<<"$ers") ||
            die "the enrolment request list is not the JSON this script expects"
        [[ -n $rows ]] || { echo "no pending enrolment requests"; exit 0; }
        { printf 'REQUEST\tCREATED (UTC)\tSAYS IT IS\tADDRESS\tPROPOSES\n'; printf '%s\n' "$rows"; } | column -t -s$'\t'
        echo "nothing was deleted. To delete:  ${0##*/} drop-pending <REQUEST>...   (tools/hub/fleet-approve.sh --dry-run says why each one is still pending)"
        exit 0
    fi
    rc=0
    for name in "${@:2}"; do
        st=$(jq -r --arg n "$name" '[.items[] | select(.metadata.name == $n) | if .status.approval == null then "pending" else "approved" end] | join(" ")' <<<"$ers")
        case $st in
            pending) "$fc" delete "enrollmentrequest/$name" >/dev/null; echo "  deleted pending request $name" ;;
            '')      echo "  $name: no such enrolment request" >&2; rc=1 ;;
            *)       echo "  $name: already approved - that is a device now. Use:  ${0##*/} decommission <NN>" >&2; rc=1 ;;
        esac
    done
    exit "$rc"
fi

device_of() {  # the live device behind alias fleet-vm-NN; nothing when there is none
    jq -r --arg a "$1" --arg any "${2:-0}" '[.items[] | select(.metadata.labels.alias == $a) | select((.status.lifecycle.status // "") != "Decommissioned" or $any == "1") | .metadata.name] | join(" ")' <<<"$devs"
}
lifecycle() { "$fc" get "device/$1" -o json | jq -r '.status.lifecycle.status // "Unknown"'; }
drop() {  # device name, alias
    "$fc" delete "device/$1" >/dev/null
    # the request it enrolled with has the same name; whether 1.3 removes it with the device is not verified
    "$fc" delete "enrollmentrequest/$1" >/dev/null 2>&1 || true
    echo "  $2: device $1 deleted"
    if [[ $(jq -r --arg d "$1" '.items[] | select(.metadata.name == $d) | .metadata.labels.role // ""' <<<"$devs") == canary ]]; then
        echo "  $2 was the canary: the fleet has none until the next fleet-approve.sh pass appoints one"
    fi
}

targets=("${@:2}")
if [[ ${targets[0]} == all ]]; then
    # highest number first, the canary (the lowest) last. No mapfile: a Mac's bash is 3.2.
    targets=()
    while read -r t; do targets+=("$t"); done < <(jq -r '.items[].metadata.labels.alias // empty | select(test("^fleet-vm-[0-9]{2}$")) | .[9:]' <<<"$devs" | sort -ru)
    (( ${#targets[@]} > 0 )) || { echo "no fleet=robots devices"; exit 0; }
    if [[ ${3:-} != --yes ]]; then
        echo "this would decommission and delete ALL ${#targets[@]} devices of the fleet: ${targets[*]}"
        echo "nothing was done. If that is what you want:  ${0##*/} decommission all --yes"
        exit 0
    fi
fi
rc=0
for t in "${targets[@]}"; do
    alias_=$(printf 'fleet-vm-%02d' "$((10#$t))")
    dev=$(device_of "$alias_" "$([[ $verb == delete ]] && echo 1 || echo 0)")
    if [[ -z $dev ]]; then echo "  $alias_: no device - nothing to do"; continue; fi
    if [[ $dev == *' '* ]]; then echo "  $alias_: MORE THAN ONE device carries this alias ($dev) - look at them in the RHEM UI, this script will not pick" >&2; rc=1; continue; fi
    if [[ $verb == delete ]]; then
        s=$(jq -r --arg d "$dev" '.items[] | select(.metadata.name == $d) | .status.summary.status // ""' <<<"$devs")
        if [[ $s == Online ]]; then echo "  $alias_: device $dev is Online - its VM is alive. Use:  ${0##*/} decommission $t" >&2; rc=1; continue; fi
        echo "  $alias_: deleting a device that is not reporting. Right if its VM was REMOVED; wrong if it is only shut off (host: ./81-fleet-scale.sh status)"
        drop "$dev" "$alias_"; continue
    fi
    "$fc" decommission "device/$dev" >/dev/null
    l=Decommissioning
    for ((i = 0; i < 36; i++)); do l=$(lifecycle "$dev"); [[ $l != Decommissioned ]] || break; sleep 5; done
    if [[ $l == Decommissioned ]]; then
        drop "$dev" "$alias_"
    else
        echo "  $alias_: device $dev is still '$l' after 3 minutes - its VM is not answering. Shut off? Start it on the host ( ./81-fleet-scale.sh <N> ) and it finishes by itself. Gone for good?  ${0##*/} delete $t" >&2; rc=1
    fi
done
[[ $rc -ne 0 ]] || echo "done. Now on the host:  cd ~/flywheel-setup && ./81-fleet-scale.sh remove ${targets[*]}"
exit "$rc"
