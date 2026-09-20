# This project was developed with assistance from AI tools.
"""Move the arm like a working robot without a policy: replay recorded action trajectories, or a built-in motion."""

# Publishes what the policy side publishes: std_msgs/Float64MultiArray on /forward_position_controller/commands,
# six joint targets in the controller's order. One episode after another; between episodes the scene is reset the
# way src/sim-reset/sim_reset.py does it (arm home first, then the cubes teleported to a randomised placement).
# Episode and offset of the first episode come from the robot id, so a wall of robots does not move in lockstep.
#
#   ROBOT_ID          r01, r02, ...                                                  (required)
#   TRAJECTORIES      npz from tools/fleet/extract_trajectories.py                   (default /trajectories/trajectories.npz;
#                     absent -> the built-in synthetic motion)
#   COMMAND_HZ        commands a second                                              (default 50, the controller's rate)
#   MOTION_SEED       changes every robot's synthetic motion and cube placements     (default 0)
#   SIM_RESET_DIR     where sim_reset.py lives in the image                          (default /ws_pai)

import math
import os
import random
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forwarder_core import ARM_JOINTS, robot_number  # noqa: E402

COMMAND_TOPIC = "/forward_position_controller/commands"
# so_arm101_macro.xacro: <limit lower= upper=> of the six joints, in ARM_JOINTS order.
JOINT_LIMITS = np.array([[-1.91986, 1.91986], [-1.74533, 1.74533], [-1.69, 1.54],
                         [-1.6, 1.6], [-2.3, 2.3], [0.0, 1.70]])
LIMIT_MARGIN = 0.02
HOME = np.zeros(6)
SYNTHETIC_FPS = 10.0
SYNTHETIC_EPISODES = 32
BLEND_S = 1.5        # ease from wherever the arm is into the episode's first samples
HOME_S = 2.5         # ease back to home before the cubes are moved
SETTLE_ARM_S = 1.5   # sim_reset.reset_sim: let physics settle after the arm is home
SETTLE_CUBES_S = 0.5
GOLDEN = 0.6180339887498949

# Synthetic reach-and-return, as joint-space waypoints: pan towards a cube, hover, lower with the gripper open,
# close, lift, carry to the tray, open. Read off the running sim with the cubes at their nominal places: every
# hover holds the fingertips 8-10 cm above the table, every grasp 2 cm, the tray pose 7 cm over the tray's middle.
# Nothing here touches the table: an arm pressed against it jams, and the contacts cost the sim real time.
#             pan    hover: lift, elbow, wrist flex    grasp: lift, elbow, wrist flex
CUBE_REACH = {"cube_small": (-0.46, (-0.20, 0.20, 1.30), (0.10, 0.30, 1.10)),
              "cube_medium": (0.23, (-0.35, 0.35, 1.30), (0.00, 0.40, 1.17)),
              "cube_large": (0.62, (0.30, -0.50, 1.40), (0.45, -0.20, 1.10))}
CUBE_ORDER = ("cube_small", "cube_medium", "cube_large")   # orange, green, blue: the order the policy works in
TRAY_REACH = (0.30, -0.30, 1.20)
GRIP_CLOSED, GRIP_OPEN = 0.25, 1.2
PEAK_SPEED = 0.9     # rad/s, sets how long a move between two waypoints takes


def clip_to_limits(q: np.ndarray) -> np.ndarray:
    """Joint targets held inside the joint limits, less a small margin."""
    return np.clip(q, JOINT_LIMITS[:, 0] + LIMIT_MARGIN, JOINT_LIMITS[:, 1] - LIMIT_MARGIN)


def smoothstep(u: float) -> float:
    """Minimum-jerk ease from 0 to 1."""
    u = min(max(u, 0.0), 1.0)
    return u * u * u * (10.0 + u * (-15.0 + 6.0 * u))


def _move(a: np.ndarray, b: np.ndarray, fps: float, speed: float) -> list[np.ndarray]:
    """Samples of an eased move from a to b, as long as its widest joint needs at this speed."""
    seconds = max(0.6, 1.875 * float(np.max(np.abs(b - a))) / speed)  # 1.875 = peak/mean speed of the ease
    n = max(2, int(round(seconds * fps)))
    return [a + (b - a) * smoothstep(i / n) for i in range(1, n + 1)]


