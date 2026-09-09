#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Provision a RHEL 10 host as an RHEM (flightctl) managed device for act-inference (D024).
# Arch-neutral: runs unchanged on the desktop stand-in VM (x86_64) and the Fury host (aarch64).
# Idempotent: safe to re-run. Trust files (policy.json, registries.d, public keys) are NOT
# written here -- the Fleet delivers them.
#
# Usage (as root):
#   ./provision.sh [--gpu|--no-gpu] [--env-file /root/activation-key]
# Registration input, first match wins (values are never printed):
#   --env-file <path>, else /root/activation-key if present: shell lines ORG_ID=... ACTIVATION_KEY=...
#   env ORG_ID + ACTIVATION_KEY   (activation key, preferred)
#   env RHSM_USER + RHSM_PASS     (username/password alternative)
# Optional: RHEM_HUB_IP=<ip> writes /etc/hosts entries for api./agent-api./ui.$RHEM_HUB_DOMAIN.
# GPU auto-detects on nvidia-smi when neither flag is given.
# Bags (D043): if the hypervisor exposes a virtiofs tag $BAGS_TAG (desktop VM: the host's bag dir), it is
# mounted at /var/lib/act-inference/bags via fstab; otherwise (Fury) that directory is local disk.
set -euo pipefail

FLIGHTCTL_AGENT_VERSION="1.3.0-1.el10"
FLIGHTCTL_REPO_URL="https://rpm.flightctl.io/flightctl-epel10.repo"
PODMAN_MIN="5.5"                      # image volumes need >= 5.5
NVIDIA_CTK_VERSION="1.20.0"           # stable repo, x86_64 + aarch64, verified 2026-09-08
NVIDIA_CTK_REPO_URL="https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo"
STATE_DIR="/var/lib/act-inference"
BAGS_TAG="${BAGS_TAG:-bags}"          # virtiofs mount tag from device/vm/create-vm.sh (D043)
RHEM_HUB_DOMAIN="${RHEM_HUB_DOMAIN:-flightctl.apps.sno-flywheel.local}"
RHEM_HUB_IP="${RHEM_HUB_IP:-}"

ARCH="$(uname -m)"
GPU_MODE="auto"
ENV_FILE=""

log() { printf '[provision] %s\n' "$*"; }
die() { printf '[provision] ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu) GPU_MODE="on" ;;
    --no-gpu) GPU_MODE="off" ;;
    --env-file) ENV_FILE="${2:-}"; [[ -n "$ENV_FILE" ]] || die "--env-file needs a path"; shift ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

[[ "$(id -u)" -eq 0 ]] || die "run as root (sudo -E $0 ...)"
[[ -r /etc/os-release ]] || die "/etc/os-release missing; expected RHEL 10"
# shellcheck disable=SC1091
. /etc/os-release
log "host: ${PRETTY_NAME:-unknown} arch=${ARCH}"
[[ "${VERSION_ID:-}" == 10* ]] || log "WARNING: expected RHEL 10, got VERSION_ID=${VERSION_ID:-?}"

version_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]; }

# ---- 1. Subscription --------------------------------------------------------
# Credentials are sourced, never echoed; registration output is redacted of identifiers.
[[ -z "$ENV_FILE" && -r /root/activation-key ]] && ENV_FILE="/root/activation-key"
if [[ -n "$ENV_FILE" ]]; then
  [[ -r "$ENV_FILE" ]] || die "cannot read env file ${ENV_FILE}"
  log "loading registration input from ${ENV_FILE}"
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi
redact() { sed -E 's/[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}/<redacted>/g; s/(org|Org|ORG)[^:]*: .*/\1: <redacted>/'; }
if ! command -v subscription-manager >/dev/null 2>&1; then
  log "subscription-manager not present; skipping registration (non-RHEL host?)"
elif subscription-manager identity >/dev/null 2>&1; then
  log "already registered"
elif [[ -n "${ORG_ID:-}" && -n "${ACTIVATION_KEY:-}" ]]; then
  log "registering with activation key"
  subscription-manager register --org "$ORG_ID" --activationkey "$ACTIVATION_KEY" | redact
elif [[ -n "${RHSM_USER:-}" && -n "${RHSM_PASS:-}" ]]; then
  log "registering with username/password"
  subscription-manager register --username "$RHSM_USER" --password "$RHSM_PASS" | redact
else
  die "not registered and no credentials found. Provide --env-file <path> (or /root/activation-key) with ORG_ID=... and ACTIVATION_KEY=..., or export ORG_ID + ACTIVATION_KEY, or RHSM_USER + RHSM_PASS, then re-run."
fi
if command -v subscription-manager >/dev/null 2>&1; then
  subscription-manager status 2>/dev/null | redact | sed 's/^/[provision]   /' || true
fi

# ---- 2. podman ----------------------------------------------------------------
log "installing podman + skopeo"
# flightctl-agent 1.3.0 shells out to skopeo to inspect application images before pulling; without
# it the first spec fails at prefetch ("required commands not found: skopeo") — seen 2026-09-08.
dnf -y install podman skopeo >/dev/null
PODMAN_VER="$(podman --version | awk '{print $3}')"
version_ge "$PODMAN_VER" "$PODMAN_MIN" \
  || die "podman ${PODMAN_VER} < ${PODMAN_MIN}; image volumes (Fleet application volumes) need >= ${PODMAN_MIN}. Enable a newer AppStream stream or update the OS before continuing."
