<!-- This project was developed with assistance from AI tools. -->
# CPU inference spike — desktop stand-in device (D024)

**Date:** 2026-09-08, updated 2026-09-09 · **Gates:** Phase 4.5 C (Fleet-delivered application) · **Decision:** D024

The desktop stand-in device is a RHEL 10 KVM VM (8 vCPU / 16 GiB, no GPU) that runs the ACT
policy on CPU while the sim, camera bridge and host runner stay on the host RTX 5090. D024 gates
that stand-in on three criteria. This record holds the numbers for criterion 1 and the procedure
for criteria 2–3.

| # | Criterion (D024) | Result |
|---|---|---|
| 1 | p95 forward latency < 0.5 × (`n_action_steps`/50) s | **PASS** — 83.2 ms vs 1000 ms (8 threads) |
| 2 | `ros2 topic hz` on the commanded-action topic: no gap > 40 ms | **FAIL as written** — C2 (30/0.95): max 335 ms, 6.2 % > 40 ms; C3 (100/0.5, D058): max 313 ms, **0.34 %** > 40 ms; see Part 2 and "C3 part 2" below |
| 3 | 20-seed D020 eval within 10 points of GPU v2 (86%) | **PASS** (2026-09-09, C3 role split) — **18/20 = 90 %** vs GPU 17/20; the 2026-09-08 attempt from the VM was invalid (Gazebo transport is host-local); see "C3 part 3" below |

## Model under test

`act-v2-ft160` (`/data/models/act-v2-ft160/act/config.json` on the host, `/home/jary/flywheel-data/models/act-v2-ft160/act`):

| Field | Value |
|---|---|
| `chunk_size` | 100 |
| `n_action_steps` | 100 |
| `n_obs_steps` | 1 |
| `observation.images.wrist` | VISUAL, `[3, 480, 480]` |
| `observation.images.static` | VISUAL, `[3, 480, 480]` |
| `observation.state` | STATE, `[6]` |
| `action` | ACTION, `[6]` |
| backbone / transformer | resnet18, dim 512, 8 heads, 4 enc / 1 dec layers, VAE on |
| `temporal_ensemble_coeff` | null (action-queue path: one forward per 100 actions) |
| control rate | 50 Hz (`so_arm101.yaml` contract `fps: 50`) |

**Threshold derivation:** one chunk covers `n_action_steps / fps = 100 / 50 = 2.0 s` of wall time;
D024 allows half of it for the forward pass → **p95 < 1000 ms**.

## Part 1 — offline forward latency (no sim contention)

### Method

- Same image as production (`act-inference:latest`, id `69cb2c3fecdb`, torch `2.9.1+cu130`,
  lerobot 0.5.1), throwaway container, `--network none` (cannot join the zenoh session),
  `CUDA_VISIBLE_DEVICES=` and no `--gpus` (the script asserts `torch.cuda.is_available()` is False).
- VM budget emulated with cgroups: `--cpus=8 --memory=16g`, `--cpuset-cpus=0,2,4,6,8,10,12,14`
  = one SMT thread on each of the 8 P-cores of the i9-13900K (`lscpu -e`: cores 0–7 are the
  5.5–5.8 GHz P-cores, CPUs 16–31 are E-cores). A second run uses all 16 P-core threads for context.
- The exact production path from lerobot's `async_inference/policy_server.py`:
  `from_pretrained` → `make_pre_post_processors(..., device override)` → `preprocessor(obs)` →
  `policy.predict_action_chunk(batch)` → `postprocessor` per action. `predict_action_chunk` is the
  call that runs the ResNet + transformer (`select_action` only calls it when the queue is empty).
  The ImageNet backbone init is nulled before loading (the checkpoint overwrites it; avoids a download).
- Synthetic observation with the real keys and shapes: images `(1, 3, 480, 480)` float32 in
  [0, 1] (as `raw_observation_to_observation` hands them over), state `(6,)`. 10 warm-up passes,
  200 timed passes, `torch.inference_mode()`, `torch.set_num_threads(N)`, `OMP_NUM_THREADS=N`.
- Host during the run: SNO VM (16 vCPU) + `so-arm-sim` + GPU `act-inference` all live; load
  average 7.4 before, 11.7 after. The VM was booting during the run. So this is *with* the
  host's normal background, not a quiet box.
- Script: `device/spike/bench_cpu_forward.py` (re-runnable on the VM: `--model <path> --threads 8`).

Command (host, 2026-09-08 15:14):

```bash
docker run --rm --network none --cpus=8 --cpuset-cpus=0,2,4,6,8,10,12,14 --memory=16g \
  -e CUDA_VISIBLE_DEVICES= -e OMP_NUM_THREADS=8 \
  -v /home/jary/flywheel-data:/data:ro -v /tmp/act-spike:/spike \
  --entrypoint python3 act-inference:latest \
  /spike/bench_cpu_forward.py --threads 8 --iters 200 --warmup 10 --json /spike/result-t8.json
```

