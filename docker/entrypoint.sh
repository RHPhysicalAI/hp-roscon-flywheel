#!/bin/bash
# This project was developed with assistance from AI tools.
# SO-ARM101 Flywheel Producer entrypoint (desktop-gpu-split mode).
#
# This version runs the sim, camera bridge, and episode emitter only.
# ACT inference runs EXTERNALLY on the host GPU via a separate container
# connected over zenoh.
#
# Processes:
# 1. Zenoh router (middleware, listens on all interfaces for external connections)
# 2. Gazebo sim with SO-ARM101 (headless, no GUI)
# 3. Camera bridge (MJPEG streams for dashboard)
# 4. Episode emitter (monitors rollouts, writes curator JSON, resets sim)
#
# SIM_CAMERAS=off (default on) is the physics-only world of the fleet tenant (D163): Gazebo loads a copy of the
# world without its sensors system, so nothing renders and no GPU is needed; the camera bridge and the episode
# emitter are not started; the state forwarder (and with MOTION_PLAYER=on the motion player) from
# src/fleet-world are. ZENOH_ROUTER=host:port, read in this mode only, links the pod's router to a robot's.
set -e
SIM_CAMERAS=${SIM_CAMERAS:-on}
case $SIM_CAMERAS in
  on|off) ;;
  *) echo "[entrypoint] SIM_CAMERAS must be on or off, not '$SIM_CAMERAS'" >&2; exit 1 ;;
esac

source /opt/ros/"$ROS_DISTRO"/setup.bash
source /ws_pai/install/setup.bash

echo "[entrypoint] Starting SO-ARM101 flywheel producer (sim-only mode)"
echo "[entrypoint] RMW_IMPLEMENTATION=$RMW_IMPLEMENTATION"
echo "[entrypoint] Inference runs externally on host GPU via zenoh"

WORLD_ARGS=()
if [ "$SIM_CAMERAS" = off ]; then
  # The copy keeps the world's name (pai_world: the reset and the pose topics depend on it). The helper cuts
  # exactly the one sensors plugin block and refuses if the rest of the world would differ in any way.
  WORLD_SRC=$(ros2 pkg prefix pai_description)/share/pai_description/world/so_arm_table.sdf
  WORLD_COPY=${FLEET_WORLD_DIR:-/tmp/fleet-world}/so_arm_table.sdf
  mkdir -p "$(dirname "$WORLD_COPY")"
  python3 /ws_pai/fleet_world/physics_only_world.py "$WORLD_SRC" "$WORLD_COPY"
  WORLD_ARGS=("world_file:=$WORLD_COPY")
  echo "[entrypoint] SIM_CAMERAS=off: physics-only world $WORLD_COPY, no camera bridge, no episode emitter"
else
  # Ensure episode output directories exist
  mkdir -p /data/episodes/raw
fi

# 1. Start zenoh router — listen on all interfaces so the host can connect
echo "[entrypoint] Starting zenoh router (listening on 0.0.0.0:7447)..."
if [ "$SIM_CAMERAS" = off ] && [ -n "${ZENOH_ROUTER:-}" ]; then
  # Stage C: the pod keeps its own router, so the world runs whether or not the robot's computer is up, and
  # the router connects out to the robot's.
  echo "[entrypoint] zenoh router connects out to tcp/$ZENOH_ROUTER"
  ZENOH_CONFIG_OVERRIDE="connect/endpoints=[\"tcp/$ZENOH_ROUTER\"]" ros2 run rmw_zenoh_cpp rmw_zenohd &
else
  ros2 run rmw_zenoh_cpp rmw_zenohd &
fi
ZENOH_PID=$!
sleep 3

# 2. Start Gazebo sim (headless)
echo "[entrypoint] Starting Gazebo sim (headless)..."
ros2 launch pai_bringup so_arm_gz_bringup.launch.py \
  gazebo_gui:=false \
  launch_rviz:=false \
  launch_rerun:=false \
  mcp:=${MCP_ENABLED:-false} \
  "${WORLD_ARGS[@]}" \
  &
SIM_PID=$!

# Wait for sim to be ready (joint_states topic publishing)
echo "[entrypoint] Waiting for sim to be ready..."
for i in $(seq 1 60); do
  if ros2 topic info /joint_states 2>/dev/null | grep -q "Publisher count: 1"; then
    echo "[entrypoint] Sim ready — /joint_states publishing"
    break
  fi
  sleep 2
done

# Activate controllers via service call with generous timeout
echo "[entrypoint] Activating controllers (30s timeout)..."
sleep 5
ros2 service call /controller_manager/switch_controller \
  controller_manager_msgs/srv/SwitchController \
  "{activate_controllers: [joint_state_broadcaster, forward_position_controller], strictness: 1, timeout: {sec: 30, nanosec: 0}}" \
  2>&1 | tee /tmp/controller_switch.log

if grep -q "ok=True" /tmp/controller_switch.log; then
  echo "[entrypoint] Controllers activated — arm ready for commands"
else
  echo "[entrypoint] WARNING: controller activation may have failed"
fi

if [ "$SIM_CAMERAS" = off ]; then
  # The ros2 cli calls above left a daemon behind (80 MB a world, times a fleet); nothing here uses the cli again.
  timeout -k 2 10 ros2 daemon stop >/dev/null 2>&1 || true
  # 3. State forwarder: joint positions and cube poses to the fleet's renderer (the cameras' replacement)
  echo "[entrypoint] Starting state forwarder (${ROBOT_ID:-no ROBOT_ID} -> ${RENDER_STATE_ADDR:-10.20.0.1:9701})..."
  python3 -u /ws_pai/fleet_world/state_forwarder.py &
  CAMERA_PID=$!
  # 4. Motion player: recorded or synthetic arm motion, for a world no policy drives
  if [ "${MOTION_PLAYER:-off}" = on ]; then
    echo "[entrypoint] Starting motion player..."
    python3 -u /ws_pai/fleet_world/motion_player.py &
    EMITTER_PID=$!
  else
    EMITTER_PID=
  fi
  echo "[entrypoint] All processes started: zenoh=$ZENOH_PID sim=$SIM_PID forwarder=$CAMERA_PID player=${EMITTER_PID:-none}"
else
# 3. Start camera bridge (MJPEG streams for dashboard)
echo "[entrypoint] Starting camera bridge on port 8081..."
python3 /ws_pai/camera_bridge.py &
CAMERA_PID=$!

# 4. Start episode emitter
echo "[entrypoint] Starting episode emitter..."
python3 /ws_pai/episode_emitter.py &
EMITTER_PID=$!

echo "[entrypoint] All processes started: zenoh=$ZENOH_PID sim=$SIM_PID camera=$CAMERA_PID emitter=$EMITTER_PID"
echo "[entrypoint] Waiting for external inference to connect via zenoh..."
fi

# Wait for any process to exit, then stop all
wait -n $ZENOH_PID $SIM_PID $CAMERA_PID $EMITTER_PID
echo "[entrypoint] A process exited, shutting down..."
kill $ZENOH_PID $SIM_PID $CAMERA_PID $EMITTER_PID 2>/dev/null
wait
