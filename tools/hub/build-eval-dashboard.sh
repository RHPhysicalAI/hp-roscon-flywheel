#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# One-time bring-up tool, not a demo-time step. Builds the evaluation dashboard image (src/eval-dashboard)
# with the hub's governed runtime-image Tekton pipeline - clone, buildah, push, cosign sign + verify into the
# hub's Rekor - rather than by hand, follows the run, and prints the digest that
# gitops/flywheel/eval-dashboard.yaml pins. KUBECONFIG must be set; needs oc, jq and git.
#
#   tools/hub/build-eval-dashboard.sh                  start a build of the current commit and follow it
#   tools/hub/build-eval-dashboard.sh --follow <run>   pick up a run started earlier (the run outlives this script)
#
# The pipeline clones from the remote, so the script refuses when the branch has unpushed commits or
# src/eval-dashboard has uncommitted changes: the image would not be what the checkout shows.
# The image goes to the repository the hub's push credentials already cover and is told apart from the runtime
# image by its tag: quay.io/jary/soarm-flywheel:eval-dashboard-<git short sha>. arm64 only - the hub's own
# architecture, so the build is native and needs no qemu registration.
# Override for one run:  GIT_URL=...  (a working remote other than the pipeline's default)
set -euo pipefail
NS=flywheel
PIPELINE=runtime-image
IMAGE_REPO=quay.io/jary/soarm-flywheel
TAG_PREFIX=eval-dashboard-
SRC=src/eval-dashboard
MANIFEST=gitops/flywheel/eval-dashboard.yaml
DEADLINE_MIN=75   # the run's own timeout is 1h; this only ends the wait if the controller never reports
die() { echo "${0##*/}: $*" >&2; exit 1; }
[[ -n ${KUBECONFIG:-} ]] || die "KUBECONFIG must be set"
for t in oc jq git; do command -v "$t" > /dev/null || die "$t is not installed"; done
here=$(cd "$(dirname "$0")" && pwd)
root=$(git -C "$here" rev-parse --show-toplevel) || die "$here is not inside a git checkout"

start_run() {
    local branch remote ahead rev created
    branch=$(git -C "$root" symbolic-ref --short -q HEAD) || die "detached HEAD: check out the branch the hub follows"
    remote=$(git -C "$root" config "branch.$branch.remote") || die "branch $branch has no upstream, and the pipeline clones from the remote"
    git -C "$root" fetch --quiet "$remote" || die "could not fetch $remote to compare with it"
    ahead=$(git -C "$root" rev-list --count '@{upstream}..HEAD')
    [[ $ahead -eq 0 ]] || die "$ahead unpushed commit(s) on $branch: the pipeline clones from the remote. Push, then run this again"
    [[ -z $(git -C "$root" status --porcelain -- "$SRC") ]] ||
        die "$SRC has uncommitted changes: the image is built from the pushed commit, not the working tree. Commit and push them, or stash them"
    [[ -f $root/$SRC/Dockerfile ]] || die "no $SRC/Dockerfile in this checkout"
    rev=$(git -C "$root" rev-parse HEAD)
    oc -n "$NS" get pipeline.tekton.dev "$PIPELINE" -o name > /dev/null || die "no $PIPELINE pipeline in $NS (argocd/tekton-app.yaml)"

    # Workspaces as in gitops/tekton/runtime-image-pipelinerun.example.yaml; a smaller claim and timeout,
    # sized for a Python web image rather than the runtime image.
    created=$(jq -n --arg ns "$NS" --arg pipeline "$PIPELINE" --arg rev "$rev" --arg repo "$IMAGE_REPO" --arg prefix "$TAG_PREFIX" \
          --arg src "$SRC" --arg url "${GIT_URL:-}" \
        '{apiVersion: "tekton.dev/v1", kind: "PipelineRun",
          metadata: {generateName: "eval-dashboard-image-", namespace: $ns},
          spec: {pipelineRef: {name: $pipeline},
                 taskRunTemplate: {serviceAccountName: "pipeline"},
                 timeouts: {pipeline: "1h0m0s"},
                 params: ([{name: "git-revision", value: $rev},
                           {name: "image-repo", value: $repo},
                           {name: "tag-prefix", value: $prefix},
                           {name: "dockerfile", value: ($src + "/Dockerfile")},
                           {name: "context", value: $src},
                           {name: "platforms", value: "linux/arm64"}]
                          + (if $url == "" then [] else [{name: "git-url", value: $url}] end)),
                 workspaces: [
                   {name: "shared", volumeClaimTemplate: {spec: {accessModes: ["ReadWriteOnce"], storageClassName: "local-path",
                                                                 resources: {requests: {storage: "10Gi"}}}}},
                   {name: "docker-credentials", secret: {secretName: "quay-push", items: [{key: ".dockerconfigjson", path: "config.json"}]}},
                   {name: "cosign-key", secret: {secretName: "cosign-signing"}},
                   {name: "rekor-public-key", configMap: {name: "rekor-public-key"}}]}}' |
        oc create -f - -o name)
    echo "${created##*/}"
}

