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

The SO-ARM producer must emit JSON with these fields:

```json
{
  "episode_id":     "string  -- uuid4",
  "timestamp":      "string  -- ISO 8601 UTC",
  "scene":          "string  -- task name",
  "model_version":  "string  -- e.g. soarm-act-v1",
  "has_failure":    "bool    -- true = injected failure, curator always rejects",

  "generation": {
    "status":       "string  -- 'ok' if rollout completed, 'error' if sim crashed",
    "latency_ms":   "number  -- rollout wall time"
  },

  "policy": {
    "status":       "string  -- 'ok' if ACT returned action, 'fallback' if timeout",
    "chunk_size":   "number  -- timesteps in action chunk",
    "smoothness":   "number  -- mean abs delta between consecutive action steps"
  },

  "avg_smoothness": "number  -- same as policy.smoothness (top-level for curator)",
  "task_success":   "bool    -- did the arm complete the task?"
}
```

### Curator scoring (adapt, don't rewrite)

The existing curator (`gitops/flywheel/curator.yaml`) scores on:
- Gate 0: `has_failure` -> always reject (keep unchanged)
- Gate 1: `generation.status != "ok"` -> penalty (keep unchanged)
- Gate 2: `policy.status` not ok/fallback -> penalty (keep unchanged)
- Gate 3: `avg_smoothness` above threshold -> penalty (retune threshold for ACT output)
- **Gate 4 (new):** `task_success == false` -> penalty

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
