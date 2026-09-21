#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# One-time bring-up tool, not a demo-time step. Builds the developer workspace image (src/dev-workspace: the
# Dev Spaces workspace with the terminal coding agent baked in) with the hub's governed runtime-image Tekton
# pipeline - clone, buildah, push, cosign sign + verify into the hub's Rekor - rather than by hand, follows
# the run, and prints the digest that devfile.yaml pins. KUBECONFIG must be set; needs oc, jq and git.
#
#   tools/hub/build-dev-workspace.sh                  start a build of the current commit and follow it
#   tools/hub/build-dev-workspace.sh --follow <run>   pick up a run started earlier (the run outlives this script)
#
# The pipeline clones from the remote, so the script refuses when the branch has unpushed commits or the
# image's sources have uncommitted changes: the image would not be what the checkout shows. Those sources are
# src/dev-workspace and the evaluation dashboard's two requirement files, which the image pre-installs; that
# is also why the build context is src/ and not src/dev-workspace.
# The image goes to the repository the hub's push credentials already cover and is told apart from the other
# images by its tag: quay.io/jary/soarm-flywheel:dev-workspace-<git short sha>. arm64 only - the hub's own
# architecture, so the build is native and needs no qemu registration. The build downloads the agent's release
# binary and the Python packages, so it needs internet access; a running workspace does not.
# Override for one run:  GIT_URL=...  (a working remote other than the pipeline's default)
# The body is shared with the other image build scripts: tools/hub/lib-build-image.sh.
set -euo pipefail
DEVFILE=devfile.yaml
# shellcheck source=/dev/null
. "$(dirname "$0")/lib-build-image.sh"

pin_hint() {
    local image=$1 root=$2 lines
    echo "Pin it by hand in $DEVFILE: the one image line of component tools. Make it read exactly"
    echo "    image: $image"
    if [[ -f $root/$DEVFILE ]]; then
        lines=$(grep -n "image: ${image%%@*}@" "$root/$DEVFILE" | cut -d: -f1 | paste -sd' ' -) || true
        echo "In this checkout it is on line: ${lines:-none found - check the file}"
    fi
    echo "Then commit and push, and bring the branch the workspace clones up to date with it: a workspace reads the"
    echo "devfile when it is created, so one that already exists has to be recreated to pick up a new image."
    echo "Never commit the file with WORKSPACE_IMAGE_DIGEST_PLACEHOLDER in it."
}

build_image --src src/dev-workspace --context src \
    --watch src/eval-dashboard/requirements.txt --watch src/eval-dashboard/requirements-dev.txt \
    --tag-prefix dev-workspace- --run-prefix dev-workspace-image- -- "$@"
