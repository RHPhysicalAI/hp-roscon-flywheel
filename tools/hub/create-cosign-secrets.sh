#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
# Creates the hand-managed Secrets `cosign-signing` (Tekton cosign-sign Task) and `cosign-signing-key`
# (KFP sign_modelcar) from one key pair. Generates the pair in COSIGN_DIR (default ~/cosign) when there
# is none; an existing pair is reused, never overwritten. The passphrase is asked for on the terminal
# and has to open the key before anything is created. Never prints it. KUBECONFIG must be set.
# Use the cosign release the pipeline signs with:  COSIGN=/path/to/cosign-v2.6.5 ./create-cosign-secrets.sh
set -euo pipefail
NS=flywheel
DIR=${COSIGN_DIR:-$HOME/cosign}
COSIGN=${COSIGN:-cosign}
for s in cosign-signing cosign-signing-key; do
  if oc get secret "$s" -n "$NS" >/dev/null 2>&1 && [ "${FORCE:-0}" != "1" ]; then
    echo "secret $NS/$s exists; FORCE=1 to replace it"; exit 0
  fi
done
mkdir -p "$DIR"; chmod 700 "$DIR"; cd "$DIR"
read -rs -p "cosign key passphrase: " PW; echo
if [ ! -f cosign.key ]; then
  read -rs -p "once more: " PW2; echo
  [ "$PW" = "$PW2" ] || { echo "the two entries differ; nothing was created" >&2; exit 1; }
  COSIGN_PASSWORD=$PW "$COSIGN" generate-key-pair
fi
# a wrong passphrase in the Secret only shows up later, as a failed signing step
derived=$(COSIGN_PASSWORD=$PW "$COSIGN" public-key --key cosign.key 2>/dev/null) || derived=
[ -n "$derived" ] && [ "$derived" = "$(cat cosign.pub)" ] ||
  { echo "that passphrase does not open $DIR/cosign.key, or cosign.pub is not its public half" >&2; exit 1; }
for s in cosign-signing cosign-signing-key; do
  printf %s "$PW" | oc create secret generic "$s" -n "$NS" --from-file=cosign.key --from-file=cosign.pub \
    --from-file=cosign.password=/dev/stdin --dry-run=client -o yaml | oc apply -f -
done
echo "public key for the Fleet's inline cosign.pub: $DIR/cosign.pub"
