#!/bin/bash
# GPU telemetry for the hub's dashboard: NVIDIA's DCGM exporter as a root quadlet (flywheel/dcgm-exporter.container),
# listening on the VM network's host address only, with port 9400 opened for the guests (the hub). The lab uplink does
# not reach it. Peers on the operator's tailnet do, with or without that opening, because this host routes
# 10.20.0.0/24 for the tailnet - accepted: it is the admin network.
#
#   ./70-dcgm.sh install [force] pull the pinned image if it is not here, let quadlet check the unit, install and start
#                               it, and only once it answers with samples open 9400/tcp in the libvirt-to-host policy;
#                               then prove it: the GPU/MIG label set and three sample values straight from the endpoint.
#                               Refuses while the installed fury-mode does not know the unit; force installs anyway
#   ./70-dcgm.sh status         unit, listener, firewall, MIG mode, and the same proof
#   ./70-dcgm.sh remove [purge] stop and remove the unit and close the port again; purge also drops the image
#                               (left in place otherwise: getting it back needs the uplink)
#
# The exporter is a client of the GPU like any other, and MIG mode only changes with no clients: fury-mode stops it
# before a switch and starts it afterwards. An installed fury-mode from before that is what install refuses over.
# What every choice rests on, how to check each hop up to the panel, and what to do when one fails: 70-dcgm.md
#
# This project was developed with assistance from AI tools.
set -euo pipefail
export LC_ALL=C
case "${1:-} ${2:-}" in
'install '|'install force'|'status '|'remove '|'remove purge') [[ $EUID -eq 0 ]] || exec sudo "$0" "$@" ;;
*) echo "usage: ${0##*/} install [force] | status | remove [purge]" >&2; exit 1 ;;
esac
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

die() { echo "${0##*/}: $*" >&2; exit 1; }
quadlet=$here/flywheel/dcgm-exporter.container
units=/etc/containers/systemd
unit=dcgm-exporter.service
policy=libvirt-to-host
port=9400/tcp
url=http://10.20.0.1:9400/metrics       # the exporter takes one listen address, and it is the one the hub scrapes

qd='' tmp=''
# The only EXIT trap of this script. Its end is the flush: under sudo the terminal can go away before tee has
# written the last lines, a final die among them - wait for it on the way out.
trap '[[ -z $qd ]] || rm -rf "$qd"; [[ -z $tmp ]] || rm -f "$tmp"; exec >&- 2>&-; wait' EXIT

mig() { nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader; }
image() { sed -n 's/^Image=//p' "$quadlet" | head -1; }
is_open() { firewall-cmd -q "$@" --policy=$policy --query-port=$port; }

# the value of one label on an exposition line, empty when the line does not carry it
awk_lbl='function lbl(s, k,    r) { r = k "=\"[^\"]*\""; if (!match(s, r)) return ""; return substr(s, RSTART + length(k) + 2, RLENGTH - length(k) - 3) }
         function slice(s,    i) { i = lbl(s, "GPU_I_ID"); return i == "" ? "whole gpu" : "instance " i " (" lbl(s, "GPU_I_PROFILE") ")" }'

# DCGM collects on its own clock: the endpoint answers at once, the first samples come a collection later
fetch() {
    local wait_s=$1 t0=$SECONDS
    tmp=$(mktemp)
    until curl -fsS -m 5 -o "$tmp" "$url" 2>/dev/null && grep -q '^DCGM_FI_DEV_FB_USED{' "$tmp"; do
        (( SECONDS - t0 < wait_s )) || return 1
        sleep 5
    done
}