### Results (verbatim)

```
== run threads=8 cpuset=0,2,4,6,8,10,12,14 cpus=8 15:14:16
model=/data/models/act-v2-ft160/act torch=2.9.1+cu130 threads=8 cpus=[0, 2, 4, 6, 8, 10, 12, 14] arch=x86_64
cpu=13th Gen Intel(R) Core(TM) i9-13900K
chunk_size=100 n_action_steps=100 fps=50 threshold_p95=1000 ms  (load 0.4s, 200 iters in 15.7s)
stage             p50      p95      p99     mean      max
preprocess        0.2      1.8      2.0      0.6      2.2
forward          75.0     83.2    101.0     76.5    116.3
postprocess       0.8      1.2      1.2      0.8      1.3
end_to_end       76.5     85.4    102.0     77.9    117.4
VERDICT PASS: forward p95 83.2 ms vs threshold 1000 ms

== run threads=16 cpuset=0-15 cpus=16 15:14:36
model=/data/models/act-v2-ft160/act torch=2.9.1+cu130 threads=16 cpus=[0, 1, ..., 15] arch=x86_64
chunk_size=100 n_action_steps=100 fps=50 threshold_p95=1000 ms  (load 0.3s, 200 iters in 16.6s)
stage             p50      p95      p99     mean      max
preprocess        0.2      0.2      0.3      0.2      5.1
forward          78.2     98.2    150.0     81.1    176.4
postprocess       1.2      1.5      1.6      1.2      6.8
end_to_end       79.5    100.0    157.0     82.6    177.8
VERDICT PASS: forward p95 98.2 ms vs threshold 1000 ms
```

| threads | cpuset | p50 ms | p95 ms | p99 ms | max ms | threshold | verdict |
|---|---|---|---|---|---|---|---|
| 8 | 0,2,4,6,8,10,12,14 (one thread per P-core) | 75.0 | **83.2** | 101.0 | 116.3 | 1000 | **PASS** |
| 16 | 0–15 (both threads of each P-core) | 78.2 | 98.2 | 150.0 | 176.4 | 1000 | PASS |
| 8 (in-guest) | VM `act-device` 10.0.0.51, 8 unpinned vCPUs, model from the modelcar image volume | 168.0 | **184.4** | 244.0 | 257.1 | 1000 | **PASS** |

### In-guest run (2026-09-08 20:50 UTC, Phase 4.5 C prep)

Same script, same image (`act-inference:latest` id `69cb2c3fecdb`, moved with `docker save | podman
load`; the VM stores it as `docker.io/library/act-inference:latest`, digest `sha256:dcf6fe0f…`),
run **inside the RHEL 10.2 VM** with rootful podman 5.8.2. The checkpoint was read from the
promoted modelcar mounted as a podman image volume (`systemd-models-test`, `Driver=image`,
`quay.io/jary/soarm-act-modelcar@sha256:bdb513ca…` → the image rootfs, checkpoint at
`<mount>/models/act`), which also validates the Fleet's volume path.

```bash
sudo podman run --rm --network none --cpus 8 --memory 14g \
  -e CUDA_VISIBLE_DEVICES= -e OMP_NUM_THREADS=8 \
  -v systemd-models-test:/models:ro -v /home/jary/spike:/spike:z \
  --entrypoint python3 docker.io/library/act-inference:latest \
  /spike/bench_cpu_forward.py --model /models/models/act --threads 8 --iters 200 --warmup 10 --json /spike/result-vm-t8.json
```

```
model=/models/models/act torch=2.9.1+cu130 threads=8 cpus=[0, 1, 2, 3, 4, 5, 6, 7] arch=x86_64
cpu=13th Gen Intel(R) Core(TM) i9-13900K
chunk_size=100 n_action_steps=100 fps=50 threshold_p95=1000 ms  (load 0.5s, 200 iters in 34.6s)
stage             p50      p95      p99     mean      max
preprocess        0.4      0.5      0.6      0.4      0.9
forward         168.0    184.4    244.0    170.3    257.1
postprocess       2.7      2.9      3.1      2.0      3.5
end_to_end      170.5    187.6    247.3    172.7    260.6
VERDICT PASS: forward p95 184.4 ms vs threshold 1000 ms
```

Reading: 2.2× the host-cgroup number (the guest's 8 vCPUs float across all 32 host threads — the
guest reports `Thread(s) per core: 1`, so some land on E-cores or SMT siblings — plus KVM overhead
and the SNO VM sharing the box), still 5.4× inside the threshold and 7.7× at p99. Part 2's
chunk-boundary check is what decides whether `VCPU_CPUSET=0,2,4,6,8,10,12,14` pinning is worth it;
compute alone says no.

