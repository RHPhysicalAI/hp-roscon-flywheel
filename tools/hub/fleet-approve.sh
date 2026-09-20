#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Approve the enrolment requests that come from fleet VMs - and only those - with the labels the robots' Fleet
# reads (gitops/rhem/fleet-robots.yaml). From a laptop on the tailnet, logged in with flightctl.
#
#   tools/hub/fleet-approve.sh              one pass over the pending requests
#   tools/hub/fleet-approve.sh --watch      keep going every 10 s until Ctrl-C (while 81-fleet-scale.sh brings VMs up)
#   tools/hub/fleet-approve.sh --dry-run    say what would be approved and what is left alone, change nothing
#
# A request is approved only when ALL of this holds; anything else is left pending for a human, never denied:
#   - the agent proposes the label enrol=fleet-vm (a drop-in in the OS image, tools/host/fury/fleet/Containerfile)
#     AND NOTHING ELSE - except alias=<its own hostname>, which the 1.3 agent adds by itself when no alias is
#     configured (internal/agent/config/config.go:154). Whether `approve -l` merges or replaces what a requester
#     proposes is not verified, so a request proposing pull_default, role, site, zenoh_router or anything else is
#     refused outright: pull_default=insecureAcceptAnything alone would turn that device's policy.json default
#     from reject to accept-anything (D131, D150)
#   - it was made in the last MAX_AGE_MIN minutes, and - when FLEET_EXPECT is set - its number is one the host
#     was actually asked to create
#   - its hostname is fleet-vm-NN, NN from 01 to 32
#   - its default address is 10.20.0.(20+NN) and its MAC 52:54:00:14:01:<NN hex> - what 81-fleet-scale.sh gave VM NN
#   - it is an arm64 machine
#   - no device that is not decommissioned already has alias=fleet-vm-NN, and no other pending request claims NN
#     (two claims on one number: neither is approved, both are printed)
# What that is and is not: everything above is reported by the requester, so it is a consistency check, not
# authentication. Authentication is the enrolment certificate - a request cannot be made without one signed by
# this hub, and the only one for the fleet is root-only on the host and in its clones
# (docs/internal/FLEET-VMS.md, "Enrolment"). What a wrongly approved device would get is the Fleet's content:
# public keys, a policy.json and two public, signed images - no secret.
#
# Labels given: fleet=robots site=fury gpu=none policy_device=cpu arch=arm64 alias=fleet-vm-NN robot=NN threads=<T>,
# and role=canary on exactly one device: the lowest-numbered VM approved while the fleet has no canary.
# No zenoh_router / zenoh_port: the template's default (127.0.0.1:7447, the router on the device) is what is wanted.
# No pull_default: a robot stays fail-closed.
#
#   FLIGHTCTL      flightctl binary, logged in to this hub     (default: flightctl, then ~/.local/bin/flightctl)
#   THREADS        the threads label = the VMs' vCPUs           (default: 2)
#   MAX_AGE_MIN    ignore requests older than this many minutes (default: 30; 0 = any age). A clone that waited
#                  longer is not broken - raise this for one run once you know why it waited
#   FLEET_EXPECT   the numbers the host created, e.g. "01 02 03" or "1-8" (default: unset = 01..32). Set it for
#                  --watch: an unattended loop should only accept what somebody asked for
#   WATCH_MINUTES  --watch stops by itself after this long      (default: 30)
#
# Requests are never denied here, so stale or bogus ones pile up, and a long list can push a real clone off the
# first page (this script warns when the hub says the list is cut short). Clearing them is wrapped too:
#     tools/hub/fleet-status.sh drop-pending <request-name>...
set -euo pipefail

