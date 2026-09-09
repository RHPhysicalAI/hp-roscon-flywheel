<!-- This project was developed with assistance from AI tools. -->
# Runtime image built + signed in-cluster by Tekton, multi-arch (Phase 4.5 F, D028)

**Date:** 2026-09-09 · **Supersedes:** `interim-runtime-image.md` (D045 host builds, Rekor 2 and 3 stay as history) · **Closes:** D026 rows 3 and 12 for the runtime image, BUILD-PLAN "torch aarch64" flag

The ACT runtime image (`docker/Dockerfile.gpu-inference`) is now built for `linux/amd64` and
`linux/arm64` by OpenShift Pipelines on the SNO, pushed as one manifest list under a git-sha
tag, signed recursively into the in-cluster RHTAS Rekor and verified with the log key — the same
key and policy the device enforces. The Fleet pins the manifest-list digest.

| |
|---|---|
| Argo app | `tekton` (`argocd/tekton-app.yaml` → `gitops/tekton/`, ns `flywheel`, prune + selfHeal) |
| PipelineRun | `runtime-image-a4` (attempt 4; attempts 1–3 below) |
| Source | `aa7392f` on `desktop-gpu-split` (public GitHub, `git-clone` cluster Task, depth 1) |
| Tag | `quay.io/jary/soarm-flywheel:act-inference-aa7392f` (no `:latest`) |
| Manifest-list digest | `sha256:3d67f4246fd0915278b419bf4a3c67c3c9e0f8a07c553305a7445f65fc2cb4af` |
| Pin as | `quay.io/jary/soarm-flywheel@sha256:3d67f4246fd0915278b419bf4a3c67c3c9e0f8a07c553305a7445f65fc2cb4af` |
| Platform digests | `linux/amd64` `sha256:af06b9892325d2f11b5255e1a97d979d38710769c3ab5aab1438567d731c1d86` · `linux/arm64` `sha256:be0004b1edd429ad4f6b603a014c0b8f976f8d4a19382369029d4fd3a2c4431a` (both `application/vnd.docker.distribution.manifest.v2+json`, 11 layers, `HEALTHCHECK` present; the index itself is `application/vnd.oci.image.index.v1+json`) |
| Rekor indexes | **11** (list), **12** (arm64), **13** (amd64) — `hashedrekord` 0.0.1, integrated 16:35:34Z / :38Z / :45Z, tree size after = 14 (list digest: **11**) |
| cosign | v2.6.5 (sha256-verified release binary, see F-T3 in the decisions draft) |
| Fleet | `gitops/rhem/fleet-act-inference.yaml` `Image=` re-pinned in commit `9e982c0` |

## Timings

| Task / leg | Start (UTC) | End (UTC) | Duration |
|---|---|---|---|
| git-clone | 15:25:55Z | 15:26:07Z | 12 s |
| build-and-push (both arches, sequential: arm64 first, then amd64, then push) | 15:26:07Z | 16:35:26Z | **69.3 min** |
| — arm64 leg under qemu-user (STEP 1 → 30) | 15:26:12Z | 16:19:09Z | 53.0 min (steps 1–9 apt/clone/vcs/rosdep 22.4 min · colcon 16.1 min · torch + lerobot pip 13.6 min · rest < 1 min) |
| — amd64 leg (native) | 16:20:07Z | 16:26:23Z | 6.3 min (steps 1–9 3.2 min · colcon 1.4 min · pip 1.6 min) |
| — `buildah manifest push --all` | 16:27:21Z | 16:35:20Z | 8.0 min (both platform images + index to quay) |
| sign (fetch-cosign, sign, verify) | 16:35:26Z | 16:35:48Z (sign) + verify re-run 16:53:37Z → 16:53:48Z | sign 12 s for three digests; verify 11 s |
| **PipelineRun total** | 15:25:55Z | 16:35:48Z | **69.9 min** to a signed, pushed image (the verify step is covered by the re-run below) |

## The torch aarch64 flag

