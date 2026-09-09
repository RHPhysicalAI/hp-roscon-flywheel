# gitops/rhem/ — Fleets and CatalogItems rendered by RHEM

This directory is synced by the flightctl `ResourceSync/rhem-fleets`
(`rhem/bootstrap/resourcesync.yaml`), **not** by Argo CD: Fleets and CatalogItems are flightctl
API objects, not Kubernetes CRs, so there is no Argo `Application` for this path (D024).

What lands here in Phase 4.5:

- `fleet-act-inference.yaml` (C) — selector `fleet=act-inference`, the quadlet app with the
  signed modelcar as an image volume, rollout policy, health check. A promotion (D025) is a
  two-regex edit of this file.
- ~~`catalogitem-soarm-act.yaml` (E2, stretch)~~ — moved to `gitops/rhem-catalog/` (own
  `ResourceSync/rhem-catalog`, `type: catalog`): a sync handles one resource type (D032), and the
  Fleet cannot pin a `catalogItemRef` on the `.volume Driver=image` path anyway (D038), so the
  CatalogItem is a version graph beside the digest-pinned Fleet, written by the pipeline's
  removable seam (D027).

Rules for files in this directory: flat (the sync does not descend into subdirectories),
`*.yaml` only for resources (this README is skipped), one or more flightctl resources per file,
`apiVersion: flightctl.io/v1beta1`, Fleets only (`type: fleet` sync).

## `fleet-act-inference.yaml` — draft status (2026-09-08, Phase 4.5 C prep)

Drafted and validated offline; **not applied** (no hub yet). What was checked:

- Every key walked against the flightctl v1.3.0 OpenAPI (`api/core/v1beta1/openapi.yaml`:
  `Fleet` → `FleetSpec` → `RolloutPolicy`/`BatchSequence`/`Batch`, `DeviceSpec.config[]` →
  `InlineConfigProviderSpec`/`FileSpec`, `applications[]` → `QuadletApplication` +
  `InlineApplicationProviderSpec` + `ApplicationEnvVars`). `Duration` must match `^(?:[1-9]\d*)?\d[smh]$`
  (`30m`), `Percentage` is a string (`100%`), `mode` is an integer (`0644` octal literal).
- Rendered as a Go template with the documented helpers (`upper`, `lower`, `replace`, `getOrDefault`)
  for a desktop device (`gpu=none policy_device=cpu`), a Fury device (`gpu=nvidia policy_device=cuda`)
  and a device with no `gpu`/`policy_device` labels; all three parse as YAML, the CPU branch adds
  `OMP_NUM_THREADS`/`TORCH_NUM_THREADS=8`, the GPU branch adds `AddDevice=nvidia.com/gpu=all`.
- The rendered `act-inference.container` + `models.volume` pass `/usr/libexec/podman/quadlet -dryrun`
  on the RHEL 10.2 VM (podman 5.8.2).

Open before the live apply (owner: C-live / D / F):

| Item | Why it is open |
|---|---|
| `Image=quay.io/jary/soarm-flywheel@sha256:TODO-F` | the runtime image exists only as local docker `act-inference:latest`; Tekton (F) builds, signs and pushes it |
| `quay.io/jary/soarm-act-modelcar` is **private** | the VM's anonymous `podman pull` gets `unauthorized`; either make the repo public or place `/root/.config/containers/auth.json` on the device before the Fleet (flightctl docs "Using Image Pull Secrets": credentials must pre-exist, the agent does not deliver them) |
| positive pull test (signed + Rekor-logged digest) | blocked on the same credentials; the negative test (unsigned `soarm-flywheel:sim-only` → "A signature was required, but no signature exists") already passes on the VM |
| `SecurityLabelDisable=true` under the `gpu=nvidia` branch | CDI + SELinux on RHEL usually needs it; confirm on the Fury, drop if `container_use_devices` suffices |
| `HealthOnFailure=kill` + `Restart=always` | chosen so an unhealthy container restarts on its own during the demo; revisit if it masks a real fault |

The trust files the Fleet writes (`policy.json`, `registries.d/quay-jary.yaml`, `cosign.pub`,
`rekor.pub`) are already on the VM by hand from the C prep — the Fleet will overwrite them with
identical content.
