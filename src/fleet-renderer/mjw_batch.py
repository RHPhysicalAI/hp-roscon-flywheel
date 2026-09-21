# This project was developed with assistance from AI tools.
"""MuJoCo-Warp batch renderer: a fixed number of worlds allocated once, the first n of them rendered per call."""

# The loop is the measured one of tools/host/fury/mjwarp-spike/bench_batch.py: qpos of shape (nworld, nq) ->
# kinematics -> refit_bvh -> render -> copy the packed uint32 pixels to the host.
#
# The renderer is compute bound at a few milliseconds per world, whatever the batch size, so worlds nobody
# is driving must not be rendered. Every mujoco_warp kernel used here is launched over d.nworld, a plain
# attribute of the data object: render() lowers it to the number of live robots for the call and restores it.
# That leans on mujoco-warp==3.13.0's internals - the pin is exact, and selftest() compares a partial batch
# against a full one, at image build time too.

from pathlib import Path

import mujoco
import mujoco_warp as mjw
import numpy as np
import warp as wp


class BatchRenderer:
    """Model, data and render context on one Warp device. Use it from one thread."""

    def __init__(self, scene_xml: Path, capacity: int, size: int, shadows: bool, device: str) -> None:
        self.capacity, self.size = capacity, size
        mjm = mujoco.MjModel.from_xml_path(str(scene_xml))
        self.nq, self.ncam = mjm.nq, mjm.ncam
        self.camera_names = [mjm.camera(i).name for i in range(mjm.ncam)]
        # host copy of every world's qpos; the caller writes rows 0..n-1 before render(n)
        self.qpos = np.tile(mjm.qpos0.astype(np.float32), (capacity, 1))
        wp.config.quiet = True
        wp.init()
        self.device = wp.get_device(device)
        self.device_name = f"{self.device.name} ({self.device.alias})"
        with wp.ScopedDevice(self.device):
            self.m = mjw.put_model(mjm)
            self.d = mjw.make_data(mjm, nworld=capacity)
            self.rc = mjw.create_render_context(mjm, nworld=capacity, cam_res=(size, size), render_rgb=True,
                                                render_depth=False, render_seg=False, use_textures=True,
                                                use_shadows=shadows, render_skybox=True)
            self.rgb_adr = [int(a) for a in self.rc.rgb_adr.numpy()]
            wp.synchronize()

    def render(self, n: int) -> np.ndarray:
        """Render worlds 0..n-1 from self.qpos; returns their packed pixels, shape (n, ncam * size * size)."""
        if not 1 <= n <= self.capacity:
            raise ValueError(f"n={n} outside 1..{self.capacity}")
        with wp.ScopedDevice(self.device):
            self.d.qpos.assign(self.qpos)
            self.d.nworld = n
            try:
                mjw.fwd_kinematics(self.m, self.d)
                mjw.refit_bvh(self.m, self.d, self.rc)
                mjw.render(self.m, self.d, self.rc)
            finally:
                self.d.nworld = self.capacity
            return self.rc.rgb_data[:n].numpy()

    def selftest(self) -> None:
        """A partial batch must draw world 0 exactly as a full batch does, and draw something."""
        self.qpos[:] = self.qpos[0]
        full = self.render(self.capacity)[0].copy()
        part = self.render(1)[0]
        if not np.array_equal(full, part):
            raise RuntimeError("a partial batch differs from a full one: mujoco_warp no longer launches over d.nworld")
        rgb = full.view(np.uint8).reshape(-1, 4)[:, :3]
        if len(np.unique(rgb, axis=0)) < 16:
            raise RuntimeError("the rendered frame is flat: fewer than 16 colours")
