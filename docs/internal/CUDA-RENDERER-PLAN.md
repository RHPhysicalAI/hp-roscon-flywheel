<!-- This project was developed with assistance from AI tools. -->
Design plan, 2026-09-20; produced read-only from the code at branch fury and the pinned upstream commits. [A] marks assumptions not verified from code.

# Plan: CUDA renderer node + zero-shot eval rig (tenants mode)

Nothing was edited or run against the host. Upstream sources were read over HTTPS at the pinned commits, without cloning.

Notation for upstream files:
- `demos@4d35` = ros-physical-ai/demos @ 4d3564cd, the sim image pin (`/Users/jary/redhat/git/hp-roscon-flywheel/docker/Dockerfile:39-42`).
- `demos@80dc` = the same repo @ 80dc00cf, the inference image pin (`docker/Dockerfile.gpu-inference:44-47`).
- `rosetta@fb38`, `lrr@e4ed` (lerobot-robot-rosetta) and `so_arm@12fb` (ros2_so_arm) come from `pai.repos` at those pins.
- `gzmj@79de` = francocipollone/gz-mujoco, `sdformat_mjcf` (`tools/host/fury/mjwarp-spike/requirements.txt:14`).
- `mjw@d1a5` = mujoco_warp v3.13.0.
- `ros_gz@kilted` is the branch, not the apt build in the image; claims resting on it are marked [A] where they matter.

Anything I could not verify from code is marked **[A]** and collected in section 4.

## 1. Findings (what is, today)

### 1.1 Camera topics and the bridge

- One `ros_gz_bridge parameter_bridge` node bridges `/clock`, `/wrist_camera/image_raw`, `/wrist_camera/camera_info`, `/static_camera/image_raw` and `/static_camera/camera_info` from gz to ROS (`demos@4d35:pai_bringup/launch/so_arm_gz_bringup.launch.py:171-182`).
- The sensors are declared in `demos@4d35:pai_bringup/urdf/so_arm_gz.urdf.xacro:68-110`:
  - 640x480, `R8G8B8`, `update_rate` 30 (sim time), `horizontal_fov` 1.2217, near clip 0.01.
  - `gz_frame_id` is `wrist_camera_link` / `static_camera_link`.
- The bridge maps `RGB_INT8` to `rgb8`, takes the stamp from the gz header (sim time) and the frame_id from header data (`ros_gz@kilted: ros_gz_bridge/src/convert/sensor_msgs.cpp:134-137`, `std_msgs.cpp:170-174`).
- Publisher QoS is probably the bridge default: reliable, keep_last 10 **[A]**.
- The only consumers in this repo are `src/camera-bridge/camera_bridge.py:55-56` (best_effort, depth 1) and the rosetta contract. Nothing consumes `camera_info`.

### 1.2 Rosetta contract (runtime image)

- `CONTRACT_SRC` is `pai_data_collection/config/rosetta/so_arm101.yaml` (`docker/inference-entrypoint.sh:63`).
- At `demos@80dc`, both image streams are declared at `so_arm101.yaml:9-24`:
  - `sensor_msgs/msg/Image`, `resize: [480, 480]`.
  - `align: {strategy: hold, stamp: header, tol_ms: 100}`.
  - QoS best_effort, keep_last 10.
- Joint states are at `so_arm101.yaml:26-44`: `/joint_states`, six joints selected by name, reliable, depth 50.
- The decoder accepts `rgb8` and `bgr8`, with 4-channel and mono paths as well, then resizes by nearest neighbour (`rosetta@fb38:rosetta/common/decoders.py:71-73,89,152-160,226-227`).
- What the stamp actually does (`rosetta@fb38:rosetta/common/contract_utils.py:148-170`, `lrr@e4ed:lerobot_robot_rosetta/rosetta.py:250-270,331-346`):
  - Under `hold`, the last frame is returned whatever its age. `tol_ms` only applies to `asof`, so it is inert here.
  - `push` drops any stamp older than the last one.
  - A stamp later than the client's clock clears the buffer, and the policy then sees zeros.
