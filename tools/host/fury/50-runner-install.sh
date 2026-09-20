#!/bin/bash
# Install the host half of a governed training run as units: the runner as a root quadlet from the runtime
# image (flywheel/flywheel-runner.container), its script outside the image so that a fix is this script and
# a restart, and - when they are here - the eval rig (51-eval-rig.sh) with its path and service units.
#
#   ./50-runner-install.sh                     the checkout is ~<sudo user>/hp-roscon-flywheel
#   sudo SRC=/path/to/checkout ./50-runner-install.sh
#
# Installs only: nothing is started. The first run writes the two MinIO keys to /etc/flywheel-runner/env
# (root, 0600). It takes them from the hub itself - Secret flywheel/hub-credentials, the one the cluster's own
# consumers of this MinIO use, read with root's kubeconfig for the hub - and only asks on the terminal when
# the hub cannot be asked. Either way they are never echoed and never reach ./log/, and an existing file is
# left alone: to change a key, edit that file as root and restart the runner.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

die() { echo "${0##*/}: $*" >&2; exit 1; }
src=${SRC:-$(getent passwd "${SUDO_USER:?run this through sudo, or set SRC}" | cut -d: -f6)/hp-roscon-flywheel}
runner=$src/src/host-runner/host_runner.py
quadlet=$here/flywheel/flywheel-runner.container
envf=/etc/flywheel-runner/env
units=/etc/containers/systemd

qd=; tmp=
# the last two commands: under sudo the terminal can go away before tee has written the final lines
trap '[[ -z $qd ]] || { rm -f "$qd/flywheel-runner.container"; rmdir "$qd"; }; [[ -z $tmp ]] || rm -f "$tmp"; exec >&- 2>&-; wait' EXIT

[[ -f $quadlet ]]                   || die "missing flywheel/flywheel-runner.container next to this script"
[[ -f $runner ]]                    || die "no runner at $runner - set SRC to the checkout"
grep -q '^RUNNER_MODE = ' "$runner" || die "$runner predates the in-process runner (no RUNNER_MODE) - update the checkout at $src"
[[ -d /data/flywheel ]]             || die "no /data/flywheel - ./02-data-disk.sh and ./14-flywheel-services.sh first"
[[ -x /usr/libexec/podman/quadlet ]] || die "no /usr/libexec/podman/quadlet - ./03-packages.sh first"
if command -v python3 >/dev/null; then
    # parse only: compiling would leave a root-owned __pycache__ in the checkout
    python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$runner" || die "$runner does not parse - nothing installed"
fi

# let quadlet check the container file, alone, before anything is installed
qd=$(mktemp -d)
cp "$quadlet" "$qd/"
gen=$(QUADLET_UNIT_DIRS=$qd /usr/libexec/podman/quadlet -dryrun 2>&1) || die "quadlet rejected flywheel-runner.container:
$gen"
# worth a look: the $$ in Exec= has to arrive here still doubled - systemd makes it the container's single $
grep '^ExecStart=' <<<"$gen" | sed 's/^/quadlet: /' || true

# ---- the two MinIO keys, before anything is installed: a unit without them would only restart in a loop.
# Asked for on the terminal itself and written straight to the file: the tee above only sees this script's
# stdout and stderr, and nothing below puts a value on either.
# one source of truth: the Secret the hub's own pods read. Nothing is printed, not even on failure.
from_hub() {
    local kc=/root/fury-sno.kubeconfig oc=
    for oc in /usr/local/bin/oc /root/sno-install/bin/oc; do [[ -x $oc ]] && break; done
    [[ -x $oc && -s $kc ]] || return 1
    ak=$("$oc" --kubeconfig "$kc" --request-timeout=20s -n flywheel get secret hub-credentials -o jsonpath='{.data.s3-access-key}' 2>/dev/null | base64 -d 2>/dev/null) || return 1
    sk=$("$oc" --kubeconfig "$kc" --request-timeout=20s -n flywheel get secret hub-credentials -o jsonpath='{.data.s3-secret-key}' 2>/dev/null | base64 -d 2>/dev/null) || return 1
    [[ -n $ak && -n $sk ]]
}

ak='' sk=''
if [[ -s $envf ]]; then
    echo "$envf exists - left alone"
