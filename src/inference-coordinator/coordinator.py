"""Inference coordinator — drives the episode lifecycle with clean phasing.

Sequence per episode (no overlap between reset and policy):
  1. Reset: arm home (if not already) + cubes (optionally randomized)
  2. Signal episode start
  3. Send RunPolicy goal, let policy drive the arm for the window
  4. Cancel the goal (policy STOPS commanding)
  5. Signal episode end (emitter evaluates cube positions)
  6. Repeat

Cancelling the goal between episodes is the key: it stops the policy's
command stream so the next reset doesn't fight it. The arm settles, the
reset is clean, and the next episode starts from a known state.
"""

import os
import subprocess
import time

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String

from rosetta_interfaces.action import RunPolicy

EPISODE_LEN = float(os.environ.get("EPISODE_LEN", "25"))
PROMPT = os.environ.get("PROMPT", "place cubes on tray")
SETTLE_S = float(os.environ.get("SETTLE_S", "3.0"))
HOME_TOLERANCE = float(os.environ.get("HOME_TOLERANCE", "0.15"))
HOME_WAIT_MAX = float(os.environ.get("HOME_WAIT_MAX", "8.0"))
# Early-stop: end an episode once the task is complete and the arm has settled,
# rather than waiting out the full window. "Settled" means the arm has stopped
# moving (not a fixed home pose) — detected from joint-position stability.
EARLY_STOP = os.environ.get("EARLY_STOP", "true").lower() == "true"
REST_EPS = float(os.environ.get("REST_EPS", "0.01"))       # max per-sample joint delta (rad) counted as "still"
REST_HOLD_S = float(os.environ.get("REST_HOLD_S", "2.0"))  # how long the arm must stay still
EARLY_MIN_S = float(os.environ.get("EARLY_MIN_S", "5.0"))  # earliest an episode may end


class Coordinator(Node):
    def __init__(self):
        super().__init__("inference_coordinator")
        self._client = ActionClient(self, RunPolicy, "/run_policy")
        self._control_pub = self.create_publisher(String, "/flywheel/episode_control", 10)
        self._latest_positions = None
        self.create_subscription(JointState, "/joint_states", self._on_joints, 10)
        self.get_logger().info("Coordinator started")

    def _on_joints(self, msg):
        self._latest_positions = list(msg.position)

    def _signal(self, msg: str):
        m = String()
        m.data = msg
        self._control_pub.publish(m)
        self.get_logger().info(f"Signaled: {msg}")

    def _arm_at_home(self) -> bool:
        if not self._latest_positions:
            return False
        # First 5 are arm joints (skip gripper)
        return all(abs(p) <= HOME_TOLERANCE for p in self._latest_positions[:5])

    def _wait_for_home(self):
        """Block until the arm reaches home or HOME_WAIT_MAX elapses."""
        deadline = time.time() + HOME_WAIT_MAX
        while time.time() < deadline:
            rclpy.spin_once(self, timeout_sec=0.2)
            if self._arm_at_home():
                self.get_logger().info("Arm confirmed at home")
                return
        self.get_logger().warn("Arm did not reach home within timeout")

    def _reset(self):
        """Reset cubes (and optionally arm) between episodes.

        Does NOT do a world reset — that destroys the controller_manager.
        Just repositions cubes via gz set_pose and optionally homes the arm.
        """
        self.get_logger().info("Resetting cubes...")
        subprocess.run(
            ["python3", "/ws_pai/sim_reset.py"],
            capture_output=True, timeout=20,
        )

    def _task_complete(self) -> bool:
        """True if all three cubes are on the tray (ground-truth cube poses)."""
        try:
            import task_eval
            success, _ = task_eval.evaluate_task()
            return success
        except Exception as e:
            self.get_logger().warn(f"Task-complete check failed: {e}")
            return False

    def run_forever(self):
        self.get_logger().info("Waiting for action server...")
        self._client.wait_for_server()
        self.get_logger().info("Action server ready")

        while rclpy.ok():
            # 1. Full world reset + controller reactivation + cube placement.
            #    No active goal, so no fighting.
            self._reset()
            time.sleep(SETTLE_S)  # let physics settle after reset

            # 2. Episode start
            self._signal("start")

            # 3. Send goal — policy drives the arm from a clean home state
            goal = RunPolicy.Goal()
            goal.prompt = PROMPT
            send_future = self._client.send_goal_async(goal)
            rclpy.spin_until_future_complete(self, send_future, timeout_sec=10)
            handle = send_future.result()
            if not handle or not handle.accepted:
                self.get_logger().warn("Goal rejected — retrying next cycle")
                self._signal("end")
                time.sleep(2)
                continue

            # 4. Policy attempt window — ends early once the task is complete
            #    and the arm has settled, so good runs don't wait out the clock.
            t_end = time.time() + EPISODE_LEN
            window_start = time.time()
            prev_pos = None
            rest_since = None
            while time.time() < t_end:
                rclpy.spin_once(self, timeout_sec=0.2)
                now = time.time()
                # Track when the arm last moved (rest = motion stopped, not a
                # specific joint pose — home is not all-zeros).
                if self._latest_positions:
                    if prev_pos and len(prev_pos) == len(self._latest_positions):
                        moved = max(
                            abs(a - b)
                            for a, b in zip(self._latest_positions[:5], prev_pos[:5])
                        )
                        if moved > REST_EPS:
                            rest_since = None
                        elif rest_since is None:
                            rest_since = now
                    prev_pos = list(self._latest_positions)
                # Only run the (costly) cube check once the arm has held still
                # past the window floor.
                if (
                    EARLY_STOP
                    and rest_since is not None
                    and now - window_start >= EARLY_MIN_S
                    and now - rest_since >= REST_HOLD_S
                ):
                    if self._task_complete():
                        self.get_logger().info(
                            "Early stop: 3/3 cubes placed and arm settled")
                        break
                    # Arm idle but task not done (e.g. stuck) — re-arm the timer
                    # so we recheck later instead of hammering the pose query.
                    rest_since = now

            # 5. Cancel the goal and WAIT for confirmation — policy fully stops
            self.get_logger().info("Cancelling goal (end of window)...")
            cancel_future = handle.cancel_goal_async()
            rclpy.spin_until_future_complete(self, cancel_future, timeout_sec=10)
            time.sleep(1.0)  # let the last command drain

            # 6. Settle, then evaluate via the end signal
            time.sleep(SETTLE_S)
            self._signal("end")
            time.sleep(1.0)


def main():
    rclpy.init()
    node = Coordinator()
    try:
        node.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
