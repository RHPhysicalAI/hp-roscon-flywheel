# This project was developed with assistance from AI tools.
"""Assemble the upstream SO-ARM101 MuJoCo scene (arm + table + tray + cubes + cameras) into one MJCF file."""

# Reproduces _generate_mjcf_at_launch() of pai_bringup/launch/so_arm_mujoco_bringup.launch.py
# (ros-physical-ai/demos @ 4d3564cd6bb5f1ee4bcdd39b73a333471b77cb4a):
#   1. pai_description/world/so_arm_table.sdf -> world.xml with sdformat_mjcf
#   2. pai_bringup/mjcf/so_arm101.xml.xacro   -> so_arm101.xml (meshdir = so_arm101_description/meshes)
#   3. pai_bringup/mjcf/scene_template.xml.xacro -> scene.xml, which <include>s 1 and 2
# and then flattens the includes into a single file with mj_saveLastXML. The arm's STL meshes are
# not copied: the output keeps the absolute meshdir of the installed so_arm101_description package.
# The SDF world is boxes and a plane only, so the conversion writes no mesh assets.
#
# Needs: source /opt/ros/$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash

import argparse
import sys
from pathlib import Path

import mujoco
import xacro

# defaults of the launch arguments x y z roll pitch yaw
ARM_BASE = {
    "arm_base_x": "0.38",
    "arm_base_y": "0.0",
    "arm_base_z": "0.4",
    "arm_base_roll": "0.0",
    "arm_base_pitch": "0.0",
    "arm_base_yaw": "3.14159",
}

IMPORT_FIX = """cannot import {name!r}, needed for the SDF -> MJCF conversion.
  sdformat_mjcf, dm_control: pip install --break-system-packages -r requirements.txt
  sdformat, gz.math:         Gazebo's Python bindings. On ROS 2 kilted they come with
                             ros-kilted-sdformat-vendor / ros-kilted-gz-math-vendor and are put on
                             PYTHONPATH by `source /opt/ros/$ROS_DISTRO/setup.bash`.
                             Check: python3 -c "import sdformat, gz.math; print(sdformat.__file__)\""""


def convert_world(sdf_path: Path, out_path: Path) -> None:
    """Convert the SDF world to an MJCF file with sdformat_mjcf."""
    try:
        from sdformat_mjcf.sdformat_to_mjcf.sdformat_to_mjcf import sdformat_file_to_mjcf
    except ImportError as err:
        sys.exit(IMPORT_FIX.format(name=err.name))
    if sdformat_file_to_mjcf(str(sdf_path), str(out_path)):
        sys.exit(f"sdformat_mjcf could not convert {sdf_path} (its errors are above)")


def process_robot(robot_xacro: Path, meshdir: Path, out_path: Path) -> None:
    """Expand the arm MJCF xacro with the mesh directory and the arm base pose."""
    doc = xacro.process_file(str(robot_xacro), mappings={"meshdir": str(meshdir), **ARM_BASE})
    out_path.write_text(doc.toxml())


def compose_scene(template_xacro: Path, world_mjcf: Path, robot_mjcf: Path, out_path: Path) -> None:
    """Expand the scene template, which includes the robot and the world MJCF."""
    doc = xacro.process_file(
        str(template_xacro),
        mappings={"world_mjcf_path": str(world_mjcf), "robot_mjcf_path": str(robot_mjcf)},
    )
    out_path.write_text(doc.toxml())


def flatten(scene_mjcf: Path, out_path: Path) -> mujoco.MjModel:
    """Compile the scene, save it as one MJCF without includes, and compile that file again."""
    model = mujoco.MjModel.from_xml_path(str(scene_mjcf))
    mujoco.mj_saveLastXML(str(out_path), model)
    return mujoco.MjModel.from_xml_path(str(out_path))


def report(model: mujoco.MjModel) -> None:
    """Print what the renderer will be given: cameras, geoms per group, meshes, lights, textures."""
    print(f"compiled: nq={model.nq} nbody={model.nbody} ngeom={model.ngeom} nmesh={model.nmesh} "
          f"nlight={model.nlight} ntex={model.ntex} nmat={model.nmat}")
    groups = {int(g): int((model.geom_group == g).sum()) for g in sorted(set(model.geom_group))}
    print(f"geoms per group: {groups}  (mujoco_warp renders groups 0, 1, 2 by default)")
    print(f"cameras: {model.ncam}")
    for i in range(model.ncam):
        width, height = model.cam_resolution[i]
        print(f"  [{i}] {model.camera(i).name}: {width}x{height}, fovy {model.cam_fovy[i]:g} deg")
    for i in range(model.nlight):
        print(f"  light [{i}] {model.light(i).name}: type {int(model.light_type[i])}, "
              f"castshadow {bool(model.light_castshadow[i])}, attenuation {model.light_attenuation[i]}")


def main() -> None:
    """Build the scene from the installed upstream packages."""
    from ament_index_python.packages import get_package_share_directory

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="path of the MJCF file to write")
    out = parser.parse_args().output.resolve()

    mjcf_dir = Path(get_package_share_directory("pai_bringup")) / "mjcf"
    world_sdf = Path(get_package_share_directory("pai_description")) / "world" / "so_arm_table.sdf"
    meshdir = Path(get_package_share_directory("so_arm101_description")) / "meshes"
    for path in (mjcf_dir / "so_arm101.xml.xacro", mjcf_dir / "scene_template.xml.xacro", world_sdf, meshdir):
        if not path.exists():
            sys.exit(f"{path} not found: the image's upstream checkout differs from 4d3564cd, "
                     f"see /ws_pai/src/demos/pai_bringup/launch/so_arm_mujoco_bringup.launch.py")

    # intermediate files stay next to the output so the converted world can be inspected
    parts = out.parent / f"{out.stem}_parts"
    parts.mkdir(parents=True, exist_ok=True)
    convert_world(world_sdf, parts / "world.xml")
    process_robot(mjcf_dir / "so_arm101.xml.xacro", meshdir, parts / "so_arm101.xml")
    compose_scene(mjcf_dir / "scene_template.xml.xacro", parts / "world.xml", parts / "so_arm101.xml",
                  parts / "scene.xml")

    report(flatten(parts / "scene.xml", out))
    print(f"wrote {out}  (intermediate files in {parts})")


if __name__ == "__main__":
    main()
