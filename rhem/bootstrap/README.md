# rhem/bootstrap/ — flightctl API objects applied once

Fleets and CatalogItems are flightctl **API objects**, not Kubernetes CRs, so Argo CD cannot
apply them (D024). RHEM has its own GitOps loop for that: a `Repository` pointing at this repo
and a `ResourceSync` that renders everything in `gitops/rhem/` from branch `desktop-gpu-split`.
The two files here bootstrap that loop and are applied **once by hand** with `flightctl apply`,
the same role `argocd/*-app.yaml` plays for Argo.

| File | Object | What it does |
|---|---|---|
| `repository.yaml` | `Repository/hp-roscon-flywheel` | `type: git`, public HTTPS URL, no auth |
| `resourcesync.yaml` | `ResourceSync/rhem-fleets` | `type: fleet`, `path: gitops/rhem`, `targetRevision: desktop-gpu-split` |

Field names follow the flightctl 1.3 core OpenAPI (`GitRepoSpec.{type,url}`,
`ResourceSyncSpec.{type,repository,targetRevision,path}`).

## Apply

```bash
# token from the desktop: ssh -n jary@10.0.0.48 'export KUBECONFIG=~/sno-flywheel/auth/kubeconfig; oc whoami -t'
flightctl login https://api.flightctl.apps.sno-flywheel.local --token <token> --insecure-skip-tls-verify
flightctl apply -f rhem/bootstrap/repository.yaml
flightctl apply -f rhem/bootstrap/resourcesync.yaml
flightctl get repository,resourcesync
```

`flightctl` 1.3.0 is installed at `~/.local/bin/flightctl` on the Mac and the desktop
(sha256-verified from the GitHub release, os/arch from `uname`).

## Expectations

- `Repository` becomes `Accessible` as soon as GitHub answers.
- `ResourceSync` reaches `Synced` only when `gitops/rhem/` exists **on the pushed branch**. Until
  the directory is committed and pushed it reports a path error; that is the expected state
  right after bootstrap, not a fault. An empty directory (README only) syncs cleanly — the sync
  reads `*.yaml`/`*.yml`/`*.json` and skips other files.
- If `flightctl get fleets` returns 403 after login, port thor-testing's RBAC workaround
  (`DEPLOYMENT_GUIDE.md:110-136`) into `gitops/rhem-config/rbac.yaml` with a matching
  `argocd/rhem-config-app.yaml`.
