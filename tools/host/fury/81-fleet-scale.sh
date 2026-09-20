#!/bin/bash
# Bring the fleet to N micro-VMs: copy-on-write clones of the golden image (80-fleet-golden.sh), each the
# computer of one robot - docs/internal/FLEET-VMS.md, D163 stage A.
#
#   ./81-fleet-scale.sh <N> [vcpus] [mem-mib]   0..32. Creates the missing ones (fleet-vm-01 upward), starts the ones
#                                               that are shut off, removes surplus ones from the highest number down.
#                                               Defaults 2 vCPU / 3072 MiB; sizes apply to VMs created by this run.
#   ./81-fleet-scale.sh status                  one line per VM
#   ./81-fleet-scale.sh destroy-all             the same as 0
#   ./81-fleet-scale.sh enrol-config <file>     install the fleet's enrolment config root-only, shred <file>
#   ./81-fleet-scale.sh guests-shutdown         one-time: let the host shut its guests down in parallel (see below)
#
# fleet-vm-NN has the address 10.20.0.(20+NN) and the MAC 52:54:00:14:01:<NN in hex>; fury-net has no DHCP, so both
# come from the clone's cloud-init seed, with its hostname and the agent's enrolment config (late binding: the
# golden image holds none). Image mode gives each clone its own machine-id and ssh host keys on first boot; the
# agent is enabled in the image and ordered after cloud-init (fleet/Containerfile), so it starts once its config is
# there and makes its key pair then. Optional: /root/fleet/debug.pub, an ssh public key - if it exists, new clones
# get a user "fleet" with it and passwordless sudo, for the benchmark. The key is public; what it opens is not
# small: root on those clones, and with it the fleet's enrolment config. Remove the file after the benchmark.
#
# Needs /root/fleet/agent-config.yaml (root, 0600): the fleet's enrolment config, made on the laptop and copied
# to this host by tools/hub/fleet-enrol-config.sh, then installed with `enrol-config` - the way the host got its
# own (40-device-provision.sh:160-165). It carries the enrolment client key. It is never printed; it reaches a
# clone base64-encoded inside that clone's seed (root:qemu 0640 in a 0750 directory). The seed is attached for as
# short a time as possible: once the clone answers on its address it is ejected - for the running VM and for later
# boots - and shredded, and the clone shreds cloud-init's cached copies itself. `status` says seed:ATTACHED until
# then. The whole list of places the key lives: docs/internal/FLEET-VMS.md section 5.
# This host holds no hub credential (D150), so it cannot tidy RHEM: before scaling DOWN, run
# tools/hub/fleet-status.sh decommission <NN>... on the laptop - this script prints the exact command.
#
# guests-shutdown: libvirt-guests stops guests ONE AFTER ANOTHER by default, up to SHUTDOWN_TIMEOUT for each -
# with the fleet that is a host shutdown of up to (N+1) x 300 s. This sets PARALLEL_SHUTDOWN in
# /etc/sysconfig/libvirt-guests. It is HOST-WIDE: it also governs how the hub VM is shut down with the host. In
# parallel mode SHUTDOWN_TIMEOUT stops being per guest and becomes one countdown for all of them
# (/usr/libexec/libvirt-guests.sh, shutdown_guests_parallel), so the number is set higher than there can be guests:
# every guest, the hub included, is asked in the first second and the hub keeps the full 300 s it has today
# (32-sno-install.sh:113). The unit is NOT restarted - stopping it is what shuts the guests down; the file is read
# when the host next stops.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
usage() { echo "usage: ${0##*/} <N 0..32> [vcpus 1..8] [mem-mib 1536..16384] | status | destroy-all | enrol-config <file> | guests-shutdown" >&2; exit 1; }
max=32
case ${1:-} in
status|destroy-all|guests-shutdown) [[ -z ${2:-} ]] || usage ;;
enrol-config) [[ -n ${2:-} && -z ${3:-} ]] || usage
    [[ -f $2 ]] || { echo "${0##*/}: no such file: $2" >&2; exit 1; } ;;
