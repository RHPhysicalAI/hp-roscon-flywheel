#!/bin/bash
# Install act 2's coding assistant as a host service: flywheel/llm-assistant.container with its cache volume,
# and the fury-mode that starts it in tenants mode and stops it for every switch.
#
# Installs only. Nothing starts here, and with MIG off the unit would skip its own start anyway
# (ExecCondition=). It comes up with:  fury-mode tenants
#
# If the smoke test (61-rhaiis-smoke.sh) left a cache volume behind, its compiled kernels are copied into the
# service's volume once, so the first governed start does not compile them again.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

units=/etc/containers/systemd
unit=$here/flywheel/llm-assistant.container
vol=$here/flywheel/llm-cache.volume
model=/data/models/RedHatAI/Qwen3-Coder-Next-NVFP4
die() { echo "${0##*/}: $*" >&2; exit 1; }

for f in "$unit" "$vol" "$here/fury-mode.sh"; do [[ -f $f ]] || die "missing ${f#"$here"/} next to this script"; done
[[ -f $model/config.json ]] || die "no model at $model - ./60-model-fetch.sh first"
img=$(sed -n 's/^Image=//p' "$unit" | head -1)
podman image exists "$img" || die "the image is not in root's storage. Once, with the uplink:  sudo podman pull $img"
# the server runs as another uid inside: the model has to be readable by others out here
runuser -u nobody -- test -r "$model/config.json" || die "$model is not readable by other users:  chmod -R o+rX $model"

# let quadlet check the two files, alone, before anything is installed
qd=$(mktemp -d); trap 'rm -f "$qd"/llm-*; rmdir "$qd"' EXIT
cp "$unit" "$vol" "$qd/"
gen=$(QUADLET_UNIT_DIRS=$qd /usr/libexec/podman/quadlet -dryrun 2>&1) || die "quadlet rejected the unit:
$gen"
grep -E '^(ExecStart|ExecCondition)=' <<<"$gen" | cut -c1-200 | sed 's/^/quadlet: /' || true

install -m 0644 "$unit" "$vol" "$units/"
install -m 0755 "$here/fury-mode.sh" /usr/local/sbin/fury-mode
restorecon -R "$units" /usr/local/sbin/fury-mode
systemctl daemon-reload

# one-time: what the smoke test compiled
if podman volume exists rhaiis-smoke-cache && ! podman volume exists llm-cache; then
    podman volume create llm-cache >/dev/null
    podman volume export rhaiis-smoke-cache | podman volume import llm-cache -
    echo "copied the smoke test's compiled kernels into volume llm-cache ($(du -sh "$(podman volume inspect --format '{{.Mountpoint}}' llm-cache)" | cut -f1))"
fi

systemctl list-unit-files 'llm-assistant*' --no-pager || true
echo
echo "installed; nothing was started. The assistant comes up with:  fury-mode tenants"
echo "(through tools/hub/fury-switch.sh from a laptop, which takes the RHEM-managed policy out of service first)"
echo "watch:  sudo journalctl -fu llm-assistant     try it:  ./61-rhaiis-smoke.sh ask     (same port, loopback)"