- The client clock is wall time. `use_sim_time` is never set (`docker/inference-entrypoint.sh:99-103`; `rosetta@fb38:params/rosetta_client.yaml` has none).
- Consequences:
  - Sim-time stamps (what Gazebo sends today) are always in the past, so they are safe.
  - Image and joint streams are sampled independently; there is no synchronisation at inference.
  - A dead renderer would feed a frozen frame silently, so the rig has to watch frame liveness itself.

### 1.3 Joint states

- `joint_state_broadcaster` runs under `controller_manager update_rate: 50` (`demos@4d35:pai_bringup/config/control/ros2_controllers.yaml:3-5`).
- `/joint_states` is in alphabetical order, so map by name (`src/inference-coordinator/coordinator.py:105-111,427-438`; `src/sim-reset/sim_reset.py:88-97`).

### 1.4 Cube poses, reset and scoring

- Scoring reads gz transport `/world/pai_world/pose/info` through `gz topic -e -n 1` and takes the first position block after the model name (`src/episode-emitter/task_eval.py:27-28,55-57,76-92`).
- Tray test: `task_eval.py:31-39,100-107`. It is used at `coordinator.py:243-252,475-483,715-726`.
- Reset uses the gz service `/world/pai_world/set_pose`, one call per cube, which is a teleport (`src/sim-reset/sim_reset.py:22-26,64-76`).
- The arm is homed over the command topic (`sim_reset.py:145-169`). There is no world reset, so sim time never jumps back (`coordinator.py:458-473`).
- Gz transport crossing between containers of a pod, with `GZ_PARTITION` set, is verified (`tools/host/fury/51-eval-rig.sh:266-276`; D155, `docs/internal/DECISIONS.md:5220-5224`).
- Bridging `pose/info` to `TFMessage` loses the names. `child_frame_id` comes only from the header data, not from `Pose.name` (`ros_gz@kilted: ros_gz_bridge/src/convert/geometry_msgs.cpp:250-263`).

### 1.5 Gazebo model versus the MJCF from `build_mjcf.py`

I checked these numerically from the two source files.

- Joint names are identical, and every axis is `0 0 1` in both models (`so_arm@12fb:so_arm101_description/urdf/so_arm101_macro.xacro:532-680`; `demos@4d35:pai_bringup/mjcf/so_arm101.xml.xacro:120-390`).
- Five joint frames match: four exactly, and `wrist_flex` to 0.05 deg. The gripper is a single hinge with range 0 to 1.70 in both, with no mimic joint.
- `wrist_roll_joint` differs by 2.70 deg about its own axis:
  - The URDF has `rpy="-1.57 3.14 0"` (`so_arm101_macro.xacro:639`).
  - The MJCF body quat is `0.0172091 -0.0172091 0.706897 0.706897` (`so_arm101.xml.xacro:329-330`).
  - Fix: `q_mj = q_gz + 0.0471 rad` for that joint. Without it the wrist camera image is rolled by 2.7 deg.
- Ranges differ between the two models. This is irrelevant, because qpos is set directly.
- Camera extrinsics are equivalent:
  - Same parents and positions, with `R_mj = R_urdf·Rx(π)`. Both check out algebraically.
  - Sources: `cameras.xacro:19-22,38-58` against `so_arm101.xml.xacro:413-414,422-423`.
  - The static camera pose is the launch default (launch `:289-299`), which `docker/entrypoint.sh:34-39` does not override.
- Camera intrinsics differ slightly:
  - Gazebo's horizontal fov of 1.2217 rad at 4:3 gives fovy 55.41 deg, f = 457.0 px.
  - The MJCF has `fovy="55"`, f = 461.0 px, a 0.9% zoom. Set it to 55.4114.
