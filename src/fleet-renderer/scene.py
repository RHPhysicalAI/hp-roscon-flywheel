# This project was developed with assistance from AI tools.
"""Bake the renderer's scene at image build time: upstream's MJCF, the geometry fixes, two colour fixes, a scene map."""

# Runs in the sim image, the only place with Gazebo's python bindings and upstream's workspace
# (docker/Dockerfile.fleet-renderer, first stage). The conversion is the one of
# tools/host/fury/mjwarp-spike/build_mjcf.py, which reproduces upstream's _generate_mjcf_at_launch():
# SDF world -> MJCF with sdformat_mjcf, the arm's xacro, the scene template, flattened into one file.
#
# What is changed afterwards, and nothing else (docs/internal/CUDA-RENDERER-PLAN.md 1.5 and 1.6):
#   geometry  camera fovy 55 -> 55.4114 (Gazebo's horizontal fov 1.2217 rad at 4:3)
#             wrist_roll_joint: qpos = Gazebo's value + 0.0471 rad, recorded in the scene map for the service
#             cubes: the converter names them link_0.., so they are found by nominal position and box size
#   look      the servo material gets the printed parts' colour (no visual in Gazebo's model uses the black one)
#             the world's materials lose the converter's factor 1.2 (a channel it clipped at 1 stays too dark)
#   files     the meshes the MJCF names are copied next to scene.xml and meshdir is made relative, so the
#             output directory is all the final image needs
#
# mujoco, xacro and the converter are imported where they are used: apply_fixes() is tested without them.

import argparse
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

FOVY = 55.4114
WRIST_ROLL_OFFSET = 0.0471
CONVERTER_GAIN = 1.2
ARM_JOINTS = ("shoulder_pan_joint", "shoulder_lift_joint", "elbow_flex_joint",
              "wrist_flex_joint", "wrist_roll_joint", "gripper_joint")
# nominal pose and visual box edge from pai_description/world/so_arm_table.sdf
SDF_CUBES = {"cube_small": ((0.16, -0.11, 0.41), 0.025), "cube_medium": ((0.17, 0.05, 0.41), 0.0312),
             "cube_large": ((0.12, 0.20, 0.41), 0.0376)}
CAMERAS = {"static": "static_camera", "wrist": "wrist_camera"}
# defaults of upstream's launch arguments x y z roll pitch yaw
ARM_BASE = {"arm_base_x": "0.38", "arm_base_y": "0.0", "arm_base_z": "0.4",
            "arm_base_roll": "0.0", "arm_base_pitch": "0.0", "arm_base_yaw": "3.14159"}


def convert(parts: Path) -> tuple[Path, Path, Path]:
    """Build the flattened scene from the installed upstream packages; returns (flat scene, world MJCF, meshdir)."""
    import mujoco
    import xacro
    from ament_index_python.packages import get_package_share_directory
    from sdformat_mjcf.sdformat_to_mjcf.sdformat_to_mjcf import sdformat_file_to_mjcf

    mjcf_dir = Path(get_package_share_directory("pai_bringup")) / "mjcf"
    world_sdf = Path(get_package_share_directory("pai_description")) / "world" / "so_arm_table.sdf"
    meshdir = Path(get_package_share_directory("so_arm101_description")) / "meshes"
    for path in (mjcf_dir / "so_arm101.xml.xacro", mjcf_dir / "scene_template.xml.xacro", world_sdf, meshdir):
        if not path.exists():
            sys.exit(f"{path} not found: the sim image's upstream checkout is not the one this was written for")

    parts.mkdir(parents=True, exist_ok=True)
    world, robot, scene, flat = (parts / n for n in ("world.xml", "so_arm101.xml", "scene.xml", "flat.xml"))
    if sdformat_file_to_mjcf(str(world_sdf), str(world)):
        sys.exit(f"sdformat_mjcf could not convert {world_sdf} (its errors are above)")
    robot.write_text(xacro.process_file(str(mjcf_dir / "so_arm101.xml.xacro"),
                                        mappings={"meshdir": str(meshdir), **ARM_BASE}).toxml())
    scene.write_text(xacro.process_file(str(mjcf_dir / "scene_template.xml.xacro"),
                                        mappings={"world_mjcf_path": str(world), "robot_mjcf_path": str(robot)}).toxml())
    mujoco.mj_saveLastXML(str(flat), mujoco.MjModel.from_xml_path(str(scene)))
    return flat, world, meshdir


def material_names(world_mjcf: str) -> list[str]:
    """Names of the materials the SDF converter wrote."""
    return [m.get("name") for m in ET.fromstring(world_mjcf).iter("material") if m.get("name")]


