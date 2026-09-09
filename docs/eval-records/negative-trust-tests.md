<!-- This project was developed with assistance from AI tools. -->
# Negative trust tests — the device's `policy.json` rejects unsigned *and* signed-but-unlogged images (Phase 4.5 C, D026 row 1–2)

**Date:** 2026-09-09 (desktop 11:12–11:13 CDT; device pulls 16:13:29–16:13:34Z). **State: all three
cases observed on the device; Phase 4.5 exit line "Negative test" met.**

The Phase 4.5 exit criterion asks for two rejections and one acceptance under the *same* policy entry:
an unsigned tag must fail with a signature error; a tag signed with the flywheel key but **without**
`--tlog-upload` must also fail (the Rekor SET is enforced, not just the key); a tag signed with the key
*and* logged in RHTAS Rekor pulls. The first and third were recorded in
[`interim-runtime-image.md`](interim-runtime-image.md) on 2026-09-08; this record adds the second and
re-runs the other two in the same minute so the three verdicts come from one policy file, one podman,
one registry state.

| Case | Image | Manifest digest | Signature attachment | Rekor | Device verdict |
|---|---|---|---|---|---|
| 1 — unsigned | `quay.io/jary/soarm-flywheel:sim-only` | `sha256:06413b09f79dd1186d7b199ffa56b01d56c12f50ed2a4671fb299cac297a66e3` | none | none | **rejected** — `A signature was required, but no signature exists` |
| 2 — signed, no tlog | `quay.io/jary/soarm-flywheel:negtest-notlog-2026-09-09` | `sha256:d9996e7b1b779ba94fc4f5699cd0dbc4dc43c96c70e69b1b861e28f63e51d2cb` | `sha256-d9996e7b….sig` (sig digest `sha256:f2b7877fd16f…`), `dev.cosignproject.cosign/signature` only | none (tree size 11 → 11) | **rejected** — `missing dev.sigstore.cosign/bundle annotation` |
| 3 — signed + tlog (control) | `quay.io/jary/soarm-flywheel:act-inference-amd64-2026-09-08b` (the Fleet's runtime image) | `sha256:2ad1fb1c393a6a5c5281abab83187d9e4aeecc05fdca8e12b7d247ced0009e11` | `sha256-2ad1fb1c….sig`, signature + `dev.sigstore.cosign/bundle` | log index 3, integratedTime 1788910239 | **accepted** — `Storing signatures`, image id `b21e9348a500` |

Same key (`/etc/pki/containers/cosign.pub`, md5 `e2acdfa8…`), same Rekor key
(`/etc/pki/containers/rekor.pub`, md5 `e34fa270…` = desktop `~/rekor-live.pub`), same repository, same
`signedIdentity: matchRepository`, same `registries.d` lookup. The only variable across the three
rows is what the signature attachment carries.

## Policy under test (device VM 10.0.0.51, rootful podman 5.8.2)

`/etc/containers/policy.json` (md5 `b203fe51…`, D042 — the Fleet's file, RHEL defaults kept; excerpt
for the repository exercised here):

```json
"quay.io/jary/soarm-flywheel": [
    {
        "type": "sigstoreSigned",
        "keyPath": "/etc/pki/containers/cosign.pub",
        "rekorPublicKeyPath": "/etc/pki/containers/rekor.pub",
        "signedIdentity": {
            "type": "matchRepository"
        }
    }
]
```

`/etc/containers/registries.d/quay-jary.yaml`:
```yaml
docker:
  quay.io/jary:
    use-sigstore-attachments: true
```

`rekorPublicKeyPath` is what turns case 2 into a rejection. containers/image's `sigstoreSigned`
verifier, when that field is set, requires the cosign attachment to carry the
`dev.sigstore.cosign/bundle` annotation and checks the bundle's Signed Entry Timestamp against
`rekor.pub` before it looks at the payload signature — a signature made with the right key but never
uploaded to the log has no bundle, so it is rejected before the key is even consulted. Without
`rekorPublicKeyPath` (`keyPath` alone) case 2 would pull.

## Case 2 — building and signing the no-tlog artifact (desktop 10.0.0.48)

A copy of a digest to a new tag keeps the digest, and a cosign signature keys on the digest — so a
`crane copy` of image b would have inherited image b's logged signature. To get a *distinct* digest
that carries only a no-tlog signature, one 118-byte file was appended as a 30th layer on top of
image b (`crane append` re-uses the 29 existing blobs):

```bash
cd ~/build-logs/negtest
printf 'negative trust test 2026-09-09: …\n' > negtest-notlog.txt
tar --owner=0 --group=0 --mtime=@0 -cf layer.tar negtest-notlog.txt
~/bin/crane append \
  -b quay.io/jary/soarm-flywheel@sha256:2ad1fb1c393a6a5c5281abab83187d9e4aeecc05fdca8e12b7d247ced0009e11 \
  -t quay.io/jary/soarm-flywheel:negtest-notlog-2026-09-09 -f layer.tar
# 2026/09/09 11:12:12 quay.io/jary/soarm-flywheel:negtest-notlog-2026-09-09: digest: sha256:d9996e7b1b779ba94fc4f5699cd0dbc4dc43c96c70e69b1b861e28f63e51d2cb size: 5148
# manifest: application/vnd.docker.distribution.manifest.v2+json, 30 layers; new layer sha256:4cbad32f… (226 B)
```

Signed with the **same** containerised recipe as the interim image (`~/build-logs/sign-negtest-notlog.sh`,
a copy of `sign-interim-b.sh`: cosign v2.6.5 in a throwaway container, `--add-host` for the Rekor
route, SNO ingress CA, 0600 temp docker config from the credential helper, `COSIGN_PASSWORD=` empty,
`SIGSTORE_REKOR_PUBLIC_KEY=/rekor.pub`), with exactly two differences — `--tlog-upload=false` and no
`--rekor-url` on the `sign` line:

```
$ curl -s http://localhost:8090/api/v1/log | jq .treeSize      # port-forward to svc/rekor-server
11
=== SIGN 2026-09-09T11:12:28-05:00
cosign sign --key /keys/cosign.key -y --tlog-upload=false quay.io/jary/soarm-flywheel@sha256:d9996e7b…51d2cb
Pushing signature to: quay.io/jary/soarm-flywheel
$ curl -s http://localhost:8090/api/v1/log | jq .treeSize
11
```

No `tlog entry created with index:` line, tree size unchanged. The attachment manifest confirms it —
`crane manifest quay.io/jary/soarm-flywheel:sha256-d9996e7b…51d2cb.sig` has one layer whose only
annotation is `dev.cosignproject.cosign/signature`; image b's `.sig` has that plus
`dev.sigstore.cosign/bundle` with `logIndex 3`, `integratedTime 1788910239`, `logID 37f4fa09cc7f385b…`.

## Case 2 — `cosign verify` with the transparency log (desktop)

This is the check the runbook and the swap agent's replacement use (D026 row 2 — no
`the tlog bypass flag` anywhere; a repo-wide grep for the flag name is empty). It fails for the no-tlog
digest and passes for image b under the identical command:

