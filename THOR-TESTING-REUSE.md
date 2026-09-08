# What Reuses from thor-testing

Source repo: `~/redhat/git/thor-testing` (do NOT modify — copy and adapt into this repo).

## Reuse verdict: ~70% of the demo skeleton

Same architecture, same story, same RH stack. The HP demo is the same flywheel re-centered on
the GB300 with a different sim producer.

## What maps 1:1 (copy and re-point)

### GitOps manifests (`gitops/`)

| Directory | Contents | Reuse notes |
|---|---|---|
| `gitops/flywheel/` | namespace, curator, sync-agent, dashboard, dreamer, edge-kafka, mirrormaker2, robot-sim, scc-rolebinding | Copy all. Replace `robot-sim.yaml` with SO-ARM producer. Retune curator thresholds. Everything else as-is. |
| `gitops/vllm-cosmos3/` | deployment (blue), deployment-green, entrypoint-configmap, namespace, scc-rolebinding, service | Copy the blue/green pattern. Re-point from Cosmos3 vLLM to ACT policy serving. The service-selector-flip mechanism (commits `77fc90f` / `5e3e87a`) is the reusable piece. |
| `gitops/observability/` | perses-instance, perses-route, tempo-datasource, edge-flywheel-dashboard | Copy as-is. Dashboard panels may need label updates for SO-ARM metrics. |
| `gitops/hub-training/` | manifest-consumer | Copy. Adapt for LeRobot ACT training trigger instead of Cosmos Vision SFT. |
| `gitops/edge-workloads/` | namespace, smoke-test | Copy if useful for initial validation. |

### Tekton pipelines (`tekton/`)

