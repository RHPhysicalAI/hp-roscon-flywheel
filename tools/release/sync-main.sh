#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Prepare `main` - the reading copy people land on - from the working branch: `main` is the working branch minus
# the paths listed in tools/lint/excluded-from-main.txt. One direction only: the working branch is never merged
# FROM main. Nothing here touches the running system: its GitOps follows the working branch, not main.
#
#   tools/release/sync-main.sh            build the result on a local branch release/main-sync-<UTC time>, lint it
#   tools/release/sync-main.sh --push     the same, and push THAT branch (never main) so it can be reviewed
#
# It works in a scratch worktree, so whatever is uncommitted in this checkout is not touched. What it makes:
# a merge commit of origin/<working branch> into origin/main (history is kept, so the next sync merges cleanly),
# then one commit that removes the excluded paths. When the merge conflicts ONLY on excluded paths (they were
# removed on main and changed on the working branch), the removal resolves it inside the merge.
# Landing it is the owner's step, by fast-forward so that no squash or rebase can break the ancestry:
#     git push origin release/main-sync-<UTC time>:main
#
#   WORK_BRANCH   the working branch (default: fury)
set -euo pipefail
work=${WORK_BRANCH:-fury}
die() { echo "${0##*/}: $*" >&2; exit 1; }
[[ $# -eq 0 || ( $# -eq 1 && $1 == --push ) ]] || die "usage: ${0##*/} [--push]"
push=${1:-}
top=$(git rev-parse --show-toplevel) || die "run it from a checkout of the repository"
cd "$top"
list=tools/lint/excluded-from-main.txt
git fetch -q origin main "$work" || die "could not fetch origin - check the network and the login, then run this again"
git cat-file -e "origin/$work:$list" 2> /dev/null || die "origin/$work has no $list - push the working branch first"
branch=release/main-sync-$(date -u +%Y%m%dT%H%M%SZ)
wt=$(mktemp -d)
cleanup() { git worktree remove --force "$wt" > /dev/null 2>&1 || true; }
trap cleanup EXIT
git worktree add -q -b "$branch" "$wt" origin/main
cd "$wt"

# the excluded paths as the working branch lists them, expanded to the files that are tracked right now
excluded() {
    git show "origin/$work:$list" | sed -e 's/#.*//' -e 's/[[:space:]]*$//' -e '/^$/d' | while IFS= read -r pattern; do
        git ls-files -- "${pattern%/\*\*}"
    done
}
drop_excluded() {
    local f n=0
    while IFS= read -r f; do git rm -q --cached -- "$f" && rm -f -- "$f" && n=$((n + 1)); done < <(excluded)
    echo "$n"
}

if git merge --no-ff --no-commit "origin/$work" > /dev/null 2>&1; then
    git commit -q -m "Merge the working branch '$work' into main"
    n=$(drop_excluded)
    if [[ $n -gt 0 ]]; then git commit -q -m "main is the reading copy: $n working-record files stay on the working branch"; fi
else
    n=$(drop_excluded)
    left=$(git diff --name-only --diff-filter=U)
    [[ -z $left ]] || die "the merge conflicts outside the excluded paths - resolve by hand, nothing was pushed:
$left"
    git commit -q -m "Merge the working branch '$work' into main; $n working-record files stay on the working branch"
fi

python3 tools/lint/docs_lint.py --main || die "the docs lint has findings on the result (above) - fix them on '$work', push, and run this again. The local branch $branch is left for inspection:  git log $branch"
echo "built $branch: $(git rev-list --count origin/main.."$branch") commits ahead of origin/main, $(git ls-files | wc -l | tr -d ' ') files, lint clean"
if [[ -n $push ]]; then
    git push -q origin "HEAD:refs/heads/$branch" || die "the push of $branch was refused - nothing else changed"
    echo "pushed $branch. Review it, then land it by fast-forward:  git push origin $branch:main"
else
    echo "not pushed. To review:  git log --stat origin/main..$branch   To push the branch:  ${0##*/} --push"
fi
