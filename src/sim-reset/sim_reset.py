"""Sim reset — resets cubes to starting positions and arm to home between episodes.

Called by the episode emitter after each episode completes. Uses:
1. gz service to reset cube poses (from upstream gz_set_cubes_poses.py)
2. ROS 2 topic publish to send arm to home position (all joints 0.0)

Can optionally randomize cube starting positions (--random) for training
diversity — different starting configs help the policy generalize.
"""

import math
import os
import re
import random
import subprocess
import sys
import time


# Cube nominal poses from upstream pai_description/world/so_arm_table.sdf
CUBE_POSES = [
    ("cube_small",  0.16, -0.11, 0.41, 0.0, 0.0, 0.0299955, 0.99955),
    ("cube_medium", 0.17,  0.05, 0.41, 0.0, 0.0, 0.0, 1.0),
    ("cube_large",  0.12,  0.20, 0.41, 0.0, 0.0, -0.3569493, 0.9341238),
]

# Randomization: perturb x,y within this radius (m) and yaw within this range
RANDOM_RADIUS = float(os.environ.get("RANDOM_RADIUS", "0.04"))
RANDOM_YAW_DEG = float(os.environ.get("RANDOM_YAW_DEG", "180"))
# Comma-separated cube names to randomize (empty = all). e.g. "cube_medium"
# cube_medium is the GREEN cube.
RANDOMIZE_ONLY = [c.strip() for c in os.environ.get("RANDOMIZE_ONLY", "").split(",") if c.strip()]


def _randomize(x, y, rng):
    """Sample new (x,y) uniformly in a disk of RANDOM_RADIUS around nominal."""
    r = RANDOM_RADIUS * math.sqrt(rng.random())
    theta = 2.0 * math.pi * rng.random()
    return x + r * math.cos(theta), y + r * math.sin(theta)


def _random_yaw(rng):
    """Sample a z-axis quaternion within RANDOM_YAW_DEG total sweep."""
    phi = math.radians(rng.uniform(-RANDOM_YAW_DEG / 2, RANDOM_YAW_DEG / 2))
    return 0.0, 0.0, math.sin(phi / 2), math.cos(phi / 2)

# Arm home position — all 6 joints at 0.0
ARM_HOME = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def reset_cubes(randomize=False, rng=None):
    """Reset cubes to nominal positions, optionally randomized, via gz service."""
    if randomize and rng is None:
        rng = random.Random()
    procs = []
    for name, x, y, z, qx, qy, qz, qw in CUBE_POSES:
        # Randomize this cube if global randomize is on AND (no per-cube filter
        # or this cube is in the filter list)
        do_rand = randomize and (not RANDOMIZE_ONLY or name in RANDOMIZE_ONLY)
        if do_rand:
            x, y = _randomize(x, y, rng)
            qx, qy, qz, qw = _random_yaw(rng)
        req = (
            f"name: '{name}', "
            f"position: {{x: {x}, y: {y}, z: {z}}}, "
            f"orientation: {{x: {qx}, y: {qy}, z: {qz}, w: {qw}}}"
        )
        cmd = [
            "gz", "service",
            "-s", "/world/pai_world/set_pose",
            "--reqtype", "gz.msgs.Pose",
            "--reptype", "gz.msgs.Boolean",
            "--req", req,
        ]
        procs.append((name, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)))

    for name, proc in procs:
        rc = proc.wait()
        if rc != 0:
            print(f"[sim-reset] WARNING: failed to reset {name} (exit {rc})", flush=True)


# If every joint is within this many radians of home (0), the arm is
# considered already at rest and we skip the reset.
HOME_TOLERANCE = float(os.environ.get("HOME_TOLERANCE", "0.15"))

# Same names/env-var shape as coordinator.py's CTRL_JOINTS/GRIPPER_JOINT (not
# imported — this script ships in a separate image, docker/Dockerfile, that
# doesn't carry coordinator.py). /joint_states publishes alphabetically, not in
# this order; arm_is_home() below looks joints up by name, never by raw index
# (D118/C12 — a positions[:5] slice silently checked the gripper instead of
# wrist_roll_joint).
ARM_JOINTS = [j.strip() for j in os.environ.get(
    "CTRL_JOINTS",
    "shoulder_pan_joint,shoulder_lift_joint,elbow_flex_joint,"
    "wrist_flex_joint,wrist_roll_joint,gripper_joint").split(",") if j.strip()]
GRIPPER_JOINT = os.environ.get("GRIPPER_JOINT", "gripper_joint")


