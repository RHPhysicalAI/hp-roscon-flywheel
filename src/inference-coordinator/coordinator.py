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
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile
from sensor_msgs.msg import JointState
from std_msgs.msg import Float64MultiArray, MultiArrayDimension, String

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
# Model-version lineage: the served policy lives here (act-inference), so the
# label belongs here too. Published (latched) to the emitter so every episode is
# stamped with the policy that actually produced it — correct across swaps
# without recreating the sim. Empty = let the emitter keep its own default.
MODEL_VERSION = os.environ.get("MODEL_VERSION", "")
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

# --- Eval harness (Phase 3, D020) ---
# Instead of the open-ended training loop, run a FIXED number of episodes over a
# REPEATABLE, seeded scene set, collect per-episode success/cubes/smoothness, and
# write an aggregate results file. Self-contained: no curator/MinIO/Kafka in the
# path, so eval episodes never enter the training corpus and eval doesn't depend
# on the hub. Every policy evaluated at the same (EVAL_SEED_BASE, EVAL_EPISODES,
# randomization ranges) sees the identical scene sequence — apples-to-apples, so
# success-rate differences between checkpoints are attributable to the policy.
EVAL_MODE = os.environ.get("EVAL_MODE", "false").lower() == "true"
EVAL_EPISODES = int(os.environ.get("EVAL_EPISODES", "50"))
EVAL_SEED_BASE = int(os.environ.get("EVAL_SEED_BASE", "1000"))
EVAL_RESULTS_DIR = os.environ.get("EVAL_RESULTS_DIR", "/data/eval")

# --- Failure recovery: return to the policy's OWN rest pose after a failed episode ---
# A failed episode can leave the arm in a rough spot (e.g. the gripper parked over the
# cube spawn), so the next episode starts badly and failures chain. The arm SPAWNS at
# all-zeros (measured on a fresh sim), so zeros is a valid in-distribution start and is
# the bootstrap pin (REST_POSE); the pose the policy itself settles into after a success
# (learned at early-stop, persisted) refines it — D016 notes the settled pose can differ
# from zeros. Drive back to that pose on failure so the gripper is clear of the spawn
# zone before the cubes are placed. (A RESET_ARM=true flip, which homes via sim_reset's
# `ros2 topic pub` subprocess, broke the loop for reasons not diagnosed; this recovery
# publishes in-node instead.) Runs only after the goal is cancelled and the bag is
# finalized, so nothing fights it and it is not recorded.
RECOVER_ON_FAIL = os.environ.get("RECOVER_ON_FAIL", "true").lower() == "true"
RECOVER_PUBLISH_S = float(os.environ.get("RECOVER_PUBLISH_S", "5.0"))
RECOVER_RATE = float(os.environ.get("RECOVER_RATE", "20"))
RECOVER_TOL = float(os.environ.get("RECOVER_TOL", "0.15"))  # rad; arm joints only
# Joint order the forward_position_controller expects. /joint_states publishes joints
# in a DIFFERENT (alphabetical) order, so commands are built by name, never by index.
CTRL_JOINTS = [j.strip() for j in os.environ.get(
    "CTRL_JOINTS",
    "shoulder_pan_joint,shoulder_lift_joint,elbow_flex_joint,"
    "wrist_flex_joint,wrist_roll_joint,gripper_joint").split(",") if j.strip()]
