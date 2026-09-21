#!/bin/bash
# The paired evaluation's own rig (D153): a second sim with its own Zenoh router, its own policy and its own
# eval coordinator in ONE podman pod - a private network namespace and its own GZ_PARTITION - so a candidate
# and the incumbent are scored on the same seeds while the production loop keeps running. Two policy servers
# on one Zenoh graph reject every goal (D132), and stopping the production policy needs a human.
#
#   ./51-eval-rig.sh run <mv> <checkpoint> [n] [seed_base]
#                                          one eval by hand; defaults: 5 episodes from seed 1000. checkpoint is a
#                                          pretrained_model directory under /data/flywheel, or the word modelcar
#                                          (the pinned incumbent image). The record lands in /data/flywheel/eval/<mv>.json
#   ./51-eval-rig.sh serve-one             take the oldest request in /data/flywheel/eval/requests, run it, write its
#                                          .done. No request: exit 0. This is what flywheel-eval.service starts.
#   ./51-eval-rig.sh status                rig pod, requests directory, last record written
#   ./51-eval-rig.sh down                  remove the rig pod (this also ends an eval that is using it)
#
# The file contract with the training runner, the verify-first experiment and the failure table: 51-eval-rig.md
#
# This project was developed with assistance from AI tools.
set -uo pipefail
export LC_ALL=C
case ${1:-} in
run|serve-one|status|down) [[ $EUID -eq 0 ]] || exec sudo "$0" "$@" ;;
*)  echo "usage: ${0##*/} run <mv> <checkpoint> [n] [seed_base] | serve-one | status | down" >&2; exit 1 ;;
esac
here=$(dirname "$(readlink -f "$0")")
# a checkout logs next to itself like the other steps; the installed copy is no place for a log directory
case $here in
/usr/*) log=/var/log/flywheel-eval-rig.log ;;
*)      mkdir -p "$here/log"; log=$here/log/$(basename "$0" .sh).log ;;
esac
# tee has to outlive a ctrl-c, or the teardown below writes into a closed pipe and dies half way
exec > >(trap '' INT; exec tee -a "$log") 2>&1
trap '' PIPE

sim_img=localhost/soarm-sim:arm64
rt=quay.io/jary/soarm-flywheel@sha256:5eba6ca4ee8acf7be87ec8da852d314d6dd16d76cfbce09a1581dbf8c5c94837
car=quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b
data=/data/flywheel
evald=$data/eval            # <mv>.json: the records the runner reads, and reuses when the seeds match
reqd=$evald/requests
work=$evald/rig-work        # the rig coordinator's /data. Its own record is rig-work/eval/<mv>.json; bags, if
                            #   recording were ever switched on, would be rig-work/bags - never production's
raw=$evald/rig-raw          # the rig emitter's local JSON, never production's episodes/raw
pod=eval-rig
c_sim=$pod-sim c_policy=$pod-policy c_coord=$pod-coordinator
partition=evalrig
lock=/run/lock/flywheel-eval-rig.lock
lock_wait=7200              # serve-one queues behind an eval started by hand rather than failing the request
min_turn=5                  # a turn that took a request lasts at least this long: a queue of refused ones must
                            #   not look like a spinning trigger to the start limit in flywheel-eval.service

mine=no follow='' stem='' started='' result='' fail='' deadline=0
mv='' ckpt='' n='' seed='' src=''

die() { fail=$*; echo "${0##*/}: $*" >&2; exit 1; }
need() { local c; for c in "$@"; do command -v "$c" >/dev/null || die "$c is missing"; done; }
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
mig() { nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader; }
alive() { [[ $(podman inspect --format '{{.State.Status}}' "$1" 2>/dev/null) == running ]]; }
unfollow() { if [[ -n $follow ]]; then { kill "$follow"; wait "$follow"; } 2>/dev/null; follow=''; fi; }

