<!-- This project was developed with assistance from AI tools. -->
# RHAIIS smoke on the large slice — operator page

The coding assistant's first step: before any quadlet or Fleet work, does Red Hat AI
Inference Server 3.5.1 serve `RedHatAI/Qwen3-Coder-Next-NVFP4` on this machine at all. `61-rhaiis-smoke.sh`
starts one hand-run container on a CDI device, waits for it, asks it two things and measures it.

**It proves:** the arm64 image starts on a 64k-page kernel; vLLM accepts compute capability 10.3 and a MIG
instance handed over by CDI name alone (no `CUDA_VISIBLE_DEVICES`); the NVFP4 expert kernels and the
linear-attention kernels of `Qwen3NextForCausalLM` exist for this GPU; the model loads offline from
`/data/models`, read-only, SELinux-confined; chat and tool calling answer; and how long a cold and a warm
start take. **It does not prove:** anything about the routed network (loopback only), governance, restarts,
isolation from busy neighbours, or answer quality.

The command blocks are meant to be pasted as they are: no comments inside them. Nothing here needs the uplink.

## Run

The GPU has to be in tenants mode for the default device. `fury-mode` stops the robot loop to switch.

```
fury-mode status
fury-mode tenants
```

```
cd ~/flywheel-setup
./61-rhaiis-smoke.sh up
./61-rhaiis-smoke.sh ask
./61-rhaiis-smoke.sh bench 5
./61-rhaiis-smoke.sh status
./61-rhaiis-smoke.sh down
```

On the whole GPU instead (MIG off): `./61-rhaiis-smoke.sh up nvidia.com/gpu=all`. `up` refuses a device that is
not in `nvidia-ctk cdi list` and says what is. Flags live in one block at the top of the script; a running
container never picks up an edit, so every attempt is `down`, edit, `up`. `down purge` also drops the cache
volume. The full server log: `sudo podman logs rhaiis-smoke`. Everything the script prints is also in
`log/61-rhaiis-smoke.log`. What the image carries:

```
sudo podman run --rm --pull=never -e LD_PRELOAD= --entrypoint=/bin/bash registry.redhat.io/rhaii/vllm-cuda-rhel9@sha256:c056e61672b6aea489ad5dde0bd2f8497230f5333e87f7cf6c494eba3bfdc808 -c "pip list" | grep -i -E "^(vllm|torch|flashinfer|triton|transformers|nvidia-cutlass)"
```

## What a good start looks like

Timings are **estimates**, scaled from one public measurement of this same model family on vLLM 0.24.0 on a
20-core GB10 (26 min cold, 7 min warm [9]); nothing here has been measured on this machine yet. Note the real ones.

| # | In the filtered log | Expect |
|---|---|---|
| 1 | preflight: MIG mode, image entrypoint/user, `nvidia-smi -L` from a throwaway container showing **one** MIG device | seconds |
| 2 | `vLLM API server version …` (3.5.0 packages 0.24.0 [2]; 3.5.1 is not in the release notes yet), `non-default args`, `Resolved architecture: Qwen3NextForCausalLM`, `Using max model len 131072` | 0.5–1 min |
| 3 | backend choices: an attention backend line, `Using 'FLASHINFER_TRTLLM' NvFp4 MoE backend out of potential backends …`, `Using FlashInfer GDN prefill kernel (requested=auto, head_k_dim=128)` | — |
| 4 | `Loading safetensors checkpoint shards … k/10`, then `Model loading took ~45 GiB`; the `on the gpu:` heartbeat climbs to ~45 000 MiB (Red Hat lists 54.8 GB of vRAM for this model [4]) | 0.5–3 min; 6 min on the GB10 |
| 5 | profiling run, `torch.compile`, FlashInfer JIT (`flashinfer.jit` lines, or silence with the heartbeat flat). The long one, and the one the cache volume removes on the next `up` | cold 3–20 min, warm ~1 min |
| 6 | `Available KV cache memory: ~50 GiB`, `GPU KV cache size: … tokens`, `Maximum concurrency for 131,072 tokens per request: …x` | — |
| 7 | `Capturing CUDA graphs …`, `Graph capturing finished`, `init engine (profile, create kv cache, warmup model) took … seconds`, `Application startup complete.` | 0.2–1 min |

