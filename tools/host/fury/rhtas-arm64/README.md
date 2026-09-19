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

## Known and not known
Read at the tags, not run: every Dockerfile and its bases (pinned digests all include arm64; the Go builder pulls
anonymously from `registry.access.redhat.com`, `rhel9/mariadb-105` and `rhel9/redis-6` only from `registry.redhat.io` with
a login; nothing installs packages, so no entitlement), the 17 `RELATED_IMAGE_*` and where each is used, standalone
`Trillian`/`Rekor` CRs, the signer and Route logic, and that `config/default` renders with `oc kustomize` alone.
**Unverified until run**: that each image builds and runs on aarch64 (Red Hat builds them for x86_64 only), disk space for
rootless podman, the registry and MariaDB on local-path, the operator outside OLM on 4.22, a real `cosign` round trip.

## Fallback
Time box: one day. If Rekor is not Ready and taking a pipeline `cosign sign` by then - or one image eats over two hours -
stop and deploy the upstream sigstore `rekor` Helm chart (plus its `trillian` chart; images are multi-arch except
Trillian's default `db_server`, which needs an arm64 MySQL). Keep Service `rekor-server` port 80 and the Route host.
