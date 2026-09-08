<!-- This project was developed with assistance from AI tools. -->
# CPU inference spike — desktop stand-in device (D024)

**Date:** 2026-09-08 · **Gates:** Phase 4.5 C (Fleet-delivered application) · **Decision:** D024

The desktop stand-in device is a RHEL 10 KVM VM (8 vCPU / 16 GiB, no GPU) that runs the ACT
policy on CPU while the sim, camera bridge and host runner stay on the host RTX 5090. D024 gates
that stand-in on three criteria. This record holds the numbers for criterion 1 and the procedure
for criteria 2–3.

| # | Criterion (D024) | Result |
|---|---|---|
| 1 | p95 forward latency < 0.5 × (`n_action_steps`/50) s | **PASS** — 83.2 ms vs 1000 ms (8 threads) |
| 2 | `ros2 topic hz` on the commanded-action topic: no gap > 40 ms | **pending** — needs the VM and a scheduled stop of host `act-inference` |
| 3 | 20-seed D020 eval within 10 points of GPU v2 (86%) | **pending** — same prerequisite; 20-seed GPU baseline is 17/20 = 85% |

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

## Files

- `device/spike/bench_cpu_forward.py` — the part-1 benchmark (re-run on the VM to get in-guest numbers)
- `device/README.md` — VM sizing, provisioning order, flags verified
