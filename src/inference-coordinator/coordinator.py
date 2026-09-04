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
import shutil
import subprocess
import threading
import time

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String

from rosetta_interfaces.action import RecordEpisode, RunPolicy

EPISODE_LEN = float(os.environ.get("EPISODE_LEN", "25"))
PROMPT = os.environ.get("PROMPT", "place cubes on tray")
# Record each rollout to a per-episode MCAP bag via the upstream Rosetta
# episode_recorder (D018). The recorder runs as its own node (launched in the
# entrypoint); the coordinator drives it with the RecordEpisode action so the
# bag boundaries match the scored-episode boundaries exactly.
RECORD = os.environ.get("RECORD", "true").lower() == "true"
RECORD_WAIT_S = float(os.environ.get("RECORD_WAIT_S", "30.0"))
# Persistence filtering: keep only bags for episodes that reached full success
# (3/3 cubes) — the curator's hard gate. A failed rollout is not training data,
# so its multi-GB bag is deleted at episode end instead of accumulating. Peak
# cube count is tracked across the window (mirrors the emitter) so a cube placed
# then knocked off still counts as a success, matching the curator's verdict.
PRUNE_REJECTED = os.environ.get("PRUNE_REJECTED", "true").lower() == "true"
BAG_DIR = os.environ.get("BAG_DIR", "/data/bags")
CUBES_TARGET = int(os.environ.get("CUBES_TARGET", "3"))
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
        # The recorder creates its action server with the relative name
        # 'record_episode', which resolves to /record_episode (root namespace) —
        # NOT /episode_recorder/record_episode (the node's docstring is wrong;
        # verified against `ros2 action info` on the live graph).
        self._rec_client = ActionClient(self, RecordEpisode, "/record_episode")
        self._rec_handle = None
        self._recording_available = False
        self._last_bag_path = None
        self._peak_cubes = 0
        self._peak_poll_active = False
        self._control_pub = self.create_publisher(String, "/flywheel/episode_control", 10)
        # Tell the emitter which recorded bag belongs to the episode it's about
        # to finalize, so it can stamp dataset_path into the curator JSON (D018,
        # Phase 2.5 step 3). Published after the policy window, before 'end'.
        self._dataset_pub = self.create_publisher(String, "/flywheel/episode_dataset", 10)
        self._latest_positions = None
        self.create_subscription(JointState, "/joint_states", self._on_joints, 10)
        self.get_logger().info("Coordinator started")

    def _start_recording(self):
        """Send a RecordEpisode goal so the recorder captures this rollout.

        Best-effort: if the recorder isn't available, the loop still runs
        (just without training data for that episode)."""
        if not (RECORD and self._recording_available):
            return
        goal = RecordEpisode.Goal()
        goal.prompt = PROMPT
        send_future = self._rec_client.send_goal_async(goal)
        rclpy.spin_until_future_complete(self, send_future, timeout_sec=10)
        handle = send_future.result()
        if handle and handle.accepted:
            self._rec_handle = handle
            self.get_logger().info("Recording started")
        else:
            self._rec_handle = None
            self.get_logger().warn("Record goal rejected — no bag for this episode")

    def _stop_recording(self):
        """Cancel the active recording and retrieve the finalized bag path.

        Waits for the action *result* (not just the cancel ack) so the bag is
        fully written before the next episode starts — this also avoids the
        recorder rejecting the next start goal with 'already recording'."""
        self._last_bag_path = None
        if self._rec_handle is None:
            return
        result_future = self._rec_handle.get_result_async()
        cancel_future = self._rec_handle.cancel_goal_async()
        rclpy.spin_until_future_complete(self, cancel_future, timeout_sec=10)
        rclpy.spin_until_future_complete(self, result_future, timeout_sec=15)
        wrapped = result_future.result()
        if wrapped is not None:
            res = wrapped.result
            self._last_bag_path = res.bag_path or None
            self.get_logger().info(
                f"Recording stopped: bag={res.bag_path} msgs={res.messages_written}")
        else:
            self.get_logger().warn("Recording stop: no result (bag path unknown)")
        self._rec_handle = None

    def _start_peak_poll(self):
        """Track the peak cube count across the episode (for the keep/prune
        decision), mirroring the emitter so both agree with the curator."""
        self._peak_cubes = 0
        self._peak_poll_active = True

        def poll():
            import task_eval
            while self._peak_poll_active:
                time.sleep(2.5)
                try:
                    _, n = task_eval.evaluate_task()
                    if n > self._peak_cubes:
                        self._peak_cubes = n
                except Exception:
                    pass

        threading.Thread(target=poll, daemon=True).start()

    def _stop_peak_poll(self):
        self._peak_poll_active = False

    def _prune_bag_if_rejected(self):
        """Delete the just-recorded bag unless the episode reached the cube
        target. Keeps only training-worthy (curated) episodes on disk. Runs as
        root inside the container, which owns BAG_DIR — no host sudo needed."""
        if not (PRUNE_REJECTED and self._last_bag_path):
            return
        if self._peak_cubes >= CUBES_TARGET:
            self.get_logger().info(
                f"Kept bag ({self._peak_cubes}/{CUBES_TARGET}): {self._last_bag_path}")
            return
        try:
            real = os.path.realpath(self._last_bag_path)
            bag_root = os.path.realpath(BAG_DIR)
            if os.path.commonpath([real, bag_root]) == bag_root and os.path.isdir(real):
                shutil.rmtree(real, ignore_errors=True)
                self.get_logger().info(
                    f"Pruned rejected bag ({self._peak_cubes}/{CUBES_TARGET}): "
                    f"{self._last_bag_path}")
            else:
                self.get_logger().warn(f"Refused to prune outside {BAG_DIR}: {real}")
        except Exception as e:
            self.get_logger().warn(f"Bag prune failed: {e}")
        self._last_bag_path = None

    def _on_joints(self, msg):
        self._latest_positions = list(msg.position)

    def _signal(self, msg: str):
        m = String()
        m.data = msg
        self._control_pub.publish(m)
        self.get_logger().info(f"Signaled: {msg}")

    def _publish_dataset(self):
        """Publish the just-recorded bag as a repo-relative ref (bags/<name>)
        so the emitter can stamp dataset_path. Empty string when no bag."""
        m = String()
        if self._last_bag_path:
            m.data = f"bags/{os.path.basename(self._last_bag_path)}"
        else:
            m.data = ""
        self._dataset_pub.publish(m)
        self.get_logger().info(f"Dataset ref: '{m.data}'")

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

        if RECORD:
            self.get_logger().info("Waiting for episode recorder action server...")
            if self._rec_client.wait_for_server(timeout_sec=RECORD_WAIT_S):
                self._recording_available = True
                self.get_logger().info("Episode recorder ready — rollouts will be recorded")
            else:
                self.get_logger().warn(
                    "Episode recorder not available — proceeding WITHOUT recording")

        while rclpy.ok():
            # 1. Full world reset + controller reactivation + cube placement.
            #    No active goal, so no fighting.
            self._reset()
            time.sleep(SETTLE_S)  # let physics settle after reset

            # 2. Episode start — begin recording, then signal, so the bag
            #    captures the rollout from the first policy command.
            self._start_recording()
            self._signal("start")
            self._start_peak_poll()

            # 3. Send goal — policy drives the arm from a clean home state
            goal = RunPolicy.Goal()
            goal.prompt = PROMPT
            send_future = self._client.send_goal_async(goal)
            rclpy.spin_until_future_complete(self, send_future, timeout_sec=10)
            handle = send_future.result()
            if not handle or not handle.accepted:
                self.get_logger().warn("Goal rejected — retrying next cycle")
                self._stop_recording()
                self._publish_dataset()
                self._stop_peak_poll()
                self._signal("end")
                self._prune_bag_if_rejected()
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

            # 5b. Stop recording once the policy has stopped commanding, so the
            #     bag holds the rollout (not the idle settle that follows).
            self._stop_recording()
            # 5c. Hand the emitter the bag ref for this episode before 'end'.
            self._publish_dataset()
            self._stop_peak_poll()

            # 6. Settle, then evaluate via the end signal
            time.sleep(SETTLE_S)
            self._signal("end")
            time.sleep(1.0)
            # 7. Persistence filter: keep the bag only if the episode succeeded
            #    (peak cubes >= target); otherwise delete it so failed rollouts
            #    don't accumulate multi-GB bags on disk.
            self._prune_bag_if_rejected()


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
