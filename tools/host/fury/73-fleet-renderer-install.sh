#!/bin/bash
# Install act 2's rendering tenant as a host service: flywheel/fleet-renderer.container (MIG slice 0:3) with its
# kernel-cache volume, the fury-mode that starts it in tenants mode and stops it for every switch, and the two
# ports the robots' worlds and the hub's router use. What it is and how to judge a run: 73-fleet-renderer.md
#
#   ./73-fleet-renderer-install.sh build     build the image here, against the local sim image, into root's storage
#                                            (minutes, needs the uplink). Until the hub's pipeline builds it
#   ./73-fleet-renderer-install.sh install   check the unit, let quadlet check it, install unit, volume and fury-mode.
#                                            In tenants mode: start it, wait until it answers /healthz, and only
#                                            then open 9701/udp and 9702/tcp for the guests. In flywheel mode
#                                            nothing starts and the ports stay closed: run install again after
#                                            fury-mode tenants
#   ./73-fleet-renderer-install.sh status    unit, what /status says (device, render rate, ms per batch, robots),
#                                            the ports, the slice
#   ./73-fleet-renderer-install.sh remove    close the ports, stop and remove the unit. The cache volume and the
#                                            image stay
#
# Who reaches 10.20.0.1:9701/udp and :9702/tcp once they are open: the guests - the hub, whose pods leave as the
# node - through the libvirt-to-host policy entry. Not the lab uplink: the entry opens nothing in the uplink's
# zone, nothing listens on the uplink's address, and the lab network has no route to 10.20.0.1. Peers on the
# operator's tailnet reach both with or without the entry, because this host routes 10.20.0.0/24 for the
# tailnet - accepted: it is the admin network. There is no authentication: it is pictures and a status page.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
case ${1:-} in
build|install|status|remove) [[ $EUID -eq 0 ]] || exec sudo "$0" "$@" ;;
*) echo "usage: ${0##*/} build | install | status | remove" >&2; exit 1 ;;
esac
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1
qd=''
# The only EXIT trap. Its end is the flush: under sudo the terminal can go away before tee has written the last
# lines, a final refusal among them - wait for it on the way out.
trap '[[ -z $qd ]] || { rm -f "$qd/fleet-renderer.container" "$qd/fleet-renderer-cache.volume"; rmdir "$qd"; }; exec >&- 2>&-; wait' EXIT

units=/etc/containers/systemd
unit=$here/flywheel/fleet-renderer.container
volume=$here/flywheel/fleet-renderer-cache.volume
svc=fleet-renderer.service
slice=nvidia.com/gpu=0:3
sim_image=localhost/soarm-sim:arm64
policy=libvirt-to-host
ports=(9701/udp 9702/tcp)
url=http://10.20.0.1:9702
wait_s=${WAIT_S:-900}
die() { echo "${0##*/}: $*" >&2; exit 1; }
mig() { nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader; }
image() { sed -n 's/^Image=//p' "$1" | head -1; }
is_open() { firewall-cmd -q "${@:2}" --policy=$policy --query-port="$1"; }
zone_ok() { [[ $(firewall-cmd --get-zone-of-interface=virbr-fury 2>/dev/null || true) == libvirt-routed ]]; }
healthz() { curl -s -o /dev/null -m 4 -w '%{http_code}' $url/healthz || true; }

# What the unit must be, measured on the file that is about to be installed rather than trusted to its comments.
check_rules() {
    local v
    [[ $(sed -n 's/^AddDevice=//p' "$unit") == "$slice" ]] ||
        die "the unit's AddDevice= is not $slice alone - that slice is the rendering tenant's (D164)"
    grep -qx 'Network=host' "$unit" && ! grep -q '^PublishPort=' "$unit" ||
        die "the unit wants Network=host and no PublishPort=: a published port is a DNAT rule, and firewalld accepts
    DNATed traffic before it looks at the libvirt-to-host policy - the port would be open to whoever reaches this host"
    for v in RENDER_STATE_ADDR=10.20.0.1:9701 RENDER_HTTP_ADDR=10.20.0.1:9702; do
        grep -qx "Environment=$v" "$unit" ||
            die "the unit has no line  Environment=$v  - both listeners bind the hub-side address and nothing else,
    and the ports are the ones this script opens and the hub's Service names"
    done
}

