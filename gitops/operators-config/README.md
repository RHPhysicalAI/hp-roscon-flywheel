# Operator configuration for the governed pipeline (D022)

Applied AFTER the operators in `gitops/operators/` reach `Succeeded` (Argo cannot sync kinds whose
CRDs don't exist yet). Owned by the Argo `operators-config` Application.

- `dsc.yaml` — RHOAI `DataScienceCluster`, minimal: **Data Science Pipelines** (runs
  `pipeline/act_flywheel_pipeline.py`), **KServe** (managed but unused — the RHEM device serves the
  policy, D057) and **Model Registry** (`modelregistry: Managed`, `registriesNamespace:
  rhoai-model-registries`, Phase 4.5 E1) managed; dashboard, workbenches, Ray, Kueue, TrustyAI etc.
  removed. SNO headroom is fine but there's no reason to carry them.
- `model-registry.yaml` — the registry itself (D027): MariaDB (`rhel9/mariadb-1011`, PVC on
  `local-path`, password from the hand-created Secret `model-registry-db`) and the `ModelRegistry`
  CR `flywheel` (`modelregistry.opendatahub.io/v1beta1`, `mysql` backend, OAuth-proxy Route), plus
  the RoleBinding that lets the DSP runner SA (`pipeline-runner-dspa`) write to it. Sync waves 2–3:
  the DSC (wave 1) must have created the namespace and the CRD first. The KFP `register_model`
  step records each promoted version here — `docs/eval-records/model-registry.md`.
- `dspa.yaml` — `DataSciencePipelinesApplication` in the `flywheel` project, object storage = our MinIO
  (`minio.minio.svc:9000`, bucket `pipelines`, creds from `hub-credentials`). The pipeline's
  Secrets (`hub-credentials`, `quay-push`, `cosign-signing-key`, `github-token`) live in the same
  namespace, which is why the DSPA is here and not in a separate project.
- `securesign.yaml` — RHTAS (Rekor/Fulcio/CTlog/TUF/Trillian). Written against the installed CRD
  schema; signing is key-based (cosign `--key`) with `--rekor-url` for the transparency log.
