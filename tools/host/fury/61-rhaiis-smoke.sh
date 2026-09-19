#!/bin/bash
# Smoke test for act 2's coding assistant: does Red Hat AI Inference Server 3.5.1 serve
# RedHatAI/Qwen3-Coder-Next-NVFP4 on this silicon at all - aarch64, 64k pages, sm_103, one MIG slice.
# A hand-started container, not the governed quadlet: loopback only, nothing survives `down`.
#
#   ./61-rhaiis-smoke.sh up [cdi-device] [gpu-share]
#                                          default nvidia.com/gpu=0:0 (the 3g slice, tenants mode). Starts the server
#                                          detached, follows the load, returns once /health answers. Ctrl-C only
#                                          stops the watching; the container carries on.
#                                          With MIG off, beside the running flywheel:  up nvidia.com/gpu=all 0.5
#                                          (half of the GPU, about what the 3g slice offers; the default share is
#                                          meant for a slice this server has to itself)
#   ./61-rhaiis-smoke.sh ask               one chat completion and one tool call; tokens/s            (no root)
#   ./61-rhaiis-smoke.sh bench [n]         n streamed requests of 256 tokens, one after another (default 5):
#                                          time to first token, tokens/s                              (no root)
#   ./61-rhaiis-smoke.sh status            container state, memory on the GPU, last 15 log lines
#   ./61-rhaiis-smoke.sh down [purge]      stop and remove; purge also drops the kernel-cache volume
#
# What each log line means, what to flip when it fails, and where every choice below comes from: 61-rhaiis-smoke.md
#
# This project was developed with assistance from AI tools.
set -uo pipefail
export LC_ALL=C
case ${1:-} in up|down|status) [[ $EUID -eq 0 ]] || exec sudo "$0" "$@" ;; esac
here=$(dirname "$(readlink -f "$0")")
log=$here/log/$(basename "$0" .sh).log
mkdir -p "$here/log" 2>/dev/null
# ask and bench run as an ordinary user and log only where a sudo run has not already taken the file
if { : >> "$log"; } 2>/dev/null; then exec > >(tee -a "$log") 2>&1; fi

# 3.5.1, arm64, pulled by digest into root's storage. The namespace is rhaii/ from 3.4 on, not rhaiis/.
img=registry.redhat.io/rhaii/vllm-cuda-rhel9@sha256:c056e61672b6aea489ad5dde0bd2f8497230f5333e87f7cf6c494eba3bfdc808
model=/data/models/RedHatAI/Qwen3-Coder-Next-NVFP4      # 60-model-fetch.sh, revision 27a8f16f463b
mnt=/models/Qwen3-Coder-Next-NVFP4
name=rhaiis-smoke
served=qwen3-coder-next
port=8000
url=http://127.0.0.1:$port

# ==== judgement calls - flip these between attempts ====================================================================
# One reason and one source each; the sources in full are in 61-rhaiis-smoke.md. vLLM file names are as of v0.24.0,
# which is what 3.5.0 packages. 3.5.1 has no release-notes section yet: the first lines of the log give the version.
max_len=131072          # context. Native is 262144; KV is only ~24 KiB a token (12 full-attention layers, 2 KV heads,
                        #   config.json), so length is not what fills the slice. Upstream card: 32768 if it will not start.
gpu_util=0.90           # a share of the slice, not of the GPU: vLLM sizes itself from CUDA's mem_get_info, which sees
                        #   the MIG instance (vllm/v1/worker/utils.py request_memory). vLLM's default is 0.92; a little under it for a first start.
max_seqs=8              # one user and a few parallel tool calls; fewer CUDA graphs to capture. Red Hat's single-GPU
                        #   workstation example uses 4 (Getting started, DGX Spark chapter).
kv_dtype=auto           # bf16 KV cache. fp8 halves it; not needed at this size, and one more kernel path.
tool_parser=qwen3_coder # model card. qwen3_xml is the other name registered for this family (vllm/tool_parsers/__init__.py).
enforce_eager=no        # yes: no torch.compile, no CUDA graphs. Slower, but two subsystems fewer in a failing start.
moe_backend=auto        # NVFP4 experts. auto tries flashinfer_trtllm first on sm_10x; then flashinfer_cutlass, cutlass,
                        #   marlin, emulation (vllm/model_executor/layers/fused_moe/oracle/nvfp4.py).