- Cube identity is not in the MJCF names:
  - `sdformat_mjcf` names each body after its link, and every cube's link is named `link`. De-duplication then gives `link`, `link_0`, and so on (`gzmj@79de: .../sdf_kinematics.py:133-141`, `converters/link.py:58-63`, `utils/sdf_utils.py:223+`).
  - Free joints come out as `freejoint`, `freejoint_0`, and so on (`converters/joint.py:71-73`).
  - The exact names are **[A]**: the conversion cannot run on the Mac.
  - Map by nominal position and box size from `demos@4d35:pai_description/world/so_arm_table.sdf:571-667`.
- A free-joint qpos is the world pose, because the model pose equals the link pose for these cubes.

### 1.6 Look

- **Servos.** All 17 URDF visuals use `3d_printed`; `sts3215` is defined but never referenced (`so_arm101_macro.xacro:17-21` and the visuals). The MJCF paints the servo meshes `sts3215` 0.1/0.1/0.1 (`so_arm101.xml.xacro:33,99,137…`). That explains D143's "black servo bodies".
- **World materials.** The converter sets rgba to clamp(0.8·diffuse + 0.4·ambient), which is 1.2x the SDF colour (`gzmj@79de: converters/material.py:73-79`):
  - The tray goes from 0.95 to 1.0, the "white tray".
  - The red cube's R channel clips.
- **Light.** The converter drops `intensity` (1.5) and the spot angles, and keeps the attenuation 0.05/1.2/2.0 (`converters/light.py:33-49`). SDF source: `so_arm_table.sdf:307-322`. MuJoCo then uses its defaults, cutoff 45 and exponent 10 **[A]**.
- **Headlight.** The template adds a headlight with diffuse 0.8 and ambient 0.4, plus a gradient skybox (`demos@4d35:pai_bringup/mjcf/scene_template.xml.xacro:24-25,31-38`).
- **Gazebo side.** Scene ambient 0.4, VCT GI with 3 bounces, and no `<sky>` or `<background>` (`so_arm_table.sdf:10-13,275-290`), so the background is the SDF default grey 0.7 **[A]**.
- **Renderer knobs** (`mjw@d1a5:mujoco_warp/_src/render_util.py:310-340`): `use_shadows`, `shadow_light_fraction` (0.3), `background_color`, `render_skybox`, `samples_per_pixel`, `enable_specular`, `use_ambient_lighting`.
- The renderer honours mat rgba, specular and shininess, and light attenuation, cutoff, exponent and diffuse (`render.py:575-617,746-766`).

### 1.7 Rig settings and baselines

- Rig settings to copy, same as `tools/host/run-coordinator.sh:70-72` (`51-eval-rig.sh:288-293`):
  - `EVAL_MODE=true`, `EVAL_EPISODES`, `EVAL_SEED_BASE`.
  - `RECORD=false`, `EPISODE_LEN=60`, `RESET_ARM=true`.
  - `RANDOMIZE_CUBES=true`, `RANDOMIZE_ONLY=cube_medium`, `RANDOM_RADIUS=0.03`, `RANDOM_YAW_DEG=180`.
- Policy env: `51-eval-rig.sh:249-252`. Pod, netns and partition: `:214-223`. Defaults 5 episodes from seed 1000: `:342`.
- Baselines:
  - D142: 4/5, mean 2.6 cubes (`DECISIONS.md:4677-4678`).
  - Rig: 3/5, mean 2.4, with four of the five seeds identical (`DECISIONS.md:5225-5228`).
- MIG layout: 3g on `0:0` for the assistant, and three 1g.31gb slices (`tools/host/fury/mig-config.sh:6,13`; `fury-mode.sh:23,81-82`).
- The policy has run on `0:1` (`13-first-inference.sh:33,53-57`). The spike ran on `0:3` (`24-mjwarp-spike.sh:20`).
- Spike costs: render 4.6 ms (3.1 ms without shadows), read-back 1.6 ms, kinematics plus refit 0.5 ms (`DECISIONS.md:4774-4776`).