### Reading

- A forward pass costs ~4% of the 2 s chunk window on 8 P-core threads; the margin to the
  threshold is 12×. Even the worst sample (116 ms) is 8.6× inside it.
- 16 threads on SMT siblings is *slower* and has a fatter tail — the VM should run 8 torch
  threads, and its 8 vCPUs should not be scheduled onto sibling hyperthreads if latency ever
  matters (`VCPU_CPUSET=0,2,4,6,8,10,12,14` in `device/vm/create-vm.sh` pins them; left unpinned
  by default because the SNO VM floats across all 32 threads).
- Caveats: bare cgroup on the host, not a KVM guest (expect a few percent of virtualisation
  overhead, none of it near the threshold); synthetic images (compute is shape-bound, so content
  does not matter); no zenoh/ROS serialisation in the loop (that is criterion 2's job).

## Parts 2–3 — pending: live sim, commanded-action cadence, 20-seed eval

**Prerequisite:** only one policy client may command the arm, so the host `act-inference`
container must be stopped for the duration (`docker stop act-inference`; restart it with
`docker start act-inference` afterwards). Schedule it; do not do it as a side effect. The sim
(`so-arm-sim`) and its zenoh router (`10.0.0.48:7447`) stay up. The VM needs the x86_64
`act-inference` image (Phase 4.5 F builds it for both arches; until then
`docker save act-inference:latest | ssh jary@10.0.0.51 sudo podman load`, 11.2 GB — **done
2026-09-08**, present as `docker.io/library/act-inference:latest`) and the checkpoint: on the VM
it is the modelcar image volume (`-v systemd-models-test:/models:ro`, path `/models/models/act`,
see the in-guest run above), not a `/var/lib/act-inference/data/models/...` bind mount. Adjust
`POLICY_PATH` and the `-v` lines below accordingly when running parts 2–3.

### Part 2 — commanded-action cadence

Topic, from the contract (`pai_data_collection/config/rosetta/so_arm101.yaml`):
`/forward_position_controller/commands` (`std_msgs/msg/Float64MultiArray`, reliable, 50 Hz).
Inputs it consumes: `/wrist_camera/image_raw`, `/static_camera/image_raw` (best-effort),
`/joint_states` (reliable).

1. On the VM, start the policy on CPU against the host router (no recording, no eval):
   ```bash
   sudo podman run --rm --name act-inference --network host \
     -e ZENOH_ROUTER=10.0.0.48:7447 -e RMW_IMPLEMENTATION=rmw_zenoh_cpp \
     -e POLICY_DEVICE=cpu -e OMP_NUM_THREADS=8 \
     -e MODEL_VERSION=act-v2-ft160 -e POLICY_PATH=/model -e RECORD=false \
     -e RESET_ARM=false -e EPISODE_LEN=60 -e RANDOMIZE_CUBES=true -e RANDOMIZE_ONLY=cube_medium -e RANDOM_RADIUS=0.03 \
     -v /var/lib/act-inference/data/models/act-v2-ft160/act:/model:ro -v /var/lib/act-inference/data:/data \
     localhost/act-inference:<tag>
   ```
   Confirm the log line `[inference] Device: cpu` and that rosetta does not print
   `Requested policy device ... using 'cpu' instead` (that would mean it asked for cuda).
2. On the host, in a throwaway container on the same zenoh session, measure for a whole episode:
   ```bash
   docker run --rm --network host -e ZENOH_ROUTER=127.0.0.1:7447 -e RMW_IMPLEMENTATION=rmw_zenoh_cpp \
     --entrypoint bash act-inference:latest -c '
     source /opt/ros/$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash
     cat > /tmp/z.json5 <<EOF
     { mode: "client", connect: { endpoints: ["tcp/$ZENOH_ROUTER"] } }
     EOF
     export ZENOH_SESSION_CONFIG_URI=/tmp/z.json5 RMW_ZENOH_CONFIG_FILE=/tmp/z.json5 RMW_ZENOH_ROUTER_CHECK_ATTEMPTS=0
     timeout 75 ros2 topic hz -w 3000 /forward_position_controller/commands'
   ```
   `ros2 topic hz` prints `average rate` plus `min/max/std dev` of the inter-message delta over the
   window. **Pass: `max` < 0.040 s** across ≥ 3 episodes (≈ 3000 messages each at 50 Hz). Watch
   the chunk boundaries in particular (every 2 s): a gap there means the next forward pass was not
   ready in time — the number to compare against part 1's p99.
3. Record the three `max` values and the average rate here.

### Part 3 — 20-seed D020 eval on CPU

Derived from `~/eval_policy.sh` on the desktop (D020 pinned config: seed base 1000, radius 0.03,
yaw 180, `cube_medium`, `EPISODE_LEN=60`, `RESET_ARM=true`, `RECORD=false`), run on the VM with
`N=20` and `POLICY_DEVICE=cpu`:

```bash
sudo podman run --rm --name act-eval --network host \
  -e ZENOH_ROUTER=10.0.0.48:7447 -e RMW_IMPLEMENTATION=rmw_zenoh_cpp \
  -e POLICY_DEVICE=cpu -e OMP_NUM_THREADS=8 \
  -e EVAL_MODE=true -e EVAL_EPISODES=20 -e EVAL_SEED_BASE=1000 \
  -e RECORD=false -e EPISODE_LEN=60 -e RESET_ARM=true \
  -e RANDOMIZE_CUBES=true -e RANDOMIZE_ONLY=cube_medium -e RANDOM_RADIUS=0.03 -e RANDOM_YAW_DEG=180 \
  -e MODEL_VERSION=eval-cpu-v2-ft160 -e POLICY_PATH=/model \
  -v /var/lib/act-inference/data/models/act-v2-ft160/act:/model:ro -v /var/lib/act-inference/data:/data \
  localhost/act-inference:<tag> 2>&1 | grep -E "\[eval\]"
```

Result lands in `/var/lib/act-inference/data/eval/eval-cpu-v2-ft160.json` (`aggregate.success_rate`).
Eval mode sends no signals to the emitter and the curator discards `eval-*` labels (D020, revised
2026-09-08), so this cannot pollute the corpus.

**Baseline on the same seeds:** GPU v2 (`docs/eval-records/phase3-ladder/eval-ft-160ep.json`,
episodes with seed 1000–1019) = **17/20 = 85%** (failures: seeds 1000, 1010, 1018).
**Pass: ≥ 15/20 (75%)** — within 10 points of the 86% full-run figure and of the 85% same-seed
figure. If it fails, apply D024's fallbacks in order: raise `n_action_steps` (cheap — part 1
shows the forward pass is not the bottleneck, so a miss here would point at cadence, not compute),
lower the Gazebo real-time factor, VFIO last.

