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
from std_msgs.msg import String

from rosetta_interfaces.action import RunPolicy

EPISODE_LEN = float(os.environ.get("EPISODE_LEN", "25"))
PROMPT = os.environ.get("PROMPT", "place cubes on tray")
SETTLE_S = float(os.environ.get("SETTLE_S", "2.0"))


class Coordinator(Node):
    def __init__(self):
        super().__init__("inference_coordinator")
        self._client = ActionClient(self, RunPolicy, "/run_policy")
        self._control_pub = self.create_publisher(String, "/flywheel/episode_control", 10)
        self.get_logger().info("Coordinator started")

    def _signal(self, msg: str):
        m = String()
        m.data = msg
        self._control_pub.publish(m)
        self.get_logger().info(f"Signaled: {msg}")

    def _reset(self):
        self.get_logger().info("Resetting sim (arm + cubes)...")
        subprocess.run(["python3", "/ws_pai/sim_reset.py"], timeout=20)

    def run_forever(self):
        self.get_logger().info("Waiting for action server...")
        self._client.wait_for_server()
        self.get_logger().info("Action server ready")

        while rclpy.ok():
            # 1. Reset while policy is idle (no active goal)
            self._reset()
            time.sleep(SETTLE_S)

            # 2. Episode start
            self._signal("start")

            # 3. Send goal
            goal = RunPolicy.Goal()
            goal.prompt = PROMPT
            send_future = self._client.send_goal_async(goal)
            rclpy.spin_until_future_complete(self, send_future, timeout_sec=10)
            handle = send_future.result()
            if not handle or not handle.accepted:
                self.get_logger().warn("Goal rejected — retrying next cycle")
                self._signal("end")
                continue

            # 4. Let the policy run for the window
            time.sleep(EPISODE_LEN)

            # 5. Cancel the goal — policy stops commanding
            self.get_logger().info("Cancelling goal (end of window)...")
            cancel_future = handle.cancel_goal_async()
            rclpy.spin_until_future_complete(self, cancel_future, timeout_sec=10)

            # 6. Episode end — emitter evaluates
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
