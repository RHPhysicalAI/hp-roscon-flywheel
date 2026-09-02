#!/bin/bash
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
set -e

source /opt/ros/"$ROS_DISTRO"/setup.bash
source /ws_pai/install/setup.bash

echo "[entrypoint] Starting SO-ARM101 flywheel producer (sim-only mode)"
echo "[entrypoint] RMW_IMPLEMENTATION=$RMW_IMPLEMENTATION"
echo "[entrypoint] Inference runs externally on host GPU via zenoh"

# Ensure episode output directories exist
mkdir -p /data/episodes/raw

# 1. Start zenoh router — listen on all interfaces so the host can connect
echo "[entrypoint] Starting zenoh router (listening on 0.0.0.0:7447)..."
ros2 run rmw_zenoh_cpp rmw_zenohd &
ZENOH_PID=$!
sleep 3

# 2. Start Gazebo sim (headless)
echo "[entrypoint] Starting Gazebo sim (headless)..."
ros2 launch pai_bringup so_arm_gz_bringup.launch.py \
  gazebo_gui:=false \
  launch_rviz:=false \
  launch_rerun:=false \
  mcp:=${MCP_ENABLED:-false} \
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

# Wait for any process to exit, then stop all
wait -n $ZENOH_PID $SIM_PID $CAMERA_PID $EMITTER_PID
echo "[entrypoint] A process exited, shutting down..."
kill $ZENOH_PID $SIM_PID $CAMERA_PID $EMITTER_PID 2>/dev/null
wait