*)  if ! [[ ${1:-} =~ ^[0-9]{1,2}$ ]] || (( 10#$1 > max )); then
        echo "${0##*/}: N must be a number from 0 to $max - the host has room for about 24 beside the hub" >&2; usage
    fi
    [[ ${2:-2} =~ ^[1-8]$ ]] || usage
    if ! [[ ${3:-3072} =~ ^[0-9]{4,5}$ ]] || (( ${3:-3072} < 1536 || ${3:-3072} > 16384 )); then usage; fi ;;
esac
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1
# One lock for 80 and 81: other people hold sessions on this host, and a failed create removes the disk and seed
# of "its" VM - which a second, concurrent run may just have made. `status` only reads and does not take it.
if [[ $1 != status ]]; then
    exec 9>/run/fleet-stage-a.lock
    flock -n 9 || { echo "${0##*/}: another 80-/81-fleet run is in progress - wait for it. Who:  sudo fuser -v /run/fleet-stage-a.lock" >&2; exit 1; }
fi

pool=/data/libvirt/fleet
golden=$pool/fleet-golden.qcow2
cfg=/root/fleet/agent-config.yaml
headroom_gib=32                         # what the host keeps for itself beyond the VMs asked for
disk_floor_gb=100                       # the flywheel's coordinator stops below this much free on /data (FURY-PLAN decision 6)
disk_per_vm_gb=6                        # what a new clone is expected to write early on; it CAN grow to the image's 20 GiB
created=()
tmp=
die() { echo "${0##*/}: $*" >&2; exit 1; }
cleanup() {
    if [[ -n $tmp ]]; then
        [[ ! -f $tmp/user-data ]] || shred -u "$tmp/user-data"
        rm -f "$tmp/meta-data" "$tmp/network-config"; rmdir "$tmp" 2>/dev/null || true
    fi
    exec >&- 2>&-; wait
}
trap cleanup EXIT

name_of() { printf 'fleet-vm-%02d' "$1"; }
ip_of()   { echo "10.20.0.$((20 + $1))"; }
mac_of()  { printf '52:54:00:14:01:%02x' "$1"; }
defined() { virsh dominfo "$1" >/dev/null 2>&1; }
state()   { virsh domstate "$1" 2>/dev/null || echo absent; }
existing() { local i; for ((i = 1; i <= max; i++)); do if defined "$(name_of "$i")" || [[ -e $pool/$(name_of "$i").qcow2 ]]; then echo "$i"; fi; done; }

ours() {  # a domain called fleet-vm-NN is only ours if its one disk is where this script puts it
    local n=$1 disks
    defined "$n" || return 0
    disks=$(virsh domblklist "$n" --details | awk '$2 == "disk" {print $4}')
    [[ $disks == "$pool/$n.qcow2" ]] || die "a VM named $n exists, but its disk is '${disks:-none}', not $pool/$n.qcow2 - it is not one of this script's. Not starting, ejecting or removing anything; rename or remove that VM first"
}
taken() {  # is VM number $1's MAC or address already somebody's? prints who; silent when free
    local n mac ip d lladdr; n=$(name_of "$1"); mac=$(mac_of "$1"); ip=$(ip_of "$1")
    for d in $(virsh list --all --name); do      # shut-off domains too: ping cannot see those (79-bootc-spike.sh uses fleet-vm-32's pair)
        [[ $d == "$n" ]] || ! virsh dumpxml "$d" 2>/dev/null | grep -qi "<mac address='$mac'" || { echo "the VM '$d' is defined with the MAC $mac"; return; }
    done
    lladdr=$(ip neigh show "$ip" 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "lladdr") print tolower($(i + 1))}' | head -1)
    if [[ -n $lladdr && $lladdr != "$mac" ]]; then echo "$ip was last seen at $lladdr, which is not $n's MAC"; return; fi
    if ping -c1 -W1 "$ip" >/dev/null 2>&1; then echo "something answers on $ip"; fi
}
# The seed carries the fleet's enrolment key, so it is attached for as short a time as possible: once a clone
# answers on its static address, cloud-init has read the whole seed (NoCloud reads user-data, meta-data and the
# network config in one go, before the network is configured) - the disc is ejected for good and the file
# shredded. Later boots do not need it: cloud-init has switched itself off. Runs at the end of every scale run,
# for every VM that still has a seed, so a clone that was slow the first time is caught by the next run.
eject_seeds() {
    local i n t
    for i in $(existing); do
        n=$(name_of "$i")
        [[ -f $pool/$n-seed.iso && $(state "$n") == running ]] || continue
        if ! ping -c1 -W1 "$(ip_of "$i")" >/dev/null 2>&1; then echo "  $n: not answering yet - its seed stays attached. Run this command again in a minute (same N): it ejects what is left"; continue; fi
        t=$(virsh domblklist "$n" --details | awk '$2 == "cdrom" {print $3; exit}')
        if [[ -n $t ]] && virsh change-media "$n" "$t" --eject --config --live >/dev/null 2>&1; then
            shred -u "$pool/$n-seed.iso"; echo "  $n: seed ejected (now and for later boots) and shredded"
        else
            echo "  $n: could not eject its seed (${t:-no cdrom found}) - it stays attached; run this command again, or look:  sudo virsh domblklist $n --details" >&2
        fi
    done
}

line() {  # one summary line for VM number $1
    local n; n=$(name_of "$1")
    local mb; mb=$(du -m "$pool/$n.qcow2" 2>/dev/null | cut -f1 || true)
    # The clone's own disk is the cheapest witness of what its agent pulled: the two images are 4.5 GiB, the
    # rest of a first boot a few hundred MB. Over 3 GB: the embedded image store was NOT used (FLEET-VMS.md, "Images").
    printf '%-12s %-9s %-11s ping:%-3s seed:%-8s disk:%6s MB %s\n' "$n" "$(state "$n")" "$(ip_of "$1")" \
        "$(ping -c1 -W1 "$(ip_of "$1")" >/dev/null 2>&1 && echo yes || echo no)" "$([[ -f $pool/$n-seed.iso ]] && echo ATTACHED || echo gone)" "${mb:--}" \
        "$(if (( ${mb:-0} > 3000 )); then echo "<- PULLED its images: the embedded store was not used"; fi)"
}

remove_vm() {  # clean shutdown first: the agent gets to say goodbye and the overlay is closed properly
    local n i; n=$(name_of "$1")
    ours "$n"
    if [[ $(state "$n") == running ]]; then
        virsh shutdown "$n" >/dev/null || true
        for ((i = 0; i < 12; i++)); do [[ $(state "$n") == running ]] || break; sleep 5; done
        [[ $(state "$n") != running ]] || virsh destroy "$n" >/dev/null
    fi
    if defined "$n"; then virsh undefine --nvram "$n" >/dev/null; fi
    if [[ -f $pool/$n-seed.iso ]]; then shred -u "$pool/$n-seed.iso"; fi
    rm -f "$pool/$n.qcow2"
    echo "removed $n"
}

create_vm() {
    local i=$1 n ip mac; n=$(name_of "$i"); ip=$(ip_of "$i"); mac=$(mac_of "$i")
    qemu-img create -q -f qcow2 -F qcow2 -b "$golden" "$pool/$n.qcow2"
    chown root:qemu "$pool/$n.qcow2"; chmod 0660 "$pool/$n.qcow2"; restorecon "$pool/$n.qcow2"
    printf 'instance-id: %s-%s\nlocal-hostname: %s\n' "$n" "$(date -u +%Y%m%dT%H%M%SZ)" "$n" > "$tmp/meta-data"
    cat > "$tmp/network-config" <<EOF
version: 2
ethernets:
  nic0:
    match: {macaddress: "$mac"}
    addresses: [$ip/24]
    routes: [{to: default, via: 10.20.0.1}]
    nameservers: {addresses: [10.20.0.1]}
EOF
    # The enrolment config goes in base64: no YAML-in-YAML indentation to get wrong, and nothing readable in a diff
    # or a terminal. cloud-init is switched off after this one boot, so a missing seed never re-runs anything.
    ( umask 077
      cat > "$tmp/user-data" <<EOF
#cloud-config
# This project was developed with assistance from AI tools.
hostname: $n
fqdn: $n.fleet.local
preserve_hostname: false
ssh_pwauth: false
disable_root: true
write_files:
  - path: /etc/flightctl/config.yaml
    permissions: "0600"
    owner: root:root
    encoding: b64
    content: $b64
$debug_user
runcmd:
  - [restorecon, -R, /etc/flightctl]
  # cloud-init keeps its own copies of this file - and so of the enrolment key - in the clone: gone once it is installed
  - [sh, -c, 'for f in /var/lib/cloud/instance/user-data.txt /var/lib/cloud/instance/user-data.txt.i /var/lib/cloud/instance/cloud-config.txt /var/lib/cloud/instance/obj.pkl /run/cloud-init/instance-data-sensitive.json; do if [ -f "\$f" ]; then shred -u "\$f"; fi; done']
  - [touch, /etc/cloud/cloud-init.disabled]
EOF
    )
    ( umask 027; xorrisofs -quiet -V cidata -J -r -o "$pool/$n-seed.iso" "$tmp/user-data" "$tmp/meta-data" "$tmp/network-config" )
    shred -u "$tmp/user-data"
    chown root:qemu "$pool/$n-seed.iso"; chmod 0640 "$pool/$n-seed.iso"; restorecon "$pool/$n-seed.iso"
    # Same machine shape as the hub's guest (32-sno-install.sh:77-92): UEFI, host-passthrough, no balloon (4k-page
    # guest, 64k-page host), memory on NUMA node 0. No autostart: after a host reboot, run this script again.
    # shellcheck disable=SC2054  # the commas belong to virt-install, not to the array
    args=(--connect qemu:///system --name "$n" --osinfo rhel10.2
          --machine virt --cpu host-passthrough --vcpus "$vcpus" --memory "$mem" --memballoon none --boot uefi --tpm none
          --numatune 0
          --disk "path=$pool/$n.qcow2,format=qcow2,bus=virtio,cache=none,io=native,discard=unmap"
          --controller type=scsi,model=virtio-scsi
          --disk "path=$pool/$n-seed.iso,device=cdrom,bus=scsi,readonly=on"
          --network "network=fury-net,model=virtio,mac=$mac"
          --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0
          --graphics none --console pty,target.type=serial --rng /dev/urandom
          --import --noautoconsole)
    if ! virt-install "${args[@]}" >/dev/null; then
        shred -u "$pool/$n-seed.iso"; rm -f "$pool/$n.qcow2"
        die "virt-install failed for $n; its disk and seed are removed again. Created before it and left running: ${created[*]:-none} - their seeds are still attached until the next run. Why it failed:  sudo journalctl -u virtqemud -n 30   then run the same command again: it continues from $n"
    fi
    created+=("$n")
}

date -u
if [[ $1 == enrol-config ]]; then
    src=$(readlink -f "$2")
    [[ $src != "$cfg" ]] || die "pass the copied file, not $cfg itself"
    if ! grep -q '^enrollment-service:' "$src" || ! grep -q 'client-key-data:' "$src"; then die "$src is not an embedded enrolment config (tools/hub/fleet-enrol-config.sh makes one)"; fi
    # the development stand-in's hub has the same names: catch a config whose server is not this hub (as 40-device-provision.sh does)
    hub=$(sed -n '/^ *server: *https:\/\//{s|^ *server: *https://||p;q;}' "$src"); hub=${hub%%/*}; hub=${hub%%:*}
    [[ -n $hub ]] || die "no https server in $src"
    ip=$(getent hosts "$hub" | awk 'NR == 1 {print $1}' || true)
    [[ $ip == 10.20.0.10 ]] || die "$hub resolves to '${ip:-nothing}' here, not 10.20.0.10 - the config was made against another hub, or ./06-network.sh has not run"
    install -d -m 0700 -o root -g root /root/fleet
    install -m 0600 -o root -g root "$src" "$cfg"
    shred -u "$src"
    echo "installed $cfg (0600), shredded $src. Certificate: $(sed -n 's/^ *client-certificate-data: *//p' "$cfg" | head -1 | base64 -d | openssl x509 -noout -enddate)"
    exit 0
fi
if [[ $1 == guests-shutdown ]]; then
    f=/etc/sysconfig/libvirt-guests; want_par=40
    [[ -f $f ]] || die "no $f - 32-sno-install.sh finish creates it (ON_SHUTDOWN=shutdown, SHUTDOWN_TIMEOUT=300). Run that first, this only adds to it"
    show() { local k; for k in ON_SHUTDOWN SHUTDOWN_TIMEOUT PARALLEL_SHUTDOWN; do printf '    %-18s %s\n' "$k" "$(sed -n "s/^$k=//p" "$f" | tail -1 | grep . || echo '(unset: libvirt default, PARALLEL_SHUTDOWN 0 = one after another)')"; done; }
    echo "before:"; show
    [[ $(sed -n 's/^ON_SHUTDOWN=//p' "$f" | tail -1) == shutdown ]] || die "ON_SHUTDOWN is not 'shutdown' in $f - parallel shutdown only applies to that mode. Not changing anything"
    if [[ $(sed -n 's/^PARALLEL_SHUTDOWN=//p' "$f" | tail -1) == "$want_par" ]]; then echo "already set - nothing to change"; exit 0; fi
    bak=$f.bak.$(date -u +%Y%m%dT%H%M%SZ)       # a host-wide file on a shared machine: keep what was there
    cp -a "$f" "$bak"; echo "backup: $bak"
    if grep -Eq '^#?PARALLEL_SHUTDOWN=' "$f"; then sed -E -i "s/^#?PARALLEL_SHUTDOWN=.*/PARALLEL_SHUTDOWN=$want_par/" "$f"
    else
        [[ -z $(tail -c1 "$f") ]] || echo >> "$f"     # no newline at the end: the append would glue onto the last line
        echo "PARALLEL_SHUTDOWN=$want_par" >> "$f"
    fi
    restorecon "$f"                                  # sed -i replaces the file
    echo "after:"; show
    echo "the file's last lines, as the unit will read them:"; tail -5 "$f" | sed 's/^/    /'
    echo "host-wide: the hub VM is shut down under the same setting. All guests are now asked at once and share one SHUTDOWN_TIMEOUT"
    echo "countdown (unchanged), instead of up to that long each, one after another. libvirt-guests was not restarted, on purpose."
    exit 0
fi
if [[ $1 == status ]]; then
    have=$(existing)
    [[ -n $have ]] || { echo "no fleet VMs. golden image: $([[ -f $golden ]] && echo present || echo "missing - ./80-fleet-golden.sh build")"; exit 0; }
    for i in $have; do line "$i"; done
    free -g | awk '/^Mem:/ {print "host memory available: " $7 " GiB"}'
    exit 0
fi

want=$((10#${1/destroy-all/0})); vcpus=${2:-2}; mem=${3:-3072}
have=$(existing); missing=(); surplus=()
for ((i = 1; i <= want; i++)); do [[ " ${have//$'\n'/ } " == *" $i "* ]] || missing+=("$i"); done
for i in $have; do (( i <= want )) || surplus+=("$i"); done

# ---- every check before anything changes
if (( ${#missing[@]} > 0 )); then
    for c in virt-install qemu-img xorrisofs shred openssl base64; do command -v "$c" >/dev/null || die "$c is missing"; done
    [[ -f $golden ]] || die "no golden image - ./80-fleet-golden.sh build"
    [[ $(virsh net-info fury-net 2>/dev/null | awk '/^Active:/ {print $2}') == yes ]] || die "fury-net is not active - ./06-network.sh"
    [[ -f $cfg ]] || die "no $cfg. On the laptop:  tools/hub/fleet-enrol-config.sh   (it makes the enrolment config and tells you how to install it here)"
    [[ $(stat -c '%U %a' "$cfg") == 'root 600' ]] || die "$cfg must be root's, mode 600:  sudo chown root: $cfg && sudo chmod 600 $cfg"
    if ! grep -q '^enrollment-service:' "$cfg" || ! grep -q 'client-key-data:' "$cfg"; then die "$cfg is not an embedded enrolment config - make it again with tools/hub/fleet-enrol-config.sh"; fi
    sed -n 's/^ *client-certificate-data: *//p' "$cfg" | head -1 | base64 -d 2>/dev/null | openssl x509 -noout -checkend 3600 >/dev/null 2>&1 ||
        die "the enrolment certificate in $cfg has expired or expires within the hour. On the laptop:  tools/hub/fleet-enrol-config.sh"
    install -d -m 0750 -o root -g qemu "$pool"; restorecon "$pool"      # 80 made it; the seeds rely on its mode, so assert it here too
    b64=$(base64 -w0 "$cfg") || die "could not encode $cfg"
    [[ -n $b64 ]] || die "$cfg encoded to nothing - a clone would get an empty enrolment config"
    for i in "${missing[@]}"; do
        who=$(taken "$i"); [[ -z $who ]] || die "not creating $(name_of "$i"): $who. (The spike holds fleet-vm-32's pair:  ./79-bootc-spike.sh remove.) Nothing was created"
    done
    davail=$(df -BG --output=avail /data | tail -1 | tr -dc 0-9)
    dneed=$(( disk_floor_gb + ${#missing[@]} * disk_per_vm_gb ))
    (( davail >= dneed )) || die "/data has $davail GB free. ${#missing[@]} new clones want $dneed GB: the $disk_floor_gb GB below which the flywheel's coordinator stops, plus $disk_per_vm_gb GB each. Free space, or ask for fewer"
    echo "disk: /data has $davail GB free. Clones are thin; each CAN grow to the image's 20 GiB - worst case for $want VMs: $(( want * 20 )) GiB. Floor to keep: $disk_floor_gb GB"
    (( davail - want * 20 >= disk_floor_gb )) || echo "warning: that worst case would go below the floor - watch  df -h /data  if the robots start pulling images" >&2
    need=$(( ${#missing[@]} * mem / 1024 + headroom_gib ))
    avail=$(free -g | awk '/^Mem:/ {print $7}')
    (( avail >= need )) || die "${#missing[@]} new VMs at $mem MiB need $need GiB with the host's $headroom_gib GiB of headroom, $avail GiB are available. Ask for fewer, or smaller ones"
    tmp=$(mktemp -d)
    debug_user='# no /root/fleet/debug.pub: nobody can log in to this clone, flightctl console is the way in'
    if [[ -s /root/fleet/debug.pub ]]; then
        dk=$(head -1 /root/fleet/debug.pub)
        if grep -q 'PRIVATE KEY' /root/fleet/debug.pub || ! [[ $dk =~ ^(ssh-(ed25519|rsa)|ecdsa-sha2-[a-z0-9-]+)\ [A-Za-z0-9+/=]+ ]] || [[ $dk == *[\"\\]* ]]; then
            die "/root/fleet/debug.pub is not one plain ssh PUBLIC key line - fix or remove it"
        fi
        debug_user=$(printf 'users:\n  - name: fleet\n    groups: [wheel]\n    sudo: ["ALL=(ALL) NOPASSWD:ALL"]\n    lock_passwd: true\n    ssh_authorized_keys:\n      - "%s"' "$dk")
        echo "new clones get the user 'fleet' with the key in /root/fleet/debug.pub. Whoever holds that key is root on those clones, and a"
        echo "clone's root can read the fleet's enrolment config for as long as its certificate is valid. After the benchmark:  sudo rm -f /root/fleet/debug.pub"
    fi
fi

for i in $have; do ours "$(name_of "$i")"; done       # before anything changes: every fleet-vm-NN we may start, eject from or remove is ours

if (( ${#surplus[@]} > 0 )); then
    echo "scaling down. RHEM still lists these devices - on the laptop, best BEFORE this, otherwise right after:"
    echo "    tools/hub/fleet-status.sh decommission $(for i in "${surplus[@]}"; do printf '%02d ' "$i"; done)"
    for ((k = ${#surplus[@]} - 1; k >= 0; k--)); do remove_vm "${surplus[k]}"; done
fi
for i in $have; do
    n=$(name_of "$i")
    if (( i <= want )) && [[ $(state "$n") == 'shut off' ]]; then virsh start "$n" >/dev/null; echo "started $n (it existed, shut off)"; fi
    if (( i <= want )) && ! defined "$n"; then die "$pool/$n.qcow2 exists but no VM $n is defined - a half-removed VM. Remove its files by hand, then run this again:  sudo shred -u $pool/$n-seed.iso; sudo rm -f $pool/$n.qcow2"; fi
done
for i in "${missing[@]+"${missing[@]}"}"; do create_vm "$i"; done
if (( ${#created[@]} > 0 )); then
    echo "waiting for ${#created[@]} new VM(s) to answer, then their seeds go (up to 3 minutes)"
    for ((k = 0; k < 36; k++)); do
        pending=0
        for i in "${missing[@]}"; do ping -c1 -W1 "$(ip_of "$i")" >/dev/null 2>&1 || pending=$((pending + 1)); done
        (( pending > 0 )) || break
        sleep 5
    done
fi
eject_seeds

echo "fleet: $want wanted, ${#missing[@]} created, ${#surplus[@]} removed"
for i in $(existing); do line "$i"; done
if (( ${#missing[@]} > 0 )); then
    echo "new VMs take about half a minute to boot and another to ask for enrolment. Then, on the laptop:  tools/hub/fleet-approve.sh --watch"
fi
