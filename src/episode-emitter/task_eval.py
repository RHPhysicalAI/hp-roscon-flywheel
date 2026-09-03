"""Task success evaluation — reads actual Gazebo cube positions.

Ground-truth task success (not a heuristic): read each cube's world pose and
check whether it's resting inside the tray footprint.

Poses are read from the world's `/world/<world>/pose/info` topic rather than the
`gz model --pose` CLI. That CLI first resolves the running world via the generic
`/gazebo/worlds` service, which does not respond in this gz build — every query
timed out and returned no pose, so every cube scored as not-placed. The
world-scoped pose-info topic is always available and carries every model's pose.

Tray geometry (from pai_description/world/so_arm_table.sdf):
  tray center: (0.06, 0.0, 0.3945)
  inner footprint: ~±0.0585 (x), ~±0.0785 (y) from center
  floor height: ~0.3945

A cube counts as "placed" if its x,y is within the tray footprint (with a
small margin) and its z is near tray-floor height (not still in the gripper
or knocked off the table).
"""

import os
import re
import subprocess

GZ_WORLD = os.environ.get("GZ_WORLD", "pai_world")
POSE_TOPIC = f"/world/{GZ_WORLD}/pose/info"

# Tray footprint (world coordinates)
TRAY_X = 0.06
TRAY_Y = 0.0
TRAY_Z = 0.3945
# Half-extents of the inner tray area (with margin for cube size)
TRAY_HALF_X = 0.075
TRAY_HALF_Y = 0.095
# Acceptable z band for "resting on tray floor" (cube center ~1.5cm above floor)
Z_MIN = 0.39
Z_MAX = 0.46

CUBES = ["cube_small", "cube_medium", "cube_large"]

_NAME_RE = re.compile(r'\s*name:\s*"([^"]+)"')
_COORD_RE = re.compile(r"\s*([xyz]):\s*([-\d.eE+]+)")


def read_cube_poses() -> dict[str, tuple[float, float, float]]:
    """Parse one pose-info message into {cube_name: (x, y, z)}."""
    try:
        result = subprocess.run(
            ["gz", "topic", "-e", "-t", POSE_TOPIC, "-n", "1"],
            capture_output=True, timeout=8, text=True,
        )
    except Exception:
        return {}

    poses: dict[str, tuple[float, float, float]] = {}
    current: str | None = None
    coords: dict[str, float] = {}
    in_position = False

    for line in result.stdout.splitlines():
        name_match = _NAME_RE.match(line)
        if name_match:
            current = name_match.group(1)
            in_position = False
            coords = {}
            continue
        if current not in CUBES or current in poses:
            continue
        # First `position {` block after a cube's name is its model pose;
        # the `orientation {` block that follows is ignored (current cleared).
        if "position {" in line:
            in_position = True
            coords = {}
            continue
        if in_position:
            coord_match = _COORD_RE.match(line)
            if coord_match:
                coords[coord_match.group(1)] = float(coord_match.group(2))
                if len(coords) == 3:
                    poses[current] = (coords["x"], coords["y"], coords["z"])
                    in_position = False
                    current = None
            elif "}" in line:
                in_position = False
    return poses


def get_cube_pose(name: str) -> tuple[float, float, float] | None:
    """World pose (x, y, z) of a single cube, or None if unavailable."""
    return read_cube_poses().get(name)


def is_on_tray(pose: tuple[float, float, float]) -> bool:
    """True if the cube position is within the tray footprint and height band."""
    x, y, z = pose
    return (
        abs(x - TRAY_X) <= TRAY_HALF_X
        and abs(y - TRAY_Y) <= TRAY_HALF_Y
        and Z_MIN <= z <= Z_MAX
    )


def evaluate_task() -> tuple[bool, int]:
    """Check all cubes. Returns (task_success, cubes_placed).

    task_success is True only if all 3 cubes are on the tray.
    cubes_placed is the count on the tray (0-3).
    """
    poses = read_cube_poses()
    placed = sum(1 for name in CUBES if poses.get(name) and is_on_tray(poses[name]))
    return (placed == len(CUBES), placed)


if __name__ == "__main__":
    poses = read_cube_poses()
    placed = sum(1 for name in CUBES if poses.get(name) and is_on_tray(poses[name]))
    print(f"task_success={placed == len(CUBES)} cubes_placed={placed}/{len(CUBES)}")
    for name in CUBES:
        pose = poses.get(name)
        if pose:
            print(f"  {name}: ({pose[0]:.3f}, {pose[1]:.3f}, {pose[2]:.3f}) "
                  f"on_tray={is_on_tray(pose)}")
        else:
            print(f"  {name}: pose unavailable")