attn_backend=auto       # auto puts FLASHINFER first on sm_10x; then FLASH_ATTN, TRITON_ATTN (vllm/platforms/cuda.py).
gdn_backend=auto        # linear-attention prefill. auto is FlashInfer's JIT-compiled kernel on Blackwell with CUDA 13;
                        #   triton skips that JIT (vllm/model_executor/layers/mamba/gdn/qwen_gdn_linear_attn.py).
load_format=auto        # fastsafetensors if the weight load sits at 0 % (Getting started, DGX Spark chapter).
clear_jemalloc=yes      # the image preloads a jemalloc built for 4k pages and this kernel has 64k: the container just
                        #   stops. Red Hat's documented fix is -e LD_PRELOAD= (Getting started, DGX Spark chapter).
selinux=confined        # confined: the model directory is mounted :ro,z (relabelled once, shared). disable: the documented
                        #   --security-opt=label=disable with a plain :ro mount. CUDA ran confined on this host (D144).
explicit_entry=no       # the docs put server arguments straight after the image. yes: --entrypoint python3 with
                        #   -m vllm.entrypoints.openai.api_server, the form Red Hat's OpenShift deployment uses.
cache_vol=rhaiis-smoke-cache    # named volume over /tmp, where the image keeps its vLLM, Triton and FlashInfer caches
                        #   (image env: VLLM_CACHE_ROOT, TRITON_CACHE_DIR, FLASHINFER_WORKSPACE_BASE). Empty: no volume,
                        #   and every `up` pays the kernel JIT again.
host_ip=127.0.0.1       # with host networking vLLM finds "its" address by routing a socket towards 8.8.8.8; one GPU in one
                        #   process needs only loopback, uplink or not (vllm/utils/network_utils.py get_ip). Empty: vLLM decides.
ready_timeout=3600      # seconds. A cold FlashInfer JIT took 26 min for this model on a 20-core GB10 (vllm issue 48031).
# =======================================================================================================================

follow='' tmp=''
unfollow() { if [[ -n $follow ]]; then { kill "$follow"; wait "$follow"; } 2>/dev/null; follow=''; fi; }
cleanup() { unfollow; [[ -n $tmp ]] && rm -rf "$tmp"; }
trap cleanup EXIT
die() { echo "${0##*/}: $*" >&2; exit 1; }
need() { local c; for c in "$@"; do command -v "$c" >/dev/null || die "$c is missing"; done; }
mig() { nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader; }
cdi_has() { nvidia-ctk cdi list 2>/dev/null | awk -v d="$1" '{gsub(/^[ \t]+|[ \t]+$/, "")} $0 == d {f = 1} END {exit !f}'; }

# MiB held on the GPU by this container's own processes; other tenants are not counted
gpu_used() {
    local pids
    pids=$(podman top "$name" hpid 2>/dev/null | tail -n +2 | tr -s ' \n' '|' | sed 's/^|//; s/|$//')
    [[ -n $pids ]] || { echo 0; return; }
    nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits 2>/dev/null |
        awk -F', *' -v re="^($pids)\$" '$1 ~ re {s += $2} END {print s + 0}'
}

gpu_report() {
    if [[ $(mig) == Enabled ]]; then
        nvidia-smi | awk '/MIG devices:/ {p = 1} /Processes:/ {p = 0} p'
    else
        nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader
    fi
    nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
}

model_id() {
    need curl jq
    id=$(curl -fsS -m 10 "$url/v1/models" 2>/dev/null | jq -r '.data[0].id // empty')
    [[ -n $id ]] || die "nothing answers on $url - is it up?  $0 status"
}