def arm_is_home() -> bool:
    """Check current joint positions. True if all (non-gripper) arm joints are
    near home (0).

    The policy returns the arm to rest when it completes the task, so on
    successful episodes the arm is already home and no reset is needed.
    On failed episodes the arm is left mid-reach and does need homing.
    """
    try:
        names_out = subprocess.run(
            ["ros2", "topic", "echo", "--once", "--field", "name", "/joint_states"],
            capture_output=True, timeout=8, text=True,
        )
        pos_out = subprocess.run(
            ["ros2", "topic", "echo", "--once", "--field", "position", "/joint_states"],
            capture_output=True, timeout=8, text=True,
        )
        # name field prints as a Python list repr: ['elbow_flex_joint', ...]
        names = re.findall(r"'([^']+)'", names_out.stdout)
        # position field prints as array('d', [0.01, -0.02, ...]) followed by a
        # trailing YAML '---' document separator — `ros2 topic echo` emits it even
        # in --once --field mode. A loose "[-\d.eE]+" scan captures '---' as a
        # spurious token; float() on it raises and this whole function used to
        # silently fall through to `except: return False` every time (found live —
        # the pre-existing filter only rejected "", ".", "-", never "---"). Parse
        # each candidate token defensively instead of trusting the character class.
        candidates = re.findall(r"[-\d.eE]+", pos_out.stdout)
        positions = []
        for tok in candidates:
            try:
                positions.append(float(tok))
            except ValueError:
                pass
        if not names or len(names) != len(positions):
            return False  # can't tell — reset to be safe
        pose = dict(zip(names, positions))
        arm = {j: pose[j] for j in ARM_JOINTS if j != GRIPPER_JOINT and j in pose}
        if len(arm) < len(ARM_JOINTS) - 1:  # expect all 5 non-gripper joints present
            return False  # can't tell — reset to be safe
        return all(abs(p) <= HOME_TOLERANCE for p in arm.values())
    except Exception:
        return False  # can't tell — reset to be safe


def reset_arm(duration=None, rate=20):
    if duration is None:
        duration = float(os.environ.get("HOME_PUBLISH_S", "6"))
    """Return arm to home — but only if it's not already home.

    Sustained home-publish at `rate` Hz for `duration` seconds (upstream
    approach). Skipped entirely if the arm is already at rest (success case).
    """
    if arm_is_home():
        print("[sim-reset] Arm already home — skipping arm reset", flush=True)
        return
    print("[sim-reset] Arm not home — returning to rest...", flush=True)
    data = ("{layout: {dim: [{label: joint, size: 6, stride: 1}]}, "
            "data: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}")
    cmd = [
        "ros2", "topic", "pub",
        "/forward_position_controller/commands",
        "std_msgs/msg/Float64MultiArray",
        data,
        "--rate", str(rate),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=duration + 2)
    except subprocess.TimeoutExpired:
        pass  # expected — the pub runs until timeout


def reset_sim(reset_arm_home=None, randomize=None, rng=None):
    """Reset arm to home (via world reset) and reposition cubes for a fresh attempt.

    Sequence:
      1. World reset — arm joints and cubes return to SDF initial state
      2. Re-place cubes (optionally randomized) on top of the reset

    The world reset is done through Gazebo, not the controller command topic,
    so it doesn't fight the continuously-running policy.

    `rng` is an optional seeded random.Random — pass one (eval harness) to make
    the cube layout reproducible; None draws a fresh unseeded layout.
    """
    if randomize is None:
        randomize = os.environ.get("RANDOMIZE_CUBES", "false").lower() == "true"
    if reset_arm_home is None:
        reset_arm_home = os.environ.get("RESET_ARM", "true").lower() == "true"
    mode = "randomized" if randomize else "nominal"
    print(f"[sim-reset] Resetting sim ({mode} cubes)...", flush=True)
    if reset_arm_home:
        reset_arm()      # world reset: arm home + cubes nominal
        time.sleep(1.5)  # let physics settle after reset
    reset_cubes(randomize=randomize, rng=rng)  # re-place cubes (randomized if enabled)
    time.sleep(0.5)
    print("[sim-reset] Sim reset complete", flush=True)


def _parse_seed(argv):
    """Return the int following --seed, or None. A seed means the caller wants a
    deterministic, reproducible cube layout (the eval harness, D020)."""
    if "--seed" in argv:
        i = argv.index("--seed")
        if i + 1 < len(argv):
            return int(argv[i + 1])
    return None


if __name__ == "__main__":
    seed = _parse_seed(sys.argv)
    # A seed implies eval: draw the scene deterministically from that seed. The
    # randomization *ranges* still come from env (RANDOM_RADIUS / RANDOMIZE_ONLY /
    # RANDOM_YAW_DEG), so every policy evaluated with the same seed base and ranges
    # sees the identical layout sequence.
    rng = random.Random(seed) if seed is not None else None
    randomize = (
        seed is not None
        or "--random" in sys.argv
        or os.environ.get("RANDOMIZE_CUBES", "false").lower() == "true"
    )
    cubes_only = "--cubes-only" in sys.argv
    if cubes_only:
        # Only reset cubes (world reset already handled arm/physics)
        print("[sim-reset] Cubes only...", flush=True)
        reset_cubes(randomize=randomize, rng=rng)
        time.sleep(0.5)
        print("[sim-reset] Done", flush=True)
    else:
        reset_sim(randomize=randomize, rng=rng)
