<!-- This project was developed with assistance from AI tools. -->
# mjwarp-spike: the real SO-ARM101 scene rendered with CUDA only

Spike, not production. `build_mjcf.py` rebuilds upstream's MuJoCo scene (ros-physical-ai/demos @ 4d3564cd: arm xacro + SDF world via `sdformat_mjcf` + scene template) as one MJCF. `render_bench.py` renders both 640x480 cameras with the MuJoCo-Warp batch renderer (ray casting in CUDA kernels, no OpenGL/Vulkan/EGL), moves the arm every frame, copies RGB to the host as uint8 and reports frame pairs per second.

Run in a throwaway container of the sim image with one CUDA device and this directory mounted (`podman run --rm -it --device nvidia.com/gpu=0:3 -v "$PWD":/spike:z --entrypoint bash localhost/soarm-sim:arm64`):

    source /opt/ros/$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash; cd /spike
    pip install --no-cache-dir --break-system-packages -r requirements.txt
    python3 build_mjcf.py /tmp/mjw/scene.xml
    python3 render_bench.py /tmp/mjw/scene.xml --out /spike/out

Expect: `build_mjcf.py` lists `static_camera` and `wrist_camera` at 640x480, 13 meshes, geoms in groups 0/2/3. `render_bench.py` prints Warp's per-module compile times, setup and first-frame seconds, then frame pairs/s and per-stage ms for two read-back paths (`get_rgb`, and the packed `rc.rgb_data` buffer, which is already bgra8), and writes `out/static_camera.png` and `out/wrist_camera.png`. Compare cost with `--no-shadows`; `--no-textures` also drops the skybox.

Verified on a Mac (MuJoCo 3.13.0, mujoco-warp 3.13.0, Warp 1.17.0 CPU device): the upstream arm xacro with its real STL meshes composes, flattens and compiles; the whole benchmark runs; images are upright and show arm meshes, shadows and skybox; both read-back paths give identical pixels.

Since run on the target: the whole thing inside a MIG 1g slice — 152 camera pairs a second with shadows (D143 addendum). At the time of writing NOT verified: the `sdformat_mjcf` conversion (no Gazebo bindings on the Mac, so the world in that test was hand-written); the pip install on aarch64.

Risks to check on the target:
- `python3 -c "import sdformat, gz.math"` must work after sourcing ROS. Read from source only: kilted's sdformat_vendor / gz_math_vendor build the bindings and add an unversioned shim to PYTHONPATH.
- The image clones upstream at HEAD, not 4d3564cd; `build_mjcf.py` stops with the missing path if the layout moved.
- Warp textures (`wp.Texture2D`, used for the skybox) are untested under MIG: if context creation fails, retry with `--no-textures`.
- Meshes, textures, spot lights and shadows are supported by the renderer; the arm is 322k triangles, which is what costs. It is a single-hit ray caster (Lambert + specular, no reflections, no anti-aliasing at 1 sample per pixel), so images will not match the Gazebo/ogre2 or MuJoCo OpenGL images a policy was trained on.
- The SDF world has no meshes, so nothing needs copying; the arm STLs are read from the installed `so_arm101_description` via an absolute `meshdir`, so the MJCF only loads inside the image.
- PyPI has aarch64 wheels usable on python 3.12 for mujoco 3.13.0, warp-lang 1.17.0, labmaze and dm-tree (checked). pip's numpy and lxml shadow the apt copies ROS uses: keep the container throwaway.
