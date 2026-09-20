<!-- This project was developed with assistance from AI tools. -->
# The fleet's rendering tenant — operator page

In tenants mode slice `nvidia.com/gpu=0:3` (1g.31gb) draws the fleet (D163, D164): every robot's world sends its
state — six joints, three cube poses — to `10.20.0.1:9701` over UDP, MuJoCo-Warp ray-traces both cameras of all
of them in one batch per tick, and `10.20.0.1:9702` serves the pictures: `/wall.mjpg` (a labelled mosaic of every
robot's overhead camera), `/robot/<id>/static|wrist.mjpg|jpg`, `/status`, `/healthz`, and a small page at `/`.
No ROS, no Gazebo; a robot that goes quiet for 5 s is greyed at its last picture and leaves the wall after 60 s.
The contract with the worlds: `docs/internal/FLEET-RENDER-CONTRACT.md`. Pieces: `src/fleet-renderer/`,
`docker/Dockerfile.fleet-renderer`, `flywheel/fleet-renderer.container` with `fleet-renderer-cache.volume`,
`73-fleet-renderer-install.sh`, and `fury-mode`, which owns starting and stopping it.

## Bring-up, once

The command blocks are meant to be pasted as they are.

```
cd ~/flywheel-setup
./73-fleet-renderer-install.sh build      # minutes, needs the uplink; ends by rendering a frame on the CPU
./73-fleet-renderer-install.sh install
```

In tenants mode `install` starts the unit, waits for `/healthz` — the first start on a slice compiles every
kernel, minutes; later starts load them from the `fleet-renderer-cache` volume — and only then lists `9701/udp`
and `9702/tcp` for the guests in the `libvirt-to-host` policy. In flywheel mode it installs, starts nothing and
leaves the ports closed: run `install` again after `fury-mode tenants`. Once per hub, from the laptop:
`oc apply -f tools/hub/manual/fleet-renderer-endpointslice.yaml` (Service and Route come with the `flywheel` app).

## At demo time: nothing

`fury-mode tenants` (from a laptop: `tools/hub/fury-switch.sh tenants`) starts it, every switch stops it first, a
boot in tenants mode brings it back, in flywheel mode the unit skips its own start. After a change to the unit
or a new image: `install` again (it restarts the unit).

## Judging a run

```
./73-fleet-renderer-install.sh status
```

| Line | Good |
|---|---|
| `device` | the slice, e.g. `NVIDIA … MIG 1g.31gb (cuda:0)` — not `cpu` |
| `render rate` | the target (15) with robots sending; `late ticks` not climbing |
| `ms per batch` | D163 measured about 4.3 ms a robot at 640x480 with shadows; 480 px should be under that. 20 robots must stay under 66 ms |
| `robots` | as many live as there are worlds, `datagrams/s` near 30 each, `dropped` flat |
| `pictures` | `the wall mosaic` under 66 ms each, or the wall shows fewer frames than are rendered |

Without any world, twenty robots for two minutes (arms moving, one cube sliding):

```
sudo podman exec fleet-renderer python3 fake_worlds.py --addr 10.20.0.1:9701 --robots 20 --seconds 120
```

Look at it: `http://10.20.0.1:9702/` on the tailnet, `ssh -N -L 9702:10.20.0.1:9702 <user>@<host>` without, the
Route `fleet-wall` on the hub. If the slice cannot hold the rate: `RENDER_SIZE`, `RENDER_FPS`, `RENDER_SHADOWS`
(`Environment=` lines of the unit, then `install`). Only robots that are sending are rendered.

## What it costs beside the slice

JPEGs, on the CPU, and only while somebody watches: a picture is encoded once per frame however many watch.
Measured on this host's CPU in this image: the mosaic of 20 robots (1920x1536) 56 ms — 0.84 of a core at 15
frames a second, never more than one; a robot's own stream 2.9 ms a frame. The unit's `CPUQuota=400%` and
`MemoryMax=8G` are the wall around it; after a run `systemctl show -p MemoryPeak fleet-renderer`, and tighten.

## When it fails

| Symptom | Cause | Do |
|---|---|---|
| `inactive` in tenants mode, journal: `CDI device nvidia.com/gpu=0:3 is not there` | the CDI spec is older than the slices | `sudo systemctl restart nvidia-cdi-refresh.service`, then `sudo systemctl start fleet-renderer.service` |
| journal: `cannot listen on udp 10.20.0.1:9701 …`, restarting every 15 s | `virbr-fury` is not up yet, or something else holds the port | wait for libvirt; `sudo ss -lunp 'sport = 9701'` |
| journal: a CUDA or Warp error at the first start; `/status` never leaves `warming up` | the image's first start on a slice as uid 1001 without capabilities is not verified | comment `DropCapability=all` and `NoNewPrivileges=true` in the unit, `install`; if that is it, say so in `DECISIONS.md` |
| `install` times out waiting for `/healthz`, the journal shows Warp compiling | a slow first compile | `WAIT_S=1800 ./73-fleet-renderer-install.sh install` |
| tiles grey although the worlds run | the states do not arrive: `dropped` in `status` names why; nothing accepted at all is the port | `status` shows the ports; from a pod `10.20.0.1:9701/udp` must be the target |
| `dropped` shows `over_capacity` | more robots than `RENDER_MAX_ROBOTS=24` | raise it in the unit, `install` |
| the wall stutters, `render rate` is fine | the mosaic takes longer than a tick, or `CPUQuota` is reached by many viewers | fewer viewers of single streams; `RENDER_JPEG_QUALITY` down from 80 |

`./73-fleet-renderer-install.sh remove` closes the ports and removes the unit; the kernel cache and the image stay.

## Not verified before the first run on the slice

Everything above ran end to end on Warp's CPU device, in this image, rootless (a rootless container cannot
open the GPU on this host). On the slice, still to see: CUDA under uid 1001 with every capability dropped; the
time of the first kernel compile; the render rate at 480 px (D163's 228 pairs a second were 640x480 with the
spike's loop — this service's loop is that loop plus rendering only the first n worlds of a fixed allocation,
checked against a full batch on the CPU device at every image build); memory; and the router carrying
`/wall.mjpg` at about 20 Mbit/s a viewer.
