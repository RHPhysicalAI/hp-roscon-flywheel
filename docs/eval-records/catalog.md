<!-- This project was developed with assistance from AI tools. -->
# RHEM Catalog — the version graph beside the Fleet (Phase 4.5 E2, D027/D038)

**Date:** 2026-09-09. **State: E2 landed; exit criterion met.** Catalog `physical-ai-models` and
`CatalogItem soarm-act` are live on the hub, rendered by a second ResourceSync (`rhem-catalog`,
`type: catalog`) from `gitops/rhem-catalog/`; the pipeline's `open_promotion_pr` now appends the
promoted candidate to that file in the same commit that edits the Fleet, through one removable
function (`append_catalog_version`). No run was triggered (it would open PR #5); the seam was proven
by a local unit-style call against the committed file, and the hand-written versions by the sync.

**Stage sentence:** *v1alpha1; the Fleet pins the digest, the Catalog shows the version graph.*

The Catalog API is **`flightctl.io/v1alpha1`** in flightctl 1.3.0 — alpha, "may change in future
releases" (`docs/user/using/managing-catalogs.md`). Every artifact here says so.

## What is live

| | |
|---|---|
| Catalog | `rhem/bootstrap/catalog.yaml` → `Catalog/physical-ai-models` (`201 Created`, hand-applied once, owner none) |
| ResourceSync | `rhem/bootstrap/resourcesync-catalog.yaml` → `ResourceSync/rhem-catalog`: `type: catalog`, `repository: hp-roscon-flywheel`, `targetRevision: desktop-gpu-split`, `path: gitops/rhem-catalog`. Conditions `Accessible=True`, `ResourceParsed=True`, `Synced=True` (16:26:01Z), `observedCommit 0141efb3…` |
| CatalogItem | `gitops/rhem-catalog/catalogitem-soarm-act.yaml` → `CatalogItem physical-ai-models/soarm-act`, `type: container`, `owner: ResourceSync/rhem-catalog`, artifact `container` = `quay.io/jary/soarm-act-modelcar`, versions `2.0.0-ft160` (`sha256:bdb513ca…`) and `2.0.0-ft160-rhem` (`sha256:18cc4412…`, `replaces: 2.0.0-ft160`), both `channels: [stable]` |
| Fleet (unchanged) | `gitops/rhem/fleet-act-inference.yaml:189,201` — `MODEL_VERSION: act-v2-ft160`, `Image=quay.io/jary/soarm-act-modelcar@sha256:bdb513ca…` |
| Pipeline version | `v-202609091125-catalog` (`119c51a3-6b71-4fdd-8755-9be65034b86f`) on pipeline `99ec0aab-…`; not run |
| Commits | `0c2dbbf` (bootstrap objects, CatalogItem, READMEs), `0141efb` (seam), this record |

CLI (desktop, `flightctl` 1.3.0 client and server):

```
$ flightctl get resourcesync
NAME          REPOSITORY          PATH                 REVISION           ACCESSIBLE  SYNCED  LAST SYNC
rhem-catalog  hp-roscon-flywheel  gitops/rhem-catalog  desktop-gpu-split  True        True    9 seconds ago
rhem-fleets   hp-roscon-flywheel  gitops/rhem          desktop-gpu-split  True        True    8 seconds ago
$ flightctl get catalogs
NAME                DISPLAY NAME        AGE
default             Default             17 hours ago
physical-ai-models  Physical AI Models  ...
$ flightctl get catalogitems --catalog physical-ai-models
NAME       TYPE       DISPLAY NAME
soarm-act  container  SO-ARM ACT policy (ModelCar)
```

Resource names: `catalogs`/`catalog`, `catalogitems`/`catalogitem` (the latter needs `--catalog <name>`,
also on `delete`). A `default` Catalog pre-exists from the chart install; ours sits beside it.

**UI:** the bundle's route table (`main.bundle-d7ee6bdf….js`) has a top-level **`/catalog`** page
(`/catalog/import`, `/catalog/install`, `/catalog/edit`) plus `/devicemanagement/fleets/catalog` and
`/devicemanagement/devices/catalog` (the "add from catalog" flows). So the catalog is at
**`https://ui.flightctl.apps.sno-flywheel.local/catalog`**, and the sync at
`https://ui.flightctl.apps.sno-flywheel.local/devicemanagement/resourcesyncs/rhem-catalog`.
Not screenshotted (no browser with that host mapped in this session) — confirm before recording.

## Schema findings (v1alpha1, verified on the live API)

| Question | Verdict | Evidence |
|---|---|---|
| `references.container` in **digest form** | **Accepted — `sha256:<hex>`, stored verbatim** | probe `catalogitem/soarm-act-probe: 201 Created`; `get -o yaml` returns `container: sha256:bdb513ca…`. This is the form the OpenAPI example itself uses (`CatalogItemSpec.versions.example`, `CatalogItemVersion.references.example`) |
| `@sha256:<hex>` form | also accepted (`201 Created`) | the server does not validate the reference string at all — `validateCatalogItemVersion` only checks the key matches an artifact type and the value is non-empty (`api/core/v1alpha1/validation.go:205-217`). Keep the bare `sha256:` form: it is the documented example and it is what the Fleet's `@` splits off |
| **Version names must be strict semver** | **Rejected: `act-v2-ft160`** | `400` — `Error at "/spec/versions/0/version": doesn't match schema due to: string doesn't match the regular expression "^(0\|[1-9]\d*)\.(0\|[1-9]\d*)\.(0\|[1-9]\d*)(?:-(…))?(?:\+(…))?$"` (OpenAPI `SemVer` pattern); the server's own `validateSemver` (`validation.go:307`) additionally forbids a `v` prefix. `replaces`/`skips` are the same type |
| Chosen mapping | `act-v<N>-<suffix>` → **`<N>.0.0-<suffix>`** (pre-release), candidate name kept in the version's `readme` | `2.0.0-ft160`, `2.0.0-ft160-rhem`; pre-release identifiers may contain `-`, so the `-rhem` suffix survives; a name outside the convention falls back to `0.0.0-<sanitized>` |
| `replaces` target must exist? | not enforced — only self-reference and cycles are rejected (`validateReplacesGraph`, `validation.go:266`) | the seam still only writes the edge when the previous candidate is already a node, so the graph never carries a dangling edge |
| Catalog hand-applied, items synced | works | the item sync's parent guard (`internal/tasks/resourcesync.go:968-1009`) rejects only a parent Catalog **owned by a different ResourceSync**; an unowned Catalog is fine. The sync marks items `owner: ResourceSync/rhem-catalog` and they are then read-only via API/CLI/UI (docs) |
| One sync per type | confirmed | `ResourceSyncType` enum `fleet \| catalog` (v1beta1 OpenAPI); `syncCatalogResources` errors on any kind other than Catalog/CatalogItem (`resourcesync.go:157`) — hence the separate `gitops/rhem-catalog/` directory (D032) |
| `catalogItemRef` on our Fleet | unavailable (D038) | `ImageOrCatalogItemRefSpec` exists for `spec.os`, `applications[].image` and app-level `volumes[].image`; the quadlet `.volume Driver=image` line is opaque inline content. The Fleet keeps the digest pin; the CatalogItem is provenance |

## The seam (`pipeline/act_flywheel_pipeline.py`)

`append_catalog_version(item_yaml, version, digest, replaces) -> str` — one nested function inside
`open_promotion_pr`, fenced `# ---- Catalog seam (D027) BEGIN … END`, with a docstring saying it is
designed to be deleted when RHEM's registry → catalog bridge lands. Shape:

- Inputs are the pipeline's own vocabulary: `version` = the candidate name, `digest` = the
  `sha256:…` already split off `image_ref`, `replaces` = the Fleet's previous `MODEL_VERSION`
  (`old_mv`, captured by the existing two-regex edit). The semver mapping lives inside the seam.
- Reads the file with PyYAML (pinned `PyYAML>=6,<7` on the component, "catalog seam only"), keeps
  everything before `  versions:` byte-for-byte (header comments, item spec), and re-renders the
  versions list in a canonical block form. **Idempotent:** an existing version is replaced in place
  (reference + readme refreshed, its `replaces` edge kept when the incumbent is itself); a new one
  is appended with `replaces` only if the previous candidate is a node.
- Called once, between the Fleet/consumer tree elements and `create_git_tree`, so the CatalogItem
  rides in the **same promotion commit**; the PR body gains one `Catalog (v1alpha1, version graph
  only …)` line. A `404` on the item file logs "skipped (not load-bearing)" — the Catalog never
  blocks a promotion.
- New defaulted param `catalog_item_file` on the component and the pipeline
  (`gitops/rhem-catalog/catalogitem-soarm-act.yaml`); the runbook's trigger body is unchanged.
- **Removal recipe (D027):** delete the fenced block, the `catalog_item_file` param (both places),
  the PyYAML pin and the `body += catalog_note` line. `gitops/rhem-catalog/` and the two bootstrap
  objects stay as the bridge's target.

Local proof (no run): the function was extracted from the fence and called against the committed
file — (a) rebuilding from a one-version file reproduces the committed file **byte-identically**;
(b) re-applying the same version + digest is a no-op; (c) same version, new digest → reference
updated in place, order and `replaces` kept; (d) `act-v3-ft200` replacing `act-v2-ft160-rhem` →
appended with the edge; (e) incumbent `upstream-act-teacher` → no dangling edge; (f) a name outside
the convention → `0.0.0-sim-only.v9`; every output parses and every version matches the OpenAPI
`SemVer` pattern. `py_compile` + KFP compile (`kfp 2.17.0`) clean; uploaded as
`v-202609091125-catalog`. Side note: the DSP upload on the port-forwarded service port went through
with an **empty** bearer token (`oc create token` failed in the non-interactive ssh); the runbook's
route-side calls still need the token.

## Exit criterion — "the CatalogItem version graph matches the Fleet's pin"

**Met.** The Fleet pins `sha256:bdb513ca…` with `MODEL_VERSION: act-v2-ft160`; the graph's node
`2.0.0-ft160` (readme `candidate act-v2-ft160`) carries exactly that digest. The head node
`2.0.0-ft160-rhem` = `sha256:18cc4412…` is PR #4's unmerged candidate, which is the state the seam
produces (the promotion commit edits Fleet and CatalogItem together, so after a merge the head node
and the pin move as one). A promotion run will now write the node the seam wrote here by hand.

## Not done / follow-ons

- **`catalogItemRef` on the Fleet** stays out (D038); revisit if a future flightctl lets a quadlet
  `.volume` resolve through the catalog, or if the modelcar is repackaged as a `data` artifact.
- The Catalog object is hand-applied (bootstrap) rather than synced. Moving `catalog.yaml` into
  `gitops/rhem-catalog/` would make the sync own it too (docs' recommended layout); left as-is so
  the bootstrap set stays "Repository + syncs + catalog", mirroring the Argo `*-app.yaml` role.
- No UI screenshot; the `/catalog` path is from the route table, not a visual check.
- Proposed decisions: session scratchpad `decisions-E2.md`.
