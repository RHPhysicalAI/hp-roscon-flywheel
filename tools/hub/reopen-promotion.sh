#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Show the promotion beat again without running the pipeline: the mirror image of tools/hub/reset-promotion.sh.
# It takes the newest promotion merge on the GitOps branch - which the reset has reverted - and proposes the same
# change again in a NEW pull request. A human merges it, and RHEM rolls the signed model to the host and through
# the robots' batches, exactly as the first time. Between showings - not a demo-time step.
#
#   tools/hub/reopen-promotion.sh           dry run: the branch, the change and the PR text it would make
#   tools/hub/reopen-promotion.sh --open    push the branch and open the pull request
#
# The pull request says what it is. Its title ends in "re-opened for a showing"; its text names the PR the pipeline
# opened, the pipeline run and the date, states that the pipeline did not run again and nothing was re-measured,
# and quotes the original text unchanged. This script writes no evaluation data, no image, no signature and no
# registry entry, and it alters no existing commit, PR or record: one new branch, one new commit, one new PR.
#
# The branch is promote/<candidate>-reshow-<UTC time>, so that the next reset finds THIS merge - merge it with a
# merge commit. The commit is the cherry-pick of the promotion merge, with every Fleet file brought to what the
# host Fleet's file pins (a promotion from before D166 knew only the host's file).
# Refused: when the newest promotion is not reverted (reset first), and while a rollout is in progress.
#
#   GITOPS_BRANCH   the branch the hub follows (default: fury)
#   FLIGHTCTL       flightctl binary, logged in to this hub (default: flightctl, then ~/.local/bin/flightctl)
set -euo pipefail
BRANCH=${GITOPS_BRANCH:-fury}
die() { echo "${0##*/}: $*" >&2; exit 1; }
# shellcheck source=tools/hub/lib-promotion.sh
. "$(dirname "$0")/lib-promotion.sh"
[[ $# -eq 0 || ( $# -eq 1 && $1 == --open ) ]] || die "usage: ${0##*/} [--open]"
open=${1:-}
for t in git jq gh; do command -v "$t" > /dev/null || die "$t is not installed"; done
find_flightctl
top=$(git rev-parse --show-toplevel) || die "run it from a checkout of the repository"
cd "$top"
if [[ -n $open ]]; then
    require_no_rollout
else
    (require_no_rollout) || echo "dry run: --open refuses for as long as that is so"
fi

git fetch -q origin "$BRANCH"
merge=$(newest_promotion_merge)
[[ -n $merge ]] || die "no promotion merge on origin/$BRANCH - there is nothing to show again. A promotion comes from a pipeline run:  tools/hub/start-promotion-run.sh <candidate>"
short=$(git log -1 --format=%h "$merge")
subject=$(git log -1 --format=%s "$merge")
promotion_reverted "$merge" || die "the newest promotion ($short  $subject) is in effect, not reverted - the reset comes first:  tools/hub/reset-promotion.sh"

# The PR the pipeline opened. A re-opened promotion records it in its commit, so a second showing names and quotes
# the original and never a re-opening.
pr=${subject#Merge pull request #}; pr=${pr%% *}
orig=$pr
what="merge $short (PR #$pr)"
if [[ $subject == *-reshow-* ]]; then
    orig=$(git log --format=%B "$merge^1..$merge^2" | sed -n 's/^Re-proposes PR #\([0-9][0-9]*\).*/\1/p')
    orig=${orig%%$'\n'*}
    what="$what, itself a re-opening of PR #$orig"
fi
[[ $orig =~ ^[0-9]+$ ]] || die "could not tell which PR the pipeline opened for $short - its branch commit has no 'Re-proposes PR #<n>' line. Look at:  git log $merge^1..$merge^2"
meta=$(gh pr view "$orig" --json number,title,body,headRefName,createdAt) ||
    die "gh could not read PR #$orig. Log in (gh auth login) and run this again - the new PR quotes the original, so it is not written without it"
otitle=$(jq -r '.title' <<<"$meta"); obody=$(jq -r '.body' <<<"$meta" | tr -d '\r')
ohead=$(jq -r '.headRefName' <<<"$meta"); odate=$(jq -r '.createdAt[0:10]' <<<"$meta")

# In a scratch worktree, so that whatever is uncommitted in this checkout is never touched.
wt=$(mktemp -d)
trap 'git worktree remove --force "$wt" > /dev/null 2>&1 || true' EXIT
git worktree add -q --detach "$wt" "origin/$BRANCH"
git -C "$wt" cherry-pick -m 1 --no-commit "$merge" > /dev/null 2>&1 ||
    die "the promotion $short does not apply cleanly to $BRANCH - something changed the same lines since the reset. Nothing was pushed; compare:  git diff $merge^1 $merge  with  git log -p $merge..origin/$BRANCH"
align_fleets "$wt"
if git -C "$wt" diff --cached --quiet; then
    die "re-applying $short changes nothing: $BRANCH already holds what it promoted ($ref_version). The reset comes first:  tools/hub/reset-promotion.sh"
fi
cand=$ref_version

run=""; nums=""
if [[ $ohead == "promote/$cand-"* ]]; then run=${ohead#"promote/$cand-"}; fi
[[ $run =~ ^[0-9a-f]{8}$ ]] || run=""   # anything else is a timestamp, not a run id
if [[ $otitle == "Promote $cand ("* ]]; then nums=$(sed -En 's/^[^(]*\(([0-9]+% -> [0-9]+%)\).*/\1/p' <<<"$otitle"); fi
branch="promote/$cand-reshow-$(date -u +%Y%m%dT%H%M%SZ)"
title="Promote $cand${nums:+ ($nums)} - re-opened for a showing"
body="Re-proposes PR #$orig (${run:+pipeline run $run, }$odate): same signed image and transparency-log entry, same gate record. The pipeline did not run again; nothing was re-measured.

This branch is the cherry-pick of $what onto \`$BRANCH\`, made by tools/hub/reopen-promotion.sh after tools/hub/reset-promotion.sh took that promotion back, so that the promotion can be shown again. Merging pins \`$ref_digest\` and \`MODEL_VERSION\` \`$cand\` in every Fleet file ($HOST_FLEET_FILE $OTHER_FLEET_FILES) and sets the trigger's lineage and the catalog entry as that merge did. Nothing else was written: no image, signature, registry version or evaluation record. Merge it with a merge commit - the reset finds a promotion by its merge commit.

The text of PR #$orig, quoted unchanged:

$(sed 's/^/> /' <<<"$obody")"

echo "newest promotion on origin/$BRANCH, reverted:  $short  $subject"
echo "branch $branch:"
git -C "$wt" diff --cached --stat | sed 's/^/  /'
[[ -z $aligned ]] || echo "  ($aligned is not part of $short: brought to what ${HOST_FLEET_FILE##*/} pins)"
echo "pull request against $BRANCH:"
echo "  $title"
sed 's/^/  | /' <<<"$body"
if [[ -z $open ]]; then
    echo "dry run: nothing was pushed and no pull request was opened. For real:  ${0##*/} --open"
    exit 0
fi

git -C "$wt" commit -q -m "Promote $cand again for a showing: cherry-pick of $short (PR #$pr)" \
    -m "Re-proposes PR #$orig${run:+ (pipeline run $run)}. Same signed image ($ref_digest), same gate record; the pipeline did not run again and nothing was re-measured. Every Fleet file pins what ${HOST_FLEET_FILE##*/} pins."
git -C "$wt" push -q origin "HEAD:refs/heads/$branch" || die "the push of $branch was refused - this login may not push to the repository. No pull request was opened"
url=$(gh pr create --base "$BRANCH" --head "$branch" --title "$title" --body "$body") ||
    die "branch $branch is pushed, but the pull request was not opened. Open it by hand with the title and text above:  gh pr create --base $BRANCH --head $branch"
echo "opened $url"
echo "merge it with a merge commit to show the beat; watch the robots with tools/hub/fleet-status.sh. To take it back:  tools/hub/reset-promotion.sh"