Through the route, inside the signing container (`sign-negtest-notlog.sh`, 11:12:35 CDT):
```
$ cosign verify --key /keys/cosign.pub --rekor-url https://rekor-server-trusted-artifact-signer.apps.sno-flywheel.local \
    quay.io/jary/soarm-flywheel@sha256:d9996e7b1b779ba94fc4f5699cd0dbc4dc43c96c70e69b1b861e28f63e51d2cb
Error: no matching signatures: signature not found in transparency log
error during command execution: no matching signatures: signature not found in transparency log
exit=12
```

Natively on the desktop through the `oc port-forward` (the form `promotion-2.md` and the runbook's
Beat 5 use), 11:13:01 CDT:
```
$ SIGSTORE_REKOR_PUBLIC_KEY=~/rekor-live.pub ~/bin/cosign verify --key ~/cosign/cosign.pub --rekor-url http://localhost:8090 \
    quay.io/jary/soarm-flywheel@sha256:d9996e7b1b779ba94fc4f5699cd0dbc4dc43c96c70e69b1b861e28f63e51d2cb
Error: no matching signatures: searching log query: &{0 } (*models.Error) is not supported by the TextConsumer, can be resolved by supporting TextUnmarshaler interface
exit=12
```
Same verdict, uglier text: with no bundle on the attachment cosign falls back to an online lookup of
the log's search index, and Rekor's not-found reply through the plain-HTTP port-forward is one the
client cannot decode. The route/TLS path returns the readable `signature not found in transparency
log`. Either way the exit code is 12 and no `Verification for …` block is printed. Worth knowing
before the F-Tekton verify task is written against the in-cluster service URL.

