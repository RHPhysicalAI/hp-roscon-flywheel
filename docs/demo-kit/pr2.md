# PR #2 — Promote act-v2-ft160-rhem (73% -> 86%)

https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/2

Merged: 2026-09-09T12:04:27Z by jeremyary (merge commit `4ce6a8a5`, head `promote/act-v2-ft160-rhem`); rolled back by PR #3 (merge `1eab0824`, 2026-09-09T12:35:10Z)

Files changed: gitops/flywheel/manifest-consumer.yaml, gitops/rhem/fleet-act-inference.yaml (2 + 3 lines, D066)

Rollout as measured (D069/D070): merge -> device serving 2:10, -> Healthy 2:42; the Fleet banner went green 29 s before the app was Healthy. Rollback (PR #3, D074): 1:28 / 1:59, no re-pull.

---

## Promotion: `act-v2-ft160-rhem` replaces `upstream-act-teacher`

| | success | mean cubes |
|---|---|---|
| incumbent `upstream-act-teacher` | 73% | 2.51 |
| candidate `act-v2-ft160-rhem` | 86% | 2.73 |

Paired on 100 identical seeded scenes: **20 fixed / 7 broken, net +13, sign-test p = 0.0192** - gate rule: promote iff net > 0 and p < 0.05 (D022) -> **PASS**.

Signed modelcar: `quay.io/jary/soarm-act-modelcar@sha256:1375d0bcc2c7c81867365b55a08bdd5fa03bf31d20cc7bde04044fdcf1a0784e`

Merging edits Fleet `act-inference` in one commit (`gitops/rhem/fleet-act-inference.yaml`: modelcar digest + `MODEL_VERSION` `act-v2-ft160` -> `act-v2-ft160-rhem`) and points the trigger at the new lineage (`gitops/flywheel/manifest-consumer.yaml`: COLLECTOR/INCUMBENT/INCUMBENT_CHECKPOINT). ResourceSync renders the Fleet; RHEM rolls it out batch by batch; each device pulls the modelcar under its policy.json (cosign key + Rekor SET) and restarts the container, which publishes the new `model_version`.

Fleet: https://ui.flightctl.apps.sno-flywheel.local/devicemanagement/fleets/act-inference

Rollback: `git revert <sha>` - revert the merge commit of this PR and merge the revert. The previous modelcar is still in device storage (image volume `reclaimPolicy: Retain`), so rolling back does not re-pull.
