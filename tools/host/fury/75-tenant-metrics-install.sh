#!/bin/bash
# The tenants' own numbers for the hub's dashboard: flywheel/tenant-metrics.container, a small exporter (code from
# src/tenant-metrics) that reads the training tenant's round log and ledger READ-ONLY and asks the rendering tenant
# for /status, and serves both as Prometheus text on 10.20.0.1:9401 - the VM network's host address only, with
# 9401/tcp opened for the guests (the hub). The GPU's numbers stay with 70-dcgm.sh; this adds what each tenant is
# doing with its slice. What every choice rests on, each hop up to the panel, what to do when one fails:
# 75-tenant-metrics.md
#
# ONE-TIME BRING-UP (nothing here is a demo-time step):
#   ./75-tenant-metrics-install.sh install   check the unit against the rules below, let quadlet check it, install
#                                            unit and code, start it, and only once it answers open 9401/tcp in the
#                                            libvirt-to-host policy; then show what it serves. In either GPU mode
#   ./75-tenant-metrics-install.sh status    unit, listener, firewall, what it serves, and the slice table below
#   ./75-tenant-metrics-install.sh remove    stop and remove unit and code, close the port
#   ./75-tenant-metrics-install.sh check     the rules alone, on the unit file next to this script. No root, no host
#                                            needed, changes nothing (tests/tenant_metrics runs it)
# BEFORE A DEMO, no root:
#   ./75-tenant-metrics-install.sh slices    GPU instance id -> MIG device -> tenant, as this host has them now, beside
#                                            the names the dashboard gives those ids. The dashboard's map is written by
#                                            hand; after any change of the MIG layout the ids SHIFT, and it would put a
#                                            wrong tenant's name on a slice without a word. Ends with 1 when they differ
# AT DEMO TIME: nothing here. The unit holds no GPU, so fury-mode does not own it: it runs in both modes and through
# every switch, and comes back at boot. A tenant that is not running reads tenant_training_up 0 / tenant_renderer_up 0.
#
# The exporter sits beside the tenants, not among them, and gets as little as it can do its work with. install
# refuses a unit file that gives it more: a GPU device, any mount beyond its own code and the training tenant's
# output (both read-only), an environment file or a secret, another listen address than 10.20.0.1:9401, a published
# port, root inside the container, a missing line of the hardening (read-only root, no capabilities, no new
# privileges), a key that would undo any of it (PodmanArgs=, Mount=, AddCapability=, ...), or another image than
# the training tenant's - the one image that is certainly in root's storage already - or another command or another
# outbound address than the renderer's /status. It also refuses while a HuggingFace token lies in the training
# tenant's cache: that cache is inside the one directory this container reads, and this container has the host's
# network.
#
# Who reaches 10.20.0.1:9401 once it is open: the guests - the hub, whose pods leave as the node - through the
# libvirt-to-host policy entry. Not the lab uplink: nothing listens on its address and its zone does not open the
# port. Peers on the operator's tailnet reach it with or without the entry, because this host routes 10.20.0.0/24
# for the tailnet - accepted: it is the admin network. No authentication: it is a page of numbers.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
export LC_ALL=C
case ${1:-} in
install|status|remove) [[ $EUID -eq 0 ]] || exec sudo "$0" "$@" ;;
check|slices) ;;
*) echo "usage: ${0##*/} install | status | remove | check | slices" >&2; exit 1 ;;
esac
here=$(dirname "$(readlink -f "$0")")
if [[ $1 != check && $1 != slices ]]; then
    mkdir -p "$here/log"
    exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1
fi
qd='' tmp=''
# The only EXIT trap. Its end is the flush: under sudo the terminal can go away before tee has written the last
# lines, a final refusal among them - wait for it on the way out.
trap '[[ -z $qd ]] || { rm -f "$qd/tenant-metrics.container"; rmdir "$qd"; }; [[ -z $tmp ]] || rm -f "$tmp"; exec >&- 2>&-; wait' EXIT

