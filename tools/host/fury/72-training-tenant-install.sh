#!/bin/bash
# Install act 2's training tenant as a host service: flywheel/training-tenant.container (MIG slice 0:2) with its
# loop script, and the fury-mode that starts it in tenants mode and stops it for every switch. What it is, what to
# show and what to compare: 72-training-tenant.md
#
#   ./72-training-tenant-install.sh install   check the unit against the tenant's rules, let quadlet check it, install
#                                             unit, script and fury-mode, create /data/flywheel/tenant-train.
#                                             Starts nothing: with MIG off the unit would skip its own start anyway
#                                             (ExecCondition=). It comes up with:  fury-mode tenants
#   ./72-training-tenant-install.sh status    unit state, the last three rounds, the slice's memory
#   ./72-training-tenant-install.sh remove    stop and remove unit and script. /data/flywheel/tenant-train stays
#
# The tenant is a showcase and stays outside the governed flywheel. install refuses a unit file that would let it
# in: a writable mount of anything under /data/flywheel but its own directory, a network, an environment file,
# another image than the runner's, or another slice.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
case ${1:-} in
install|status|remove) [[ $EUID -eq 0 ]] || exec sudo "$0" "$@" ;;
*) echo "usage: ${0##*/} install | status | remove" >&2; exit 1 ;;
esac
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1
qd=''
# The only EXIT trap. Its end is the flush: under sudo the terminal can go away before tee has written the last
# lines, a final refusal among them - wait for it on the way out.
trap '[[ -z $qd ]] || { rm -f "$qd/training-tenant.container"; rmdir "$qd"; }; exec >&- 2>&-; wait' EXIT

units=/etc/containers/systemd
lib=/usr/local/lib/flywheel
unit=$here/flywheel/training-tenant.container
runner_unit=$here/flywheel/flywheel-runner.container
script=$here/flywheel/training-tenant.sh
svc=training-tenant.service
slice=nvidia.com/gpu=0:2
data=/data/flywheel/tenant-train
weights=$data/cache/torch/hub/checkpoints
dataset=/data/flywheel/datasets/flywheel-ladder-160
die() { echo "${0##*/}: $*" >&2; exit 1; }
mig() { nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader; }
image() { sed -n 's/^Image=//p' "$1" | head -1; }

# The tenant's rules, measured on the file that is about to be installed rather than trusted to its comments.
check_rules() {
    local bad
    [[ $(image "$unit") == "$(image "$runner_unit")" ]] ||
        die "training-tenant.container and flywheel-runner.container name different images. The tenant trains in the signed
    runtime image the runner uses: copy the runner's Image= line into flywheel/training-tenant.container"
    [[ $(image "$unit") == *@sha256:* ]] || die "the unit's Image= is not pinned by digest: $(image "$unit")"
    [[ $(sed -n 's/^AddDevice=//p' "$unit") == "$slice" ]] ||
        die "the unit's AddDevice= is not $slice alone - that slice is the training tenant's (D164)"
    grep -qx 'Network=none' "$unit" ||
        die "the unit has no Network=none line: the tenant must not be able to reach Kafka, the object store or a registry"
    ! grep -q '^EnvironmentFile=' "$unit" ||
        die "the unit reads an EnvironmentFile=: the tenant gets no credentials - take the line out"
    # host path : container path : options. Writable is whatever does not say ro.
    bad=$(sed -n 's/^Volume=//p' "$unit" |
        awk -F: -v own="$data" '$1 ~ /^\/data\/flywheel(\/|$)/ && $1 != own && ("," $3 ",") !~ /,ro,/ {print "    " $1}')
    [[ -z $bad ]] || die "the unit mounts these writable, and only $data may be:
$bad
    add :ro to each in flywheel/training-tenant.container"
}

