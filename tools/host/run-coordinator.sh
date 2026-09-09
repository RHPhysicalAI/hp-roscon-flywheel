#!/bin/bash
# Run the sim-side coordinator role (coordinator + episode recorder + sim reset) next to the
# simulator, driving the policy container's /run_policy on the RHEM device (Phase 4.5 C3 role split).
# Bags land in $DATA_DIR/bags, so dataset_path stays "bags/<sec>_<nsec>" for the assembler and prune.
#
#   IMAGE=quay.io/jary/soarm-flywheel@sha256:... tools/host/run-coordinator.sh            # loop (detached)
#   IMAGE=... MODE=eval MODEL_VERSION=eval-cpu-v2-ft160 tools/host/run-coordinator.sh 20 1000   # D020 eval, foreground
#
# Extra engine flags go after the positional args are consumed via EXTRA_ARGS, e.g. EXTRA_ARGS="-e EPISODE_LEN=60".
# This project was developed with assistance from AI tools.
set -euo pipefail

ENGINE=${ENGINE:-docker}                     # docker on the desktop, podman on the Fury
IMAGE=${IMAGE:?set IMAGE to the digest-pinned runtime image}
MODE=${MODE:-loop}                           # loop | eval
NAME=${NAME:-$([ "$MODE" = eval ] && echo act-eval || echo act-coordinator)}
DATA_DIR=${DATA_DIR:-$HOME/flywheel-data}
ZENOH_ROUTER=${ZENOH_ROUTER:-127.0.0.1:7447}
# Loop: must equal the device's label (healthcheck.sh compares it with /flywheel/model_version).
# Eval: the eval label; the curator discards eval-* lineage and the eval never signals the emitter.
MODEL_VERSION=${MODEL_VERSION:-act-v2-ft160}
EXTRA_ARGS=${EXTRA_ARGS:-}
# podman needs the SELinux relabel on bind mounts; docker ignores it on hosts without SELinux.
SUFFIX=$([ "$ENGINE" = podman ] && echo ":z" || echo "")

args=(--name "$NAME" --network host
  -e ROLE=coordinator -e "ZENOH_ROUTER=$ZENOH_ROUTER" -e "MODEL_VERSION=$MODEL_VERSION"
  -v "$DATA_DIR:/data$SUFFIX" -v "$DATA_DIR/bags:/data/bags$SUFFIX")

# The loop keeps ~1.3 GB per successful episode; it never runs without disk-guard.sh (2026-09-09 outage).
MIN_FREE_GB=${MIN_FREE_GB:-100}
preflight_loop() {
  if ! pgrep -f '[d]isk-guard.sh' >/dev/null; then
    echo "refusing: disk-guard.sh is not running. Start it first:" >&2
    echo "  nohup $(dirname "$0")/disk-guard.sh >/dev/null 2>&1 &" >&2
    exit 3
  fi
  local free; free=$(df -BG --output=avail "$DATA_DIR/bags" | tail -1 | tr -dc 0-9)
  if [ "${free:-0}" -lt "$MIN_FREE_GB" ]; then
    echo "refusing: ${free:-?} GB free under $DATA_DIR/bags, need MIN_FREE_GB=$MIN_FREE_GB." >&2
    echo "  Archive or prune bags (tools/host/prune_bags.py --yes ports the manifest class) and retry." >&2
    exit 3
  fi
}

case "$MODE" in
  loop)
    preflight_loop
    "$ENGINE" container rm -f "$NAME" >/dev/null 2>&1 || true
    "$ENGINE" run -d --restart unless-stopped "${args[@]}" $EXTRA_ARGS "$IMAGE"
    echo "started $NAME (role coordinator) from $IMAGE"
    ;;
  eval)
    # Same pinned D020 config as eval_policy.sh: seeded scenes, no bags, arm homed between seeds.
    N=${1:-50}; SEED=${2:-1000}
    "$ENGINE" container rm -f "$NAME" >/dev/null 2>&1 || true
    "$ENGINE" run --rm --no-healthcheck "${args[@]}" \
      -e EVAL_MODE=true -e "EVAL_EPISODES=$N" -e "EVAL_SEED_BASE=$SEED" \
      -e RECORD=false -e EPISODE_LEN=60 -e RESET_ARM=true \
      -e RANDOMIZE_CUBES=true -e RANDOMIZE_ONLY=cube_medium -e RANDOM_RADIUS=0.03 -e RANDOM_YAW_DEG=180 \
      $EXTRA_ARGS "$IMAGE"
    echo "=== eval $MODEL_VERSION DONE -> $DATA_DIR/eval/$MODEL_VERSION.json ==="
    ;;
  *) echo "MODE must be loop or eval" >&2; exit 2 ;;
esac
