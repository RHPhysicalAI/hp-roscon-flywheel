<!-- This project was developed with assistance from AI tools. -->
# Robot zero — operator page

In tenants mode slice `nvidia.com/gpu=0:1` (1g.31gb) serves the GPU host's **own policy**: the Fleet's signed
`act-inference` application, the one promoted in act 1, **placed on that slice by RHEM** through the device's
`gpu_device` label. Nothing about the policy is a host unit of ours. What the host adds is the robot around it,
four units with no GPU in them:

| Unit | What |
|---|---|
| `robot-zero-sim` | the physics-only world (the fleet worlds' signed image, `SIM_CAMERAS=off`), the Zenoh router the policy connects to, and the state forwarder that reports to the rendering tenant as `r00` |
| `robot-zero-frames` | fetches `r00`'s two pictures from `10.20.0.1:9702` and publishes them as `/static_camera/image_raw` and `/wrist_camera/image_raw`, 640x480 `rgb8`: the 480x480 picture centred and padded, never stretched (`src/robot-zero/`) |
| `robot-zero-episodes` | the flywheel's coordinator with `RECORD=false`: homes the arm, re-places the cubes, runs the policy for up to a minute, repeats |
| `robot-zero-emitter` | the image's own episode emitter, listening only: one summary per episode (a few KB of JSON, no bag behind it) posted to the **show curator** on the hub, `http://10.20.0.10:30812/episode` — the live lane of the flywheel page |

`robot-zero-sim` is the handle: starting it starts the other three, stopping or restarting it takes them along, and
whenever it has (re)started it restarts the policy **if that is running** (the policy's action server does not
survive a restart of the router). `fury-mode` owns all four like the other tenants.

**Robot zero records nothing.** It sees the rendering tenant's pixels, not Gazebo's. No unit mounts anything of
`/data`, none has a credential or the pipeline's addresses, and the episode recorder is not started. `install`
checks all of that on the files it installs (`./74-robot-zero-install.sh check` runs the same rules anywhere),
together with the hardening every unit carries — read-only root, no capabilities, no new privileges — and it
refuses any key that could undo either (`PodmanArgs=`, `Mount=`, `Secret=`, a second `RECORD=` line, ...).

**Judged, not kept: the one address.** The flywheel page tells its story live from robot zero's episodes, so they
are judged — by a second deployment of the curator's own code on the hub, `curator-show`
(`gitops/flywheel/curator-show.yaml`): the real gates (they judge physics, not pixels), a throwaway volume, no
sync agent, no object storage, no Kafka, no way out of its pod. The emitter's unit carries that curator's address
and `install` holds it to exactly that line: the flywheel's own curator (port 30802), a second address of any
kind, or that line in any other unit is refused, as is a fallback directory outside the container's tmpfs or
anything but the image's own emitter as the command. An address can also be reached without naming one, so no
unit may mention a proxy (the emitter's HTTP client and podman both honour proxy variables; the emitter's unit
switches them off with `no_proxy=*`), no line may be continued onto the next or quote an `Environment=` value
(the rules read lines; systemd joins and unquotes them), and `install` looks once more at what quadlet generated
from the emitter's file — one `CURATOR_URL`, no other address, no proxy, nothing inherited — before it installs
anything (`./74-robot-zero-install.sh check-generated <file>` runs that rule on a saved `quadlet -dryrun`). On the
hub side only this host's address (`10.20.0.1`) is let in to that port, records are validated and bounded, and the
page escapes what it shows. The emitter moves nothing and resets nothing — the episode loop does that, as in
flywheel mode, where the two run side by side the same way.

**The flywheel's recorder stays out of tenants mode.** Until robot zero there was no Zenoh router on this host
under MIG, so a stray `systemctl start act-coordinator` did nothing. Now there is one, with a policy and camera
topics behind it: that recorder would write bags of ray-traced pixels into `/data/flywheel/bags`. It carries its
own start condition now (MIG off, like `so-arm-sim`), `install` refuses a checkout in which it does not, reinstalls
that one unit file where it is already installed, and `fury-mode` stops it on every switch.

## Bring-up, once

The command blocks are meant to be pasted as they are. Host, with the uplink (about 5 GB; the host's policy
verifies the signature):

```
sudo podman pull quay.io/jary/soarm-flywheel@sha256:179cbedc5a65759030c2a7cb58b64e952eb50aba7ca421fd7fe57176e2029fe4
```

`src/robot-zero/` has to be in the checkout on the host (`~/hp-roscon-flywheel`, or `SRC=<checkout>`), and the
current `fury-mode.sh`, `74-robot-zero-install.sh`, `flywheel/robot-zero-*.container` (four files),
`flywheel/act-coordinator.container` and `flywheel/so-arm-sim.container` in `~/flywheel-setup`. The hub side of
the live lane (`curator-show`, synced by Argo CD from `gitops/flywheel/`) should be up first; without it the
emitter says `POST to curator failed` after each episode and nothing else happens.

Pre-flight, host: the hand-installed policy unit from before enrolment must be gone. `so-arm-sim`'s MIG check is a
start condition now, and a condition that fails no longer holds back a unit that `Requires=` the sim — that unit
was the only one, and it takes the whole GPU. Expect `No such file` (`install` refuses otherwise):

```
ls /etc/containers/systemd/act-inference.container
```

```
cd ~/flywheel-setup
./74-robot-zero-install.sh install
```

In tenants mode `install` starts the four units and waits until the renderer shows `r00` live and the frames
flow; in flywheel mode it installs and starts nothing. Run again on a host that already has robot zero, it is
how the fourth unit gets in: the world restarts once, and the policy with it. Then, from a laptop logged in to the hub:

```
FURY_SSH=<user>@<host> tools/hub/robot-zero.sh up
```

That names slice `0:1` in the device's `gpu_device` label, waits until RHEM has rendered
`AddDevice=nvidia.com/gpu=MIG-…` into the device's spec and the agent has applied it, and only then starts the
policy. It refuses to start a policy whose quadlet still says `all`: with MIG on that would land on the
assistant's slice.

## At demo time: nothing, or one of these

| Want | Do |
|---|---|
| robot zero after a mode switch or a reboot | nothing: `fury-mode tenants` starts the host side, a boot in tenants mode brings it back, `tools/hub/fury-switch.sh tenants` ends with `robot-zero.sh up` |
| reset robot zero alone | laptop: `tools/hub/robot-zero.sh reset` (sudo password once) — host: `fury-mode zero`. No other tenant is touched |
| see it | laptop: `tools/hub/robot-zero.sh status` (no sudo) — host: `./74-robot-zero-install.sh status` |
| take the policy off the slice | laptop: `tools/hub/robot-zero.sh down`. `r00` stays on the wall, arm at rest |

Order of a full switch, which `fury-switch.sh` keeps: **to flywheel** — stop the policy, remove the label, wait for
the whole-GPU quadlet, `fury-mode flywheel`, start the policy. **To tenants** — stop the policy, `fury-mode
tenants`, set the label, wait for the slice's quadlet, start the policy. The policy is always stopped first and
started last, and its placement only ever changes while it is stopped.

## Judging it

```
./74-robot-zero-install.sh status
```

| Line | Good |
|---|---|
| the four units | `active` |
| `policy (rhem-managed)` | `active/running` |
| `the policy's unit names` | `nvidia.com/gpu=MIG-…`, the UUID `nvidia-smi -L` shows for `Device  1` — never `all` while MIG is on |
| `r00 as the renderer sees it` | `live`, near 30 states a second |
| the frames | `15.0 frames/s a camera … live`, few failed fetches |
| the episode loop | `Signaled: start` / `Cancelling goal` cycling; `Waiting for action server...` means the policy is not up |
| the episode reporter | `Episode … -> SUCCESS` or `FAIL`, `cubes=n [k total]` after each episode, and no `POST to curator failed` |
| slice 0:1 | a `policy pid` line with a few GiB — only after the first goal, the model loads lazily |

Whether the arm picks cubes from the ray-traced pixels is **not** a criterion: it must run, move and
be managed correctly.

## When it fails

| Symptom | Cause | Do |
|---|---|---|
| units `inactive`, journal: `CDI device nvidia.com/gpu=0:1 is not there` | MIG is off, or the CDI spec is older than the slices | `fury-mode status`; in tenants mode `sudo systemctl restart nvidia-cdi-refresh.service`, then `fury-mode zero` |
| a robot-zero unit fails at start with a read-only, permission or capability error | the hardening (`ReadOnly=true`, `DropCapability=all`, `NoNewPrivileges=true`) was proven for this image as a pod on the hub, not under podman on this host | do **not** take the lines out: `install` refuses a unit without them, on purpose. Find what was denied and bring that back to the orchestrator — see below |
| `robot-zero-sim` restarts every 10 s: `address already in use` on 7447 | another Zenoh router on the host — a hand-started sim | `sudo ss -ltnp 'sport = 7447'`, stop that |
| `r00` not on the wall, the sim runs | the forwarder's states do not reach `10.20.0.1:9701`, or the renderer is down | `./73-fleet-renderer-install.sh status` (`dropped` names a reason); `sudo journalctl -u robot-zero-sim \| grep state-forwarder` |
| frames: `no picture yet (404 …)` | the renderer has not heard from `r00` yet | wait for the sim's first minute; then the row above |
| frames: `Connection refused` | the renderer is not running | `fury-mode status`, `sudo systemctl start fleet-renderer.service` |
| episode loop sits at `Waiting for action server...` | the policy is stopped or unhealthy | laptop: `robot-zero.sh status`, then `robot-zero.sh up` |
| reporter: `POST to curator failed` | the show curator on the hub does not answer on `10.20.0.10:30812` | laptop: `oc -n flywheel get deploy,svc curator-show`; from the host `curl -s -m 4 -o /dev/null -w '%{http_code}\n' http://10.20.0.10:30812/` answers `501` when it is up. Nothing is lost that was meant to be kept: the summary went to the container's tmpfs |
| the flywheel page says `waiting for robot zero` while the episode loop cycles | the reporter does not hear the loop (no `Episode started:` in its journal), or is down | `sudo journalctl -u robot-zero-emitter -n 30`; then `fury-mode zero` |
| the page's badge shows `soarm-act-v1` | the reporter did not get the policy's label from the graph and stamped its default | `fury-mode zero` (the label is latched by the policy; the reporter picks it up when it joins) |
| episode loop: cubes never move back | the cube reset cannot reach Gazebo | `GZ_PARTITION` must be the same line in the sim's and the episodes' unit (`check` tests it) |
| policy restarts every few minutes in RHEM | its health check cannot see the router: `robot-zero-sim` is down | `fury-mode zero` |
| `fury-mode: interrupted - check: fury-mode status` | a switch or a reset was cut short (Ctrl-C, a dropped ssh) | `fury-mode status` says what is up; run the same command again — every step of it can be repeated |
| `fury-mode status`: placement `no slice named yet` or `WRONG` | the label does not fit the mode | the line names the command: `robot-zero.sh up` or `down` |
| `robot-zero.sh up`: `the hub refused the label change` | the hub did not take the device document back | `flightctl edit device/<name>`, add `gpu_device: MIG-…` under labels by hand, then `up` again (it sees the label and goes on) |

`./74-robot-zero-install.sh remove` removes the four units and the bridge code; it refuses while the policy runs.

### If the hardening is what stops a unit

The three lines are part of what keeps robot zero away from the flywheel's data, so a unit that will not start with
them is a finding, not a setting. Nothing is changed on the host; this is what to collect and hand to the
orchestrator, who decides the narrowest change (a path moved under `/tmp` through an environment variable, one
capability — never the lines as a whole) and changes the installer's rule together with the unit:

```
sudo journalctl -u robot-zero-sim -u robot-zero-frames -u robot-zero-episodes -u robot-zero-emitter -n 60 --no-pager
sudo ausearch -m avc,user_avc -ts recent 2>/dev/null | tail -20
sudo podman ps -a --filter name=robot-zero --format '{{.Names}} {{.Status}}'
```

The first names the path or the call that was refused (`Read-only file system: '/…'`, `Operation not permitted`),
the second says whether SELinux, not the hardening, was the one refusing. Until it is resolved robot zero stays
down; every other tenant is unaffected, and `r00` is simply not on the wall.

## Not verified before the first run on the host

Tested off the host: the bridge's geometry, stamps, fetch (its deadline against a dribbling server, oversized and
bomb pictures) and staleness, the node's messages against stubbed ROS and that no exception ends it, the unit rules,
the shell of the world's `ExecStartPost=`, and `robot-zero.sh`'s order of stop, label and start against a stand-in
hub that rejects a careless write; `robot-zero.sh
status` ran against the real hub and host. Still to see on the machine: the three units under podman with the
hardening; rmw_zenoh carrying two 0.9 MB frames fifteen times a second from a client to the policy; the policy
starting with `AddDevice=nvidia.com/gpu=MIG-…` (the `gpu_device` label, never exercised before); that `flightctl apply` of the
device document changes the label and RHEM re-renders it at once while the application is stopped; that a
restart of the world pulls the other three units and the policy along as designed; a boot in tenants mode.

