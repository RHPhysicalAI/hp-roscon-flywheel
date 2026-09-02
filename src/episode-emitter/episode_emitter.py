"""Episode emitter — bridges SO-ARM101 Rosetta rollouts to the flywheel curator.

After each ACT policy rollout in the Gazebo sim, this node:
1. Subscribes to /joint_states and camera topics to monitor the rollout
2. Detects episode boundaries (Rosetta start/stop signals)
3. Evaluates task success by checking cube positions relative to the tray
4. Computes trajectory smoothness from joint command deltas
5. Writes a summary JSON to /data/episodes/raw/ for the curator to score

The full rollout data (MCAP rosbag) is handled by Rosetta's episode_recorder.
This emitter only produces the lightweight metadata JSON the curator needs.

Runs as a ROS 2 node alongside the sim and Rosetta.
"""

import json
import math
import os
import time
import uuid
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import JointState
from std_msgs.msg import Float64MultiArray

RAW_DIR = Path(os.environ.get("RAW_DIR", "/data/episodes/raw"))
# When running on the host (not in SNO), POST episodes to the curator receiver
CURATOR_URL = os.environ.get("CURATOR_URL", "")  # e.g. http://10.0.0.49:30802/episode
MODEL_VERSION = os.environ.get("MODEL_VERSION", "soarm-act-v1")
FAILURE_RATE = float(os.environ.get("FAILURE_RATE", "0.1"))  # fraction of episodes to inject as failures
SCENE = os.environ.get("SCENE", "place_cubes_on_tray")
# Episode boundary: if no new joint commands for this many seconds, episode ends
EPISODE_TIMEOUT_S = float(os.environ.get("EPISODE_TIMEOUT_S", "5.0"))
# Maximum episode duration — force-end after this many seconds
MAX_EPISODE_S = float(os.environ.get("MAX_EPISODE_S", "60.0"))
# Minimum steps for a valid episode
MIN_STEPS = int(os.environ.get("MIN_STEPS", "10"))


