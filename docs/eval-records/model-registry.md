<!-- This project was developed with assistance from AI tools. -->
# Model Registry — the durable promotion record (Phase 4.5 E1, D027)

**Date:** 2026-09-09. **State: exit criterion met.** Registry live on the hub under GitOps; the pipeline
registers the candidate (`register_model`), opens the PR and writes the PR URL back (`record_pr_url`).
Run **`9015ecd4-5524-45cf-b6c7-f045a17860bc`** (version `v-202609091102-registry2`) went
`trigger+wait -> gate -> package -> sign -> register_model -> open_promotion_pr -> record_pr_url` in 4 m 10 s
and left version `act-v2-ft160-rhem` in the registry with the signed digest, the metrics, Rekor index and
PR **#4** (rehearsal, not merged). The first proof run `3afee844-…` registered the version and then failed
in `open_promotion_pr` — see *Two fixes* below.

## What is live

| | |
|---|---|
| DSC | `gitops/operators-config/dsc.yaml`: `modelregistry: {managementState: Managed, registriesNamespace: rhoai-model-registries}`; `ModelRegistryReady=True` ~10 s after the Argo sync (commit `e368afe`) |
| CRD | `modelregistries.modelregistry.opendatahub.io` — `v1alpha1` (served) + **`v1beta1`** (served, storage). `v1beta1` has no `istio`; auth is `spec.oauthProxy` |
| Operator | `model-registry-operator-controller-manager` in `redhat-ods-applications`, image `rhoai/odh-model-registry-operator-rhel9`; server `rhoai/odh-model-registry-rhel9` (EmbedMD, `--embedmd-database-type=mysql`), proxy `openshift4/ose-oauth-proxy-rhel9` |
| CR | `gitops/operators-config/model-registry.yaml` — `ModelRegistry` **`flywheel`** in `rhoai-model-registries`: `grpc: {}`, `rest: {}`, `oauthProxy: {serviceRoute: enabled}`, `mysql: {host: model-registry-db.rhoai-model-registries.svc, port: 3306, database: model_registry, username: mlmduser, passwordSecret: {name: model-registry-db, key: database-password}}`. Conditions: `Available=True (DeploymentAvailable)`, `OAuthProxyAvailable=True`, `Progressing=False` |
| DB | MariaDB `registry.redhat.io/rhel9/mariadb-1011:9.8-1788409987`, PVC `model-registry-db` 10Gi `local-path`, Secret `model-registry-db` hand-created by `tools/hub/create-model-registry-db-secret.sh` (listed in `argocd/README.md`) |
| Service / Route | `flywheel.rhoai-model-registries.svc:8443` (`https-api`, serving cert); Route **`https://flywheel-rest.apps.sno-flywheel.local`** (reencrypt) — new `/etc/hosts` name, `10.0.0.49`. REST base `/api/model_registry/v1alpha3` (`/v1` → 404 on this server) |
| Auth | OAuth proxy `--openshift-delegate-urls` → SAR `get services/flywheel` in the ns. Operator-created Role `registry-user-flywheel` (+ Group binding `flywheel-users`); git adds RoleBinding `registry-user-flywheel-pipeline` for SA `flywheel/pipeline-runner-dspa` |
| Network | operator NetworkPolicy `flywheel-https-route` admits 8443 from the router only; git adds `flywheel-https-pipeline` admitting the `flywheel` namespace (the Route is not resolvable in-cluster) |
| Side effect | the component also deploys RHOAI's model catalog: `model-catalog` Deployment + Route `model-catalog.apps.sno-flywheel.local` (unused) |
| Forced | `gitops/operators/rhoai-operator.yaml` `config.resources` — rhods-operator 3 × 500m → 100m CPU request (node was at 99% requested / 12% used; the DB pod could not schedule) |

Probes (desktop, runner SA token via the route): `GET …/registered_models` → **200** `{"items":[]…}`;
no token → **403**. From a pod in `flywheel` with client 0.3.11 against the Service + `service-ca.crt`:
`get_registered_model("soarm-act") -> None`, `SMOKE OK` (proxy header
`Gap-Auth: system:serviceaccount:flywheel:pipeline-runner-dspa`).

## Pipeline change (`pipeline/act_flywheel_pipeline.py`)

`trigger+wait -> gate -> package -> sign -> register_model -> open_promotion_pr -> record_pr_url`

- `register_model(model_name="soarm-act", image_ref=<index@digest>, candidate, checkpoint_uri, dataset_uri,
  eval_report_uri, report_json, rekor_index, rekor_url, run_id, model_registry_url)`:
  `ModelRegistry(server, 8443, author="act-flywheel-pipeline", user_token=<pod SA token>, custom_ca=<service-ca.crt>)`
  `.register_model(name, uri, model_format_name="lerobot-act", model_format_version="1", version=candidate,
  metadata={dataset_uri, checkpoint_uri, eval_report_uri, incumbent, incumbent_success_rate,
  candidate_success_rate, n_paired, fixed, broken, net, sign_test_p, gate_rule, verdict, rekor_index,
  rekor_url, pr_url: "", dsp_run_id})`. Returns the version id.
