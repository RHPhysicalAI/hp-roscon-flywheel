#!/bin/bash
# The training tenant's loop (flywheel/training-tenant.container, MIG slice 0:2): an endless sweep of short, real
# ACT fine-tunes. Each round takes the next line of a fixed list - starting checkpoint, learning rate, seed - runs
# lerobot-train for TENANT_STEPS steps and appends one line to rounds.jsonl. It runs INSIDE the container, as its
# pid 1; on the host it is only ever installed, never run.
#
# The lerobot-train line is the governed runner's (src/host-runner/host_runner.py, train_command) with four
# differences: the output directory, a short fixed --steps, --log_freq=100 (a loss line every half minute on
# stage, not every 1000 steps) and the two swept values, --policy.optimizer_lr and --seed.
#
# It is a showcase and must stay outside the governed flywheel. In here that is not a rule to remember: the
# dataset and the checkpoints are read-only mounts, the container has no network, and /flywheel/tenant-train is
# the only place that takes a write.
#
#   /flywheel/tenant-train/round-<n>-<config>/run/        lerobot's output (checkpoint + optimizer state)
#   /flywheel/tenant-train/round-<n>-<config>/train.log   everything lerobot printed
#   /flywheel/tenant-train/rounds.jsonl                   one line per finished round, failed ones included
#
# A failed round ends the script with 1: systemd's RestartSec is the pause before the next try. A stop (SIGTERM,
# the mode switch) ends it with 143 at once; the round it interrupts leaves no line and is not resumed.
#
# This project was developed with assistance from AI tools.
set -uo pipefail        # not -e: a failed round is recorded first, then ends the script
set -m                  # job control: a background job is a process group of its own, so a stop reaches its children too
shopt -s nullglob
export LC_ALL=C         # glob order below is byte order

out=/flywheel/tenant-train
ledger=$out/rounds.jsonl
dataset_id=flywheel-ladder-160
dataset=/flywheel/datasets/$dataset_id
steps=${TENANT_STEPS:-3000}
workers=${TENANT_NUM_WORKERS:-8}
keep=${TENANT_KEEP_ROUNDS:-3}
min_free_gb=${TENANT_MIN_FREE_GB:-50}
log_freq=100

declare -A ckpt=(
    [teacher]=/flywheel/train/upstream-act-teacher/checkpoints/last/pretrained_model
    [v2]=/flywheel/train/act-v2-ft160/checkpoints/last/pretrained_model
)
# start  learning-rate  seed. 1e-5 is the governed recipe's rate, 1000 the seed lerobot uses when the runner
# names none; one sweep is eight rounds.
configs=(
    "teacher 1e-5 1000"
    "v2 1e-5 1000"
    "teacher 3e-5 1000"
    "v2 3e-5 1000"
    "teacher 1e-5 2000"
    "v2 1e-5 2000"
    "teacher 3e-5 2000"
    "v2 3e-5 2000"
)

say() { echo "[tenant] $*"; }
die() { say "$*" >&2; exit 1; }

child='' follower=''
# a whole process group each (set -m): lerobot-train with its loader workers, and the log follower's pipeline
end_group() { [[ -z $1 ]] || kill -TERM -- "-$1" 2>/dev/null; }
on_stop() {
    say "stopping: the round in progress is abandoned, the next start begins a new one"
    end_group "$child"; end_group "$follower"
    exit 143
}
trap on_stop TERM INT

for v in steps workers keep min_free_gb; do
    [[ ${!v} =~ ^[1-9][0-9]*$ ]] || die "$v='${!v}' is not a whole number above zero - fix the Environment= line in training-tenant.container"
done
(( steps >= log_freq )) || die "TENANT_STEPS=$steps is below --log_freq=$log_freq: the round would print no loss line"
[[ -d $out && -w $out ]]          || die "$out is not a writable directory - ./72-training-tenant-install.sh install on the host"
[[ -f $dataset/meta/info.json ]]  || die "no dataset at $dataset - on the host: ./53-stage-promotion.sh"
for k in "${!ckpt[@]}"; do
    [[ -f ${ckpt[$k]}/model.safetensors ]] || die "no $k checkpoint at ${ckpt[$k]} - on the host: ./53-stage-promotion.sh"
done
command -v lerobot-train >/dev/null || die "lerobot-train is not on PATH in this image"

