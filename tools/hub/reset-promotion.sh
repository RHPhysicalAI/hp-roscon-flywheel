#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Put the hub and the device back to "before the last promotion", so the promotion beat can be shown again.
# Between rehearsals - not a demo-time step. It reverts the merge commit of the newest promotion PR on the GitOps
# branch: that one commit holds the Fleet's modelcar digest and MODEL_VERSION, the trigger's lineage and the
# catalog item, so reverting it is the whole reset. RHEM then rolls the previous model back out; its image is
# still in the device's storage, so nothing is pulled.
#
#   tools/hub/reset-promotion.sh            show what would be reverted, ask, revert, push, wait for the device
#   tools/hub/reset-promotion.sh --yes      the same without asking
#
# Left alone on purpose: the run's evaluation records in MinIO (the evaluation page stays pinned to them), the
# registry version (the next run updates it), the signed images (every run's images keep their own tags).
# Then show the beat again with:  tools/hub/start-promotion-run.sh <candidate> 0.25 360
set -euo pipefail
BRANCH=${GITOPS_BRANCH:-fury}
die() { echo "${0##*/}: $*" >&2; exit 1; }
for t in git jq; do command -v "$t" > /dev/null || die "$t is not installed"; done
top=$(git rev-parse --show-toplevel) || die "run it from a checkout of the repository"
cd "$top"

git fetch -q origin "$BRANCH"
merge=$(git log "origin/$BRANCH" --merges -1 --format=%H --grep='^Merge pull request #[0-9]* from .*/promote/')
[[ -n $merge ]] || die "no promotion merge on origin/$BRANCH"
if git log "origin/$BRANCH" --format=%s "$merge..origin/$BRANCH" | grep -q "^Revert \"$(git log -1 --format=%s "$merge" | sed 's/[][\.*^$/]/\\&/g')"; then
    die "the newest promotion merge ($(git log -1 --format=%h "$merge")) is already reverted - nothing to reset"
fi
echo "newest promotion on origin/$BRANCH:"
git log -1 --format='  %h  %s%n      %b' "$merge" | sed '/^ *$/d'
git diff "$merge^1" "$merge" --stat | sed 's/^/  /'
if [[ ${1:-} != --yes ]]; then
    read -r -p "revert it and push to $BRANCH? [y/N] " a || a=n
    [[ $a == y || $a == Y ]] || die "left as it is"
fi

fleet_mv() { flightctl get fleet act-inference -o json 2> /dev/null | jq -r '[.spec.template.spec.applications[]?.envVars.MODEL_VERSION // empty] | join(",")' || true; }
before=$(command -v flightctl > /dev/null && fleet_mv || true)

# In a scratch worktree, so that whatever is uncommitted in this checkout is never touched.
wt=$(mktemp -d)
trap 'git worktree remove --force "$wt" > /dev/null 2>&1 || true' EXIT
git worktree add -q --detach "$wt" "origin/$BRANCH"
git -C "$wt" revert -m 1 --no-edit "$merge" > /dev/null || die "the revert does not apply cleanly - something changed the same lines since; resolve by hand"
git -C "$wt" push -q origin "HEAD:$BRANCH"
echo "pushed $(git -C "$wt" log -1 --format='%h  %s')"
git merge -q --ff-only "origin/$BRANCH" 2> /dev/null || echo "note: this checkout is not on $BRANCH or has diverged - pull it yourself"

command -v flightctl > /dev/null || { echo "flightctl is not installed here - watch the rollout in the RHEM UI"; exit 0; }
echo "waiting for the device (up to 4 minutes)"
for _ in $(seq 1 24); do
    sleep 10
    f=$(fleet_mv)
    d=$(flightctl get devices -o json 2> /dev/null | jq -r '[.items[] | select(.metadata.owner == "Fleet/act-inference") | "\(.status.updated.status)/\(.status.applicationsSummary.status)"] | join(" ")' || true)
    echo "  fleet serves ${f:-?}   devices ${d:-?}"
    # the fleet has to have re-rendered first: until then the device is still "up to date" with the promoted spec
    [[ -n $f && $f != "$before" && -n $d && $d != *OutOfDate* && $d != *Updating* && $d != *Error* && $d != *Degraded* ]] && { echo "reset: the fleet serves $f"; exit 0; }
done
echo "the device has not settled yet - check the RHEM UI"
