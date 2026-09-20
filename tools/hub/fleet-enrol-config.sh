#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Make the fleet VMs' enrolment config on this laptop and hand it to the host. One-time bring-up, and again when
# the certificate has expired (tools/host/fury/81-fleet-scale.sh says so when it has). Same steps as the host's
# own enrolment (tools/host/fury/41-device-enroll.md, section 2), wrapped: the config carries the enrolment client
# key and is never displayed.
#
#   tools/hub/fleet-enrol-config.sh [days]      validity, default 14, 1..365
#
# One certificate for the whole fleet, short-lived on purpose. What it allows: asking this hub to enrol a device.
# It does not make a device - every request still waits for an approval (tools/hub/fleet-approve.sh).
#
# RUN THIS AND THE HOST STEP BACK TO BACK. The file lands in the host's login account, and that account is shared:
# its mode keeps other ACCOUNTS out, not the other people who log in as the same one. Until
# `81-fleet-scale.sh enrol-config` has moved it under /root and shredded it, it is a live credential for the
# whole fleet lying in a shared home. If the host step slips, do not leave it there: shred it on the host and run
# this again later - a new certificate costs nothing. NEVER copy it into a git checkout.
#
# Local copies, one story: every file the command writes sits in a 0700 temp directory and is removed by name on
# the way out - overwritten first where `shred` exists (Linux), plainly unlinked on a Mac, which has no shred and
# where overwriting in place means nothing anyway (APFS is copy-on-write, on flash). On a Mac the protection is
# the directory's mode, the seconds the file exists, and FileVault.
#
#   FURY_SSH     ssh target of the host, user@host                       (required)
#   FLIGHTCTL    flightctl binary, logged in to this hub                  (default: flightctl, then ~/.local/bin/flightctl)
set -euo pipefail

die() { echo "${0##*/}: $*" >&2; exit 1; }
days=${1:-14}
if ! [[ $days =~ ^[0-9]{1,3}$ ]] || (( 10#$days < 1 || 10#$days > 365 )); then die "usage: ${0##*/} [days 1..365]"; fi
: "${FURY_SSH:?set FURY_SSH=user@host (the ssh target of the machine)}"
fc=${FLIGHTCTL:-$(command -v flightctl || echo "$HOME/.local/bin/flightctl")}
[[ -x $fc ]] || die "no flightctl at $fc"
"$fc" get fleets >/dev/null 2>&1 ||
    die "flightctl is not logged in to the hub. Log in (oc login as a real user, then: flightctl login <api url> --token \"\$(oc whoami -t)\") and run this again"

d=$(mktemp -d); chmod 700 "$d"
wipe() { local f; for f in "$@"; do [[ -f $f ]] || continue; if command -v shred >/dev/null; then shred -u "$f"; else rm -f "$f"; fi; done; }
# every file the command can leave behind, by name - no recursive delete
trap 'wipe "$d/agent-config.yaml" "$d/fleet-vm.key" "$d/fleet-vm.crt" "$d/ca.crt"; rmdir "$d" 2>/dev/null || echo "${0##*/}: look at what is left in $d and delete it by hand" >&2' EXIT
( umask 077
  "$fc" certificate request --signer=flightctl.io/enrollment --expiration="${days}d" --output=embedded \
        --name fleet-vm --output-dir "$d" > "$d/agent-config.yaml" )
[[ $(grep -c client-key-data "$d/agent-config.yaml") == 1 ]] || die "flightctl did not return an embedded enrolment config"

# Created 0600 from its first byte (umask, then cat): scp -p would create it at the remote umask and fix the mode
# afterwards. The local copy is gone when this script ends either way, so a failure here means: run it again.
# shellcheck disable=SC2029  # nothing in the remote command comes from a variable
ssh "$FURY_SSH" 'umask 077; mkdir -p flywheel-setup && cat > flywheel-setup/fleet-agent-config.yaml' < "$d/agent-config.yaml" ||
    die "could not write flywheel-setup/fleet-agent-config.yaml on $FURY_SSH - check the ssh target and that the home directory is writable, then run this again (it makes a fresh certificate; nothing was kept here)"
# shellcheck disable=SC2029
landed=$(ssh "$FURY_SSH" 'stat -c "%a %U:%G  %n" flywheel-setup/fleet-agent-config.yaml; stat -c "%a %U:%G  %n" flywheel-setup') ||
    die "the file was written but could not be checked on $FURY_SSH. On the host, look at it, then either go on to the next step or shred it:  shred -u ~/flywheel-setup/fleet-agent-config.yaml"
printf '    %s\n' "${landed//$'\n'/$'\n'    }"
[[ $landed == 600\ * ]] || die "the file did not land with mode 600 (above). On the host:  shred -u ~/flywheel-setup/fleet-agent-config.yaml   - then find out why and run this again"

cat <<EOF
the enrolment config is on the host as ~/flywheel-setup/fleet-agent-config.yaml (valid $days days), mode as shown.
It is a live credential for the whole fleet in a SHARED login account. NOW, on the host - it installs the file
root-only and shreds this copy:
    cd ~/flywheel-setup && ./81-fleet-scale.sh enrol-config fleet-agent-config.yaml
Not doing that right now? Then on the host:  shred -u ~/flywheel-setup/fleet-agent-config.yaml   and run this script again later.
EOF