## 2. Design

### 2.1 Renderer node (`src/cuda-renderer/`)

**Files**
- `render_core.py`: pure functions with no ROS or Warp imports.
- `mjw_renderer.py`: lifted from the `Bench` class (`tools/host/fury/mjwarp-spike/render_bench.py:71-135`), with state arrays shaped `(nworld, nq)` from the start.
- `pose_source.py`, `cuda_renderer_node.py`, `build_scene.py`, `look.yaml`, `look_compare.py`.
- `build_scene.py` comes from `build_mjcf.py`, plus fovy 55.4114, the servo material fix, and a `scene_map.json` holding joint qpos addresses, per-joint offsets and the cube mapping. It asserts three free bodies, each matching an SDF cube within 1 mm and in size.
- Tests go in `tests/cuda_renderer/`, following the conftest pattern of `tests/host_runner/conftest.py:13-36`.

**Inputs**
- `/joint_states`: reliable subscription, mapped by name, with the `wrist_roll` offset added.
- Cube poses: gz transport `/world/pai_world/dynamic_pose/info` (or `pose/info`), keyed by model name `cube_small|cube_medium|cube_large`. The gz quaternion xyzw is reordered to MuJoCo wxyz.
  - Source 1: gz Python bindings. `gz_transport_vendor@kilted` installs a PYTHONPATH hook; module names `gz.transport14` / `gz.msgs11` are **[A]**.
  - Fallback: a long-running `gz topic -e --json-output` subprocess **[A]**.
  - Both sit behind one `PoseSource` interface, so a ROS relay can replace them when sims run remotely in the fleet act.
- Nothing else moves. The table and tray are `<static>` (`so_arm_table.sdf:359,457`).

**Tick**
- A 30 Hz wall timer drives it.
- Each tick optionally interpolates joints and slerps cubes to a common sim time `t = min(latest stamps)`. Latest-with-latest can skew the two streams by up to 20 ms, which would make a held cube jitter in the wrist camera.
- Then: `qpos.assign` → `fwd_kinematics` → `refit_bvh` → `render` → packed read-back → BGRA to `rgb8` (`render_bench.py:109-117`) → `array.array('B')`. Passing a numpy array or a list to `msg.data` is the slow path.
- A teleport needs nothing special: state is set every tick, and no dynamics run.

**Publish**
- Topics: `/wrist_camera/image_raw` and `/static_camera/image_raw`, 640x480, `rgb8`, step 1920.
- frame_id: `wrist_camera_link` / `static_camera_link`.
- Stamp: the sim time of the rendered state, non-decreasing (required by `contract_utils.py:148-152`).
- QoS: RELIABLE, KEEP_LAST 10, which matches best_effort subscribers too.
- `camera_info` (fx = fy = 457.02, cx = 320, cy = 240) is optional, since nothing consumes it.
- A `topic_prefix` parameter gives a side-topic mode (`/cuda/...`) for the look comparison.
- The idle bridge in a physics-only sim probably still advertises the image topics **[A]**. That is cosmetic: a second publisher appears in `ros2 topic info`.

**Latency at 30 Hz (33 ms period)**

| Stage | Cost |
|---|---|
| State age | 0-20 ms |
| Interpolation, if on | about +20 ms |
| Kinematics and BVH refit | 0.5 ms |
| Render, both cameras | 4.6 ms |
| Read-back | 1.6 ms |
| Repack to `rgb8` | about 1-2 ms (estimate) |
| Publishing 2 x 0.92 MB over rmw_zenoh | about 2-6 ms (estimate; step 3 measures it) |

- Compute is about 10-15 ms per tick.
- At `samples_per_pixel=2`, render grows to about 18 ms, which still fits one robot.
- With chunks of 100 actions at threshold 0.5, the policy infers roughly once a second, so tens of ms of latency are second-order to the score. The pixels matter more.

