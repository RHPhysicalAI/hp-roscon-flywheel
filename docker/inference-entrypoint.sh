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

# Strip recording/max_duration_s from contract (policy runner doesn't accept them)
CONTRACT_SRC=$(ros2 pkg prefix pai_data_collection)/share/pai_data_collection/config/rosetta/so_arm101.yaml
CONTRACT=/tmp/so_arm101_inference.yaml
python3 -c "
import yaml, sys
with open('$CONTRACT_SRC') as f:
    c = yaml.safe_load(f)
for k in ['max_duration_s', 'recording']:
    c.pop(k, None)
with open('$CONTRACT', 'w') as f:
    yaml.dump(c, f)
print(f'[inference] Stripped contract written to $CONTRACT')
"

# Start Rosetta policy runner (includes action server + policy server)
echo "[inference] Launching Rosetta client with ACT on GPU..."
ros2 launch rosetta rosetta_client_launch.py \
  contract_path:=${CONTRACT} \
  pretrained_name_or_path:=${POLICY_PATH} \
  policy_type:=act \
  policy_device:=cuda \
  &
ROSETTA_PID=$!

# Wait for policy server to be ready
echo "[inference] Waiting for policy server..."
sleep 15

# Baseline: one continuous RunPolicy goal drives the arm. Episodes are
# windowed by wall clock. Only cubes are reset to nominal between episodes
# (via the emitter side or left to the policy's own homing). No arm reset,
# no goal cancellation — the proven-working configuration.
EPISODE_LEN=${EPISODE_LEN:-25}

echo "[inference] Sending continuous RunPolicy goal..."
ros2 action send_goal /run_policy rosetta_interfaces/action/RunPolicy \
  "{prompt: 'place cubes on tray'}" &
sleep 5

echo "[inference] Starting episode windowing loop (${EPISODE_LEN}s/episode)..."
SETTLE_S=${SETTLE_S:-3}
while true; do
  # Reset cubes — honors RANDOMIZE_CUBES / RANDOMIZE_ONLY / RESET_ARM env.
  python3 /ws_pai/sim_reset.py 2>&1 | grep -v Warning || true

  ros2 topic pub --once /flywheel/episode_control std_msgs/msg/String "{data: start}" 2>&1 | tail -1

  # Policy attempt window
  sleep ${EPISODE_LEN}

  # Let the scene settle before evaluation: the policy's last action may have
  # a cube mid-air or the arm mid-transit. Wait so cubes come to rest, then
  # evaluate. (The policy keeps running but by end of window it has usually
  # finished placing; the settle catches the final resting positions.)
  sleep ${SETTLE_S}

  ros2 topic pub --once /flywheel/episode_control std_msgs/msg/String "{data: end}" 2>&1 | tail -1
  sleep 1
done
