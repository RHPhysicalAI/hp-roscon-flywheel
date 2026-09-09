# Operator configuration for the governed pipeline (D022)

Applied AFTER the operators in `gitops/operators/` reach `Succeeded` (Argo cannot sync kinds whose
CRDs don't exist yet). Owned by the Argo `operators-config` Application.

- `dsc.yaml` — RHOAI `DataScienceCluster`, minimal: **Data Science Pipelines** (runs
  `pipeline/act_flywheel_pipeline.py`) and **KServe** (managed but unused — the RHEM device serves the
  policy, D057) managed; dashboard, workbenches, Ray, Kueue, TrustyAI etc. removed; model registry
  arrives with Phase 4.5 E1. SNO headroom is
  fine but there's no reason to carry them.
- `dspa.yaml` — `DataSciencePipelinesApplication` in the `flywheel` project, object storage = our MinIO
  (`minio.minio.svc:9000`, bucket `pipelines`, creds from `hub-credentials`). The pipeline's
  Secrets (`hub-credentials`, `quay-push`, `cosign-signing-key`, `github-token`) live in the same
  namespace, which is why the DSPA is here and not in a separate project.
- `securesign.yaml` — RHTAS (Rekor/Fulcio/CTlog/TUF/Trillian). Written against the installed CRD
  schema; signing is key-based (cosign `--key`) with `--rekor-url` for the transparency log.
