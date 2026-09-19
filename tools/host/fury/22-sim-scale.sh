#!/bin/bash
# How many GPU-rendered sims does this box carry before they stop being smooth? Measure it.
# MIG has to be off (graphics only exists on the unpartitioned GPU).
#
#   ./22-sim-scale.sh 1 2 4 6 8          grow to each count in turn, measure, clean up at the end
#   ./22-sim-scale.sh load 4             same, with 4 sims and a CUDA job hammering the GPU alongside
#
# Every sim gets its own network namespace (default bridge network, no host networking), so each has
# its own zenoh router and camera bridge on the usual ports, and its own GZ_PARTITION so gazebo
# discovery can't cross between them. That is also the shape a multi-robot setup would have.
#
# Per sim: camera frames actually delivered in 10 s, real-time factor. For the box: GPU utilisation, cpu.
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

img=localhost/soarm-sim:arm64
rt=quay.io/jary/soarm-flywheel@sha256:02e66d895ed4ba328aa43263561027c18406d774887f465ab7acbd81e4c42d08
die() { echo "${0##*/}: $*" >&2; exit 1; }

frames='
import sys, time, urllib.request
r = urllib.request.urlopen("http://127.0.0.1:8081/" + sys.argv[1], timeout=5)
t, n = time.time(), 0
while time.time() - t < 10:
    n += r.read(65536).count(b"Content-Type: image/jpeg")
print(n)
'

cleanup() {
    podman ps -a --format '{{.Names}}' | grep -E '^(scale-sim-[0-9]+|scale-load)$' | while read -r c; do
        podman stop -t 3 "$c" >/dev/null 2>&1; podman rm "$c" >/dev/null 2>&1
    done
}
trap cleanup EXIT

start_sim() {
    podman run -d --name "scale-sim-$1" --device nvidia.com/gpu=all -e NVIDIA_DRIVER_CAPABILITIES=all \
        -e "GZ_PARTITION=scale$1" "$img" >/dev/null || die "sim $1 did not start"
    for _ in $(seq 1 36); do
        podman exec "scale-sim-$1" python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8081/health", timeout=2)' >/dev/null 2>&1 && return
        sleep 5
    done
    echo "sim $1 never answered on its camera bridge"
}

measure() {
    local n=$1 i
    sleep 15    # let the newest one settle
    echo "---- $n sim(s) ----"
    for i in $(seq 1 "$n"); do
        st=$(podman exec "scale-sim-$i" python3 -c "$frames" static 2>/dev/null)
        wr=$(podman exec "scale-sim-$i" python3 -c "$frames" wrist 2>/dev/null)
        rtf=$(podman exec "scale-sim-$i" bash -c 'source /opt/ros/$ROS_DISTRO/setup.bash; timeout -k 2 6 gz topic -e -n 1 -t /world/pai_world/stats 2>/dev/null' | sed -n 's/^real_time_factor: //p')
        gl=$(podman exec "scale-sim-$i" bash -c 'grep -h -m1 "GL_VENDOR" /root/.gz/rendering/ogre2.log 2>/dev/null' | sed 's/.*GL_VENDOR = //')
        printf 'sim %-2s static %3s/10s  wrist %3s/10s  rtf %-6s  gl %s\n' "$i" "${st:-?}" "${wr:-?}" "${rtf:-?}" "${gl:-?}"
    done
    echo "gpu util (5 x 1s): $(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader -l 1 | head -5 | paste -sd' ')   mem $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
    podman stats --no-stream --format '{{.Name}} {{.CPUPerc}}' | grep scale- | awk '{gsub("%","",$2); s+=$2} END {printf "cpu across the test containers: %.0f%% (= %.1f cores), load %s\n", s, s/100, L}' L="$(cut -d' ' -f1 /proc/loadavg)"
}

[[ $(nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader) == Disabled ]] || die "MIG is on - ./21-render-trial.sh full first"
for c in act-inference so-arm-sim; do
    podman container exists "$c" && { podman stop -t 10 "$c" >/dev/null; podman rm "$c" >/dev/null; echo "removed $c for a clean measurement"; }
done
cleanup
date -u

if [[ ${1:-} == load ]]; then
    n=${2:-4}
    for i in $(seq 1 "$n"); do start_sim "$i"; done
    measure "$n"
    echo "## now with a CUDA job saturating the GPU next to them"
    podman run -d --name scale-load --device nvidia.com/gpu=all --entrypoint python3 "$rt" -c '
import torch
x = torch.randn(16384, 16384, device="cuda")
while True:
    y = x @ x
    torch.cuda.synchronize()
' >/dev/null || die "load container did not start"
    sleep 20
    measure "$n"
else
    [[ $# -gt 0 ]] || set -- 1 2 4
    have=0
    for n in "$@"; do
        while (( have < n )); do have=$((have + 1)); start_sim "$have"; done
        measure "$n"
    done
fi
date -u