Then `ask` should print code, `finish_reason: tool_calls`, `call: square_the_number {"input_num": 1024}` and
`well-formed tool call: yes`. `bench` has no target yet; its numbers are the baseline the governed version and
the isolation test (Phase 6.4) get compared against.

## When it fails: symptom → cause → what to flip

One change per attempt. Variable names are the ones in the script's judgement block.

| Symptom in the log | Likely cause | Flip |
|---|---|---|
| `<jemalloc>: Unsupported system page size`, or the container (even the preflight `nvidia-smi`) exits at once with nothing else | the image preloads a jemalloc built for 4k pages; this kernel has 64k [1] | `clear_jemalloc=yes` (the default; this only appears if it was turned off) |
| preflight: `unresolvable CDI devices`, or `nvidia-smi -L` lists no or several MIG devices | CDI spec is stale after a mode switch, or the device is `all` with MIG on | `sudo systemctl restart nvidia-cdi-refresh`, then `nvidia-ctk cdi list`; pass one slice |
| `PermissionError … /models/…`, or an offline-mode error that names huggingface.co, right after start | the container cannot read the mount: SELinux label, or mode bits for uid 1001 (`up` checks the second) | `sudo ausearch -m avc -ts recent`; `selinux=disable` [1]; `chmod -R o+rX` on the model |
| `error: unrecognized arguments`, a usage text, or `… --model …` rejected | the image's entrypoint is not the API server the docs imply (the preflight prints it) | `explicit_entry=yes` [5] |
| `Free memory on device … is less than desired GPU memory utilization` | something else holds memory on that slice, or the wrong device [7] | `./61-rhaiis-smoke.sh status`; free the slice; lower `gpu_util` |
| `No available memory for the cache blocks`, or `To serve at least one request with the model's max seq len … KV cache is needed` | budget too small after weights and profiling [7] | lower `max_len` (32768 [3]); raise `gpu_util` to 0.95; `kv_dtype=fp8` |
| `torch.OutOfMemoryError` during `Capturing CUDA graphs` or the profiling run | graphs and workspaces above the budget | lower `gpu_util`; lower `max_seqs`; `enforce_eager=yes` |
| `NvFp4 MoE backend '…' does not support the deployment configuration`, `no kernel image is available for execution on the device`, `CUDA error: illegal …`, or a crash right after the `NvFp4 MoE backend` line | the FlashInfer TRT-LLM NVFP4 expert kernels do not run on sm_103 in this build [6] | `moe_backend=flashinfer_cutlass`, then `cutlass`, then `marlin` (slow but plain) |
| minutes of silence after the weights with the heartbeat flat; `nvcc`/`ninja` errors; `Permission denied` under `/tmp/.cache/flashinfer`; a download attempt to an NVIDIA artifact host | FlashInfer is JIT-compiling sm_103 kernels, or wants cubins the image does not ship (it "compiles/downloads kernels on first use" [8]) | wait once (cache volume keeps it); if it cannot compile or fetch: `attn_backend=FLASH_ATTN`, `moe_backend=cutlass`, `gdn_backend=triton` |
| an MLIR / cuTe-DSL / `legalize` error, or a crash on the first prompt, near `GDN prefill` | FlashInfer's Blackwell GDN prefill kernel is JIT-built through nvidia-cutlass-dsl, with a known packaging fault upstream [6] | `gdn_backend=triton` |
| crash or hang on the first request with the attention backend in the trace | FlashInfer attention on sm_103 / head size 256 | `attn_backend=FLASH_ATTN`, then `TRITON_ATTN` (Red Hat's own 3.5 known-issue workaround form [2]) |
| Inductor / Triton compile errors (`ptxas`, `BackendCompilerFailed`), or a hang in `torch.compile` | compile stack on aarch64 + sm_103 | `enforce_eager=yes` [2]; if that fixes it, try `down purge` once with it back off |
| `Loading safetensors checkpoint shards: 0%` and nothing more | the mmap loader stalling [1] (documented for unified-memory GB10; not expected on HBM) | `load_format=fastsafetensors` |
| `ask`: `well-formed tool call: NO` and the text shows `<tool_call>` / `<function=…>` markup | the parser does not match what the model emits | `tool_parser=qwen3_xml` [6]; check the model's `chat_template.jinja` was read (no `--chat-template` is passed) |
| `"auto" tool choice requires --enable-auto-tool-choice …` (HTTP 400) | flags lost in an edit | restore the two tool flags [3] |
| the server dies with `signal=SIGTERM` in its shutdown lines and no error | something outside stopped it — seen upstream with **rootless** podman over ssh without linger [10] | not expected rootful; check `journalctl -e` for who stopped it |
| `up` times out, container still running, log still moving | cold JIT on a busy machine | keep watching; raise `ready_timeout`; the next start is warm |

Could not be checked from documents and will only show on the machine: whether the image ships FlashInfer's
cubin and JIT-cache packages; the image's entrypoint, user and vLLM version (3.5.1); whether a named volume
over `/tmp` sits well with the image; NVML calls against a MIG parent from inside a confined container.

## Decisions for the governed version (not made here)

1. **Where it lives.** A device belongs to exactly one Fleet [11], and this host is in `act-inference`: the server
   becomes a second application in that Fleet's template (or the Fleet is generalised), switched by labels the way
   `AddDevice=` already is (`gitops/rhem/fleet-act-inference.yaml:241`).
2. **Naming the slice.** A label value cannot hold `:` (`fleet-act-inference.yaml:24-27`), so it is a `MIG-<uuid>`
   name. Check that the uuid survives `fury-mode flywheel` → `tenants`; if not, a stable alias is needed.
3. **The act switch.** In flywheel mode the slice does not exist and the unit would restart forever. Who stops and
   starts it: `fury-mode` refusing as it does for the policy (`fury-mode.sh:30-35`), a hub-side `flightctl app stop`,
   or the MIG layout becoming a Fleet-delivered file.
4. **Health.** `/health` on the API port. The image may not carry `curl`: a `python3 -c` urllib one-liner is the safe
   `HealthCmd`. Start period = this smoke's measured cold start with margin — `HealthStartupCmd` plus
   `Notify=healthy` fits better than one long `HealthStartPeriod`; `TimeoutStartSec=300` and the Fleet's
   `defaultUpdateTimeout: 30m` (`fleet-act-inference.yaml:103,257`) are both shorter than a cold JIT start may be.
5. **Stop.** vLLM shuts down on SIGTERM, unlike the ROS tree: no `StopSignal=` override expected; take `StopTimeout`
   and the exit status from how `down` behaves here (`SuccessExitStatus=143` if it reports failed).
6. **Restart.** `Restart=always` with a start limit: a crash loop here costs a 47 GB load per turn.
   `HealthOnFailure=kill` as for the policy.
7. **Model mount.** `:ro,z` relabels once and is right for a directory shared with nothing else but re-used across
   container instances (`Z` would relabel privately on every start); plain `:ro` needs `SecurityLabelDisable=true`,
   which this host has not needed. Or Red Hat's signed ModelCar
   (`registry.redhat.io/rhai/modelcar-qwen3-coder-next-nvfp4:3.0` [3]) as a `Driver=image` volume like the ACT
   one — governed and verified on pull, at the price of a second 48 GB copy in root's storage; arm64 not checked.
8. **Rootful or rootless.** The docs run rootless with `--userns=keep-id:uid=1001` [1]; the Fleet is rootful by choice
   (`fleet-act-inference.yaml:203-204`). Rootless adds linger [10], CDI spec access and `container_use_devices`.
9. **Exposure.** Bind address (tailnet and `10.20.0.1`, or all with a firewalld rule), `Network=host` or
   `PublishPort=`, an API key (`--api-key`, Red Hat's "Configuring API key authentication" [1]) and how the Fleet
   delivers it, TLS or none on a routed private network.
10. **Caches.** Keep a volume for the JIT and compile caches so a restart takes minutes less, and decide its
    lifecycle under the agent; or accept cold starts.
11. **Image trust and pulls.** `registry.redhat.io` is already sigstore-enforced in the Fleet's `policy.json`
    (`fleet-act-inference.yaml:127-133`); the device needs registry credentials for a first pull, and Phase 8b
    needs the image and its signature in the internal registry.
12. **Keep from the smoke:** `LD_PRELOAD=` while the kernel is 64k, the offline and no-telemetry environment, a
    fixed `--served-model-name` as the client contract; take `max_len`, `gpu_util`, `max_seqs` from measured numbers.
13. **Neighbours.** Cold JIT uses every core it finds: CPU and memory limits (`PodmanArgs=`) so the sim and
    training tenants are not starved; `/metrics` is there for Phase 8's panels.

## Sources

1. Red Hat AI Inference 3.5, *Getting started* — CUDA podman chapter (the `podman run`, `label=disable`, `:Z`,
   `keep-id:uid=1001`, `--shm-size=4g`), DGX Spark chapter (`-e LD_PRELOAD=` on 64k RHEL, `fastsafetensors`,
   `--max-num-seqs 4`), package listing, API keys:
   https://docs.redhat.com/en/documentation/red_hat_ai_inference/3.5/html-single/getting_started/index
2. Release notes 3.5 (vLLM v0.24.0 in 3.5.0; known issues worked around with `--enforce-eager`,
   `--attention-backend`, `--moe-backend`):
   https://docs.redhat.com/en/documentation/red_hat_ai_inference/3.5/html-single/release_notes/index — supported
   hardware (GB200/GB300, AArch64, CUDA 13.0):
   https://docs.redhat.com/en/documentation/red_hat_ai/latest/html-single/supported_product_and_hardware_configurations/index
3. Model card and `config.json` at the pinned revision:
   https://huggingface.co/RedHatAI/Qwen3-Coder-Next-NVFP4/blob/27a8f16f463b9a13c91c332c40cf93e09717347e/README.md
   — upstream card (non-thinking only, 262144 context, "reduce to 32768 if the server fails to start"):
   https://huggingface.co/Qwen/Qwen3-Coder-Next
4. Red Hat AI validated models (this model: 54.8 GB vRAM, validated from 1×B200 and 1×H100 up):
   https://docs.redhat.com/en/documentation/red_hat_ai/latest/html-single/validated_models/index
5. Red Hat AI Inference 3.5, standalone container on OpenShift (`python -m vllm.entrypoints.openai.api_server`):
   https://docs.redhat.com/en/documentation/red_hat_ai_inference/3.5/html-single/deploy_the_standalone_red_hat_ai_inference_container_in_openshift_container_platform/index
6. vLLM v0.24.0 source — `vllm/model_executor/layers/fused_moe/oracle/nvfp4.py`, `vllm/platforms/cuda.py`,
   `vllm/model_executor/layers/mamba/gdn/qwen_gdn_linear_attn.py` (and NVIDIA/cutlass issues 3170, 3259),
   `vllm/tool_parsers/__init__.py`, `vllm/engine/arg_utils.py`, `CMakeLists.txt` (10.3 in the arch lists):
   https://github.com/vllm-project/vllm/tree/v0.24.0
7. vLLM v0.24.0 `vllm/v1/worker/utils.py` (`request_memory`), `vllm/utils/mem_utils.py`, `vllm/v1/core/kv_cache_utils.py`.
8. FlashInfer v0.6.12 README (cubin and jit-cache packages "for faster initialization and offline usage") and
   `flashinfer/jit/fused_moe.py`, `flashinfer/jit/gemm/core.py` (sm103 modules): https://github.com/flashinfer-ai/flashinfer/tree/v0.6.12
9. vLLM issue 48031 and the measurement on PR 48871 (this model family, 0.24.0, GB10, cold 1575 s):
   https://github.com/vllm-project/vllm/issues/48031 —
   https://github.com/vllm-project/vllm/pull/48871#issuecomment-5139558421
10. vLLM issue 50067 (0.24.0 on RHEL 10 aarch64 with 64k pages; the "crash" was rootless podman without linger):
    https://github.com/vllm-project/vllm/issues/50067 — MIG uuids in `CUDA_VISIBLE_DEVICES` still fail (issues 41848,
    35295), which is why the slice is handed over by CDI name only.
11. flightctl v1.3.0 `docs/user/using/managing-fleets.md` ("a device cannot be member of more than one fleet"):
    https://github.com/flightctl/flightctl/blob/v1.3.0/docs/user/using/managing-fleets.md
