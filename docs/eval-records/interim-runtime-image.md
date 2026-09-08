<!-- This project was developed with assistance from AI tools. -->
# Interim amd64 runtime image — built on the host, signed into Rekor (D024/D026/D028 bridge)

**Date:** 2026-09-08 · **Unblocks:** Phase 4.5 C (Fleet-delivered application) · **Replaced by:** Phase 4.5 F (Tekton multi-arch)

D028 makes Tekton the recorded build path for the runtime images. Item C needs a **signed,
digest-pinned** `act-inference` image *today*, before F exists. This record documents the one-off
host build that fills that gap so it is a dated interim, not drift: every field below is what the
Fleet pins until the Tekton multi-arch manifest replaces it.

| | |
|---|---|
| Tag | `quay.io/jary/soarm-flywheel:act-inference-amd64-2026-09-08` |
| Manifest digest | `sha256:29955e4e9422b194f950ba66d43689a2815a3670e5e0b398921d78501d5bb140` |
| Pin as | `quay.io/jary/soarm-flywheel@sha256:29955e4e9422b194f950ba66d43689a2815a3670e5e0b398921d78501d5bb140` |
| Platform | `linux/amd64` only (single manifest, `application/vnd.docker.distribution.manifest.v2+json`, 28 layers) |
| Local image id | `sha256:fe4dcfc77fa924f17256ddc12db27325601dec01c4b3374a49ff0354f784c117` (created 2026-09-08T16:45:59-05:00) |
| Rekor log index | **2** (uuid `06f19aeda42b56bde43a9e2985d5cf4e2a7b5743d2bb9ea2abe79bc8d6a2d50444595692f3257515`, `hashedrekord` 0.0.1, integrated 1788904702 = 2026-09-08T21:58:22Z, tree size after = 3) |
| Signature | `quay.io/jary/soarm-flywheel:sha256-29955e4e…5bb140.sig` (cosign attachment, bundle embedded) |
| Signing key | desktop `~/cosign/cosign.key` (same key as the KFP `cosign-signing-key` Secret; `cosign.pub` md5 matches `/etc/pki/containers/cosign.pub` on the device VM) |
| Rekor key | `/api/v1/log/publicKey` on the RHTAS route; md5 matches `/etc/pki/containers/rekor.pub` on the device VM |

## What is in the image

Same `docker/Dockerfile.gpu-inference` as the running `act-inference:latest` (id `69cb2c3fecdb`, built
~12:45 the same day), plus today's uncommitted Phase 4.5 C changes:

- `docker/inference-entrypoint.sh`: `POLICY_DEVICE` env (default `cuda`) replaces the hard-coded
  `policy_device:=cuda`; the old `sed` on `rosetta_client.yaml` is gone.
- `docker/healthcheck.sh` (new) + `HEALTHCHECK --interval=30s --timeout=10s --start-period=240s
  --retries=3 CMD ["/healthcheck.sh"]`: healthy only when `/flywheel/model_version` equals
  `$MODEL_VERSION` and `/run_policy` is on the graph.

`docker image inspect` confirms `Config.Healthcheck.Test = [CMD /healthcheck.sh]`, `Architecture = amd64`.

## Build

Host: desktop `10.0.0.48` (Ubuntu, docker 28 / buildkit v0.26.2, docker group, no sudo). Context is
the host's own checkout `~/redhat/git/hp-roscon-flywheel` (branch `desktop-gpu-split` @ `0827369`,
clean) — the same context that produced `act-inference:latest` (its `docker history` shows the
repo-root `COPY src/... ` and `COPY docker/...` steps). The three changed files were rsync'd from the
working tree into that checkout's `docker/` only (no `git pull` on the host):

