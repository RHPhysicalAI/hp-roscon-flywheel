"""Task success evaluation — queries actual Gazebo cube positions.

Replaces the step-count heuristic with ground-truth: at episode end, query
each cube's pose from Gazebo and check whether it's resting inside the tray
footprint. This is the honest task-success signal a ROSCon audience expects.

Tray geometry (from pai_description/world/so_arm_table.sdf):
  tray center: (0.06, 0.0, 0.3945)
  inner footprint: ~±0.0585 (x), ~±0.0785 (y) from center
  floor height: ~0.3945

A cube counts as "placed" if its x,y is within the tray footprint (with a
small margin) and its z is near tray-floor height (not still in the gripper
or knocked off the table).
"""

import re
import subprocess

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

_POSE_RE = re.compile(r"\[\s*([-\d.e]+)\s+([-\d.e]+)\s+([-\d.e]+)\s*\]")


def get_cube_pose(name: str) -> tuple[float, float, float] | None:
    """Query a cube's world pose via gz model. Returns (x, y, z) or None."""
    try:
        result = subprocess.run(
            ["gz", "model", "-m", name, "--pose"],
            capture_output=True, timeout=8, text=True,
        )
        # Parse the first [x y z] bracket after "Pose"
        out = result.stdout
        idx = out.find("Pose")
        if idx < 0:
            return None
        matches = _POSE_RE.findall(out[idx:])
        if not matches:
            return None
        x, y, z = (float(v) for v in matches[0])
        return (x, y, z)
    except Exception:
        return None


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
    placed = 0
    for name in CUBES:
        pose = get_cube_pose(name)
        if pose and is_on_tray(pose):
            placed += 1
    return (placed == len(CUBES), placed)


if __name__ == "__main__":
    success, count = evaluate_task()
    print(f"task_success={success} cubes_placed={count}/{len(CUBES)}")
    for name in CUBES:
        pose = get_cube_pose(name)
        if pose:
            print(f"  {name}: ({pose[0]:.3f}, {pose[1]:.3f}, {pose[2]:.3f}) "
                  f"on_tray={is_on_tray(pose)}")
        else:
            print(f"  {name}: pose unavailable")