Control, same command, image b (11:13:03 CDT):
```
$ SIGSTORE_REKOR_PUBLIC_KEY=~/rekor-live.pub ~/bin/cosign verify --key ~/cosign/cosign.pub --rekor-url http://localhost:8090 \
    quay.io/jary/soarm-flywheel@sha256:2ad1fb1c393a6a5c5281abab83187d9e4aeecc05fdca8e12b7d247ced0009e11

Verification for quay.io/jary/soarm-flywheel@sha256:2ad1fb1c393a6a5c5281abab83187d9e4aeecc05fdca8e12b7d247ced0009e11 --
The following checks were performed on each of these signatures:
  - The cosign claims were validated
  - Existence of the claims in the transparency log was verified offline
  - The signatures were verified against the specified public key
bundle logIndex 3 integratedTime 1788910239
exit=0
```

## Device pulls (VM `act-device`, 2026-09-09T16:13:29–34Z, one session)

**Case 2 — signed without tlog, by digest — rejected:**
```
$ sudo podman pull quay.io/jary/soarm-flywheel@sha256:d9996e7b1b779ba94fc4f5699cd0dbc4dc43c96c70e69b1b861e28f63e51d2cb
Trying to pull quay.io/jary/soarm-flywheel@sha256:d9996e7b1b779ba94fc4f5699cd0dbc4dc43c96c70e69b1b861e28f63e51d2cb...
Error: unable to copy from source docker://quay.io/jary/soarm-flywheel@sha256:d9996e7b1b779ba94fc4f5699cd0dbc4dc43c96c70e69b1b861e28f63e51d2cb: Source image rejected: missing dev.sigstore.cosign/bundle annotation
exit=125
```
By tag, same result:
```
$ sudo podman pull quay.io/jary/soarm-flywheel:negtest-notlog-2026-09-09
Trying to pull quay.io/jary/soarm-flywheel:negtest-notlog-2026-09-09...
Error: unable to copy from source docker://quay.io/jary/soarm-flywheel:negtest-notlog-2026-09-09: Source image rejected: missing dev.sigstore.cosign/bundle annotation
exit=125
```
The rejection is immediate (no `Copying blob` lines — the attachment is fetched and judged before any
layer). Note the wording: podman does not say "rekor" or "transparency log"; the SET lives inside the
bundle annotation, and the absence of that annotation is the first thing the `rekorPublicKeyPath`
branch checks. Anyone grepping device logs for the Rekor-enforcement failure should look for
`missing dev.sigstore.cosign/bundle annotation`.

**Case 1 — unsigned, same repository — rejected** (re-run of the 2026-09-08 leg, verbatim identical):
```
$ sudo podman pull quay.io/jary/soarm-flywheel@sha256:06413b09f79dd1186d7b199ffa56b01d56c12f50ed2a4671fb299cac297a66e3
Trying to pull quay.io/jary/soarm-flywheel@sha256:06413b09f79dd1186d7b199ffa56b01d56c12f50ed2a4671fb299cac297a66e3...
Error: unable to copy from source docker://quay.io/jary/soarm-flywheel@sha256:06413b09f79dd1186d7b199ffa56b01d56c12f50ed2a4671fb299cac297a66e3: Source image rejected: A signature was required, but no signature exists
exit=125
```

**Case 3 — signed + logged (image b, the digest the Fleet pins) — accepted.** The image was already in
local storage; `podman pull` still re-fetches the attachment and re-verifies it against
`cosign.pub` + `rekor.pub` before deciding the layers are present:
```
$ sudo podman pull quay.io/jary/soarm-flywheel@sha256:2ad1fb1c393a6a5c5281abab83187d9e4aeecc05fdca8e12b7d247ced0009e11
Trying to pull quay.io/jary/soarm-flywheel@sha256:2ad1fb1c393a6a5c5281abab83187d9e4aeecc05fdca8e12b7d247ced0009e11...
Getting image source signatures
Checking if image destination supports signatures
Copying blob sha256:8bc368cc…   (× 29, all already present)
Copying config sha256:b21e9348a50089e3859f59adedbaaa1be2e9a746fc0cf504705292a3fcd53a3d
Writing manifest to image destination
Storing signatures
b21e9348a50089e3859f59adedbaaa1be2e9a746fc0cf504705292a3fcd53a3d
exit=0
```

