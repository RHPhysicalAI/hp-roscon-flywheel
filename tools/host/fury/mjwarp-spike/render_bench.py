# This project was developed with assistance from AI tools.
"""Render both scene cameras with the mujoco_warp batch renderer, copy RGB to the host, report frame pairs/s."""

# Written against mujoco_warp v3.13.0 (PyPI mujoco-warp==3.13.0 = commit d1a55b6); the signatures
# used are unchanged at main 87e742d (2026-09-17). Everything called is exported by
# mujoco_warp/__init__.py and listed in https://mujoco.readthedocs.io/en/stable/mjwarp/api.html:
#   put_model, make_data, fwd_kinematics, create_render_context, refit_bvh, render, get_rgb
# The loop is the one in the docs' "Batch Rendering" section and notebooks/tutorial.ipynb:
# change state -> kinematics -> refit_bvh -> render -> get_rgb. (The docs' prose calls get_rgb with
# rgb_data=/cam_id= keywords; the source and the API page say (rc, camera_index, rgb_out), so it is
# called positionally here.)
#
# How the renderer's features are switched, all arguments of create_render_context():
#   textures  use_textures=True (default on). In this scene only the skybox is textured: the
#             checker "groundplane" material of the scene template is not assigned to any geom.
#   skybox    render_skybox=True (default off -> flat background_color); needs use_textures.
#   lights    no switch: read from the model (the SDF spot light and <visual><headlight>).
#             use_ambient_lighting=True (default) keeps the ambient terms.
#   shadows   use_shadows=True (default OFF); only lights with castshadow; an occluded point keeps
#             shadow_light_fraction=0.3 of the light.
#   geoms     enabled_geom_groups=[0, 1, 2] (default): world visuals are group 0 and arm visual
#             meshes group 2; the collision geoms of both are group 3 and stay out of the image.

import argparse
import time
from pathlib import Path

import mujoco
import mujoco_warp as mjw
import numpy as np
import warp as wp
from PIL import Image

WIDTH, HEIGHT = 640, 480
SWING = 0.15  # rad, amplitude of the joint motion that makes every BVH refit do real work