# Atomic, and it has to work even when jq is what is missing.
write_done() {      # status result error
    local tmp
    tmp=$(mktemp "$reqd/.done.XXXXXX") || return 1
    if ! jq -n --arg s "$1" --arg r "$2" --arg e "$3" --arg t0 "$started" --arg t1 "$(now)" '
            {status: $s, result: (if $r == "" then null else $r end), error: (if $e == "" then null else $e end),
             started: $t0, finished: $t1}' > "$tmp" 2>/dev/null; then
        printf '{"status": "%s", "result": null, "error": "%s", "started": "%s", "finished": "%s"}\n' \
            "$1" "$(tr -d '\\"' <<<"$3" | tr -c '[:print:]' ' ')" "$started" "$(now)" > "$tmp"
    fi
    chmod 0644 "$tmp" && mv -f -T "$tmp" "$reqd/$stem.done"
}

# Every way out comes through here. The .done goes first: the pod can take half a minute to stop, and a
# unit being stopped has only TimeoutStopSec left.
finish() {
    local rc=$?
    trap '' INT TERM
    unfollow
    if [[ -n $stem ]]; then
        if [[ $rc -eq 0 && -n $result ]]; then
            write_done ok "$result" ''
        else
            [[ $rc -ne 0 ]] || rc=1
            write_done failed '' "${fail:-ended with exit code $rc before a record was written}"
        fi
        # shellcheck disable=SC2181     # either branch above
        if [[ $? -eq 0 ]]; then echo "## wrote $reqd/$stem.done"; else echo "could NOT write $reqd/$stem.done"; fi
    fi
    # only a pod this run created: a refusal because one exists must not remove somebody else's
    if [[ $mine == yes ]]; then
        echo "## removing the $pod pod"
        podman pod rm -f -t 10 "$pod" >/dev/null 2>&1 || podman pod rm -f "$pod" >/dev/null 2>&1 ||
            echo "the $pod pod could not be removed:  $0 down"
    fi
    if [[ -n $stem ]] && (( SECONDS < min_turn )); then sleep $((min_turn - SECONDS)); fi
    exit "$rc"
}

# ---- validation, shared by run and serve-one ------------------------------------------------------------------------

