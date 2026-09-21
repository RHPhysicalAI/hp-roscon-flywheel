<!-- This project was developed with assistance from AI tools. -->
# Operator configuration for the governed pipeline

Applied AFTER the operators in `gitops/operators/` reach `Succeeded` (Argo cannot sync kinds whose
CRDs don't exist yet). Owned by the Argo `operators-config` Application.

- `dsc.yaml` — OpenShift AI's `DSCInitialization` and `DataScienceCluster`, minimal: **Data Science
  Pipelines** (`aipipelines`; runs `pipeline/act_flywheel_pipeline.py`), the **dashboard** (the
  pipeline's run graph and the Model Registry's pages) and **Model Registry** (`modelregistry:
  Managed`, `registriesNamespace: rhoai-model-registries`) managed; KServe, workbenches, Ray, Kueue,
  the training operators, TrustyAI and the rest removed. Nothing is served through KServe here — the
  devices Red Hat Edge Manager manages serve the policy — and removing it also avoids its
  cert-manager prerequisite.
- `model-registry.yaml` — the registry itself: MariaDB (`rhel9/mariadb-1011`, PVC on
  `local-path`, password from the hand-created Secret `model-registry-db`) and the `ModelRegistry`
  CR `flywheel` (`modelregistry.opendatahub.io/v1beta1`, `mysql` backend, a kube-rbac-proxy Route), plus
  the RoleBinding that lets the DSP runner SA (`pipeline-runner-dspa`) write to it. Sync waves 2–3:
  the DSC (wave 1) must have created the namespace and the CRD first. The KFP `register_model`
  step records each promoted version here.
- `dspa.yaml` — `DataSciencePipelinesApplication` in the `flywheel` project; its object storage is the
  hub's own (`minio.minio.svc:9000`, bucket `pipelines`, creds from `hub-credentials`). The pipeline's
  Secrets (`hub-credentials`, `quay-push`, `cosign-signing-key`, `github-token`) live in the same
  namespace, which is why the DSPA is here and not in a separate project.
- `namespace-securesign.yaml` — the `trusted-artifact-signer` namespace, sync wave 0. On this arm64 hub
  the transparency log that lives in it (Rekor on Trillian; key-based cosign signing with
  `--rekor-url`) is brought up by hand from `tools/host/fury/rhtas-arm64/`, not by a CR in this
  directory.
