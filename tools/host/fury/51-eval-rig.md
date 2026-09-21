<!-- This project was developed with assistance from AI tools. -->
# The eval rig — operator page

The training pipeline scores a candidate checkpoint and the incumbent on the same seeded scenes. Two policy
servers on one Zenoh graph get every goal rejected, and stopping the production policy needs a human, so
the eval runs in its **own rig**: one podman pod `eval-rig` holding a second sim with its own Zenoh router,
its own policy and its own eval coordinator. The pod is a private network namespace on podman's bridge — `:7447`
and `:8081` exist a second time in there, nothing is published, nothing uses host networking — and all three
containers carry `GZ_PARTITION=evalrig`, so Gazebo discovery cannot cross into production's sim either. Production
is never stopped, named or touched. `51-eval-rig.sh` builds the pod, waits for sim and policy, runs the pinned evaluation
scene (60 s episodes, `cube_medium` randomised, arm homed between seeds), moves
the record into place and **always** removes the pod again.

Eval mode records no bags (`RECORD=false`, the recorder is not even launched) and sends the emitter no signals.
What the rig could write at all goes under `/data/flywheel/eval/`: `rig-work/` is the coordinator's `/data`,
`rig-raw/` the emitter's local JSON (expected to stay empty; the rig's sim has no `CURATOR_URL`).

The command blocks are meant to be pasted as they are: no comments inside them.

## The contract with the training runner

| | |
|---|---|
| Request | `/data/flywheel/eval/requests/<mv>.json` — `{"model_version": "<mv>", "checkpoint": …, "seed_base": <int>, "n": <1..500>}`. `<mv>`: `[A-Za-z0-9._-]`, at most 64, not starting with a dot. Write it under another name (a dot name, or not ending in `.json`) and rename it into place. |
| `checkpoint` | an absolute path to a `pretrained_model` directory that resolves under `/data/flywheel/` and holds `model.safetensors` (mounted read-only at `/model`); or `modelcar`, the pinned incumbent image (`/modelcar/models/act`); or `HF`, which is **refused**: not supported on this host. |
| Taken | the request is renamed to `<mv>.json.taken` before anything else, and an older `<mv>.done` is removed. |
| Record | `/data/flywheel/eval/<mv>.json`, the coordinator's own eval record (`aggregate`, `episodes`, `eval_config.seed_base`, `eval_config.episodes`, …), moved there only when it is the one asked for and at least one episode was scored. A bad record stays in `rig-work/eval/` — the runner reuses records whose seeds match. |
| Answer | `/data/flywheel/eval/requests/<mv>.done` — `{"status": "ok"|"failed", "result": "<path>"|null, "error": "<text>"|null, "started": "<iso>", "finished": "<iso>"}`, written by rename, on every way out including a refusal, a timeout and SIGTERM. Only SIGKILL or a power cut leaves a `.taken` without a `.done`. |
| Budget | `n` × 120 s + 15 min for the whole eval, start-up included. |

`flywheel-eval.path` watches `requests/*.json` and starts `flywheel-eval.service`, which runs
`flywheel-eval-rig serve-one`: the oldest request, one per turn. A request that arrives during a turn is picked up
when the turn ends. With MIG on, or a leftover pod in the way, the request is still taken and answered `failed`
with the reason. Output: `journalctl -u flywheel-eval.service` and `/var/log/flywheel-eval-rig.log`; from a checkout,
`log/51-eval-rig.log`.

## Verify first: five seeds beside the running loop

The design is conditional on this: the incumbent, scored in the rig while production collects, against
the **4/5, mean 2.6 cubes, no goal rejected** of the first smoke test (same image, same checkpoint, same seeds
1000–1004, scored against the production sim). Before: MIG off, the loop running, no rig.

```
fury-mode status
systemctl is-active so-arm-sim act-coordinator
cd ~/flywheel-setup
./51-eval-rig.sh status
sudo podman logs --since 30m act-coordinator 2>&1 | grep -c "Goal rejected"
curl -s -m 10 http://127.0.0.1:8081/static | grep -a -c "Content-Type: image/jpeg"
```

The last two are production's baseline: goals rejected in the last half hour (expect 0) and camera frames in
10 s (30 fps is 300). Then the eval, about 12 minutes:

```
./51-eval-rig.sh run eval-rig-verify modelcar 5 1000
```

In a second terminal while it runs, repeat the two production lines every few minutes, and look at the rig:

