#!/bin/bash
# ACT inference entrypoint. ROLE selects what this container runs (Phase 4.5 C3 role split):
#   policy      — rosetta client + policy server + latched /flywheel/model_version (the RHEM-managed device)
#   coordinator — coordinator + episode recorder + sim reset, driving a remote /run_policy (next to the sim)
#   all         — everything in one container (host GPU path; Fury single box)
# Connects to the sim via zenoh.
# This project was developed with assistance from AI tools.
set -e

source /opt/ros/"$ROS_DISTRO"/setup.bash
source /ws_pai/install/setup.bash

ROLE=${ROLE:-all}
ZENOH_ROUTER=${ZENOH_ROUTER:-"10.0.0.49:7447"}
POLICY_PATH=${POLICY_PATH:-"francocipollone/rospai_act_sim_arm101_place_cubes_on_tray"}
# cuda on the GPU host / Fury; cpu on the desktop device VM (D024)
POLICY_DEVICE=${POLICY_DEVICE:-cuda}
# Client chunking (D024 fallback 1): ask for the checkpoint's full chunk and re-request at half queue,
# so one CPU forward (~250 ms) hides behind ~1 s of queued actions. Upstream params file: 30 / 0.95.
ACTIONS_PER_CHUNK=${ACTIONS_PER_CHUNK:-100}
CHUNK_SIZE_THRESHOLD=${CHUNK_SIZE_THRESHOLD:-0.5}

case "$ROLE" in policy|coordinator|all) ;; *) echo "[inference] Unknown ROLE=$ROLE (policy|coordinator|all)"; exit 2 ;; esac

echo "[inference] Starting ACT inference, role: $ROLE"
echo "[inference] Zenoh router: $ZENOH_ROUTER"
if [ "$ROLE" != "coordinator" ]; then
  echo "[inference] Policy: $POLICY_PATH"
  echo "[inference] Device: $POLICY_DEVICE"
  echo "[inference] Chunking: actions_per_chunk=$ACTIONS_PER_CHUNK chunk_size_threshold=$CHUNK_SIZE_THRESHOLD"
fi

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

# Full contract: the recorder needs the recording/fps keys the policy runner must not see.
CONTRACT_SRC=$(ros2 pkg prefix pai_data_collection)/share/pai_data_collection/config/rosetta/so_arm101.yaml

start_policy() {
  # Strip recording/max_duration_s from contract (policy runner doesn't accept them)
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

  # rosetta_client_launch.py takes tuning from its params_file only; key:=value launch args for
  # policy_device / actions_per_chunk are not declared and were silently ignored (cpu-spike.md part 2).
  PARAMS_SRC=$(ros2 pkg prefix rosetta)/share/rosetta/params/rosetta_client.yaml
  PARAMS=/tmp/rosetta_client_params.yaml
  python3 -c "
import yaml
with open('$PARAMS_SRC') as f:
    p = yaml.safe_load(f)
rp = p['rosetta_client']['ros__parameters']
rp['policy_device'] = '$POLICY_DEVICE'
rp['actions_per_chunk'] = int('$ACTIONS_PER_CHUNK')
rp['chunk_size_threshold'] = float('$CHUNK_SIZE_THRESHOLD')
with open('$PARAMS', 'w') as f:
    yaml.dump(p, f)
print(f'[inference] Params written to $PARAMS: policy_device={rp[\"policy_device\"]} '
      f'actions_per_chunk={rp[\"actions_per_chunk\"]} chunk_size_threshold={rp[\"chunk_size_threshold\"]}')
"

  # Start Rosetta policy runner (includes action server + policy server)
  echo "[inference] Launching Rosetta client with ACT on ${POLICY_DEVICE}..."
  ros2 launch rosetta rosetta_client_launch.py \
    params_file:=${PARAMS} \
    contract_path:=${CONTRACT} \
    pretrained_name_or_path:=${POLICY_PATH} \
    &
  ROSETTA_PID=$!

  # Wait for policy server to be ready
  echo "[inference] Waiting for policy server..."
  sleep 15
}

start_recorder() {
  # Episode recorder (D018): records the contract topics (cameras, joint states, actions) to a
  # per-episode MCAP bag, driven by the coordinator's RecordEpisode action. BAG_DIR should be a
  # host-mounted volume so bags survive container recreation.
  RECORD=${RECORD:-true}
  BAG_DIR=${BAG_DIR:-/data/bags}
  if [ "$RECORD" = "true" ]; then
    mkdir -p "$BAG_DIR"
    echo "[inference] Starting episode recorder -> $BAG_DIR (contract: $CONTRACT_SRC)"
    ros2 launch rosetta episode_recorder_launch.py \
      contract_path:="${CONTRACT_SRC}" \
      bag_base_dir:="${BAG_DIR}" \
      &
    RECORDER_PID=$!
    echo "[inference] Waiting for recorder to configure+activate..."
    sleep 10
  fi
}

# The coordinator drives clean episode phasing with confirmation waits:
#   reset -> confirm arm home -> settle -> start -> send goal -> window ->
#   cancel goal (wait) -> settle -> end (evaluate).
# In the coordinator role it observes /flywheel/model_version from the policy container instead
# of publishing it (coordinator.py, ROLE).
case "$ROLE" in
  policy)
    start_policy
    echo "[inference] Publishing model_version for the emitter..."
    python3 /ws_pai/model_version_pub.py
    ;;
  coordinator)
    start_recorder
    echo "[inference] Starting Python coordinator (remote policy)..."
    python3 /ws_pai/coordinator.py
    ;;
  all)
    start_policy
    start_recorder
    echo "[inference] Starting Python coordinator..."
    python3 /ws_pai/coordinator.py
    ;;
esac
