#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Enroll a provisioned device into RHEM and approve it with the act-inference labels (D024).
# Runs where the flightctl CLI is logged in (the presenting laptop or desktop); reaches the device over ssh.
#
# Usage:
#   device/enroll.sh <ssh-target> [--reset]
#     <ssh-target>  e.g. jary@10.0.0.50 (desktop VM) or jary@<fury-ip>; needs passwordless sudo
#     --reset       wipe /var/lib/flightctl on the device first (re-enrollment)
#
# Labels (env, defaults are the desktop VM; the Fury run overrides them):
#   FLEET=act-inference SITE=desktop GPU=none POLICY_DEVICE=cpu ARCH=<from uname -m>
#   ZENOH_ROUTER=10.0.0.48 ZENOH_PORT=7447 EXTRA_LABELS="k=v,k2=v2"
# ':' is not a legal label-value character (Kubernetes IsValidLabelValue, which flightctl
# 1.3.0 applies server-side), so the router host and port are two labels.
#   Fury:  SITE=fury GPU=nvidia POLICY_DEVICE=cuda ZENOH_ROUTER=<ip> device/enroll.sh jary@<fury>
set -euo pipefail

TARGET="${1:-}"
RESET="false"
[[ "${2:-}" == "--reset" ]] && RESET="true"
[[ -n "$TARGET" ]] || { sed -n '4,17p' "$0"; exit 1; }

DEVICE_NAME="${DEVICE_NAME:-act-device}"        # name of the enrollment certificate request
FLEET="${FLEET:-act-inference}"
SITE="${SITE:-desktop}"
GPU="${GPU:-none}"
POLICY_DEVICE="${POLICY_DEVICE:-cpu}"
ZENOH_ROUTER="${ZENOH_ROUTER:-10.0.0.48}"
ZENOH_PORT="${ZENOH_PORT:-7447}"
EXTRA_LABELS="${EXTRA_LABELS:-}"
REPLACE_LABELS="${REPLACE_LABELS:-false}"
ENROLL_TIMEOUT="${ENROLL_TIMEOUT:-300}"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)

log() { printf '[enroll] %s\n' "$*"; }
die() { printf '[enroll] ERROR: %s\n' "$*" >&2; exit 1; }
remote() { ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }

command -v flightctl >/dev/null 2>&1 || die "flightctl CLI not on PATH (install 1.3.0 into ~/.local/bin)"
command -v jq >/dev/null 2>&1 || die "jq not on PATH"
flightctl get devices --limit 1 >/dev/null 2>&1 \
  || die "flightctl is not logged in. Run: flightctl login https://api.<rhem-host> --token \$(oc whoami -t) --insecure-skip-tls-verify"
remote true >/dev/null 2>&1 || die "cannot ssh to ${TARGET}"
remote sudo -n true >/dev/null 2>&1 || die "${TARGET} needs passwordless sudo"

MACHINE="$(remote uname -m)"
case "$MACHINE" in
  x86_64) ARCH_DEFAULT="amd64" ;;
  aarch64) ARCH_DEFAULT="arm64" ;;
  *) ARCH_DEFAULT="$MACHINE" ;;
esac
ARCH="${ARCH:-$ARCH_DEFAULT}"

# Kubernetes label value rule: <=63 chars, alnum at both ends, [-A-Za-z0-9_.] inside.
LABEL_VALUE_RE='^([A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?)?$'
LABELS=(
  "fleet=${FLEET}" "site=${SITE}" "gpu=${GPU}" "policy_device=${POLICY_DEVICE}"
  "arch=${ARCH}" "zenoh_router=${ZENOH_ROUTER}" "zenoh_port=${ZENOH_PORT}"
)
if [[ -n "$EXTRA_LABELS" ]]; then
  IFS=',' read -r -a extra <<<"$EXTRA_LABELS"
  LABELS+=("${extra[@]}")
