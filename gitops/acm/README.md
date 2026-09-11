# ACM (Red Hat Advanced Cluster Management) — the integrated-console RHEM path

**Status: PREPARED, NOT APPLIED — blocked on node CPU (D135).**

The operator asked to run RHEM the product way — fleets/devices viewed inside the OpenShift
console — rather than the standalone `flightctl` UI route we have today. That integrated view
comes from ACM's **edge-manager** component plus the `flightctl` console plugin (this is how
thor-testing surfaced RHEM in-console; its ACM ran on a multi-node OSD hub with ample resources).

## Why it isn't applied

On 2026-09-11 the SNO was at **87% CPU requested** (13.5 of 15.5 cores; ~2 cores free) after the
RHOAI dashboard was enabled (D134). ACM's MultiClusterHub base needs **~4+ CPU cores**; it does not
fit on the current 16-vCPU VM. Installing it would leave MCH components `Pending` and risk
starving the running demo (RHOAI, RHTAS, pipelines, MinIO, Kafka, the flywheel). So nothing here
was applied — these manifests are staged and reproducible for when the node has room.

Top CPU requesters at baseline: `redhat-ods-applications` 3410m, `flightctl` 2412m,
`openshift-gitops` 1875m, `flywheel` 1615m.

## Operator action required first

Increase the SNO VM's vCPU (e.g. 16 → 24) and restart it, so the node has ~10 cores free for the
ACM hub base + edge-manager. This is a `virsh`/host-sudo + SNO-reboot step (operator-only). Note
the standalone `flightctl` (~2.4 cores) can be retired *after* the ACM edge-manager is proven
(a later migration phase: re-enroll `act-device` to ACM's flightctl endpoint, recreate the Fleet
and Catalog there), recovering some of that — but during migration both coexist, so the peak
still needs the larger node.

## Apply order (after the resize)

1. `oc apply -f gitops/acm/operator.yaml` (or `oc apply -f argocd/acm-app.yaml` to let Argo own it);
   wait for CSV `advanced-cluster-management.v2.17.x` → Succeeded.
2. Validate component names against the installed CRD:
   `oc explain multiclusterhub.spec.overrides.components` — confirm the edge-manager component name
   (2.13 docs: `edge-manager-preview`, Tech Preview) before applying the MCH.
3. `oc apply -f gitops/acm/multiclusterhub.yaml`; watch it reconcile, watching node CPU the whole
   time. Trim more components if it's tight.
4. Enable the console plugin:
   `oc patch console.operator.openshift.io cluster --type json -p '[{"op":"add","path":"/spec/plugins/-","value":"flightctl-plugin"}]'`
   (confirm the plugin name from the ConsolePlugin the edge-manager component creates).
5. Verify: `oc get console.operator cluster -o jsonpath='{.spec.plugins}'` includes the plugin;
   the ACM `flightctl-api` pod is Running in `open-cluster-management`; the edge-manager route exists.
6. Then (separate phase): re-enroll `act-device` to ACM's flightctl endpoint and recreate the
   Fleet/Catalog; only after that's Healthy, retire the standalone `argocd/rhem-app.yaml`.
