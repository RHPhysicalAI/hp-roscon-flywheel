#!/bin/bash
# First real inference on this machine: the policy from the Fleet's pinned image on slice 0:1,
# model mounted the way the Fleet mounts it, driven by a short seeded eval against the running sim.
# Policy and coordinator are separate containers - the shape the managed device will have - and the
# policy stays SELinux-confined.
#
#   ./13-first-inference.sh [episodes] [cdi-device] [label] [image]
#                                          defaults: 5, nvidia.com/gpu=0:1, eval-fury-smoke, the image below; the sim must be up
#   ./13-first-inference.sh down           stop and remove the policy container
#
# Evidence that the policy really drove the arm is cubes_placed > 0 with goals accepted - a healthy
# container alone proves nothing, the healthcheck can't see a wedged action server.
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

img=quay.io/jary/soarm-flywheel@sha256:5eba6ca4ee8acf7be87ec8da852d314d6dd16d76cfbce09a1581dbf8c5c94837
car=quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b
name=act-inference
data=/data/flywheel
src=${SRC:-$(getent passwd "${SUDO_USER:?run this through sudo, or set SRC}" | cut -d: -f6)/hp-roscon-flywheel}
die() { echo "${0##*/}: $*" >&2; exit 1; }

if [[ ${1:-} == down ]]; then
    podman stop -t 10 "$name"; podman rm "$name"
    exit
fi
n=${1:-5}
dev=${2:-nvidia.com/gpu=0:1}
label=${3:-eval-fury-smoke}
img=${4:-$img}

podman container exists so-arm-sim      || die "the sim is not running - ./12-sim-smoke.sh first"
[[ -x $src/tools/host/run-coordinator.sh ]] || die "no checkout at $src"
mkdir -p "$data/bags" "$data/eval"

date -u
# only a running container of this very image is reused: a dead one from an earlier run, or one serving
# another image, would otherwise be waited on in place of the image under test
if podman container exists "$name"; then
    cur=$(podman inspect --format '{{.State.Status}} {{.ImageName}}' "$name")
    if [[ $cur != "running $img" ]]; then
        echo "## removing a leftover $name container: $cur"
        podman rm -f -t 10 "$name" >/dev/null
    fi
fi
if ! podman container exists "$name"; then
    podman run -d --name "$name" --network host --device "$dev" \
        -e ROLE=policy -e POLICY_DEVICE=cuda -e MODEL_VERSION=act-v2-ft160 \
        -e POLICY_PATH=/modelcar/models/act -e ZENOH_ROUTER=127.0.0.1:7447 \
        -e ACTIONS_PER_CHUNK=100 -e CHUNK_SIZE_THRESHOLD=0.5 \
        --mount "type=image,source=$car,destination=/modelcar" "$img" || die "policy container did not start"
fi

# The image's healthcheck hangs if it runs before the version is published (its ros2 call ignores the
# TERM from `timeout`), so wait for the publish line first and only then ask.
echo "## waiting for the policy to publish its model version (up to 5 min)"
for i in $(seq 1 100); do
    podman logs "$name" 2>&1 | grep -q 'Published model_version' && break
    if [[ $(podman inspect --format '{{.State.Status}}' "$name" 2>/dev/null) != running ]]; then
        podman logs --tail 15 "$name" 2>&1
        die "the policy container is not running any more - its last lines are above"
    fi
    sleep 3
done
echo "## health"
ok=no
for i in $(seq 1 12); do
    if timeout -k 2 30 podman exec "$name" /healthcheck.sh; then ok=yes; break; fi
    podman container exists "$name" || break
    sleep 5
done
echo "healthy=$ok after $((i * 5))s"
echo "## policy log";   podman logs --tail 25 "$name" 2>&1
echo "## page-size trouble?"; podman logs "$name" 2>&1 | grep -i -E 'jemalloc|page size|bus error|illegal instruction|segfault' | head -5
echo "## on the slice";  nvidia-smi --query-compute-apps=pid,name,used_memory --format=csv
[[ $ok == yes ]] || die "policy never became healthy - not starting the eval"

echo "## eval: $n seeded episodes, 60 s each"
ENGINE=podman IMAGE="$img" MODE=eval MODEL_VERSION="$label" DATA_DIR="$data" \
    "$src/tools/host/run-coordinator.sh" "$n" 1000

out=$data/eval/$label.json
[[ -f $out ]] || die "no result file at $out"
echo "## result"
jq '.aggregate' "$out"
jq -c '.episodes[]? | {seed, cubes_placed, goal_accepted, served_model_version, steps}' "$out"
date -u