build() {
    local src
    src=${SRC:-$(getent passwd "${SUDO_USER:?run this through sudo, or set SRC}" | cut -d: -f6)/hp-roscon-flywheel}
    [[ -f $src/docker/Dockerfile.fleet-renderer ]] || die "no checkout at $src (SRC=<checkout> to name another)"
    podman image exists "$sim_image" ||
        die "$sim_image is not in root's storage, and the scene is baked from it:  ./11-sim-build.sh  first"
    # the last step of the build renders a frame on the cpu device: a wheel set that does not work ends the build
    time podman build -f "$src/docker/Dockerfile.fleet-renderer" --build-arg SIM_IMAGE="$sim_image" \
        -t "$(image "$unit")" "$src" || die "the image did not build (above). Nothing was installed or changed"
    podman image inspect "$(image "$unit")" --format 'image: {{.Os}}/{{.Architecture}}  {{.Size}} bytes  {{.Id}}'
    echo "next:  $0 install"
}

open_ports() {
    local p
    command -v firewall-cmd >/dev/null || die "firewall-cmd is missing"
    firewall-cmd --info-policy=$policy >/dev/null 2>&1 || die "no firewalld policy $policy - ./06-network.sh first"
    zone_ok || die "virbr-fury is not in zone libvirt-routed - ./06-network.sh first"
    # Runtime and permanent, and no --reload: a reload throws away runtime-only state, and the zone binding of the
    # hub's bridge is libvirt's runtime state - with the hub VM running.
    for p in "${ports[@]}"; do
        is_open "$p"             || firewall-cmd --policy=$policy --add-port="$p"
        is_open "$p" --permanent || firewall-cmd --permanent --policy=$policy --add-port="$p"
    done
    zone_ok || die "virbr-fury left its zone:  sudo virsh net-destroy fury-net && sudo virsh net-start fury-net  - with the hub VM shut down"
    echo "firewalld: ${ports[*]} open in policy $policy (guests to this host). The lab uplink is not opened; tailnet peers reach 10.20.0.1 with or without it (accepted: the admin network)"
}

close_ports() {
    local p
    command -v firewall-cmd >/dev/null && firewall-cmd --info-policy=$policy >/dev/null 2>&1 || return 0
    for p in "${ports[@]}"; do
        ! is_open "$p"             || firewall-cmd --policy=$policy --remove-port="$p"
        ! is_open "$p" --permanent || firewall-cmd --permanent --policy=$policy --remove-port="$p"
    done
    echo "closed ${ports[*]} in $policy"
}

