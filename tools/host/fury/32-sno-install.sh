#!/bin/bash
# Single-node OpenShift hub as a KVM guest, agent-based install. One stage at a time:
#
#   ./32-sno-install.sh fetch [channel]   installer + oc for aarch64 from the mirror, checksums verified (default stable-4.22)
#   ./32-sno-install.sh image             render the configs with the pull secret and an ssh key, build the agent ISO
#   ./32-sno-install.sh vm                define and start the guest (32 vCPU / 128 GiB / 600 GB) from that ISO
#   ./32-sno-install.sh wait              follow the install to the end - run it in tmux, it takes about an hour
#   ./32-sno-install.sh finish            eject and delete the ISO, autostart, clean guest shutdown with the host, first oc checks
#
# Everything lives in /root/sno-install (mode 700) and this script logs there too, not under the shared
# home: the rendered config, the ISO and the installer's state all carry the pull secret, and `wait` prints
# the kubeadmin password when it finishes. Expects the pull secret at /root/sno-install/pull-secret.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
work=/root/sno-install
cluster=$work/cluster
name=sno-flywheel
mac=52:54:00:14:00:0a
pool=/data/libvirt/images
iso=$pool/agent.aarch64.iso
umask 077
mkdir -p "$work/log" "$work/bin"
exec > >(tee -a "$work/log/$(date -u +%Y%m%dT%H%M%SZ)-${1:-none}.log") 2>&1
export PATH=$work/bin:$PATH

die() { echo "${0##*/}: $*" >&2; exit 1; }

case ${1:-} in
fetch)
    channel=${2:-stable-4.22}
    base=https://mirror.openshift.com/pub/openshift-v4/aarch64/clients/ocp/$channel
    ver=$(curl -fsSL "$base/release.txt" | sed -n 's/^Name:[[:space:]]*//p')
    [[ -n $ver ]] || die "could not read the version from $base"
    echo "channel $channel is at $ver"
    cd "$work/bin"
    curl -fL --progress-bar -O "$base/openshift-install-linux-$ver.tar.gz" -O "$base/openshift-client-linux-$ver.tar.gz" -O "$base/sha256sum.txt"
    sha256sum -c --ignore-missing sha256sum.txt
    tar -xzf "openshift-install-linux-$ver.tar.gz" openshift-install
    tar -xzf "openshift-client-linux-$ver.tar.gz" oc kubectl
    v=$(openshift-install version); echo "$v"
    [[ $v == *'release architecture arm64'* ]] || die "this is not the arm64 installer"
    ;;
image)
    command -v nmstatectl >/dev/null         || die "nmstatectl is missing - ./31-sno-hostprep.sh first"
    [[ -x $work/bin/openshift-install ]]     || die "run '$0 fetch' first"
    [[ -s $work/pull-secret ]]               || die "no pull secret at $work/pull-secret"
    jq -e '.auths | length > 0' "$work/pull-secret" >/dev/null || die "the pull secret is not the expected JSON"
    [[ -f $work/core_ed25519 ]] || ssh-keygen -q -t ed25519 -N '' -C "core@$name" -f "$work/core_ed25519"
    if [[ -e $cluster ]]; then die "$cluster exists - move it aside before building a new image"; fi
    mkdir -p "$cluster"
    grep -v '^#' "$here/sno/install-config.yaml" > "$cluster/install-config.yaml"
    grep -v '^#' "$here/sno/agent-config.yaml"   > "$cluster/agent-config.yaml"
    {
        printf "pullSecret: '%s'\n" "$(jq -c . "$work/pull-secret")"
        printf "sshKey: '%s'\n" "$(cat "$work/core_ed25519.pub")"
    } >> "$cluster/install-config.yaml"
    grep -c "$mac" "$cluster/agent-config.yaml" | grep -qx 2 || die "the MAC in agent-config.yaml does not match this script"
    openshift-install --dir "$cluster" agent create image
    install -m 0600 "$cluster/agent.aarch64.iso" "$iso"
    ls -lh "$iso"
    echo "image built. it carries the pull secret: root-only, and 'finish' deletes it"
    ;;
