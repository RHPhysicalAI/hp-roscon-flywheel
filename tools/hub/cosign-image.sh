#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Sign an image that already exists in a registry with THIS hub's key, into this hub's Rekor, through the
# cluster's own cosign-sign Task - the key never leaves the cluster. One-time bring-up, not a demo-time step:
# used for images that were not built by this hub's pipelines (a modelcar packaged earlier), so that a device
# enrolled here can verify them. The image gets a second signature; its digest does not change.
#
#   tools/hub/cosign-image.sh <repository>@sha256:<digest>          sign, then verify, print the Rekor index
#   tools/hub/cosign-image.sh --follow <taskrun>                    pick a started run up again
#
# KUBECONFIG must be set.
set -euo pipefail
NS=flywheel
die() { echo "${0##*/}: $*" >&2; exit 1; }
for t in oc jq; do command -v "$t" > /dev/null || die "$t is not installed"; done
[[ -n ${KUBECONFIG:-} ]] || die "KUBECONFIG is not set"

if [[ ${1:-} == --follow ]]; then
    run=${2:?taskrun name}
else
    image=${1:?image as repository@sha256:digest}
    [[ $image =~ ^[a-z0-9./_-]+@sha256:[0-9a-f]{64}$ ]] || die "give the image by digest: <repository>@sha256:<64 hex>"
    oc -n "$NS" get task.tekton.dev cosign-sign -o name > /dev/null || die "no cosign-sign task in $NS (argocd/tekton-app.yaml)"
    run=$(jq -n --arg image "$image" --arg ns "$NS" '{
            apiVersion: "tekton.dev/v1", kind: "TaskRun",
            metadata: {generateName: "cosign-image-", namespace: $ns},
            spec: {serviceAccountName: "pipeline", taskRef: {name: "cosign-sign"}, timeout: "20m",
                   params: [{name: "IMAGE", value: $image}],
                   workspaces: [
                     {name: "cosign-key", secret: {secretName: "cosign-signing"}},
                     {name: "rekor-public-key", configMap: {name: "rekor-public-key"}},
                     {name: "dockerconfig", secret: {secretName: "quay-push", items: [{key: ".dockerconfigjson", path: "config.json"}]}}]}}' |
          oc create -f - -o name)
    run=${run##*/}
    echo "started $run for $image"
fi

state() { oc -n "$NS" get taskrun.tekton.dev "$run" -o json | jq -r '(.status.conditions // [])[] | select(.type == "Succeeded") | "\(.status) \(.reason)"'; }
for _ in $(seq 1 120); do
    s=$(state || true)
    case $s in
        True*)  break ;;
        False*) pod=$(oc -n "$NS" get taskrun.tekton.dev "$run" -o jsonpath='{.status.podName}')
                [[ -z $pod ]] || oc -n "$NS" logs "$pod" --all-containers --tail=40 >&2 || true
                die "$run failed: $s" ;;
    esac
    sleep 5
done
[[ ${s:-} == True* ]] || die "$run has not finished after 10 minutes: tools/hub/cosign-image.sh --follow $run"

oc -n "$NS" get taskrun.tekton.dev "$run" -o json |
    jq -r '"signed and verified: \(.spec.params[] | select(.name == "IMAGE") | .value)\n" +
           ((.status.results // []) | map("    \(.name)  \(.value)") | join("\n"))'
