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
# The body is shared with the other image build scripts: tools/hub/lib-build-image.sh.
set -euo pipefail
MANIFEST=gitops/flywheel/eval-dashboard.yaml
# shellcheck source=/dev/null
. "$(dirname "$0")/lib-build-image.sh"

pin_hint() {
    local image=$1 root=$2 lines
    echo "Pin it by hand in $MANIFEST: the file has two image lines, one in Deployment eval-dashboard and one in"
    echo "Deployment eval-dashboard-live. Make both read exactly"
    echo "    image: $image"
    if [[ -f $root/$MANIFEST ]]; then
        lines=$(grep -n "image: ${image%%@*}@" "$root/$MANIFEST" | cut -d: -f1 | paste -sd' ' -) || true
        echo "In this checkout they are on lines: ${lines:-none found - check the file}"
    fi
    echo "Then commit and push; Argo CD rolls both Deployments. Never commit the file with IMAGE_DIGEST_PLACEHOLDER in it."
}

build_image --src src/eval-dashboard --tag-prefix eval-dashboard- --run-prefix eval-dashboard-image- -- "$@"