def synthetic_episode(seed: int, fps: float = SYNTHETIC_FPS) -> np.ndarray:
    """One reach-and-return episode, T x 6 float32 inside the joint limits: the three cubes to the tray in CUBE_ORDER, then home."""
    rng = random.Random(seed)
    speed = PEAK_SPEED * rng.uniform(0.75, 1.15)
    points = [HOME.copy()]
    for cube in CUBE_ORDER:
        pan, hover_shape, grasp_shape = CUBE_REACH[cube]
        pan += rng.uniform(-0.10, 0.10)
        roll = rng.uniform(-0.5, 0.5)
        hover = np.array([pan, *(v + rng.uniform(-0.04, 0.04) for v in hover_shape), roll, GRIP_OPEN])
        grasp = np.array([pan, *grasp_shape, roll, GRIP_OPEN])
        tray = np.array([rng.uniform(-0.15, 0.15), *TRAY_REACH, rng.uniform(-0.3, 0.3), GRIP_CLOSED])
        closed, lifted, released = grasp.copy(), hover.copy(), tray.copy()
        closed[5] = lifted[5] = GRIP_CLOSED
        released[5] = GRIP_OPEN
        points += [hover, grasp, closed, lifted, tray, released]
    points.append(HOME.copy())

    samples = [points[0]]
    for a, b in zip(points, points[1:]):
        samples += _move(a, b, fps, speed)
        samples += [b] * int(round(rng.uniform(0.2, 0.7) * fps))
    return clip_to_limits(np.array(samples)).astype(np.float32)


def synthetic_episodes(seed: int = 0, count: int = SYNTHETIC_EPISODES) -> list[np.ndarray]:
    """The built-in episode set; the same seed gives every pod the same set, the robot id picks where it starts."""
    return [synthetic_episode(seed * 1000 + i) for i in range(count)]


def load_trajectories(path: str) -> tuple[list[np.ndarray], float]:
    """Episodes (T x 6, radians, controller order) and their fps from an extract_trajectories.py npz."""
    with np.load(path, allow_pickle=False) as npz:
        names = [str(n) for n in npz["joint_names"]]
        if names != list(ARM_JOINTS):
            raise ValueError(f"joint order in {path} is {names}, expected {list(ARM_JOINTS)}")
        actions, lengths, fps = npz["actions"], npz["episode_lengths"], float(npz["fps"])
    if actions.ndim != 2 or actions.shape[1] != 6 or int(lengths.sum()) != len(actions) or fps <= 0 or len(lengths) == 0:
        raise ValueError(f"{path}: actions {actions.shape}, lengths sum {int(lengths.sum())}, fps {fps}")
    if not np.all(np.isfinite(actions)) or int(lengths.min()) < 2:
        raise ValueError(f"{path}: a non-finite action, or an episode shorter than two samples")
    bounds = np.cumsum(lengths)[:-1]
    return [clip_to_limits(e) for e in np.split(actions.astype(np.float64), bounds)], fps


def start_position(robot: int, lengths: list[int]) -> tuple[int, int]:
    """First episode and the sample to enter it at, for robot number 1, 2, ...: neighbours differ in both."""
    episode = (robot - 1) % len(lengths)
    fraction = (robot * GOLDEN) % 1.0 * 0.8   # never in the last fifth: the first episode still shows some motion
    return episode, int(fraction * (lengths[episode] - 1))


def sample(episode: np.ndarray, fps: float, t: float) -> np.ndarray:
    """The episode's joint targets at t seconds, linear between samples, held at the ends."""
    x = min(max(t * fps, 0.0), len(episode) - 1.0)
    i = min(int(x), len(episode) - 2)
    return episode[i] + (episode[i + 1] - episode[i]) * (x - i)


def duration(episode: np.ndarray, fps: float) -> float:
    """Seconds from an episode's first sample to its last."""
    return (len(episode) - 1) / fps


def blend(start: np.ndarray, target: np.ndarray, elapsed: float, over: float = BLEND_S) -> np.ndarray:
    """Ease from a start pose into the moving target during the first seconds of an episode."""
    return start + (target - start) * smoothstep(elapsed / over) if elapsed < over else target


