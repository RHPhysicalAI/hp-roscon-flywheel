# argocd/ — Argo CD bootstrap Applications

Everything in `gitops/` is delivered by Argo CD (OpenShift GitOps, ns `openshift-gitops`).
Each `*-app.yaml` here is an Argo `Application` that is applied **once by hand** to bootstrap;
after that Argo syncs the referenced `gitops/<dir>` from branch `desktop-gpu-split` (`selfHeal:
true`). Nothing else in this directory is applied by Argo itself.

## Bootstrap order

Run from the desktop host (the presenting laptop — the operator's machine, used for browsing/
phone/ssh — has no kubeconfig of its own; see `docs/DEMO_RUNBOOK.md`):

```bash
export KUBECONFIG=~/sno-flywheel/auth/kubeconfig
```

| # | File | Argo app | Target ns | Waits on |
|---|---|---|---|---|
| 1 | `storage-app.yaml` | `storage` | `local-path-storage` | — (default StorageClass `local-path`) |
| 2 | `operators-app.yaml` | `operators` | `openshift-operators` | 1 |
| 3 | `operators-config-app.yaml` | `operators-config` | `redhat-ods-operator` | 2 — all CSVs `Succeeded` (`oc get csv -A`) |
| 4 | `minio-app.yaml` | `minio` | `minio` | 1; Secret `minio-credentials` (below) |
| 5 | `flywheel-app.yaml` | `flywheel` | `flywheel` | 3, 4; Secret `hub-credentials` (below) |
| 6 | `observability-app.yaml` | `observability` | `observability` | Perses CRDs — Cluster Observability + Tempo operators, installed by hand (not in `gitops/operators/`) |
| 7 | — | `act-serving` | `flywheel` | retired 2026-09-09 after the first RHEM promotion and rollback (D025): file removed, app and its Deployments/Service deleted |
| 8 | `repo-flightctl-charts.yaml` | — (repository Secret) | `openshift-gitops` | — |
| 9 | `rhem-app.yaml` | `rhem` | `flightctl` | 1, 8, **OCP ≥ 4.19** (chart `kubeVersion >= 1.32`) |
| 10 | `tekton-app.yaml` | `tekton` | `flywheel` | 2 (Pipelines CSV, `pipeline` SA); Secrets `quay-push` + `cosign-signing` (below) |

Every app runs `prune: true` (D026 row 5, Phase 4.5 F): the cluster equals `gitops/<dir>`, and
`oc get applications.argoproj.io -n openshift-gitops` lists exactly one app per `*-app.yaml` here.

```bash
oc apply -f argocd/storage-app.yaml
oc apply -f argocd/operators-app.yaml
oc apply -f argocd/operators-config-app.yaml     # after CSVs are Succeeded
oc apply -f argocd/minio-app.yaml                # after creating minio-credentials by hand
oc apply -f argocd/flywheel-app.yaml             # after creating hub-credentials by hand
oc apply -f argocd/observability-app.yaml
oc apply -f argocd/repo-flightctl-charts.yaml
oc apply -f argocd/rhem-app.yaml                 # after the cluster is on 4.19
oc apply -f argocd/tekton-app.yaml               # runtime-image build + sign (D028)
oc get applications.argoproj.io -n openshift-gitops
```

## Hand-created Secrets (never in git)

Argo does not track these (no `argocd.argoproj.io/tracking-id` annotation), so `prune: true`
never touches them; a fresh cluster needs each one before the app that consumes it syncs. Names
and keys only — the MinIO values live in `~/.minio-env` on the desktop, the rest with the operator.