units=/etc/containers/systemd
lib=/usr/local/lib/flywheel/tenant-metrics
code=(exporter.py metrics_core.py)
quadlet=$here/flywheel/tenant-metrics.container
trainer=$here/flywheel/training-tenant.container
unit=tenant-metrics.service
data=/data/flywheel/tenant-train
# Where a HuggingFace credential would lie: the training tenant's HF_HOME is cache/huggingface under its output.
# For the tests alone, `check` looks under another tree; install and status never read the variable.
scan=$data
[[ $1 != check ]] || scan=${TENANT_METRICS_CHECK_DATA:-$data}
status_url=http://10.20.0.1:9702/status
policy=libvirt-to-host
port=9401/tcp
addr=10.20.0.1:9401
url=http://$addr/metrics
die() { echo "${0##*/}: $*" >&2; exit 1; }
mig() { nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader 2>/dev/null || true; }
image() { sed -n 's/^Image=//p' "$1" | head -1; }
is_open() { firewall-cmd -q "$@" --policy=$policy --query-port=$port; }
zone_ok() { [[ $(firewall-cmd --get-zone-of-interface=virbr-fury 2>/dev/null || true) == libvirt-routed ]]; }

# The exporter's rules, measured on the file that is about to be installed rather than trusted to its comments.
check_rules() {
    local bad want line
    [[ -f $quadlet ]] || die "missing flywheel/tenant-metrics.container next to this script"
    [[ -f $trainer ]] || die "missing flywheel/training-tenant.container next to this script (the image is compared with it)"
    [[ $(image "$quadlet") == *@sha256:* ]] || die "the unit's Image= is not pinned by digest: $(image "$quadlet")"
    [[ $(image "$quadlet") == "$(image "$trainer")" ]] ||
        die "tenant-metrics.container and training-tenant.container name different images. The exporter uses the training
    tenant's image because that one is in root's storage already: copy the Image= line of
    flywheel/training-tenant.container into flywheel/tenant-metrics.container"
    grep -qx 'Pull=never' "$quadlet" || die "the unit has no Pull=never line, and a start must never reach for a registry: put it back in flywheel/tenant-metrics.container"
    bad=$(grep -E '^(AddDevice|EnvironmentFile|Secret|PodmanArgs|GlobalArgs|Mount|Tmpfs|Rootfs|Pod|PublishPort|ExposeHostPort|AddCapability|SecurityLabelDisable|SecurityLabelType|SecurityLabelLevel|SeccompProfile|Unmask|GroupAdd|UserNS|UIDMap|GIDMap|SubUIDMap|SubGIDMap)=' "$quadlet" || true)
    [[ -z $bad ]] || die "the unit has lines the exporter does not get - no GPU, no credentials, no way around the hardening:
$(sed 's/^/    /' <<<"$bad")
    take them out of flywheel/tenant-metrics.container"
    for line in Network=host ReadOnly=true DropCapability=all NoNewPrivileges=true "Environment=METRICS_ADDR=$addr" \
                "Environment=RENDER_STATUS_URL=$status_url" Entrypoint=python3 "Exec=-u /opt/tenant-metrics/exporter.py"; do
        [[ $(grep -cx -- "$line" "$quadlet" || true) -eq 1 ]] ||
            die "the unit must have the line  $line  exactly once - put it back in flywheel/tenant-metrics.container"
    done
    [[ $(grep -c '^Environment=METRICS_ADDR=' "$quadlet" || true) -eq 1 && $(grep -c '^Network=' "$quadlet" || true) -eq 1 ]] ||
        die "the unit sets METRICS_ADDR or Network= more than once, and the last one wins: keep  Environment=METRICS_ADDR=$addr  and  Network=host  only in flywheel/tenant-metrics.container"
    # what runs, and the one connection it makes outwards: string-exact above, and no second line to win over it
    for line in ReadOnly DropCapability NoNewPrivileges User Group Entrypoint Exec Environment=RENDER_STATUS_URL; do
        [[ $(grep -c "^$line=" "$quadlet" || true) -eq 1 ]] ||
            die "the unit must set $line= exactly once (a later line would win) - fix flywheel/tenant-metrics.container"
    done
    grep -Eqx 'User=[1-9][0-9]*' "$quadlet" && grep -Eqx 'Group=[1-9][0-9]*' "$quadlet" ||
        die "the unit must run as a numeric user and group other than 0:  User=65534  and  Group=65534  in flywheel/tenant-metrics.container"
    want=$(printf '%s\n' "Volume=$data:/tenant-train:ro" "Volume=$lib:/opt/tenant-metrics:ro,z" | sort)
    [[ $(grep '^Volume=' "$quadlet" | sort) == "$want" ]] ||
        die "the unit's mounts must be exactly these two, both read-only:
$(sed 's/^/    /' <<<"$want")
    - its own code and the training tenant's output, nothing else of this host. Fix flywheel/tenant-metrics.container"
    ! grep -Eq '^(Exec|Entrypoint|Environment)=.*(/data/flywheel/(train|eval|bags|datasets|cache|episodes)|10\.20\.0\.10|journal)' "$quadlet" ||
        die "a line of the unit names the flywheel's data, the hub's address or the journal. The exporter reads the training
    tenant's output and asks the renderer on 10.20.0.1, nothing else: take it out of flywheel/tenant-metrics.container"
}