up() {
    local dev=${1:-nvidia.com/gpu=0:0} mode state next t0 shards run pre=()
    need podman nvidia-smi nvidia-ctk curl jq ss runuser
    if [[ -n ${2:-} ]]; then
        [[ $2 =~ ^0\.[0-9]+$ ]] || die "the gpu share is a fraction such as 0.5, not '$2'"
        gpu_util=$2
    fi
    date -u
    mode=$(mig)
    echo "mig mode: $mode    requested device: $dev"
    cdi_has "$dev" || die "$dev is not a CDI device on this host right now. What there is:
$(nvidia-ctk cdi list 2>/dev/null | grep 'nvidia.com/' | sed 's/^/    /')
0:0 to 0:3 exist only in tenants mode (fury-mode tenants). With MIG off:  $0 up nvidia.com/gpu=all"
    if [[ $mode == Enabled && $dev == *=all ]]; then
        echo "note: MIG is on and the device is 'all' - the container sees every slice and CUDA takes the first one"
    fi

    podman image exists "$img" || die "the image is not in root's storage. Once, with the uplink:  sudo podman pull $img"
    [[ -f $model/config.json ]] || die "no model at $model - ./60-model-fetch.sh first"
    shards=("$model"/model-*.safetensors)
    [[ ${#shards[@]} -eq 10 ]] || die "expected 10 safetensors shards in $model, found ${#shards[@]} - ./60-model-fetch.sh check"
    # the server runs as uid 1001 inside, which is nobody in particular out here
    runuser -u nobody -- test -r "$model/config.json" ||
        die "$model is not readable by other users, and the container's uid 1001 is one. Fix:  chmod -R o+rX $model  (and o+x on the directories above it)"
    podman container exists "$name" && die "a $name container already exists - flags only apply to a new one:  $0 down"
    [[ -z $(ss -Hltn "sport = :$port") ]] || die "port $port is taken: $(ss -Hltnp "sport = :$port" | head -1)"

    [[ $clear_jemalloc == yes ]] && pre+=(-e LD_PRELOAD=)
    [[ $selinux == disable ]] && pre+=(--security-opt label=disable)
    echo "## image: entrypoint, cmd, user"
    podman image inspect --format '{{.Config.Entrypoint}}  {{.Config.Cmd}}  user={{.Config.User}}' "$img"
    echo "## what a container on $dev sees"
    podman run --rm --pull=never --device "$dev" "${pre[@]}" --entrypoint nvidia-smi "$img" -L ||
        die "a throwaway container could not run nvidia-smi on $dev - CDI, SELinux or jemalloc; nothing was started"

    run=(podman run -d --name "$name" --pull=never --network host --device "$dev" --shm-size=4g "${pre[@]}"
         -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1)
    [[ -n $host_ip ]] && run+=(-e "VLLM_HOST_IP=$host_ip")
    if [[ $selinux == disable ]]; then run+=(-v "$model:$mnt:ro"); else run+=(-v "$model:$mnt:ro,z"); fi
    [[ -n $cache_vol ]] && run+=(-v "$cache_vol:/tmp")
    [[ $explicit_entry == yes ]] && run+=(--entrypoint python3)
    run+=("$img")
    [[ $explicit_entry == yes ]] && run+=(-m vllm.entrypoints.openai.api_server)
    run+=(--model "$mnt" --served-model-name "$served" --host 127.0.0.1 --port "$port"
          --tensor-parallel-size 1 --max-model-len "$max_len" --gpu-memory-utilization "$gpu_util"
          --max-num-seqs "$max_seqs" --kv-cache-dtype "$kv_dtype"
          --enable-auto-tool-choice --tool-call-parser "$tool_parser")
    [[ $enforce_eager == yes ]]  && run+=(--enforce-eager)
    [[ $moe_backend != auto ]]   && run+=(--moe-backend "$moe_backend")
    [[ $attn_backend != auto ]]  && run+=(--attention-backend "$attn_backend")
    [[ $gdn_backend != auto ]]   && run+=(--gdn-prefill-backend "$gdn_backend")
    [[ $load_format != auto ]]   && run+=(--load-format "$load_format")
    echo "## starting"
    printf '%q ' "${run[@]}"; echo
    "${run[@]}" || die "podman could not start the container"

    # the lines that show progress or trouble, matched in lower case; tqdm redraws with \r, so that ends a record too
    local show='version|non-default args|architecture|max model len|backend|kernel|loading|took|compil|flashinfer|jit'
    show+='|autotun|still waiting|kv cache|concurrency|cuda graph|capturing|init engine|startup complete|uvicorn'
    show+='|warning|error|traceback|exception|jemalloc|page size|denied|no such file|killed|signal|out of memory'
    echo "## waiting for /health, up to $((ready_timeout / 60)) min: weights, kernel JIT, CUDA graphs. Filtered log:"
    podman logs -f "$name" > >(awk -v re="$show" 'BEGIN {RS = "[\r\n]"} tolower($0) ~ re {print substr($0, 1, 200); fflush()}') 2>&1 &
    follow=$!
    t0=$SECONDS next=$((SECONDS + 30))
    until curl -fsS -o /dev/null -m 3 "$url/health" 2>/dev/null; do
        state=$(podman inspect --format '{{.State.Status}}' "$name" 2>/dev/null)
        if [[ $state != running ]]; then
            unfollow
            echo "## the container is ${state:-gone}, exit code $(podman inspect --format '{{.State.ExitCode}}' "$name" 2>/dev/null), after $((SECONDS - t0)) s. Its last lines:"
            podman logs --tail 40 "$name" 2>&1 | cut -c1-300
            die "the server died while starting. Look the symptom up in 61-rhaiis-smoke.md, flip one thing, then:  $0 down  and  $0 up"
        fi
        if (( SECONDS - t0 >= ready_timeout )); then
            die "not ready after $ready_timeout s, and still running. Keep watching:  sudo podman logs -f $name"
        fi
        if (( SECONDS >= next )); then
            printf '   .. %4d s   on the gpu: %s MiB\n' "$((SECONDS - t0))" "$(gpu_used)"
            next=$((SECONDS + 30))
        fi
        sleep 5
    done
    unfollow
    echo "## ready after $((SECONDS - t0)) s"
    curl -fsS -m 10 "$url/v1/models" | jq -r '.data[] | "serving \(.id), max_model_len \(.max_model_len)"'
    echo "## what vLLM chose"
    podman logs "$name" 2>&1 | grep -iE 'api server version|backend|gdn prefill|model loading took|kv cache|maximum concurrency|init engine' | cut -c1-200 | head -20
    echo "## on the device"
    gpu_report
    date -u
    echo "next:  $0 ask"
}

# one POST to the chat endpoint: body in $tmp/out, wall seconds in $secs
post() {
    secs=$(curl -sS -m 600 -o "$tmp/out" -w '%{time_total}' -H 'Content-Type: application/json' -d "$1" "$url/v1/chat/completions") ||
        die "the request failed"
    jq -e '.choices[0]' "$tmp/out" >/dev/null 2>&1 || { cat "$tmp/out"; echo; die "no choices in the answer"; }
}
rate() {
    jq -r '.usage | "\(.prompt_tokens) \(.completion_tokens)"' "$tmp/out" |
        awk -v s="$secs" '{printf "%d prompt + %d completion tokens in %.2f s = %.1f tokens/s, prefill included\n", $1, $2, s, (s > 0 ? $2 / s : 0)}'
}

ask() {
    local req ok
    model_id
    tmp=$(mktemp -d)
    echo "## chat ($id)"
    req=$(jq -n --arg m "$id" '{model: $m, max_tokens: 300, messages: [{role: "user",
        content: "Write a Python function that clamps a joint angle to its limits. Code only."}]}')
    post "$req"
    jq -r '.choices[0].message.content' "$tmp/out"
    rate

    echo "## tool call (the example from the model card)"
    req=$(jq -n --arg m "$id" '{model: $m, max_tokens: 300, tool_choice: "auto",
        messages: [{role: "user", content: "square the number 1024"}],
        tools: [{type: "function", function: {name: "square_the_number", description: "output the square of the number.",
            parameters: {type: "object", required: ["input_num"],
                properties: {input_num: {type: "number", description: "input_num is a number that will be squared"}}}}}]}')
    post "$req"
    jq -r '.choices[0] | "finish_reason: \(.finish_reason)", (.message.tool_calls[]? | "call: \(.function.name) \(.function.arguments)")' "$tmp/out"
    # well-formed: the right function, arguments that parse as JSON, the argument a number
    ok=$(jq -r '[.choices[0].message.tool_calls[]? | select(.type == "function" and .function.name == "square_the_number")
                 | (.function.arguments | try fromjson catch null) | select(type == "object" and (.input_num | type) == "number")] | length' "$tmp/out")
    if [[ $ok -ge 1 ]]; then
        echo "well-formed tool call: yes"
    else
        echo "well-formed tool call: NO. What came back as text instead:"
        jq -r '.choices[0].message.content // "(nothing)"' "$tmp/out" | head -20
    fi
    rate
}

bench() {
    local n=${1:-5} req i line first usage t0 t1
    [[ $n =~ ^[1-9][0-9]*$ ]] || die "bench wants a count, got '$n'"
    [[ -n ${EPOCHREALTIME:-} ]] || die "bench needs bash 5 for its clock"
    model_id
    tmp=$(mktemp -d)
    # ignore_eos (a vLLM extension) makes every answer exactly max_tokens long
    req=$(jq -n --arg m "$id" '{model: $m, max_tokens: 256, ignore_eos: true, stream: true, stream_options: {include_usage: true},
        messages: [{role: "user", content: "Explain step by step how a PID controller holds a robot arm joint on its target angle."}]}')
    echo "## $n requests, one at a time, 256 tokens each ($id)"
    for i in $(seq 1 "$n"); do
        first='' usage=''
        t0=$EPOCHREALTIME
        # builtins only in this loop: a process per chunk would be slower than the stream it is timing
        while IFS= read -r line; do
            [[ $line == data:* ]] || continue
            if [[ -z $first && ( $line == *'"content":"'[!\"]* || $line == *'"content": "'[!\"]* ) ]]; then first=$EPOCHREALTIME; fi
            if [[ $line == *'"usage":{'* || $line == *'"usage": {'* ]]; then usage=${line#data: }; fi
        done < <(curl -sS -N -m 600 -H 'Content-Type: application/json' -d "$req" "$url/v1/chat/completions")
        t1=$EPOCHREALTIME
        [[ -n $usage ]] || die "request $i brought no usage record back - is the server still up?  $0 status"
        jq -r '.usage.completion_tokens' <<<"$usage" |
            awk -v i="$i" -v t0="$t0" -v tf="${first:-0}" -v t1="$t1" '{
                ttft = (tf > 0 ? tf - t0 : -1); dec = (tf > 0 && t1 > tf && $1 > 1 ? ($1 - 1) / (t1 - tf) : 0)
                printf "%d %.3f %.1f %.1f %d\n", i, ttft, dec, $1 / (t1 - t0), $1}' >> "$tmp/rows"
        awk 'END {printf "  #%d  ttft %6.3f s   decode %6.1f tokens/s   end to end %6.1f tokens/s   (%d tokens)\n", $1, $2, $3, $4, $5}' "$tmp/rows"
    done
    awk '{if ($2 >= 0) {t += $2; d += $3; k++}; e += $4} END {
        printf "mean over %d: ttft %s s, decode %s tokens/s, end to end %.1f tokens/s\n", NR,
            (k ? sprintf("%.3f", t / k) : "n/a"), (k ? sprintf("%.1f", d / k) : "n/a"), e / NR}' "$tmp/rows"
    echo "decode = after the first token. ttft -1: no content chunk was recognised in that stream. Request 1 also pays any warm-up."
}

status() {
    need podman nvidia-smi
    echo "mig mode: $(mig)"
    if podman container exists "$name"; then
        podman inspect --format '{{.Name}}: {{.State.Status}}, exit code {{.State.ExitCode}}, started {{.State.StartedAt}}' "$name"
        echo "health: HTTP $(curl -s -o /dev/null -m 3 -w '%{http_code}' "$url/health")   held on the gpu by this container: $(gpu_used) MiB"
    else
        echo "no $name container"
    fi
    echo "## on the device"
    gpu_report
    if podman container exists "$name"; then echo "## last 15 log lines"; podman logs --tail 15 "$name" 2>&1 | cut -c1-300; fi
}

down() {
    need podman
    if podman container exists "$name"; then
        podman stop -t 30 "$name" >/dev/null; podman rm "$name" >/dev/null
        echo "removed $name"
    else
        echo "no $name container"
    fi
    if [[ ${1:-} == purge && -n $cache_vol ]] && podman volume exists "$cache_vol"; then
        podman volume rm "$cache_vol" >/dev/null && echo "removed volume $cache_vol"
    fi
}

case ${1:-} in
up)     up "${2:-}" "${3:-}" ;;
ask)    ask ;;
bench)  bench "${2:-}" ;;
status) status ;;
down)   down "${2:-}" ;;
*)      echo "usage: ${0##*/} up [cdi-device] [gpu-share] | ask | bench [n] | status | down [purge]" >&2; exit 1 ;;
esac