# sets mv ckpt n seed, and src: the directory to mount, empty for the modelcar image
check_args() {
    local root real file
    mv=$1 ckpt=$2 n=$3 seed=$4 src=''
    # no leading dot: the path unit's glob does not see dot files, which leaves the writer room for a temp name
    [[ $mv =~ ^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$ ]] ||
        die "model_version must be 1-64 characters of [A-Za-z0-9._-] and not start with a dot"
    [[ $n =~ ^[0-9]{1,3}$ ]] || die "n must be a whole number from 1 to 500"
    n=$((10#$n))
    (( n >= 1 && n <= 500 )) || die "n must be a whole number from 1 to 500"
    # sign and digits apart: a leading zero would otherwise be read as octal
    [[ $seed =~ ^(-?)0*([0-9]{1,10})$ ]] || die "seed_base must be a whole number between -2000000000 and 2000000000"
    seed=$(( ${BASH_REMATCH[1]}${BASH_REMATCH[2]} ))
    (( seed >= -2000000000 && seed <= 2000000000 )) || die "seed_base must be a whole number between -2000000000 and 2000000000"
    case $ckpt in
    modelcar) ;;
    HF) die "checkpoint HF is not supported on this host: the upstream teacher would be fetched from the hub at start, unpinned and unsigned. Use modelcar for the incumbent, or a pretrained_model directory under $data/" ;;
    /*) # the characters first, so that nothing below prints or mounts anything odd (':' and ',' split a mount)
        [[ $ckpt =~ ^[A-Za-z0-9._/@+=-]+$ ]] || die "checkpoint path may only hold [A-Za-z0-9._/@+=-]"
        root=$(realpath -e -- "$data" 2>/dev/null) || die "$data does not exist"
        real=$(realpath -e -- "$ckpt" 2>/dev/null) || die "checkpoint $ckpt does not exist"
        # resolved, so neither .. nor a symlink leads out of the data root
        [[ $real == "$root"/* ]] || die "checkpoint $ckpt resolves to $real, which is not under $root/"
        [[ $real =~ ^[A-Za-z0-9._/@+=-]+$ ]] || die "checkpoint resolves to a path with characters outside [A-Za-z0-9._/@+=-]"
        [[ -d $real ]] || die "checkpoint $real is not a directory"
        # a link pointing out of the directory would dangle inside the container
        file=$(realpath -e -- "$real/model.safetensors" 2>/dev/null) || die "no model.safetensors in $real - is it the pretrained_model directory?"
        [[ -f $file && $file == "$real"/* ]] || die "model.safetensors in $real is not a file inside that directory"
        src=$real ;;
    *)  die "checkpoint must be an absolute path under $data/, or the word modelcar" ;;
    esac
}

# Strict on the four fields; any other key is reported and ignored.
parse_request() {
    jq -r --arg stem "$2" '
        def whole(lo; hi): type == "number" and . == floor and . >= lo and . <= hi;
        def bad(m): "ERR\t" + m;
        if type != "object" then bad("the request is not a JSON object")
        elif (.model_version | type) != "string" then bad("model_version is missing or not a string")
        elif .model_version != $stem then bad("model_version does not equal the file name")
        elif (.checkpoint | type) != "string" then bad("checkpoint is missing or not a string")
        elif (.n | whole(1; 500) | not) then bad("n must be a whole number from 1 to 500")
        elif (.seed_base | whole(-2000000000; 2000000000) | not) then bad("seed_base must be a whole number between -2000000000 and 2000000000")
        else ["OK", .model_version, .checkpoint, (.n | floor | tostring), (.seed_base | floor | tostring),
              (keys - ["model_version", "checkpoint", "n", "seed_base"] | join(" "))] | @tsv
        end' "$1" 2>/dev/null
}

# ---- the rig --------------------------------------------------------------------------------------------------------

gone() {            # container, what it was doing
    unfollow
    echo "## $1 is $(podman inspect --format '{{.State.Status}}, exit code {{.State.ExitCode}}' "$1" 2>/dev/null || echo gone). Its last lines:"
    podman logs --tail 25 "$1" 2>&1 | cut -c1-300
    die "$1 stopped $2 - its last lines are in $log"
}

# 0 the line is there, 1 out of time, 2 the container died. Counted, not grep -q: that leaves early, podman
# takes a SIGPIPE on a long log, and pipefail then reports a line that was found as missing.
has_line() { [[ $(podman logs "$1" 2>&1 | grep -c -E "$2") -gt 0 ]]; }
wait_line() {       # container, pattern, seconds
    local t0=$SECONDS
    until has_line "$1" "$2"; do
        alive "$1" || return 2
        (( SECONDS - t0 < $3 && SECONDS < deadline )) || return 1
        sleep 3
    done
}

# camera frames delivered in 10 s, counted inside the pod: nothing is published to the host
frames='
import sys, time, urllib.request
r = urllib.request.urlopen("http://127.0.0.1:8081/" + sys.argv[1], timeout=5)
t, n = time.time(), 0
while time.time() - t < 10:
    n += r.read(65536).count(b"Content-Type: image/jpeg")
print(n)
'
both_cameras='
import json, sys, urllib.request
h = json.load(urllib.request.urlopen("http://127.0.0.1:8081/health", timeout=3))
sys.exit(0 if h.get("wrist") and h.get("static") else 1)
'
# shellcheck disable=SC2016     # expanded by the shell in the container
ros_env='source /opt/ros/$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash; export ZENOH_SESSION_CONFIG_URI=/tmp/zenoh_session.json5 RMW_ZENOH_CONFIG_FILE=/tmp/zenoh_session.json5 RMW_ZENOH_ROUTER_CHECK_ATTEMPTS=0'

preflight() {
    local mode c
    need podman nvidia-smi jq realpath timeout
    mode=$(mig 2>/dev/null) || die "nvidia-smi could not report the MIG mode"
    [[ $mode == Disabled ]] ||
        die "MIG is $mode: the sim's cameras render with OpenGL, which this GPU only offers with MIG off. fury-mode flywheel first"
    if podman pod exists "$pod"; then
        die "an $pod pod already exists and no eval holds the lock, so it is a leftover. Look, then remove it:  $0 status  and  $0 down"
    fi
    for c in "$sim_img" "$rt"; do
        podman image exists "$c" || die "image $c is not in root's storage, and the rig never pulls"
    done
    if [[ -z $src ]]; then
        podman image exists "$car" || die "the modelcar image $car is not in root's storage, and the rig never pulls"
    fi
}

eval_once() {
    local native=$work/eval/$mv.json label=${src:-$car} model i ok cubes servers state rc gl st wr
    deadline=$((SECONDS + n * 120 + 900))      # 60 s an episode, reset and settle on top, then the start-up
    echo "## eval $mv: $n episodes from seed $seed, checkpoint $label, at most $((deadline - SECONDS)) s"
    mkdir -p "$evald" "$work/eval" "$raw" || die "could not create the rig's directories under $evald"
    rm -f "$native"                            # a record left by a run that failed must not pass for this one's

    # bridge, never host, and nothing published: 7447 and 8081 exist a second time in here, unseen from outside.
    # 9>&- below: the lock must die with this script, not live on in a container's monitor process
    podman pod create --name "$pod" --network bridge >/dev/null || die "could not create the $pod pod"
    mine=yes

    # CURATOR_URL empty on purpose: an eval episode must never reach the hub's curator. Eval mode sends the
    # emitter no start signal either, so rig-raw stays empty - the mount is for the day that changes.
    podman run -d --pod "$pod" --name "$c_sim" --pull=never --stop-signal SIGINT --device nvidia.com/gpu=all \
        -e NVIDIA_DRIVER_CAPABILITIES=all -e "GZ_PARTITION=$partition" -e CURATOR_URL= \
        -v "$raw:/data/episodes/raw:z" "$sim_img" 9>&- >/dev/null || die "the rig sim did not start"
    echo "## waiting for the sim (up to 5 min)"
    wait_line "$c_sim" '\[entrypoint\] All processes started' 300
    case $? in 1) gone "$c_sim" "short of 'All processes started' in time" ;; 2) gone "$c_sim" "while starting" ;; esac
    if has_line "$c_sim" 'controller activation may have failed'; then
        gone "$c_sim" "being useful: its controllers did not activate, the arm cannot be driven"
    fi
    ok=no
    for i in $(seq 1 24); do
        if timeout -k 2 10 podman exec "$c_sim" python3 -c "$both_cameras" >/dev/null 2>&1; then ok=yes; break; fi
        alive "$c_sim" || gone "$c_sim" "before its cameras delivered"
        sleep 5
    done
    [[ $ok == yes ]] || gone "$c_sim" "short of fresh frames from both cameras after $((i * 5)) s"
    gl=$(podman exec "$c_sim" bash -c 'grep -h -m1 GL_VENDOR /root/.gz/rendering/ogre2.log 2>/dev/null' | sed 's/.*GL_VENDOR = //')
    st=$(timeout -k 2 30 podman exec "$c_sim" python3 -c "$frames" static 2>/dev/null)
    wr=$(timeout -k 2 30 podman exec "$c_sim" python3 -c "$frames" wrist 2>/dev/null)
    echo "renderer: ${gl:-unknown}    frames in 10 s: static ${st:-?}, wrist ${wr:-?}  (30 fps is 300)"
    # a software renderer manages under 2 fps: the score would measure the renderer, not the policy
    [[ -z $gl || $gl == *NVIDIA* ]] || die "the rig sim renders on '$gl', not on the GPU - the eval would not be comparable"

    # GZ_PARTITION on all three: the coordinator resets and judges cubes through gazebo transport, and the
    # policy container is where the probe below runs
    model=(--mount "type=image,source=$car,destination=/modelcar" -e POLICY_PATH=/modelcar/models/act)
    [[ -z $src ]] || model=(-v "$src:/model:ro,z" -e POLICY_PATH=/model)
    # no scheduled health check: the one in this image hangs when it runs before the version is published
    podman run -d --pod "$pod" --name "$c_policy" --pull=never --no-healthcheck --stop-signal SIGINT \
        --device nvidia.com/gpu=all -e ROLE=policy -e POLICY_DEVICE=cuda -e "MODEL_VERSION=$mv" \
        -e ZENOH_ROUTER=127.0.0.1:7447 -e ACTIONS_PER_CHUNK=100 -e CHUNK_SIZE_THRESHOLD=0.5 \
        -e "GZ_PARTITION=$partition" "${model[@]}" "$rt" 9>&- >/dev/null || die "the rig policy did not start"
    echo "## waiting for the policy to publish its model version (up to 5 min)"
    wait_line "$c_policy" 'Published model_version' 300
    case $? in 1) gone "$c_policy" "short of publishing its model version in time" ;; 2) gone "$c_policy" "while loading" ;; esac
    echo "## health"
    ok=no
    for i in $(seq 1 12); do
        if timeout -k 2 30 podman exec "$c_policy" /healthcheck.sh; then ok=yes; break; fi
        alive "$c_policy" || gone "$c_policy" "before it was healthy"
        alive "$c_sim" || gone "$c_sim" "while the policy was starting"
        sleep 5
    done
    [[ $ok == yes ]] || gone "$c_policy" "short of healthy after $((i * 5)) s"

    # The two things this design rests on, asked from the runtime image before an hour goes into episodes:
    # cube poses cross from the sim to a pod-mate, and the rig's graph has one policy server - its own.
    # Without the first the coordinator scores every episode 0 and still writes a well-formed record.
    cubes=0
    for i in 1 2 3; do
        cubes=$(timeout -k 2 20 podman exec "$c_policy" bash -c "$ros_env; timeout -k 2 10 gz topic -e -n 1 -t /world/pai_world/pose/info" 2>/dev/null |
                grep -c -E 'name: "cube_(small|medium|large)"')
        (( cubes >= 3 )) && break
        sleep 3
    done
    (( cubes >= 3 )) || die "cube poses do not reach the runtime image over gazebo transport (saw $cubes of 3, GZ_PARTITION=$partition): every episode would score 0"
    servers=$(timeout -k 2 20 podman exec "$c_policy" bash -c "$ros_env; timeout -k 2 10 ros2 action info /run_policy" 2>/dev/null |
              sed -n 's/^Action servers: //p' | head -1)
    echo "gazebo transport: $cubes cube poses seen from the policy container    /run_policy servers on the rig's graph: ${servers:-unreadable}"
    if [[ ${servers:-1} =~ ^[0-9]+$ ]] && (( ${servers:-1} > 1 )); then
        die "$servers policy servers on the rig's graph: it is not isolated from production, and every goal on both sides gets rejected (D132)"
    fi

    # The pinned D020 scene of tools/host/run-coordinator.sh (MODE=eval) - keep the two in step. That script
    # itself cannot be used: it hard-codes --network host and mounts production's bags directory.
    # RECORD=false: the recorder is not even launched, so an eval writes no bags at all.
    # POLICY_PATH is only copied into the record by this role: it says which checkpoint was scored.
    podman run -d --pod "$pod" --name "$c_coord" --pull=never --no-healthcheck --stop-signal SIGINT \
        -e ROLE=coordinator -e ZENOH_ROUTER=127.0.0.1:7447 -e "MODEL_VERSION=$mv" -e "GZ_PARTITION=$partition" \
        -e EVAL_MODE=true -e "EVAL_EPISODES=$n" -e "EVAL_SEED_BASE=$seed" -e "POLICY_PATH=$label" \
        -e RECORD=false -e EPISODE_LEN=60 -e RESET_ARM=true \
        -e RANDOMIZE_CUBES=true -e RANDOMIZE_ONLY=cube_medium -e RANDOM_RADIUS=0.03 -e RANDOM_YAW_DEG=180 \
        -v "$work:/data:z" "$rt" 9>&- >/dev/null || die "the eval coordinator did not start"
    echo "## eval running"
    podman logs -f "$c_coord" 2>&1 9>&- &
    follow=$!
    # watched rather than waited on: a sim or policy that dies under the coordinator would otherwise turn into
    # a record full of zeros or rejected goals, and a signal is acted on within one sleep
    while state=$(podman inspect --format '{{.State.Status}}' "$c_coord" 2>/dev/null); [[ $state == running ]]; do
        alive "$c_sim"    || gone "$c_sim" "during the eval"
        alive "$c_policy" || gone "$c_policy" "during the eval"
        (( SECONDS < deadline )) || { unfollow; die "the eval did not finish inside its budget of $((n * 120 + 900)) s for $n episodes"; }
        sleep 5
    done
    unfollow
    rc=$(podman inspect --format '{{.State.ExitCode}}' "$c_coord" 2>/dev/null)

    [[ -f $native ]] || die "the coordinator ended with exit code ${rc:-?} and wrote no record to $native"
    [[ ${rc:-0} == 0 ]] || echo "note: the coordinator wrote its record and then left with exit code $rc"
    jq -e --argjson n "$n" --argjson s "$seed" '
        (.aggregate | type) == "object" and (.episodes | type) == "array" and (.episodes | length) == $n
        and .eval_config.seed_base == $s and .eval_config.episodes == $n' "$native" >/dev/null 2>&1 ||
        die "the record in $native is not the one asked for (aggregate, $n episodes, seed_base $seed) - left there"
    # the runner reuses a record whose seeds match: one with nothing scored must never get to where it looks
    jq -e '.aggregate.n > 0' "$native" >/dev/null 2>&1 ||
        die "no episode was scored: all $n goals were rejected by the rig's policy server. The record is left in $native"
    [[ ! -e $evald/$mv.json ]] || echo "note: replacing the earlier $evald/$mv.json"
    mv -f -T "$native" "$evald/$mv.json" || die "could not move the record to $evald/$mv.json"
    result=$evald/$mv.json
}

report() {
    echo "## result: $result"
    jq '.aggregate' "$result"
    jq -c '.episodes[]? | {seed, cubes_placed, goal_accepted, served_model_version, steps}' "$result"
    [[ $(jq -r '.aggregate.goal_rejected // 0' "$result") == 0 ]] || echo "note: goals were rejected inside the rig - those episodes are not in n"
}

take_lock() {       # seconds to wait; 0 refuses at once
    local t0=$SECONDS
    exec 9>>"$lock" || die "could not open $lock"
    until flock -n 9; do
        (( SECONDS - t0 < $1 )) || return 1
        (( (SECONDS - t0) % 300 )) || echo "another eval holds $lock - waiting"
        sleep 5
    done
}

run() {
    [[ $# -ge 2 && $# -le 4 ]] || die "usage: ${0##*/} run <mv> <checkpoint> [n] [seed_base]"
    need flock
    check_args "$1" "$2" "${3:-5}" "${4:-1000}"
    take_lock 0 || die "another eval is running (it holds $lock):  $0 status"
    preflight
    date -u
    eval_once
    report
    date -u
}