# The mount is the training tenant's whole output, its caches included, and $HF_HOME/token (or stored_tokens) is
# where a HuggingFace credential lives. The tenant has no network and needs none; this container has the host's.
# $1 is what to do about one: die, or echo for a warning.
check_tokens() {
    local f
    for f in "$scan/cache/huggingface/token" "$scan/cache/huggingface/stored_tokens"; do
        [[ -e $f || -L $f ]] || continue
        "$1" "a HuggingFace token lies at $f, inside the directory the exporter mounts - and the exporter has the host's
    network. The training tenant has no network and never uses it. Remove it:  sudo shred -u $f
    (and revoke it at its issuer if it was ever real), then:  $0 install"
    done
}
warn() { echo "WARNING: $*"; }

# GPU instance id -> MIG device -> tenant. The tenant on a slice is decided by its CDI name (0:N, the MIG device
# index: what the tenants' units name in AddDevice=). The dashboard cannot see that index - DCGM labels a slice with
# the driver's GPU instance id - so it carries a hand-written map, repeated here. tests/tenant_metrics holds the two
# copies together. Reads nvidia-smi only; ends with 1 when the dashboard would misname a slice.
slices() {
    local rows devs
    echo "## slices: GPU instance id -> MIG device -> tenant, beside the dashboard's names (gpu-tenants-dashboard.yaml)"
    command -v nvidia-smi >/dev/null || { echo "  no nvidia-smi here: this is for the GPU host"; return 1; }
    if [[ $(mig) != Enabled ]]; then
        echo "  MIG is off (flywheel mode): one whole GPU, no slices to name. Look again in tenants mode."
        return 0
    fi
    # the MIG table of plain nvidia-smi: | GPU  GI ID  CI ID  MIG Dev | ...   and -L:   MIG 1g.31gb  Device  2: (UUID...)
    rows=$(nvidia-smi | awk '/MIG devices:/ {p = 1} /Processes:/ {p = 0}
        p && /^\|[ ]+[0-9]+[ ]+[0-9]+[ ]+[0-9]+[ ]+[0-9]+[ ]+\|/ {print "G", $3, $5}' || true)
    devs=$(nvidia-smi -L | sed -n 's/^ *MIG  *\([^ ]*\)  *Device  *\([0-9][0-9]*\):.*/D \2 \1/p' || true)
    printf '%s\n%s\n' "$devs" "$rows" | awk '
        BEGIN {
            tenant[0] = "assistant"; tenant[1] = "robot zero"; tenant[2] = "training"; tenant[3] = "rendering"
            size[0] = "3g"; size[1] = "1g"; size[2] = "1g"; size[3] = "1g"
            # the dashboard: label_replace(..., "slice", "<name>", "GPU_I_ID", "<id>")
            name[1] = "assistant (3g)"; name[11] = "robot zero (1g)"; name[12] = "training (1g)"; name[13] = "rendering (1g)"
            printf "  %-6s %-8s %-10s %-14s %-22s %s\n", "GI id", "MIG dev", "profile", "tenant (0:N)", "the dashboard says", ""
        }
        $1 == "D" { profile[$2] = $3 }
        $1 == "G" {
            gi = $2; dev = $3; seen[gi] = 1; n++
            is = (dev in tenant) ? tenant[dev] : "nobody"
            says = (gi in name) ? name[gi] : "(no name: <profile> / instance " gi ")"
            verdict = "ok"
            if (!(gi in name) || index(name[gi], is " (") != 1) verdict = "WRONG NAME"
            else if (index(profile[dev], size[dev] ".") != 1) verdict = "WRONG SIZE: a " size[dev] " slice was expected for " is
            if (verdict != "ok") bad++
            printf "  %-6s %-8s %-10s %-14s %-22s %s\n", gi, dev, (dev in profile) ? profile[dev] : "?", is, says, verdict
        }
        END {
            for (gi in name) if (!(gi in seen)) { printf "  %-6s %-8s %-10s %-14s %-22s %s\n", gi, "-", "-", "-", name[gi], "NOT ON THIS GPU: its group on the dashboard stays empty"; bad++ }
            if (!n) { print "  no MIG devices in the nvidia-smi table"; bad++ }
            exit bad ? 1 : 0
        }' || {
        echo "  The dashboard would misname a slice. The ids follow the MIG layout (mig-config.sh: -cgi 9,19,19,19). Either put that"
        echo "  layout back:  fury-mode tenants   - or edit the four ids in gitops/observability/gpu-tenants-dashboard.yaml (its"
        echo "  header, the shared panels' label_replace lines, the tenant groups' GPU_I_ID=) and the map in this script's slices()."
        return 1
    }
}

