# shellcheck shell=bash
# This project was developed with assistance from AI tools.
#
# Shared by reset-promotion.sh and reopen-promotion.sh. Sourced, not run; the sourcing script defines die and BRANCH.
#
# The rule both scripts keep: every Fleet file pins the same model (modelcar digest + MODEL_VERSION), and the host
# Fleet's file is the reference. The pipeline's promotion PR edits all of them in one commit (D166); a merge from
# before that knew only the host's file, so reverting or re-proposing it needs the other files brought along.
HOST_FLEET_FILE=gitops/rhem/fleet-act-inference.yaml
OTHER_FLEET_FILES="gitops/rhem/fleet-robots.yaml"   # space separated
HOST_FLEET=act-inference
ROBOTS_FLEET=robots

find_flightctl() {  # sets fc
    fc=${FLIGHTCTL:-$(command -v flightctl || echo "$HOME/.local/bin/flightctl")}
    [[ -x $fc ]] || die "no flightctl at $fc - it is how this script sees whether a rollout is in progress. Install it, or point FLIGHTCTL at the binary, and run this again"
}

# Dies while a device of either Fleet is Updating or OutOfDate: a revert or a merge on top of a running rollout
# leaves the two Fleets at different points of two rollouts.
require_no_rollout() {
    local devs busy
    devs=$("$fc" get devices --limit 0 -o json 2> /dev/null) ||
        die "flightctl is not logged in to the hub, so a rollout in progress cannot be ruled out. Log in (oc login as a real user, then: flightctl login <api url> --token \"\$(oc whoami -t)\") and run this again"
    busy=$(jq -r --arg h "Fleet/$HOST_FLEET" --arg r "Fleet/$ROBOTS_FLEET" '[.items[] | select(.metadata.owner == $h or .metadata.owner == $r)
            | select(.status.updated.status == "Updating" or .status.updated.status == "OutOfDate")
            | "\(.metadata.labels.alias // .metadata.name[0:12])=\(.status.updated.status)"] | join(" ")' <<<"$devs") ||
        die "the device list is not the JSON this script expects - look at:  $fc get devices -o json"
    [[ -z $busy ]] || die "a rollout is in progress: $busy. Wait until every device of Fleet $HOST_FLEET and Fleet $ROBOTS_FLEET is UpToDate (tools/hub/fleet-status.sh shows the robots; a robot that is shut off stays OutOfDate until its VM is started), then run this again"
}

newest_promotion_merge() {  # the newest promotion merge on origin/$BRANCH; nothing when there is none
    git log "origin/$BRANCH" --merges -1 --format=%H --grep='^Merge pull request #[0-9]* from .*/promote/'
}

promotion_reverted() {  # <merge>: true when a later commit on the branch reverts it
    local subject later
    subject=$(git log -1 --format=%s "$1" | sed 's/[][\.*^$/]/\\&/g')
    later=$(git log "origin/$BRANCH" --format=%s "$1..origin/$BRANCH")
    grep -q "^Revert \"$subject" <<<"$later"
}

# <file>: sets pin_digest and pin_version; dies unless the file holds exactly one of each (the same two sites the
# pipeline's promotion PR edits).
fleet_pin() {
    pin_digest=$(grep -Eo 'soarm-act-modelcar@sha256:[0-9a-f]{64}' "$1" || true)
    pin_digest=${pin_digest#*@}
    pin_version=$(sed -En 's/^[[:space:]]+MODEL_VERSION:[[:space:]]*([^[:space:]]+).*/\1/p' "$1")
    [[ $pin_digest =~ ^sha256:[0-9a-f]{64}$ && $pin_version =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] ||
        die "${1##*/} must hold exactly one soarm-act-modelcar@sha256:... and one MODEL_VERSION - found '$pin_digest' and '$pin_version'. Repair the file on $BRANCH by hand and run this again"
}

# <worktree>: brings every other Fleet file to what the host Fleet's file pins there and stages it. Sets ref_digest
# and ref_version (the host Fleet's pin) and aligned (the files it changed, empty when they already agreed).
align_fleets() {
    local wt=$1 f
    aligned=""
    fleet_pin "$wt/$HOST_FLEET_FILE"; ref_digest=$pin_digest; ref_version=$pin_version
    for f in $OTHER_FLEET_FILES; do
        [[ -f $wt/$f ]] || continue
        fleet_pin "$wt/$f"
        [[ $pin_digest != "$ref_digest" || $pin_version != "$ref_version" ]] || continue
        sed -E -e "s|(soarm-act-modelcar)@sha256:[0-9a-f]{64}|\1@$ref_digest|" \
               -e "s|^([[:space:]]+MODEL_VERSION:[[:space:]]*)[^[:space:]]+|\1$ref_version|" "$wt/$f" > "$wt/$f.aligned"
        mv "$wt/$f.aligned" "$wt/$f"
        fleet_pin "$wt/$f"
        [[ $pin_digest == "$ref_digest" && $pin_version == "$ref_version" ]] || die "could not bring $f to $ref_version - nothing was pushed; edit it by hand"
        git -C "$wt" add "$f"
        aligned="$aligned${aligned:+ }$f"
    done
}