log "podman ${PODMAN_VER} OK (>= ${PODMAN_MIN})"

# ---- 3. flightctl agent (package mode, pinned) --------------------------------
log "installing flightctl repo -> /etc/yum.repos.d/flightctl.repo"
curl -fsSL -o /etc/yum.repos.d/flightctl.repo "$FLIGHTCTL_REPO_URL"
log "installing flightctl-agent-${FLIGHTCTL_AGENT_VERSION} (weak deps off: no greenboot)"
dnf -y install --setopt=install_weak_deps=False "flightctl-agent-${FLIGHTCTL_AGENT_VERSION}" >/dev/null
INSTALLED="$(rpm -q --qf '%{VERSION}-%{RELEASE}' flightctl-agent)"
[[ "$INSTALLED" == "$FLIGHTCTL_AGENT_VERSION" ]] || die "flightctl-agent is ${INSTALLED}, expected ${FLIGHTCTL_AGENT_VERSION}"
if rpm -q greenboot flightctl-greenboot >/dev/null 2>&1; then
  log "WARNING: greenboot packages present; package-mode devices do not need them (thor D023)"
fi
systemctl enable flightctl-agent.service >/dev/null
log "flightctl-agent ${INSTALLED} enabled (not started: enrollment config comes first via enroll.sh)"

# ---- 4. Runtime state dirs + hub name resolution --------------------------------
mkdir -p "${STATE_DIR}/data" "${STATE_DIR}/bags"
log "state dirs ready under ${STATE_DIR}"
# D043: the bags dir is the host's directory over virtiofs when the hypervisor offers the tag (desktop
# VM), else local disk (Fury). context= labels the whole mount for the container, so the quadlet mounts
# it without :z (virtiofs without xattr support cannot take a relabel); the local case is labelled here.
BAGS_MOUNT="${STATE_DIR}/bags"
BAGS_SOURCE="local"
modprobe virtiofs 2>/dev/null || true
if grep -qsx "$BAGS_TAG" /sys/fs/virtiofs/*/tag 2>/dev/null; then
  BAGS_SOURCE="virtiofs:${BAGS_TAG}"
  if ! grep -qE "^${BAGS_TAG}[[:space:]]+${BAGS_MOUNT}[[:space:]]+virtiofs" /etc/fstab; then
    printf '%s %s virtiofs defaults,nofail,context=system_u:object_r:container_file_t:s0 0 0\n' "$BAGS_TAG" "$BAGS_MOUNT" >> /etc/fstab
    systemctl daemon-reload
  fi
  mountpoint -q "$BAGS_MOUNT" || mount "$BAGS_MOUNT"
  log "bags: virtiofs tag '${BAGS_TAG}' mounted at ${BAGS_MOUNT}"
else
  chcon -R -t container_file_t "$BAGS_MOUNT" 2>/dev/null || true
fi
if [[ -n "$RHEM_HUB_IP" ]]; then
  # Same failure mode as the Rekor UI (D006): the SNO routes are not in DNS.
  sed -i '/# rhem-hub$/d' /etc/hosts
  for h in api agent-api ui; do
    printf '%s %s.%s # rhem-hub\n' "$RHEM_HUB_IP" "$h" "$RHEM_HUB_DOMAIN" >> /etc/hosts
  done
  log "/etc/hosts: api./agent-api./ui.${RHEM_HUB_DOMAIN} -> ${RHEM_HUB_IP}"
fi

# ---- 5. GPU (Fury only) ---------------------------------------------------------
if [[ "$GPU_MODE" == "auto" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1; then GPU_MODE="on"; else GPU_MODE="off"; fi
  log "GPU auto-detect: ${GPU_MODE}"
fi
if [[ "$GPU_MODE" == "on" ]]; then
  nvidia-smi >/dev/null 2>&1 || die "--gpu requested but nvidia-smi fails; install the NVIDIA driver first, then re-run"
  log "installing nvidia-container-toolkit-${NVIDIA_CTK_VERSION} (repo: ${NVIDIA_CTK_REPO_URL})"
  curl -fsSL -o /etc/yum.repos.d/nvidia-container-toolkit.repo "$NVIDIA_CTK_REPO_URL"
  dnf -y install "nvidia-container-toolkit-${NVIDIA_CTK_VERSION}" >/dev/null
  mkdir -p /etc/cdi
  nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
  log "CDI devices: $(nvidia-ctk cdi list | tr '\n' ' ')"
fi

# ---- Summary --------------------------------------------------------------------
cat <<SUMMARY
[provision] done
  arch            ${ARCH}
  podman          ${PODMAN_VER}
  flightctl-agent ${INSTALLED} (enabled, stopped)
  gpu             ${GPU_MODE}
  bags            ${BAGS_SOURCE} at ${BAGS_MOUNT}
  next            run device/enroll.sh from a machine with the flightctl CLI logged in
SUMMARY