**Image and environment**
- `docker/Dockerfile.cuda-renderer`, FROM the sim image, which has the workspace, the gz bindings and the meshes.
- Two stages: the build stage pip-installs `sdformat_mjcf` and `dm-control` and bakes `scene.xml`; the runtime stage installs only mujoco, mujoco-warp, warp-lang and numpy. This keeps pip's protobuf away from `gz.msgs` **[A risk]**.
- A build-time import test, as in `Dockerfile.gpu-inference:59-61`.
- The entrypoint copies the Zenoh client block of `docker/inference-entrypoint.sh:35-48`.
- The Warp kernel cache sits on a named volume, because the first compile takes about 7 s.
- The image is built natively on the host, like `tools/host/fury/11-sim-build.sh:24-25`.

### 2.2 Sim without rendered cameras

- The smallest change is a world variant without the `gz-sim-sensors-system` block (`so_arm_table.sdf:275-290`), passed through the existing `world_file` launch argument (launch `:265-269`).
- The world name must stay `pai_world`. The launch file derives it from the file (launch `:40-46,142`), and `task_eval` and `sim_reset` hard-code it.
- Keep the other three plugins, so that whatever loads Physics today stays untouched. The world lists no Physics plugin at `:291-302`, and the mechanism is **[A]**.
- For the rig, no image change: generate the stripped file from the image's own copy, and bind-mount it over `/ws_pai/install/pai_description/share/pai_description/world/so_arm_table.sdf` (path **[A]**).
- Durable form: `SIM_CAMERAS=off` in `docker/entrypoint.sh:34-39`, which generates the variant and adds `world_file:=`.
- The physics-only sim container needs no `--device` and no `NVIDIA_DRIVER_CAPABILITIES`.
- Proof that nothing renders: `/root/.gz/rendering/ogre2.log` is absent, and the real-time factor is about 1 (the pattern of `tools/host/fury/22-sim-scale.sh`).

### 2.3 Look tuning

**Knobs**
- All knobs live in `look.yaml`. It is applied to the MjModel arrays and the render-context kwargs after compile, so tuning needs no MJCF rebuild. Its hash goes into the eval record.
- Suggested starting values:

| Knob | Start |
|---|---|
| `sts3215` rgba | same as `3d_printed` |
| World `mat_rgba` | divide by 1.2 |
| Arm `mat_specular` | about 0 |
| Headlight diffuse | 0 |
| Headlight ambient | 0.4 to 0.6 (stands in for GI) |
| `light_diffuse` | x1.5 |
| Light cutoff | about 86 deg |
| Light exponent | about 1 |
| `shadow_light_fraction` | 0.5 to 0.6 |
| `render_skybox` | False |
| `background_color` | 0.7 grey |
| `samples_per_pixel` | 1 or 2 |
| Per-channel gamma/gain LUT on the CPU frame | optional; the renderer's output is linear, ogre2's is sRGB **[A]** |

**Procedure** (flywheel mode, MIG off)
- The renderer follows a running sim on `/cuda/...`.
- `look_compare.py` pairs frames by header stamp, within 1/60 s, and saves calibration sets: `{gz,mjw}_{static,wrist}.png` plus `state.json`, at home and at 3-4 arm poses.
- No-judgment statistics:
  - Per-channel mean and std, a 32-bin histogram intersection, and a 1-D Wasserstein distance.
  - Per-object mean RGB over the renderer's own segmentation masks (`render_seg`) applied to both images. This gives direct per-material targets.
  - Cube colour-blob centroid error in px, and arm-mask IoU. These validate extrinsics, fovy, the wrist offset and the cube mapping in one go.
- Proposed targets:
  - Channel means within ±8/255.
  - Histogram intersection ≥ 0.85.
  - Cube centroid error ≤ 2 px.
  - Arm-mask IoU ≥ 0.9.