The fourth unit, the episode reporter, was added after those ran. Tested off the host: its rules, spoiled one line at a time, and
the same rule on quadlet-shaped output. **First thing to check on the machine, before anything else about it: the
emitter's Zenoh session.** It is the one assumption everything after it rests on. The emitter has no router
setting; it uses the image's default session — a peer that connects to the router on `localhost:7447` — from a
container of its own, which works only because the network is the host's. Look at it first:

```
sudo journalctl -u robot-zero-emitter -n 30 --no-pager
```

Good: `Episode emitter started`, then `Model version set from coordinator: …`, then `Episode started: …` within
a minute of the episode loop's next `Signaled: start`. Bad: the unit is `active` and the episode loop cycles, but
no `Episode started:` ever comes (the session does not reach the router, or does not hear the loop), or Zenoh
lines about a router it cannot find. The emitter starts no router, so `address already in use` on 7447 is not its
line; if that shows in **its** journal, the unit is not running the command the installer pins. If the session is
the problem, nothing else below can work; bring the journal back to the orchestrator (the fix is a client session
file like the frames bridge writes, which needs a change of the unit and of the installer's rule together).

Then, in this order: the emitter under podman with the hardening (it writes only under `/tmp`); its cube check
reading robot zero's Gazebo across containers (`GZ_PARTITION`); the policy's latched label reaching it; the POST
arriving at the show curator's NodePort — from this host `curl -s -m 4 -o /dev/null -w '%{http_code}\n'
http://10.20.0.10:30812/` answers `501`; a timeout means the hub's NetworkPolicy does not see this host as
`10.20.0.1` (`gitops/flywheel/curator-show.yaml` says what to take out).

## Rehearsing the 160 moment — REHEARSAL ONLY

At about one judged pass a minute the threshold comes round every few hours, so it cannot be waited for in a
rehearsal. The shortcut is to write the show curator's running total by hand, from a laptop logged in to the hub:

```
oc -n flywheel exec deploy/curator-show -- python -c "import json,pathlib; p=pathlib.Path('/data/episodes/totals.json'); t=json.loads(p.read_text()); print('WAS', t); t['pass']=158; p.write_text(json.dumps(t))"
```

Two passes later the bar fills, the banner reads *this is where a governed training run would start* and stays
for five minutes, then the count begins again.

**The number on the page is then fabricated.** It was typed, not counted: from that moment the page shows a
count that robot zero did not earn, and the `since HH:MM` beside it gives it away (a hundred and fifty passes in
ten minutes). That is acceptable with nobody watching and at no other time. **Put it back before an audience**:
write the `WAS` value printed above back the same way (`t['pass']=<was>`), or start the lane over honestly at
zero — `oc -n flywheel exec deploy/curator-show -- python -c "import pathlib; pathlib.Path('/data/episodes/totals.json').unlink()"`
— after which the page counts what is on the volume and `since` starts with the next verdict. Never do this on
stage, and never leave it overnight before a show.