| Secret | Namespace | Keys | Used by | Created with |
|---|---|---|---|---|
| `minio-credentials` | `minio` | `root-user`, `root-password` | MinIO server; `minio-olga-readonly-setup` Job | the `oc create secret … \| oc apply` pair below |
| `hub-credentials` | `flywheel` | `s3-access-key`, `s3-secret-key` (same values) | sync-agent, rejected-mirror, DSPA object storage, KFP `eval_gate`/`package_modelcar` — `gitops/flywheel/README.md` | same |
| `minio-olga-readonly-credentials` | `minio` | `access-key`, `secret-key` | `minio-olga-readonly-setup` Job | see `gitops/minio/minio-readonly-user.yaml` |
| `cosign-signing-key` | `flywheel` | `cosign.key`, `cosign.pub`, `cosign.password` | KFP `sign_modelcar` (`COSIGN_PASSWORD` ← `cosign.password`) | same key material as `cosign-signing` |
| `github-token` | `flywheel` | `token` | KFP `open_promotion_pr` | GitHub fine-grained token (contents + pull requests) |
| `cosign-signing` | `flywheel` | `cosign.key`, `cosign.pub`, `cosign.password` | `tekton` app — `cosign-sign` Task (workspace `cosign-key`) | `oc create secret generic cosign-signing -n flywheel --from-file=cosign.key=$HOME/cosign/cosign.key --from-file=cosign.pub=$HOME/cosign/cosign.pub --from-literal=cosign.password="$COSIGN_PASSWORD"` on the desktop (same key as the KFP `cosign-signing-key` Secret and the Fleet's `cosign.pub`) |
| `model-registry-db` | `rhoai-model-registries` | `database-password` | `model-registry.yaml` — MariaDB `MYSQL_PASSWORD` and the `ModelRegistry` CR's `mysql.passwordSecret` (E1) | `tools/hub/create-model-registry-db-secret.sh` (generates the value locally; never echoed) |
| `quay-push` | `flywheel` | `.dockerconfigjson` | KFP `package_modelcar`/`sign_modelcar`; `tekton` app projects it as `config.json` (PipelineRun workspace `items:`) | quay.io robot dockerconfigjson |

The MinIO pair, from the desktop, without echoing values:

```bash
set -a; source ~/.minio-env; set +a
oc create secret generic minio-credentials -n minio \
  --from-literal=root-user="$MINIO_ACCESS_KEY" --from-literal=root-password="$MINIO_SECRET_KEY" \
  --dry-run=client -o yaml | oc apply -f -
oc create secret generic hub-credentials -n flywheel \
  --from-literal=s3-access-key="$MINIO_ACCESS_KEY" --from-literal=s3-secret-key="$MINIO_SECRET_KEY" \
  --dry-run=client -o yaml | oc apply -f -
```

## The Helm OCI repository Secret (`repo-flightctl-charts.yaml`)

`rhem-app.yaml` uses a Helm **OCI** source (`repoURL: quay.io/flightctl/charts`, `chart:
flightctl`, `targetRevision: 1.3.0`). Argo CD only treats a `repoURL` as OCI when a repository
Secret with `enableOCI: "true"` matches it, so the Secret must exist before the app. The registry
is public: the Secret holds `name`, `type: helm`, `url` and `enableOCI` only — no credentials —
which is why it lives in git. Argo picks it up by the `argocd.argoproj.io/secret-type: repository`
label; no restart needed.

## RHEM routes and `/etc/hosts` (D006)

The chart lays routes out as `<service>.<namespace>.apps.<cluster domain>`, i.e. under
`*.flightctl.apps.sno-flywheel.local`. Add this line to `/etc/hosts` on the presenting laptop (and on the
device VM, Phase 4.5 B), next to the existing `10.0.0.49` lines:

```
10.0.0.49 ui.flightctl.apps.sno-flywheel.local api.flightctl.apps.sno-flywheel.local agent-api.flightctl.apps.sno-flywheel.local remote-access.flightctl.apps.sno-flywheel.local agent-remote-access.flightctl.apps.sno-flywheel.local cli-artifacts.flightctl.apps.sno-flywheel.local telemetry.flightctl.apps.sno-flywheel.local alertmanager-proxy.flightctl.apps.sno-flywheel.local
```

`oc get route -n flightctl` lists the live set. The UI is `https://ui.flightctl.apps.sno-flywheel.local`
(OpenShift OAuth login); the CLI logs in with a real user's token — see `rhem/bootstrap/README.md`
for why the chart's ServiceAccount token does not work and how to get a kubeadmin token.

## Why `rhem-app.yaml` pins so many values

Argo CD renders Helm with `helm template`, where Helm's `lookup` returns nothing. The flightctl
chart uses `lookup` for the cluster base domain, the `oauth-openshift` Route (`fail` if absent),
the ingress CA and every previously generated password. `rhem-app.yaml` therefore sets the base
domain, the OAuth URLs and TLS-verify explicitly, and holds the four generated Secrets, the
`OAuthClient` secret and the service ConfigMaps that embed it at their live values
(`ignoreDifferences` + `RespectIgnoreDifferences=true`). The comments in the file say which is
which. Fleets and CatalogItems are **not** Argo's job — see `rhem/bootstrap/README.md`.