Device storage after the run holds exactly the two logged runtime digests (`2ad1fb1c…`, `29955e4e…`)
and the two logged modelcar digests; nothing from cases 1 or 2 landed.

## Case 4 — outside the enumerated registries, rejected by the default (review W-10, 2026-09-09T22:18:29Z)

Until W-10 the policy's `default` was `insecureAcceptAnything`: enforcement covered only the four
enumerated entries, and any other reference — a renamed repo, a typo in a promotion edit, a mirror,
docker.io — would have pulled unsigned with no error. The Fleet now ships `"default": [{"type":
"reject"}]` (renderedVersion 9, `UpToDate` / `Healthy` 105 s after the ResourceSync picked up
`366ac14`; `cat /etc/containers/policy.json` on the device confirms the default). A public image
from a registry the policy does not name is refused before any signature is looked at:
```
$ sudo podman pull docker.io/library/alpine:3.20
Trying to pull docker.io/library/alpine:3.20...
Error: unable to copy from source docker://alpine:3.20: copying system image from manifest list: Source image rejected: Running image docker://alpine:3.20 is rejected by policy.
exit=125
```
Cases 1 and 2 re-run in the same session with the new policy, verbatim identical (`A signature was
required, but no signature exists` / `missing dev.sigstore.cosign/bundle annotation`, both exit 125),
and the positive control still passes — the modelcar the Fleet pins re-verified and stored its
signature:
```
$ sudo podman pull quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b
Getting image source signatures
Checking if image destination supports signatures
Storing signatures
e974191054163f6a0871793e5714aa4a772283adae4e5e9ea2772e7afed0f706
exit=0
```
Device after the session: renderedVersion 9, `UpToDate`, `Healthy`.

The "right signature, wrong repository" variant — the pinned modelcar copied unmodified to
`quay.io/jary/soarm-flywheel-negtest:policy-default-2026-09-09` (same digest `bdb513ca…`, never
signed there) — could not be exercised yet: quay created that repository private, so the pull fails
on `reading manifest … unauthorized` before the policy is consulted. Once the repository is made
public in quay it becomes the standing artifact for that case; expected result is the same
`rejected by policy` line, since `default: reject` applies before `signedIdentity` is ever evaluated.

## Artifacts left in place

- `quay.io/jary/soarm-flywheel-negtest:policy-default-2026-09-09` (private until the operator flips it;
  the pinned modelcar's bytes, no signature in that repository) — Case 4's wrong-repository variant.

- `quay.io/jary/soarm-flywheel:negtest-notlog-2026-09-09` (`sha256:d9996e7b…51d2cb`) and its
  `sha256-d9996e7b…51d2cb.sig` attachment stay in quay **on purpose** — they are the demo's
  standing negative-test artifact (the runbook can pull the tag on the device to show the rejection
  live without re-signing anything). Do not move `:latest` to it; do not sign it into Rekor — doing so
  would silently convert it into a passing case.
- Desktop: `~/build-logs/negtest/` (layer tar, `digest.txt`, `append.log`, sign/verify logs) and
  `~/build-logs/sign-negtest-notlog.sh`.
- Rekor tree size stays 11; no log entry was created by this test.

## What this closes

Phase 4.5 exit line *"Negative test: an unsigned tag fails with a signature error; a tag signed
without `--tlog-upload` also fails on the VM (Rekor SET enforced)"* — both halves observed on the
device under the Fleet's `policy.json`, with the logged image b as the positive control in the same
session. D026 rows 1 (node trust) and 2 (Rekor at verify) are demonstrated end to end on the device
path; row 3 (Tekton-built) remains with F. Case 4 (2026-09-09, review W-10) closes the fail-open
default: a reference outside the enumerated registries is rejected by policy, not accepted unsigned.