def parse_args() -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mjcf", type=Path, help="scene written by build_mjcf.py")
    parser.add_argument("--frames", type=int, default=300, help="timed frames per pass")
    parser.add_argument("--warmup", type=int, default=20, help="untimed frames after the first one")
    parser.add_argument("--out", type=Path, default=Path("."), help="directory for the PNG files")
    parser.add_argument("--device", default="cuda:0", help="Warp device; 'cpu' only proves the code path")
    for name in ("shadows", "textures", "skybox"):
        parser.add_argument(f"--{name}", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    # the skybox is sampled from the texture array, which is empty without use_textures
    args.skybox = args.skybox and args.textures
    return args


class ArmMotion:
    """Host copy of qpos with every limited hinge joint swinging inside its range."""

    def __init__(self, mjm: mujoco.MjModel) -> None:
        hinge = (mjm.jnt_type == mujoco.mjtJoint.mjJNT_HINGE) & (mjm.jnt_limited == 1)
        self.adr = mjm.jnt_qposadr[hinge]
        low, high = mjm.jnt_range[hinge, 0], mjm.jnt_range[hinge, 1]
        self.center = np.clip(mjm.qpos0[self.adr], low + SWING, high - SWING)
        self.phase = np.arange(len(self.adr))
        self.qpos = mjm.qpos0.astype(np.float32).reshape(1, mjm.nq).copy()

    def at(self, frame: int) -> np.ndarray:
        """Return qpos of shape (1, nq) for a frame number."""
        self.qpos[0, self.adr] = self.center + SWING * np.sin(0.1 * frame + self.phase)
        return self.qpos


class Bench:
    """Model, data and render context on the device, plus the two ways of reading RGB back."""

    def __init__(self, mjm: mujoco.MjModel, args: argparse.Namespace) -> None:
        self.motion = ArmMotion(mjm)
        self.ncam = mjm.ncam
        self.m = mjw.put_model(mjm)
        self.d = mjw.make_data(mjm, nworld=1)
        self.rc = mjw.create_render_context(
            mjm,
            nworld=1,
            cam_res=(WIDTH, HEIGHT),
            render_rgb=True,
            render_depth=False,
            render_seg=False,
            use_textures=args.textures,
            use_shadows=args.shadows,
            render_skybox=args.skybox,
        )
        self.rgb_dev = [wp.zeros((1, HEIGHT, WIDTH), dtype=wp.vec3) for _ in range(self.ncam)]
        self.rgb_adr = self.rc.rgb_adr.numpy()
        self.stage_s: dict[str, float] = {}

    def _mark(self, stage: str, since: float) -> float:
        """Wait for the device, add the elapsed time to a stage, return the new start time."""
        wp.synchronize()
        now = time.perf_counter()
        self.stage_s[stage] = self.stage_s.get(stage, 0.0) + now - since
        return now

    def read_get_rgb(self) -> list[np.ndarray]:
        """Documented path: get_rgb unpacks to float vec3 on the device, the host converts to uint8."""
        frames = []
        for cam in range(self.ncam):
            mjw.get_rgb(self.rc, cam, self.rgb_dev[cam])
            frames.append(np.rint(self.rgb_dev[cam].numpy()[0] * 255.0).astype(np.uint8))
        return frames

    def read_packed(self) -> list[np.ndarray]:
        """Copy rc.rgb_data (uint32 0xAARRGGBB per pixel, cameras stacked at rc.rgb_adr) and reorder the bytes."""
        packed = self.rc.rgb_data.numpy()[0]
        frames = []
        for cam in range(self.ncam):
            start = self.rgb_adr[cam]
            bgra = packed[start : start + WIDTH * HEIGHT].view(np.uint8).reshape(HEIGHT, WIDTH, 4)
            frames.append(np.ascontiguousarray(bgra[..., 2::-1]))  # little-endian host: bytes are B G R A
        return frames

    def frame(self, number: int, packed: bool, stages: bool = False) -> list[np.ndarray]:
        """Pose the arm, update kinematics and the BVH, render, and return one uint8 (H, W, 3) image per camera."""
        t = time.perf_counter()
        self.d.qpos.assign(self.motion.at(number))
        mjw.fwd_kinematics(self.m, self.d)  # geom, camera and light poses; no collision or dynamics
        if stages:
            t = self._mark("qpos upload + fwd_kinematics", t)
        mjw.refit_bvh(self.m, self.d, self.rc)
        if stages:
            t = self._mark("refit_bvh", t)
        mjw.render(self.m, self.d, self.rc)
        if stages:
            t = self._mark("render", t)
        images = self.read_packed() if packed else self.read_get_rgb()
        if stages:
            self._mark("read back to host uint8", t)
        return images

    def timed(self, frames: int, packed: bool, stages: bool) -> float:
        """Run frames and return the wall time; with stages, synchronise after each one to time it."""
        self.stage_s = {}
        wp.synchronize()
        start = time.perf_counter()
        for number in range(frames):
            self.frame(number, packed, stages)
        wp.synchronize()
        return time.perf_counter() - start


def main() -> None:
    """Run the benchmark and write the first frame of each camera."""
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    mjm = mujoco.MjModel.from_xml_path(str(args.mjcf))
    names = [mjm.camera(i).name for i in range(mjm.ncam)]
    print(f"mujoco {mujoco.__version__} | mujoco_warp {mjw.__version__} | warp {wp.__version__}")
    print(f"model: {mjm.ngeom} geoms, {mjm.nmesh} meshes ({mjm.nmeshface} faces), {mjm.nlight} lights, cameras {names}")

    wp.init()
    device = wp.get_device(args.device)
    print(f"device: {device.name} | kernel cache: {wp.config.kernel_cache_dir}")
    with wp.ScopedDevice(device):
        t0 = time.perf_counter()
        bench = Bench(mjm, args)
        wp.synchronize()
        t1 = time.perf_counter()
        first = bench.frame(0, packed=False)
        wp.synchronize()
        t2 = time.perf_counter()
        first_packed = bench.frame(0, packed=True)

        for cam, name in enumerate(names):
            path = args.out / f"{name}.png"
            Image.fromarray(first[cam]).save(path)
            diff = int(np.abs(first[cam].astype(np.int16) - first_packed[cam].astype(np.int16)).max())
            print(f"{path}: mean rgb {first[cam].reshape(-1, 3).mean(axis=0).round(1)}, "
                  f"{len(np.unique(first[cam].reshape(-1, 3), axis=0))} colours, "
                  f"packed read-back differs by at most {diff} (expect 0)")

        for number in range(args.warmup):
            bench.frame(number, packed=number % 2 == 1)

        print(f"\nsetup (put_model, make_data, create_render_context, BVH build): {t1 - t0:.1f} s")
        print(f"first frame: {t2 - t1:.1f} s")
        print("both include kernel compilation (or a load from the kernel cache); Warp's own "
              "'Module ... took ... ms (compiled)' lines above give it per module")
        print(f"\n{mjm.ncam} cameras at {WIDTH}x{HEIGHT}, shadows={args.shadows} textures={args.textures} "
              f"skybox={args.skybox}, {args.frames} frames per pass, arm moving every frame")
        for label, packed in (("get_rgb (documented)", False), ("rc.rgb_data packed", True)):
            wall = bench.timed(args.frames, packed, stages=False)
            print(f"  read-back via {label}: {args.frames / wall:.0f} frame pairs/s, "
                  f"{1e3 * wall / args.frames:.2f} ms each, incl. copy to host  (needed: 30)")
            bench.timed(args.frames, packed, stages=True)
            for stage, seconds in bench.stage_s.items():
                print(f"      {stage}: {1e3 * seconds / args.frames:.2f} ms")


if __name__ == "__main__":
    main()
