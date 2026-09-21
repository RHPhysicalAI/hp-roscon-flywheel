#!/bin/bash
# Install the host side of robot zero (D164): flywheel/robot-zero-sim.container (the physics-only world that reports
# to the fleet's renderer as r00), flywheel/robot-zero-frames.container (the renderer's pictures of r00 on the
# policy's image topics, with its code from src/robot-zero), flywheel/robot-zero-episodes.container (the episode
# loop, recording nothing), flywheel/robot-zero-emitter.container (each episode's summary to the show curator, which
# judges it for the flywheel page and keeps nothing, D166), and the fury-mode that starts them in tenants mode and
# stops them for every switch. None of them uses the GPU. Robot zero's GPU tenant is the RHEM-managed policy; placing it on slice 0:1 and
# starting it is the laptop's half: tools/hub/robot-zero.sh up. What it is and how to judge it: 74-robot-zero.md
#
# ONE-TIME BRING-UP (nothing here is a demo-time step):
#   ./74-robot-zero-install.sh install   check the four units against robot zero's rules, let quadlet check them,
#                                        install units, bridge code and fury-mode. In tenants mode: (re)start them and
#                                        wait until the renderer shows r00 live and the frames flow. In flywheel mode
#                                        nothing starts: they come up with  fury-mode tenants
#   ./74-robot-zero-install.sh status    units, the policy's unit and the device it names, r00 on the wall, the last
#                                        lines of the frames, the episode loop and the reporter, slice 0:1
#   ./74-robot-zero-install.sh remove    stop and remove the units and the bridge code. Refused while the policy runs
#   ./74-robot-zero-install.sh check     the rules below alone, on the unit files next to this script. No root, no
#                                        host needed, changes nothing (tests/robot_zero runs it)
#   ./74-robot-zero-install.sh check-generated FILE
#                                        the emitter's one-address rule on a saved  quadlet -dryrun  output: what
#                                        install checks after quadlet has parsed the files. No root, changes nothing
# AT DEMO TIME: nothing here. fury-mode tenants starts robot zero's host side, a boot in tenants mode brings it back,
# fury-mode zero (from a laptop: tools/hub/robot-zero.sh reset) restarts it alone.
#
# Robot zero sees the rendering tenant's pixels, not Gazebo's, so nothing it does may reach the flywheel's data.
# install refuses unit files that would let it: any mount at all beyond the bridge's own code, an environment file,
# the curator's, the object store's or Kafka's name or the hub's address on any line, recording left on (or turned
# on again by a later line), a GPU device, another image than the two signed ones by digest, a missing line of the
# hardening (read-only root, no capabilities, no new privileges), or a key that would undo any of it
# (PodmanArgs=, Mount=, Secret=, ...). It also refuses while the flywheel's recorder has no MIG guard of its own.
# One exception, as narrow as it can be made: robot-zero-emitter.container carries exactly one line with an address
# of the hub - the SHOW curator's (gitops/flywheel/curator-show.yaml: the real gates, nothing behind them). The
# flywheel's curator there, a second address of any kind, or that line in any other unit is refused like before.
# An address can also be reached without naming one: the emitter's HTTP client, and podman itself, honour proxy
# variables. So no unit may mention a proxy, the emitter switches them off (no_proxy=*), no line may be continued
# onto the next or quote an Environment= value (the rules read lines, systemd joins and unquotes them), and install
# looks once more at what quadlet made of the emitter's file: one CURATOR_URL, no other address, no proxy.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
case ${1:-} in
install|status|remove) [[ $EUID -eq 0 ]] || exec sudo "$0" "$@" ;;
check) ;;
check-generated) [[ -r ${2:-} ]] || { echo "usage: ${0##*/} check-generated <file with the output of quadlet -dryrun>" >&2; exit 1; } ;;
*) echo "usage: ${0##*/} install | status | remove | check | check-generated FILE" >&2; exit 1 ;;
esac
here=$(dirname "$(readlink -f "$0")")
if [[ $1 != check && $1 != check-generated ]]; then
    mkdir -p "$here/log"
    exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1