`https://download.pytorch.org/whl/cu130/torch/` lists `torch-2.9.1+cu130-cp3{10,11,12,13,313t,14,314t}-manylinux_2_28_aarch64.whl`,
and the same index has `torchvision-0.24.1-…-manylinux_2_28_aarch64.whl` and
`torchaudio-2.9.1-…-manylinux_2_28_aarch64.whl` — **without** the `+cu130` local version that the
x86 wheels (and the Dockerfile's pins) carry. Ubuntu 24.04 (`ros:kilted`) ships glibc 2.39 ≥ 2.28.
So: the wheels exist and CUDA 13 aarch64 is served from the same index, but the version strings
differ per arch (attempt 3 proved it: `No matching distribution found for torchaudio==2.9.1+cu130`).
The Dockerfile now takes `TORCH_INDEX` and `TORCH_SPEC` build args, with `TARGETARCH`-derived
defaults `TORCH_SPEC_AMD64` / `TORCH_SPEC_ARM64`. The arm64 leg's pip step in attempt 4 is the
proof: `torch spec (arm64): torch==2.9.1+cu130 torchaudio==2.9.1 torchvision==0.24.1 from https://download.pytorch.org/whl/cu130` followed by `Successfully installed …` (STEP 16/30, 16:04:42Z → 16:18:19Z under qemu).

## What runs where

- `gitops/tekton/qemu-binfmt.yaml` — DaemonSet in `flywheel`: init container
  `docker.io/tonistiigi/binfmt@sha256:400a4873…` (qemu v10.2.3) `--install arm64`, privileged, own
  SA `qemu-binfmt` with the privileged SCC; holder container `ubi9/ubi-minimal@sha256:34880b64…`.
  Node: `/proc/sys/fs/binfmt_misc/qemu-aarch64` → `interpreter /usr/bin/qemu-aarch64`, `flags: POCF`.
  Pod delete → new pod's init logs `installing: arm64 OK` again (idempotent).
- `gitops/tekton/pipeline-sa-scc.yaml` — RoleBinding `pipeline` SA → `system:openshift:scc:privileged`
  (thor D009). Build pod annotation `openshift.io/scc: privileged` (checked on attempt 1).
- `gitops/tekton/buildah-cross-arch-task.yaml` — `registry.redhat.io/rhel9/buildah@sha256:61eccd0b…`
  (buildah 1.43.2, the operator's own pin), privileged step, native overlay storage in
  `<workspace>/.buildah` via a generated `storage.conf`, `buildah bud --platform
  linux/amd64,linux/arm64 --manifest <image> --jobs 1`, `buildah manifest push --all --digestfile`.
- `gitops/tekton/cosign-sign-task.yaml` — ubi9-minimal pinned + cosign v2.6.5 checked against
  `cosign_checksums.txt`; `cosign sign --key … --rekor-url http://rekor-server.trusted-artifact-signer.svc
  --tlog-upload=true --recursive=true -y`; `cosign verify --key cosign.pub --rekor-url …` with
  `SIGSTORE_REKOR_PUBLIC_KEY=<ConfigMap rekor-public-key>/rekor.pub` (never the tlog bypass flag).
- `gitops/tekton/rekor-public-key.yaml` — the Rekor log key (md5 `e34fa270…`, identical to the
  Fleet's `/etc/pki/containers/rekor.pub` and to the live `/api/v1/log/publicKey`).
- `gitops/tekton/runtime-image-pipeline.yaml` + `runtime-image-pipelinerun.example.yaml` — the
  Pipeline and the `generateName` run template (Argo excludes `*.example.yaml`). One 60 Gi
  local-path PVC per run (Tekton's affinity assistant allows one PVC workspace per TaskRun);
  `quay-push` projected as `config.json`; `cosign-signing` Secret (hand-created, `argocd/README.md`).
- Desktop: `~/.local/bin/tkn` 0.46.0 (GitHub release, `checksums.txt` verified).

## Attempt 1 — `runtime-image-sfgmc` (12:47:31Z → 12:48:01Z, failed)

`FROM ros:kilted`: `short-name resolution enforced but cannot prompt without a TTY` for both
platforms. buildah on the rhel9 image enforces short names; fixed by qualifying the base image
in the Dockerfile (`docker.io/library/ros:${DISTRO}`, commit `12cbc83`). Run deleted (PVC released).

## Attempt 2 — `runtime-image-2fh8n` (12:51:32Z → 13:17:54Z, failed at the colcon step, both arches)

Exit 127 at `RUN source /opt/ros/$ROS_DISTRO/setup.bash && colcon build …`: buildah builds in
**OCI** format by default and the OCI image spec has no `SHELL` (or `HEALTHCHECK`), so the
Dockerfile's `SHELL ["/bin/bash", "-c"]` was ignored and `/bin/sh` has no `source`. Fixed with a
`FORMAT` Task param defaulting to `docker` (commit `2f9e174`); docker format is also what keeps
the `HEALTHCHECK` the Fleet's quadlet relies on. Useful timing from this run: the arm64 leg went
STEP 1 → 9 (apt upgrade, `demos` clone, `vcs import`, `rosdep install`) under qemu in ~18 min
(12:51:49Z → ~13:10Z); buildah with `--jobs 1` built the platforms **sequentially, arm64 first**.
Run deleted (PVC released).

## Attempt 3 — `runtime-image-hl7l8` (14:22:53Z → 15:23:53Z, arm64 failed at the torch step; amd64 built all 25 steps)

Submitted during the Multus stale-token incident (13:45Z–14:34Z, cluster-wide): the local-path
`helper-pod-create-pvc-*` could not get a network sandbox, so the PVC stayed Pending and the run
sat 14 min until the operator recreated the multus pod (14:34:41Z); the helper then Completed on
its own retry and git-clone started at 14:36:50Z. Timestamps from the build pod log:

| Leg | Step | Start | End | Duration |
|---|---|---|---|---|
| arm64 (qemu) | 1–9 FROM → `rosdep install` | 14:36:55Z | 14:59:36Z | 22.7 min |
| arm64 (qemu) | 10 `colcon build` | 14:59:36Z | 15:15:58Z | 16.4 min |
| arm64 (qemu) | 11 torch pip | 15:15:58Z | 15:23:49Z (failed) | 7.9 min |
| amd64 (native) | 1–9 | 15:16:04Z | 15:19:22Z | 3.3 min |
| amd64 (native) | 10 `colcon build` | 15:19:22Z | 15:20:46Z | 1.4 min |
| amd64 (native) | 11 torch pip | 15:20:46Z | 15:22:26Z | 1.7 min |
| amd64 (native) | 12–25 | 15:22:26Z | 15:22:31Z | — |

(buildah started the amd64 leg after the arm64 leg had failed, then reported both.) The arm64
error: `No matching distribution found for torchaudio==2.9.1+cu130 (from versions: 2.2.0,
2.9.0, 2.9.1, 2.10.0, 2.10.0+cu130, 2.11.0+cu130)`. On the cu130 index the aarch64 wheels for
`torchaudio` 2.9.1 and `torchvision` 0.24.1 carry **no** `+cu130` local version (only `torch`
does), so the amd64 pins do not resolve on arm64. Fixed as the BUILD-PLAN flag foresaw: the
Dockerfile takes `TORCH_INDEX` / `TORCH_SPEC` build args with `TARGETARCH`-derived defaults
(`TORCH_SPEC_AMD64` = the three `+cu130` pins, `TORCH_SPEC_ARM64` = `torch==2.9.1+cu130
torchaudio==2.9.1 torchvision==0.24.1`), commit `aa7392f`. Run deleted (PVC released).

## Attempt 4 — `runtime-image-a4` (15:25:54Z → 16:35:48Z; build + push + sign succeeded, verify step failed on a missing `xargs`)

git-clone 12 s; build-and-push 69.3 min (table above); `Pushed quay.io/jary/soarm-flywheel:act-inference-aa7392f -> sha256:3d67f424…` at 16:35:20Z.
The `sign` step signed all three digests (`--recursive`, Rekor 11/12/13, signatures pushed at
16:35:34–16:35:45Z) and then exited 127 on `xargs: command not found` — ubi-minimal has no
`xargs`; the index list is now joined in bash (commit `7bf70fb`), and the Task gained `SIGN=false`
(verify-only) so an already-signed digest is never re-signed. Two reporting bugs found the same
way and fixed for the next run (commit `9d7013e`): `PLATFORM_DIGESTS` carried the *local*
pre-push instance digests (`b3b2ce60…`/`34be6793…`, unknown to quay — layers are compressed on
push, and buildah 1.43 cannot `manifest inspect` a remote reference: "unsupported transport
docker"), so the Task now reads the pushed index over the registry API; and `--tls-verify` is a
subcommand flag, not a global one.

Verify re-run: `TaskRun cosign-verify-3d67f424` (Task `cosign-sign`, `SIGN=false`, same
workspaces), 16:53:37Z → 16:53:48Z, **Succeeded**, results `REKOR_INDEX=11`, `COSIGN_VERSION=v2.6.5`.

## Sign + verify (in-cluster)

```
[sign]   Signing quay.io/jary/soarm-flywheel@sha256:3d67f424… (recursive=true) -> http://rekor-server.trusted-artifact-signer.svc
         tlog entry created with index: 11   Pushing signature to: quay.io/jary/soarm-flywheel
         tlog entry created with index: 12   Pushing signature to: quay.io/jary/soarm-flywheel
         tlog entry created with index: 13   Pushing signature to: quay.io/jary/soarm-flywheel
[verify] Verifying quay.io/jary/soarm-flywheel@sha256:3d67f424… against cosign.pub + Rekor key /workspace/rekor-public-key/rekor.pub
         Verification for quay.io/jary/soarm-flywheel@sha256:3d67f424… --
         The following checks were performed on each of these signatures:
           - The cosign claims were validated
           - Existence of the claims in the transparency log was verified offline
           - The signatures were verified against the specified public key
         Verified: bundle logIndex=11
```

Pass/fail of the verify step is the cosign exit code (0), never the message text (over the
plain-HTTP in-cluster Service a missing log entry surfaces as exit 12 with an undecodable body).
Rekor entries fetched from the route: `logIndex=11` uuid `06f19aeda42b56bdec92c7e6…` payload hash
`b0b44210d7b2f1a7…`; `12` uuid `06f19aeda42b56bd97293524…` hash `2e4b2f6f07300240…`; `13` uuid
`06f19aeda42b56bd974192f1…` hash `dbbc485bda332721…`; `GET /api/v1/log` → `treeSize: 14`.

## Registry evidence (`crane` 0.20)

```
$ crane manifest quay.io/jary/soarm-flywheel@sha256:3d67f4246fd0915278b419bf4a3c67c3c9e0f8a07c553305a7445f65fc2cb4af
application/vnd.oci.image.index.v1+json
  linux/arm64 sha256:be0004b1edd429ad4f6b603a014c0b8f976f8d4a19382369029d4fd3a2c4431a 2067
  linux/amd64 sha256:af06b9892325d2f11b5255e1a97d979d38710769c3ab5aab1438567d731c1d86 2067
$ crane digest quay.io/jary/soarm-flywheel:act-inference-aa7392f
sha256:3d67f4246fd0915278b419bf4a3c67c3c9e0f8a07c553305a7445f65fc2cb4af
$ crane config …@sha256:af06b989…   → amd64 linux, Healthcheck {Test: [CMD /healthcheck.sh], Interval 30s, Timeout 10s, StartPeriod 240s, Retries 3}, Entrypoint [/entrypoint.sh]
$ crane config …@sha256:be0004b1…   → arm64 linux, same Healthcheck / Entrypoint
signature attachments: sha256-3d67f424….sig → bundle logIndex 11 · sha256-be0004b1….sig → 12 · sha256-af06b989….sig → 13
```

## Device evidence (`act-device` VM 10.0.0.51, rootful podman 5.8.2, `policy.json` `sigstoreSigned` + `rekorPublicKeyPath`)

Pull by the **manifest-list** digest under the Fleet-written policy (podman resolves the amd64
instance `af06b989…` and verifies *its* cosign attachment — Rekor 13 — against `cosign.pub` and
the embedded bundle against `rekor.pub`; this is why the Task signs `--recursive`):

```
$ sudo podman pull quay.io/jary/soarm-flywheel@sha256:3d67f4246fd0915278b419bf4a3c67c3c9e0f8a07c553305a7445f65fc2cb4af   # 16:54:21Z → 16:59:38Z
Copying blob …  Copying config sha256:1c8c4fa683ce…
Writing manifest to image destination
Storing signatures
1c8c4fa683ce7d400be7b1348144a8855582aab1deca43971285585863350e97        exit=0
$ sudo podman image inspect … --format 'id={{.Id}} arch={{.Architecture}} healthcheck={{.Config.Healthcheck.Test}} size={{.Size}}'
id=1c8c4fa683ce7d400be7b1348144a8855582aab1deca43971285585863350e97 arch=amd64 healthcheck=[CMD /healthcheck.sh] size=11349408666
$ sudo podman run --rm --entrypoint /bin/bash … -c 'source /opt/ros/kilted/setup.bash && ros2 --help | head -2 && ls -l /healthcheck.sh /entrypoint.sh /ws_pai/model_version_pub.py && grep -c ROLE /entrypoint.sh && python3 -c "import torch, torchvision, torchaudio; print(torch.__version__, torchvision.__version__, torchaudio.__version__, torch.cuda.is_available())"'
usage: ros2 [-h] [--use-python-default-buffering] …
-rwxr-xr-x. 1 root root 5896 Sep  9 15:26 /entrypoint.sh
-rwxr-xr-x. 1 root root 1726 Sep  9 15:26 /healthcheck.sh
-rwxr-xr-x. 1 root root 1176 Sep  9 15:26 /ws_pai/model_version_pub.py
7                                   # ROLE= handling present in the entrypoint (D057)
2.9.1+cu130 0.24.1+cu130 2.9.1+cu130 False   # CUDA false: CPU stand-in device (D024)
```

## Fleet re-pin and rollout

Commit `9e982c0` (pushed 17:00:24Z) changes only the runtime `Image=` line (+ its comment) in
`gitops/rhem/fleet-act-inference.yaml`: `…@sha256:2ad1fb1c…` (D045 interim) → `…@sha256:3d67f424…`.
`MODEL_VERSION: act-v2-ft160` and the modelcar `Image=` line are untouched.

| Event | Time (UTC) | Δ from push |
|---|---|---|
| ResourceSyncs `rhem-fleets` / `rhem-catalog` observe `9e982c0`, Synced | ≤ 17:02:32Z | ≤ +2:08 |
| Device `renderedVersion` 6 → **7**, `updated: UpToDate` | ≤ 17:02:32Z | ≤ +2:08 |
| `act-inference` container started on `1c8c4fa683ce` (= `…@sha256:3d67f424…`) | 17:02:22Z | +1:58 |
| `applicationsSummary: Healthy`, podman `(healthy)`, `Published model_version: act-v2-ft160` | ≤ 17:04:33Z | ≤ +4:09 |
| Still `Healthy`, `Up 20 minutes (healthy)`, `Published model_version: act-v2-ft160` | 17:22:38Z | +22:14 |

The image had been pulled on the VM by the pre-check above, so the roll spent no time in the
11 GB pull; a cold device would add ~5 min. The `Degraded` seen at 17:02:32Z is the healthcheck's
240 s start-period, not a failure.

## Node disk

`/var` on the SNO: 87 G free before the run, 62 G minimum observed during the build,
197 G (the build step emptied its storage **and** the deleted attempt-2/3 PipelineRuns' PVCs — whose `helper-pod-delete-pvc-*` had been blocked by the Multus incident — were reclaimed in the same window; a failed build leaves its overlay storage on the PVC until the PipelineRun is deleted) after the build step emptied its storage.
