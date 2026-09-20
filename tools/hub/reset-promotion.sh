#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Put the hub, the host's device and the robots back to "before the last promotion", so the promotion beat can be
# shown again. Between showings - not a demo-time step. It reverts the merge commit of the newest promotion PR on
# the GitOps branch: that one commit holds every Fleet file's modelcar digest and MODEL_VERSION, the trigger's
# lineage and the catalog item, so reverting it is the whole reset. RHEM then rolls the previous model back out:
# to the host at once, to the robots batch by batch (this script watches for a quarter of an hour, then hands over
# to tools/hub/fleet-status.sh - robots still rolling are not a failure).
#
#   tools/hub/reset-promotion.sh            show what would be pushed, ask, push, wait for the host and the robots
#   tools/hub/reset-promotion.sh --yes      the same without asking
#
# After a reset every Fleet file pins the same model, and the host Fleet's file is the reference. A promotion
# merged before the PR step edited both Fleets (D166) changed only the host's file; reverting it would leave the
# robots on the promoted model, so the robots' file is then brought along in a second commit of the same push.
# What a device pulls: nothing when it has served the previous model before - the image stays in its storage
# (image volume, reclaimPolicy Retain). The robots have not, the first time: each pulls the previous modelcar
# once, under its own signature policy. From the second reset on, both images are on every device.
# Refused while a device of either Fleet is Updating or OutOfDate: let a rollout finish before starting the next.
#
# Left alone on purpose: the run's evaluation records in object storage (the evaluation page stays pinned to
# them), the registry version (the next run updates it), the signed images (every run's images keep their own tags).
# Then show the beat again, cheaply:  tools/hub/reopen-promotion.sh  (re-proposes the same signed model in a new
# PR, says so in the PR, runs nothing). With a real run instead:  tools/hub/start-promotion-run.sh <candidate> 0.25 360
#
#   GITOPS_BRANCH   the branch the hub follows (default: fury)
#   FLIGHTCTL       flightctl binary, logged in to this hub (default: flightctl, then ~/.local/bin/flightctl)
set -euo pipefail
BRANCH=${GITOPS_BRANCH:-fury}
die() { echo "${0##*/}: $*" >&2; exit 1; }
# shellcheck source=tools/hub/lib-promotion.sh
. "$(dirname "$0")/lib-promotion.sh"
[[ $# -eq 0 || ( $# -eq 1 && $1 == --yes ) ]] || die "usage: ${0##*/} [--yes]"
for t in git jq; do command -v "$t" > /dev/null || die "$t is not installed"; done
find_flightctl
top=$(git rev-parse --show-toplevel) || die "run it from a checkout of the repository"
cd "$top"
require_no_rollout

git fetch -q origin "$BRANCH"
merge=$(newest_promotion_merge)
[[ -n $merge ]] || die "no promotion merge on origin/$BRANCH - there is nothing to reset"
if promotion_reverted "$merge"; then
    die "the newest promotion merge ($(git log -1 --format=%h "$merge")) is already reverted - nothing to reset. To show the promotion again:  tools/hub/reopen-promotion.sh"
fi
echo "newest promotion on origin/$BRANCH:"
git log -1 --format='  %h  %s%n      %b' "$merge" | sed '/^ *$/d'

# In a scratch worktree, so that whatever is uncommitted in this checkout is never touched.
wt=$(mktemp -d)
trap 'git worktree remove --force "$wt" > /dev/null 2>&1 || true' EXIT
git worktree add -q --detach "$wt" "origin/$BRANCH"
git -C "$wt" revert -m 1 --no-edit "$merge" > /dev/null || die "the revert does not apply cleanly - something changed the same lines since; resolve by hand: git revert -m 1 $merge on $BRANCH"
align_fleets "$wt"
if [[ -n $aligned ]]; then
    git -C "$wt" commit -q -m "Reset: $aligned follows ${HOST_FLEET_FILE##*/} back to $ref_version" \
        -m "The reverted promotion merge ($(git log -1 --format=%h "$merge")) is from before the promotion PR edited every Fleet file, so its revert moved only the host Fleet's file. After a reset every Fleet file pins the same model, the host Fleet's file being the reference: $ref_version, ${ref_digest:0:19}..."
fi
echo "to push to $BRANCH:"
git -C "$wt" log --format='  %h  %s' "origin/$BRANCH..HEAD"
git -C "$wt" diff --stat "origin/$BRANCH" HEAD | sed 's/^/  /'
echo "afterwards every Fleet file pins $ref_version (${ref_digest:0:19}...)"
[[ -z $aligned ]] || echo "  the promotion merge did not know $aligned: the second commit brings it along (a robot that never served $ref_version pulls it, once)"
if [[ ${1:-} != --yes ]]; then
    read -r -p "push to $BRANCH? [y/N] " a || a=n
    [[ $a == y || $a == Y ]] || die "left as it is - nothing was pushed"
fi
git -C "$wt" push -q origin "HEAD:$BRANCH" || die "the push to $BRANCH was refused - the branch moved meanwhile, or this login may not push to it. Nothing was changed; run this again"
echo "pushed $(git -C "$wt" log -1 --format='%h  %s')"
git merge -q --ff-only "origin/$BRANCH" 2> /dev/null || echo "note: this checkout is not on $BRANCH or has diverged - pull it yourself"

fleet_mv() { "$fc" get fleet "$1" -o json 2> /dev/null | jq -r '[.spec.template.spec.applications[]?.envVars.MODEL_VERSION // empty] | join(",")' || true; }
echo "waiting for the host's device, then the robots' batches (up to 15 minutes; stopping this wait changes nothing - the push is done)"
settled=0 d='' r=''
for _ in $(seq 1 90); do
    sleep 10
    hf=$(fleet_mv "$HOST_FLEET"); rf=$(fleet_mv "$ROBOTS_FLEET")
    devs=$("$fc" get devices --limit 0 -o json 2> /dev/null || true)
    d=$(jq -r --arg o "Fleet/$HOST_FLEET" '[.items[] | select(.metadata.owner == $o) | "\(.status.updated.status)/\(.status.applicationsSummary.status)"] | join(" ")' <<<"$devs" 2> /dev/null || true)
    r=$(jq -r --arg o "Fleet/$ROBOTS_FLEET" '[.items[] | select(.metadata.owner == $o)] | "\([.[] | select(.status.updated.status == "UpToDate")] | length)/\(length)"' <<<"$devs" 2> /dev/null || true)
    echo "  Fleet $HOST_FLEET serves ${hf:-?}, its devices ${d:-?}   Fleet $ROBOTS_FLEET serves ${rf:-?}, robots ${r:-?} UpToDate"
    # a Fleet has to have re-rendered first: until then its devices are still "up to date" with the promoted spec.
    # Twice in a row, because a device is marked OutOfDate a moment after its Fleet changes.
    if [[ $hf == "$ref_version" && $rf == "$ref_version" && -n $d && $d != *OutOfDate* && $d != *Updating* && $d != *Error* && $d != *Degraded* && -n $r && ${r%/*} == "${r#*/}" ]]; then
        settled=$((settled + 1))
    else
        settled=0
    fi
    [[ $settled -lt 2 ]] || { echo "reset: both Fleets serve $ref_version, robots $r UpToDate"; exit 0; }
done
echo "not settled within 15 minutes: the host's devices ${d:-?}, robots ${r:-?} UpToDate. The robots roll in batches - that is no failure. Keep watching:  tools/hub/fleet-status.sh"
