# Cluster operators for the governed pipeline (D022)

GitOps-managed OLM subscriptions. Owned by the Argo `operators` Application (`argocd/operators-app.yaml`
is applied once by hand to bootstrap; everything else syncs from here).

| operator | namespace | channel | why |
|---|---|---|---|
| `openshift-pipelines-operator-rh` | openshift-operators | latest | Tekton builds + signs the multi-arch runtime image in-cluster (`gitops/tekton/`, Argo app `tekton`, D028); modelcar packaging + signing stay in the KFP run |
| `rhtas-operator` | openshift-operators | stable | Red Hat Trusted Artifact Signer: Rekor transparency log for `cosign sign --rekor-url` |
| `rhods-operator` | redhat-ods-operator | stable | RHOAI: Data Science Pipelines (KFP v2) runs the pipeline. KServe is enabled but unused — the RHEM-managed device serves the policy, not the cluster (D057). Model Registry (`modelregistry`, Phase 4.5 E1, planned) records each promoted version's digest, dataset and eval numbers |

The CRs that turn the operators on (`DataScienceCluster`, `Securesign`) live in `gitops/operators-config/`
and are applied after the CSVs reach `Succeeded`.

Not OLM, but part of the same platform plane and also Argo-owned:

| component | namespace | delivered by | why |
|---|---|---|---|
| RHEM hub (flightctl 1.3.0) | flightctl | `argocd/rhem-app.yaml` — Helm OCI chart `quay.io/flightctl/charts/flightctl:1.3.0` (D024) | Fleet-managed rollout of the signed modelcar to the device; the Fleet is the promotion PR's target (D025) |
| Model Registry (RHOAI component, planned — E1) | flywheel | `gitops/operators-config/dsc.yaml` (`modelregistry: Managed`) + `model-registry.yaml` | the durable version → digest/dataset/eval/Rekor join (D027) |