- Side-by-side and abs-diff PNGs go to a human.
- `:8081/<cam>/snapshot` (`src/camera-bridge/camera_bridge.py:95-98`) is JPEG q60 with no stamp, so use it for eyeballing only.
- Once the calibration states are saved, tuning runs offline on any CUDA device.

### 2.4 Zero-shot eval rig

New script `tools/host/fury/28-eval-rig-cuda.sh` (name is a suggestion), copied from `51-eval-rig.sh`.

**Preflight and isolation**
- Preflight is inverted: MIG must be Enabled, and both slice CDI names must exist (the `fury-mode.sh:81` pattern). The slices should be idle.
- Pod `eval-rig-cuda`, bridge network, nothing published. Port 7447 is private to the pod's netns, as at `51:214`.
- `GZ_PARTITION=evalrigcuda` on all four containers.
- Its own lock file.
- Runs as root, like 51, because the images live in root's storage (`51:199-204`). Rootless with CDI is possible later.
- One-line addition to `fury-mode.sh:41` for the new pod name.

**Containers, in order**
- Sim: no GPU, stripped world, `CURATOR_URL=`.
- Renderer on `nvidia.com/gpu=0:3`.
- Policy on `nvidia.com/gpu=0:1`, env identical to `51:249-252`.
- Coordinator, env identical to `51:288-293`.

**Why two slices**
- They are hardware-isolated, so inference bursts cannot jitter the render, and the score measures the pixels.
- It mirrors act 2's layout, with a rendering tenant and a serving tenant. Both placements are already proven.
- `0:2` stays free for training.
- Memory numbers: the repo has none beyond the ~200 MB checkpoint (`DECISIONS.md:964`). The policy's footprint is printed by `13-first-inference.sh:80` into a host-side log, and the renderer's is unmeasured **[A]**. Neither should be close to 31 GB.

**Gates before any episode**
- `camera_bridge` inside the sim container already subscribes to the same topics, so `both_cameras` and `frames` (`51:174-186,231-240`) work unchanged. They prove topic, type, encoding and ~300 frames per 10 s through the rig's own router.
- The `GL_VENDOR` gate (`51:237-242`) is replaced by three checks: the renderer logs a MIG device, `ogre2.log` is absent, and the real-time factor is ≥ 0.95.
- One PNG per camera is saved into the work directory.
- The checks for 3 cube poses and for a single `/run_policy` server (`51:266-282`) are kept.

**Records**
- Records go to `/data/flywheel/eval-cuda/` only, never `/data/flywheel/eval/`. The training runner reuses records whose seeds match (`51:39,314-316`), so a CUDA-pixel record must not reach the gate.
- A `renderer` block is merged in with jq: look hash, spp, shadows, measured fps, versions. `coordinator.py` stays unchanged.
- Run `modelcar 5 1000`. Also run n=10 on both rigs, because `51-eval-rig.md:78-79` says five is noisy. Accepting the result is the operator's call (D143 addendum 2).

## 3. Work breakdown

Off = needs no Fury. Host = needs the Fury.