fi
qd=''
# The only EXIT trap. Its end is the flush: under sudo the terminal can go away before tee has written the last
# lines, a final refusal among them - wait for it on the way out.
trap '[[ -z $qd ]] || { rm -f "$qd"/robot-zero-sim.container "$qd"/robot-zero-frames.container "$qd"/robot-zero-episodes.container "$qd"/robot-zero-emitter.container "$qd"/act-coordinator.container; rmdir "$qd"; }; exec >&- 2>&-; wait' EXIT

units=/etc/containers/systemd
lib=/usr/local/lib/flywheel/robot-zero
names=(robot-zero-sim robot-zero-frames robot-zero-episodes robot-zero-emitter)
sim=$here/flywheel/robot-zero-sim.container
frames=$here/flywheel/robot-zero-frames.container
episodes=$here/flywheel/robot-zero-episodes.container
emitter=$here/flywheel/robot-zero-emitter.container
# The one address of the hub a robot-zero unit may carry, and only the emitter's: the show curator's NodePort
# (gitops/flywheel/curator-show.yaml). The flywheel's own curator is on 30802 and stays out of every unit.
show_line='Environment=CURATOR_URL=http://10.20.0.10:30812/episode'
# what runs in the emitter's container: the signed image's own emitter under its ROS environment, nothing else
emitter_exec="Exec=-c 'source /opt/ros/\$\${ROS_DISTRO}/setup.bash && source /ws_pai/install/setup.bash && exec python3 -u /ws_pai/episode_emitter.py'"
coordinator=$here/flywheel/act-coordinator.container
code=(frame_bridge.py bridge_core.py)
robot=r00
url=http://10.20.0.1:9702
wait_s=${WAIT_S:-180}
die() { echo "${0##*/}: $*" >&2; exit 1; }
mig() { nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader; }
image() { sed -n 's/^Image=//p' "$1" | head -1; }
# Every value a unit's Environment= lines give a variable, one a line. Compared as strings, never as patterns:
# what is looked for can come from a unit file. systemd lets a later line override an earlier one, so a rule holds
# only when the value it wants is the ONLY one the unit assigns (is). Words are split at blanks, which is only what
# systemd does as long as nothing is quoted or continued: check_rules refuses both before it asks anything here.
assigned() {  # unit file, variable
    local line w words
    while IFS= read -r line; do
        read -ra words <<<"${line#Environment=}"
        for w in "${words[@]}"; do
            if [[ $w == "$2="* ]]; then echo "${w#*=}"; fi
        done
    done < <(grep '^Environment=' "$1" || true)
}
is() { [[ $(assigned "$1" "$2") == "$3" ]]; }                          # unit file, variable, the one value it must have
policy_state() { systemctl list-units --all --no-legend --plain 'act-inference-*-act-inference.service' | awk '{print $3 "/" $4}'; }
policy_device() { sed -n 's|^AddDevice=||p' "$units"/*/act-inference-*-act-inference.container 2>/dev/null | head -1; }
# r00 as the renderer sees it: "live 0.02 30.0" (state, seconds since its last state, states a second), or nothing
wall() {
    curl -s -f -m 4 $url/status 2>/dev/null | python3 -c '
import json, sys
try:
    robots = json.load(sys.stdin).get("robots", [])
except ValueError:
    sys.exit(0)
for r in robots:
    if r.get("id") == sys.argv[1]:
        print(r.get("state"), r.get("age_s"), r.get("datagrams_per_s"))
' "$robot" || true
}

# Robot zero's rules, measured on the files that are about to be installed rather than trusted to their comments.
check_rules() {
    local f n bad line part
    for f in "$sim" "$frames" "$episodes" "$emitter"; do
        n=${f##*/}
        [[ $(image "$f") =~ ^quay\.io/jary/soarm-flywheel@sha256:[0-9a-f]{64}$ ]] ||
            die "$n: Image= is not a sha256 digest of the signed repository quay.io/jary/soarm-flywheel: $(image "$f")"
        [[ $(grep -c '^Image=' "$f") -eq 1 ]] || die "$n has more than one Image= line: the last one wins, and only the first was checked - leave one"
        grep -qx 'Pull=never' "$f"   || die "$n has no Pull=never line: a unit start must never wait for, or depend on, a registry"
        grep -qx 'Network=host' "$f" || die "$n has no Network=host line: the policy reaches this world's router on the host's address"
        # the hardening the units claim: each line exactly, and nothing that takes it back
        for line in ReadOnly=true DropCapability=all NoNewPrivileges=true; do
            grep -qx "$line" "$f" || die "$n has no  $line  line. Robot zero's containers run with a read-only root, no
    capabilities and no new privileges; if one of them does not start that way, that is a finding to bring back
    (74-robot-zero.md, 'When it fails'), not a line to take out - put it back"
            [[ $(grep -c "^${line%%=*}=" "$f") -eq 1 ]] || die "$n sets ${line%%=*}= more than once: the last one wins - leave the one  $line"
        done
        # The rules below read lines. systemd joins a line that ends in a backslash with the next one - and a "#"
        # line after it is then no comment - and it unquotes Environment= values, where one pair of quotes can hold
        # several words. Neither is used in these files, so neither is interpreted here: both are refused.
        bad=$(grep -n '\\[[:space:]]*$' "$f" || true)
        [[ -z $bad ]] || die "$n continues a line onto the next (a backslash at the end): robot zero's rules are read a line at a time,
    and what systemd joins they would not see - write it on one line:
$bad"
        bad=$(grep -En "^Environment=.*[\"']" "$f" || true)
        [[ -z $bad ]] || die "$n quotes an Environment= value: robot zero's units assign plain words, one variable a word, so that the
    rules read what systemd reads - take the quotes out:
$bad"
        # A proxy variable reaches another address without naming one: the emitter's urllib honours http_proxy and
        # its kin, and podman hands its own proxy environment on to a container. The emitter's unit switches them
        # off with exactly two lines, checked below; no other line of any unit may mention one.
        bad=$(grep -in 'proxy' "$f" | grep -Ev '^[0-9]+:[[:space:]]*#' || true)
        [[ $f != "$emitter" ]] || bad=$(grep -vxE '[0-9]+:Environment=(no_proxy|NO_PROXY)=\*' <<<"$bad" || true)
        [[ -z $bad ]] || die "$n mentions a proxy: through one, robot zero's episodes would go to an address no rule here ever sees - take out:
$bad"
        bad=$(grep -En '^(Mount|PodmanArgs|GlobalArgs|Secret|Rootfs|Tmpfs|AddCapability|SecurityLabelDisable|SecurityLabelType|AddDevice|EnvironmentFile|Unmask|UserNS)=' "$f" || true)
        [[ -z $bad ]] || die "$n has a key robot zero's units do not use - a mount, a device, credentials or raw podman
    arguments can each undo what the other rules hold (slice 0:1 is the policy's alone, and RHEM's to hand out;
    robot zero gets no credentials and writes nowhere) - take out:
$bad"
        # Any line that is not a comment, not only Environment=: Exec= and Entrypoint= can carry an address too.
        # The emitter's unit is looked at harder (every URL and every IPv4 address) and is let off one line, whole
        # and compared as a string.
        if [[ $f == "$emitter" ]]; then
            bad=$(grep -E '(CURATOR|KAFKA|MINIO|S3_|AWS_|ARTIFACT_BUCKET|BOOTSTRAP|://|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)' "$f" |
                  grep -Ev '^[[:space:]]*#' | grep -vxF "$show_line" || true)
            [[ -z $bad ]] || die "$n may name one address and one only - the show curator's, which judges robot zero's episodes and keeps
    nothing:  $show_line
    The flywheel's own curator (port 30802), its pipeline, or any second address must not be reachable from here - take out:
$bad"
        else
            bad=$(grep -En '(CURATOR|KAFKA|MINIO|S3_|AWS_|ARTIFACT_BUCKET|BOOTSTRAP|10\.20\.0\.10([^0-9]|$))' "$f" | grep -Ev '^[0-9]+:[[:space:]]*#' || true)
            [[ -z $bad ]] || die "$n names the flywheel's pipeline or the hub's address, and robot zero must not be able to feed it (the show
    curator's address belongs in robot-zero-emitter.container and nowhere else) - take out:
$bad"
        fi
        bad=$(sed -n 's/^Volume=//p' "$f" | grep -vxF "$lib:/opt/robot-zero:ro,z" || true)
        [[ -z $bad ]] || die "$n mounts something, and robot zero writes nowhere and reads only its own code - take out:
    Volume=$bad"
    done
    for f in "$sim" "$episodes"; do
        ! grep -Eq '^(Exec|Entrypoint)=' "$f" ||
            die "${f##*/} overrides the image's entrypoint (Exec= or Entrypoint=): what runs in it is the signed image's own launcher, whose behaviour the other rules rely on - take the line out"
    done
    [[ $(sed -n 's/^Volume=//p' "$frames") == "$lib:/opt/robot-zero:ro,z" ]] ||
        die "robot-zero-frames.container must mount its code, read-only, and nothing else:  Volume=$lib:/opt/robot-zero:ro,z"
    [[ $(image "$sim") == "$(image "$frames")" ]] ||
        die "robot-zero-sim.container and robot-zero-frames.container name different images: both run the fleet worlds' sim image (gitops/fleet-worlds/world.yaml) - copy the Image= line from one to the other"
    # The episode reporter: the signed sim image's own emitter and nothing else, one address, nothing mounted, and
    # a fallback directory that ends with the container.
    [[ $(image "$sim") == "$(image "$emitter")" ]] ||
        die "robot-zero-emitter.container does not name the image of robot-zero-sim.container: the emitter it runs is that signed image's own - copy the Image= line"
    is "$emitter" CURATOR_URL "${show_line#Environment=CURATOR_URL=}" ||
        die "robot-zero-emitter.container must carry this line, once, and no other CURATOR_URL:  $show_line
    Without it the emitter only writes to its tmpfs; with any other value robot zero's episodes go where they must not"
    { is "$emitter" no_proxy '*' && is "$emitter" NO_PROXY '*'; } ||
        die "robot-zero-emitter.container must carry these two lines, once each:  Environment=no_proxy=*  and  Environment=NO_PROXY=*
    They switch every proxy off for the emitter's HTTP client, whatever podman inherits from the host and hands on"
    [[ -z $(sed -n 's/^Volume=//p' "$emitter") ]] ||
        die "robot-zero-emitter.container mounts something: the emitter's code is in the image and it writes nowhere but its tmpfs - take the Volume= line out"
    n=$(assigned "$emitter" RAW_DIR)
    [[ $n =~ ^/tmp/[A-Za-z0-9_-]+$ ]] ||
        die "robot-zero-emitter.container must set RAW_DIR once, to a directory directly under /tmp (the container's tmpfs), got '${n//$'\n'/ and }':
    that is where a summary goes when the hub does not answer, and it must end with the container"
    if [[ $(grep -c '^Entrypoint=' "$emitter") -ne 1 || $(grep -c '^Exec=' "$emitter") -ne 1 ]] ||
       ! grep -qxF 'Entrypoint=/bin/bash' "$emitter" || ! grep -qxF "$emitter_exec" "$emitter"; then
        die "robot-zero-emitter.container must run the image's own emitter and nothing else - exactly these two lines, once each:
    Entrypoint=/bin/bash
    $emitter_exec"
    fi
    [[ -f $coordinator && $(image "$episodes") == "$(image "$coordinator")" ]] ||
        die "robot-zero-episodes.container does not name the image of flywheel/act-coordinator.container: the episode loop is the flywheel's own coordinator - copy that Image= line"
    is "$sim" SIM_CAMERAS off ||
        die "robot-zero-sim.container must set SIM_CAMERAS=off, once: with cameras the image also starts the episode emitter, which is what reports episodes to the curator"
    is "$sim" ROBOT_ID "$robot"    || die "robot-zero-sim.container must set ROBOT_ID=$robot, once: that id is robot zero's on the wall (FLEET-RENDER-CONTRACT.md)"
    is "$frames" ROBOT_ID "$robot" || die "robot-zero-frames.container must set ROBOT_ID=$robot, once: it would fetch another robot's pictures"
    is "$episodes" RECORD false ||
        die "robot-zero-episodes.container must set RECORD=false, once and nothing else: recording is on by default, a later line overrides an earlier one, and a bag of rendered pixels is what must never exist"
    is "$episodes" ROLE coordinator || die "robot-zero-episodes.container must set ROLE=coordinator, once: any other role starts a second policy on the graph"
    [[ -z $(assigned "$episodes" EVAL_MODE) ]] || die "robot-zero-episodes.container sets EVAL_MODE: an eval writes result files - take it out"
    for part in BAG_DIR EVAL_RESULTS_DIR CURATOR_URL; do
        [[ -z $(assigned "$episodes" $part)$(assigned "$sim" $part) ]] || die "a robot-zero unit sets $part: robot zero has nowhere to write and nobody to report to - take it out"
    done
    # the value comes from a unit file: it is checked for what it is, and compared as a string
    n=$(assigned "$sim" GZ_PARTITION)
    [[ $n =~ ^[A-Za-z0-9_-]+$ ]] ||
        die "robot-zero-sim.container must set GZ_PARTITION once, to a plain name (letters, digits, - and _), got '${n//$'\n'/ and }'"
    is "$episodes" GZ_PARTITION "$n" ||
        die "robot-zero-sim.container and robot-zero-episodes.container must carry the same Environment=GZ_PARTITION=$n line, once each: the cube reset talks to that Gazebo"
    is "$emitter" GZ_PARTITION "$n" ||
        die "robot-zero-sim.container and robot-zero-emitter.container must carry the same Environment=GZ_PARTITION=$n line, once each: the cube count is read from that Gazebo"
    # The flywheel's own recorder: since robot zero there is a router, a policy and camera topics on this host under
    # MIG, and a recorder started by mistake would record them into the flywheel's bags. It must refuse that itself.
    mig_guard "$coordinator" || die "flywheel/act-coordinator.container has no MIG start condition (ExecCondition= ... mig.mode.current ... = Disabled): update the checkout.
    Without it a stray  systemctl start act-coordinator  in tenants mode records robot zero's ray-traced pixels into /data/flywheel/bags"
    [[ ! -f $here/flywheel/so-arm-sim.container ]] || mig_guard "$here/flywheel/so-arm-sim.container" ||
        die "flywheel/so-arm-sim.container has no MIG start condition (ExecCondition= ... mig.mode.current ... = Disabled): update the checkout"
}
# The emitter's one-address rule once more, on what quadlet generated from the file: after systemd's own joining,
# unquoting, ordering and de-duplication there is nothing left for a line-reading rule to have missed. Every word
# of the podman command that carries a URL, an IPv4 address or a proxy must be one of three, and podman's own
# environment (what --http-proxy would hand on) must be quadlet's single line.
check_generated() {  # the output of quadlet -dryrun
    python3 -c '
import re, shlex, sys
show, text = sys.argv[1], sys.stdin.read()
found = re.search(r"^---robot-zero-emitter\.service---\n(.*?)(?=^---[^\n]*---$|\Z)", text, re.M | re.S)
if not found:
    sys.exit("quadlet generated no robot-zero-emitter.service")
lines = found.group(1).splitlines()
starts = [l for l in lines if l.startswith("ExecStart=")]
if len(starts) != 1:
    sys.exit("the generated robot-zero-emitter.service has %d ExecStart= lines, not one" % len(starts))
words = shlex.split(starts[0][len("ExecStart="):])
allowed = {"CURATOR_URL=" + show, "no_proxy=*", "NO_PROXY=*"}
bad = [w for w in words if re.search(r"://|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+|proxy|CURATOR", w, re.I) and w not in allowed]
bad += [w for w in words if w.startswith(("--env-file", "--env-host", "--env-merge"))]
bad += ["missing: --env " + w for w in sorted(allowed) if words.count(w) != 1]
section = ""
for l in lines:   # [X-Container] repeats the file; podman runs with the environment of [Service]
    section = l if l.startswith("[") else section
    if section == "[Service]" and l.startswith("Environment=") and l != "Environment=PODMAN_SYSTEMD_UNIT=%n":
        bad.append("[Service] " + l)
if bad:
    sys.exit("\n".join(bad))
' "${show_line#Environment=CURATOR_URL=}" <<<"$1" || die "what quadlet generated from robot-zero-emitter.container does not keep the one-address rule: the container must get
    exactly  CURATOR_URL=${show_line#Environment=CURATOR_URL=}  no_proxy=*  NO_PROXY=*  and no other address, URL or proxy, and podman
    itself no environment of ours. Nothing installed. Look at the lines above, and at:  QUADLET_UNIT_DIRS=<dir with the file> /usr/libexec/podman/quadlet -dryrun"
}
# True when a unit file only starts with MIG off: an ExecCondition= that compares mig.mode.current with Disabled.
mig_guard() { grep -Eq '^ExecCondition=.*mig\.mode\.current[^|]*= Disabled \]' "$1"; }

install_() {
    local f src gen waited=0 seen='' frames_ok=''
    src=${SRC:-$(getent passwd "${SUDO_USER:?run this through sudo, or set SRC}" | cut -d: -f6)/hp-roscon-flywheel}
    for f in "$sim" "$frames" "$episodes" "$emitter" "$here/fury-mode.sh"; do
        [[ -f $f ]] || die "missing ${f#"$here"/} next to this script"
    done
    for f in "${code[@]}"; do
        [[ -f $src/src/robot-zero/$f ]] || die "no $src/src/robot-zero/$f - copy src/robot-zero/ into that checkout, or SRC=<checkout> $0 install"
        python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$src/src/robot-zero/$f" ||
            die "$src/src/robot-zero/$f does not parse - nothing installed"
    done
    [[ -x /usr/libexec/podman/quadlet ]] || die "no /usr/libexec/podman/quadlet - ./03-packages.sh first"
    bash -n "$here/fury-mode.sh"         || die "the fury-mode.sh next to this script does not parse - nothing installed"
    grep -q '^zero=.*robot-zero-emitter\.service' "$here/fury-mode.sh" ||
        die "the fury-mode.sh next to this script does not know all four robot-zero units (no zero= line with robot-zero-emitter.service): update the checkout.
    Without it a mode switch would leave a robot-zero unit running, and nothing would start it in tenants mode"
    check_rules
    # so-arm-sim's MIG check is a start condition now, which no longer holds back a unit that Requires= it: the
    # pre-enrolment, hand-installed policy unit (whole gpu) must be gone, as it is on an enrolled host
    [[ ! -e $units/act-inference.container ]] ||
        die "$units/act-inference.container is still installed - the hand-installed, pre-enrolment policy on the whole gpu.
    On an enrolled host the policy is RHEM's. Remove it:  sudo rm $units/act-inference.container && sudo systemctl daemon-reload"
    for f in "$sim" "$episodes"; do
        podman image exists "$(image "$f")" ||
            die "$(image "$f") is not in root's storage. Once, with the uplink (the host's policy verifies its signature):
    sudo podman pull $(image "$f")"
    done

    # let quadlet check the container files this installs, and nothing else, before anything is installed
    qd=$(mktemp -d)
    cp "$sim" "$frames" "$episodes" "$emitter" "$coordinator" "$qd/"
    gen=$(QUADLET_UNIT_DIRS=$qd /usr/libexec/podman/quadlet -dryrun 2>&1) || die "quadlet rejected a robot-zero unit, or act-coordinator.container:
$gen"
    grep -E '^(ExecStart|ExecCondition|ExecStartPost)=' <<<"$gen" | cut -c1-240 | sed 's/^/quadlet: /' || true
    check_generated "$gen"
    [[ $(grep -Ec -- '^ExecStart=.*--cgroups[= ]split' <<<"$gen") -eq 5 ]] ||
        die "quadlet does not run every container inside its unit's cgroup here (no --cgroups=split in a generated
    command), so MemoryMax= and CPUQuota= would bind nothing. Add  CgroupsMode=split  under [Container] in the four
    flywheel/robot-zero-*.container files, then this again"

    install -d -m 0755 -o root -g root "$lib"
    for f in "${code[@]}"; do install -m 0644 -o root -g root "$src/src/robot-zero/$f" "$lib/$f"; done
    install -m 0644 -o root -g root "$sim" "$frames" "$episodes" "$emitter" "$units/"
    # The recorder's guard goes in with robot zero, not at some later run of 14-flywheel-services.sh: from the moment
    # robot zero's router is up, an unguarded recorder on this host is one stray start away from the flywheel's bags.
    # Only where that unit is installed already, and only that file; a running recorder keeps running (MIG is off then).
    if [[ -f $units/act-coordinator.container ]]; then
        install -m 0644 -o root -g root "$coordinator" "$units/"
        restorecon "$units/act-coordinator.container"
        echo "act-coordinator.container: reinstalled with its MIG start condition"
    fi
    install -m 0755 "$here/fury-mode.sh" /usr/local/sbin/fury-mode
    # not $lib: the frames container mounts it with :z and relabels it at every start
    for f in "${names[@]}"; do restorecon "$units/$f.container"; done
    restorecon /usr/local/sbin/fury-mode
    systemctl daemon-reload
    systemctl list-unit-files 'robot-zero*' --no-pager || true
    echo

    if [[ $(mig) != Enabled ]]; then
        # not `systemctl enable`: a quadlet unit is generated and enable refuses it; its [Install] section was applied above
        echo "installed. MIG is off (flywheel mode), so nothing was started: the units skip their own start in this mode."
        echo "Robot zero's host side comes up with  fury-mode tenants  (from a laptop: tools/hub/fury-switch.sh tenants,"
        echo "which then also places the policy on slice 0:1 and starts it)."
        return 0
    fi
    [[ $(curl -s -o /dev/null -m 4 -w '%{http_code}' $url/healthz || true) == 200 ]] ||
        echo "WARNING: the fleet renderer does not answer on $url - r00 cannot show up until it does:  ./73-fleet-renderer-install.sh status"
    for f in "${names[@]}"; do systemctl reset-failed "$f.service" 2>/dev/null || true; done
    # restart, not start: a second install is how a changed unit or changed bridge code gets in. The world's unit
    # takes the other three along and restarts the policy itself, if that is running.
    systemctl restart "${names[0]}.service" || die "${names[0]}.service did not start:
$(journalctl -u "${names[0]}" -n 15 --no-pager 2>/dev/null | cut -c1-220)"
    for f in "${names[@]}"; do systemctl start --no-block "$f.service"; done
    echo "started. The sim needs about a minute before its joints report; waiting up to $wait_s s for $robot on the wall and the frames:"
    while :; do
        seen=$(wall)
        if journalctl -u "${names[1]}" --since "-${wait_s} s" --no-pager -o cat 2>/dev/null | grep -q 'wrist: pictures are arriving'; then frames_ok=yes; fi
        [[ $seen == live* && -n $frames_ok ]] && break
        for f in "${names[@]}"; do
            [[ $(systemctl is-failed "$f.service" 2>/dev/null || true) != failed ]] || die "$f.service failed. Its end:
$(journalctl -u "$f" -n 15 --no-pager 2>/dev/null | cut -c1-220)
    Fix, then:  $0 install     (the failure table of 74-robot-zero.md names the usual causes)"
        done
        ((waited < wait_s)) || die "after $wait_s s: $robot on the wall is '${seen:-not there}', the frames ${frames_ok:-have not arrived}.
    The world:   sudo journalctl -u ${names[0]} -n 40      (a slow start: WAIT_S=400 $0 install)
    The frames:  sudo journalctl -u ${names[1]} -n 20
    The failure table of 74-robot-zero.md names the usual causes"
        sleep 10; waited=$((waited + 10))
        echo "  ${waited} s: wall '${seen:-not there}', frames ${frames_ok:-not yet} - $(journalctl -u "${names[0]}" -n 1 --no-pager -o cat 2>/dev/null | cut -c1-140)"
    done
    echo "$robot is live on the wall and its pictures are on the policy's topics after about $waited s"
    echo
    status
    echo
    case $(policy_state) in
        active/*) echo "the policy is running: it was restarted with its world and takes the next goal from the episode loop." ;;
        *) echo "next, from a laptop logged in to the hub - RHEM places the policy on slice 0:1 and starts it:"
           echo "  FURY_SSH=<user>@<this host> tools/hub/robot-zero.sh up" ;;
    esac
}

status() {
    local f tbl row pid rest seen dev
    for f in "${names[@]}"; do
        printf '%-30s %s\n' "$f.service" "$(systemctl is-active "$f.service" 2>/dev/null || true)"
        systemctl show -p ActiveEnterTimestamp -p NRestarts -p MemoryCurrent "$f.service" 2>/dev/null | sed 's/^/  /' || true
    done
    dev=$(policy_device)
    printf '%-30s %s\n' "policy (rhem-managed)" "$(policy_state)"
    printf '%-30s %s\n' "the policy's unit names" "${dev:-no policy quadlet on this host}"
    echo "## $robot as the renderer sees it ($url/status)"
    seen=$(wall)
    if [[ -n $seen ]]; then
        read -r f tbl row <<<"$seen"
        echo "  $f, last state $tbl s ago, $row states a second   (live, near 30: the world reports; stale: it has stopped)"
    else
        echo "  not on the wall: the world is not sending, or the renderer does not answer"
    fi
    echo "## the frames (last lines of ${names[1]})"
    journalctl -u "${names[1]}" -n 3 --no-pager -o cat 2>/dev/null | cut -c1-200 | sed 's/^/  /' || true
    echo "## the episode loop (last lines of ${names[2]})"
    journalctl -u "${names[2]}" -n 4 --no-pager -o cat 2>/dev/null | cut -c1-200 | sed 's/^/  /' || true
    echo "## the episode reporter, to the show curator only (last lines of ${names[3]})"
    journalctl -u "${names[3]}" -n 3 --no-pager -o cat 2>/dev/null | cut -c1-200 | sed 's/^/  /' || true
    echo "## slice 0:1 on the gpu"
    if [[ $(mig) == Enabled ]]; then
        # the MIG table of plain nvidia-smi; robot zero's slice is MIG device 1 of GPU 0 (fourth column)
        tbl=$(nvidia-smi | awk '/MIG devices:/ {p = 1} /Processes:/ {p = 0} p' || true)
        row=$(awk '/^\|[[:space:]]+0[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+1[[:space:]]+\|/' <<<"$tbl" || true)
        if [[ -n $row ]]; then echo "$row"; else echo "$tbl"; fi
        # the policy shows up as a compute process only after its first goal: the model is loaded lazily
        while IFS=, read -r pid rest; do
            pid=${pid//[!0-9]/}
            if [[ -n $pid ]] && grep -Eqs '/act-inference-[^/]*-act-inference\.service/' "/proc/$pid/cgroup"; then echo "  policy pid $pid:$rest"; fi
        done < <(nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null || true)
    else
        echo "  MIG is off (flywheel mode): there is no slice, and the units skip every start"
    fi
}

remove() {
    local f
    case $(policy_state) in
        active/*|activating/*) die "the RHEM-managed policy is running, and without robot zero's world it turns unhealthy and is restarted every few
    minutes. First, from the laptop:  FURY_SSH=<user>@<this host> tools/hub/robot-zero.sh down   - then this again" ;;
    esac
    systemctl stop "${names[0]}.service" "${names[1]}.service" "${names[2]}.service" "${names[3]}.service" 2>/dev/null || true
    for f in "${names[@]}"; do
        podman rm -f -t 10 "$f" >/dev/null 2>&1 || true
        rm -f "$units/$f.container"
    done
    for f in "${code[@]}"; do rm -f "$lib/$f"; done
    rmdir "$lib" 2>/dev/null || true
    systemctl daemon-reload
    for f in "${names[@]}"; do systemctl reset-failed "$f.service" 2>/dev/null || true; done
    echo "removed the four units and the bridge code. fury-mode stays: it passes over a robot zero that is not installed."
    echo "left in place, yours to delete: the sim image (sudo podman rmi $(image "$sim"))."
    echo "r00 greys out on the wall after 5 s and leaves it after a minute. The device's gpu_device label, if still set,"
    echo "is the hub's:  tools/hub/robot-zero.sh down  removes it."
}

[[ $1 != check ]] || { check_rules; echo "the four robot-zero units keep robot zero's rules"; exit 0; }
[[ $1 != check-generated ]] || { check_generated "$(cat "$2")"; echo "the generated robot-zero-emitter.service keeps the one-address rule"; exit 0; }
date -u
case $1 in
install) install_ ;;
status)  status ;;
remove)  remove ;;
esac