def cube_rng(robot: int, episode_count: int, seed: int = 0) -> random.Random:
    """The cube placement's random source: differs per robot and per episode played, repeatable for a seed."""
    return random.Random(seed * 1_000_003 + robot * 10_007 + episode_count)


def run() -> None:
    """The ROS side: publish at COMMAND_HZ, reset the scene between episodes."""
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import JointState
    from std_msgs.msg import Float64MultiArray, MultiArrayDimension

    sys.path.insert(0, os.environ.get("SIM_RESET_DIR", "/ws_pai"))
    from sim_reset import reset_cubes

    def log(msg: str) -> None:
        print(f"[motion-player] {msg}", flush=True)

    robot = robot_number(os.environ.get("ROBOT_ID", ""))
    hz = float(os.environ.get("COMMAND_HZ", "50"))
    seed = int(os.environ.get("MOTION_SEED", "0"))
    path = os.environ.get("TRAJECTORIES", "/trajectories/trajectories.npz")
    if os.path.isfile(path):
        episodes, fps = load_trajectories(path)
        log(f"{len(episodes)} recorded episodes at {fps:g} fps from {path}")
    else:
        episodes, fps = synthetic_episodes(seed), SYNTHETIC_FPS
        log(f"no {path}: {len(episodes)} built-in synthetic episodes")

    rclpy.init()
    node = Node("fleet_motion_player")
    pub = node.create_publisher(Float64MultiArray, COMMAND_TOPIC, 10)
    measured: dict[str, float] = {}
    node.create_subscription(JointState, "/joint_states",
                             lambda m: measured.update(zip(m.name, m.position)), 10)
    msg = Float64MultiArray()
    msg.layout.dim = [MultiArrayDimension(label="joint", size=6, stride=1)]
    commanded = HOME.copy()

    def publish(q: np.ndarray) -> None:
        nonlocal commanded
        commanded = clip_to_limits(np.asarray(q, dtype=np.float64))
        msg.data = [float(v) for v in commanded]
        pub.publish(msg)

    def tick_until(seconds: float, target) -> None:
        """Publish target(elapsed) at the command rate for this long, serving the subscription in between."""
        t0 = time.monotonic()
        step = 1.0 / hz
        due = t0
        while rclpy.ok():
            now = time.monotonic()
            if now - t0 >= seconds:
                return
            publish(target(now - t0))
            due = max(due + step, now - step)   # a late tick is not made up for with a burst
            rclpy.spin_once(node, timeout_sec=max(0.0, due - time.monotonic()))
            pause = due - time.monotonic()
            if pause > 0:
                time.sleep(pause)

    # wait for the sim, and start from where the arm really is
    while rclpy.ok() and not all(j in measured for j in ARM_JOINTS):
        rclpy.spin_once(node, timeout_sec=0.5)
    commanded = np.array([measured[j] for j in ARM_JOINTS])

    index, offset = start_position(robot, [len(e) for e in episodes])
    played = 0
    try:
        while rclpy.ok():
            episode = episodes[index]
            t_in = offset / fps
            start = commanded.copy()
            reset_cubes(randomize=True, rng=cube_rng(robot, played, seed))
            log(f"episode {index} from {t_in:.1f} s of {duration(episode, fps):.1f} s (played {played}), cubes placed")
            tick_until(SETTLE_CUBES_S, lambda _, s=start: s)
            tick_until(duration(episode, fps) - t_in,
                       lambda e, ep=episode, s=start, t0=t_in: blend(s, sample(ep, fps, t0 + e), e))
            # scene reset, in sim_reset.reset_sim's order: the arm home and settled, then (next pass) the cubes
            last = commanded.copy()
            tick_until(HOME_S, lambda e, s=last: s + (HOME - s) * smoothstep(e / HOME_S))
            tick_until(SETTLE_ARM_S, lambda _: HOME)
            error = max(abs(measured.get(j, math.inf)) for j in ARM_JOINTS[:5])
            log(f"arm home, largest joint error {error:.3f} rad")
            index, offset, played = (index + 1) % len(episodes), 0, played + 1
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    run()
