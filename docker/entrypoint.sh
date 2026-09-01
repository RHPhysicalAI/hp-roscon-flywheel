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

# Activate controllers via service call with a generous timeout.
# The launch file's spawner uses a 5s timeout which is too short in
# constrained environments (VMs, resource-limited pods). The service
# call approach lets us set a 30s timeout.
echo "[entrypoint] Activating controllers (30s timeout)..."
sleep 5
ros2 service call /controller_manager/switch_controller \
  controller_manager_msgs/srv/SwitchController \
  "{activate_controllers: [joint_state_broadcaster, forward_position_controller], strictness: 1, timeout: {sec: 30, nanosec: 0}}" \
  2>&1 | tee /tmp/controller_switch.log

if grep -q "ok=True" /tmp/controller_switch.log; then
  echo "[entrypoint] Controllers activated — arm ready for commands"
else
  echo "[entrypoint] WARNING: controller activation may have failed — check logs"
fi

# 3. Start episode emitter
echo "[entrypoint] Starting episode emitter..."
python3 /ws_pai/episode_emitter.py &
EMITTER_PID=$!

# Patch Rosetta params for CPU-only inference
if [ "${POLICY_DEVICE:-cpu}" = "cpu" ]; then
  sed -i 's/policy_device: "cuda"/policy_device: "cpu"/' \
    /ws_pai/install/rosetta/share/rosetta/params/rosetta_client.yaml
  echo "[entrypoint] Patched rosetta_client.yaml: policy_device=cpu"
fi

# 4. Start ACT policy inference via Rosetta (if enabled)
INFERENCE_PID=""
if [ "${RUN_INFERENCE:-true}" = "true" ]; then
  echo "[entrypoint] Starting ACT policy inference via Rosetta..."
  echo "[entrypoint] Policy: ${POLICY_PATH:-francocipollone/rospai_act_sim_arm101_place_cubes_on_tray}"
  echo "[entrypoint] Device: ${POLICY_DEVICE:-cpu}"
  ros2 launch rosetta rosetta_client_launch.py \
    contract_path:=$(ros2 pkg prefix pai_data_collection)/share/pai_data_collection/config/rosetta/so_arm101.yaml \
    pretrained_name_or_path:=${POLICY_PATH:-francocipollone/rospai_act_sim_arm101_place_cubes_on_tray} \
    policy_type:=act \
    policy_device:=${POLICY_DEVICE:-cpu} \
    &
  INFERENCE_PID=$!
  echo "[entrypoint] Inference started, PID: $INFERENCE_PID"
else
  echo "[entrypoint] Inference disabled (RUN_INFERENCE=false)"
fi

echo "[entrypoint] All processes started: zenoh=$ZENOH_PID sim=$SIM_PID emitter=$EMITTER_PID inference=$INFERENCE_PID"

# Wait for any process to exit, then stop all
if [ -n "$INFERENCE_PID" ]; then
  wait -n $ZENOH_PID $SIM_PID $EMITTER_PID $INFERENCE_PID
else
  wait -n $ZENOH_PID $SIM_PID $EMITTER_PID
fi
echo "[entrypoint] A process exited, shutting down..."
kill $ZENOH_PID $SIM_PID $EMITTER_PID $INFERENCE_PID 2>/dev/null
wait
