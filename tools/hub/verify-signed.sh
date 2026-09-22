#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Verify a signed image the way every device does: the pipeline's signing key, and this hub's transparency log.
# Prints cosign's three checks and the log entry's index and address. Changes nothing anywhere.
#
#   tools/hub/verify-signed.sh                 the model image Fleet act-inference pins right now
#   tools/hub/verify-signed.sh IMAGE@DIGEST    any image the pipeline signed
#
# Needs cosign, oc with KUBECONFIG set, and the log's route reachable. The keys come from the cluster: the
# public halves only (Secret cosign-signing's cosign.pub, ConfigMap rekor-public-key).
set -euo pipefail
die() { echo "${0##*/}: $*" >&2; exit 1; }
top=$(git rev-parse --show-toplevel 2> /dev/null) || die "run it from a checkout of the repository"
command -v cosign > /dev/null || die "cosign is not on PATH"
image=${1:-$(grep -o 'quay.io/jary/soarm-act-modelcar@sha256:[0-9a-f]*' "$top/gitops/rhem/fleet-act-inference.yaml" | head -1)}
[[ -n $image ]] || die "no image given and none pinned in gitops/rhem/fleet-act-inference.yaml"

rekor_host=$(oc get route -n trusted-artifact-signer -o jsonpath='{.items[?(@.spec.to.name=="rekor-server")].spec.host}')
[[ -n $rekor_host ]] || die "no rekor-server route in namespace trusted-artifact-signer - is KUBECONFIG set?"
keys=$(mktemp -d)
trap 'rm -rf "$keys"' EXIT
oc get secret cosign-signing -n flywheel -o jsonpath='{.data.cosign\.pub}' | base64 -d > "$keys/cosign.pub"
oc get configmap rekor-public-key -n flywheel -o jsonpath='{.data.rekor\.pub}' > "$keys/rekor.pub"
[[ -s $keys/cosign.pub && -s $keys/rekor.pub ]] || die "could not read the two public keys from the cluster"

echo "image:  $image"
echo "log:    https://$rekor_host"
echo
out=$(SIGSTORE_REKOR_PUBLIC_KEY="$keys/rekor.pub" cosign verify --key "$keys/cosign.pub" --rekor-url "https://$rekor_host" "$image" 2>&1 1> "$keys/bundle.json") \
    || { echo "$out" | grep -v "deprecated"; die "verification FAILED"; }
echo "$out" | grep -v "deprecated" | sed -e '/^$/d'
index=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[0]["optional"]["Bundle"]["Payload"]["logIndex"])' "$keys/bundle.json")
echo
echo "log entry:  index $index"
echo "            https://$rekor_host/api/v1/log/entries?logIndex=$index"