| File | What it does | Reuse notes |
|---|---|---|
| `00-buildah-cross-arch-task.yaml` | arm64 cross-build via buildah + qemu | Directly reusable — GB300 is aarch64 like Thor. On the x86 desktop, builds x86 natively and arm64 via qemu. |
| `01-cosign-sign-task.yaml` | cosign sign + Rekor transparency log | Directly reusable. Use cosign **v2.4.1** not v3 (v3's OCI 1.1 referrers tag scheme is incompatible — thor-testing D015). |
| `02-pipeline.yaml` / `03-pipelinerun.yaml` | Full build+sign pipeline | Copy. Adapt image names. |
| `04-download-weights-task.yaml` | Download model weights | Adapt for ACT checkpoints instead of Cosmos3. |
| `05-modelcar-pipeline.yaml` / `06-modelcar-pipelinerun.yaml` | Package model as KServe modelcar OCI | Directly reusable — model-agnostic. |
| `07-package-modelcar-task.yaml` | `crane append` to build modelcar layer | Directly reusable. Use `crane append` not `buildah bud` for big model layers (thor-testing D013 — buildah stalls on 10+ GB layers). |

### Training pipeline (`pipeline/`)

| File | Reuse notes |
|---|---|
| `cosmos3_finetune_pipeline.py` | **Replace** with LeRobot ACT fine-tune pipeline. Pipeline *shape* transfers (train -> eval -> package -> sign -> promotion PR) but the training code is completely different. |

### Demo artifacts

| Asset | Reuse notes |
|---|---|
| `DEMO_RUNBOOK.md` | 6-beat narrative arc + Short Cut / Full Live structure transfers. Re-skin for SO-ARM. |
| `dream-comparison/` | v1 vs v2 before/after visuals. Create the SO-ARM equivalent. |
| `DECISIONS.md` (~D037) | Do not copy wholesale — but the solved gotchas below are invaluable reference. |

## The integration seam

The flywheel services communicate through **filesystem directories** and **structured JSON**:

```
Producer writes ->  /data/episodes/raw/*.json
Curator reads  <-   /data/episodes/raw/*.json
Curator writes ->   /data/episodes/curated/*.json  (pass)
                    /data/episodes/rejected/*.json  (reject)
Sync-agent reads <- /data/episodes/curated/*.json
Sync-agent uploads -> MinIO (bucket: episodes-curated, key: <model_version>/<episode_id>.json)
Sync-agent publishes -> Kafka (topic: episode-manifests, payload: {episode_id, s3_uri, score, ...})
```

All components use `hostPath` volumes pointing at `/var/lib/episodes/` on the node.

### Episode JSON contract (what the curator reads)

The SO-ARM episode-emitter adapter writes a summary JSON after each rollout. The full
rollout data (MCAP rosbag / LeRobot dataset) is stored separately for training; this JSON
is the lightweight metadata the curator scores on.

```json
{
  "episode_id":     "string  -- uuid4",
  "timestamp":      "string  -- ISO 8601 UTC",
  "scene":          "string  -- task name (e.g. 'place_cubes_on_tray')",
  "model_version":  "string  -- e.g. 'soarm-act-v1'",
  "has_failure":    "bool    -- true = injected failure, curator always rejects",

  "rollout": {
    "status":       "string  -- 'ok' if rollout completed, 'error' if sim crashed",
    "steps":        "number  -- total timesteps in the episode",
    "duration_s":   "number  -- wall time in seconds"
  },

  "task_success":   "bool    -- did the arm complete the placement task?",
  "cubes_placed":   "number  -- how many cubes landed on the tray (0-3)",

  "avg_smoothness": "number  -- mean abs delta between consecutive joint commands",

  "dataset_path":   "string  -- repo-relative path to the recorded MCAP bag for this
                                rollout (bags/<name>), or null if unrecorded. Ported to a
                                LeRobot dataset by the assembler. Replaces rosbag_path (D018)."
}
```

### Curator scoring (rewritten for SO-ARM)

The curator (`gitops/flywheel/curator.yaml`) scores on task performance:
- Gate 0: `has_failure` -> always reject (demo failure injection)
- Gate 1: `rollout.status != "ok"` or `rollout.steps < 10` -> penalty (incomplete episode)
- Gate 2: `task_success == false` -> penalty (primary signal — did the arm do the job?)
- Gate 3: `avg_smoothness` above threshold -> penalty (jerky motion = low-quality trajectory)

Keep the pass/reject directory structure and Kafka publish unchanged.

## Hard-won gotchas from thor-testing (highest-value carryover)

These are real issues solved in thor-testing's decision log that **will recur** on this project:

| ID | Gotcha | Resolution |
|---|---|---|
| D009 | qemu segfaults under standard `pipelines-scc` for arm64 cross-builds | Needs privileged SCC. On the x86 desktop, native builds avoid qemu entirely; only arm64 cross-builds need it. |
| D013 | `buildah bud` stalls on 10+ GB model layers (fuse-overlayfs) | Use `crane append` — pushed same layer in <5 min vs. >1 hr. |
| D015 | cosign v3.1.3 OCI 1.1 referrers tag scheme incompatible with registry | Pin cosign **v2.4.1**. |
| D018 | `policy.json` sigstoreSigned alone not enough for trust verification | Also need `registries.d` with `use-sigstore-attachments: true`. |
| D031 | The existing `robot-sim` is a Cosmos3 world-model **generator** (image-to-video), not a physics sim | This is why we're replacing it with SO-ARM/Gazebo — completely different paradigm. The plumbing around it doesn't care. |
| D035 | `vla-training` is actually a WAM (world-action model), not a VLA | Use accurate naming in this project from the start. |
| GPU Operator | GPU Deployments must use `Recreate` strategy, never `RollingUpdate` | With one GPU, RollingUpdate deadlocks — new pod can't schedule while old pod holds the GPU. Learned in both thor-testing and grid-resilience-showcase. |
| vLLM CUDA | Must call `torch.zeros(1, device="cuda")` before importing vLLM | CUDA pre-init required against OpenRM driver. May not apply to ACT serving but worth knowing. |

## RHEM / flightctl (added 2026-09-08)

D024 makes RHEM the device plane (package-mode `flightctl-agent` 1.3 on the Fury host; a RHEL 10
VM as the desktop stand-in). thor-testing ran flightctl 1.1/1.2 on a CS10 bootc image, so the
reusable pieces are the install block, the enrollment CLI, and the trust config — not the topology.

| thor-testing source | What it is | Reuse notes |
|---|---|---|
| `derived-image/Containerfile:21-23` | flightctl EPEL10 repo (`rpm.flightctl.io/flightctl-epel10.repo`) | Copy into `device/provision.sh` as-is. |
| `derived-image/Containerfile:59-62` | Pinned agent install (`dnf install --setopt=install_weak_deps=False flightctl-agent-1.2.0-1.el10` + `systemctl enable`) | Same pattern, now **1.3.0**, package mode on a running host rather than a bootc layer. |
| `DEPLOYMENT_GUIDE.md:160-181` | Enrollment: `flightctl certificate request --signer flightctl.io/enrollment --output embedded` → `/etc/flightctl/config.yaml`, start the agent, approve | Directly reusable; add our labels at approval (`fleet`, `site`, `gpu`, `policy_device`, `zenoh_router`). |
| `DEPLOYMENT_GUIDE.md:110-136` | D005 RBAC workaround (`flightctl-admin` dropped from a per-org RoleBinding; `system:cluster-admins` vs `cluster-admins`) | Only if `flightctl get fleets` returns 403 on 1.3 — kubeadmin is in `system:cluster-admins`, so probably not. Port to `gitops/rhem-config/rbac.yaml` if it bites. |
| `derived-image/config/policy.json` | `sigstoreSigned` policy for the internal registry (D015) | Base for the Fleet's inline `/etc/containers/policy.json`; re-point at `quay.io/jary/*`, add `rekorPublicKeyPath` (D026). |
| `derived-image/config/registries.d-internal-registry.yaml` | `use-sigstore-attachments: true` (D018) | Base for the Fleet's inline `registries.d/quay-jary.yaml`. |
| D023 | Mask flightctl's greenboot auto-configure service; it hid a latent `oc` path bug | Irrelevant in package mode (no greenboot). Relevant again the day bootc returns. |

**What thor-testing never built** — so nobody goes looking for it there:
- No `Fleet` CR, no device labels, no application delivery (model + runtime went Argo →
  MicroShift over the ACM cluster-proxy, not through RHEM)
- No `ResourceSync`, no `Repository`, no CatalogItem
- Hub installed by hand with Helm, not under GitOps
- RHEM absent from `DEMO_RUNBOOK.md` — enrollment + the OS plane were the whole story

## What's genuinely new (not in thor-testing)

1. **The SO-ARM producer** — Gazebo + ROS 2 + ACT policy rollout emitting curator-compatible JSON.
2. **The LeRobot ACT fine-tune pipeline** — replacing Cosmos Vision SFT.
3. **Single-node topology** — collapsing hub + device onto one SNO. thor-testing had OSD-on-AWS as
   the hub and MicroShift-on-Thor as the device. The manifests that assumed a tunnel between them
   (RHACM cluster-proxy, D007) need to be dropped/adapted.
4. **Gazebo visualization** — making the arm visible in a browser. The `gz-camera-stream` plugin
   and `rhork` viewer from `github.com/RHPhysicalAI/` are candidates (Olga evaluating, APPENG-6261).

## thor-testing repo structure reference

```
~/redhat/git/thor-testing/
  gitops/
    flywheel/           <- curator, sync-agent, robot-sim, dashboard, kafka, dreamer, etc.
    vllm-cosmos3/       <- blue/green model serving (deployment, service, namespace)
    observability/      <- perses, tempo, otel dashboard
    hub-training/       <- manifest-consumer (Kafka -> training trigger)
    edge-workloads/     <- namespace, smoke-test
  tekton/               <- buildah cross-arch, cosign sign, modelcar package pipelines
  pipeline/             <- cosmos3_finetune_pipeline.py (KFP pipeline definition)
  DEMO_RUNBOOK.md       <- 6-beat demo script (Short Cut + Full Live)
  DECISIONS.md          <- ~D037 decision log
  DEPLOYMENT_GUIDE.md   <- two-tier deployment guide (we're collapsing to one-tier)
  PROJECT-BRIEF.md      <- original project brief
  PROJECT_STATUS.md     <- status as of 2026-08-19
  dream-comparison/     <- v1 vs v2 before/after visuals
```