install_() {
    local f gen ctx w in_ root have=no
    for f in "$unit" "$runner_unit" "$script" "$here/fury-mode.sh"; do
        [[ -f $f ]] || die "missing ${f#"$here"/} next to this script"
    done
    [[ -x /usr/libexec/podman/quadlet ]] || die "no /usr/libexec/podman/quadlet - ./03-packages.sh first"
    bash -n "$script"                    || die "flywheel/training-tenant.sh does not parse - nothing installed"
    grep -q '^tenant=' "$here/fury-mode.sh" ||
        die "the fury-mode.sh next to this script does not know the tenant (no tenant= line): update the checkout.
    Without it a mode switch would find the tenant's lerobot-train and refuse"
    check_rules

    # What a round reads. Mounted read-only and NOT relabelled - the tenant changes nothing on the flywheel's side,
    # not even a label - so the labels have to be right already. The runner's own :z over /data/flywheel made them so.
    for in_ in "$dataset|meta/info.json" \
               "/data/flywheel/train/upstream-act-teacher/checkpoints/last/pretrained_model|model.safetensors" \
               "/data/flywheel/train/act-v2-ft160/checkpoints/last/pretrained_model|model.safetensors"; do
        root=${in_%%|*}
        [[ -f $root/${in_#*|} ]] || die "missing $root/${in_#*|} - ./53-stage-promotion.sh puts the dataset and both checkpoints there"
        selinuxenabled 2>/dev/null || continue
        for f in "$root" "$root/${in_#*|}"; do
            ctx=$(stat -c %C "$f")
            [[ $ctx == *:container_file_t:s0 ]] || die "$f is labelled $ctx, and the tenant's container cannot read that.
    The runner's start relabels all of /data/flywheel (in flywheel mode:  sudo systemctl restart flywheel-runner.service),
    or this input alone:  sudo chcon -R -t container_file_t -l s0 $root"
        done
    done
    podman image exists "$(image "$unit")" ||
        die "the runtime image is not in root's storage. Once, with the uplink:  sudo podman pull $(image "$unit")"

    # let quadlet check the container file, alone, before anything is installed
    qd=$(mktemp -d)
    cp "$unit" "$qd/"
    gen=$(QUADLET_UNIT_DIRS=$qd /usr/libexec/podman/quadlet -dryrun 2>&1) || die "quadlet rejected training-tenant.container:
$gen"
    # worth a look: the $$ in Exec= has to arrive here still doubled - systemd makes it the container's single $
    grep -E '^(ExecStart|ExecCondition)=' <<<"$gen" | cut -c1-240 | sed 's/^/quadlet: /' || true
    grep -Eq -- '--cgroups[= ]split' <<<"$gen" ||
        die "quadlet does not run the container inside the unit's cgroup here (no --cgroups=split in the generated
    command), so MemoryMax= and CPUQuota= would bind nothing and fury-mode could not tell the tenant's training from
    a governed run. Add  CgroupsMode=split  under [Container] in flywheel/training-tenant.container, then this again"

    # its own directory and caches; nothing else under /data/flywheel is the tenant's to write
    install -d -m 0755 -o root -g root "$data" "$data/cache/huggingface" "$weights"
    # The one download a fine-tune makes is the backbone's ImageNet weights, and the container has no network.
    # The runner fetched them into its own cache when it first trained here: a copy, the original stays.
    for w in /data/flywheel/cache/torch/hub/checkpoints/resnet*.pth; do
        [[ -f $w ]] || continue
        [[ -f $weights/${w##*/} ]] || { cp "$w" "$weights/"; echo "copied ${w##*/} from the runner's cache"; }
    done
    for w in "$weights"/resnet*.pth; do
        if [[ -f $w ]]; then have=yes; fi
    done

    install -d -m 0755 "$lib"
    install -m 0755 -o root -g root "$script" "$lib/training-tenant.sh"
    install -m 0644 -o root -g root "$unit" "$units/"
    install -m 0755 "$here/fury-mode.sh" /usr/local/sbin/fury-mode
    # by name: the runner's script lives in the same directory and carries the label its container gave it
    restorecon "$lib/training-tenant.sh" "$units/training-tenant.container" /usr/local/sbin/fury-mode
    systemctl daemon-reload
    systemctl list-unit-files 'training-tenant*' --no-pager || true

    echo
    # not `systemctl enable`: a quadlet unit is generated and enable refuses it; its [Install] section was applied above
    echo "installed; nothing was started. The tenant comes up with:  fury-mode tenants"
    echo "(through tools/hub/fury-switch.sh from a laptop, which takes the RHEM-managed policy out of service first)"
    if [[ $(systemctl is-active "$svc" 2>/dev/null || true) == active ]]; then
        echo "it is running the previous install. Between rounds, or now (the round in progress is dropped):  sudo systemctl restart $svc"
    elif [[ $(mig) == Enabled ]]; then
        echo "the machine is in tenants mode already:  sudo systemctl start $svc"
    fi
    echo "watch:  sudo journalctl -fu training-tenant     rounds:  $0 status"
    if [[ $have == no ]]; then
        echo
        echo "WARNING: no backbone weights (resnet*.pth) in the runner's cache or in $weights."
        echo "  The container has no network, so the first round would fail on that download. Once, with the uplink:"
        echo "  sudo podman run --rm --pull=never -v $data/cache/torch:/t:z -e TORCH_HOME=/t --entrypoint python3 $(image "$unit") -c \"import torchvision; torchvision.models.resnet18(weights='IMAGENET1K_V1')\""
    fi
}

status() {
    local tbl row pid rest
    printf '%-28s %s\n' "$svc" "$(systemctl is-active "$svc" 2>/dev/null || true)"
    systemctl show -p ActiveEnterTimestamp -p NRestarts -p MemoryCurrent -p MemoryPeak "$svc" 2>/dev/null | sed 's/^/  /' || true
    echo "## the last rounds ($data/rounds.jsonl)"
    if [[ -s $data/rounds.jsonl ]]; then tail -n 3 "$data/rounds.jsonl"; else echo "  none yet"; fi
    echo "## slice 0:2 on the gpu"
    if [[ $(mig) == Enabled ]]; then
        # the MIG table of plain nvidia-smi; the tenant's slice is MIG device 2 of GPU 0 (fourth column)
        tbl=$(nvidia-smi | awk '/MIG devices:/ {p = 1} /Processes:/ {p = 0} p' || true)
        row=$(awk '/^\|[[:space:]]+0[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+2[[:space:]]+\|/' <<<"$tbl" || true)
        if [[ -n $row ]]; then echo "$row"; else echo "$tbl"; fi
        # and what the tenant's own processes hold: compute apps whose cgroup is this unit's
        while IFS=, read -r pid rest; do
            pid=${pid//[!0-9]/}
            if [[ -n $pid ]] && grep -qsF "/$svc/" "/proc/$pid/cgroup"; then echo "  tenant pid $pid:$rest"; fi
        done < <(nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null || true)
    else
        echo "  MIG is off (flywheel mode): there is no slice, and the unit skips every start"
    fi
    echo "## on disk"
    if [[ -d $data ]]; then du -sh "$data" | sed 's/^/  /'; else echo "  no $data - not installed"; fi
}

remove() {
    systemctl stop "$svc" 2>/dev/null || true
    podman rm -f -t 10 training-tenant >/dev/null 2>&1 || true
    rm -f "$units/training-tenant.container" "$lib/training-tenant.sh"
    systemctl daemon-reload
    systemctl reset-failed "$svc" 2>/dev/null || true
    echo "removed the unit and its script. fury-mode stays: it passes over a tenant whose unit is not installed."
    if [[ -d $data ]]; then
        echo "left in place: $data ($(du -sh "$data" | cut -f1)) - the rounds, the ledger and the tenant's caches."
        echo "Nothing else reads it; it is yours to delete."
    fi
}

date -u
case $1 in
install) install_ ;;
status)  status ;;
remove)  remove ;;
esac