# what the endpoint serves right now, into $tmp; waits up to $1 seconds for the first answer
fetch() {
    local wait_s=$1 t0=$SECONDS
    tmp=$(mktemp)
    until curl -fsS -m 5 -o "$tmp" "$url" 2>/dev/null && grep -q '^tenant_training_up ' "$tmp"; do
        (( SECONDS - t0 < wait_s )) || return 1
        sleep 3
    done
}

prove() {
    echo "## what it serves ($url)"
    grep -E '^tenant_(training|renderer)_' "$tmp" | sed 's/^/  /'
    grep -q '^tenant_training_up 1' "$tmp" ||
        echo "note: tenant_training_up 0 - no round log written in the last two minutes. Expected in flywheel mode, or with the
      tenant stopped. In tenants mode:  systemctl is-active training-tenant.service   and  ./72-training-tenant-install.sh status"
    grep -q '^tenant_renderer_up 1' "$tmp" ||
        echo "note: tenant_renderer_up 0 - no JSON from http://10.20.0.1:9702/status. Expected in flywheel mode. In tenants mode:
      ./73-fleet-renderer-install.sh status"
}

install_() {
    local f src gen ctx owner changed=no opened=no
    local undo="To take it out again:  $0 remove"
    src=${SRC:-$(getent passwd "${SUDO_USER:?run this through sudo, or set SRC}" | cut -d: -f6)/hp-roscon-flywheel}
    check_rules
    check_tokens die
    for f in "${code[@]}"; do
        [[ -f $src/src/tenant-metrics/$f ]] || die "no $src/src/tenant-metrics/$f - copy src/tenant-metrics/ into that checkout, or SRC=<checkout> $0 install"
        python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$src/src/tenant-metrics/$f" ||
            die "$src/src/tenant-metrics/$f does not parse - nothing installed"
    done
    [[ -x /usr/libexec/podman/quadlet ]] || die "no /usr/libexec/podman/quadlet - ./03-packages.sh first"
    command -v firewall-cmd >/dev/null   || die "firewall-cmd is missing"
    zone_ok                              || die "virbr-fury is not in zone libvirt-routed - ./06-network.sh first"
    firewall-cmd --info-policy=$policy >/dev/null 2>&1 || die "no firewalld policy $policy - is this the host 06-network.sh set up?"
    podman image exists "$(image "$quadlet")" ||
        die "$(image "$quadlet") is not in root's storage. It is the training tenant's image. Once, with the uplink:
    sudo podman pull $(image "$quadlet")"
    # the bind mount's source has to exist for the container to start at all
    [[ -d $data ]] || die "no $data - the training tenant creates it:  ./72-training-tenant-install.sh install   then this again"
    if selinuxenabled 2>/dev/null; then
        ctx=$(stat -c %C "$data")
        [[ $ctx == *:container_file_t:s0 ]] ||
            echo "note: $data is labelled $ctx. The training tenant's first start relabels it (its mount has :z); until then the
      exporter cannot read it and serves tenant_training_up 0. Nothing to do here."
    fi

    # let quadlet check the container file, alone, before anything is installed
    qd=$(mktemp -d)
    cp "$quadlet" "$qd/"
    gen=$(QUADLET_UNIT_DIRS=$qd /usr/libexec/podman/quadlet -dryrun 2>&1) || die "quadlet rejected tenant-metrics.container:
$gen"
    grep -E '^(ExecStart|ExecStartPre)=' <<<"$gen" | cut -c1-240 | sed 's/^/quadlet: /' || true
    grep -Eq -- '--cgroups[= ]split' <<<"$gen" ||
        die "quadlet does not run the container inside the unit's cgroup here (no --cgroups=split in the generated
    command), so MemoryMax= and CPUQuota= would bind nothing. Add  CgroupsMode=split  under [Container] in
    flywheel/tenant-metrics.container, then this again"

    cmp -s "$quadlet" "$units/tenant-metrics.container" || changed=yes
    for f in "${code[@]}"; do cmp -s "$src/src/tenant-metrics/$f" "$lib/$f" || changed=yes; done
    install -d -m 0755 -o root -g root "$lib"
    for f in "${code[@]}"; do install -m 0644 -o root -g root "$src/src/tenant-metrics/$f" "$lib/$f"; done
    install -m 0644 -o root -g root "$quadlet" "$units/"
    # not $lib: the container mounts it with :z and relabels it at every start
    restorecon "$units/tenant-metrics.container"
    systemctl daemon-reload

    # A container that systemd started carries its unit's name in a label and is the unit's own. Only a hand-started
    # leftover would collide with the unit's container name.
    if podman container exists tenant-metrics; then
        owner=$(podman inspect --format '{{index .Config.Labels "PODMAN_SYSTEMD_UNIT"}}' tenant-metrics 2>/dev/null || true)
        if [[ -z $owner || $owner == '<no value>' ]]; then
            podman rm -f -t 10 tenant-metrics >/dev/null 2>&1 || true
            echo "removed hand-started tenant-metrics"
        fi
    fi
    # not `systemctl enable`: a quadlet unit is generated and enable refuses it; its [Install] section was applied above
    if [[ $changed == yes || $(systemctl is-active $unit 2>/dev/null || true) != active ]]; then
        systemctl reset-failed $unit 2>/dev/null || true
        systemctl restart $unit              || die "$unit did not start:  sudo journalctl -u ${unit%.service} -n 30
    $undo"
    else
        echo "$unit is running the same unit file and code - left alone"
    fi

    echo "## waiting for the first answer"
    fetch 60 || die "nothing useful from $url after 60 s:  sudo journalctl -u ${unit%.service} -n 30
    The port was NOT opened. $undo"

    # Only now, behind a service that answers: the fetch above is local and does not traverse the policy.
    # Runtime and permanent, and no --reload: a reload throws away runtime-only state, and the zone that
    # virbr-fury sits in is libvirt's runtime state. The hub and every device behind it route through that bridge.
    if ! is_open; then
        firewall-cmd --policy=$policy --add-port=$port || die "could not add $port to policy $policy at runtime. $undo"
        opened=yes
    fi
    if ! is_open --permanent && ! firewall-cmd --permanent --policy=$policy --add-port=$port; then
        # not half open: what this run opened at runtime goes again, so that a reboot changes nothing
        [[ $opened == no ]] || firewall-cmd --policy=$policy --remove-port=$port || true
        die "could not add $port to policy $policy permanently; the runtime opening this run made was taken back. $undo"
    fi
    zone_ok || die "virbr-fury left its zone:  sudo virsh net-destroy fury-net && sudo virsh net-start fury-net  - with the hub VM shut down
    $undo"
    echo "firewalld: $port open in policy $policy (guests to this host). The lab uplink is not opened; tailnet peers reach 10.20.0.1 with or without it (accepted: the admin network)"

    prove
    slices || true
    echo
    echo "next, the hub: commit gitops/observability/{fury-tenants-scrape,fury-assistant-scrape,gpu-tenants-dashboard}.yaml, let"
    echo "      Argo sync, and follow 'Verify each hop' in 75-tenant-metrics.md. Synced in Argo CD means the custom resources"
    echo "      exist, not that Perses accepted the dashboard:  oc -n observability describe persesdashboard gpu-tenants"
    echo "      In a terminal, from a laptop:  FURY_SSH=<user>@<host> tools/hub/training-watch.sh"
}