prove() {
    local n want
    echo "## series per entity (from DCGM_FI_DEV_FB_USED)"
    awk "$awk_lbl"' /^DCGM_FI_DEV_FB_USED\{/ { printf "  gpu %s  %-24s %s\n", lbl($0, "gpu"), lbl($0, "modelName"), slice($0) }' "$tmp"
    echo "## three samples"
    awk "$awk_lbl"' /^DCGM_FI_(PROF_GR_ENGINE_ACTIVE|DEV_FB_USED|DEV_POWER_USAGE)\{/ {
        f = $0; sub(/\{.*/, "", f); printf "  %-30s %-28s %s\n", f, slice($0), $NF }' "$tmp" | sort
    n=$(grep -c '^DCGM_FI_DEV_FB_USED{' "$tmp" || true)
    if [[ $(mig) == Enabled ]]; then
        want=$(nvidia-smi -L | grep -c 'MIG ' || true)
        [[ $n -eq $want ]] || echo "WARNING: MIG is on with $want instances but the exporter shows $n entities. It enumerates them once, at start:  sudo systemctl restart $unit"
    fi
    # the fields the dashboard cannot do without; the reasons they go missing are different ones
    if ! grep -q '^DCGM_FI_PROF_GR_ENGINE_ACTIVE{' "$tmp"; then
        echo "## no profiling series. What the exporter said:"
        podman logs dcgm-exporter 2>&1 | grep -i -E 'profil|privilege|SYS_ADMIN|not supported|error' | tail -8 || true
        die "DCGM_FI_PROF_GR_ENGINE_ACTIVE is missing, and it is the only per-instance utilisation there is. In order:
    a recent SELinux denial for this container?   sudo ausearch -m avc -ts recent | grep -i -E 'dcgm|nv-hostengine'
        then first:  sudo setsebool -P container_use_devices=1 && sudo systemctl restart $unit   and:  $0 install
        what comes after that if it is not enough, the unit's label=disable line last: 70-dcgm.md, failure table
    a profiler holding the counters (Nsight)?      it and DCGM cannot both have them
    otherwise this GPU or driver does not offer the field to DCGM: 70-dcgm.md, failure table
    The unit is up and serves the rest (memory per slice) meanwhile. To take it out again:  $0 remove"
    fi
    grep -q '^DCGM_FI_DEV_POWER_USAGE{' "$tmp" ||
        echo "note: no DCGM_FI_DEV_POWER_USAGE series in this mode - the power panel stays empty here (70-dcgm.md, failure table)"
}

install_() {
    local img arch gen zone owner changed=no knows=yes
    local undo="To take it out again:  $0 remove"
    [[ -f $quadlet ]]                       || die "missing flywheel/dcgm-exporter.container next to this script"
    [[ -x /usr/libexec/podman/quadlet ]]    || die "no /usr/libexec/podman/quadlet - ./03-packages.sh first"
    command -v firewall-cmd >/dev/null      || die "firewall-cmd is missing"
    # -c, not -q: a grep that leaves early turns into SIGPIPE + pipefail
    [[ $(nvidia-ctk cdi list 2>/dev/null | grep -c 'nvidia.com/gpu=all' || true) -ge 1 ]] ||
        die "no nvidia.com/gpu=all in the CDI spec:  sudo systemctl restart nvidia-cdi-refresh.service"
    zone=$(firewall-cmd --get-zone-of-interface=virbr-fury 2>/dev/null || true)
    [[ $zone == libvirt-routed ]]           || die "virbr-fury is in zone '${zone:-none}', expected libvirt-routed - ./06-network.sh first"
    firewall-cmd --info-policy=$policy >/dev/null 2>&1 || die "no firewalld policy $policy - is this the host 06-network.sh set up?"
    # a fury-mode from before the exporter leaves it running through a switch, and MIG mode does not change under it
    if [[ $(grep -c '^telemetry=' /usr/local/sbin/fury-mode 2>/dev/null || true) -lt 1 ]]; then
        knows=no
        [[ ${1:-} == force ]] || die "the installed fury-mode does not know this unit (no telemetry= line in /usr/local/sbin/fury-mode),
    and MIG mode will not change while the exporter runs. First:  ./14-flywheel-services.sh   (it installs only, nothing is stopped)
    To install anyway, and stop the unit by hand around every switch:  $0 install force"
    fi

    img=$(image)
    [[ $img == *@sha256:* ]]                || die "the unit's Image= is not pinned by digest: $img"
    if podman image exists "$img"; then
        echo "image is already in root's storage"
    else
        echo "pulling $img"
        podman pull "$img"                  || die "pull failed - the unit has Pull=never, so nothing was installed. It needs the uplink, once."
    fi
    arch=$(podman image inspect --format '{{.Architecture}}' "$img")
    [[ $arch == arm64 ]]                    || die "the image in storage is $arch, not arm64"

    # let quadlet check the container file, alone, before anything is installed
    qd=$(mktemp -d)
    cp "$quadlet" "$qd/"
    gen=$(QUADLET_UNIT_DIRS=$qd /usr/libexec/podman/quadlet -dryrun 2>&1) || die "quadlet rejected dcgm-exporter.container:
$gen"
    grep '^ExecStart=' <<<"$gen" | sed 's/^/quadlet: /' || true

    cmp -s "$quadlet" "$units/dcgm-exporter.container" || changed=yes
    install -m 0644 -o root -g root "$quadlet" "$units/"
    restorecon "$units/dcgm-exporter.container"

    systemctl daemon-reload
    # A container that systemd started carries its unit's name in a label and is the unit's own. Only a hand-started
    # leftover would collide with the unit's container name.
    if podman container exists dcgm-exporter; then
        owner=$(podman inspect --format '{{index .Config.Labels "PODMAN_SYSTEMD_UNIT"}}' dcgm-exporter 2>/dev/null || true)
        if [[ -z $owner || $owner == '<no value>' ]]; then
            podman rm -f -t 10 dcgm-exporter >/dev/null 2>&1 || true
            echo "removed hand-started dcgm-exporter"
        fi
    fi
    # not `systemctl enable`: a quadlet unit is generated and enable refuses it; its [Install] section was applied above
    if [[ $changed == yes || $(systemctl is-active $unit 2>/dev/null || true) != active ]]; then
        systemctl restart $unit             || die "$unit did not start:  sudo journalctl -u $unit -n 30
    $undo"
    else
        echo "$unit is running the same unit file - left alone"
    fi

    echo "## waiting for the first samples"
    fetch 120 || die "nothing useful from $url after 120 s:  sudo journalctl -u $unit -n 30
    $undo"

    # Only now, behind a service that answers: the fetch above is local and does not traverse the policy.
    # Runtime and permanent, and no --reload: a reload throws away runtime-only state, and the zone that
    # virbr-fury sits in is libvirt's runtime state. The hub and every device behind it route through that bridge.
    is_open             || firewall-cmd --policy=$policy --add-port=$port ||
        die "could not add $port to policy $policy at runtime. $undo"
    is_open --permanent || firewall-cmd --permanent --policy=$policy --add-port=$port ||
        die "could not add $port to policy $policy permanently. $undo"
    zone=$(firewall-cmd --get-zone-of-interface=virbr-fury 2>/dev/null || true)
    [[ $zone == libvirt-routed ]]           || die "virbr-fury left its zone (now '${zone:-none}'):  sudo virsh net-destroy fury-net && sudo virsh net-start fury-net  - with the hub VM shut down
    $undo"
    echo "firewalld: $port open in policy $policy (guests to this host). The lab uplink is not opened; tailnet peers reach 10.20.0.1 with or without it (accepted: the admin network)"

    echo "mig mode: $(mig)"
    prove
    echo
    if [[ $knows == yes ]]; then
        echo "next: the installed fury-mode knows this unit and takes it through a switch by itself."
    else
        echo "next: forced. The installed fury-mode does NOT know this unit, and MIG mode will not change while it runs."
        echo "      ./14-flywheel-services.sh first; until then (70-dcgm.md, The mode switch):  sudo systemctl stop $unit   before every fury-mode switch."
    fi
    echo "      Then the hub: commit gitops/observability/{monitoring-stack,fury-gpu-scrape,prometheus-datasource,gpu-tenants-dashboard}.yaml,"
    echo "      let Argo sync, and follow 'Verify each hop' in 70-dcgm.md to its last step. Synced in Argo CD means the custom"
    echo "      resources exist, not that Perses accepted the dashboard. Hop 4 is the check for that:"
    echo "      oc -n observability describe persesdashboard gpu-tenants   and the perses-operator log."
}

