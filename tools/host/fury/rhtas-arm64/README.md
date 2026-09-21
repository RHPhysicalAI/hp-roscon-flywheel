<!-- This project was developed with assistance from AI tools. -->
# RHTAS 1.4.3 Rekor + Trillian, rebuilt for arm64

Red Hat Trusted Artifact Signer 1.4.3 ships its server images for amd64 only. This kit rebuilds the minimum the
flywheel needs - a Rekor log on Trillian, driven by the RHTAS operator - natively on the aarch64 host, from Red Hat's
public midstream source (`github.com/securesign`: `rekor`, `trillian` at `rhtas-v1.4.3`, `secure-sign-operator` at `v1.4.3`,
commits pinned in `build.sh`). **Not built by Red Hat, not supported, not the product**: same source and Dockerfiles, a
different builder. No Fulcio, CT log, TUF, TSA or search UI (signing is key-based); `createtree` and `ose-tools` stay Red Hat's.

## Order of operations
1. Cluster registry (platform `none` leaves it Removed): `oc apply -f registry.yaml`, then the `oc patch` in its header.
   That file also creates the `rhtas-arm64` project, the `pusher` account and the pull rights of steps 2 and 3. On a
   single node, switching the registry on rolls the API server once: expect a few minutes of API errors.
2. Wait for `oc get co` to settle and for `default-route` in `openshift-image-registry` to answer 401 on `/v2/`.
3. On the host, as an ordinary user: `./build.sh bases` with a `registry.redhat.io` login (the database and redis
   bases; drop the login again afterwards), and `podman login` on the registry route with a short-lived token
   (`oc -n rhtas-arm64 create token pusher --duration 2h`, a ServiceAccount holding `system:image-builder`). Trust the route with ConfigMap
   `default-ingress-cert` (`openshift-config-managed`) as `~/.config/containers/certs.d/<route>/ca.crt`, or `TLS_VERIFY=false`.
4. `export REGISTRY=default-route-openshift-image-registry.apps.sno-flywheel.local/rhtas-arm64` and
   `PULL_REGISTRY=image-registry.openshift-image-registry.svc:5000/rhtas-arm64`; `./build.sh all`, `push`, `manifests`.
   (Private quay.io repositories plus a pull secret also work; the database and redis images must never be public.)
5. `oc apply --server-side -f ~/rhtas-arm64-build/operator-install.yaml`: CRDs, RBAC and the manager in
   `openshift-rhtas-operator`, no OLM. Scale `cli-server` to 0 (`related-images.tmpl` says why).
6. The signer Secret as described in `rekor-trillian.yaml`, `oc apply -f rekor-trillian.yaml`, wait for
   `oc -n trusted-artifact-signer get trillian,rekor` to report Ready.
7. `curl -k https://rekor-server-trusted-artifact-signer.apps.sno-flywheel.local/api/v1/log/publicKey` goes, byte for
   byte, into `gitops/tekton/rekor-public-key.yaml` and the Fleet's inline `rekor.pub` - unless the old signer key was reused.

## What was verified (2026-09-19, aarch64 host, OpenShift 4.22 single node)
All six images build natively with rootless podman in about four and a half minutes together (rekor-server 2 min 7 s,
logserver 58 s, logsigner 31 s, operator 42 s, database and redis seconds each; 11 GB of rootless store). The operator
runs outside OLM from `operator-install.yaml`, pulls from the cluster registry, and brings the standalone `Trillian` and
`Rekor` CRs to Ready in about 90 seconds on the node-local provisioner. A `cosign` v2.6.5 `sign-blob` from a pod created
log entry 0 and `verify-blob` passed against the log's own public key. `cli-server` stays at 0 once scaled down.
Not exercised: the search UI, backfill, monitoring, a restore of the log from its volumes.

## Fallback
Not needed so far. Should the rebuilt images stop being workable, deploy the upstream sigstore `rekor` Helm chart (plus its `trillian` chart; images are multi-arch except
Trillian's default `db_server`, which needs an arm64 MySQL). Keep Service `rekor-server` port 80 and the Route host.
