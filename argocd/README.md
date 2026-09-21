<!-- This project was developed with assistance from AI tools. -->
# argocd/ — Argo CD bootstrap Applications

Everything in `gitops/` is delivered by Argo CD (OpenShift GitOps, ns `openshift-gitops`).
Each `*-app.yaml` here is an Argo `Application` that is applied **once by hand** to bootstrap;
after that Argo syncs the referenced `gitops/<dir>` from branch `fury` (`selfHeal:
true`). Nothing else in this directory is applied by Argo itself.

## Bootstrap order

Run with a cluster-admin login on the hub: the installer's kubeconfig (`tools/host/fury/32-sno-install.sh` leaves
it on the GPU host) or an `oc login` from a laptop.

| # | File | Argo app | Target ns | Waits on |
|---|---|---|---|---|
| 0 | `bootstrap-operators.yaml` | — (four Subscriptions) | `openshift-operators` | — ; then `oc adm policy add-cluster-role-to-user cluster-admin -z openshift-gitops-argocd-application-controller -n openshift-gitops` once that namespace exists |
| 1 | `storage-app.yaml` | `storage` | `local-path-storage` | — (default StorageClass `local-path`) |
| 2 | `operators-app.yaml` | `operators` | `openshift-operators` | 1 |
| 3 | `operators-config-app.yaml` | `operators-config` | `flywheel` (its objects name their own) | 2 — all CSVs `Succeeded` (`oc get csv -A`) |
| 4 | `minio-app.yaml` | `minio` (object storage) | `minio` | 1; Secret `minio-credentials` (below) |
| 5 | `flywheel-app.yaml` | `flywheel` | `flywheel` | 3, 4; Secret `hub-credentials` (below) |
| 6 | `observability-app.yaml` | `observability` | `observability` | 0 — the Perses CRDs come with the Cluster Observability operator (with Tempo, from `bootstrap-operators.yaml`, not `gitops/operators/`) |
| 7 | `rhem-app.yaml` | `rhem` | `flightctl` | 1, **OCP ≥ 4.19** (chart `kubeVersion >= 1.32`); Red Hat's chart `redhat-rhem` 1.3.0 from `https://charts.openshift.io` |
| 8 | `tekton-app.yaml` | `tekton` | `flywheel` | 2 (Pipelines CSV, `pipeline` SA); Secrets `quay-push` + `cosign-signing` (below) |
| 9 | `devspaces-app.yaml` | `devspaces` | `openshift-devspaces` | 0 — Dev Spaces and DevWorkspace CSVs `Succeeded`; 1 (workspace PVC on `local-path`) |
| 10 | `fleet-worlds-app.yaml` | `fleet-worlds` | `fleet` | 8 — `gitops/fleet-worlds/world.yaml` pins a real image digest first (`tools/hub/build-fleet-world.sh`, built and signed by the `tekton` app's pipeline) |

Every app runs `prune: true`: the cluster equals `gitops/<dir>`, and
`oc get applications.argoproj.io -n openshift-gitops` lists exactly one app per `*-app.yaml` here.

Not in this table: the transparency log. On this arm64 hub it is brought up by hand from
`tools/host/fury/rhtas-arm64/` (its README has the order), before the `tekton` app's first signing run; its public
key is `gitops/tekton/rekor-public-key.yaml`.

```bash
oc apply -f argocd/bootstrap-operators.yaml   # GitOps, Cluster Observability, Tempo, Dev Spaces; wait for the CSVs
oc adm policy add-cluster-role-to-user cluster-admin -z openshift-gitops-argocd-application-controller -n openshift-gitops
oc apply -f argocd/storage-app.yaml
oc apply -f argocd/operators-app.yaml
oc apply -f argocd/operators-config-app.yaml     # after CSVs are Succeeded
oc apply -f argocd/minio-app.yaml                # after creating minio-credentials by hand
oc apply -f argocd/flywheel-app.yaml             # after creating hub-credentials by hand
oc apply -f argocd/observability-app.yaml
oc apply -f argocd/rhem-app.yaml                 # after the cluster is on 4.19
oc apply -f argocd/tekton-app.yaml               # runtime-image build + sign
oc apply -f argocd/devspaces-app.yaml            # after the Dev Spaces CSV is Succeeded
oc apply -f argocd/fleet-worlds-app.yaml         # after world.yaml pins a real image digest
oc get applications.argoproj.io -n openshift-gitops
```

## Dev Spaces (`devspaces-app.yaml`)

OpenShift Dev Spaces is here for one thing: a single demo workspace in the browser, in which a terminal coding agent
works on this repo against the in-cluster model endpoint `http://assistant.flywheel.svc:8000/v1` - the Service has no
Route, so the agent has to run in a pod, and a workspace is that pod. `gitops/devspaces/checluster.yaml` switches idling
off (a workspace left open during a talk is still there), keeps workspace images on the node once pulled and serves
editor extensions from the registry embedded in the install, so a started workspace needs no internet. The operator
and its DevWorkspace dependency come from `bootstrap-operators.yaml`. The dashboard URL is
`oc get checluster devspaces -n openshift-devspaces -o jsonpath='{.status.cheURL}'`
(`https://devspaces.apps.<cluster domain>`; login is the cluster's OAuth) and resolves like the other `*.apps` names.

## Hand-created Secrets (never in git)

Argo does not track these (no `argocd.argoproj.io/tracking-id` annotation), so `prune: true`
never touches them; a fresh cluster needs each one before the app that consumes it syncs. Names
and keys only — the values stay with the operator.

| Secret | Namespace | Keys | Used by | Created with |
|---|---|---|---|---|
| `minio-credentials` | `minio` | `root-user`, `root-password` | the object storage server; `minio-eval-readonly-setup` Job | the `oc create secret … \| oc apply` pair below |
| `hub-credentials` | `flywheel` | `s3-access-key`, `s3-secret-key` (same values) | sync-agent, rejected-mirror, DSPA object storage, KFP `eval_gate`/`package_modelcar` — `gitops/flywheel/README.md` | same |
| `minio-eval-readonly-credentials` | `minio` and `flywheel` (same values in both) | `access-key`, `secret-key` | `minio-eval-readonly-setup` Job (`minio`); `eval-dashboard` and `eval-dashboard-live` Deployments (`flywheel`) | see `gitops/minio/minio-readonly-user.yaml` |
| `cosign-signing-key` | `flywheel` | `cosign.key`, `cosign.pub`, `cosign.password` | KFP `sign_modelcar` (`COSIGN_PASSWORD` ← `cosign.password`) | same script, same key material as `cosign-signing` |
| `github-token` | `flywheel` | `token` | KFP `open_promotion_pr` | GitHub fine-grained token (contents + pull requests) |
| `cosign-signing` | `flywheel` | `cosign.key`, `cosign.pub`, `cosign.password` | `tekton` app — `cosign-sign` Task (workspace `cosign-key`) | `tools/hub/create-cosign-secrets.sh` (generates the pair if there is none, asks for the passphrase on the terminal, checks that it opens the key, creates this Secret and `cosign-signing-key`; its `cosign.pub` is the Fleet's inline `cosign.pub`) |
| `model-registry-db` | `rhoai-model-registries` | `database-password` | `model-registry.yaml` — MariaDB `MYSQL_PASSWORD` and the `ModelRegistry` CR's `mysql.passwordSecret` (E1) | `tools/hub/create-model-registry-db-secret.sh` (generates the value locally; never echoed) |
| `quay-push` | `flywheel` | `.dockerconfigjson` | KFP `package_modelcar`/`sign_modelcar`; `tekton` app projects it as `config.json` (PipelineRun workspace `items:`) | quay.io robot dockerconfigjson |

The object storage pair, without echoing values - `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` are set in the shell
beforehand, from wherever the operator keeps them:

```bash
oc create secret generic minio-credentials -n minio \
  --from-literal=root-user="$MINIO_ACCESS_KEY" --from-literal=root-password="$MINIO_SECRET_KEY" \
  --dry-run=client -o yaml | oc apply -f -
oc create secret generic hub-credentials -n flywheel \
  --from-literal=s3-access-key="$MINIO_ACCESS_KEY" --from-literal=s3-secret-key="$MINIO_SECRET_KEY" \
  --dry-run=client -o yaml | oc apply -f -
```

## Hand-applied objects Argo CD will not manage

Argo CD leaves `EndpointSlice` and `Endpoints` out of what it manages: one placed under `gitops/` is reported
Synced and never created. The three this hub needs live outside `gitops/` and are applied once per hub, after the
`flywheel` app has synced:

```
oc apply -f tools/hub/manual/sim-cameras-endpointslice.yaml
oc apply -f tools/hub/manual/assistant-endpointslice.yaml
oc apply -f tools/hub/manual/fleet-renderer-endpointslice.yaml
```

The second gives Service `assistant` its endpoint - the coding assistant's hub-side listener on the GPU host
(`tools/host/fury/64-assistant-expose.sh open`). The first gives Service `sim-cameras-host` its endpoint - the camera bridge next to the sim on the GPU host - behind the
https Route `sim-cameras`. The host side is `tools/host/fury/15-camera-port.sh open`.
The third gives Service `fleet-renderer` its endpoint - the fleet's rendering tenant on the GPU host - behind
the https Route `fleet-wall` (`gitops/flywheel/fleet-wall.yaml`). The host side is
`tools/host/fury/73-fleet-renderer-install.sh install`, which opens the port once the renderer answers.

## The Edge Manager chart (`rhem-app.yaml`, `repo-flightctl-charts.yaml`)

`rhem-app.yaml` installs Red Hat Edge Manager 1.3.0 from Red Hat's chart: `repoURL: https://charts.openshift.io`,
`chart: redhat-rhem`, `targetRevision: 1.3.0` - an ordinary https Helm repository, so Argo CD needs no repository
Secret for it. Every image comes from `registry.redhat.io`, and the page reads "Red Hat Edge Manager" because the
chart takes its branding from its own name. Release name (`flightctl`) and values are the ones the project chart
was installed with, so the same objects were updated in place.

`repo-flightctl-charts.yaml` is only needed to go back to the project chart (`quay.io/flightctl/charts`, a Helm
**OCI** source): Argo CD only treats a `repoURL` as OCI when a repository Secret with `enableOCI: "true"` matches
it. The registry is public: the Secret holds `name`, `type: helm`, `url` and `enableOCI` only — no credentials —
which is why it lives in git. It is not part of the bootstrap order above.

## RHEM routes

The chart lays routes out as `<service>.<namespace>.apps.<cluster domain>`, i.e. under
`*.flightctl.apps.sno-flywheel.local`. Those names resolve like every other `*.apps` name of the hub - through the
GPU host's DNS (`tools/host/fury/dnsmasq-fury.conf`), which answers the whole apps domain - so they need no
entry of their own.

`oc get route -n flightctl` lists the live set. The UI is `https://ui.flightctl.apps.sno-flywheel.local`
(OpenShift OAuth login); the CLI logs in with a real user's token — see `rhem/bootstrap/README.md`
for why the chart's ServiceAccount token does not work.

## Why `rhem-app.yaml` pins so many values

Argo CD renders Helm with `helm template`, where Helm's `lookup` returns nothing. The
chart uses `lookup` for the cluster base domain, the `oauth-openshift` Route (`fail` if absent),
the ingress CA and every previously generated password. `rhem-app.yaml` therefore sets the base
domain, the OAuth URLs and TLS-verify explicitly, and holds the four generated Secrets, the
`OAuthClient` secret and the service ConfigMaps that embed it at their live values
(`ignoreDifferences` + `RespectIgnoreDifferences=true`). The comments in the file say which is
which. Fleets and CatalogItems are **not** Argo's job — see `rhem/bootstrap/README.md`.