```bash
rsync -av docker/ jary@10.0.0.48:redhat/git/hp-roscon-flywheel/docker/
ssh jary@10.0.0.48 'cd ~/redhat/git/hp-roscon-flywheel && docker build --platform linux/amd64 \
  -f docker/Dockerfile.gpu-inference \
  -t quay.io/jary/soarm-flywheel:act-inference-amd64-2026-09-08 . \
  > ~/build-logs/act-inference-amd64-2026-09-08.log 2>&1'
```

Build args: defaults only (`DISTRO=kilted`), identical to the running image. Timing: started
16:45:57 CDT, image written 16:45:59 — steps 1–11 (apt, upstream `demos` clone, `vcs import`,
`rosdep`, colcon, torch/lerobot pip, boto3/kafka) were all `CACHED` from the morning build; only the
seven `COPY` steps and the `chmod` re-ran. The `git clone --depth 1` of `ros-physical-ai/demos` is
therefore still the morning's snapshot, not a fresh fetch.

```bash
docker push quay.io/jary/soarm-flywheel:act-inference-amd64-2026-09-08   # 16:49:02 → 16:52:32
# act-inference-amd64-2026-09-08: digest: sha256:29955e4e9422b194f950ba66d43689a2815a3670e5e0b398921d78501d5bb140 size: 6196
~/bin/crane digest quay.io/jary/soarm-flywheel:act-inference-amd64-2026-09-08
# sha256:29955e4e9422b194f950ba66d43689a2815a3670e5e0b398921d78501d5bb140
```

