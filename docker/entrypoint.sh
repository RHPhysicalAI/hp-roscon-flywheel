#!/bin/bash
# SO-ARM101 Flywheel Producer entrypoint.
#
# Launches three processes:
# 1. Zenoh router (rmw_zenoh middleware router)
# 2. Gazebo sim with SO-ARM101 (headless, no GUI)
# 3. Episode emitter (monitors rollouts, writes curator JSON)
#
# All three share the same ROS 2 domain via zenoh.
set -e

source /opt/ros/"$ROS_DISTRO"/setup.bash
source /ws_pai/install/setup.bash

echo "[entrypoint] Starting SO-ARM101 flywheel producer"
echo "[entrypoint] RMW_IMPLEMENTATION=$RMW_IMPLEMENTATION"
echo "[entrypoint] MODEL_VERSION=${MODEL_VERSION:-soarm-act-v1}"

# Ensure episode output directories exist
mkdir -p /data/episodes/raw

# 1. Start zenoh router in background
echo "[entrypoint] Starting zenoh router..."
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

# Re-spawn controllers if they failed (common in constrained environments
# where Gazebo takes longer to initialize than the spawner timeout allows)
echo "[entrypoint] Ensuring controllers are active..."
sleep 5
for ctrl in joint_state_broadcaster forward_position_controller; do
  if ! ros2 control list_controllers 2>/dev/null | grep -q "$ctrl.*active"; then
    echo "[entrypoint] Re-spawning $ctrl..."
    ros2 run controller_manager spawner "$ctrl" -c /controller_manager \
      --ros-args -p use_sim_time:=true &
  fi
done
sleep 10

# Verify controllers
if ros2 control list_controllers 2>/dev/null | grep -q "forward_position_controller.*active"; then
  echo "[entrypoint] Controllers active — arm ready for commands"
else
  echo "[entrypoint] WARNING: forward_position_controller not active — arm may not respond to commands"
fi

# 3. Start episode emitter
echo "[entrypoint] Starting episode emitter..."
python3 /ws_pai/episode_emitter.py &
EMITTER_PID=$!

echo "[entrypoint] All processes started: zenoh=$ZENOH_PID sim=$SIM_PID emitter=$EMITTER_PID"

# Wait for any process to exit, then stop all
wait -n $ZENOH_PID $SIM_PID $EMITTER_PID
echo "[entrypoint] A process exited, shutting down..."
kill $ZENOH_PID $SIM_PID $EMITTER_PID 2>/dev/null
wait
