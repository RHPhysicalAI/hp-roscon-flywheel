<!-- This project was developed with assistance from AI tools. -->
# gitops/flywheel — the hub-side flywheel workloads

Owned by the Argo `flywheel` Application (`argocd/flywheel-app.yaml`, `prune: true`).

## The live lane: `curator-show`

`curator-show.yaml` is a second Deployment of the curator's own code (the `curator-code` ConfigMap, one code, two
deployments) that judges robot zero's episodes for the flywheel page while the demo stays in tenants mode.
It reads **no** Secret and has no way to the flywheel's data: its own PVC instead of the node's
`/var/lib/episodes`, its own ServiceAccount without an SCC grant (the pod requires `restricted-v2`, which has no
host paths), no token, no egress. Two pages mount its volume, both read-only: the dashboard, and
`eval-dashboard-show.yaml` - the live lane's own episodes page, a third instance of the evaluation dashboard image
that sees only `curated/` and `rejected/`, has no credential and no egress, and says on the page that it is not the
collection's record (`eval-dashboard-live`, which never reads this volume). Its NodePort is 30812; the
flywheel's curator keeps 30802. Only the GPU host (`10.20.0.1`) is let in to that port (NetworkPolicy, with the
Service's `externalTrafficPolicy: Local` keeping the client's address). Both curators carry the same
`CURATOR_CODE_REV` and are bumped together. `tests/hub/test_show_lane.py` holds all of that.

## Hand-created Secret: `hub-credentials`

The S3 credentials every workload here (and the DSP pipeline, `gitops/operators-config/dspa.yaml`)
reads are **not in git**. Argo does not track the Secret, so a sync or
prune never touches it. Create or refresh it without echoing the values - `MINIO_ACCESS_KEY` and
`MINIO_SECRET_KEY` are set in the shell beforehand, from wherever the operator keeps them:

```bash
oc create secret generic hub-credentials -n flywheel \
  --from-literal=s3-access-key="$MINIO_ACCESS_KEY" --from-literal=s3-secret-key="$MINIO_SECRET_KEY" \
  --dry-run=client -o yaml | oc apply -f -
```

Keys: `s3-access-key`, `s3-secret-key`. The values must match `minio-credentials` in the `minio`
namespace (`root-user`/`root-password`) — see `argocd/README.md` for the full hand-created list.
