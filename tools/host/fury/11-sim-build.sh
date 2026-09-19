#!/bin/bash
# Build the sim image natively. It has never existed for arm64: the pipeline only builds the
# runtime image. ROS 2 + Gazebo + the upstream demos workspace through colcon, so give it a while
# and run it in tmux.
#
#   --format docker  the Dockerfile relies on SHELL ["/bin/bash","-c"]; buildah drops SHELL in oci format
#   --from           the checkout's FROM is an unqualified short name, which podman won't resolve without a tty
#
# The Dockerfile clones the upstream demos at HEAD and pip-installs unpinned, so every build is its own.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

die() { echo "${0##*/}: $*" >&2; exit 1; }
src=${SRC:-$(getent passwd "${SUDO_USER:?run this through sudo, or set SRC}" | cut -d: -f6)/hp-roscon-flywheel}
tag=localhost/soarm-sim:arm64
[[ -f $src/docker/Dockerfile ]] || die "no checkout at $src"

date -u
time podman build --format docker --from docker.io/library/ros:kilted \
    -f "$src/docker/Dockerfile" -t "$tag" "$src"
date -u

podman image inspect "$tag" --format 'image: {{.Os}}/{{.Architecture}}  {{.Size}} bytes'
# the Dockerfile strips every .git from the workspace, so the upstream commit can't be read back
# from the image: note `git ls-remote https://github.com/ros-physical-ai/demos HEAD` at build time
df -h /data | tail -1
