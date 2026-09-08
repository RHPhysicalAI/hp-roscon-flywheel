#!/bin/bash
# Container health for act-inference: the expected model_version is latched on the graph (coordinator in
# role all, model_version_pub.py in role policy) and the rosetta policy action server is up. Exit 0 only
# when both hold (D024, Phase 4.5 C).
# This project was developed with assistance from AI tools.
source /opt/ros/"$ROS_DISTRO"/setup.bash
source /ws_pai/install/setup.bash
# After the sources: the ROS/colcon setup scripts reference unbound variables and abort under -u.
set -u

# Same zenoh client session the entrypoint configured; without it ros2 would try to start a local router.
ZENOH_CFG=/tmp/zenoh_session.json5
export ZENOH_SESSION_CONFIG_URI="$ZENOH_CFG" RMW_ZENOH_CONFIG_FILE="$ZENOH_CFG" RMW_ZENOH_ROUTER_CHECK_ATTEMPTS=0
[ -f "$ZENOH_CFG" ] || { echo "unhealthy: $ZENOH_CFG not written yet"; exit 1; }
[ -n "${MODEL_VERSION:-}" ] || { echo "unhealthy: MODEL_VERSION is unset"; exit 1; }

# Latched (TRANSIENT_LOCAL) publisher; the subscriber must ask for the same durability.
got=$(timeout "${HEALTH_STEP_TIMEOUT:-4}" ros2 topic echo --once \
  --qos-durability transient_local --qos-reliability reliable \
  /flywheel/model_version 2>/dev/null | sed -n 's/^data: //p' | tr -d "'\"" | head -1)
[ "$got" = "$MODEL_VERSION" ] || { echo "unhealthy: model_version '${got:-<none>}' != '$MODEL_VERSION'"; exit 1; }

# rosetta_client registers 'run_policy' in the root namespace (rosetta_client_node.py, ActionServer(..., 'run_policy')).
action="${POLICY_ACTION:-/run_policy}"
timeout "${HEALTH_STEP_TIMEOUT:-4}" ros2 action list 2>/dev/null | grep -qx "$action" \
  || { echo "unhealthy: action server $action not on the graph"; exit 1; }

echo "healthy: model_version=$got action=$action"
exit 0