## Parts 2–3 — live run on the RHEM-managed VM (2026-09-08, Phase 4.5 C cut-over)

Setting: host `act-inference` stopped 22:45:58Z (swap agent + bag watchdog killed first); the VM
enrolled 22:46:10Z as device `s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg` (alias
`act-device.localdomain`, labels `fleet=act-inference site=desktop gpu=none policy_device=cpu
arch=amd64 zenoh_router=10.0.0.48 zenoh_port=7447`); Fleet `act-inference` generation 3
(`a8fbe3b`), renderedVersion 3, `applicationsSummary: Healthy`, container
`act-inference-128875-act-inference` `(healthy)` from 22:58:49Z. Runtime image
`quay.io/jary/soarm-flywheel@sha256:29955e4e…` (D045), checkpoint from the modelcar image volume
(`/modelcar/models/act`), `POLICY_DEVICE=cpu`, `OMP_NUM_THREADS=8`, `TORCH_NUM_THREADS=8`,
`RECORD=false` (D044). The sim, zenoh router and pose UI stayed on the host.

### Part 2 — commanded-action cadence: **FAIL** (as written)

Method: instead of the host-side `ros2 topic hz` above, a probe *inside the managed container*
(`podman exec … python3 /tmp/cadence_probe.py 300`; source in the C-live agent's scratchpad and
reproduced below in spirit by `ros2 topic hz`) subscribed to `/forward_position_controller/commands`
(reliable) and to `/flywheel/episode_control`, and recorded wall-clock inter-message gaps only inside
the first 59.5 s after each `start` signal — so the cancel→end idle between episodes is excluded.
Run 23:00:50Z–23:05:50Z, three full episodes plus a partial:

| ep | start (Z) | msgs | rate (wall) | gap p50 | p95 | p99 | max | > 40 ms | > 100 ms | > 200 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 23:01:21 | 2156 | 36.3 Hz | 20.2 ms | 57.1 | 211.1 | **335.2** | 134 | 64 | 28 |
| 2 | 23:02:39 | 2134 | 35.9 Hz | 20.2 ms | 54.9 | 229.2 | **313.4** | 135 | 62 | 46 |
| 3 | 23:03:56 | 2121 | 35.8 Hz | 20.2 ms | 54.6 | 229.9 | **325.3** | 129 | 62 | 42 |
| 4 (partial, 35.7 s) | 23:05:14 | 1288 | 36.1 Hz | 20.2 ms | 52.6 | 228.4 | 298.7 | 78 | 35 | 23 |

