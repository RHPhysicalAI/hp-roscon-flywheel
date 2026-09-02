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
        """Full world reset + controller reactivation + cube randomization.

        World reset returns everything to SDF initial state (arm joints,
        cubes, physics). Controllers deactivate on reset, so we re-activate
        them. Then randomize cubes if configured.
        """
        self.get_logger().info("World reset...")
        subprocess.run([
            "gz", "service",
            "-s", "/world/pai_world/control",
            "--reqtype", "gz.msgs.WorldControl",
            "--reptype", "gz.msgs.Boolean",
            "--timeout", "5000",
            "--req", "reset: {all: true}",
        ], capture_output=True, timeout=10)

        # Re-activate controllers (world reset deactivates them).
        # Retry — the controller_manager may need time to reinitialize.
        self.get_logger().info("Re-activating controllers...")
        for attempt in range(5):
            time.sleep(3)
            result = subprocess.run([
                "ros2", "service", "call",
                "/controller_manager/switch_controller",
                "controller_manager_msgs/srv/SwitchController",
                "{activate_controllers: [joint_state_broadcaster, forward_position_controller], strictness: 1, timeout: {sec: 30, nanosec: 0}}",
            ], capture_output=True, timeout=40, text=True)
            if "ok=True" in result.stdout:
                self.get_logger().info("Controllers activated")
                break
            self.get_logger().warn(f"Controller activation attempt {attempt+1} failed, retrying...")
        else:
            self.get_logger().error("Failed to activate controllers after 5 attempts")

        # Randomize cubes if configured (after world reset put them at nominal)
        self.get_logger().info("Placing cubes...")
        subprocess.run([
            "python3", "/ws_pai/sim_reset.py",
            "--cubes-only",
        ], capture_output=True, timeout=15)

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

            # 4. Policy attempt window
            t_end = time.time() + EPISODE_LEN
            while time.time() < t_end:
                rclpy.spin_once(self, timeout_sec=0.2)

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
