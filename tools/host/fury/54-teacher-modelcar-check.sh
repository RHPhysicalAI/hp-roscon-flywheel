#!/bin/bash
# Before the Fleet is pointed at the teacher's modelcar (a digest goes into the Fleet only after it has served
# on this GPU - D149): one-time bring-up, three checks.
#
#   1. the image pulls by digest as root - that is, under the device's own /etc/containers/policy.json, so the
#      pull succeeding means this hub's signature on it was found and verified
#   2. the weights inside it are byte for byte the teacher checkpoint staged under /data/flywheel
#   3. those weights serve on the GPU: three seeded episodes in the eval rig, beside the running loop
#
#   ./54-teacher-modelcar-check.sh            all three (about ten minutes, most of it the episodes)
#   ./54-teacher-modelcar-check.sh nopolicy   1 and 2 only
#
# The rig's record is written as smoke-teacher-modelcar.json, never over a staged evaluation record.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1
trap 'exec >&- 2>&-; wait' EXIT

car=quay.io/jary/soarm-act-modelcar@sha256:d5e5897ff3b917e81d2b16d026c91dc603242f31b5aa4e054d3673fb2ad308ac
ckpt=/data/flywheel/train/upstream-act-teacher/checkpoints/last/pretrained_model
die() { echo "${0##*/}: $*" >&2; exit 1; }
[[ -f $ckpt/model.safetensors ]] || die "no staged teacher checkpoint under $ckpt - ./53-stage-promotion.sh first"

date -u
echo "== 1. pull under the device's policy"
grep -q soarm-act-modelcar /etc/containers/policy.json || echo "note: policy.json does not name the modelcar repository - the pull below proves less than it should"
podman pull -q "$car" || die "the pull was refused - the signature policy did not accept this image"

echo "== 2. weights in the image against the staged checkpoint"
m=$(podman image mount "$car")
trap 'podman image unmount "$car" > /dev/null 2>&1 || true; exec >&- 2>&-; wait' EXIT
w=$(find "$m" -name model.safetensors | head -n 1)
[[ -n $w ]] || die "no model.safetensors in the image"
a=$(sha256sum "$w" | cut -d' ' -f1); b=$(sha256sum "$ckpt/model.safetensors" | cut -d' ' -f1)
echo "image   ${w#"$m"}  $a"
echo "staged  $b"
[[ $a == "$b" ]] || die "the weights differ - do not pin this digest"
echo "identical"
podman image unmount "$car" > /dev/null

[[ ${1:-} == nopolicy ]] && { echo "skipped the episodes"; exit 0; }
echo "== 3. three episodes on the GPU from those weights"
"$here/51-eval-rig.sh" run smoke-teacher-modelcar "$ckpt" 3 1000
