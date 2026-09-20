<!-- This project was developed with assistance from AI tools. -->
# Fleet rendering tenant: the contract between the robots' worlds and the renderer

Stage B of the fleet tenant (D163, D164). Two halves are built separately against this page: the **worlds**
(physics-only sims, pods on the hub) and the **rendering tenant** (one process on MIG slice `0:3` of the GPU host).
Nothing here is about the policy; stage C adds the frames' path to each robot's computer.

## Shape

- A world knows its robot's state: six arm joint positions and three cube poses. It sends that state to the
  renderer many times a second. It never receives anything back in stage B.
- The renderer keeps the latest state of every robot it has heard from, ray-traces both cameras of all of them in
  one batch per tick with MuJoCo-Warp, and serves pictures over HTTP: a mosaic of every robot's overhead camera for
  the fleet wall, and each robot's two cameras on their own.
- The renderer has **no ROS and no Gazebo in it**: state in over UDP, pictures out over HTTP. The worlds have no
  CUDA in them. Either half can be restarted without the other noticing more than a stale tile.

## State: world -> renderer

One UDP datagram per robot per update, to `RENDER_STATE_ADDR` (default `10.20.0.1:9701`), UTF-8 JSON, one object,
at most 1200 bytes:

```json
{"v": 1, "robot": "r07", "t": 1234.567,
 "q": {"shoulder_pan_joint": 0.0, "shoulder_lift_joint": 0.0, "elbow_flex_joint": 0.0,
       "wrist_flex_joint": 0.0, "wrist_roll_joint": 0.0, "gripper_joint": 0.0},
 "cubes": {"cube_small": [x, y, z, qw, qx, qy, qz],
           "cube_medium": [x, y, z, qw, qx, qy, qz],
           "cube_large": [x, y, z, qw, qx, qy, qz]}}
```

- `robot`: `r` + two digits, the same number as the robot's computer `fleet-vm-NN`; `r00` is robot zero (D164).
- `t`: the world's sim time in seconds. The renderer ignores a datagram whose `t` is older than the last one it
  accepted from that robot, unless it is more than 5 s older (a restarted world starts again from zero).
- `q`: radians, **Gazebo's values as `/joint_states` reports them**, keyed by joint name - the joint names are
  whatever `/joint_states` carries for the six arm joints (the sender must not rename them; the renderer maps names
  to the MuJoCo model and applies the wrist-roll offset of +0.0471 rad itself).
- `cubes`: world poses, metres, quaternion in **w, x, y, z** order (the sender converts from Gazebo's x, y, z, w).
- Send rate: 30 a second is plenty; the renderer uses the latest and never queues. Loss is fine. A robot not heard
  from for 5 s is drawn greyed out at its last state; after 60 s it leaves the wall.
- Unknown keys are ignored; a datagram that does not parse, or has another `v`, is counted and dropped.
  So is one that lacks a joint or a cube, carries a non-finite number or a zero quaternion. The wall shows live
  robots and, greyed, stale ones.

## Pictures: renderer -> anyone

HTTP on `RENDER_HTTP_ADDR` (default `10.20.0.1:9702`), no authentication, read-only:

| Path | What |
|---|---|
| `GET /wall.mjpg` | `multipart/x-mixed-replace` MJPEG: one mosaic of every live robot's overhead (`static`) camera, labelled with the robot id, grid sized to the fleet, up to 1920 px wide |
| `GET /robot/<id>/static.mjpg`, `/robot/<id>/wrist.mjpg` | one robot's camera as MJPEG |
| `GET /robot/<id>/static.jpg`, `/robot/<id>/wrist.jpg` | the latest frame, once |
| `GET /status` | JSON: `robots[{id, state, age_s, datagrams_per_s, datagrams}]`, `robots_live`, `robots_stale`, `render_fps`, `fps_target`, `batch_ms`, `batch_worlds`, `idle_batch_ms`, `render_size`, `shadows`, `device`, `max_robots`, `dropped{total, <reason>}`, `overruns`, `streams`, `jpegs_per_s`, `wall_ms`, `phase`, `last_frame_age_s` |
| `GET /healthz` | 200 when the render loop has produced a frame in the last 2 s. With no robot live it renders one world at rest twice a second, so it is healthy before any world exists |
| `GET /`, `GET /wall.jpg` | a small page with the mosaic and the status table; the mosaic once |

Defaults (D163's measurement: about 228 camera pairs a second per 1g.31gb slice at 640x480): render **480x480**,
**15 frames a second**, shadows on - about twenty robots from one slice. `RENDER_SIZE`, `RENDER_FPS`,
`RENDER_SHADOWS` change them; `RENDER_DEVICE` (default `cuda:0`) and `RENDER_MAX_ROBOTS` (default 24: the batch is
allocated once, and a robot beyond it is dropped and counted as `over_capacity`) size it. The output is converted from the renderer's linear colour to sRGB before encoding.

## Network

The worlds are pods on the hub (`10.20.0.10`, traffic leaves as the node); the renderer listens on the host's
hub-side address only. The host lists `9701/udp` and `9702/tcp` for guests in the `libvirt-to-host` policy
(pattern: `tools/host/fury/15-camera-port.sh`). Browsers reach the pictures through the hub's https Route in front
of a Service whose one endpoint is `10.20.0.1:9702` (pattern: D159; the EndpointSlice is applied by hand because
Argo CD does not manage EndpointSlices).
