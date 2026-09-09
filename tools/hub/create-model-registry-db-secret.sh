#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
# Creates the hand-managed Secret `model-registry-db` (Phase 4.5 E1) with a locally generated
# password. Run once on the desktop (KUBECONFIG set); idempotent - re-running keeps the existing
# Secret unless FORCE=1. Never prints the value.
set -euo pipefail
NS=rhoai-model-registries
NAME=model-registry-db
if oc get secret "$NAME" -n "$NS" >/dev/null 2>&1 && [ "${FORCE:-0}" != "1" ]; then
  echo "secret $NS/$NAME exists; FORCE=1 to regenerate (the DB volume keeps the old password)"; exit 0
fi
PW=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)
oc create secret generic "$NAME" -n "$NS" --from-literal=database-password="$PW" \
  --dry-run=client -o yaml | oc apply -f -
echo "secret $NS/$NAME created"
