# Cluster operators for the governed pipeline (D022)

GitOps-managed OLM subscriptions. Owned by the Argo `operators` Application (`argocd/operators-app.yaml`
is applied once by hand to bootstrap; everything else syncs from here).

| operator | namespace | channel | why |
|---|---|---|---|
| `openshift-pipelines-operator-rh` | openshift-operators | latest | Tekton tasks reused inside the pipeline (package-modelcar, cosign-sign) |
| `rhtas-operator` | openshift-operators | stable | Red Hat Trusted Artifact Signer: Rekor transparency log for `cosign sign --rekor-url` |
| `rhods-operator` | redhat-ods-operator | stable | RHOAI: Data Science Pipelines (KFP v2) runs the pipeline; KServe serves the modelcar on the target |

The CRs that turn the operators on (`DataScienceCluster`, `Securesign`) live in `gitops/operators-config/`
and are applied after the CSVs reach `Succeeded`.
