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
# Create a zenoh session config that connects as a client to the remote router
ZENOH_CFG="/tmp/zenoh_session.json5"
cat > "$ZENOH_CFG" << EZCFG
{
  mode: "client",
  connect: {
    endpoints: ["tcp/${ZENOH_ROUTER}"]
  }
}
EZCFG
export ZENOH_SESSION_CONFIG_URI="$ZENOH_CFG"
export RMW_ZENOH_CONFIG_FILE="$ZENOH_CFG"

# Don't start a local router — we're connecting to the remote one
export RMW_ZENOH_ROUTER_CHECK_ATTEMPTS=0

echo "[inference] Zenoh config: connecting to tcp://${ZENOH_ROUTER}"

# Wait for /joint_states to appear (sim is ready via zenoh)
echo "[inference] Waiting for sim topics via zenoh..."
for i in $(seq 1 60); do
  if ros2 topic info /joint_states 2>/dev/null | grep -q "Publisher count"; then
    echo "[inference] Connected to sim — /joint_states visible"
    break
  fi
  sleep 2
done

# Patch Rosetta to use cuda
sed -i 's/policy_device: "cuda"/policy_device: "cuda"/' \
  /ws_pai/install/rosetta/share/rosetta/params/rosetta_client.yaml 2>/dev/null || true

# Start Rosetta client (includes action server + policy server)
echo "[inference] Launching Rosetta client with ACT policy on GPU..."
ros2 launch rosetta rosetta_client_launch.py \
  contract_path:=$(ros2 pkg prefix pai_data_collection)/share/pai_data_collection/config/rosetta/so_arm101.yaml \
  pretrained_name_or_path:=${POLICY_PATH} \
  policy_type:=act \
  policy_device:=cuda \
  &
ROSETTA_PID=$!

# Wait for policy server to be ready
echo "[inference] Waiting for policy server..."
sleep 15

# Continuous rollout loop
echo "[inference] Starting continuous rollout loop..."
while true; do
  echo "[inference] Sending RunPolicy goal..."
  ros2 action send_goal /run_policy rosetta_interfaces/action/RunPolicy \
    "{prompt: 'place cubes on tray'}" --feedback 2>&1 | tail -5

  echo "[inference] Rollout complete, waiting 3s before next..."
  sleep 3
done
