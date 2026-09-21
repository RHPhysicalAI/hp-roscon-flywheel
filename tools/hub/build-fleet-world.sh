#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# One-time bring-up tool, not a demo-time step. Builds the sim image (docker/Dockerfile) for the fleet's worlds -
# the physics-only sims that run as pods on the hub (D163; gitops/fleet-worlds) - with the hub's governed
# runtime-image Tekton pipeline - clone, buildah, push, cosign sign + verify into the hub's Rekor - rather than
# by hand, follows the run, and prints the digest that gitops/fleet-worlds/world.yaml pins. KUBECONFIG must be
# set; needs oc, jq and git.
#
#   tools/hub/build-fleet-world.sh                  start a build of the current commit and follow it
#   tools/hub/build-fleet-world.sh --follow <run>   pick up a run started earlier (the run outlives this script)
#
# The pipeline clones from the remote, so the script refuses when the branch has unpushed commits or the image's
# sources have uncommitted changes: the image would not be what the checkout shows. Those sources are docker/
# (the Dockerfile and its entrypoint) and the four src/ folders the Dockerfile copies from; the build context is
# the repository root for the same reason. It is the same image the flywheel's sim runs from: SIM_CAMERAS=off in
# the entrypoint is what makes a world physics-only, and nothing in the image changes when it is unset.
# The image goes to the repository the hub's push credentials already cover and is told apart from the other
# images by its tag: quay.io/jary/soarm-flywheel:fleet-world-<git short sha>. arm64 only - the hub's own
# architecture, so the build is native (about 6 minutes: a colcon build of the ROS workspace) and needs no qemu
# registration. The build clones the upstream ROS repositories and installs packages, so it needs internet
# access; a running world does not.
# Override for one run:  GIT_URL=...  (a working remote other than the pipeline's default)
# The body is shared with the other image build scripts: tools/hub/lib-build-image.sh.
set -euo pipefail
MANIFEST=gitops/fleet-worlds/world.yaml
# shellcheck source=/dev/null
. "$(dirname "$0")/lib-build-image.sh"

pin_hint() {
    local image=$1 root=$2 lines
    echo "Pin it by hand in $MANIFEST: the one image line of StatefulSet world. Make it read exactly"
    echo "    image: $image"
    if [[ -f $root/$MANIFEST ]]; then
        lines=$(grep -n "image: ${image%%@*}@" "$root/$MANIFEST" | cut -d: -f1 | paste -sd' ' -) || true
        echo "In this checkout it is on line: ${lines:-none found - check the file}"
    fi
    echo "Then commit and push; once argocd/fleet-worlds-app.yaml is applied, Argo CD rolls the worlds."
    echo "Never commit the file with FLEET_WORLD_IMAGE_DIGEST_PLACEHOLDER in it."
}

# 30Gi: the image is about 5 GB unpacked, and buildah keeps its layers and the pushed blobs on the same claim.
build_image --src docker --context . --storage 30Gi \
    --watch src/fleet-world --watch src/sim-reset --watch src/episode-emitter --watch src/camera-bridge \
    --tag-prefix fleet-world- --run-prefix fleet-world-image- -- "$@"