| # | Step | Testable outcome | Where |
|---|---|---|---|
| 0 | Host facts | Python imports of gz transport/msgs. Rate and names on `dynamic_pose/info`. `ros2 topic info -v` on a camera topic. `/joint_states` stamp against sim time. Installed SDF path. Body names after conversion. GPU MiB of policy and spike. | Host, 0.5 d |
| 1 | `render_core` and tests | qpos build (order, offset, quaternion reorder). Interpolation. `rgb8` packing (bytes, step, upright). CameraInfo from fovy. Look overrides. Cube matching. | Off, 1 d |
| 2 | `build_scene` and FK equivalence | Test fixtures are the six URDF origins, against MuJoCo FK of the arm MJCF (it compiles on a Mac, `mjwarp-spike/README.md:15`). `gripper_link` and `jaw_link` within 1 mm / 0.1 deg over random poses. The test fails without the wrist offset. | Off; conversion on host. 1 d |
| 3 | Node and image | `ros2 topic hz` ≥ 30 on both topics with a moving arm. Correct encoding, frame_id and sim stamps. PNG saved. Tick ms logged. 20-minute soak with flat GPU memory. | Host (or the x86 desktop **[A]**), 1-1.5 d |
| 4 | Physics-only sim | No `ogre2.log`, RTF ≈ 1, 50 Hz joints, cubes at rest, controllers active. | Host, 0.5 d |
| 5 | Look capture and tuning | Statistics JSON plus PNGs. Cube centroid ≤ 2 px (geometry first), then the colour targets. | Capture on host, MIG off; fitting Off. 1-2 d, timeboxed |
| 6 | Rig variant | Dry run passes every gate. Five seeds from 1000, record in `eval-cuda/`. | Host, tenants mode, 1-1.5 d |

Effort:
- About 6-8 engineer-days in total.
- About 3-4 days to a first number. That path bind-mounts the source and pip-installs at start like the spike, with no interpolation and one look pass.

**Risks, with a cheap early test for each**

1. The domain gap survives tuning.
   - Before the node exists, run the checkpoint on paired Gazebo and renderer frames of the same calibration states. Use rosetta's own `decode_ros_image` for preprocessing.
   - Compare the action chunks (L2 in degrees). Half a day; it doubles as a tuning metric.
2. Kinematic or extrinsic mismatch. Covered by the FK test in step 2 and the mask and centroid checks in step 5.
3. Cube identity and the pose source. Step 0 one-liners decide it. Fallbacks: the JSON CLI stream, or a `PosePublisher` plugin plus bridge in the world variant.
4. rclpy throughput for 55 MB/s of images.
   - Test: a dummy-publisher microbenchmark in step 3.
   - Fallback: publish `gz.msgs.Image` on the gz camera topics and let the existing bridge (launch `:171-182`) emit the ROS messages. That is the same publisher and QoS as today.
5. Behaviour of the physics-only world. Step 4.
6. pip shadowing numpy or protobuf. Two-stage image plus a build-time import test.
7. Warp under MIG over a long run. The step 3 soak.
8. Tuning becomes a time sink. Timebox it; D143 addendum 2 defines the decision.

## 4. Assumptions not verified from code

- The bridge's default publisher QoS, and whether it advertises idle topics eagerly.
- SceneBroadcaster pose topics: the 60 Hz default, the content of `dynamic_pose/info`, and whether per-pose header data is absent.
- The gz Python module names, and whether they are present in the image.
- The `--json-output` flag of `gz topic`.
- The converted body and joint names, and the material numbering.
- MuJoCo's light defaults (cutoff 45, exponent 10), and SDF's default background of 0.7.
- The renderer's output is linear, with no gamma.
- What loads Physics for a world that does not list it.
- The installed SDF path, in particular colcon isolated install.
- How the URDF colour maps to Gazebo's ambient and diffuse.
- GPU memory of the policy and of the renderer.
- Whether `/joint_states` stamps are sim time.
- Nothing sits inside Gazebo's 1 cm near clip on the wrist camera.
- The `ros_gz` `kilted` branch equals the apt build in the image.
- The x86 desktop (`AGENTS.md:19-20`) is still usable.

### Critical Files for Implementation
- /Users/jary/redhat/git/hp-roscon-flywheel/tools/host/fury/mjwarp-spike/build_mjcf.py
- /Users/jary/redhat/git/hp-roscon-flywheel/tools/host/fury/mjwarp-spike/render_bench.py
- /Users/jary/redhat/git/hp-roscon-flywheel/tools/host/fury/51-eval-rig.sh
- /Users/jary/redhat/git/hp-roscon-flywheel/docker/entrypoint.sh
- /Users/jary/redhat/git/hp-roscon-flywheel/docker/inference-entrypoint.sh