serve_one() {
    local name out tag r_mv r_ckpt r_n r_seed extra
    need flock find sort
    mkdir -p "$reqd" || die "could not create $reqd"
    take_lock "$lock_wait" || die "gave up after $lock_wait s behind another eval; the request stays queued"

    # Oldest first. Anything the path unit's glob matches has to be taken, whatever it is - a directory or a
    # link left in place would start the service again the moment it ends, for ever. Dot files are the writer's.
    name=''
    IFS= read -r -d '' name < <(find "$reqd" -mindepth 1 -maxdepth 1 -name '*.json' ! -name '.*' -printf '%T@\t%f\0' | sort -z -n | head -z -n 1)
    name=${name#*$'\t'}
    if [[ -z $name ]]; then echo "no request in $reqd"; exit 0; fi

    date -u
    started=$(now)
    rm -f "$reqd/${name%.json}.done"           # an answer to an earlier request of this name is not this one's
    mv -f -T "$reqd/$name" "$reqd/$name.taken" || die "could not take $reqd/$name"
    stem=${name%.json}                         # from here on every way out writes $stem.done
    echo "## took request $name"

    [[ -f $reqd/$name.taken && ! -L $reqd/$name.taken ]] || die "the request is not a regular file"
    (( $(stat -c %s "$reqd/$name.taken") <= 65536 )) || die "the request is larger than 64 KiB"
    command -v jq >/dev/null || die "jq is missing"
    out=$(parse_request "$reqd/$name.taken" "$stem")
    # a writer that did not rename into place may still have been writing
    [[ -n $out ]] || { sleep 2; out=$(parse_request "$reqd/$name.taken" "$stem"); }
    [[ -n $out ]] || die "the request is not valid JSON"
    IFS=$'\t' read -r tag r_mv r_ckpt r_n r_seed extra <<<"$out"
    [[ $tag == OK ]] || die "${r_mv:-the request could not be read}"
    [[ -z $extra ]] || echo "note: ignoring keys the contract does not have: $extra"
    check_args "$r_mv" "$r_ckpt" "$r_n" "$r_seed"
    preflight
    eval_once
    report
    date -u
}

status() {
    local newest
    echo "mig mode: $(mig 2>/dev/null || echo unreadable)"
    if podman pod exists "$pod"; then
        podman ps -a --filter "pod=$pod" --format '{{.Names}}  {{.Status}}'
        if alive "$c_sim"; then
            echo "rig cameras: $(timeout -k 2 10 podman exec "$c_sim" python3 -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8081/health", timeout=3).read().decode())' 2>&1 | tail -1)"
            echo "rig frames in 10 s: static $(timeout -k 2 30 podman exec "$c_sim" python3 -c "$frames" static 2>/dev/null || echo '?')  (30 fps is 300)"
        fi
    else
        echo "no $pod pod"
    fi
    if ( exec 9>>"$lock" && flock -n 9 ) 2>/dev/null; then echo "lock: free"; else echo "lock: held - an eval is running"; fi
    echo "## units"
    for u in flywheel-eval.path flywheel-eval.service; do printf '%-24s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null)"; done
    echo "## requests ($reqd)"
    find "$reqd" -mindepth 1 -maxdepth 1 -printf '%TY-%Tm-%Td %TH:%TM  %8s  %f\n' 2>/dev/null | sort | tail -20
    echo "## last record written"
    newest=$(find "$evald" -mindepth 1 -maxdepth 1 -type f -name '*.json' -printf '%T@\t%p\n' 2>/dev/null | sort -n | tail -1 | cut -f2-)
    if [[ -n $newest ]]; then
        ls -l --time-style=long-iso "$newest"
        jq -c '{model_version, policy_path, timestamp, seed_base: .eval_config.seed_base, episodes: .eval_config.episodes, aggregate}' "$newest"
    else
        echo "none in $evald"
    fi
}

down() {
    need podman
    if podman pod exists "$pod"; then
        podman pod rm -f -t 10 "$pod" >/dev/null && echo "removed the $pod pod"
    else
        echo "no $pod pod"
    fi
}

trap finish EXIT
trap 'fail="interrupted by SIGTERM"; exit 143' TERM
trap 'fail="interrupted by SIGINT"; exit 130' INT
case $1 in
run)        shift; run "$@" ;;
serve-one)  serve_one ;;
status)     status ;;
down)       down ;;
esac