class EpisodeEmitter(Node):
    """Monitors rollouts and emits curator-compatible episode JSON."""

    def __init__(self):
        super().__init__("episode_emitter")
        RAW_DIR.mkdir(parents=True, exist_ok=True)

        self._episode_id = None
        self._episode_start = None
        self._steps = 0
        self._prev_positions = None
        self._smoothness_deltas = []
        self._last_command_time = None
        self._rollout_active = False
        self._episodes_emitted = 0

        qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=50,
        )

        # Subscribe to joint states (monitor rollout activity)
        self.create_subscription(
            JointState, "/joint_states", self._on_joint_state, qos
        )

        # Subscribe to action commands (detect when policy is sending commands)
        self.create_subscription(
            Float64MultiArray,
            "/forward_position_controller/commands",
            self._on_command,
            qos,
        )

        # Timer to check for episode timeout
        self.create_timer(1.0, self._check_timeout)

        self.get_logger().info(
            f"Episode emitter started — model={MODEL_VERSION} "
            f"scene={SCENE} failure_rate={FAILURE_RATE}"
        )

    def _start_episode(self):
        """Begin tracking a new episode."""
        self._episode_id = str(uuid.uuid4())
        self._episode_start = time.time()
        self._steps = 0
        self._prev_positions = None
        self._smoothness_deltas = []
        self._rollout_active = True
        self.get_logger().info(f"Episode started: {self._episode_id}")

    def _on_joint_state(self, msg: JointState):
        """Track joint states for smoothness computation."""
        if not self._rollout_active:
            return

        positions = list(msg.position)
        if self._prev_positions is not None and len(positions) == len(
            self._prev_positions
        ):
            # Compute per-joint absolute delta
            deltas = [
                abs(p - pp) for p, pp in zip(positions, self._prev_positions)
            ]
            self._smoothness_deltas.append(sum(deltas) / len(deltas))

        self._prev_positions = positions
        self._steps += 1

    def _on_command(self, msg: Float64MultiArray):
        """Detect rollout activity from policy commands."""
        now = time.time()
        if not self._rollout_active:
            self._start_episode()
        self._last_command_time = now

    def _check_timeout(self):
        """End episode if idle too long or max duration reached."""
        if not self._rollout_active:
            return
        now = time.time()
        # Max duration — force-end long-running episodes
        if self._episode_start and (now - self._episode_start) > MAX_EPISODE_S:
            self.get_logger().info(
                f"Episode {self._episode_id[:8]} hit max duration ({MAX_EPISODE_S}s)")
            self._end_episode()
            return
        # Idle timeout — end if no commands received
        if self._last_command_time is None:
            return
        if now - self._last_command_time > EPISODE_TIMEOUT_S:
            self._end_episode()

    def _compute_task_success(self) -> tuple[bool, int]:
        """Evaluate task success from actual Gazebo cube positions.

        Queries each cube's world pose and checks whether it's resting inside
        the tray footprint. This is ground-truth, not a heuristic.
        """
        if self._steps < MIN_STEPS:
            return False, 0
        try:
            import task_eval
            return task_eval.evaluate_task()
        except Exception as e:
            self.get_logger().warn(f"Task eval failed, falling back: {e}")
            return False, 0

    def _avg_smoothness(self) -> float:
        """Mean absolute joint-command delta across the episode."""
        if not self._smoothness_deltas:
            return 0.0
        return sum(self._smoothness_deltas) / len(self._smoothness_deltas)

    def _end_episode(self):
        """Finalize episode, write curator JSON, and reset sim for next attempt."""
        self._rollout_active = False
        duration = time.time() - self._episode_start
        task_success, cubes_placed = self._compute_task_success()
        avg_smoothness = self._avg_smoothness()

        # Failure injection for demo
        import random
        has_failure = random.random() < FAILURE_RATE

        rollout_status = "ok" if self._steps >= MIN_STEPS else "truncated"

        episode = {
            "episode_id": self._episode_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "scene": SCENE,
            "model_version": MODEL_VERSION,
            "has_failure": has_failure,
            "rollout": {
                "status": rollout_status,
                "steps": self._steps,
                "duration_s": round(duration, 2),
            },
            "task_success": task_success and not has_failure,
            "cubes_placed": cubes_placed if not has_failure else 0,
            "avg_smoothness": round(avg_smoothness, 6),
            "rosbag_path": f"rosbags/{self._episode_id}.mcap",
        }

        # Deliver to curator — local file or remote POST
        if CURATOR_URL:
            try:
                import urllib.request
                req = urllib.request.Request(
                    CURATOR_URL,
                    data=json.dumps(episode).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                self.get_logger().warn(f"POST to curator failed: {e}")
                # Fallback: write locally
                RAW_DIR.mkdir(parents=True, exist_ok=True)
                out_path = RAW_DIR / f"{self._episode_id}.json"
                out_path.write_text(json.dumps(episode, indent=2))
        else:
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            out_path = RAW_DIR / f"{self._episode_id}.json"
            out_path.write_text(json.dumps(episode, indent=2))
        self._episodes_emitted += 1

        verdict = "INJECTED-FAIL" if has_failure else ("SUCCESS" if task_success else "FAIL")
        self.get_logger().info(
            f"Episode {self._episode_id[:8]} -> {verdict} "
            f"steps={self._steps} smooth={avg_smoothness:.4f} "
            f"cubes={cubes_placed} [{self._episodes_emitted} total]"
        )

        # Reset sim for next episode (cubes back to start, arm to home)
        self.get_logger().info("Resetting sim for next episode...")
        try:
            import subprocess
            subprocess.run(
                ["python3", "/ws_pai/sim_reset.py"],
                timeout=15,
                capture_output=True,
            )
        except Exception as e:
            self.get_logger().warn(f"Sim reset failed: {e}")

        # Reset for next episode
        self._episode_id = None
        self._last_command_time = None


def main():
    rclpy.init()
    node = EpisodeEmitter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
