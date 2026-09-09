# PR #1 — Promote act-v2-ft160 (73% -> 86%)

https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/1

Merged: 2026-09-08T16:45:04Z by jeremyary

Files changed: gitops/act-serving/deployment-green.yaml, gitops/act-serving/service.yaml

---

## Promotion: `act-v2-ft160` replaces `upstream-act-teacher`

| | success | mean cubes |
|---|---|---|
| incumbent `upstream-act-teacher` | 73% | 2.51 |
| candidate `act-v2-ft160` | 86% | 2.73 |

Paired on 100 identical seeded scenes: **20 fixed / 7 broken, net +13, sign-test p = 0.0192** - gate rule: promote iff net > 0 and p < 0.05 (D022) -> **PASS**.

Signed modelcar: `quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b`

Merging flips blue/green atomically (green replicas 1, blue 0, Service -> green); Argo syncs it; the swap agent applies it on the desktop.