- Ordering **(a)**: registered before the PR (D027); `record_pr_url` writes `pr_url` onto the version's
  custom properties after `open_promotion_pr`. An empty `pr_url` reads as "signed and registered, PR not opened".
- `sign_modelcar` → `NamedTuple(image_ref, rekor_index)`; the index is parsed from cosign's
  `tlog entry created with index: N` (first line = the index manifest's own entry; `-1` without Rekor).
- `trigger_and_wait` → third output `dataset_uri` (the host runner reports it; `"reused"` on the D023 path).
- Client pin **`model-registry==0.3.11`** — last client on `v1alpha3`; 0.3.12 / 0.3.15 / 0.3.16 target `/v1`
  and got `404 page not found` in-cluster.
- Ride-along fix: `COSIGN_PASSWORD` is read from the mounted `/etc/cosign/cosign.password` when present.
  The DSP launcher ignores `optional: true` on `secretAsEnv`; the live `cosign-signing-key` has no
  `cosign.password` key → run `f1785799` died in `CreateContainerConfigError` at `sign-modelcar` and was
  terminated. (Copying the key from Tekton's `cosign-signing` Secret was blocked for the agent.)
- New pipeline param `model_registry_url` (default `https://flywheel.rhoai-model-registries.svc:8443`).
- Versions uploaded: `v-202609090836-registry` (0.3.16, superseded), `v-202609090839-registry`
  (`e372c403-…`, env-secret path, superseded), `v-202609090850-registry` (`29400621-…`, run `3afee844`),
  **`v-202609091102-registry2` (`3f700dc3-7f0f-4366-9073-a05624e7722a`, run `9015ecd4`)**.

### Two fixes after the first proof run (commit `8d90222`)

Run `3afee844-007a-4c97-ab70-5f9cc38b9853` (after the Multus repair) reached `register_model` —
`registered_model_id: 1`, version id 2, `rekor_index: 5`, modelcar
`quay.io/jary/soarm-act-modelcar@sha256:2879ddae…` (Rekor [5, 6, 7]) — then `open_promotion_pr` died:
`github.GithubException 422 Reference already exists` at `repo.create_git_ref("refs/heads/promote/act-v2-ft160-rhem")`.
Merged PR #2's head branch still exists on GitHub, so D066's fixed branch name collides with itself on any
second run for the same candidate. `record_pr_url` never ran; the version sat with `pr_url: ""` (exactly the
state D083 wanted visible — but self-inflicted).

1. **`open_promotion_pr`**: takes `run_id` (the same `dsl.PIPELINE_JOB_ID_PLACEHOLDER` `register_model`
   records as `dsp_run_id`); head = **`promote/<candidate>-<run_id[:8]>`** (UTC timestamp if empty). If the
   ref exists anyway it is force-moved (`get_git_ref(...).edit(sha, force=True)`); an open PR for the same
   head is reused. Retry-safe.
2. **`register_model`**: client 0.3.11 raises `StoreError("Version … already exists")` on a second
   registration (it checks `get_model_version_by_params` and never updates). A rerun for a candidate whose
   version exists now **updates** that version — custom properties, description, and the artifact `uri` if
   the digest changed — instead of failing. One version row per candidate (D027); the row reflects the
   latest run. Log: `"action": "updated (previous dsp_run_id=3afee844-…)"`.

## Proof run `9015ecd4-5524-45cf-b6c7-f045a17860bc` — SUCCEEDED

Submitted 16:02:47Z through the DSP API (`POST /apis/v2beta1/runs`, port-forward on the host, SA token),
D1's parameters (`candidate=act-v2-ft160-rhem incumbent=upstream-act-teacher collector=upstream-act-teacher
incumbent_checkpoint=hf`, D023 pre-trained path), experiment `flywheel-promotions`; finished 16:06:57Z.

| task | result |
|---|---|
| trigger-and-wait | host runner reused the checkpoint + eval (`dataset_uri: reused`) |
| eval-gate | 0.73 → 0.86, fixed 20 / broken 7, net +13, p = 0.0192 → PASS |
| package-modelcar | re-packaged: **new** index digest `sha256:18cc4412a21bfdd48d27b654558d1268e7c95f168f28b50873c4e2417369e61a` (amd64 `1668097979…`, arm64 `e83b7e17…`) — same weights, but the D079 multi-arch build is not byte-reproducible, so every run yields a new index digest; expected |
| sign-modelcar | Rekor `tlog entry created with index: 8` (index manifest) + 9, 10 (per-arch, recursive) → `rekor_index: 8` |
| register-model | `updated (previous dsp_run_id=3afee844-…)`, `registered_model_id: 1`, version id 2, artifact uri → `18cc4412…` |
| open-promotion-pr | https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/4 on `promote/act-v2-ft160-rhem-9015ecd4`, commit `b33b5133` |
| record-pr-url | `pr_url recorded on soarm-act act-v2-ft160-rhem 2 -> …/pull/4` |

The first run's earlier `3afee844` state (`FAILED` at `open-promotion-pr`) and the first modelcar
`2879ddae…` (Rekor 5–7) remain in DSP / Quay / Rekor; the registry does not reference them any more.

### Registered version

Captured after the run via the Route (`--resolve flywheel-rest.apps.sno-flywheel.local:443:10.0.0.49`,
runner-SA bearer token, `GET /api/model_registry/v1alpha3/...`), trimmed:

```json
// GET /registered_models
{"items": [{"id": "1", "name": "soarm-act", "owner": "act-flywheel-pipeline", "state": "LIVE",
            "customProperties": {}, "createTimeSinceEpoch": "1788964667330"}], "size": 1}

// GET /model_versions
{"items": [{
  "id": "2", "name": "act-v2-ft160-rhem", "registeredModelId": "1", "author": "act-flywheel-pipeline", "state": "LIVE",
  "description": "upstream-act-teacher 0.73 -> act-v2-ft160-rhem 0.86, net +13, p=0.0192",
  "createTimeSinceEpoch": "1788964667349", "lastUpdateTimeSinceEpoch": "1788970005032",
  "customProperties": {
    "dataset_uri":            {"string_value": "reused"},
    "checkpoint_uri":         {"string_value": "s3://episodes-data/checkpoints/act-v2-ft160-rhem/pretrained_model.tar.gz"},
    "eval_report_uri":        {"string_value": "s3://episodes-data/eval/9015ecd4-5524-45cf-b6c7-f045a17860bc/eval_report.json"},
    "incumbent":              {"string_value": "upstream-act-teacher"},
    "incumbent_success_rate": {"double_value": 0.73},
    "candidate_success_rate": {"double_value": 0.86},
    "n_paired": {"int_value": "100"}, "fixed": {"int_value": "20"}, "broken": {"int_value": "7"}, "net": {"int_value": "13"},
    "sign_test_p":            {"double_value": 0.0192},
    "gate_rule":              {"string_value": "promote iff net > 0 and p < 0.05 (D022)"},
    "verdict":                {"string_value": "PASS"},
    "rekor_index":            {"int_value": "8"},
    "rekor_url":              {"string_value": "http://rekor-server.trusted-artifact-signer.svc"},
    "pr_url":                 {"string_value": "https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/4"},
    "dsp_run_id":             {"string_value": "9015ecd4-5524-45cf-b6c7-f045a17860bc"}
  }}], "size": 1}

// GET /model_artifacts
{"items": [{"id": "1", "artifactType": "model-artifact", "name": "soarm-act", "state": "UNKNOWN",
            "modelFormatName": "lerobot-act", "modelFormatVersion": "1",
            "uri": "quay.io/jary/soarm-act-modelcar@sha256:18cc4412a21bfdd48d27b654558d1268e7c95f168f28b50873c4e2417369e61a",
            "createTimeSinceEpoch": "1788964667371", "lastUpdateTimeSinceEpoch": "1788969937722"}], "size": 1}
```

(`metadataType` fields elided; each custom property carries `MetadataStringValue` / `MetadataIntValue` /
`MetadataDoubleValue`. `int_value` is a string on the wire.) The five-way join D027 asked for is one
GET: digest (`model_artifacts[0].uri`), metrics, Rekor index, PR URL, DSP run id.

### PR #4 — rehearsal, **do not merge**

https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/4 — `Promote act-v2-ft160-rhem (73% -> 86%)`, head
`promote/act-v2-ft160-rhem-9015ecd4`, base `desktop-gpu-split`, one commit `b33b5133`. Diff is the D066 shape:

```
gitops/rhem/fleet-act-inference.yaml
-  Image=quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b
+  Image=quay.io/jary/soarm-act-modelcar@sha256:18cc4412a21bfdd48d27b654558d1268e7c95f168f28b50873c4e2417369e61a
-  MODEL_VERSION: act-v2-ft160
+  MODEL_VERSION: act-v2-ft160-rhem
gitops/flywheel/manifest-consumer.yaml
-  INCUMBENT / COLLECTOR: act-v2-ft160;  INCUMBENT_CHECKPOINT: s3://…/act-v2-ft160/pretrained_model.tar.gz
+  INCUMBENT / COLLECTOR: act-v2-ft160-rhem;  INCUMBENT_CHECKPOINT: s3://…/act-v2-ft160-rhem/pretrained_model.tar.gz
```

The base currently serves `act-v2-ft160` @ `bdb513ca…` (the D2 rollback, PR #3), so the diff re-promotes the
same weights the Fleet ran under PR #2, under a fresh digest. Merging would roll the device again for no
behavioural change — leave it open as the "PR waiting for the human gate" screen or close it unmerged.

**Exit criterion (BUILD-PLAN item E, "the registry shows the promoted version with digest + metrics for at
least one candidate"): met** — version `act-v2-ft160-rhem` carries the signed digest, the paired-eval metrics,
Rekor index 8 and PR #4.

## Also observed

303 pods on the 250-pod node (114 Succeeded + 13 Failed linger — KFP driver/executor pods from every
run). Not the sandbox cause, but a TTL on finished workflows / a periodic prune belongs on the inbox.