fi
LABEL_ARGS=()
for kv in "${LABELS[@]}"; do
  key="${kv%%=*}"; val="${kv#*=}"
  [[ -n "$key" && "$kv" == *=* ]] || die "bad label '${kv}' (want key=value)"
  [[ "$val" =~ $LABEL_VALUE_RE && ${#val} -le 63 ]] || die "label ${key}='${val}' violates the label-value rule (no ':' or '/', alnum at both ends)"
  LABEL_ARGS+=(-l "$kv")
done
[[ "$REPLACE_LABELS" == "true" ]] && LABEL_ARGS+=(--replace-labels)

pending_names() { flightctl get enrollmentrequests -o json | jq -r '.items[] | select((.status.approval // null) == null) | .metadata.name'; }
BEFORE="$(pending_names || true)"

# ---- 1. Enrollment certificate -> /etc/flightctl/config.yaml on the device -------------
WORK="$(mktemp -d)"
trap 'find "$WORK" -mindepth 1 -delete 2>/dev/null; rmdir "$WORK" 2>/dev/null || true' EXIT
log "requesting enrollment certificate '${DEVICE_NAME}'"
flightctl certificate request --name "$DEVICE_NAME" --signer flightctl.io/enrollment \
  --output embedded --output-dir "$WORK" > "$WORK/agent-config.yaml"
grep -q "enrollment-service" "$WORK/agent-config.yaml" || die "agent config does not look like an embedded enrollment config: $WORK/agent-config.yaml"

log "installing agent config on ${TARGET}"
scp "${SSH_OPTS[@]}" -q "$WORK/agent-config.yaml" "${TARGET}:/tmp/flightctl-config.yaml"
if [[ "$RESET" == "true" ]]; then
  log "resetting agent state on the device"
  remote 'sudo systemctl stop flightctl-agent; sudo find /var/lib/flightctl -mindepth 1 -delete 2>/dev/null || true'
fi
remote 'sudo install -D -m 0600 -o root -g root /tmp/flightctl-config.yaml /etc/flightctl/config.yaml && rm -f /tmp/flightctl-config.yaml && sudo systemctl enable --now flightctl-agent && systemctl is-active flightctl-agent'

# ---- 2. Wait for the enrollment request ------------------------------------------------
log "waiting up to ${ENROLL_TIMEOUT}s for the enrollment request"
ER=""
deadline=$(( $(date +%s) + ENROLL_TIMEOUT ))
while [[ $(date +%s) -lt $deadline ]]; do
  NOW="$(pending_names || true)"
  NEW="$(comm -13 <(printf '%s\n' "$BEFORE" | sort) <(printf '%s\n' "$NOW" | sort) | sed '/^$/d' || true)"
  if [[ $(printf '%s\n' "$NEW" | sed '/^$/d' | wc -l) -eq 1 ]]; then ER="$NEW"; break; fi
  if [[ -z "$BEFORE" && $(printf '%s\n' "$NOW" | sed '/^$/d' | wc -l) -eq 1 ]]; then ER="$NOW"; break; fi
  sleep 5
done
[[ -n "$ER" ]] || die "no new pending enrollment request appeared. Check: ssh ${TARGET} sudo journalctl -u flightctl-agent -n 50; flightctl get enrollmentrequests"
log "enrollment request: ${ER}"

# ---- 3. Approve with labels -------------------------------------------------------------
log "flightctl approve ${LABEL_ARGS[*]} enrollmentrequest/${ER}"
flightctl approve "${LABEL_ARGS[@]}" "enrollmentrequest/${ER}"

log "waiting for device/${ER}"
for _ in $(seq 1 24); do
  if flightctl get "device/${ER}" -o json >/tmp/enroll-device.$$ 2>/dev/null; then
    jq -r '"device \(.metadata.name)  labels: \(.metadata.labels | to_entries | map("\(.key)=\(.value)") | join(","))  status: \(.status.summary.status // "unknown")"' /tmp/enroll-device.$$
    rm -f /tmp/enroll-device.$$
    log "done. UI: Devices -> ${ER}; CLI: flightctl get device/${ER} -o yaml"
    exit 0
  fi
  sleep 5
done
die "device/${ER} not visible after approval; check flightctl get devices"
