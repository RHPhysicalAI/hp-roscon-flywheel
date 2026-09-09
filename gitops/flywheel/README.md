# gitops/flywheel — the hub-side flywheel workloads

Owned by the Argo `flywheel` Application (`argocd/flywheel-app.yaml`, `prune: true`).

## Hand-created Secret: `hub-credentials`

The S3 credentials every workload here (and the DSP pipeline, `gitops/operators-config/dspa.yaml`)
reads are **not in git** (Phase 4.5 F, D026 row 13). Argo does not track the Secret, so a sync or
prune never touches it. Create or refresh it from the desktop's `~/.minio-env` without echoing the
values:

```bash
set -a; source ~/.minio-env; set +a
oc create secret generic hub-credentials -n flywheel \
  --from-literal=s3-access-key="$MINIO_ACCESS_KEY" --from-literal=s3-secret-key="$MINIO_SECRET_KEY" \
  --dry-run=client -o yaml | oc apply -f -
```

Keys: `s3-access-key`, `s3-secret-key`. The values must match `minio-credentials` in the `minio`
namespace (`root-user`/`root-password`) — see `argocd/README.md` for the full hand-created list.
