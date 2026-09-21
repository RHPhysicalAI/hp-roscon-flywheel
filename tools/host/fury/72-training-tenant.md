<!-- This project was developed with assistance from AI tools. -->
# The training tenant — operator page

In tenants mode the GPU is four MIG slices. Slice `nvidia.com/gpu=0:2` (1g.31gb) trains: real ACT
fine-tunes, one short round after another, for as long as the machine is in that mode — a busy slice beside the
coding assistant (`0:0`), robot zero (`0:1`) and the fleet's renderer (`0:3`). It is there to show isolation and
what the machine carries. **It is not part of the governed flywheel**: nothing it produces is evaluated,
packaged, signed, registered or rolled out.

A round is one line of a fixed list — start from the teacher or from `act-v2-ft160`, learning rate `1e-5` (the
recipe's) or `3e-5`, seed 1000 or 2000; eight lines, then over again — on the 161-episode dataset
`flywheel-ladder-160`, with the governed runner's own `lerobot-train` command line in the runner's own signed
image. Pieces: `flywheel/training-tenant.container` (the unit), `flywheel/training-tenant.sh` (the loop, mounted
into the container), `72-training-tenant-install.sh`, and `fury-mode`, which owns starting and stopping it.

## What it must never touch

| Rule | What holds it |
|---|---|
| never writes where the runner looks (`/data/flywheel/train`, `eval`, `datasets`) | the dataset and the two checkpoints are **read-only mounts**, not relabelled; the rest of `/data/flywheel` is not mounted at all |
| its own output only | the one writable mount is `/data/flywheel/tenant-train` |
| never publishes to Kafka or the object store, never touches the model registry | `Network=none`, and no `EnvironmentFile=`: no route and no keys |
| cannot starve the fleet or the hub | `CPUQuota=1000%`, `MemoryMax=48G` on the unit, 8 loader workers, fixed |

`install` measures the first three on the unit file it is about to install and refuses a copy that breaks one.
`fury-mode` still refuses a switch while a *governed* training runs; the tenant's own `lerobot-train` is told
apart by the unit that owns the process (its cgroup) and is simply stopped by the switch.

## Commands

The command blocks are meant to be pasted as they are. Once, in either mode — it starts nothing:

```
cd ~/flywheel-setup
./72-training-tenant-install.sh install
```

After that the mode switch owns it: `fury-mode tenants` (from a laptop: `tools/hub/fury-switch.sh tenants`) starts
it once the four slices exist, every switch stops it first, a boot in tenants mode brings it back, and in
flywheel mode the unit skips its own start. After a change to the script or the unit: `install` again, then
`sudo systemctl restart training-tenant.service` (the round in progress is dropped).

```
./72-training-tenant-install.sh status
fury-mode status
./72-training-tenant-install.sh remove
```

`remove` leaves `/data/flywheel/tenant-train` where it is. Disk use stays bounded without it: the last three
round directories are kept (about 0.6 GB each), older ones go.

## On stage

1. **The journal** — lerobot's own loss lines, one every 100 steps (about every ten seconds), and one line of
   JSON when a round ends. Wrapped for a projector — step of the round, loss, steps per second, data wait, every
   round's start and end called out — from a laptop, or on the host with `--local`; no sudo (`75-tenant-metrics.md`):

   ```
   FURY_SSH=<user>@<host> tools/hub/training-watch.sh
   ```

   The lines as they are:

   ```
   sudo journalctl -fu training-tenant
   ```

2. **The GPU tenants dashboard** (`70-dcgm.md`, `75-tenant-metrics.md`): the *Training tenant* group — loss,
   steps per second, the round, the loss curve round after round — and the `training (1g)` line that goes busy
   when the tenant starts, beside the assistant's. Stop the tenant and only its numbers drop.
3. **The ledger** — one line per round: configuration, steps, wall time, last loss, steps per second, and the
   seconds per step spent computing (`updt_s`) against waiting for data (`data_s`), from lerobot's log lines:

   ```
   tail -n 3 /data/flywheel/tenant-train/rounds.jsonl | jq -c '{round, start, lr, seed, wall_s, steps_per_s, last_loss, updt_s, data_s}'
   ```

## The isolation beat

While the tenant trains (a loss line in the last minute), measure the assistant on its own slice, as an
ordinary user:

```
./61-rhaiis-smoke.sh bench
```

The number to hold it against was taken with the other slices idle: **time to first token 0.132 s,
decode 245.7 tokens/s, end to end 218.7 tokens/s**. MIG gives each slice its own compute and memory, so decode
should not move; if anything does it is time to first token, which has a CPU part, and the CPU is shared. Then
`sudo systemctl stop training-tenant.service`, `bench` again, `sudo systemctl start training-tenant.service`.
Measured on 2026-09-20 with the tenant training next door: **time to first token 0.135 s, decode 246.6 tokens/s,
end to end 218.9** - no measurable difference.

## How long a round is — an estimate, to be replaced by the first round

Measured on the whole GPU, batch 8, 4 loader workers (`flywheel/flywheel-runner.container`): 0.064 s a step
computing, 0.18 s waiting for data. So the loader delivered one batch per 0.244 s from 4 workers — about one
worker-second per batch — and 8 workers deliver one every 0.12 s. Computing on a 1g slice is not measured: a
seventh of the GPU's compute puts it between 0.064 s (if the whole GPU was not the limit at batch 8) and 0.45 s
(if it was, and compute scales with the slice). A step is the larger of the two: **0.12 to 0.45 s, 0.3 s taken as
the middle**. `TENANT_STEPS=3000` is then 15 minutes (6 to 22 across the range), plus about a minute to start and
save. After the first round, set it to `900 x steps_per_s` from `rounds.jsonl` (`Environment=TENANT_STEPS=` in
the unit, `install`, restart). Memory is not the constraint: during that fine-tune the whole GPU's memory peaked
at 6.9 GiB, sim and policy included, and the slice has 31.

## When it fails

| Symptom | Cause | Do |
|---|---|---|
| `fury-mode status` shows `inactive` in tenants mode, journal: `CDI device nvidia.com/gpu=0:2 is not there` | the CDI spec is older than the slices | `sudo systemctl restart nvidia-cdi-refresh.service`, then `sudo systemctl start training-tenant.service` |
| first round fails at once, the log's end names `resnet18-…pth` and a download | the backbone weights are not in the tenant's cache, and it has no network | the `podman run` line that `install` prints under its WARNING (once, with the uplink) |
| first round fails at once with an argument error | `--policy.optimizer_lr` or `--seed` is not what this lerobot calls it (see below) | fix the flag in `flywheel/training-tenant.sh`, `install`, restart |
| `Permission denied` on the dataset or a checkpoint | its SELinux label is not `container_file_t:s0`; the tenant does not relabel the flywheel's files | `install` names the path and the `chcon` line |
| unit `failed`, `start-limit-hit` | ten failed starts within the hour | read `journalctl -u training-tenant -n 60`, fix, `sudo systemctl reset-failed training-tenant.service`, start. `fury-mode tenants` resets the count by itself |
| rounds end with `exit 137`, or the journal says `oom-kill` | `MemoryMax=48G` was too tight | `systemctl show -p MemoryPeak training-tenant`; raise it in the unit, with a look at what the fleet needs |
| `fury-mode` refuses a switch over "a training run", and the pid it names is the tenant's own `lerobot-train` | the container is not running inside its unit's cgroup, so `fury-mode` took it for a governed run (it refuses when in doubt) | `sudo systemctl stop training-tenant.service`, switch; then `CgroupsMode=split` under `[Container]` in the unit and `install` |

## Not verified before the first run

- `--policy.optimizer_lr=` and `--seed=` are not flags the runner uses. They were checked against a lerobot
  0.6.1 source tree, not the image's 0.5.1. Every other flag is the runner's, as is the environment.
- The ledger reads `loss:`, `updt_s:` and `data_s:` from lerobot's log lines - the fields the runner's journal
  showed during the measured fine-tune. A field that is not printed becomes `null` in the ledger, never a guess.
- That a fine-tune from a checkpoint still fetches the backbone's ImageNet weights, and that the runner's cache
  has them under `/data/flywheel/cache/torch/hub/checkpoints/`.
- Step time on a 1g slice, memory of 8 workers, and whether `nvidia-smi` prints per-process memory under MIG on
  this driver (`status` falls back to the whole MIG table).