def apply_fixes(flat_mjcf: str, world_materials: list[str]) -> tuple[str, str]:
    """The fixed scene with a relative meshdir, and the meshdir the flattened file named."""
    root = ET.fromstring(flat_mjcf)
    cameras = list(root.iter("camera"))
    if not cameras:
        sys.exit("the scene has no camera")
    for camera in cameras:
        camera.set("fovy", f"{FOVY}")

    materials = {m.get("name"): m for m in root.iter("material")}
    for name in ("sts3215", "3d_printed"):
        if name not in materials:
            sys.exit(f"the arm's material {name!r} is gone from upstream's MJCF: look at so_arm101.xml.xacro again")
    materials["sts3215"].set("rgba", materials["3d_printed"].get("rgba", "1 1 1 1"))
    absent = [name for name in world_materials if name not in materials]
    if not world_materials or absent:
        sys.exit(f"world materials not in the flattened scene: {absent or 'the converter wrote none'}")
    for name in world_materials:
        # a material saved without rgba has MuJoCo's default, white - which is the converter's clipped 1.2
        r, g, b, a = (float(v) for v in materials[name].get("rgba", "1 1 1 1").split())
        materials[name].set("rgba", f"{r / CONVERTER_GAIN:.6g} {g / CONVERTER_GAIN:.6g} {b / CONVERTER_GAIN:.6g} {a:.6g}")

    compiler = root.find("compiler")
    if compiler is None or not compiler.get("meshdir"):
        sys.exit("the flattened scene names no meshdir")
    meshdir = compiler.get("meshdir")
    compiler.set("meshdir", "meshes")
    return ET.tostring(root, encoding="unicode"), meshdir


def copy_meshes(fixed_mjcf: str, meshdir: Path, out: Path) -> int:
    """Copy the files the scene's assets name, and only those."""
    files = {e.get("file") for tag in ("mesh", "texture", "hfield") for e in ET.fromstring(fixed_mjcf).iter(tag)}
    files.discard(None)
    for name in sorted(files):
        source, target = meshdir / name, out / "meshes" / name
        if Path(name).is_absolute() or ".." in Path(name).parts or not source.is_file():
            sys.exit(f"asset {name!r} of the scene is not a file under {meshdir}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return len(files)


def scene_map(scene_xml: Path) -> dict:
    """Compile the baked scene from where it will live and find the joints, the cubes and the cameras in it."""
    import mujoco
    import numpy as np

    mjm = mujoco.MjModel.from_xml_path(str(scene_xml))
    joints = {}
    for name in ARM_JOINTS:
        jid = mujoco.mj_name2id(mjm, mujoco.mjtObj.mjOBJ_JOINT, name)
        if jid < 0 or mjm.jnt_type[jid] != mujoco.mjtJoint.mjJNT_HINGE:
            sys.exit(f"arm joint {name!r} is not a hinge of the scene: /joint_states' names no longer match the MJCF")
        joints[name] = {"qposadr": int(mjm.jnt_qposadr[jid]),
                        "offset": WRIST_ROLL_OFFSET if name == "wrist_roll_joint" else 0.0}
    cubes = {}
    for jid in range(mjm.njnt):
        if mjm.jnt_type[jid] != mujoco.mjtJoint.mjJNT_FREE:
            continue
        body = mjm.jnt_bodyid[jid]
        visual = [g for g in range(mjm.ngeom) if mjm.geom_bodyid[g] == body and mjm.geom_group[g] == 0]
        if not visual:
            continue
        edge = 2 * float(mjm.geom_size[visual[0]][0])
        for name, (pos, size) in SDF_CUBES.items():
            if np.allclose(mjm.body_pos[body], pos, atol=1e-3) and abs(edge - size) < 1e-4:
                cubes[name] = {"qposadr": int(mjm.jnt_qposadr[jid]), "body": mjm.body(body).name,
                               "joint": mjm.joint(jid).name}
    if sorted(cubes) != sorted(SDF_CUBES) or len({c["qposadr"] for c in cubes.values()}) != 3:
        sys.exit(f"cube mapping incomplete, found {cubes}: the SDF's cube poses or sizes changed (SDF_CUBES)")
    cameras = {}
    for stream, name in CAMERAS.items():
        cid = mujoco.mj_name2id(mjm, mujoco.mjtObj.mjOBJ_CAMERA, name)
        if cid < 0:
            sys.exit(f"camera {name!r} is not in the scene")
        if abs(float(mjm.cam_fovy[cid]) - FOVY) > 1e-4:
            sys.exit(f"camera {name!r} compiled with fovy {mjm.cam_fovy[cid]}, not {FOVY}")
        cameras[stream] = int(cid)
    print(f"compiled {scene_xml}: nq={mjm.nq} ngeom={mjm.ngeom} nmesh={mjm.nmesh} ncam={mjm.ncam} nlight={mjm.nlight}")
    return {"nq": int(mjm.nq), "joints": joints, "cubes": cubes, "cameras": cameras, "fovy": FOVY}


def main() -> None:
    """Write scene.xml, scene_map.json and meshes/ into the output directory, or fail the build."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="directory to write; everything the final image needs")
    parser.add_argument("--flat", type=Path, help="start from an already flattened scene instead of converting")
    parser.add_argument("--world", type=Path, help="with --flat: the converter's world.xml (its materials' names)")
    parser.add_argument("--meshdir", type=Path, help="with --flat: where the meshes are, if not where the scene says")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    if args.flat:
        if not args.world:
            parser.error("--flat needs --world")
        flat, world, meshdir = args.flat, args.world, args.meshdir
    else:
        flat, world, meshdir = convert(out / "parts")
    fixed, named_meshdir = apply_fixes(flat.read_text(), material_names(world.read_text()))
    copied = copy_meshes(fixed, meshdir or Path(named_meshdir), out)
    (out / "scene.xml").write_text(fixed)
    smap = scene_map(out / "scene.xml")
    (out / "scene_map.json").write_text(json.dumps(smap, indent=1) + "\n")
    print(f"wrote {out}/scene.xml, scene_map.json and {copied} mesh files\nscene map: {json.dumps(smap)}")


if __name__ == "__main__":
    main()
