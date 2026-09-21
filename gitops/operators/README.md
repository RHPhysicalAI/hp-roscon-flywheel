<!-- This project was developed with assistance from AI tools. -->
# Cluster operators for the governed pipeline

GitOps-managed OLM subscriptions. Owned by the Argo `operators` Application (`argocd/operators-app.yaml`
is applied once by hand to bootstrap; everything else syncs from here).

| operator | namespace | channel | why |
|---|---|---|---|
| `openshift-pipelines-operator-rh` | openshift-operators | latest | Tekton builds + signs this project's images in-cluster (`gitops/tekton/`, Argo app `tekton`); the model image's packaging + signing stay in the KFP run |
| `rhods-operator` | redhat-ods-operator | stable-3.5 | Red Hat OpenShift AI 3.5 (on the 4.22 catalog plain `stable` still resolves to 2.25): Data Science Pipelines (KFP v2) runs the pipeline. KServe is removed — the devices Red Hat Edge Manager manages serve the policy, not the cluster. Model Registry (`modelregistry` Managed) records each promoted version's digest, dataset, eval numbers, Rekor index and PR URL |

The CRs that turn OpenShift AI on (`DSCInitialization`, `DataScienceCluster`) live in `gitops/operators-config/`
and are applied after the CSVs reach `Succeeded`.

Not in this directory:

| component | namespace | delivered by | why |
|---|---|---|---|
| OpenShift GitOps, Cluster Observability, Tempo, OpenShift Dev Spaces | openshift-operators | `argocd/bootstrap-operators.yaml`, applied by hand before the first Application | Argo CD has to exist before it can own anything; the other three are wanted before their apps sync |
| Red Hat Edge Manager 1.3.0 | flightctl | `argocd/rhem-app.yaml` — Red Hat's Helm chart `redhat-rhem` 1.3.0 from `https://charts.openshift.io` | Fleet-managed rollout of the signed model image to the devices; the Fleets are the promotion PR's target |
| Transparency log (Rekor on Trillian) | trusted-artifact-signer | by hand, from `tools/host/fury/rhtas-arm64/` — not through OLM | the Red Hat Trusted Artifact Signer operator and its server images are offered for amd64 only, so on this arm64 hub the log is a rebuild of the public 1.4.3 source; signing is key-based cosign with `--rekor-url` |
| Model Registry (OpenShift AI component) | rhoai-model-registries | `gitops/operators-config/dsc.yaml` (`modelregistry: Managed`) + `model-registry.yaml` (MariaDB, `ModelRegistry` CR `flywheel`, runner RoleBinding, NetworkPolicy); Route `flywheel-rest.apps.sno-flywheel.local` | the durable version → digest/dataset/eval/Rekor/PR join, written by the KFP `register_model` step |