vm)
    [[ -s $iso ]]                                                                      || die "no agent ISO - run '$0 image' first"
    [[ $(virsh net-info fury-net 2>/dev/null | awk '/^Active:/ {print $2}') == yes ]]  || die "fury-net is not active"
    if virsh dominfo "$name" >/dev/null 2>&1; then die "$name is already defined"; fi
    if ping -c1 -W1 10.20.0.10 >/dev/null 2>&1; then die "something already answers on 10.20.0.10"; fi
    free -g | awk '/^Mem:/ {if ($7 < 150) exit 1}'                                     || die "not enough free memory for a 128 GiB guest"
    os=generic
    for o in linux2024 linux2022; do
        if osinfo-query os short-id="$o" 2>/dev/null | awk -v o="$o" '$0 ~ o {f=1} END {exit !f}'; then os=$o; break; fi
    done
    # disk first, ISO second: the empty disk falls through to the ISO, and after the installer's reboot the
    # node comes up from disk with no media juggling. No balloon: this host has 64k pages, the guest 4k.
    # numatune 0: the host has nine NUMA nodes, 1-8 being cpu-less ones the GPU driver owns - guest RAM stays on node 0.
    # shellcheck disable=SC2054  # the commas belong to virt-install, not to the array
    args=(--connect qemu:///system --name "$name" --osinfo "$os"
          --machine virt --cpu host-passthrough --vcpus 32,sockets=1,cores=32,threads=1
          --memory 131072 --memballoon none --boot uefi --tpm none
          --numatune 0
          --disk "path=$pool/$name.qcow2,size=600,format=qcow2,bus=virtio,cache=none,io=native,discard=unmap,boot.order=1"
          --controller type=scsi,model=virtio-scsi
          --disk "path=$iso,device=cdrom,bus=scsi,readonly=on,boot.order=2"
          --network "network=fury-net,model=virtio,mac=$mac"
          --graphics none --console pty,target.type=serial --rng /dev/urandom
          --import --noautoconsole)
    virt-install "${args[@]}" --print-xml --dry-run | grep -E '<gic|<loader|<boot order|<memballoon' || die "virt-install rejected the definition"
    virt-install "${args[@]}"
    virsh domstate "$name"
    echo "started. next:  tmux new -s sno   then   $0 wait      (console: sudo virsh console $name, leave with Ctrl-])"
    ;;
wait)
    openshift-install --dir "$cluster" agent wait-for bootstrap-complete --log-level=info
    openshift-install --dir "$cluster" agent wait-for install-complete --log-level=info
    echo "the kubeadmin password was printed above and is in $cluster/auth/kubeadmin-password - keep both out of chat and git"
    ;;
finish)
    export KUBECONFIG=$cluster/auth/kubeconfig
    [[ -s $KUBECONFIG ]] || die "no kubeconfig yet"
    oc get nodes -o wide
    oc get clusterversion
    oc get co --no-headers | awk '$3 != "True" || $5 == "True" {bad = 1; print "not settled: " $0} END {if (!bad) print "all cluster operators available and not degraded"}'
    virsh change-media "$name" sda --eject --config --live || true
    rm -f "$iso" "$cluster/agent.aarch64.iso"
    virsh autostart "$name"
    # a host reboot takes minutes here: let the guest shut down cleanly first
    # RHEL 10 ships no /etc/sysconfig/libvirt-guests; the unit reads it if it exists
    cfg=/etc/sysconfig/libvirt-guests
    touch "$cfg"
    for kv in ON_SHUTDOWN=shutdown SHUTDOWN_TIMEOUT=300; do
        if grep -q "^#\?${kv%%=*}=" "$cfg"; then sed -i "s/^#\?${kv%%=*}=.*/$kv/" "$cfg"; else echo "$kv" >> "$cfg"; fi
    done
    systemctl enable --now libvirt-guests.service
    install -m 0600 "$KUBECONFIG" /root/fury-sno.kubeconfig
    oc debug node/master-0 -q -- chroot /host sh -c 'grep PRETTY_NAME /etc/os-release; getconf PAGESIZE' 2>/dev/null || true
    echo "kubeconfig: /root/fury-sno.kubeconfig (never merge it with the desktop's - same names, different CA)"
    ;;
*)  echo "usage: $0 fetch [channel] | image | vm | wait | finish" >&2; exit 1 ;;
esac
