#!/bin/bash
# This project was developed with assistance from AI tools.
#
# Rebuild the Rekor + Trillian side of Red Hat Trusted Artifact Signer 1.4.3 for arm64, from the public
# midstream source (github.com/securesign) at the release tags. Red Hat ships these images for amd64 only.
# One stage at a time:
#
#   REGISTRY=<registry>/<namespace> ./build.sh bases       pull the two registry.redhat.io base images (the only step that needs that login)
#   REGISTRY=...                    ./build.sh rekor       rekor-server
#   REGISTRY=...                    ./build.sh trillian    trillian-logserver, -logsigner, -database, -redis
#   REGISTRY=...                    ./build.sh operator    rhtas-operator, with related-images.tmpl as its built-in defaults
#   REGISTRY=...                    ./build.sh all         the three above
#   REGISTRY=...                    ./build.sh backfill    rekor-backfill-redis: only if the Rekor CR enables backFillRedis
#   REGISTRY=...                    ./build.sh push        push what was built, record the digests
#   REGISTRY=...                    ./build.sh manifests   render the operator install YAML (needs oc or kubectl, no cluster)
#   REGISTRY=...                    ./build.sh list        what is built
#
#   REGISTRY       where the images are pushed, e.g. the cluster registry's route plus a namespace
#   PULL_REGISTRY  the name the cluster pulls them by, when that differs (the registry's in-cluster service)
#   TAG            default 1.4.3-arm64: use a new one for a rebuild, the node will not pull the same tag twice
#   TLS_VERIFY     false to push to a route whose CA this host does not trust yet
#   WORK           default ~/rhtas-arm64-build
#
# No root, rootless podman is enough. trillian-database, trillian-redis and rekor-backfill-redis are built
# FROM registry.redhat.io RHEL images. `bases` pulls those by digest and nothing afterwards goes back to
# that registry, so the login (podman login, or REGISTRY_AUTH_FILE=<pull secret>) can be dropped again as
# soon as it returns. Keep those three images private - they are not redistributable. The rest is public UBI.
set -euo pipefail

