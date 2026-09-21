#!/bin/bash
# The real scene - upstream's MuJoCo model of the arm, table, tray and cubes - drawn by the MuJoCo-Warp
# ray tracer on one CUDA device: no OpenGL anywhere, so it can live in a MIG slice. Reports frame pairs
# per second for the two 640x480 cameras (with and without shadows) and leaves one PNG per camera in
# mjwarp-spike/out so the look can be compared with Gazebo's frames.
#
#   ./24-mjwarp-spike.sh                       MIG slice 0:3
#   ./24-mjwarp-spike.sh nvidia.com/gpu=all    whole GPU (MIG off)
#
# Throwaway container from the sim image (it has the upstream workspace and Gazebo's python bindings,
# which the SDF -> MJCF conversion needs); pip installs stay inside it.
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

dev=${1:-nvidia.com/gpu=0:3}
spike=$here/mjwarp-spike
[[ -f $spike/render_bench.py ]] || { echo "no mjwarp-spike directory next to this script" >&2; exit 1; }
mkdir -p "$spike/out"

date -u
nvidia-smi -L | sed 's/ (UUID.*//'
podman run --rm --device "$dev" -v "$spike":/spike:z --entrypoint bash localhost/soarm-sim:arm64 -c '
    source /opt/ros/$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash; cd /spike
    python3 -c "import sdformat, gz.math" && echo "gazebo python bindings: ok" || echo "gazebo python bindings: MISSING"
    pip install --no-cache-dir --break-system-packages -r requirements.txt 2>&1 | grep -v "Running pip as" | tail -3
    python3 build_mjcf.py /tmp/mjw/scene.xml || exit 1
    python3 render_bench.py /tmp/mjw/scene.xml --out /spike/out || exit 1
    echo "---- again without shadows"
    python3 render_bench.py /tmp/mjw/scene.xml --out /spike/out-noshadow --no-shadows --frames 150 | tail -12
'
ls -l "$spike/out" "$spike/out-noshadow" 2>/dev/null
date -u
