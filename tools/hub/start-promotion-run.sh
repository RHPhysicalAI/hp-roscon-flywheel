#!/usr/bin/env bash
# This project was developed with assistance from AI tools.
#
# Start one run of the act-flywheel-promotion pipeline by hand, the way the manifest consumer does when the
# success count reaches its threshold - for a dress rehearsal, or to repeat a run whose trigger was consumed.
# It does not touch the consumer's count. KUBECONFIG must be set.
#
#   tools/hub/start-promotion-run.sh <candidate> [steps_per_frame] [eval_n]
#       candidate        a new model-version name, e.g. act-v2-ft160-rehearsal-202609192215
#       steps_per_frame  default 0.25 (the pipeline's own); 0.002 makes a few-hundred-step fine-tune
#       eval_n           default 100 seeded episodes per policy; 3 for a rehearsal
#
# Incumbent, collector and the incumbent's checkpoint are read from the manifest-consumer Deployment, so a
# hand-started run fine-tunes from and is judged against the same model an automatic one would be.
set -euo pipefail
NS=flywheel
NAME=act-flywheel-promotion
die() { echo "${0##*/}: $*" >&2; exit 1; }
cand=${1:?candidate name}
spf=${2:-0.25}
n=${3:-100}
[[ $cand =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || die "candidate must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\$ (the host runner refuses anything else)"
[[ $spf =~ ^[0-9]*\.?[0-9]+$ && $n =~ ^[0-9]+$ ]] || die "steps_per_frame is a number, eval_n an integer"

env_of() { oc -n "$NS" get deploy manifest-consumer -o json | jq -r --arg k "$1" '.spec.template.spec.containers[0].env[] | select(.name == $k) | .value'; }
incumbent=$(env_of INCUMBENT); collector=$(env_of COLLECTOR); ckpt=$(env_of INCUMBENT_CHECKPOINT)
[[ -n $incumbent && -n $collector && -n $ckpt ]] || die "could not read INCUMBENT / COLLECTOR / INCUMBENT_CHECKPOINT from deploy/manifest-consumer"

host=$(oc -n "$NS" get route ds-pipeline-dspa -o jsonpath='{.spec.host}')
ca=$(mktemp); trap 'rm -f "$ca"' EXIT
oc -n openshift-config-managed get cm default-ingress-cert -o jsonpath='{.data.ca-bundle\.crt}' > "$ca"
token=$(oc -n "$NS" create token manifest-consumer --duration 10m)
api() { curl -fsS --cacert "$ca" -H "Authorization: Bearer $token" "$@"; }

pid=$(api "https://$host/apis/v2beta1/pipelines?page_size=100" | jq -r --arg n "$NAME" '[.pipelines[]? | select(.display_name == $n) | .pipeline_id] | join(" ")')
[[ -n $pid && $pid != *' '* ]] || die "expected exactly one pipeline named $NAME on the server, found: '${pid:-none}' (tools/hub/upload-pipeline.sh)"
vid=$(api "https://$host/apis/v2beta1/pipelines/$pid/versions?sort_by=created_at%20desc&page_size=1" | jq -r '.pipeline_versions[0].pipeline_version_id')

body=$(jq -n --arg c "$cand" --arg i "$incumbent" --arg col "$collector" --arg ck "$ckpt" --arg pid "$pid" --arg vid "$vid" \
             --argjson spf "$spf" --argjson n "$n" \
    '{display_name: ("promote-" + $c), pipeline_version_reference: {pipeline_id: $pid, pipeline_version_id: $vid},
      runtime_config: {parameters: {candidate: $c, incumbent: $i, collector: $col, incumbent_checkpoint: $ck,
                                    steps_per_frame: $spf, eval_n: $n}}}')
api -H 'Content-Type: application/json' -d "$body" "https://$host/apis/v2beta1/runs" |
    jq -r '"started run \(.run_id)  \(.display_name)  state \(.state)"'
echo "incumbent $incumbent, collector $collector, steps_per_frame $spf, eval_n $n"
echo "follow it: RHOAI dashboard > Data science pipelines > Runs, and on the host: sudo journalctl -fu flywheel-runner"
