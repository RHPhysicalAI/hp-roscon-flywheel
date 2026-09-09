<!-- This project was developed with assistance from AI tools. -->
# Model Registry — the durable promotion record (Phase 4.5 E1, D027)

**Date:** 2026-09-09. **State:** registry live on the hub under GitOps; pipeline `register_model` +
`record_pr_url` written, compiled, uploaded (`v-202609090850-registry`); the proof run
`3afee844-007a-4c97-ab70-5f9cc38b9853` is **parked on a cluster fault** (Multus `Unauthorized`, below) —
the registered-version JSON and PR #4 sections are filled in once it completes.

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
  (`e372c403-…`, env-secret path, superseded), **`v-202609090850-registry` (`29400621-5531-429b-80b5-8e24f613f240`)**.

## Proof run — pending a cluster repair

Run **`3afee844-007a-4c97-ab70-5f9cc38b9853`** (created 13:50:42Z), D1's parameters
(`candidate=act-v2-ft160-rhem incumbent=upstream-act-teacher collector=upstream-act-teacher incumbent_checkpoint=hf`).
Its `root-driver` pod cannot get a network sandbox: since **13:45:03Z** every new pod in `flywheel`
fails with `Multus: […]: error waiting for pod: Unauthorized` (586 such lines in the `multus-4rzbb`
log in 40 min; the `rejected-mirror` CronJob pods fail identically). Stale Multus API token; the
repair is a restart of the Multus daemonset pod — an operator action:

```bash
oc delete pod -n openshift-multus -l app=multus      # daemonset recreates it in ~20 s
oc get events -n flywheel --sort-by=.lastTimestamp | tail -3   # FailedCreatePodSandBox should stop
```

The run needs no resubmission — the kubelet retries the sandbox and Argo continues. Expected: PR **#4**
re-promoting `act-v2-ft160 → act-v2-ft160-rhem` (rehearsal, **do not merge**), then:

```bash
H=flywheel-rest.apps.sno-flywheel.local; T=$(oc create token pipeline-runner-dspa -n flywheel --duration=10m)
curl -sk --resolve $H:443:10.0.0.49 -H "Authorization: Bearer $T" https://$H/api/model_registry/v1alpha3/registered_models
curl -sk --resolve $H:443:10.0.0.49 -H "Authorization: Bearer $T" "https://$H/api/model_registry/v1alpha3/model_versions"
curl -sk --resolve $H:443:10.0.0.49 -H "Authorization: Bearer $T" "https://$H/api/model_registry/v1alpha3/model_artifacts"
```

### Registered version (to fill in)

_pending the run: `registered_models[0]` (`soarm-act`), `model_versions[0]` (`act-v2-ft160-rhem`, custom
properties incl. `rekor_index`, `pr_url`), `model_artifacts[0].uri` (= the signed index digest)._

### PR #4

_pending._

## Also observed

303 pods on the 250-pod node (114 Succeeded + 13 Failed linger — KFP driver/executor pods from every
run). Not the sandbox cause, but a TTL on finished workflows / a periodic prune belongs on the inbox.
