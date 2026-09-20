# shellcheck shell=bash
# This project was developed with assistance from AI tools.
#
# Shared body of the hub's image build scripts (build-eval-dashboard.sh, build-dev-workspace.sh). Sourced, not
# run. Starts a PipelineRun of the governed runtime-image Tekton pipeline - clone, buildah, push, cosign sign +
# verify into the hub's Rekor - for one Dockerfile, follows it, and prints the digest.
#
# The sourcing script defines pin_hint and calls
#   build_image --src <dir> --tag-prefix <p> --run-prefix <p> [--context <dir>] [--watch <path>]... -- "$@"
#     --src          directory of the Dockerfile, relative to the repository root
#     --tag-prefix   tells this image's tags apart in the one repository the push credentials cover
#     --run-prefix   generateName of the PipelineRun
#     --context      build context when it is not the --src directory
#     --watch        a further path the image is built from, checked for uncommitted changes like --src
#   pin_hint <repository@digest> <checkout root>   prints where the digest goes
# arm64 only - the hub's own architecture, so the build is native and needs no qemu registration.
NS=flywheel
PIPELINE=runtime-image
IMAGE_REPO=quay.io/jary/soarm-flywheel
DEADLINE_MIN=75   # the run's own timeout is 1h; this only ends the wait if the controller never reports
die() { echo "${0##*/}: $*" >&2; exit 1; }

start_run() {
    local branch remote ahead rev created dirty
    branch=$(git -C "$root" symbolic-ref --short -q HEAD) || die "detached HEAD: check out the branch the hub follows"
    remote=$(git -C "$root" config "branch.$branch.remote") || die "branch $branch has no upstream, and the pipeline clones from the remote"
    git -C "$root" fetch --quiet "$remote" || die "could not fetch $remote to compare with it"
    ahead=$(git -C "$root" rev-list --count '@{upstream}..HEAD')
    [[ $ahead -eq 0 ]] || die "$ahead unpushed commit(s) on $branch: the pipeline clones from the remote. Push, then run this again"
    for dirty in "$SRC" ${WATCH[@]+"${WATCH[@]}"}; do
        [[ -z $(git -C "$root" status --porcelain -- "$dirty") ]] ||
            die "$dirty has uncommitted changes: the image is built from the pushed commit, not the working tree. Commit and push them, or stash them"
    done
    [[ -f $root/$SRC/Dockerfile ]] || die "no $SRC/Dockerfile in this checkout"
    rev=$(git -C "$root" rev-parse HEAD)
    oc -n "$NS" get pipeline.tekton.dev "$PIPELINE" -o name > /dev/null || die "no $PIPELINE pipeline in $NS (argocd/tekton-app.yaml)"

    # Workspaces as in gitops/tekton/runtime-image-pipelinerun.example.yaml; a smaller claim and timeout,
    # sized for an image on the Python base rather than the runtime image.
    created=$(jq -n --arg ns "$NS" --arg pipeline "$PIPELINE" --arg rev "$rev" --arg repo "$IMAGE_REPO" --arg prefix "$TAG_PREFIX" \
          --arg src "$SRC" --arg context "${CONTEXT:-$SRC}" --arg run "$RUN_PREFIX" --arg url "${GIT_URL:-}" \
        '{apiVersion: "tekton.dev/v1", kind: "PipelineRun",
          metadata: {generateName: $run, namespace: $ns},
          spec: {pipelineRef: {name: $pipeline},
                 taskRunTemplate: {serviceAccountName: "pipeline"},
                 timeouts: {pipeline: "1h0m0s"},
                 params: ([{name: "git-revision", value: $rev},
                           {name: "image-repo", value: $repo},
                           {name: "tag-prefix", value: $prefix},
                           {name: "dockerfile", value: ($src + "/Dockerfile")},
                           {name: "context", value: $context},
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

build_image() {
    local here deadline last state tasks pushed
    SRC="" CONTEXT="" TAG_PREFIX="" RUN_PREFIX="" WATCH=()
    while [[ $# -gt 0 ]]; do
        case $1 in
            --src) SRC=$2; shift 2 ;;
            --context) CONTEXT=$2; shift 2 ;;
            --watch) WATCH+=("$2"); shift 2 ;;
            --tag-prefix) TAG_PREFIX=$2; shift 2 ;;
            --run-prefix) RUN_PREFIX=$2; shift 2 ;;
            --) shift; break ;;
            *) die "build_image: unknown option $1" ;;
        esac
    done
    [[ -n $SRC && -n $TAG_PREFIX && -n $RUN_PREFIX ]] || die "build_image: --src, --tag-prefix and --run-prefix are required"
    [[ -n ${KUBECONFIG:-} ]] || die "KUBECONFIG must be set"
    for t in oc jq git; do command -v "$t" > /dev/null || die "$t is not installed"; done
    here=$(cd "$(dirname "$0")" && pwd)
    root=$(git -C "$here" rev-parse --show-toplevel) || die "$here is not inside a git checkout"

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
    pin_hint "$IMAGE_REPO@$digest" "$root"
    echo "The finished run keeps its 10 Gi claim until it is deleted: oc -n $NS delete pipelinerun $run"
}