status() {
    echo "mig mode: $(mig)"
    printf '%-28s %s\n' "$unit" "$(systemctl is-active $unit 2>/dev/null || true)"
    printf '%-28s %s\n' "image in storage" "$(podman image exists "$(image)" && echo yes || echo no)"
    printf '%-28s %s\n' "listener" "$(ss -Hltn 'sport = :9400' | awk '{print $4}' | paste -sd' ' -)"
    printf '%-28s runtime %s, permanent %s\n' "$port in $policy" "$(is_open && echo open || echo closed)" "$(is_open --permanent && echo open || echo closed)"
    if fetch 0; then prove; else echo "nothing useful from $url - not running, or still before its first collection"; fi
    echo "## last 10 log lines"
    journalctl -u $unit -n 10 --no-pager 2>/dev/null || true
}

remove() {
    systemctl stop $unit 2>/dev/null || true
    podman rm -f -t 10 dcgm-exporter >/dev/null 2>&1 || true
    rm -f "$units/dcgm-exporter.container"
    systemctl daemon-reload
    systemctl reset-failed $unit 2>/dev/null || true
    # the mirror of install: both states, no reload
    ! is_open             || firewall-cmd --policy=$policy --remove-port=$port
    ! is_open --permanent || firewall-cmd --permanent --policy=$policy --remove-port=$port
    echo "removed the unit and closed $port in $policy"
    if [[ ${1:-} == purge ]]; then
        podman rmi "$(image)" >/dev/null 2>&1 && echo "removed the image" || echo "the image was not in storage, or is in use"
    else
        echo "the image stays in root's storage (getting it back needs the uplink):  $0 remove purge"
    fi
    echo "next: the hub's scrape target goes down with this. Remove gitops/observability/fury-gpu-scrape.yaml if that is meant to last."
}

case $1 in
install) install_ "${2:-}" ;;
status)  status ;;
remove)  remove "${2:-}" ;;
esac
