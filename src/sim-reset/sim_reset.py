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


def arm_is_home() -> bool:
    """Check current joint positions. True if all joints are near home (0).

    The policy returns the arm to rest when it completes the task, so on
    successful episodes the arm is already home and no reset is needed.
    On failed episodes the arm is left mid-reach and does need homing.
    """
    try:
        result = subprocess.run(
            ["ros2", "topic", "echo", "--once", "--field", "position",
             "/joint_states"],
            capture_output=True, timeout=8, text=True,
        )
        # Output is a list like: [0.01, -0.02, 0.0, ...]
        nums = re.findall(r"[-\d.eE]+", result.stdout)
        positions = [float(n) for n in nums if n not in ("", ".", "-")]
        if not positions:
            return False  # can't tell — reset to be safe
        return all(abs(p) <= HOME_TOLERANCE for p in positions[:5])  # arm joints (skip gripper)
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


def reset_sim(reset_arm_home=None, randomize=None):
    """Reset arm to home (via world reset) and reposition cubes for a fresh attempt.

    Sequence:
      1. World reset — arm joints and cubes return to SDF initial state
      2. Re-place cubes (optionally randomized) on top of the reset

    The world reset is done through Gazebo, not the controller command topic,
    so it doesn't fight the continuously-running policy.
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
    reset_cubes(randomize=randomize)  # re-place cubes (randomized if enabled)
    time.sleep(0.5)
    print("[sim-reset] Sim reset complete", flush=True)


if __name__ == "__main__":
    randomize = "--random" in sys.argv or os.environ.get("RANDOMIZE_CUBES", "false").lower() == "true"
    cubes_only = "--cubes-only" in sys.argv
    if cubes_only:
        # Only reset cubes (world reset already handled arm/physics)
        print("[sim-reset] Cubes only...", flush=True)
        reset_cubes(randomize=randomize)
        time.sleep(0.5)
        print("[sim-reset] Done", flush=True)
    else:
        reset_sim(randomize=randomize)