GRIPPER_JOINT = os.environ.get("GRIPPER_JOINT", "gripper_joint")
# Optional bootstrap pin: comma-separated positions in CTRL_JOINTS order (e.g. the
# arm's spawn pose). Lets recovery work before any success has been observed; the
# learned pose (persisted to REST_POSE_FILE) takes precedence and refines it.
_REST_POSE_ENV = os.environ.get("REST_POSE", "")
# Learned poses persist here (host-mounted /data) so a restart doesn't have to wait
# for a success to bootstrap recovery. A pin (REST_POSE) takes precedence.
REST_POSE_FILE = os.environ.get("REST_POSE_FILE", "/data/rest_pose.json")


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
        # Latched publisher so a late-joining emitter still gets the label.
        latched = QoSProfile(
            depth=1,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
        )
        self._mv_pub = self.create_publisher(String, "/flywheel/model_version", latched)
        if MODEL_VERSION:
            m = String()
            m.data = MODEL_VERSION
            self._mv_pub.publish(m)
            self.get_logger().info(f"Published model_version: {MODEL_VERSION}")
        self._latest_positions = None
        self._latest_names = None
        # Failure recovery state: the learned rest pose ({joint: pos}) and whether the
        # last episode failed (drives the recovery at the top of the next cycle).
        self._rest_pose = None
        if _REST_POSE_ENV:
            vals = [float(v) for v in _REST_POSE_ENV.split(",")]
            if len(vals) == len(CTRL_JOINTS):
                self._rest_pose = dict(zip(CTRL_JOINTS, vals))
                self.get_logger().info(f"Rest pose pinned from REST_POSE: {self._rest_pose}")
            else:
                self.get_logger().warn("REST_POSE ignored: wrong length")
        # A persisted LEARNED pose (the arm's actual settled pose) beats the env pin,
        # which is only a bootstrap so recovery works before the first success.
        learned = self._load_rest_pose()
        if learned is not None:
            self._rest_pose = learned
        # A restart can land the arm anywhere (e.g. mid-episode when the previous
        # container was stopped). If a rest pose is known, recover on the FIRST
        # cycle too, not just after an observed failure.
        self._last_failed = self._rest_pose is not None
        self._cmd_pub = self.create_publisher(
            Float64MultiArray, "/forward_position_controller/commands", 10)
        # Eval-only per-episode metric accumulators (D020). Inert in the training
        # loop (_metrics_active stays False), so the running producer is unaffected.
        self._metrics_active = False
        self._sm_deltas = []          # per-sample mean |Δjoint| — mirrors the emitter
        self._prev_metric_pos = None
        self._step_count = 0          # /joint_states messages during the window
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
        pos = list(msg.position)
        self._latest_names = list(msg.name)
        # During an eval window, accumulate trajectory smoothness the same way the
        # emitter does (mean absolute per-joint delta between consecutive samples,
        # averaged over the episode) so the eval metric matches the produced one.
        if self._metrics_active:
            if self._prev_metric_pos and len(pos) == len(self._prev_metric_pos):
                deltas = [abs(a - b) for a, b in zip(pos, self._prev_metric_pos)]
                self._sm_deltas.append(sum(deltas) / len(deltas))
            self._prev_metric_pos = pos
            self._step_count += 1
        self._latest_positions = pos

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

    def _load_rest_pose(self):
        """Load a previously learned rest pose from REST_POSE_FILE, or None."""
        try:
            import json
            with open(REST_POSE_FILE) as f:
                pose = json.load(f)
            if all(j in pose for j in CTRL_JOINTS):
                self.get_logger().info(
                    "Rest pose loaded from file: "
                    + ", ".join(f"{j}={pose[j]:.3f}" for j in CTRL_JOINTS))
                return {j: float(pose[j]) for j in CTRL_JOINTS}
        except FileNotFoundError:
            pass
        except Exception as e:
            self.get_logger().warn(f"Rest pose file unreadable: {e}")
        return None

    def _save_rest_pose(self):
        try:
            import json
            os.makedirs(os.path.dirname(REST_POSE_FILE), exist_ok=True)
            with open(REST_POSE_FILE, "w") as f:
                json.dump({j: self._rest_pose[j] for j in CTRL_JOINTS}, f, indent=1)
        except Exception as e:
            self.get_logger().warn(f"Rest pose not saved: {e}")

    def _learn_rest_pose(self):
        """Snapshot the arm's settled pose after a successful episode as the policy's
        rest pose (by joint name). Called at early-stop, when the rest detector has
        already confirmed the arm is still. Skipped if REST_POSE pins it."""
        if not (self._latest_positions and self._latest_names):
            return
        pose = dict(zip(self._latest_names, self._latest_positions))
        if any(j not in pose for j in CTRL_JOINTS):
            self.get_logger().warn(f"Rest pose not learned: joints missing from /joint_states")
            return
        moved = (max(abs(pose[j] - self._rest_pose[j]) for j in CTRL_JOINTS)
                 if self._rest_pose else None)
        self._rest_pose = pose
        if moved is None or moved > 0.05:
            self.get_logger().info(
                "Learned rest pose: " + ", ".join(f"{j}={pose[j]:.3f}" for j in CTRL_JOINTS))
            self._save_rest_pose()

    def _recover_arm(self):
        """After a failed episode, drive the arm back to the learned rest pose before
        the cubes are reset. No goal is active here, so nothing fights the command."""
        if not (RECOVER_ON_FAIL and self._last_failed and self._rest_pose):
            return
        msg = Float64MultiArray()
        msg.layout.dim.append(
            MultiArrayDimension(label="joint", size=len(CTRL_JOINTS), stride=1))
        msg.data = [float(self._rest_pose[j]) for j in CTRL_JOINTS]
        self.get_logger().info(
            f"Recovering arm to rest pose after failed episode ({RECOVER_PUBLISH_S:.0f}s)...")
        deadline = time.time() + RECOVER_PUBLISH_S
        while time.time() < deadline:
            self._cmd_pub.publish(msg)
            rclpy.spin_once(self, timeout_sec=1.0 / RECOVER_RATE)
        # Report how close we got (arm joints only; the gripper may rest on a cube).
        if self._latest_positions and self._latest_names:
            cur = dict(zip(self._latest_names, self._latest_positions))
            err = max(abs(cur[j] - self._rest_pose[j])
                      for j in CTRL_JOINTS if j != GRIPPER_JOINT and j in cur)
            if err <= RECOVER_TOL:
                self.get_logger().info(f"Arm recovered to rest pose (max err {err:.3f} rad)")
            else:
                self.get_logger().warn(f"Arm did not reach rest pose (max err {err:.3f} rad)")

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

    def _reset(self, seed=None):
        """Reset cubes (and optionally arm) between episodes.

        Does NOT do a world reset — that destroys the controller_manager.
        Just repositions cubes via gz set_pose and optionally homes the arm.

        When `seed` is given (eval mode), the cube layout is drawn deterministically
        from that seed, so episode i of every policy sees the identical scene (D020).
        """
        self.get_logger().info(
            "Resetting cubes..." if seed is None
            else f"Resetting cubes (seed={seed})...")
        cmd = ["python3", "/ws_pai/sim_reset.py"]
        if seed is not None:
            cmd += ["--seed", str(seed)]
        subprocess.run(cmd, capture_output=True, timeout=30)

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
            # 0. If the last episode failed, return the arm to the policy's own rest
            #    pose first, so the gripper is clear before cubes are placed and the
            #    next episode starts in-distribution (no chained failures).
            self._recover_arm()
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
                self._last_failed = True
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
                        self._learn_rest_pose()  # settled after success = rest pose
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
            # 8. Remember the outcome so the next cycle can recover the arm if needed.
            self._last_failed = self._peak_cubes < CUBES_TARGET

    # ---- Eval harness (Phase 3, D020) ------------------------------------------

    def _policy_window(self) -> bool:
        """Drive the policy for one attempt window; return True if it early-stopped.

        Same timing logic as run_forever's step 4, factored out for the eval path.
        Early-stop ends a run once 3/3 cubes are placed and the arm has settled."""
        t_end = time.time() + EPISODE_LEN
        window_start = time.time()
        prev_pos = None
        rest_since = None
        while time.time() < t_end:
            rclpy.spin_once(self, timeout_sec=0.2)
            now = time.time()
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
            if (
                EARLY_STOP
                and rest_since is not None
                and now - window_start >= EARLY_MIN_S
                and now - rest_since >= REST_HOLD_S
            ):
                if self._task_complete():
                    self.get_logger().info("Early stop: 3/3 cubes placed and arm settled")
                    return True
                rest_since = now
        return False

    def run_eval(self):
        """Run EVAL_EPISODES over a fixed, seeded scene set and write results.

        No recording, no pruning, no curator — this is measurement only. Each
        episode's scene is drawn from EVAL_SEED_BASE + i, so the sequence is
        identical for every policy evaluated at the same config (apples-to-apples).
        """
        self.get_logger().info(
            f"EVAL MODE — {EVAL_EPISODES} episodes, seed_base={EVAL_SEED_BASE}, "
            f"model_version={MODEL_VERSION or '(emitter default)'}")
        self.get_logger().info("Waiting for action server...")
        self._client.wait_for_server()
        self.get_logger().info("Action server ready")

        results = []
        for i in range(EVAL_EPISODES):
            seed = EVAL_SEED_BASE + i
            self.get_logger().info(f"[eval] episode {i + 1}/{EVAL_EPISODES} seed={seed}")

            # 1. Deterministic scene + clean arm start.
            self._reset(seed=seed)
            time.sleep(SETTLE_S)

            # 2. Begin metric capture for this episode.
            self._sm_deltas = []
            self._prev_metric_pos = None
            self._step_count = 0
            self._start_peak_poll()   # resets peak to 0, spawns the poll thread
            self._metrics_active = True
            ep_start = time.time()
            self._signal("start")

            # 3. Send goal + run the attempt window.
            goal = RunPolicy.Goal()
            goal.prompt = PROMPT
            send_future = self._client.send_goal_async(goal)
            rclpy.spin_until_future_complete(self, send_future, timeout_sec=10)
            handle = send_future.result()
            early = False
            accepted = bool(handle and handle.accepted)
            if not accepted:
                self.get_logger().warn("[eval] goal rejected — episode recorded as aborted")
            else:
                early = self._policy_window()
                cancel_future = handle.cancel_goal_async()
                rclpy.spin_until_future_complete(self, cancel_future, timeout_sec=10)
                time.sleep(1.0)

            # 4. Stop metrics, settle, then score from ground-truth cube poses.
            self._metrics_active = False
            self._stop_peak_poll()
            time.sleep(SETTLE_S)
            self._signal("end")
            time.sleep(0.5)
            try:
                import task_eval
                _, snapshot = task_eval.evaluate_task()
            except Exception as e:
                self.get_logger().warn(f"[eval] task eval failed: {e}")
                snapshot = 0
            # Peak vs. snapshot, mirroring the emitter (a cube placed then knocked
            # off still counts) so the eval number matches the produced one.
            cubes = max(self._peak_cubes, snapshot)
            smooth = (round(sum(self._sm_deltas) / len(self._sm_deltas), 6)
                      if self._sm_deltas else 0.0)
            row = {
                "index": i,
                "seed": seed,
                "cubes_placed": cubes,
                "task_success": cubes >= CUBES_TARGET,
                "steps": self._step_count,
                "avg_smoothness": smooth,
                "duration_s": round(time.time() - ep_start, 2),
                "early_stopped": early,
                "goal_accepted": accepted,
            }
            results.append(row)
            self.get_logger().info(
                f"[eval] ep {i} -> cubes={cubes}/{CUBES_TARGET} "
                f"success={row['task_success']} steps={row['steps']} "
                f"smooth={smooth}")

        self._write_eval_results(results)

    def _write_eval_results(self, results):
        """Aggregate the per-episode rows and write a results JSON to
        EVAL_RESULTS_DIR/<model_version>.json — the source for the Phase 3
        success-rate-vs-dataset-size chart (step 6)."""
        import json
        n = len(results)
        successes = sum(1 for r in results if r["task_success"])
        hist = {str(k): sum(1 for r in results if r["cubes_placed"] == k)
                for k in range(CUBES_TARGET + 1)}
        sm_vals = [r["avg_smoothness"] for r in results if r["avg_smoothness"] > 0]
        mean_sm = round(sum(sm_vals) / len(sm_vals), 6) if sm_vals else 0.0
        mean_cubes = round(sum(r["cubes_placed"] for r in results) / n, 3) if n else 0.0
        mv = MODEL_VERSION or "unversioned"
        doc = {
            "model_version": mv,
            "policy_path": os.environ.get("POLICY_PATH", ""),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "eval_config": {
                "episodes": EVAL_EPISODES,
                "seed_base": EVAL_SEED_BASE,
                "scene": PROMPT,
                "episode_len_s": EPISODE_LEN,
                "early_stop": EARLY_STOP,
                "cubes_target": CUBES_TARGET,
                # The scene distribution (recorded so a re-run is verifiably identical).
                "randomize_cubes": os.environ.get("RANDOMIZE_CUBES", "false"),
                "randomize_only": os.environ.get("RANDOMIZE_ONLY", ""),
                "random_radius": os.environ.get("RANDOM_RADIUS", ""),
                "random_yaw_deg": os.environ.get("RANDOM_YAW_DEG", ""),
            },
            "aggregate": {
                "n": n,
                "successes": successes,
                "success_rate": round(successes / n, 4) if n else 0.0,
                "mean_cubes": mean_cubes,
                "cubes_hist": hist,
                "mean_smoothness": mean_sm,
            },
            "episodes": results,
        }
        os.makedirs(EVAL_RESULTS_DIR, exist_ok=True)
        path = os.path.join(EVAL_RESULTS_DIR, f"{mv.replace('/', '_')}.json")
        with open(path, "w") as f:
            json.dump(doc, f, indent=2)
        agg = doc["aggregate"]
        self.get_logger().info(
            f"[eval] DONE — {successes}/{n} success "
            f"({agg['success_rate'] * 100:.1f}%), mean_cubes={mean_cubes}, "
            f"cubes_hist={hist}, mean_smooth={mean_sm}")
        self.get_logger().info(f"[eval] results -> {path}")


def main():
    rclpy.init()
    node = Coordinator()
    try:
        if EVAL_MODE:
            node.run_eval()   # fixed-N seeded eval, then exit (batch job)
        else:
            node.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
