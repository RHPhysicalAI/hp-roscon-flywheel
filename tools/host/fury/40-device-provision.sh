#!/bin/bash
# Make this host the RHEM-managed device: the hand-installed policy unit goes, the distro's policy.json is
# kept and compared with the copy the Fleet will write over it (D042), the flightctl agent is installed
# (package mode, pinned) and started with an enrollment config.
#
#   ./40-device-provision.sh <agent-config.yaml>    first run
#   ./40-device-provision.sh                        again later: same checks, the installed config is left alone
#
# <agent-config.yaml> is made on a laptop that is logged in to this hub with flightctl, then copied here
# (41-device-enroll.md has the whole sequence):
#     flightctl certificate request --signer=enrollment --expiration=365d --output=embedded \
#         --name fury-host --output-dir "$d" > "$d/agent-config.yaml"
# It carries the enrollment client key: it is installed root-only, never printed, and the copy handed to
# this script is shredded. Needs fleet-act-inference.yaml (gitops/rhem/) next to this script.
#
# Not done here, on purpose: no NVIDIA repo and no `nvidia-ctk cdi generate` (the toolkit came from RHEL
# supplementary in 03 and nvidia-cdi-refresh owns the CDI spec), no /etc/hosts entries (dnsmasq answers
# the hub's names), no subscription step, no state directories (the Fleet mounts none since the role split).
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

agent_ver=1.3.0-1.el10
repo_url=https://rpm.flightctl.io/flightctl-epel10.repo
hub=10.20.0.10
pull_default=insecureAcceptAnything     # the label this host is approved with; every other device renders "reject"
cfg=/etc/flightctl/config.yaml
cert=/var/lib/flightctl/certs/agent.crt
live=/etc/containers/policy.json
keep=/etc/containers/policy.json.rhel-default
units=/etc/containers/systemd
src=${1:-}
fleet=$here/fleet-act-inference.yaml
[[ -f $fleet ]] || fleet=$here/../../../gitops/rhem/fleet-act-inference.yaml

die()  { echo "${0##*/}: $*" >&2; exit 1; }
warn() { echo "${0##*/}: warning: $*" >&2; }
fp=$(mktemp); ca=$(mktemp)      # the Fleet's policy.json and the hub CA from the config - neither is a secret
trap 'rm -f "$fp" "$ca"' EXIT

[[ $(uname -m) == aarch64 ]]   || die "written for the aarch64 host, this is $(uname -m)"
[[ -f $fleet ]]                || die "no fleet-act-inference.yaml next to this script - copy gitops/rhem/fleet-act-inference.yaml here"
for c in podman skopeo jq openssl nvidia-smi nvidia-ctk; do
    command -v "$c" >/dev/null || die "$c is missing - ./03-packages.sh first"
done
pv=$(podman version --format '{{.Client.Version}}')
[[ $(printf '%s\n' 5.5 "$pv" | sort -V | head -1) == 5.5 ]] || die "podman $pv is older than 5.5 (image volumes)"

# ---- the enrollment config, checked before anything changes. Its contents never reach the log.
if [[ -s $cert ]]; then
    [[ -z $src ]] || warn "already enrolled - $src is ignored and left where it is"
    src=
elif [[ -n $src ]]; then
    [[ -f $src && -r $src ]]                    || die "cannot read $src"
    [[ $(readlink -f "$src") != "$cfg" ]]       || die "pass the copied file, not $cfg itself"
    grep -q '^enrollment-service:' "$src"       || die "$src is not an agent config (no enrollment-service section)"
    grep -q 'client-key-data:' "$src"           || die "$src has no embedded key - it has to come from --output=embedded"
    hostport=$(sed -n '/^ *server: *https:\/\//{s|^ *server: *https://||p;q;}' "$src"); hostport=${hostport%%/*}
    host=${hostport%%:*}; port=443
    if [[ $hostport == *:* ]]; then port=${hostport##*:}; fi
    [[ -n $host ]]                              || die "no https server in $src"
    ip=$(getent hosts "$host" | awk 'NR == 1 {print $1}' || true)
    [[ $ip == "$hub" ]]                         || die "$host resolves to '${ip:-nothing}' here, not $hub"
    # the development stand-in's hub has the same names and another CA: catch a config made against it
    sed -n '/^ *certificate-authority-data: */{s///p;q;}' "$src" | base64 -d > "$ca" 2>/dev/null || true
    seen=$(timeout 15 openssl s_client -connect "$host:$port" -servername "$host" -CAfile "$ca" </dev/null 2>&1 || true)
    case $seen in
        *'Verify return code: 0 (ok)'*) echo "hub $host:$port presents a certificate from the CA in the config" ;;
        *) warn "could not confirm that $host:$port chains to the CA in the config - if the agent logs x509 errors, the config was made against the other cluster" ;;
    esac
