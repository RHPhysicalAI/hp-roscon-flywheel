#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# One command for the presenter to size the fleet's worlds (D163 stage B: the physics-only sims that run as pods
# on the hub, gitops/fleet-worlds), from a laptop:
#
#   fleet-worlds-scale.sh <N>                   N worlds: robots r01 ... r<N>; 0 stops them all
#   fleet-worlds-scale.sh status                what is running, and which robot each pod is
#   fleet-worlds-scale.sh trajectories <npz>    recorded motion for every world, from tools/fleet/extract_trajectories.py
#                                               (a hand-made ConfigMap; the worlds restart to read it)
#   fleet-worlds-scale.sh trajectories --none   back to the built-in synthetic motion
#
# Scaling up goes in steps of STEP worlds, each waited for: a world wants more than two cores for the half minute
# it takes to start, and the hub is one node that also runs the platform. Scaling down is immediate.
# The ceiling: a world costs about one core and 0.75 GiB at steady state (measured; see world.yaml), and the hub has
# 32 vCPUs. 16 worlds leave the platform its share; above 20 this script refuses.
#
#   KUBECONFIG         the hub's kubeconfig                                        (required)
#   STEP               worlds started at once when scaling up                      (default 4)
#   FLEET_WORLDS_MAX   the ceiling, for an operator who has measured the hub       (default 20)
set -euo pipefail

NS=fleet
STS=statefulset/world
COMFORTABLE=16
max=${FLEET_WORLDS_MAX:-20}
step=${STEP:-4}
die() { echo "${0##*/}: $*" >&2; exit 1; }
[[ -n ${KUBECONFIG:-} ]] || die "KUBECONFIG must be set"
for t in oc jq; do command -v "$t" >/dev/null || die "$t is not installed"; done
[[ $max =~ ^[0-9]+$ && $max -le 98 ]] || die "FLEET_WORLDS_MAX must be a number up to 98 (robot ids are r01-r99)"
[[ $step =~ ^[1-9][0-9]*$ ]] || die "STEP must be a positive number"
need_worlds() {
    oc -n "$NS" get "$STS" -o name >/dev/null 2>&1 ||
        die "no $STS in namespace $NS: apply the Argo CD app first (oc apply -f argocd/fleet-worlds-app.yaml)"
}

summary() {
    oc -n "$NS" get "$STS" -o json |
        jq -r '"worlds: \(.status.readyReplicas // 0) ready of \(.spec.replicas) wanted" +
               (if .spec.replicas > 0 then " (r01-r\(.spec.replicas | tostring | if length < 2 then "0" + . else . end))" else "" end)'
}
wait_ready() {  # wanted seconds
    local ready i
    for ((i = 0; i < $2; i += 5)); do
        ready=$(oc -n "$NS" get "$STS" -o json | jq -r '.status.readyReplicas // 0')
        [[ $ready -ge $1 ]] && return 0
        sleep 5
    done
    return 1
}

case ${1:-} in
status)
    need_worlds
    summary
    oc -n "$NS" get pods -l app=world -o json |
        jq -r '.items | sort_by(.metadata.name | split("-")[-1] | tonumber)[]
               | (.metadata.name | split("-")[-1] | tonumber + 1 | tostring | if length < 2 then "0" + . else . end) as $nn
               | "  r\($nn)  \(.metadata.name)  \(.status.phase)  ready=\([.status.containerStatuses[]?.ready] | length > 0 and all)  restarts=\([.status.containerStatuses[]?.restartCount] | add // 0)"'
    oc -n "$NS" get configmap fleet-trajectories -o name >/dev/null 2>&1 &&
        echo "motion: recorded (ConfigMap fleet-trajectories)" || echo "motion: built-in synthetic"
    ;;
trajectories)
    file=${2:?an npz from tools/fleet/extract_trajectories.py, or --none}
    if [[ $file == --none ]]; then
        need_worlds
        oc -n "$NS" delete configmap fleet-trajectories --ignore-not-found
    else
        [[ -f $file ]] || die "no file $file"
        size=$(wc -c <"$file")
        [[ $size -le $((700 * 1024)) ]] ||
            die "$file is $((size / 1024)) KiB; a ConfigMap holds about 700 KiB of it. Extract again with a lower --fps or fewer --episodes"
        need_worlds
        oc -n "$NS" create configmap fleet-trajectories --from-file=trajectories.npz="$file" --dry-run=client -o yaml |
            oc -n "$NS" apply --server-side --force-conflicts -f -
    fi
    # a running world has read its motion already; restart them, one after the other
    oc -n "$NS" rollout restart "$STS"
    oc -n "$NS" rollout status "$STS" --timeout=30m
    summary
    ;;
''|-h|--help)
    sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; exit 1
    ;;
*)
    n=$1
    [[ $n =~ ^[0-9]+$ ]] || die "usage: ${0##*/} <N> | status | trajectories <npz>|--none"
    [[ $n -le $max ]] ||
        die "$n worlds is above the ceiling of $max: a world is a core and 0.8 GiB, and the hub is one 32-vCPU node that runs the
    platform too. If the hub has room (oc adm top node), raise it for this run: FLEET_WORLDS_MAX=$n ${0##*/} $n"
    need_worlds
    [[ $n -le $COMFORTABLE ]] || echo "note: above $COMFORTABLE worlds the platform's headroom gets thin - watch: oc adm top node"
    now=$(oc -n "$NS" get "$STS" -o json | jq -r '.spec.replicas')
    if [[ $n -le $now ]]; then
        oc -n "$NS" scale "$STS" --replicas="$n" >/dev/null
    else
        while [[ $now -lt $n ]]; do
            now=$(( now + step > n ? n : now + step ))
            oc -n "$NS" scale "$STS" --replicas="$now" >/dev/null
            echo "  $now wanted, waiting for them to be ready..."
            wait_ready "$now" 360 || die "only $(oc -n "$NS" get "$STS" -o json | jq -r '.status.readyReplicas // 0') of $now worlds became ready in 6 minutes. Look at:  ${0##*/} status   and   oc -n $NS logs world-$((now - 1)) --tail=40"
        done
    fi
    summary
    ;;
esac