No `:latest` tag was pushed or moved (the repo's pre-existing `latest` and `sim-only` tags are untouched).

## Sign + verify (RHTAS Rekor, `--tlog-upload=true`, no `--insecure-ignore-tlog`)

The Rekor route `rekor-server-trusted-artifact-signer.apps.sno-flywheel.local` is **not** in the
host's `/etc/hosts` (only the other SNO routes are, and there is no sudo), and the route serves the
cluster's ingress-operator CA. So cosign v2.6.5 (`~/bin/cosign`, statically linked) ran inside a
throwaway container on the host with `--add-host <route>:10.0.0.49`, `SSL_CERT_FILE` set to the
ingress CA (from `oc get secret router-ca -n openshift-ingress-operator` via
`~/sno-flywheel/auth/kubeconfig`), and registry auth from a 0600 temp docker config built from the
host's docker credential helper (deleted on exit). Script: `~/build-logs/sign-interim.sh` on the host.

```bash
# inside: docker run --rm --add-host $H:10.0.0.49 -e SSL_CERT_FILE=/ca/ca.crt -e COSIGN_PASSWORD= ... --entrypoint /cosign
cosign sign --key /keys/cosign.key -y --rekor-url https://$H --tlog-upload=true \
  quay.io/jary/soarm-flywheel@sha256:29955e4e9422b194f950ba66d43689a2815a3670e5e0b398921d78501d5bb140
# tlog entry created with index: 2
# Pushing signature to: quay.io/jary/soarm-flywheel                       (16:58:20–16:58:24 CDT)

SIGSTORE_REKOR_PUBLIC_KEY=/keys/rekor.pub \
cosign verify --key /keys/cosign.pub --rekor-url https://$H \
  quay.io/jary/soarm-flywheel@sha256:29955e4e9422b194f950ba66d43689a2815a3670e5e0b398921d78501d5bb140
# Verification for quay.io/jary/soarm-flywheel@sha256:29955e4e… --
#   - The cosign claims were validated
#   - Existence of the claims in the transparency log was verified offline
#   - The signatures were verified against the specified public key
# bundle: logIndex 2, integratedTime 1788904702, logID 37f4fa09cc7f385b…
```

Gotcha worth keeping (Phase 4.5 F will hit it in the Tekton verify step): with a custom
`--rekor-url`, cosign v2.6.5 `verify` still looks the Rekor log key up in the public sigstore TUF
root and fails with `rekor log public key not found for payload`. The fix is
`SIGSTORE_REKOR_PUBLIC_KEY=<file with /api/v1/log/publicKey>` — the same key the device's
`policy.json` uses as `rekorPublicKeyPath`. `--insecure-ignore-tlog` is *not* the fix.

Rekor entry (fetched from the route): `GET /api/v1/log/entries?logIndex=2` → kind `hashedrekord`,
payload hash `d0457f47344a636582395178c476e177083315a6bdb674b2966cecfeab2cb35a`; `GET /api/v1/log`
→ `treeSize: 3` (indices 0 and 1 are the D022 modelcar signatures).

## Device-side smoke test (`act-device` VM, 10.0.0.51, rootful podman 5.8.2, `policy.json` enforcing `sigstoreSigned` + `rekorPublicKeyPath` for `quay.io/jary/soarm-flywheel`)

The VM was shut off when the pre-signing pull was attempted (see *Sequence of events*), so the
"unsigned" leg uses the other image in the same repository, `sim-only`
(`sha256:06413b09f79dd1186d7b199ffa56b01d56c12f50ed2a4671fb299cac297a66e3`, never signed) — same
`policy.json` entry, same `signedIdentity: matchRepository`, same `registries.d` lookup.

**Unsigned image, same repo, same policy — rejected** (VM, 2026-09-08T22:11:28Z, VM booted 22:11:02Z):
```
$ sudo podman pull quay.io/jary/soarm-flywheel@sha256:06413b09f79dd1186d7b199ffa56b01d56c12f50ed2a4671fb299cac297a66e3
Trying to pull quay.io/jary/soarm-flywheel@sha256:06413b09f79dd1186d7b199ffa56b01d56c12f50ed2a4671fb299cac297a66e3...
Error: unable to copy from source docker://quay.io/jary/soarm-flywheel@sha256:06413b09f79dd1186d7b199ffa56b01d56c12f50ed2a4671fb299cac297a66e3: Source image rejected: A signature was required, but no signature exists
exit=125
```

**Signed interim digest — accepted:**
(VM, started 22:11:28Z, finished 2026-09-08T22:16:37Z — 11.4 GB from quay):
```
$ sudo podman pull quay.io/jary/soarm-flywheel@sha256:29955e4e9422b194f950ba66d43689a2815a3670e5e0b398921d78501d5bb140
Trying to pull quay.io/jary/soarm-flywheel@sha256:29955e4e9422b194f950ba66d43689a2815a3670e5e0b398921d78501d5bb140...
Getting image source signatures
Checking if image destination supports signatures
Writing manifest to image destination
Storing signatures
fe4dcfc77fa924f17256ddc12db27325601dec01c4b3374a49ff0354f784c117
$ sudo podman image inspect quay.io/jary/soarm-flywheel@sha256:29955e4e… --format 'id={{.Id}} arch={{.Architecture}} healthcheck={{.Config.Healthcheck.Test}}'
id=fe4dcfc77fa924f17256ddc12db27325601dec01c4b3374a49ff0354f784c117 arch=amd64 healthcheck=[CMD /healthcheck.sh]
```
"Getting image source signatures / Storing signatures" is podman fetching the cosign attachment,
checking it against `cosign.pub` and the embedded Rekor bundle against `rekor.pub`, then keeping the
signature locally. Same key, same repo, same policy as the rejected pull above — the only difference
is the Rekor-logged signature. The image id matches the host build (`fe4dcfc77fa9`).

## Sequence of events / deviations

- 16:45 build · 16:49–16:52 push · 16:5x pre-signing pull attempt: the VM was **`shut off`** in
  libvirt (`virsh list --all`), not crashed — it had answered at ~16:40. It was not started by this
  procedure (someone shut it down deliberately; D029-style snapshots need it off).
- Rekor was up while the SNO was at `4.18.54 → 4.19.44, 76% (waiting on console)`; the node reboot
  in that hop takes Rekor down, so signing went ahead at 16:58 rather than waiting for the VM.
  Consequence: the "before signing" pull of *this* digest could not be captured. The negative test is
  covered instead by pulling an unsigned image from the same repository under the same policy
  (`sim-only` by digest), which exercises the identical `sigstoreSigned` rule.

## Replacement

Phase 4.5 F replaces this with the Tekton-built multi-arch manifest (amd64 + arm64) signed in-cluster
(D028). When that lands: re-pin `gitops/rhem/fleet-act-inference.yaml` to the new manifest digest,
leave this tag and Rekor index 2 in place as history (do not delete the signature), and mark this
record superseded.

## 2026-09-08b — role split + chunk params (Phase 4.5 C3)

Same recipe, second interim build. Supersedes the digest above in the Fleet; index 2 stays in Rekor as history.

| | |
|---|---|
| Tag | `quay.io/jary/soarm-flywheel:act-inference-amd64-2026-09-08b` |
| Manifest digest | `sha256:2ad1fb1c393a6a5c5281abab83187d9e4aeecc05fdca8e12b7d247ced0009e11` |
| Pin as | `quay.io/jary/soarm-flywheel@sha256:2ad1fb1c393a6a5c5281abab83187d9e4aeecc05fdca8e12b7d247ced0009e11` |
| Platform | `linux/amd64` only (single manifest, `application/vnd.docker.distribution.manifest.v2+json`, 29 layers — one more `COPY`) |
| Local image id | `sha256:b21e9348a50089e3859f59adedbaaa1be2e9a746fc0cf504705292a3fcd53a3d` (created 2026-09-08T18:29:40-05:00) |
| Rekor log index | **3** (uuid `06f19aeda42b56bdc637632d152d957fb0695d330ae76f53c42336f549c78396dc1a9ec862c21541`, `hashedrekord`, payload hash `894deb6ddb615bf6470cd7a52331c7c135dfad3c025fd0bf3370f570d10d8fa6`, integrated 1788910239 = 2026-09-08T23:30:39Z, tree size after = 4) |
| Signature | `quay.io/jary/soarm-flywheel:sha256-2ad1fb1c…0009e11.sig` (cosign attachment, bundle embedded) |
| Signing / Rekor keys | unchanged (desktop `~/cosign/cosign.key`; `~/rekor-live.pub`, md5 `e34fa270…` = the device's `/etc/pki/containers/rekor.pub`) |

What changed vs. `29955e4e`: `docker/inference-entrypoint.sh` gains `ROLE=policy|coordinator|all` and
writes the rosetta client `params_file` (`policy_device`, `actions_per_chunk`, `chunk_size_threshold`
from env — the old `key:=value` launch args were not declared by `rosetta_client_launch.py` and were
ignored); new `src/inference-coordinator/model_version_pub.py` (latched `/flywheel/model_version` for
the policy role); `coordinator.py` observes instead of publishing the label in the coordinator role;
`docker/healthcheck.sh` sources ROS before `set -u` (the C2 `HealthCmd=` workaround is no longer needed).

Build: host checkout at `0827369` with `docker/` and `src/` rsync'd in from the working tree
(`rsync -a docker/ src/ …`); `docker build --platform linux/amd64 -f docker/Dockerfile.gpu-inference
-t quay.io/jary/soarm-flywheel:act-inference-amd64-2026-09-08b .` 18:29:39–18:29:41 CDT, 10 steps
`CACHED`, only the `COPY`/`chmod` layers re-ran; `docker push` 18:29:41–18:29:47 (all but the new
layers already in quay). Sign + verify: `~/build-logs/sign-interim-b.sh` (the same container recipe;
verify run with `SIGSTORE_REKOR_PUBLIC_KEY=/rekor.pub` mounted from `~/rekor-live.pub`), 18:30:38–18:30:41 CDT:
`tlog entry created with index: 3`; verify — cosign claims validated, transparency-log existence
verified offline, signature verified against the key. No `:latest` tag moved.
