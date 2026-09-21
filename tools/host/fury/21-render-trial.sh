#!/bin/bash
# Where can the sim's cameras render on this box? An experiment, not a configuration: it changes
# the MIG state for a few minutes and puts it back.
#
#   ./21-render-trial.sh mig       MIG stays on; give the sim a MIG slice plus the driver's graphics
#                                  libraries. NVIDIA's MIG guide says no graphics API works in MIG mode
#                                  on this GPU - this shows it on our own hardware.
#   ./21-render-trial.sh full      MIG off; sim and policy share the whole GPU the way the desktop did.
#                                  Then run:  ./13-first-inference.sh 5 nvidia.com/gpu=all eval-fury-gpurender
#   ./21-render-trial.sh restore   stop both containers, MIG back on with the usual layout, CDI refreshed.
#
# The GPU has to be idle to change MIG mode, so `full` and `restore` stop the sim and the policy first.
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

sim=so-arm-sim
policy=act-inference
img=localhost/soarm-sim:arm64
die() { echo "${0##*/}: $*" >&2; exit 1; }
mode() { nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader; }
ros() { podman exec "$sim" bash -c "source /opt/ros/\$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash; timeout -k 2 $1 $2"; }

stop_all() {
    for c in "$policy" "$sim"; do
        podman container exists "$c" && { podman stop -t 10 "$c" >/dev/null; podman rm "$c" >/dev/null; echo "removed $c"; }
    done
    local busy; busy=$(nvidia-smi --query-compute-apps=pid,name --format=csv,noheader)
    [[ -z $busy ]] || die "something else is on the gpu: $busy"
}

start_sim() {   # $1 = cdi device
    mkdir -p /data/flywheel/episodes
    podman run -d --name "$sim" --network host --device "$1" -e NVIDIA_DRIVER_CAPABILITIES=all \
        -v /data/flywheel/episodes:/data/episodes:z "$img" >/dev/null || die "sim did not start"
    echo "## waiting for the camera bridge (up to 3 min)"
    for _ in $(seq 1 36); do
        curl -fsS -m 2 http://127.0.0.1:8081/health >/dev/null 2>&1 && break
        podman container exists "$sim" || break
        sleep 5
    done
    echo "## camera bridge"; curl -sS -m 3 http://127.0.0.1:8081/health; echo
    echo "## what the renderer ended up on"
    podman exec "$sim" bash -c 'grep -h -i -E "GL_RENDERER|GL_VENDOR|GL_VERSION|EGL|Selected|llvmpipe|NVIDIA" /root/.gz/rendering/ogre2.log 2>/dev/null | head -12'
    podman logs "$sim" 2>&1 | grep -i -E 'egl|headless|render.*(fail|error|unable)|Unable' | head -8
    echo "## camera rates"
    for t in /static_camera/image_raw /wrist_camera/image_raw; do
        printf '%-28s ' "$t"; ros 10 "ros2 topic hz $t" 2>&1 | grep -m1 'average rate'; echo
    done
    echo "## gpu";  nvidia-smi --query-compute-apps=pid,name,used_memory --format=csv,noheader
    nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader
    podman stats --no-stream --format '{{.Name}}  cpu {{.CPUPerc}}' "$sim"
}

date -u
case ${1:-} in
mig)
    [[ $(mode) == Enabled ]] || die "MIG is not on"
    podman container exists "$sim" && { podman stop -t 10 "$sim" >/dev/null; podman rm "$sim" >/dev/null; }
    start_sim nvidia.com/gpu=0:3
    echo "sim left running on slice 0:3 with graphics libraries - './12-sim-smoke.sh down' removes it"
    ;;
full)
    stop_all
    nvidia-smi mig -dci && nvidia-smi mig -dgi
    nvidia-smi -i 0 -mig 0
    if [[ $(mode) != Disabled ]]; then
        echo "MIG disable is pending - putting it back rather than rebooting"
        nvidia-smi -i 0 -mig 1; systemctl restart mig-config.service nvidia-cdi-refresh.service
        die "could not leave MIG mode without a reset"
    fi
    systemctl restart nvidia-cdi-refresh.service
    nvidia-ctk cdi list 2>/dev/null
    start_sim nvidia.com/gpu=all
    podman run -d --name "$policy" --network host --device nvidia.com/gpu=all \
        -e ROLE=policy -e POLICY_DEVICE=cuda -e MODEL_VERSION=act-v2-ft160 \
        -e POLICY_PATH=/modelcar/models/act -e ZENOH_ROUTER=127.0.0.1:7447 \
        -e ACTIONS_PER_CHUNK=100 -e CHUNK_SIZE_THRESHOLD=0.5 \
        --mount "type=image,source=quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b,destination=/modelcar" \
        quay.io/jary/soarm-flywheel@sha256:5eba6ca4ee8acf7be87ec8da852d314d6dd16d76cfbce09a1581dbf8c5c94837 >/dev/null \
        || die "policy did not start"
    echo "MIG is OFF. next:  ./13-first-inference.sh 5 nvidia.com/gpu=all eval-fury-gpurender   then:  $0 restore"
    ;;
restore)
    stop_all
    systemctl restart mig-config.service
    systemctl restart nvidia-cdi-refresh.service
    nvidia-smi -L
    n=$(nvidia-ctk cdi list 2>/dev/null | grep -c 'nvidia.com/gpu=0:[0-9]' || true)
    [[ $n -eq 4 ]] || die "wanted 4 slices back in the cdi spec, found $n"
    echo "MIG is back on with 4 slices"
    ;;
*)  echo "usage: $0 mig|full|restore" >&2; exit 1 ;;
esac
date -u
