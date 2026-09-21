#!/bin/bash
# How many robots can one MIG slice render: the MuJoCo-Warp BATCH renderer on the real scene, N worlds per call
# (every world its own arm motion and cube placement, two 640x480 cameras each, RGB copied back to the host),
# for N = 1 4 8 16 32. Prints, per N: camera pairs per second in total, robots that is at 30 fps, per-stage
# milliseconds, and the slice's peak memory as the host sees it. About five minutes; one measurement, run once.
#
#   ./25-mjwarp-batch.sh                     MIG slice 0:3, N = 1 4 8 16 32
#   ./25-mjwarp-batch.sh 0:2 "1 8 24"        another slice, other batch sizes
#
# Runs as root for one reason: on this host a rootless, SELinux-confined container cannot open the GPU's device
# nodes (they keep their host label when rootless podman binds them), and the labels stay on. Same throwaway
# container as 24-mjwarp-spike.sh: the sim image, pip installs inside it, nothing left behind but the log.
#
# This project was developed with assistance from AI tools.
set -uo pipefail
slice=${1:-0:3}
worlds=${2:-1 4 8 16 32}
[[ $slice =~ ^0:[0-3]$ ]] || { echo "usage: ${0##*/} [0:N] [\"batch sizes\"]   - a MIG slice of GPU 0, e.g. 0:3" >&2; exit 1; }
[[ $worlds =~ ^[0-9\ ]+$ ]] || { echo "batch sizes are numbers separated by spaces" >&2; exit 1; }
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1
# Under sudo the terminal can go away before tee has written the last lines - wait for it on the way out.
trap 'podman rm -f cudarig-batch >/dev/null 2>&1; exec >&- 2>&-; wait' EXIT

die() { echo "${0##*/}: $*" >&2; exit 1; }
spike=$here/mjwarp-spike
for f in bench_batch.py build_mjcf.py requirements.txt; do [[ -f $spike/$f ]] || die "missing mjwarp-spike/$f next to this script"; done
[[ $(nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader) == Enabled ]] || die "MIG is off - this measures a slice: fury-mode tenants first"
nvidia-ctk cdi list 2>/dev/null | tr -d '[:blank:]' | grep -qxF "nvidia.com/gpu=$slice" || die "no CDI device nvidia.com/gpu=$slice"
podman image exists localhost/soarm-sim:arm64 || die "the sim image is not in root's storage (11-sim-build.sh)"
dev=${slice#0:}
# memory of one MIG device as nvidia-smi's table prints it: "|  <gpu>  <gi>  <ci>  <mig dev>  |  <used>MiB / <total>MiB"
slice_mib() { nvidia-smi | awk -v dev="$dev" '$2 == 0 && $5 == dev && $7 ~ /MiB/ { sub("MiB", "", $7); print $7; exit }'; }
used=$(slice_mib); [[ ${used:-0} -lt 1000 ]] || die "slice $slice already holds ${used} MiB - something is running there; pick another slice or stop it"

date -u
nvidia-smi -L | sed 's/ (UUID.*//'
podman run -d --name cudarig-batch --pull=never --device "nvidia.com/gpu=$slice" -v "$spike":/spike:ro,z \
    --entrypoint bash localhost/soarm-sim:arm64 -c 'sleep infinity' >/dev/null || die "the container did not start"
podman exec cudarig-batch bash -c '
    source /opt/ros/$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash; cd /spike
    pip install --no-cache-dir --break-system-packages -r requirements.txt 2>&1 | grep -v "Running pip as" | tail -2
    mkdir -p /tmp/mjw && python3 build_mjcf.py /tmp/mjw/scene.xml >/dev/null || exit 1
    python3 -c "import warp as wp; wp.init(); assert wp.is_cuda_available(), \"no CUDA device in the container\""' ||
    die "set-up inside the container failed (lines above)"

echo "slice $slice before: $(slice_mib) MiB"
for n in $worlds; do
    echo "== $n worlds"
    podman exec cudarig-batch bash -c "source /opt/ros/\$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash
        python3 -u /spike/bench_batch.py /tmp/mjw/scene.xml --nworld $n 2>&1 | grep -E 'RESULT|rror|Traceback|out of memory'" &
    job=$!; peak=0
    while kill -0 "$job" 2>/dev/null; do
        now=$(slice_mib); (( ${now:-0} > peak )) && peak=$now
        sleep 2
    done
    wait "$job" || echo "   (that batch size did not finish cleanly)"
    echo "   slice peak memory: $peak MiB"
done
echo "slice $slice after: $(slice_mib) MiB"
date -u
echo "done - the container is removed on the way out"