```
sudo podman logs --since 30m act-coordinator 2>&1 | grep -c "Goal rejected"
curl -s -m 10 http://127.0.0.1:8081/static | grep -a -c "Content-Type: image/jpeg"
cd ~/flywheel-setup
./51-eval-rig.sh status
```

Afterwards, seed by seed against the earlier smoke (its record, if `13-first-inference.sh` left it there), and
what the rig left behind:

```
sudo jq -c '.episodes[] | {seed, cubes_placed, goal_accepted}' /data/flywheel/eval/eval-fury-smoke.json /data/flywheel/eval/eval-rig-verify.json
sudo find /data/flywheel/eval/rig-raw /data/flywheel/eval/rig-work -type f
sudo podman pod exists eval-rig; echo $?
```

**Pass:** the rig prints `renderer: NVIDIA Corporation`, about 300 frames for both cameras, `3 cube poses seen`
and `/run_policy servers on the rig's graph: 1`; the result is 4/5 or 5/5 with `goal_rejected: 0` (3/5 is within
the noise of five episodes — run it again with `n` 10 before judging); production's rejected count does not move and its
frame count stays near 300; no file under `rig-raw`; the pod is gone (`1`). `served_model_version: null` in the
episode lines is what eval mode has always written, not a fault. **Fail:** production rejects goals or drops
frames while the rig is up — `./51-eval-rig.sh down` at once — or the rig scores clearly below the smoke on the
same seeds. Then the fallback stands: an attended window with `flightctl app stop`.

## When it fails

The message is what `run` prints last and what `.done` carries in `error`.

| Message, or what you see | Cause | Do |
|---|---|---|
| `MIG is Enabled` | tenants mode: no OpenGL on a MIG device, the sim cannot render | `fury-mode flywheel`, then resubmit the request |
| `an eval-rig pod already exists … a leftover` | an earlier run was killed hard (SIGKILL, reboot) before its teardown | `./51-eval-rig.sh status`, `./51-eval-rig.sh down`, resubmit |
| `another eval is running` | a second `run` by hand; `serve-one` queues up to 2 h instead | wait, or `status` |
| `image … is not in root's storage` | the rig never pulls | `./11-sim-build.sh` for the sim; the runtime and modelcar images come with the Fleet's policy |
| `eval-rig-sim stopped …` | the last 25 lines are above it. CDI stale after a mode switch, GPU memory, a sim start flake | `sudo systemctl restart nvidia-cdi-refresh`; `nvidia-smi`; run again |
| `its controllers did not activate` | the sim's controller switch timed out: the arm would not move | run again; twice in a row, look at `./12-sim-smoke.sh` on production's sim |
| `renders on '…', not on the GPU` | software rendering (under 2 fps): the score would measure the renderer | MIG state, `nvidia-ctk cdi list`, `NVIDIA_DRIVER_CAPABILITIES` |
| `eval-rig-policy stopped while loading` | the directory is not a loadable `pretrained_model` (`config.json` and the processor files next to `model.safetensors`), or CUDA out of memory beside production and a training job | the lines above it; `nvidia-smi` |
| `short of healthy` | the health check prints why: version not latched, or `/run_policy` not on the graph | its lines; run again |
| `cube poses do not reach the runtime image` | gazebo transport does not cross between the pod's containers, or `GZ_PARTITION` is not honoured on one side | the design's first assumption failed — stop here and report it; fallback as above |
| `N policy servers on the rig's graph` | the rig's Zenoh graph is joined with production's | the pod is already being removed; check production's rejected count; fallback as above |
| `no episode was scored` | every goal rejected inside the rig | the record is in `rig-work/eval/`; policy log lines in the run's output |
| `wrote no record` / `not the one asked for` | the coordinator died, or wrote something else | its output is in the log |
| `did not finish inside its budget` | a hang — an action server that never appears blocks the coordinator for ever | the log shows the last episode reached |
| `.done` says `not valid JSON`, `does not equal the file name`, `n must be …`, `not under /data/flywheel/`, `no model.safetensors` | the request itself | fix the writer; the refused request is kept as `.json.taken` |
| `flywheel-eval.path` is `failed`, `unit-start-limit-hit` | something matching `*.json` that `serve-one` could not rename away, so the trigger span | `ls -la /data/flywheel/eval/requests`, remove it, `sudo systemctl reset-failed flywheel-eval.service flywheel-eval.path`, `sudo systemctl start flywheel-eval.path` |
| a `.json.taken` with no `.done` | the script was killed with SIGKILL, or the host went down | `down`, resubmit |
