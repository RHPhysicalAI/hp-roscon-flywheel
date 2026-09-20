# This project was developed with assistance from AI tools.
"""Pure helpers of the fleet world's state forwarder: the render contract's datagram, with no ROS or Gazebo imports."""

# The contract is docs/internal/FLEET-RENDER-CONTRACT.md ("State: world -> renderer").

import json
import math
import re

CONTRACT_VERSION = 1
MAX_DATAGRAM_BYTES = 1200
# The six arm joints by their /joint_states names, in the controller's order (the wire format is keyed by name).
ARM_JOINTS = ("shoulder_pan_joint", "shoulder_lift_joint", "elbow_flex_joint",
              "wrist_flex_joint", "wrist_roll_joint", "gripper_joint")
CUBES = ("cube_small", "cube_medium", "cube_large")
_ROBOT_ID = re.compile(r"r\d\d")
_DECIMALS = (6, 4, 3, 2)


def valid_robot_id(robot: str) -> str:
    """The robot id unchanged when it is 'r' plus two digits, ValueError otherwise."""
    if not isinstance(robot, str) or not _ROBOT_ID.fullmatch(robot):
        raise ValueError(f"robot id must be 'r' and two digits (r01, r17), got {robot!r}")
    return robot


def robot_number(robot: str) -> int:
    """The number in a robot id: 7 for r07."""
    return int(valid_robot_id(robot)[1:])


def parse_addr(addr: str) -> tuple[str, int]:
    """Split 'host:port' into its parts, ValueError when it is not one."""
    host, sep, port = addr.rpartition(":")
    if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"expected host:port, got {addr!r}")
    return host, int(port)


def xyzw_to_wxyz(q) -> list[float]:
    """Gazebo's quaternion order (x, y, z, w) to the contract's (w, x, y, z)."""
    x, y, z, w = q
    return [w, x, y, z]


def cube_pose(position, orientation_xyzw) -> list[float]:
    """One cube as the contract wants it: x, y, z, qw, qx, qy, qz."""
    return [*position, *xyzw_to_wxyz(orientation_xyzw)]


def select_joints(names, positions, wanted=ARM_JOINTS) -> dict[str, float] | None:
    """The wanted joints by name from a /joint_states sample, None when one is missing or the sample is ragged."""
    if len(names) != len(positions):
        return None
    by_name = dict(zip(names, positions))
    if any(j not in by_name for j in wanted):
        return None
    return {j: float(by_name[j]) for j in wanted}


def first_cube_poses(poses) -> dict[str, list[float]]:
    """Cube poses from (name, position xyz, orientation xyzw) triples; the model precedes its links, so the first hit wins."""
    found: dict[str, list[float]] = {}
    for name, position, orientation in poses:
        if name in CUBES and name not in found:
            found[name] = cube_pose(position, orientation)
    return found


def cubes_from_json(message: dict) -> tuple[float, dict[str, list[float]]]:
    """Stamp and cube poses from one `gz topic -e --json-output` Pose_V message (protobuf JSON omits zero fields)."""
    stamp = message.get("header", {}).get("stamp", {})
    t = float(stamp.get("sec", 0)) + float(stamp.get("nsec", 0)) * 1e-9
    triples = []
    for pose in message.get("pose", []):
        p, o = pose.get("position", {}), pose.get("orientation", {})
        triples.append((pose.get("name", ""),
                        [float(p.get(k, 0.0)) for k in "xyz"],
                        [float(o.get(k, 0.0)) for k in "xyzw"]))
    return t, first_cube_poses(triples)


def _rounded(value: float, decimals: int) -> float:
    """Rounded, and never a negative zero."""
    return round(float(value), decimals) + 0.0


def build_datagram(robot: str, t: float, joints: dict[str, float], cubes: dict[str, list[float]]) -> bytes:
    """The contract's datagram; ValueError for a bad id, a missing cube, a non-finite value or more than 1200 bytes."""
    valid_robot_id(robot)
    if len(joints) != len(ARM_JOINTS):
        raise ValueError(f"expected {len(ARM_JOINTS)} joints, got {len(joints)}")
    missing = [c for c in CUBES if c not in cubes]
    if missing:
        raise ValueError(f"missing cubes: {missing}")
    values = [t, *joints.values(), *(v for c in CUBES for v in cubes[c])]
    if any(len(cubes[c]) != 7 for c in CUBES) or not all(math.isfinite(v) for v in values):
        raise ValueError("a cube pose is not 7 numbers, or a value is not finite")
    # Fewer decimals only if the full form would not fit: six decimals of a radian or a metre is far below a pixel.
    for decimals in _DECIMALS:
        body = {"v": CONTRACT_VERSION, "robot": robot, "t": round(float(t), 4),
                "q": {name: _rounded(v, decimals) for name, v in joints.items()},
                "cubes": {c: [_rounded(v, decimals) for v in cubes[c]] for c in CUBES}}
        data = json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(data) <= MAX_DATAGRAM_BYTES:
            return data
    raise ValueError(f"datagram is {len(data)} bytes, the contract allows {MAX_DATAGRAM_BYTES}")
