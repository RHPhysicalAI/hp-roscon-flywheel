#!/bin/bash
# ACT inference entrypoint — runs on the desktop host with GPU.
# Connects to the sim in SNO via zenoh.
set -e

source /opt/ros/"$ROS_DISTRO"/setup.bash
source /ws_pai/install/setup.bash

ZENOH_ROUTER=${ZENOH_ROUTER:-"10.0.0.49:7447"}
POLICY_PATH=${POLICY_PATH:-"francocipollone/rospai_act_sim_arm101_place_cubes_on_tray"}

echo "[inference] Starting ACT policy inference (GPU)"
echo "[inference] Zenoh router: $ZENOH_ROUTER"
echo "[inference] Policy: $POLICY_PATH"
echo "[inference] Device: cuda"

# Configure zenoh to connect to the remote router (sim pod in SNO)
export ZENOH_ROUTER_CHECK_ATTEMPTS=30
export RMW_ZENOH_ROUTER_CONFIG_URI=""

# Wait for /joint_states to appear (sim is ready)
echo "[inference] Waiting for sim topics via zenoh..."
for i in $(seq 1 60); do
  if ros2 topic info /joint_states 2>/dev/null | grep -q "Publisher count"; then
    echo "[inference] Connected to sim — /joint_states visible"
    break
  fi
  sleep 2
done

# Continuous rollout loop
echo "[inference] Starting continuous rollout loop..."
while true; do
  echo "[inference] Sending RunPolicy goal..."
  ros2 action send_goal /run_policy rosetta_interfaces/action/RunPolicy \
    "{prompt: 'place cubes on tray'}" 2>&1 | tail -3

  echo "[inference] Rollout complete, waiting 3s before next..."
  sleep 3
done