elif [[ ! -s $cfg ]]; then
    die "no enrollment config. Make one on the laptop (top of this script, or 41-device-enroll.md), copy it here, pass its path"
fi

# ---- the hand-installed policy has to be down before this goes any further; the operator stops it, not this script.
# The Fleet's copy runs as act-inference-<id>-act-inference, so the names would not clash - but two /run_policy
# servers on one Zenoh graph get every goal rejected (D132).
state=$(systemctl is-active act-inference.service 2>/dev/null || true)
case $state in
    inactive|failed|unknown|'') ;;
    *) die "the hand-installed policy is still serving (act-inference.service is $state). Stop the recorder and the
policy yourself, then run this again - the sim can stay up, and nothing has been changed yet:
    sudo systemctl stop act-coordinator.service act-inference.service" ;;
esac
if podman container exists act-inference; then
    die "a hand-started container named act-inference exists. Remove it, then run this again:
    ./13-first-inference.sh down"
fi

# ---- policy.json (D042). The Fleet's inline file replaces the distro's: keep the distro's, and make sure the
# Fleet carries the Red Hat registry entries exactly as this release ships them.
awk '/- path: \/etc\/containers\/policy\.json/ {f=1; next}
     f && /content: \|/ {c=1; next}
     c && /^ *- path:/ {exit}
     c {sub(/^ +/, ""); print}' "$fleet" |
    sed -E "s/\{\{ getOrDefault \.metadata\.labels \"pull_default\" \"[A-Za-z]+\" \}\}/$pull_default/" > "$fp"
jq -e '.default and .transports.docker' "$fp" >/dev/null || die "could not read the Fleet's policy.json out of $fleet"
if diff -q <(jq -S . "$live") <(jq -S . "$fp") >/dev/null; then
    echo "$live is already the Fleet's"
    [[ -s $keep ]] || warn "no copy of the distro's policy.json was kept ($keep)"
elif [[ ! -s $keep ]]; then
    pkg=$(rpm -qf "$live") || die "$live belongs to no package - find out who wrote it before the Fleet replaces it"
    edited=$(rpm -V "$pkg" | grep -F "$live" || true)
    [[ -z $edited ]] || warn "$live was edited after $pkg installed it ($edited) - the edited file is what gets kept"
    install -m 0644 "$live" "$keep"
    restorecon "$keep"
    echo "kept the policy.json from $pkg as $keep"
fi
if [[ -s $keep ]]; then
    rh='.transports.docker | {"registry.access.redhat.com": .["registry.access.redhat.com"], "registry.redhat.io": .["registry.redhat.io"]}'
    if ! diff <(jq -S "$rh" "$keep") <(jq -S "$rh" "$fp"); then
        die "the Fleet's policy.json does not carry this release's Red Hat registry entries (above: < this host, > the Fleet).
Fix gitops/rhem/fleet-act-inference.yaml first - once the Fleet's file lands, registry.redhat.io pulls would break here."
    fi
    echo "Red Hat registry entries: the Fleet's copy matches this release"
    # shellcheck disable=SC2016  # $t is jq's, not the shell's
    scopes='.transports | to_entries[] | .key as $t | .value | keys[] | "\($t) \(if . == "" then "*" else . end)"'
    echo "default: $(jq -r '.default[0].type' "$keep") here -> $(jq -r '.default[0].type' "$fp") in the Fleet's. Scopes the Fleet's copy drops:"
    comm -23 <(jq -r "$scopes" "$keep" | sort) <(jq -r "$scopes" "$fp" | sort) | sed 's/^/    /'
