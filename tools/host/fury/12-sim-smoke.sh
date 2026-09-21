#!/bin/bash
# Does the sim come up headless on this box? Robot publishing joint states, both cameras
# rendering (there is no GPU for it here - MIG slices have no graphics - so this is Mesa on the
# cpu), camera bridge answering. Reports; doesn't try to fix anything.
#
#   ./12-sim-smoke.sh            start it if it isn't running, check, leave it up (the eval needs it)
#   ./12-sim-smoke.sh down       stop and remove it
#
# While it is up the camera bridge is on :8081 - from the tailnet: http://<host>:8081/static
#
# This project was developed with assistance from AI tools.
set -uo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

name=so-arm-sim
img=localhost/soarm-sim:arm64
# ros2 cli tools can ignore SIGTERM on the way out (rmw_zenoh shutdown), so always follow up with KILL
ros() { podman exec "$name" bash -c "source /opt/ros/\$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash; timeout -k 2 $1 $2"; }

if [[ ${1:-} == down ]]; then
    podman stop -t 10 "$name"; podman rm "$name"
    exit
fi

date -u
if podman container exists "$name"; then
    echo "$name is already running"
    podman exec "$name" pkill -9 -f 'ros2 topic' 2>/dev/null     # leftovers from an earlier check
else
    mkdir -p /data/flywheel/episodes
    podman run -d --name "$name" --network host -v /data/flywheel/episodes:/data/episodes:z "$img" || exit 1
fi

echo "## waiting for the camera bridge (up to 5 min)"
for i in $(seq 1 60); do
    curl -fsS -m 2 http://127.0.0.1:8081/health >/dev/null 2>&1 && break
    podman container exists "$name" || { echo "container is gone"; break; }
    sleep 5
done
echo "answered after $(( (i - 1) * 5 ))s"

echo "## camera bridge";  curl -sS -m 3 http://127.0.0.1:8081/health; echo
curl -sS -m 5 -o /data/flywheel/static-snapshot.jpg http://127.0.0.1:8081/static/snapshot && ls -l /data/flywheel/static-snapshot.jpg
echo "## listeners";      ss -lntp | grep -E ':(7447|8081)\s'
echo "## topics";         ros 15 "ros2 topic list" | grep -E 'joint_states|image_raw|run_policy|clock'
echo "## rates"
for t in /joint_states /static_camera/image_raw /wrist_camera/image_raw; do
    printf '%-28s ' "$t"; ros 10 "ros2 topic hz $t" 2>&1 | grep -m1 'average rate' || echo "no rate measured"
done
echo "## renderer / gl";  podman logs "$name" 2>&1 | grep -i -E 'ogre|egl|opengl|llvmpipe|render|vulkan|unable|error|segfault' | head -15
echo "## zenoh";          podman logs "$name" 2>&1 | grep -i -E 'jemalloc|page size' | head -6
echo "## cost";           podman stats --no-stream --format '{{.Name}}  cpu {{.CPUPerc}}  mem {{.MemUsage}}' "$name"
                          echo "load: $(cut -d' ' -f1-3 /proc/loadavg) on $(nproc) cores"
echo "## last log lines"; podman logs --tail 12 "$name" 2>&1
