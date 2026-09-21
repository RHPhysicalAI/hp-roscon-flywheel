# This project was developed with assistance from AI tools.
"""MuJoCo-Warp batch renderer on this scene: nworld robots, two 640x480 cameras each, frame pairs per second and device memory."""

# One process per nworld so the memory numbers are clean. Every world gets its own arm motion and cube
# placement every frame, so each BVH refit does real work, and the packed RGB of all worlds is copied to the
# host as uint8 - the same loop as render_bench.py of the spike, with (nworld, nq) state.

import argparse
import json
import time
from pathlib import Path

import mujoco
import mujoco_warp as mjw
import numpy as np
import warp as wp

WIDTH, HEIGHT = 640, 480
SWING = 0.15


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mjcf", type=Path)
    parser.add_argument("--nworld", type=int, default=1)
    parser.add_argument("--frames", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--shadows", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--out", type=Path, default=None, help="write world 0 and the last world of the last frame here")
    args = parser.parse_args()

    mjm = mujoco.MjModel.from_xml_path(str(args.mjcf))
    mjm.cam_fovy[:] = 55.4114
    n = args.nworld
    hinge = (mjm.jnt_type == mujoco.mjtJoint.mjJNT_HINGE) & (mjm.jnt_limited == 1)
    adr = mjm.jnt_qposadr[hinge]
    centre = np.clip(mjm.qpos0[adr], mjm.jnt_range[hinge, 0] + SWING, mjm.jnt_range[hinge, 1] - SWING)
    phase = np.arange(len(adr))[None, :] + np.arange(n)[:, None] * 0.7
    qpos = np.tile(mjm.qpos0.astype(np.float32), (n, 1))
    free = mjm.jnt_qposadr[mjm.jnt_type == mujoco.mjtJoint.mjJNT_FREE]
    rng = np.random.default_rng(0)
    for a in free:  # a different cube layout per world
        qpos[:, a: a + 2] += rng.uniform(-0.03, 0.03, size=(n, 2)).astype(np.float32)

    wp.init()
    device = wp.get_device(args.device)
    with wp.ScopedDevice(device):
        base_used = (device.total_memory - device.free_memory) if device.is_cuda else 0
        t0 = time.perf_counter()
        m = mjw.put_model(mjm)
        d = mjw.make_data(mjm, nworld=n)
        rc = mjw.create_render_context(mjm, nworld=n, cam_res=(WIDTH, HEIGHT), render_rgb=True, render_depth=False,
                                       render_seg=False, use_textures=True, use_shadows=args.shadows, render_skybox=True)
        wp.synchronize()
        setup_s = time.perf_counter() - t0
        rgb_adr = rc.rgb_adr.numpy()
        stage = {}

        def frame(number: int, timed: bool) -> np.ndarray:
            t = time.perf_counter()
            qpos[:, adr] = centre[None, :] + SWING * np.sin(0.1 * number + phase)
            d.qpos.assign(qpos)
            mjw.fwd_kinematics(m, d)
            mjw.refit_bvh(m, d, rc)
            if timed:
                wp.synchronize(); now = time.perf_counter(); stage["state + kinematics + refit_bvh"] = stage.get("state + kinematics + refit_bvh", 0) + now - t; t = now
            mjw.render(m, d, rc)
            if timed:
                wp.synchronize(); now = time.perf_counter(); stage["render"] = stage.get("render", 0) + now - t; t = now
            packed = rc.rgb_data.numpy()
            if timed:
                now = time.perf_counter(); stage["read back"] = stage.get("read back", 0) + now - t
            return packed

        packed = frame(0, False)
        wp.synchronize()
        first_s = time.perf_counter() - t0 - setup_s
        assert packed.shape[0] == n, packed.shape
        for number in range(args.warmup):
            frame(number, False)
        wp.synchronize()
        t1 = time.perf_counter()
        for number in range(args.frames):
            packed = frame(number, False)
        wp.synchronize()
        wall = time.perf_counter() - t1
        for number in range(args.frames):
            frame(number, True)

        result = {"nworld": n, "shadows": args.shadows, "device": device.name, "frames": args.frames,
                  "setup_s": round(setup_s, 2), "first_frame_s": round(first_s, 2),
                  "batch_ms": round(1e3 * wall / args.frames, 2),
                  "batches_per_s": round(args.frames / wall, 1),
                  "camera_pairs_per_s_total": round(n * args.frames / wall, 1),
                  "robots_at_30_fps": int(n * args.frames / wall / 30),
                  "stage_ms": {k: round(1e3 * v / args.frames, 2) for k, v in stage.items()},
                  "readback_MB_per_batch": round(packed.nbytes / 2**20, 1)}
        if device.is_cuda:
            result["device_mem_used_MiB"] = round((device.total_memory - device.free_memory) / 2**20)
            result["device_mem_used_before_MiB"] = round(base_used / 2**20)
            result["warp_mempool_high_MiB"] = round(wp.get_mempool_used_mem_high(device) / 2**20)
        print("RESULT " + json.dumps(result), flush=True)

        if args.out:
            from PIL import Image
            args.out.mkdir(parents=True, exist_ok=True)
            for world in {0, n - 1}:
                for cam in range(mjm.ncam):
                    start = rgb_adr[cam]
                    bgra = packed[world, start: start + WIDTH * HEIGHT].view(np.uint8).reshape(HEIGHT, WIDTH, 4)
                    name = mjm.camera(cam).name.replace("_camera", "")
                    Image.fromarray(np.ascontiguousarray(bgra[..., 2::-1])).save(args.out / f"batch{n}_world{world}_{name}.png")


if __name__ == "__main__":
    main()