# One more than the highest round anything remembers: the directories (an interrupted round leaves one and no
# ledger line, and lerobot-train refuses an output directory that exists) and the ledger (it outlives them).
next_round() {
    local max=0 d num
    for d in /flywheel/tenant-train/round-*; do
        [[ ${d##*/} =~ ^round-([0-9]{6})- ]] || continue
        num=$((10#${BASH_REMATCH[1]}))
        (( num <= max )) || max=$num
    done
    if [[ -s $ledger ]]; then
        num=$(tail -n 1 "$ledger" | sed -n 's/^{"round": \([0-9][0-9]*\),.*/\1/p')
        [[ -z $num ]] || (( 10#$num <= max )) || max=$((10#$num))
    fi
    echo $((max + 1))
}

# Keep the newest $keep round directories, remove the rest. This is the one recursive delete in the tenant:
#   - what it could reach at all is the container's own filesystem and /flywheel/tenant-train: the flywheel's
#     data is mounted read-only or not mounted
#   - the glob hangs off a literal path, not off "$out": no variable that could be empty sits in front of it
#   - a candidate has to be a real directory (not a link) whose whole path matches the round pattern, and it is
#     removed by that exact path, after --, without -f
#   - keep is at least 1 (checked above), so the round that just finished always stays
prune() {
    local d all=() i
    for d in /flywheel/tenant-train/round-*; do
        [[ -d $d && ! -L $d ]] || continue
        [[ $d =~ ^/flywheel/tenant-train/round-[0-9]{6}-[A-Za-z0-9.-]+$ ]] || continue
        all+=("$d")
    done
    # byte order is round order: the number is zero-padded
    for (( i = 0; i < ${#all[@]} - keep; i++ )); do
        rm -r -- "${all[i]}" || die "could not remove ${all[i]} - the disk would fill one round at a time"
        say "removed ${all[i]##*/} (keeping the last $keep rounds)"
    done
}

# One ledger line from the round's log. lerobot prints, every log_freq steps, a line like
#   step:200 smpl:2K ep:1 epch:0.01 loss:0.123 grdn:4.5 lr:1.0e-05 updt_s:0.251 data_s:0.012
# - the values are means over those steps. updt_s and data_s below are means over the round's lines without the
# first one (warm-up). A field lerobot did not print becomes null, never a guess.
record() {
    python3 - "$@" <<'PYEOF' >> "$ledger"
import json, math, re, sys, time

n, name, start, lr, seed, steps, workers, rc, wall, log = sys.argv[1:11]
steps, wall, rc = int(steps), int(wall), int(rc)
try:
    text = open(log, errors="replace").read().replace("\r", "\n")
except OSError:
    text = ""
lines = [row for row in text.split("\n") if "loss:" in row]


def field(line, key):
    m = re.search(r"(?:^|\s)" + re.escape(key) + r":([0-9.eE+-]+|nan|inf)", line)
    try:
        v = float(m.group(1)) if m else None
    except ValueError:
        v = None
    return v if v is not None and math.isfinite(v) else None


def mean(key):
    vals = [v for v in (field(row, key) for row in (lines[1:] if len(lines) > 1 else lines)) if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


print(json.dumps({
    "round": int(n), "dir": name, "start": start, "lr": lr, "seed": int(seed), "steps": steps,
    "num_workers": int(workers), "ok": rc == 0, "exit": rc, "wall_s": wall,
    "steps_per_s": round(steps / wall, 3) if rc == 0 and wall > 0 else None,
    "last_loss": field(lines[-1], "loss") if lines else None,
    "updt_s": mean("updt_s"), "data_s": mean("data_s"), "loss_lines": len(lines),
    "ended": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}))
PYEOF
}

say "sweep of ${#configs[@]} configurations, $steps steps a round, $workers loader workers, last $keep rounds kept; dataset $dataset_id"
while :; do
    free_gb=$(df -BG --output=avail "$out" | tail -n 1 | tr -dc '0-9')
    (( ${free_gb:-0} >= min_free_gb )) || die "only ${free_gb:-?} GiB free under $out, wanted $min_free_gb: not starting a round on a disk the flywheel needs"

    n=$(next_round)
    read -r start lr seed <<<"${configs[(n - 1) % ${#configs[@]}]}"
    name=$(printf 'round-%06d-%s-lr%s-s%s' "$n" "$start" "$lr" "$seed")
    dir=$out/$name
    log=$dir/train.log
    mkdir "$dir" || die "could not create $dir"
    say "round $n: from the $start checkpoint, lr $lr, seed $seed, $steps steps -> ${name}/"

    t0=$SECONDS
    # lerobot-train writes its log file itself and a follower passes its loss lines, the end and anything that went
    # wrong on to the journal. Not one pipeline: a pipeline ends when the last process holding the pipe does, and a
    # loader worker that outlives a crashed trainer would hold the round for ever. This way the round ends when
    # lerobot-train does, with its own exit status.
    : > "$log"
    ( tail -n +1 -F "$log" 2>/dev/null | sed -u 's/\r/\n/g' | grep --line-buffered -E 'loss:|End of training|rror|Traceback' ) &
    follower=$!
    lerobot-train --policy.path="${ckpt[$start]}" --dataset.repo_id="$dataset_id" --dataset.root="$dataset" \
        --dataset.video_backend=pyav --policy.device=cuda --policy.push_to_hub=false \
        --output_dir="$dir/run" --steps="$steps" --save_freq="$steps" --log_freq="$log_freq" \
        --num_workers="$workers" --policy.optimizer_lr="$lr" --seed="$seed" > "$log" 2>&1 &
    child=$!
    # In the background and waited for: bash runs a trap only between commands, and wait is the one a signal interrupts.
    wait "$child"; rc=$?
    wall=$((SECONDS - t0))
    end_group "$child"; child=''            # a worker left behind must not hold the slice into the next round
    sleep 2                                 # the follower's last lines
    end_group "$follower"; wait "$follower" 2>/dev/null; follower=''

    record "$n" "$name" "$start" "$lr" "$seed" "$steps" "$workers" "$rc" "$wall" "$log" ||
        die "could not append round $n to $ledger"
    if (( rc != 0 )); then
        say "round $n FAILED (lerobot-train exit $rc after $wall s). The end of ${name}/train.log:"
        tr '\r' '\n' < "$log" | tail -n 25
        prune
        exit 1
    fi
    say "round $n done in $((wall / 60)) min $((wall % 60)) s: $(tail -n 1 "$ledger")"
    prune
done
