#!/bin/bash
# First CUDA on this box from the flywheel's own runtime image: arm64 torch, on one MIG slice,
# on the 64k-page kernel, with the container still confined by SELinux. If this trips on page
# size the kernel question gets settled now, before anything is built on top of it.
#
#   ./10-cuda.sh [slice]        # default 0:1, the slice policy serving will use
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

# the digests the Fleet pins (gitops/rhem/fleet-act-inference.yaml)
img=quay.io/jary/soarm-flywheel@sha256:02e66d895ed4ba328aa43263561027c18406d774887f465ab7acbd81e4c42d08
car=quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b
slice=${1:-0:1}
die() { echo "${0##*/}: $*" >&2; exit 1; }

py='
import resource, time, torch
print("torch", torch.__version__, "| cuda", torch.version.cuda, "| page", resource.getpagesize())
assert torch.cuda.is_available(), "cuda is not available"
print(torch.cuda.get_device_name(0), round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1), "GiB")
x = torch.randn(8192, 8192, device="cuda")
torch.cuda.synchronize(); t = time.time()
for _ in range(20):
    y = x @ x
torch.cuda.synchronize()
print("matmul ok:", round(time.time() - t, 3), "s")
p = torch.empty(1 << 28, dtype=torch.uint8).pin_memory()      # pinned host memory is where 64k pages bite
g = p.to("cuda", non_blocking=True); torch.cuda.synchronize()
print("pinned copy ok:", g.numel() >> 20, "MiB")
'
cuda() { podman run --rm "$@" --device "nvidia.com/gpu=$slice" --entrypoint python3 "$img" -c "$py"; }

date -u; uname -r; getconf PAGESIZE
time podman pull -q "$img"
podman image inspect "$img" --format 'image: {{.Os}}/{{.Architecture}}  {{.Size}} bytes'

if ! cuda; then
    echo "-- confined run failed; once more with labels off, to tell selinux from cuda"
    cuda --security-opt label=disable || die "fails unconfined too: driver, cuda or page size - not selinux"
    die "works only with labels off: selinux. look at: ausearch -m avc -ts recent"
fi
getsebool container_use_devices

# the model reaches the container as an image volume, the way the Fleet mounts it
podman pull -q "$car"
podman run --rm --mount "type=image,source=$car,destination=/modelcar" --entrypoint ls "$img" -la /modelcar/models/act
df -h /data | tail -1
