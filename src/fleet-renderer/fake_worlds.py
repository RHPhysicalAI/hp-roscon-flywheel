# This project was developed with assistance from AI tools.
"""Stand in for the robots' worlds: contract-conformant state datagrams for N robots, with no sim anywhere."""

# For exercising the renderer and the wall before the worlds exist, and for load: every robot moves its arm
# smoothly through a few poses of the real joint ranges, two cubes rest where the scene puts them (a little
# different per robot) and the middle one slides in a small circle. Standard library only.
#
#   python3 fake_worlds.py --robots 20 --addr 10.20.0.1:9701
#   python3 fake_worlds.py --robots 6 --quiet 2 --quiet-after 10     the last two fall silent: greyed tiles
#   python3 fake_worlds.py --robots 2 --restart-after 20             sim time starts again from zero

import argparse
import json
import math
import socket
import time

JOINTS = ("shoulder_pan_joint", "shoulder_lift_joint", "elbow_flex_joint",
          "wrist_flex_joint", "wrist_roll_joint", "gripper_joint")
# rest, a reach to the side with the wrist rolled, over the tray, a reach to the other side
POSES = ((0.0, 0.0, 0.0, 0.0, 0.0, 0.0), (-0.5, -0.6, 1.2, 0.0, 0.8, 1.2),
         (0.0, 0.6, -0.6, 1.2, 0.0, 1.0), (0.5, -0.3, 0.8, 0.6, -0.5, 0.2))
SECONDS_PER_POSE = 2.5
# resting poses as the sim settles them: x, y, z of the centre
CUBES = {"cube_small": (0.16, -0.11, 0.4055), "cube_medium": (0.17, 0.05, 0.4086), "cube_large": (0.12, 0.20, 0.4108)}
MAX_DATAGRAM = 1200


def arm(t: float, robot: int) -> dict[str, float]:
    """Joint positions at time t: smoothstep between poses, each robot somewhere else in the cycle."""
    phase = (t + 0.9 * robot) / SECONDS_PER_POSE
    index, x = int(phase) % len(POSES), phase % 1.0
    blend = x * x * (3 - 2 * x)
    a, b = POSES[index], POSES[(index + 1) % len(POSES)]
    return {name: round(p + (q - p) * blend, 5) for name, p, q in zip(JOINTS, a, b)}


def yaw_pose(x: float, y: float, z: float, yaw: float) -> list[float]:
    """x y z and a rotation about the vertical as w x y z."""
    return [round(v, 5) for v in (x, y, z, math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2))]


def cubes(t: float, robot: int) -> dict[str, list[float]]:
    """Cube poses at time t: two at rest, a little different per robot, and the middle one on a slow circle."""
    out = {}
    for number, (name, (x, y, z)) in enumerate(CUBES.items()):
        dx, dy = 0.012 * math.sin(robot * 1.7 + number), 0.012 * math.cos(robot * 2.3 + number)
        yaw = 0.4 * robot + number
        if name == "cube_medium":
            angle = 2 * math.pi * t / 8.0 + robot
            dx, dy, yaw = dx + 0.02 * math.cos(angle), dy + 0.02 * math.sin(angle), angle
        out[name] = yaw_pose(x + dx, y + dy, z, yaw)
    return out


def datagram(robot: int, t: float) -> bytes:
    """One robot's state as the contract's JSON."""
    doc = {"v": 1, "robot": f"r{robot:02d}", "t": round(t, 3), "q": arm(t, robot), "cubes": cubes(t, robot)}
    data = json.dumps(doc, separators=(",", ":")).encode()
    assert len(data) <= MAX_DATAGRAM, len(data)
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addr", default="127.0.0.1:9701", help="the renderer's RENDER_STATE_ADDR")
    parser.add_argument("--robots", type=int, default=4)
    parser.add_argument("--first", type=int, default=0, help="number of the first robot")
    parser.add_argument("--rate", type=float, default=30.0, help="datagrams a second per robot")
    parser.add_argument("--seconds", type=float, default=0.0, help="stop after this long; 0 runs until interrupted")
    parser.add_argument("--quiet", type=int, default=0, help="this many of the last robots fall silent ...")
    parser.add_argument("--quiet-after", type=float, default=10.0, help="... after this many seconds")
    parser.add_argument("--restart-after", type=float, default=0.0, help="sim time starts again from zero, once")
    args = parser.parse_args()
    if not (args.robots >= 1 and args.first >= 0 and args.first + args.robots <= 100):
        parser.error("robot ids are r00..r99")
    host, _, port = args.addr.rpartition(":")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (host, int(port))

    start = time.monotonic()
    ticks = sent = 0
    print(f"{args.robots} robots from r{args.first:02d} to udp {args.addr} at {args.rate:g} a second each", flush=True)
    try:
        while True:
            elapsed = time.monotonic() - start
            if args.seconds and elapsed >= args.seconds:
                break
            t = elapsed - args.restart_after if args.restart_after and elapsed >= args.restart_after else elapsed
            talking = args.robots - (args.quiet if elapsed >= args.quiet_after else 0)
            for robot in range(args.first, args.first + talking):
                try:
                    sock.sendto(datagram(robot, t), target)
                    sent += 1
                except OSError:
                    pass   # nobody listening yet: loss is fine
            ticks += 1
            time.sleep(max(0.0, start + ticks / args.rate - time.monotonic()))
    except KeyboardInterrupt:
        pass
    print(f"sent {sent} datagrams in {time.monotonic() - start:.1f} s")


if __name__ == "__main__":
    main()
