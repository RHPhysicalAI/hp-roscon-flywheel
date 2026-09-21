#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# One-time bring-up tool, not a demo-time step. Builds the evaluation dashboard image (src/eval-dashboard)
# with the hub's governed runtime-image Tekton pipeline - clone, buildah, push, cosign sign + verify into the
# hub's Rekor - rather than by hand, follows the run, and prints the digest that
# gitops/flywheel/eval-dashboard.yaml and eval-dashboard-show.yaml pin. KUBECONFIG must be set; needs oc, jq and git.
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
# The body is shared with the other image build scripts: tools/hub/lib-build-image.sh.
set -euo pipefail
MANIFEST=gitops/flywheel/eval-dashboard.yaml
SHOW_MANIFEST=gitops/flywheel/eval-dashboard-show.yaml
# shellcheck source=/dev/null
. "$(dirname "$0")/lib-build-image.sh"

pin_hint() {
    local image=$1 root=$2 lines manifest
    echo "Pin it by hand, one image, three Deployments: $MANIFEST has two image lines (eval-dashboard,"
    echo "eval-dashboard-live) and $SHOW_MANIFEST has one (eval-dashboard-show). Make all three read exactly"
    echo "    image: $image"
    for manifest in "$MANIFEST" "$SHOW_MANIFEST"; do
        [[ -f $root/$manifest ]] || continue
        lines=$(grep -n "image: ${image%%@*}@" "$root/$manifest" | cut -d: -f1 | paste -sd' ' -) || true
        echo "In this checkout, $manifest: lines ${lines:-none found - check the file}"
    done
    echo "Then commit and push; Argo CD rolls the Deployments. Never commit a file with IMAGE_DIGEST_PLACEHOLDER in it."
}

build_image --src src/eval-dashboard --tag-prefix eval-dashboard- --run-prefix eval-dashboard-image- -- "$@"