status() {
    printf '%-28s %s\n' "$unit" "$(systemctl is-active $unit 2>/dev/null || true)"
    systemctl show -p ActiveEnterTimestamp -p NRestarts -p MemoryCurrent -p MemoryPeak $unit 2>/dev/null | sed 's/^/  /' || true
    printf '%-28s %s\n' "image in storage" "$(podman image exists "$(image "$quadlet")" && echo yes || echo no)"
    printf '%-28s %s\n' "listener" "$(ss -Hltn 'sport = :9401' | awk '{print $4}' | paste -sd' ' -)   (expected: $addr and nothing else)"
    if command -v firewall-cmd >/dev/null && firewall-cmd --info-policy=$policy >/dev/null 2>&1; then
        printf '%-28s runtime %s, permanent %s\n' "$port in $policy" "$(is_open && echo open || echo closed)" "$(is_open --permanent && echo open || echo closed)"
    else
        echo "no firewalld policy $policy"
    fi
    if fetch 0; then prove; else echo "nothing useful from $url - not running:  sudo journalctl -u ${unit%.service} -n 30"; fi
    check_tokens warn
    slices || true
    echo "## last 10 log lines"
    journalctl -u ${unit%.service} -n 10 --no-pager 2>/dev/null || true
}

remove() {
    local f
    systemctl stop $unit 2>/dev/null || true
    podman rm -f -t 10 tenant-metrics >/dev/null 2>&1 || true
    rm -f "$units/tenant-metrics.container"
    # by name, then the directory only if that left it empty - no recursive delete
    for f in "${code[@]}"; do rm -f "$lib/$f"; done
    [[ ! -d $lib ]] || rmdir "$lib" 2>/dev/null || echo "left $lib in place: it holds files this script did not put there"
    systemctl daemon-reload
    systemctl reset-failed $unit 2>/dev/null || true
    # the mirror of install: both states, no reload
    if command -v firewall-cmd >/dev/null && firewall-cmd --info-policy=$policy >/dev/null 2>&1; then
        ! is_open             || firewall-cmd --policy=$policy --remove-port=$port
        ! is_open --permanent || firewall-cmd --permanent --policy=$policy --remove-port=$port
    fi
    echo "removed the unit and its code and closed $port in $policy. The image stays: it is the training tenant's."
    echo "next: the hub's scrape target goes down with this. Remove gitops/observability/fury-tenants-scrape.yaml if that is meant to last."
}

case $1 in
check)   check_rules; check_tokens die; echo "tenant-metrics.container keeps the exporter's rules" ;;
slices)  slices ;;
install) date -u; install_ ;;
status)  date -u; status ;;
remove)  date -u; remove ;;
esac