die() { echo "${0##*/}: $*" >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || die "native arm64 builds only, and this machine is $(uname -m)"
[[ $EUID -ne 0 ]]            || die "run this as an ordinary user, not through sudo"
: "${REGISTRY:?set REGISTRY=<registry>/<namespace>}"

here=$(dirname "$(readlink -f "$0")")
work=${WORK:-$HOME/rhtas-arm64-build}
tag=${TAG:-1.4.3-arm64}
pull=${PULL_REGISTRY:-$REGISTRY}
names=(rekor-server trillian-logserver trillian-logsigner trillian-database trillian-redis rhtas-operator rekor-backfill-redis)
mkdir -p "$work/log" "$work/containerfiles" "$work/digests"
exec > >(tee -a "$work/log/$(date -u +%Y%m%dT%H%M%SZ)-${1:-none}.log") 2>&1

# a tag can be moved, a commit cannot
fetch() {  # repo tag commit
    local dir=$work/$1
    [[ -d $dir/.git ]] || git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$2" "https://github.com/securesign/$1" "$dir"
    [[ $(git -C "$dir" rev-parse HEAD) == "$3" ]] || die "$dir is not $2 ($3): move it aside and run again"
}
fetch_rekor()    { fetch rekor    rhtas-v1.4.3 55171081d055fd15bf531ce18ac28136e67baef8; }
fetch_trillian() { fetch trillian rhtas-v1.4.3 5e3ffa09ee112c0f37aff41678291ee2ef2640ce; }

# Red Hat's Dockerfile minus what an outside build cannot reach: the UBI builder comes from the public
# registry (same digest), and the debug/test stages based on the internal brew registry are dropped
containerfile() {
    sed 's#^FROM registry\.redhat\.io/ubi9/#FROM registry.access.redhat.com/ubi9/#' "$1" |
        awk 'toupper($1) == "FROM" { skip = ($2 ~ /^brew\.registry\.redhat\.io\//) } !skip'
}

# FROM lines that only a registry.redhat.io login can satisfy, on stdin
red_hat_bases() { awk 'toupper($1) == "FROM" && $2 ~ /^registry\.redhat\.io\// {print $2}' | sort -u; }

# Go module caches and builder layers land in the rootless store, which sits in the home directory
room() {
    local root avail
    root=$(podman info --format '{{.Store.GraphRoot}}')
    avail=$(df --output=avail -BG "$root" | tail -1 | tr -dc 0-9)
    (( avail >= 30 )) || die "only ${avail}G free under $root, the builds want about 30G"
}

build() {  # image repo dockerfile
    local img=$REGISTRY/$1:$tag src=$work/$2 cf=$work/containerfiles/$1 base
    containerfile "$src/$3" > "$cf"
    while read -r base; do
        podman image exists "$base" || die "$1 is built FROM $base: run '$0 bases' first"
    done < <(red_hat_bases < "$cf")
    room
    echo "== $img  <-  securesign/$2 $3"
    time podman build --platform linux/arm64 -f "$cf" -t "$img" "$src"
    [[ $(podman image inspect "$img" --format '{{.Architecture}}') == arm64 ]] || die "$img did not come out as arm64"
}

# the kit's image list with the name the cluster pulls by
render_env() {
    sed -e "s#\${REGISTRY}#$pull#g" -e "s#\${TAG}#$tag#g" "$here/related-images.tmpl"
}

fetch_operator() {
    fetch secure-sign-operator v1.4.3 927b660f8ada68577569f6268029619ec782b5cb
    # `go generate` embeds this file in the manager as its defaults; the Deployment's env is set from it too
    render_env > "$work/secure-sign-operator/config/default/images.env"
}

report() {
    local n img
    for n in "${names[@]}"; do
        img=$REGISTRY/$n:$tag
        podman image exists "$img" || continue
        podman image inspect "$img" --format "{{.Id}}  {{.Os}}/{{.Architecture}}  {{.Size}} bytes  $img"
    done
    df -h "$work" | tail -1
}

stage_bases() {
    local f base
    fetch_trillian
    podman login --get-login registry.redhat.io >/dev/null 2>&1 ||
        die "needs a registry.redhat.io login for this one step: podman login registry.redhat.io, or REGISTRY_AUTH_FILE=<pull secret>"
    while read -r base; do
        podman pull --platform linux/arm64 "$base"
        [[ $(podman image inspect "$base" --format '{{.Architecture}}') == arm64 ]] || die "$base has no arm64 variant"
    done < <(for f in Dockerfile.database.rh Dockerfile.redis.rh; do containerfile "$work/trillian/$f"; done | red_hat_bases)
    echo "bases are local: the registry.redhat.io login is not needed again"
}

stage_rekor() {
    fetch_rekor
    build rekor-server rekor Dockerfile.rekor-server.rh
}

stage_trillian() {
    fetch_trillian
    build trillian-logserver trillian Dockerfile.logserver.rh
    build trillian-logsigner trillian Dockerfile.logsigner.rh
    build trillian-database  trillian Dockerfile.database.rh
    build trillian-redis     trillian Dockerfile.redis.rh
}

stage_operator() {
    fetch_operator
    build rhtas-operator secure-sign-operator Dockerfile.rhtas-operator.rh
}

case ${1:-} in
bases)    stage_bases ;;
rekor)    stage_rekor;    report ;;
trillian) stage_trillian; report ;;
operator) stage_operator; report ;;
all)      stage_rekor; stage_trillian; stage_operator; report ;;
backfill)
    fetch_rekor
    build rekor-backfill-redis rekor Dockerfile.backfill-redis.rh
    report
    ;;
push)
    args=()
    if [[ -n ${TLS_VERIFY:-} ]]; then args+=("--tls-verify=$TLS_VERIFY"); fi
    for n in "${names[@]}"; do
        img=$REGISTRY/$n:$tag
        if ! podman image exists "$img"; then echo "not built, skipped: $img"; continue; fi
        podman push "${args[@]}" --digestfile "$work/digests/$n" "$img"
        echo "pushed $img  $(cat "$work/digests/$n")"
    done
    ;;
manifests)
    k=$(command -v oc || command -v kubectl) || die "no oc or kubectl here for 'kustomize': install the client, or copy $work to a machine that has one"
    fetch_operator
    mkdir -p "$work/deploy"
    cat > "$work/deploy/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
- ../secure-sign-operator/config/default
images:
- name: registry.redhat.io/rhtas/rhtas-rhel9-operator
  newName: $pull/rhtas-operator
  newTag: $tag
EOF
    "$k" kustomize "$work/deploy" > "$work/operator-install.yaml"
    grep -q "image: $pull/rhtas-operator:$tag" "$work/operator-install.yaml" || die "the manager image was not replaced in the render"
    echo "ours:";      grep -A1 'name: RELATED_IMAGE_' "$work/operator-install.yaml" | grep 'value:' | grep -F "$pull/" || die "no RELATED_IMAGE points at $pull"
    echo "Red Hat's:"; grep -A1 'name: RELATED_IMAGE_' "$work/operator-install.yaml" | grep 'value:' | grep -vF "$pull/"
    echo "rendered $work/operator-install.yaml - apply it with: oc apply --server-side -f $work/operator-install.yaml"
    ;;
list)     report ;;
*)  echo "usage: REGISTRY=<registry>/<namespace> $0 bases | rekor | trillian | operator | all | backfill | push | manifests | list" >&2; exit 1 ;;
esac