else
    if from_hub; then
        echo "took the two MinIO keys from Secret flywheel/hub-credentials on the hub (not shown)"
    else
        [[ -t 0 ]] || die "the hub's Secret could not be read and stdin is not a terminal: run this from a terminal, it then asks for the two MinIO keys"
        echo "could not read Secret flywheel/hub-credentials with root's kubeconfig - asking instead"
        sleep 0.3       # the prompts bypass the tee: let it print what is still on its way first
        printf 'MinIO access key (not shown): ' > /dev/tty
        IFS= read -rs ak; echo > /dev/tty
        printf 'MinIO secret key (not shown): ' > /dev/tty
        IFS= read -rs sk; echo > /dev/tty
    fi
    [[ -n $ak && -n $sk ]]            || die "an empty key - nothing written, run this again"
    [[ $ak$sk != *[[:space:]]* ]]     || die "whitespace in a key - nothing written, run this again"
    install -d -m 0700 -o root -g root /etc/flywheel-runner
    tmp=$(umask 077; mktemp /etc/flywheel-runner/.env.XXXXXX)
    # podman's --env-file format: NAME=value, the value taken literally - no quotes
    printf 'MINIO_ACCESS_KEY=%s\nMINIO_SECRET_KEY=%s\n' "$ak" "$sk" > "$tmp"
    unset ak sk
    chmod 0600 "$tmp"
    mv -f "$tmp" "$envf"; tmp=
    restorecon -R /etc/flywheel-runner
    echo "wrote $envf (root, 0600)"
fi
[[ $(stat -c '%U %a' "$envf") == 'root 600' ]] || die "$envf is not root 0600:  sudo chown root:root $envf && sudo chmod 0600 $envf"

install -d -m 0755 /usr/local/lib/flywheel
install -m 0644 -o root -g root "$runner"  /usr/local/lib/flywheel/host_runner.py
install -m 0644 -o root -g root "$quadlet" "$units/"
restorecon -R /usr/local/lib/flywheel "$units"

# the eval rig is its own piece of work: all three files, or none of it
rig=$here/51-eval-rig.sh
eval_units=()
for u in flywheel-eval.path flywheel-eval.service; do
    for f in "$here/flywheel/$u" "$here/$u"; do
        if [[ -f $f ]]; then eval_units+=("$f"); break; fi
    done
done
eval_installed=no
if [[ -f $rig && ${#eval_units[@]} -eq 2 ]]; then
    install -m 0755 -o root -g root "$rig" /usr/local/sbin/flywheel-eval-rig
    install -m 0644 -o root -g root "${eval_units[@]}" /etc/systemd/system/
    restorecon /usr/local/sbin/flywheel-eval-rig /etc/systemd/system/flywheel-eval.path /etc/systemd/system/flywheel-eval.service
    eval_installed=yes
else
    echo "eval rig skipped: 51-eval-rig.sh, flywheel-eval.path and flywheel-eval.service are not all here yet."
    echo "  Without it the runner trains, then waits EVAL_TIMEOUT_S for an eval nobody runs. Run this again once they exist."
fi

mkdir -p /data/flywheel/train /data/flywheel/datasets /data/flywheel/eval/requests

systemctl daemon-reload
systemctl list-unit-files 'flywheel-runner*' 'flywheel-eval*' --no-pager || true

img=$(sed -n 's/^Image=//p' "$quadlet" | head -1)
if [[ $(systemctl is-active flywheel-runner.service 2>/dev/null || true) == active ]]; then
    echo "flywheel-runner is running the previous install. Between runs:  sudo systemctl restart flywheel-runner.service"
fi
# not `systemctl enable flywheel-runner.service`: a quadlet unit is generated and enable refuses it. Its
# [Install] section (WantedBy=fury-flywheel.target) was applied by the daemon-reload above.
echo
echo "nothing was started. next:"
podman image exists "$img" || echo "  sudo podman pull $img      (the unit has Pull=never and the image is not in root's storage)"
if [[ $eval_installed == yes ]]; then
    echo "  sudo systemctl enable --now flywheel-eval.path"
fi
echo "  sudo systemctl start flywheel-runner.service      (from now on it starts and stops with fury-flywheel.target, i.e. with fury-mode)"
echo "watch:"
echo "  sudo journalctl -fu flywheel-runner"