All three full episodes: 6408 intervals, **max 335.2 ms, 398 (6.21 %) over 40 ms**. Criterion:
no gap > 40 ms → **FAIL**. `ros2 topic hz -w 3000` in the same container over 23:00:50–23:03:00Z:
`average rate: 33.1`, `std dev: 0.135 s` (its `max 5.24 s` spans an inter-episode idle and is not
a within-window number).

Corroboration from the client itself: `rosetta.py:324` declares an action timeout of 2 frame
periods (= 40 ms at the contract's 50 Hz) and logs `Action timeout - sending safety action`;
**67** such lines fell inside episode 1's window (~1.1/s), 334 between the 22:58:19Z container
start and 23:04Z.

Mechanism (from the same logs): the policy server reports `Actions per chunk: 30` and logs
`Running inference for observation #… (must_go: True)` up to **4 times per second**. The live
node parameters (`ros2 param get /rosetta_client …`, 23:12Z) are `actions_per_chunk=30` and
`chunk_size_threshold=0.95` (node defaults are 50 / 0.5, `rosetta_client_node.py:169-177`; the
values come from the launch file's `params_file`, not from the entrypoint). At a 0.95 threshold the
client asks for the next chunk as soon as ~2 of 30 actions have been consumed, i.e. effectively
continuously, while one in-guest forward pass takes 170–250 ms (part 1, in-guest row). The queue runs dry at every chunk boundary
and the ~200–330 ms gaps are one forward pass each. Part 1's 2 s budget assumed one forward per
`n_action_steps = 100`; the client never asks for 100. The stall is compute-contention at the chunk
cadence, not zenoh or the VM's network.

Reading against D024's fallbacks: "raise `n_action_steps`" maps to raising the client's
`actions_per_chunk` toward the checkpoint's 100 (a `params_file` value for
`rosetta_client_launch.py` — the entrypoint's `policy_device:=` style argument is not declared by
that launch file, see the device-plumbing finding — so it rides with F's image; the Fleet can then
set it per `gpu` label). At 100 actions per chunk with a lower threshold (e.g. 0.5) the server would
run ~0.5 forwards/s and have ~1 s of queue to hide a 250 ms forward; keeping 0.95 with 100 actions
would still re-request early but only once per ~2 s. vCPU pinning (`VCPU_CPUSET`, D034) buys ~2× on the
forward and would not by itself clear a 0.6 s chunk with margin. Lower RTF and VFIO stay last.

Real-time check (same container, 23:09Z, `rtf_probe.py 20`): `/clock` advanced 19.80 s over
19.99 s wall (**RTF 0.990**) and `/joint_states` arrived at **49.6 Hz wall** — the sim runs at speed,
so the 36 Hz command rate and the gaps above are wall-time stalls on the policy side (the client
misses ~28 % of 50 Hz ticks); the 40 ms criterion needs no RTF scaling. For scale, `ros2 topic hz
-w 1000 /joint_states` from the same container (23:03Z): `average rate: 49.315`, `max: 0.047 s`,
`std dev: 0.00227 s` — the sim's own 50 Hz stream is tight to ~2 ms with one 47 ms outlier.

Device plumbing finding: `rosetta_client_node.py:568` logged `Requested policy device 'cuda' but
the requested backend is unavailable; using 'cpu' instead` even though the entrypoint launched with
`policy_device:=cpu` and printed `Launching Rosetta client with ACT on cpu`. `ros2 param get
/rosetta_client policy_device` → `cuda`: `rosetta_client_launch.py` declares no `policy_device`
launch argument (it takes a `params_file`; `rosetta_hil_launch.py` is the one that declares it), so
the entrypoint's `policy_device:=cpu` is silently ignored, the node keeps its default `cuda`
(checkpoint `config.json` also says `"device": "cuda"`), and CPU is selected by the
CUDA-unavailable fallback, not by the setting. On a GPU host
`POLICY_DEVICE=cpu` would therefore still run on CUDA. See the C-live decisions for the fix owner (F).

No host GPU baseline could be read from the host container logs: every `act-inference` instance
since ~20:00Z was cycled by the bag watchdog inside 18–42 s.

### Part 3 — 20-seed D020 eval on CPU: **INVALID from the device VM** (stopped after 5/20)

Procedure as planned, on the VM with the Fleet app target stopped
(`systemctl stop act-inference-128875-flightctl-quadlet-app.target` at 23:06:39Z — the agent did
not restart it), using the Fleet's image and the modelcar image volume:

```bash
sudo podman run -d --name act-eval --network host \
  -e ZENOH_ROUTER=10.0.0.48:7447 -e RMW_IMPLEMENTATION=rmw_zenoh_cpp \
  -e POLICY_DEVICE=cpu -e OMP_NUM_THREADS=8 -e TORCH_NUM_THREADS=8 \
  -e EVAL_MODE=true -e EVAL_EPISODES=20 -e EVAL_SEED_BASE=1000 \
  -e RECORD=false -e EPISODE_LEN=60 -e RESET_ARM=true \
  -e RANDOMIZE_CUBES=true -e RANDOMIZE_ONLY=cube_medium -e RANDOM_RADIUS=0.03 -e RANDOM_YAW_DEG=180 \
  -e MODEL_VERSION=eval-cpu-v2-ft160 -e POLICY_PATH=/modelcar/models/act \
  -v systemd-act-inference-128875-models:/modelcar:ro -v /var/lib/act-inference/data:/data:z \
  quay.io/jary/soarm-flywheel@sha256:29955e4e9422b194f950ba66d43689a2815a3670e5e0b398921d78501d5bb140
```

Baseline on the same seeds re-read from `docs/eval-records/phase3-ladder/eval-ft-160ep.json`:
17/20 (failures 1000, 1010, 1018). Pass: ≥ 15/20.

Run 23:08:43Z–23:17:18Z, stopped after five episodes because every result was structurally
impossible rather than a policy outcome:

```
[eval] ep 0 -> cubes=0/3 success=False steps=416 smooth=0.004519   (seed 1000; GPU: fail)
[eval] ep 1 -> cubes=0/3 success=False steps=382 smooth=0.00484    (seed 1001; GPU: pass, 1447 steps)
[eval] ep 2 -> cubes=0/3 success=False steps=365 smooth=0.005054   (seed 1002; GPU: pass, 1957 steps)
[eval] ep 3 -> cubes=0/3 success=False steps=373 smooth=0.004966   (seed 1003; GPU: pass, 1320 steps)
[eval] ep 4 -> cubes=0/3 success=False steps=435 smooth=0.004418   (seed 1004; GPU: pass, 1335 steps)
```

Container stopped and removed 23:17:18–23:17:29Z (full log kept on the VM as
`/home/jary/spike/eval-cpu-v2-ft160-INVALID-*.log`; no result JSON was written). The Fleet app
target was started again at 23:17:29Z and was `healthy` at 23:18:20Z with
`Published model_version: act-v2-ft160`.

Why it cannot work from the VM: the coordinator's seeded reset (`sim_reset.py` → `gz service
…/set_pose`) and its cube judge (`task_eval.evaluate_task()` → `gz topic -e -t
/world/pai_world/pose/info -n 1`, 8 s timeout, silent `{}` on failure) both use **Gazebo transport**,
not ROS/zenoh. From inside the VM container: `gz topic -l` does list `/world/pai_world/pose/info`
(multicast discovery crosses `br0`), but `gz topic -e … -n 1` receives nothing in 20 s and
`gz service -l` shows no `set_pose` service — the publisher/service data path back to the guest
does not come up (`so-arm-sim` runs `--network host` on `jary-ubuntu`, so its advertised endpoints
are host-side). So on the VM every `Resetting cubes (seed=N)` is a silent no-op and every cube
count is 0. The `steps` figure (~6 Hz of `/joint_states`) is a second, separate artefact: in eval
mode the coordinator counts joint states itself from a Python thread that is starved on a VM whose
8 vCPUs are saturated by torch; on the GPU host it sees 20–43 Hz.

The same gap qualifies the live-loop evidence above: the Fleet-managed coordinator on the VM also
never resets cubes, so after the first post-cut-over episode (`79d80ff1`, 1/3 at the emitter's early
evaluation, then all three placed later in that window) the following "successes" (`ba0d930c`
3/3 within 2 s of `start`, `355f8af7`, `9711f96d`, …) inherit cubes already on the tray. They are
valid evidence of the **lineage path** (VM coordinator → `/flywheel/model_version` → host emitter
→ curator → `episodes-curated/act-v2-ft160/`), not of task performance.

**Verdict: part 3 not run to a result; criterion 3 remains open.** What would make it valid:
(a) run the coordinator (reset + judge) where Gazebo transport is local — on the host — against
the VM's `/run_policy` action server, with the device container running the rosetta client only
(needs an entrypoint switch, F); or (b) make Gazebo transport reachable from the device (`GZ_IP` /
`GZ_PARTITION` on both ends and a route for the publisher back to the guest — untested, and it
touches `so-arm-sim`); or (c) move reset + judge behind ROS services on the host (the cleanest
contract for the Fury, where the sim is also on a different box than the device). Until one lands,
the desktop stand-in's episodes should be read as lineage proof only, and the runbook's Beat 6
should not quote their success rate.

## C3 — role split in place (2026-09-08 23:44Z → 2026-09-09), parts 2–3 re-run status

Setting: Fleet `210e79e` rolled to renderedVersion 4 (`UpToDate`, `Healthy`, podman `(healthy)`);
device container runs `ROLE=policy` from `quay.io/jary/soarm-flywheel@sha256:2ad1fb1c…` with
`ros2 param get /rosetta_client` → `actions_per_chunk 100`, `chunk_size_threshold 0.5`,
`policy_device cpu` (no CUDA-fallback line). Host coordinator `act-coordinator` (same image,
`ROLE=coordinator`, env/mounts of the old `act-inference` mirrored) started 23:54:11Z: it observed
`act-v2-ft160` from the device, reset cubes over host-local Gazebo transport (0/3 on the tray at
every sampled episode start, including after a 3/3 success), drove `/run_policy` on the VM, and
published `Dataset ref: 'bags/<sec>_<nsec>'` for every episode — so `dataset_path` is non-null
again. Emitter verdicts 23:54Z–00:45Z: 20 SUCCESS / 47 FAIL (real scenes, both classes); 62 bags
kept by the coordinator's own 3/3 verdict.

**Part 2 (re-measure at 100/0.5):** probe run inside the device container 23:54:49–00:01:49Z
(`cadence_probe.py 420 24.5`, 25 s windows — the mirrored loop uses `EPISODE_LEN=25`). The log
(`~/spike/cadence-c3-235449.log` on the VM) is **not yet read**: at 00:44:45Z the desktop root
filesystem filled (62 new bags, 79 GB, on 84 GB free) and qemu paused both VMs with an I/O error.
Table and verdict follow once the VM resumes.

**Part 3 (20-seed D020 eval, coordinator on the host, policy on the VM):** **not run** — same
outage; the procedure is `IMAGE=<digest> MODE=eval MODEL_VERSION=eval-cpu-v2-ft160
tools/host/run-coordinator.sh 20 1000` with the loop stopped. Pass ≥ 15/20 vs GPU 17/20.

Coordinator stopped 10:28:05Z (2188 `Goal rejected` cycles against the paused VM, all pruned).
Blocker and options: C3 decisions (disk).

## Files

- `device/spike/bench_cpu_forward.py` — the part-1 benchmark (re-run on the VM to get in-guest numbers)
- `device/README.md` — VM sizing, provisioning order, flags verified

## C3 part 2 — commanded-action cadence at 100/0.5 (probe log read after the 2026-09-09 recovery)

Log: `~/spike/cadence-c3-235449.log` on the VM (`cadence_probe.py 420 24.5`, same probe as C2,
inside the device container, `ROLE=policy`, `actions_per_chunk=100`, `chunk_size_threshold=0.5`).
Run 23:54:49–00:01:49Z, host coordinator driving 25 s episodes (`EPISODE_LEN=25`); eight full
24.5 s windows plus a partial ninth:

| ep | start (Z) | msgs | rate (wall) | gap p50 | p95 | p99 | max | > 40 ms | > 100 ms | > 200 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 23:55:27 | 1145 | 46.8 Hz | 20.1 ms | 22.3 | 26.5 | **312.8** | 7 | 2 | 2 |
| 2 | 23:56:17 | 1173 | 47.9 Hz | 20.1 ms | 22.5 | 25.5 | 61.0 | 3 | 0 | 0 |
| 3 | 23:57:02 | 1164 | 47.5 Hz | 20.1 ms | 23.0 | 29.9 | 112.4 | 8 | 1 | 0 |
| 4 | 23:57:47 | 1172 | 47.9 Hz | 20.2 ms | 22.8 | 26.5 | 64.7 | 4 | 0 | 0 |
| 5 | 23:58:37 | 1176 | 48.0 Hz | 20.1 ms | 22.5 | 27.0 | 63.3 | 2 | 0 | 0 |
| 6 | 23:59:22 | 1176 | 48.0 Hz | 20.1 ms | 22.6 | 26.8 | 59.0 | 4 | 0 | 0 |
| 7 | 00:00:07 | 1176 | 48.0 Hz | 20.1 ms | 22.6 | 25.7 | 35.0 | 0 | 0 | 0 |
| 8 | 00:00:52 | 1173 | 47.9 Hz | 20.1 ms | 22.5 | 26.6 | 99.6 | 4 | 0 | 0 |
| 9 (partial, 11.5 s) | 00:01:38 | 531 | 46.2 Hz | 20.1 ms | 22.8 | 24.9 | 35.1 | 0 | 0 | 0 |

All eight full windows: **9347 intervals, max 312.8 ms, 32 (0.34 %) over 40 ms**. Criterion "no
gap > 40 ms" → **still FAIL as written**. Against C2 (30/0.95, D053: 6408 intervals, max 335.2 ms,
398 = 6.21 % over 40 ms): the over-threshold share fell 18× (6.21 % → 0.34 %), the wall rate rose
from ~36 Hz to ~48 Hz (the client now misses ~4 % of 50 Hz ticks instead of ~28 %), p95 moved from
~55 ms to ~22.5 ms and p99 from ~225 ms to ~27 ms, and the > 200 ms stalls (28–46 per C2 episode)
are gone except for two in the first episode after the coordinator start (ep 1's 312.8 ms; the next
worst window is 112.4 ms, and two windows — ep 7 and the partial ep 9 — had no gap over 40 ms at
all). Excluding ep 1, 25 of 8203 intervals (0.30 %) exceed 40 ms with max 112.4 ms. The residual
gaps are still one forward pass each (~60–110 ms), landing where a chunk boundary meets a slow
forward; at 100/0.5 the queue hides most of them but not all. D053's conclusion stands: part 2 is
a FAIL on the letter of the criterion, D024's first fallback (raise the chunk toward
`n_action_steps`) does most of what it promised, and part 3 remains the arbiter.

## C3 part 3 — 20-seed D020 eval, coordinator on the host, policy on the VM: **PASS 18/20 (90 %)**

Run 11:15:46–11:40:03Z (2026-09-09), after the disk recovery (D062), with the disk guard armed and
266 GB free, the loop coordinator stopped, and the device app target restarted first (a goal left
executing at the 00:44Z pause made the rosetta client answer `Rejected: already running` to every
new goal; the first attempt at 11:15Z recorded 11 seeds as `goal rejected — episode recorded as
aborted` and was stopped — log `~/eval-cpu-v2-ft160-c3-INVALID-stale-goal.log`; see the recovery
addendum). Command, from the host:

```bash
IMAGE=quay.io/jary/soarm-flywheel@sha256:2ad1fb1c393a6a5c5281abab83187d9e4aeecc05fdca8e12b7d247ced0009e11 \
  MODE=eval MODEL_VERSION=eval-cpu-v2-ft160 tools/host/run-coordinator.sh 20 1000
```

Device: `ROLE=policy`, CPU, `actions_per_chunk=100`, `chunk_size_threshold=0.5`, served
`act-v2-ft160` (recorded as `served_model_version`). Host: reset + judge over local Gazebo
transport, D020 config (`EPISODE_LEN=60`, `cube_medium`, radius 0.03, yaw 180, `RECORD=false`).
Result JSON: `docs/eval-records/eval-cpu-v2-ft160.json` (copy of
`~/flywheel-data/eval/eval-cpu-v2-ft160.json`); all 20 episodes `goal_accepted: true`.

| seed | 1000 | 1001 | 1002 | 1003 | 1004 | 1005 | 1006 | 1007 | 1008 | 1009 | 1010 | 1011 | 1012 | 1013 | 1014 | 1015 | 1016 | 1017 | 1018 | 1019 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CPU (VM, 100/0.5) cubes | 3 | 3 | 3 | 3 | 3 | 3 | 3 | **1** | 3 | 3 | 3 | 3 | 3 | **2** | 3 | 3 | 3 | 3 | 3 | 3 |
| GPU (host, 30/0.95) | fail | pass | pass | pass | pass | pass | pass | pass | pass | pass | fail | pass | pass | pass | pass | pass | pass | pass | fail | pass |

```
[eval] DONE — 18/20 success (90.0%), mean_cubes=2.85, cubes_hist={'0': 0, '1': 1, '2': 1, '3': 18}, mean_smooth=0.005143
```

Successful episodes took 1172–2444 `/joint_states` steps (23–49 s at 50 Hz; the host coordinator
counts them, so the D056 step artefact is gone); the two failures ran to the 60 s cap (2955 and
2932 steps). **Criterion 3: 18/20 = 90 % vs the GPU same-seed baseline 17/20 = 85 % and the 86 %
full-run figure → PASS** (threshold ≥ 15/20). The two seed sets do not overlap: CPU missed 1007
and 1013, the GPU missed 1000, 1010 and 1018 — at n = 20 this is one run's noise on a ~85–90 %
policy, not evidence that CPU is better; the GPU baseline was also taken at 30/0.95 (D059 caveat).

**D024 status after C3:** criterion 1 PASS (part 1), criterion 2 FAIL as written but 0.34 % over
40 ms at 100/0.5 (C3 part 2), criterion 3 PASS. The first fallback (chunk toward `n_action_steps`,
D058) is applied and sufficient; lower RTF, vCPU pinning and VFIO were not needed. The desktop
stand-in is a valid device for the demo path on task success, with the cadence residual recorded.