die() { echo "${0##*/}: $*" >&2; exit 1; }
mode=once
case ${1:-} in
'') ;;
--watch) mode=watch ;;
--dry-run) mode=dry ;;
*) die "usage: ${0##*/} [--watch | --dry-run]" ;;
esac
[[ -z ${2:-} ]] || die "usage: ${0##*/} [--watch | --dry-run]"
threads=${THREADS:-2}
[[ $threads =~ ^[1-8]$ ]] || die "THREADS is the VMs' vCPU count, 1..8"
max_age=${MAX_AGE_MIN:-30}; watch_min=${WATCH_MINUTES:-30}
[[ $max_age =~ ^[0-9]{1,5}$ && $watch_min =~ ^[0-9]{1,4}$ ]] || die "MAX_AGE_MIN and WATCH_MINUTES are whole minutes"
expect=                                     # space-separated two-digit numbers; empty = any of 01..32
for tok in ${FLEET_EXPECT:-}; do
    if [[ $tok =~ ^([0-9]{1,2})-([0-9]{1,2})$ ]]; then lo=$((10#${BASH_REMATCH[1]})); hi=$((10#${BASH_REMATCH[2]}))
    elif [[ $tok =~ ^[0-9]{1,2}$ ]]; then lo=$((10#$tok)); hi=$lo
    else die "FLEET_EXPECT is numbers and ranges, e.g. \"01 02 03\" or \"1-8\" - not '$tok'"; fi
    (( lo >= 1 && hi <= 32 && lo <= hi )) || die "FLEET_EXPECT: '$tok' is outside 01..32"
    for ((x = lo; x <= hi; x++)); do expect+="$(printf '%02d' "$x") "; done
done
fc=${FLIGHTCTL:-$(command -v flightctl || echo "$HOME/.local/bin/flightctl")}
[[ -x $fc ]] || die "no flightctl at $fc"
command -v jq >/dev/null || die "jq is missing"
"$fc" get fleets >/dev/null 2>&1 ||
    die "flightctl is not logged in to the hub. Log in (oc login as a real user, then: flightctl login <api url> --token \"\$(oc whoami -t)\") and run this again"

# One line per pending request: name, verdict (NN or a reason), hostname. All the judging is here, in one jq program.
# shellcheck disable=SC2016  # the $variables are jq's
judge='
  def nn: capture("^fleet-vm-(?<n>[0-9]{2})$").n;
  ($devs.items | map(select((.status.lifecycle.status // "") != "Decommissioned") | .metadata.labels.alias // empty)) as $taken
  | [ .items[] | select(.status.approval == null)
      | (.spec.deviceStatus.systemInfo // {}) as $si
      | {name: .metadata.name, host: ($si.hostname // ""), labels: (.spec.labels // {}),
         age: (((.metadata.creationTimestamp // "") | sub("\\.[0-9]+Z$"; "Z") | (try fromdateiso8601 catch null)) as $t | if $t == null then null else ((now - $t) / 60 | floor) end),
         ip: (($si.netIpDefault // "") | split("/")[0]), mac: (($si.netMacDefault // "") | ascii_downcase),
         arch: ($si.architecture // "")}
      | .n = ((.host | nn?) // null)
    ] as $reqs
  | $reqs[]
  | . as $r
  | (if   ($r.labels.enrol // "") != "fleet-vm"          then "not-a-fleet-vm (no enrol=fleet-vm label)"
     elif (($r.labels | del(.enrol) | del(.alias) | length) > 0) or ($r.labels | has("alias") and .alias != $r.host)
                                                         then "PROPOSES-ITS-OWN-LABELS: \($r.labels | del(.enrol) | to_entries | map("\(.key)=\(.value)") | join(","))"
     elif $r.n == null                                   then "hostname-is-not-fleet-vm-NN"
     elif (($r.n | tonumber) < 1 or ($r.n | tonumber) > 32) then "number-out-of-range"
     elif $r.ip  != "10.20.0.\(20 + ($r.n | tonumber))"  then "address-\($r.ip)-is-not-that-VMs"
     elif $r.mac != "52:54:00:14:01:\(($r.n | tonumber) as $x | "0123456789abcdef" as $h | $h[($x / 16 | floor):($x / 16 | floor) + 1] + $h[($x % 16):($x % 16) + 1])" then "mac-\($r.mac)-is-not-that-VMs"
     elif ($r.arch != "arm64" and $r.arch != "aarch64")  then "architecture-\($r.arch)"
     elif ($taken | index($r.host)) != null              then "DUPLICATE: a device already has alias=\($r.host)"
     elif ([$reqs[] | select(.host == $r.host)] | length) > 1 then "DUPLICATE: more than one pending request claims \($r.host)"
     elif ($expect != "" and (($expect | split(" ")) | index($r.n)) == null) then "not-expected (FLEET_EXPECT does not list \($r.n))"
     elif ($maxage > 0 and ($r.age == null or $r.age > $maxage)) then "too-old (\($r.age // "?") min; MAX_AGE_MIN=\($maxage))"
     else "ok" end) as $v
  | [$r.name, $v, (if $r.host == "" then "-" else $r.host end), ($r.n // "-")] | @tsv'

pass() {
    local ers devs canary name verdict host n labels shown l reqs cut approved=0
    ers=$("$fc" get enrollmentrequests --limit 0 -o json) || die "could not list enrolment requests"
    devs=$("$fc" get devices -l fleet=robots --limit 0 -o json) || die "could not list the fleet's devices"
    cut=$(jq -r '[.metadata.remainingItemCount // empty, (.metadata.continue // empty | select(. != "") | "more")] | join(" ")' <<<"$ers") || die "the enrolment request list is not the JSON this script expects"
    [[ -z $cut ]] || echo "  WARNING: the hub cut the request list short ($cut not shown) - a real clone may be among them. Clear stale ones:  tools/hub/fleet-status.sh drop-pending <name>..." >&2
    # jq's verdicts are collected first: read straight from a pipe, a jq error would look like 'nothing to approve'
    reqs=$(jq -r --argjson devs "$devs" --arg expect "${expect% }" --argjson maxage "$max_age" "$judge" <<<"$ers") ||
        die "could not judge the pending requests - the hub's JSON is not what this script expects. Nothing was approved. Look:  $fc get enrollmentrequests -o json | jq '.items[0]'"
    canary_count() { "$fc" get devices -l fleet=robots --limit 0 -o json | jq -r '[.items[] | select((.status.lifecycle.status // "") != "Decommissioned") | select(.metadata.labels.role == "canary")] | length'; }
    canary=$(canary_count) || die "could not count the fleet's canaries"
    while IFS=$'\t' read -r name verdict host n; do
        [[ -n $name ]] || continue
        if [[ $verdict != ok ]]; then
            if [[ $mode != watch || $verdict == DUPLICATE* || $verdict == PROPOSES* ]]; then printf '  left pending  %-14s %s  (%s)\n' "$host" "$name" "$verdict"; fi
            continue
        fi
        labels=(-l fleet=robots -l site=fury -l gpu=none -l policy_device=cpu -l arch=arm64 -l "alias=$host" -l "robot=$n" -l "threads=$threads")
        # read again right before appointing one: a --watch loop and a manual run must not each pick a canary
        if (( canary == 0 )) && [[ $mode != dry ]]; then canary=$(canary_count) || die "could not count the fleet's canaries"; fi
        if (( canary == 0 )); then labels+=(-l role=canary); canary=1; fi
        if [[ $mode == dry ]]; then
            shown=; for l in "${labels[@]}"; do [[ $l == -l ]] || shown+="$l "; done
            printf '  would approve %-14s %s  %s\n' "$host" "$name" "$shown"
        else
            "$fc" approve "${labels[@]}" "enrollmentrequest/$name" >/dev/null
            printf '  approved      %-14s %s%s\n' "$host" "$name" "$([[ " ${labels[*]} " == *role=canary* ]] && echo '  (the canary)')"
        fi
        approved=$((approved + 1))
    done < <(sort -t$'\t' -k3,3 <<<"$reqs")
    [[ $mode == watch && $approved -eq 0 ]] || echo "$(date +%H:%M:%S)  $approved $([[ $mode == dry ]] && echo 'would be approved (nothing was changed)' || echo approved), fleet=robots devices now: $("$fc" get devices -l fleet=robots -o name | wc -l | tr -d ' ')"
}

if [[ $mode == watch ]]; then
    [[ -n $expect ]] || echo "note: FLEET_EXPECT is not set - every number from 01 to 32 is acceptable to this unattended loop. Better:  FLEET_EXPECT=\"1-8\" $0 --watch"
    echo "watching for fleet VMs' enrolment requests every 10 s for $watch_min min - Ctrl-C to stop sooner. Duplicates and label-proposers are printed, never approved."
    deadline=$(( $(date +%s) + watch_min * 60 ))
    while (( $(date +%s) < deadline )); do pass; sleep 10; done
    echo "stopped after $watch_min minutes (WATCH_MINUTES). Run it again if VMs are still coming up."
else
    pass
    [[ $mode == dry ]] || echo "next: tools/hub/fleet-status.sh"
fi
