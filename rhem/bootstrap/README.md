# rhem/bootstrap/ — flightctl API objects applied once

Fleets and CatalogItems are flightctl **API objects**, not Kubernetes CRs, so Argo CD cannot
apply them (D024). RHEM has its own GitOps loop for that: a `Repository` pointing at this repo
and two `ResourceSync`s — one renders the Fleets in `gitops/rhem/`, the other the CatalogItems in
`gitops/rhem-catalog/` (a sync handles one resource type, D032) — both from branch
`desktop-gpu-split`. The files here bootstrap that loop and are applied **once by hand** with
`flightctl apply`, the same role `argocd/*-app.yaml` plays for Argo.

| File | Object | What it does |
|---|---|---|
| `repository.yaml` | `Repository/hp-roscon-flywheel` | `type: git`, public HTTPS URL, no auth |
| `resourcesync.yaml` | `ResourceSync/rhem-fleets` | `type: fleet`, `path: gitops/rhem`, `targetRevision: desktop-gpu-split` |
| `catalog.yaml` | `Catalog/physical-ai-models` (**v1alpha1**) | the software catalog the promoted model versions live in (E2, D027); hand-applied and unowned, so the catalog sync may put items in it |
| `resourcesync-catalog.yaml` | `ResourceSync/rhem-catalog` | `type: catalog`, `path: gitops/rhem-catalog`, `targetRevision: desktop-gpu-split`; accepts only `Catalog`/`CatalogItem` kinds |

Field names follow the flightctl 1.3 core OpenAPI (`GitRepoSpec.{type,url}`,
`ResourceSyncSpec.{type,repository,targetRevision,path}`, `ResourceSyncType` = `fleet | catalog`)
and, for the Catalog, `api/core/v1alpha1/openapi.yaml` (`CatalogSpec`). The Catalog API is alpha
in flightctl 1.3.0 and may change.

## Apply

Log in with a **real OpenShift user's** token (kubeadmin is fine: it is in `system:cluster-admins`,
which the chart maps to `flightctl-admin`). The default `~/sno-flywheel/auth/kubeconfig` is
certificate-based, so `oc whoami -t` there is empty — get a token from a throwaway login:

```bash
# on the desktop
KUBECONFIG=$(mktemp) oc login https://api.sno-flywheel.local:6443 -u kubeadmin \
  -p "$(cat ~/sno-flywheel/auth/kubeadmin-password)" --insecure-skip-tls-verify
flightctl login https://api.flightctl.apps.sno-flywheel.local --token "$(oc whoami -t)" --insecure-skip-tls-verify
flightctl apply -f rhem/bootstrap/repository.yaml
flightctl apply -f rhem/bootstrap/resourcesync.yaml
flightctl apply -f rhem/bootstrap/catalog.yaml
flightctl apply -f rhem/bootstrap/resourcesync-catalog.yaml
flightctl get repository,resourcesync
flightctl get catalogs; flightctl get catalogitems --catalog physical-ai-models
```

Verified 2026-09-08: `flightctl get fleets` answers as kubeadmin without any RBAC workaround.
Two things that do **not** work:

- The chart's `flightctl-admin` ServiceAccount token (`oc create token flightctl-admin -n
  flightctl`) validates but maps to **no organisation** ("You do not have access to any
  organizations") — OpenShift organisations are the projects the caller can read, and the SA
  cannot `get` its own project. Use a user token.
- If `api.flightctl.apps.sno-flywheel.local` does not resolve on the machine you are on (no
  `/etc/hosts` line yet), the route is TLS-passthrough so an IP URL will not SNI-route. Use a
  port-forward instead: `oc port-forward -n flightctl svc/flightctl-api 3443:3443` and
  `flightctl login https://localhost:3443 ...`. The desktop also still had a thor-era
  `~/.config/flightctl/client.yaml` pointing at the retired OSD hub; `flightctl login` replaces
  it (backup left as `client.yaml.bak-osd-hub-2026-09-08`).

`flightctl` 1.3.0 is installed at `~/.local/bin/flightctl` on the presenting laptop and the desktop
(sha256-verified from the GitHub release, os/arch from `uname`).

## Expectations

- `Repository` becomes `Accessible` as soon as GitHub answers.
- `ResourceSync` reaches `Synced` only when its path (`gitops/rhem/`, `gitops/rhem-catalog/`) exists **on the pushed branch**. Until
  the directory is committed and pushed it reports a path error; that is the expected state
  right after bootstrap, not a fault. An empty directory (README only) syncs cleanly — the sync
  reads `*.yaml`/`*.yml`/`*.json` and skips other files.
- If `flightctl get fleets` returns 403 after login, port thor-testing's RBAC workaround
  (`DEPLOYMENT_GUIDE.md:110-136`) into `gitops/rhem-config/rbac.yaml` with a matching
  `argocd/rhem-config-app.yaml`.