install_() {
    local f gen waited=0 code
    for f in "$unit" "$volume" "$here/fury-mode.sh"; do
        [[ -f $f ]] || die "missing ${f#"$here"/} next to this script"
    done
    [[ -x /usr/libexec/podman/quadlet ]] || die "no /usr/libexec/podman/quadlet - ./03-packages.sh first"
    grep -q '^renderer=' "$here/fury-mode.sh" ||
        die "the fury-mode.sh next to this script does not know the renderer (no renderer= line): update the checkout.
    Without it a mode switch would leave the renderer on its slice, and MIG does not change under a client"
    check_rules
    podman image exists "$(image "$unit")" ||
        die "$(image "$unit") is not in root's storage:  $0 build   (minutes, needs the uplink)"

    # let quadlet check the container file, with its volume and nothing else, before anything is installed
    qd=$(mktemp -d)
    cp "$unit" "$volume" "$qd/"
    gen=$(QUADLET_UNIT_DIRS=$qd /usr/libexec/podman/quadlet -dryrun 2>&1) || die "quadlet rejected fleet-renderer.container:
$gen"
    grep -E '^(ExecStart|ExecCondition)=' <<<"$gen" | cut -c1-240 | sed 's/^/quadlet: /' || true
    grep -Eq -- '--cgroups[= ]split' <<<"$gen" ||
        die "quadlet does not run the container inside the unit's cgroup here (no --cgroups=split in the generated
    command), so MemoryMax= and CPUQuota= would bind nothing. Add  CgroupsMode=split  under [Container] in
    flywheel/fleet-renderer.container, then this again"

    install -m 0644 -o root -g root "$unit" "$volume" "$units/"
    install -m 0755 "$here/fury-mode.sh" /usr/local/sbin/fury-mode
    restorecon "$units/fleet-renderer.container" "$units/fleet-renderer-cache.volume" /usr/local/sbin/fury-mode
    systemctl daemon-reload
    systemctl list-unit-files 'fleet-renderer*' --no-pager || true
    echo

    if [[ $(mig) != Enabled ]]; then
        # not `systemctl enable`: a quadlet unit is generated and enable refuses it; its [Install] section was applied above
        echo "installed. MIG is off (flywheel mode), so nothing was started and the ports stay closed."
        echo "After  fury-mode tenants  (from a laptop: tools/hub/fury-switch.sh tenants), which starts it:  $0 install"
        echo "again - it then waits for /healthz and opens ${ports[*]} for the guests."
        return 0
    fi
    systemctl reset-failed "$svc" 2>/dev/null || true
    # restart, not start: a second install is how a changed unit or a new image gets in
    systemctl restart --no-block "$svc"
    echo "started on slice 0:3. Waiting for $url/healthz - the first start on a slice compiles every kernel"
    echo "(minutes; later starts load them from the fleet-renderer-cache volume). Up to $wait_s s:"
    until code=$(healthz); [[ $code == 200 ]]; do
        [[ $(systemctl is-failed "$svc" 2>/dev/null || true) != failed ]] ||
            die "the unit failed. Its end:
$(journalctl -u fleet-renderer -n 15 --no-pager 2>/dev/null | cut -c1-220)
    The ports were NOT opened. Fix, then:  $0 install"
        ((waited < wait_s)) || die "no 200 from $url/healthz after $wait_s s (last: ${code:-none}). The ports were NOT opened.
    Look:  sudo journalctl -u fleet-renderer -n 40    - still compiling: WAIT_S=1800 $0 install
    - a CUDA error at the first start: the failure table of 73-fleet-renderer.md"
        sleep 10; waited=$((waited + 10))
        echo "  ${waited} s: healthz ${code:-none} - $(journalctl -u fleet-renderer -n 1 --no-pager -o cat 2>/dev/null | cut -c1-160)"
    done
    echo "healthz 200 after about $waited s"
    open_ports
    echo
    status
    echo
    echo "next, from a laptop with the hub's kubeconfig, once per hub:  oc apply -f tools/hub/manual/fleet-renderer-endpointslice.yaml"
    echo "and without any world yet, twenty robots on the wall for two minutes:"
    echo "  sudo podman exec fleet-renderer python3 fake_worlds.py --addr 10.20.0.1:9701 --robots 20 --seconds 120"
}