# "<status> <reason>" of the run's Succeeded condition; empty until the controller has set one.
run_state() { oc -n "$NS" get pipelinerun.tekton.dev "$run" -o json | jq -r '(.status.conditions // [])[] | select(.type == "Succeeded") | "\(.status) \(.reason)"'; }
run_result() { oc -n "$NS" get pipelinerun.tekton.dev "$run" -o json | jq -r --arg k "$1" '(.status.results // [])[] | select(.name == $k) | .value'; }
task_states() {
    oc -n "$NS" get taskrun.tekton.dev -l "tekton.dev/pipelineRun=$run" -o json |
        jq -r '[.items | sort_by(.metadata.creationTimestamp)[]
                | "\(.metadata.labels["tekton.dev/pipelineTask"])=\((.status.conditions // [])[0].reason // "Pending")"] | join("  ")'
}
show_failures() {
    local tr pod
    while read -r tr pod; do
        [[ -n $tr ]] || continue
        echo "--- failed: $tr (last lines of its log)" >&2
        [[ -z $pod || $pod == null ]] || oc -n "$NS" logs "$pod" --all-containers --tail=30 >&2 || true
    done < <(oc -n "$NS" get taskrun.tekton.dev -l "tekton.dev/pipelineRun=$run" -o json |
             jq -r '.items[] | select(any(.status.conditions[]?; .type == "Succeeded" and .status == "False")) | "\(.metadata.name) \(.status.podName)"')
}

case ${1:-} in
    "") run=$(start_run); [[ -n $run ]] || die "the PipelineRun was not created"
        echo "started $run (commit $(git -C "$root" rev-parse --short HEAD)); it keeps running if this script is interrupted:"
        echo "    ${0##*/} --follow $run" ;;
    --follow) run=${2:?run name}; [[ $run =~ ^[a-z0-9][a-z0-9.-]*$ ]] || die "not a run name: $run" ;;
    *) die "usage: ${0##*/} [--follow <run>]" ;;
esac

deadline=$(( $(date +%s) + DEADLINE_MIN * 60 )); last=""
while :; do
    state=$(run_state); tasks=$(task_states)
    [[ $tasks == "$last" ]] || { echo "$(date -u +%H:%M:%SZ)  ${tasks:-waiting for the first task}"; last=$tasks; }
    case $state in
        True\ *) break ;;
        False\ *) show_failures
            pushed=$(oc -n "$NS" get taskrun.tekton.dev -l "tekton.dev/pipelineRun=$run,tekton.dev/pipelineTask=build-and-push" -o json |
                     jq -r '.items[].status.results[]? | select(.name == "IMAGE_DIGEST") | .value')
            [[ -z $pushed ]] || echo "note: $IMAGE_REPO@$pushed was pushed but is not signed and verified - do not pin it" >&2
            die "$run failed (${state#False })" ;;
    esac
    [[ $(date +%s) -lt $deadline ]] || die "$run has reported no result after $DEADLINE_MIN min; pick it up again with --follow $run"
    sleep 10
done

digest=$(run_result IMAGE_DIGEST)
[[ $digest =~ ^sha256:[0-9a-f]{64}$ ]] || die "$run succeeded but its IMAGE_DIGEST result is not a digest: '$digest'"
echo
echo "built, pushed, signed and verified:"
echo "    image        $IMAGE_REPO@$digest"
echo "    tag          $(run_result IMAGE_TAG)   commit $(run_result GIT_COMMIT)"
echo "    platforms    $(run_result PLATFORM_DIGESTS)"
echo "    Rekor index  $(run_result REKOR_INDEX)"
echo
echo "Pin it by hand in $MANIFEST: the file has two image lines, one in Deployment eval-dashboard and one in"
echo "Deployment eval-dashboard-live. Make both read exactly"
echo "    image: $IMAGE_REPO@$digest"
if [[ -f $root/$MANIFEST ]]; then
    lines=$(grep -n "image: $IMAGE_REPO@" "$root/$MANIFEST" | cut -d: -f1 | paste -sd' ' -) || true
    echo "In this checkout they are on lines: ${lines:-none found - check the file}"
fi
echo "Then commit and push; Argo CD rolls both Deployments. Never commit the file with IMAGE_DIGEST_PLACEHOLDER in it."
echo "The finished run keeps its 10 Gi claim until it is deleted: oc -n $NS delete pipelinerun $run"