fi
while read -r k; do
    case $k in /etc/pki/containers/*) continue ;; esac      # the Fleet writes these two itself
    [[ -s $k ]] || die "the Fleet's policy.json points at $k, which does not exist on this host"
done < <(jq -r '.transports.docker[][] | .keyPath, .rekorPublicKeyPath | select(. != null)' "$fp" | sort -u)

# ---- the agent: package mode, no weak deps (no greenboot). The repo is left disabled so that nobody's
# `dnf update` on this shared machine moves the agent off the pinned build.
if [[ $(rpm -q --qf '%{VERSION}-%{RELEASE}' flightctl-agent 2>/dev/null || true) != "$agent_ver" ]]; then
    curl -fsSL -o /etc/yum.repos.d/flightctl.repo "$repo_url"
    sed -i 's/^enabled=1$/enabled=0/' /etc/yum.repos.d/flightctl.repo
    restorecon /etc/yum.repos.d/flightctl.repo
    dnf -y install --enablerepo=flightctl --setopt=install_weak_deps=False "flightctl-agent-$agent_ver"
fi
rpm -q flightctl-agent flightctl-selinux
if rpm -q greenboot flightctl-greenboot >/dev/null 2>&1; then warn "greenboot is installed; a package-mode device does not want it"; fi

# ---- only now does the hand-installed unit go: had dnf failed above, the old policy would still be startable
if [[ -f $units/act-inference.container ]]; then
    rm -f "$units/act-inference.container"
    systemctl daemon-reload
    systemctl reset-failed act-inference.service 2>/dev/null || true
    echo "removed $units/act-inference.container (the source stays in flywheel/)"
fi
if grep -qs '^Requires=.*act-inference\.service' "$units/act-coordinator.container"; then
    warn "act-coordinator.container still has Requires=act-inference.service, a unit that no longer exists: the recorder will not start until that line is gone"
fi
want=$(sed -n 's/^ *Image=\(quay\.io\/jary\/soarm-flywheel@sha256:[0-9a-f]*\).*/\1/p' "$fleet" | head -1)
for u in "$units"/*.container; do
    [[ -f $u ]] || continue
    img=$(sed -n 's/^Image=//p' "$u" | head -1)
    if [[ $img == quay.io/jary/soarm-flywheel@* && $img != "$want" ]]; then
        warn "${u##*/} names ${img##*@}, not the Fleet's digest: under the Fleet's policy.json podman will run it from local storage but never pull it again"
    fi
done

if [[ -n $src ]]; then
    install -D -m 0600 -o root -g root "$src" "$cfg"
    restorecon -R /etc/flightctl
    shred -u "$src"
    echo "installed $cfg (0600), shredded $src"
fi

# ---- the GPU as the Fleet's quadlet will find it. Read-only: the CDI spec is nvidia-cdi-refresh's.
[[ ! -e /etc/cdi/nvidia.yaml ]] || warn "/etc/cdi/nvidia.yaml exists; it goes stale at the next mode switch - remove it"
mode=$(nvidia-smi -i 0 --query-gpu=mig.mode.current --format=csv,noheader)
devs=$(nvidia-ctk cdi list 2>/dev/null || true)
if [[ $mode == Disabled ]]; then
    grep -qx 'nvidia.com/gpu=all' <<<"$devs" || die "no nvidia.com/gpu=all in the CDI spec - systemctl restart nvidia-cdi-refresh"
    slice="leave it unset: MIG is off, and without the label the Fleet hands the container the whole GPU"
else
    uuid=$(nvidia-smi -L | sed -n 's/.*MIG .*Device  *1: (UUID: \(MIG-[0-9a-f-]*\)).*/\1/p')
    [[ -n $uuid ]]                             || die "could not read slice 0:1's UUID from nvidia-smi -L"
    grep -qx "nvidia.com/gpu=$uuid" <<<"$devs" || die "slice 0:1 ($uuid) is not in the CDI spec - systemctl restart nvidia-cdi-refresh"
    slice="$uuid (slice 0:1)"
    warn "MIG is on: no sim and no Zenoh router in this mode, the policy will not turn healthy. For the Phase 4 exit: fury-mode flywheel"
fi

# --no-block: the unit is Type=notify and an agent waiting for approval may not have reported ready yet
systemctl enable flightctl-agent.service
systemctl start --no-block flightctl-agent.service
name=
for _ in {1..15}; do
    name=$(journalctl -u flightctl-agent --no-pager -o cat | sed -n 's/.*Bootstrapping device: *\([a-z0-9]*\).*/\1/p' | tail -1)
    [[ -z $name ]] || break
    sleep 2
done
echo "flightctl-agent: $(systemctl is-active flightctl-agent.service || true)    MIG mode: $mode"

if [[ -s $cert ]]; then
    echo "already enrolled as ${name:-(name not in the journal)}. From the laptop:  flightctl get devices -o wide"
    exit 0
fi
[[ -n $name ]] || name="(not in the journal yet:  sudo journalctl -u flightctl-agent | grep Bootstrapping)"
cat <<EOF

next, from the laptop - 41-device-enroll.md, step 4 onwards:
  1. flightctl get enrollmentrequests
       expect exactly one Pending request, named
       $name
  2. approve that request with these labels (step 4 has the full command):
       fleet=act-inference site=fury gpu=nvidia arch=arm64 policy_device=cuda
       zenoh_router=10.20.0.1 zenoh_port=7447 alias=fury-host pull_default=$pull_default
       gpu_device - $slice
  3. back here, watch the Fleet land:  sudo journalctl -fu flightctl-agent
     then  sudo podman ps  should show act-inference-128875-act-inference, healthy after a few minutes
from then on /etc/containers/policy.json is the Fleet's. With the pull_default label this host keeps pulling
from anywhere, and the two quay.io/jary repositories are signature- and Rekor-enforced. Without the label the
default is reject, and a pull from anywhere else needs  --signature-policy $keep
EOF