status() {
    local p tbl row pid rest doc
    printf '%-28s %s\n' "$svc" "$(systemctl is-active "$svc" 2>/dev/null || true)"
    systemctl show -p ActiveEnterTimestamp -p NRestarts -p MemoryCurrent -p MemoryPeak -p CPUUsageNSec "$svc" 2>/dev/null | sed 's/^/  /' || true
    printf '%-28s %s\n' "$url/healthz" "$(healthz)   (000: not listening; 503: no frame in the last 2 s, or still compiling)"
    echo "## what the renderer says ($url/status)"
    if doc=$(curl -s -f -m 4 $url/status 2>/dev/null) && [[ -n $doc ]]; then
        if command -v python3 >/dev/null; then
            # no quote inside a quote and no backslash: the host's python may be older than 3.12
            python3 -c '
import json, sys
s = json.loads(sys.argv[1])
g = s.get
per = round(g("batch_ms") / g("batch_worlds"), 2) if g("batch_ms") and g("batch_worlds") else None
print("  phase            %s" % g("phase"))
print("  device           %s" % g("device"))
print("  robots           %s live, %s stale, room for %s" % (g("robots_live"), g("robots_stale"), g("max_robots")))
print("  render rate      %s of %s frames/s, %s late ticks since start" % (g("render_fps"), g("fps_target"), g("overruns")))
print("  ms per batch     %s for %s robots = %s ms a robot; one world while idle: %s" % (g("batch_ms"), g("batch_worlds"), per, g("idle_batch_ms")))
print("  render           %s px, shadows %s" % (g("render_size"), g("shadows")))
print("  datagrams        %s accepted, dropped %s" % (g("datagrams_accepted"), g("dropped")))
print("  pictures         %s streams open, %s jpeg/s, the wall mosaic %s ms each" % (g("streams"), g("jpegs_per_s"), g("wall_ms")))
for r in g("robots") or []:
    print("    %s  %-5s  last state %s s ago  %s/s" % (r["id"], r["state"], r["age_s"], r["datagrams_per_s"]))
' "$doc" || echo "$doc"
        else
            echo "$doc"
        fi
    else
        echo "  no answer"
    fi
    echo "## the ports, for the guests"
    if command -v firewall-cmd >/dev/null && firewall-cmd --info-policy=$policy >/dev/null 2>&1; then
        for p in "${ports[@]}"; do
            printf '  %-26s runtime %s, permanent %s\n' "$p in $policy" "$(is_open "$p" && echo open || echo closed)" "$(is_open "$p" --permanent && echo open || echo closed)"
        done
    else
        echo "  no firewalld policy $policy"
    fi
    echo "## slice 0:3 on the gpu"
    if [[ $(mig) == Enabled ]]; then
        # the MIG table of plain nvidia-smi; the renderer's slice is MIG device 3 of GPU 0 (fourth column)
        tbl=$(nvidia-smi | awk '/MIG devices:/ {p = 1} /Processes:/ {p = 0} p' || true)
        row=$(awk '/^\|[[:space:]]+0[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+3[[:space:]]+\|/' <<<"$tbl" || true)
        if [[ -n $row ]]; then echo "$row"; else echo "$tbl"; fi
        while IFS=, read -r pid rest; do
            pid=${pid//[!0-9]/}
            if [[ -n $pid ]] && grep -qsF "/$svc/" "/proc/$pid/cgroup"; then echo "  renderer pid $pid:$rest"; fi
        done < <(nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null || true)
    else
        echo "  MIG is off (flywheel mode): there is no slice, and the unit skips every start"
    fi
}

remove() {
    close_ports
    systemctl stop "$svc" 2>/dev/null || true
    podman rm -f -t 10 fleet-renderer >/dev/null 2>&1 || true
    rm -f "$units/fleet-renderer.container" "$units/fleet-renderer-cache.volume"
    systemctl daemon-reload
    systemctl reset-failed "$svc" 2>/dev/null || true
    echo "removed the unit and its volume unit. fury-mode stays: it passes over a renderer whose unit is not installed."
    echo "left in place, yours to delete: the compiled kernels (sudo podman volume rm fleet-renderer-cache) and the"
    echo "image (sudo podman rmi $(image "$unit")). On the hub the Service and Route answer 503 until it is back."
}

date -u
case $1 in
build)   build ;;
install) install_ ;;
status)  status ;;
remove)  remove ;;
esac
