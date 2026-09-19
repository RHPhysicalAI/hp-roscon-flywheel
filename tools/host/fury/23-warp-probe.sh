#!/bin/bash
# Can camera images be produced with CUDA compute alone on this box - and inside a MIG slice?
# NVIDIA Warp ray-casts a small tabletop scene (a few boxes) at 640x480 for two "cameras" and
# reports frames per second. No OpenGL, Vulkan or EGL anywhere: Warp JIT-compiles CUDA kernels.
# Nobody seems to have reported Warp rendering under MIG, so this is the gate for that whole idea.
#
#   ./23-warp-probe.sh                      on MIG slice 0:3 (MIG must be on)
#   ./23-warp-probe.sh nvidia.com/gpu=all   on the whole GPU (MIG off) - proves aarch64 / Blackwell / CUDA first
#
# Throwaway container from the runtime image; installs warp-lang from PyPI inside it, keeps nothing.
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

img=quay.io/jary/soarm-flywheel@sha256:5eba6ca4ee8acf7be87ec8da852d314d6dd16d76cfbce09a1581dbf8c5c94837
dev=${1:-nvidia.com/gpu=0:3}

py='
import time
import numpy as np
import warp as wp

wp.init()
d = wp.get_device("cuda:0")
print("device:", d.name, "| arch sm_%d" % d.arch, "| mem %.1f GiB" % (d.total_memory / 2**30))

def box(center, half):
    c, h = np.array(center, dtype=np.float32), np.array(half, dtype=np.float32)
    corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], dtype=np.float32) * h + c
    tris = [0,1,3, 0,3,2, 4,6,7, 4,7,5, 0,4,5, 0,5,1, 2,3,7, 2,7,6, 0,2,6, 0,6,4, 1,5,7, 1,7,3]
    return corners, np.array(tris, dtype=np.int32)

# table, tray, three cubes, and a stack of boxes standing in for the arm
parts = [box((0, -0.02, -0.6), (0.6, 0.02, 0.4)), box((0, 0.01, -0.35), (0.12, 0.01, 0.09)),
         box((-0.2, 0.02, -0.5), (0.02, 0.02, 0.02)), box((0.0, 0.02, -0.5), (0.02, 0.02, 0.02)),
         box((0.2, 0.02, -0.5), (0.02, 0.02, 0.02))]
parts += [box((0, 0.05 + 0.06 * i, -0.8), (0.03, 0.03, 0.03)) for i in range(6)]
verts, idx, off = [], [], 0
for v, t in parts:
    verts.append(v); idx.append(t + off); off += len(v)
mesh = wp.Mesh(points=wp.array(np.concatenate(verts), dtype=wp.vec3, device=d),
               indices=wp.array(np.concatenate(idx), dtype=wp.int32, device=d))

@wp.kernel
def render(mesh_id: wp.uint64, cam: wp.vec3, width: int, height: int, pixels: wp.array(dtype=wp.vec3)):
    tid = wp.tid()
    x = tid % width
    y = tid // width
    sx = 2.0 * float(x) / float(width) - 1.0
    sy = (1.0 - 2.0 * float(y) / float(height)) * float(height) / float(width)
    rd = wp.normalize(wp.vec3(sx, sy - 0.35, -1.0))
    q = wp.mesh_query_ray(mesh_id, cam, rd, 1.0e6)
    color = wp.vec3(0.85, 0.85, 0.85)
    if q.result:
        light = wp.normalize(wp.vec3(0.3, 1.0, 0.5))
        color = wp.vec3(0.2, 0.5, 0.5) * (0.25 + 0.75 * wp.max(wp.dot(q.normal, light), 0.0))
    pixels[tid] = color

W, H, frames = 640, 480, 300
cams = [wp.vec3(0.0, 0.45, 0.2), wp.vec3(0.0, 0.25, -0.55)]
bufs = [wp.zeros(W * H, dtype=wp.vec3, device=d) for _ in cams]
t0 = time.time()
for c, b in zip(cams, bufs):
    wp.launch(render, dim=W * H, inputs=[mesh.id, c, W, H, b], device=d)
wp.synchronize()
print("first frame incl. kernel compile: %.1f s" % (time.time() - t0))

t0 = time.time()
for _ in range(frames):
    for c, b in zip(cams, bufs):
        wp.launch(render, dim=W * H, inputs=[mesh.id, c, W, H, b], device=d)
    host = [b.numpy() for b in bufs]          # copy back, as a camera publisher would have to
wp.synchronize()
dt = time.time() - t0
lit = float((host[0].sum(axis=1) < 2.5).mean())
print("two 640x480 cameras, incl. copy to host: %.0f frame pairs/s  (needed: 30)" % (frames / dt))
print("fraction of pixels that hit geometry in camera 0: %.2f" % lit)
'

date -u
nvidia-smi -L | sed 's/(UUID.*//'
podman run --rm -i --device "$dev" --entrypoint bash "$img" -c '
    pip install --quiet --no-cache-dir --break-system-packages warp-lang 2>&1 | grep -v "Running pip as" | tail -2
    cat > /tmp/probe.py        # warp reads kernel source from the file, so it cannot come in on stdin
    python3 /tmp/probe.py
' <<<"$py"
date -u
