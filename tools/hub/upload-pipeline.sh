#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Upload pipeline/act_flywheel_pipeline.yaml to this hub's Data Science Pipelines server under the name the
# manifest consumer looks it up by. First time: a new pipeline. After that: a new version of it (the consumer
# always starts the newest version). Idempotent apart from adding a version. KUBECONFIG must be set.
#
# Talks to the DSPA route with a ten-minute token of the manifest-consumer ServiceAccount - the account that
# starts the runs, so an upload that works proves the consumer's access as well. The token is never printed.
set -euo pipefail
NS=flywheel
NAME=act-flywheel-promotion
here=$(cd "$(dirname "$0")" && pwd)
file=$here/../../pipeline/act_flywheel_pipeline.yaml
die() { echo "${0##*/}: $*" >&2; exit 1; }
[[ -f $file ]] || die "no compiled pipeline at $file"
grep -q "^  name: $NAME\$" "$file" || die "$file is not the $NAME pipeline"

host=$(oc -n "$NS" get route ds-pipeline-dspa -o jsonpath='{.spec.host}') || die "no ds-pipeline-dspa route in $NS"
ca=$(mktemp); trap 'rm -f "$ca"' EXIT
oc -n openshift-config-managed get cm default-ingress-cert -o jsonpath='{.data.ca-bundle\.crt}' > "$ca"
token=$(oc -n "$NS" create token manifest-consumer --duration 10m)
api() { curl -fsS --cacert "$ca" -H "Authorization: Bearer $token" "$@"; }

id=$(api "https://$host/apis/v2beta1/pipelines?page_size=100" | jq -r --arg n "$NAME" '[.pipelines[]? | select(.display_name == $n) | .pipeline_id] | join(" ")')
[[ $id != *' '* ]] || die "more than one pipeline is called $NAME: $id"
ver="$(git -C "$here" rev-parse --short HEAD 2>/dev/null || echo nogit)-$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -z $id ]]; then
    api -F "uploadfile=@$file" "https://$host/apis/v2beta1/pipelines/upload?name=$NAME" | jq -r '"created pipeline \(.display_name)  id \(.pipeline_id)"'
else
    api -F "uploadfile=@$file" "https://$host/apis/v2beta1/pipelines/upload_version?name=$ver&pipelineid=$id" |
        jq -r --arg n "$NAME" '"added version \(.display_name) to \($n)  (\(.pipeline_id))"'
fi
api "https://$host/apis/v2beta1/pipelines?page_size=100" | jq -r --arg n "$NAME" '.pipelines[] | select(.display_name == $n) | "on the server: \(.display_name)  \(.pipeline_id)  created \(.created_at)"'
