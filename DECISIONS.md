# Decisions Log

## D001 — Defer VFIO GPU passthrough to Phase 2-3

**Date:** 2026-09-01
**Context:** Phase 0 calls for VFIO passthrough of the RTX 5090 into the SNO VM so the GPU
Operator can expose `nvidia.com/gpu: 1`. However, Phases 0-1 require no GPU workloads — the
hub plane (Argo CD, MinIO, Kafka, RHTAS, observability) is entirely CPU-bound. VFIO passthrough
would make the desktop headless (Intel iGPU is disabled in BIOS, no display output without the
5090) and kill the active desktop session (Xorg, GNOME, Steam, VS Code, etc.).

**Decision:** Stand up SNO without GPU passthrough. Defer VFIO binding until Phase 2-3 when
training/inference workloads actually need GPU inside the cluster. The desktop stays fully
functional as a daily-use machine in the meantime.

**Trade-off:** Phase 0 exit criteria for `nvidia.com/gpu: 1` schedulable and `cuda-vectoradd`
smoke test are deferred. Everything else — SNO running, network accessible, resource baseline —
is met without the GPU.

**Revisit:** When Phase 2 (SO-ARM producer) or Phase 3 (LeRobot ACT training) needs GPU
scheduling inside OpenShift. At that point: add `intel_iommu=on iommu=pt` to GRUB, bind
`vfio-pci` for `10de:2b85` (VGA) and `10de:22e8` (Audio) in IOMMU group 18, reboot headless,
pass GPU to VM.

---

## D002 — Desktop IP changed from 10.0.0.4 to 10.0.0.48 after bridge setup

**Date:** 2026-09-01
**Context:** Creating the `br0` network bridge for KVM VM bridged networking required moving
`eno2` under the bridge. The bridge got a new DHCP lease at `.48` instead of the original `.4`.

**Decision:** Accept the DHCP-assigned IP. The desktop is now at `10.0.0.48` and the SNO VM
is at `10.0.0.49`. Both are DHCP — consider setting DHCP reservations on the router if IP
stability becomes important.

**Impact:** SSH config and any scripts referencing the desktop IP need to use `.48` (or the
Tailscale IP `100.76.33.18` which is stable).

---

## D003 — SNO VM resource allocation

**Date:** 2026-09-01
**Context:** Desktop has 125 GiB RAM, i9-13900K (24 cores / 32 threads). Need to size the
SNO VM large enough for the full flywheel workload while keeping the host functional.

**Decision:**
- **vCPUs:** 16 (host-passthrough) — leaves 16 threads for host desktop use
- **RAM:** 72 GiB (73728 MiB) — leaves ~53 GiB for host
- **Disk:** 250 GB qcow2 thin-provisioned on root NVMe (1.1 TB free)
- **Network:** bridged to `br0`, VM at 10.0.0.49 via DHCP

**Measured baseline (SNO idle, no workloads):**
- Platform CPU: 1264m / 15500m allocatable (8%)
- Platform memory: 12677 MiB / ~71 GiB allocatable (17%)
- Remaining for workloads: ~14 vCPUs, ~59 GiB RAM
- Host memory with VM running: 93 GiB available out of 125 GiB

**Decision gate check:** 59 GiB available inside the VM for workloads, well above the 64 GB
threshold in the build plan. No need to increase VM allocation or fall back to MicroShift.

---

## D004 — OCP version: 4.17.56

**Date:** 2026-09-01
**Context:** Matched the existing `oc` 4.17.11 client on the desktop. Used stable-4.17 channel.

**Decision:** OCP 4.17.56 installed via agent-based installer. RHCOS
417.94.202607240132-0, Kubernetes v1.30.14, CRI-O 1.30.14.

---

## D005 — Agent-based installer rendezvousIP must match VM's actual IP

**Date:** 2026-09-01
**Context:** First install attempt used `rendezvousIP: 10.0.0.50` in the agent-config, but
DHCP assigned the VM `10.0.0.49`. The assisted-service tried to bind to `.50`, which didn't
exist on the node, causing the agent to fail with "Connection refused" on port 8090.

**Decision:** Regenerated the ISO with `rendezvousIP: 10.0.0.49` matching the DHCP-assigned
address. The MAC address `52:54:00:f1:00:01` gets the same DHCP lease consistently, so this
works. For future reference: if using DHCP with the agent-based installer, the rendezvousIP
must match whatever the node actually gets.

---

## D006 — DNS: /etc/hosts for API access

**Date:** 2026-09-01
**Context:** The SNO cluster API is at `api.sno-flywheel.local:6443`. No DNS server on the
local network resolves this.

**Decision:** Added `10.0.0.49 api.sno-flywheel.local` to `/etc/hosts` on the desktop.
Same entry needed on any machine that wants `oc` access (e.g. Jeremy's Mac).

**Future:** If a wildcard is needed for routes (`*.apps.sno-flywheel.local`), either add
individual `/etc/hosts` entries per route or set up a lightweight DNS (dnsmasq) on the desktop.

---

## D007 — Dropped OSD/multi-cluster manifests for SNO

**Date:** 2026-09-01
**Context:** thor-testing used a two-tier topology (MicroShift-on-Thor as edge, OSD-on-AWS as
hub). Three manifest files assumed that topology and have no equivalent on single-node SNO.

**Dropped:**
- `flywheel/mirrormaker2.yaml` — bridged edge Kafka to hub OSD fleet Kafka over TLS
- `hub-training/manifest-consumer.yaml` — hub-side Kafka consumer that triggered training via
  Data Science Pipelines on the OSD hub
- `edge-workloads/smoke-test.yaml` — ACM/cluster-proxy delivery canary for thor

**Adapted:**
- `sync-agent.yaml` / `dreamer.yaml` — S3_ENDPOINT changed from OSD MinIO route to local
  `http://minio.minio.svc:9000`
- `dashboard.yaml` — kubeconfig fallback path changed from MicroShift to SNO
  (`/etc/kubernetes/kubeconfig`)

**New:**
- `minio/minio.yaml` — local MinIO deployment (was external on OSD in thor-testing)
- `flywheel/hub-credentials.yaml` — S3 credentials for local MinIO

---

## D008 — SELinux hostPath labeling on RHCOS

**Date:** 2026-09-01
**Context:** Flywheel services (curator, sync-agent) use hostPath volumes at `/var/lib/episodes`.
On RHCOS with SELinux enforcing, containers get MCS labels (e.g. `s0:c27,c9`) but files created
by the `core` user via SSH have no MCS categories (`s0`), causing permission denied errors even
with world-readable file permissions.

**Resolution:** Run `sudo chcon -Rt svirt_sandbox_file_t /var/lib/episodes/` on the node to
label the directory tree for container access. Must be re-applied if new directories are created
directly on the host.

**Future:** When the SO-ARM producer writes episodes from inside a pod, the files will inherit
the container's SELinux context automatically. This is only an issue for host-created test files.

---

## D009 — MinIO uses emptyDir (non-persistent) for Phase 1

**Date:** 2026-09-01
**Context:** The MinIO hostPath at `/var/lib/minio` failed with permission errors (same SELinux
issue as D008). Switched to emptyDir for fast iteration.

**Trade-off:** MinIO data is lost on pod restart. Acceptable for Phase 1 smoke testing. Switch
to a PVC or properly-labeled hostPath when data persistence matters (Phase 2+).

---

## D010 — Phase 1 smoke test results

**Date:** 2026-09-01
**Context:** Injected two dummy episodes matching the curator contract from THOR-TESTING-REUSE.md.

**Results:**
- Episode `smoke-test-001` (clean, task_success=true, smoothness=0.05):
  PASS score=0.800 -> curated -> uploaded to MinIO `episodes-curated/soarm-act-v1/smoke-test-001.json`
  -> manifest published to Kafka `episode-manifests` -> moved to `sent/`
- Episode `smoke-test-002` (has_failure=true):
  REJECT score=0.000 ("injected-failure") -> moved to `rejected/`

**Conclusion:** Full data path works end-to-end on SNO: sim -> curator -> sync-agent -> MinIO +
Kafka. All OSD-specific assumptions resolved. No code changes needed to curator or sync-agent
logic — only endpoint configuration.

---

## D011 — Perses TraceQuery panels incompatible with COO-managed Perses

**Date:** 2026-09-01
**Context:** The thor-testing Perses dashboard (`edge-flywheel-dashboard.yaml`) uses 5 panels
with `TraceQuery` type to query Tempo trace data. The Cluster Observability Operator's bundled
Perses frontend only ships with `TimeSeriesQuery` panel support — no trace panel plugin.

**Symptom:** All panels show "panel does not support queries of type 'TraceQuery'. Supported
types: TimeSeriesQuery."

**Decision:** Accept for now. The Tempo backend is working (Perses proxy returns 200 on all
trace queries), and the Jaeger UI route provides direct trace browsing. The dashboard panels
will need to be rebuilt for SO-ARM trace format in Phase 2 anyway — do that work then, either
as TimeSeriesQuery panels or by installing a standalone Perses with the trace plugin.

---

## D012 — Phase 1 complete: full service inventory

**Date:** 2026-09-01

**Services running on SNO (OCP 4.17.56):**

| Namespace | Service | Purpose |
|---|---|---|
| `minio` | MinIO | S3-compatible object store for episodes |
| `flywheel` | edge-kafka | KRaft single-broker Kafka |
| `flywheel` | curator | Episode quality scoring |
| `flywheel` | sync-agent | S3 upload + Kafka manifest publish |
| `flywheel` | dashboard | Flask UI (NodePort 30801 + Route) |
| `openshift-gitops` | Argo CD (8 pods) | GitOps delivery (no apps configured yet) |
| `observability` | Tempo (TempoMonolithic) | Distributed tracing backend |
| `observability` | Perses | Observability dashboard UI |

**Verified:** dummy episodes flow end-to-end through the pipeline.
**Desktop IP:** 10.0.0.48. **SNO VM IP:** 10.0.0.49.
**Not yet deployed:** robot-sim (replaced by SO-ARM in Phase 2), dreamer (needs GPU/Cosmos3),
vllm-cosmos3 blue/green (replaced by ACT serving in Phase 3), RHTAS/sigstore (Phase 3).

---

## D013 — Desktop GPU split: sim in SNO, ACT inference on the host GPU

**Date:** 2026-09-02
**Context:** VFIO passthrough of the RTX 5090 into the SNO VM stays deferred (D001) — it makes
the desktop headless and kills the active session. But Phase 2 needs the pre-trained ACT policy
to actually drive the SO-ARM in the loop, which is GPU work. Passing the GPU through the cluster
was the original plan for that; it's off the table for now.

**Decision:** Split the workload across the box instead of passing the GPU into the VM:
- **Sim in SNO** — Gazebo + SO-ARM101 runs CPU-only inside the cluster (`so-arm-sim.yaml`).
- **ACT inference on the host** — the policy runs in a container directly on the host against
  the RTX 5090 (`docker/Dockerfile.gpu-inference`, `inference-entrypoint.sh`), no cluster GPU.
- **Wiring is zenoh cross-network** — the host inference container runs `rmw_zenoh` in **client
  mode** connecting to the in-cluster zenoh router (`docker/zenoh-connect.json5`). This is the
  bridge that lets host-GPU inference and in-cluster sim share ROS 2 topics across the VM boundary.

**Host-side components** (all under `src/`, run on the desktop, not in the cluster):
- `episode-emitter/` — watches rollouts, builds curator-contract episode JSON, and **POSTs to a
  curator HTTP receiver** (sim moved to host, so the old hostPath handoff was replaced with HTTP).
  `task_eval.py` does real task-success detection from Gazebo cube poses.
- `inference-coordinator/coordinator.py` — phases episodes cleanly: cancels the policy goal between
  episodes and waits for confirmation so episode boundaries aren't ragged. Inference drives the
  episode cadence; sim and inference no longer fight over lifecycle.
- `sim-reset/sim_reset.py` — resets between episodes. **Cubes-only reset** (randomized cube
  positions) — a full Gazebo world reset was tried and reverted because it kills the ros2_control
  controllers and they don't reliably reactivate.
- `camera-bridge/camera_bridge.py` — MJPEG stream of the sim for the browser (HTTP-only host
  bridge; TLS/proxy paths caused mixed-content and buffering problems).

**Curator changes:** task success is now a **hard gate**, not a scoring penalty (`04b2756`).
Cube count is tracked as the **peak** reached during an episode (arm can knock a placed cube off),
and every curation-log row shows `X/3`.

**Status:** this is the active work on branch `desktop-gpu-split` (~40 commits ahead of `main`,
all 2026-09-02). Still iterating on clean episode boundaries and reliable cube-count reporting.
This is Phase 2 (the producer) — it generates curated episodes; it does **not** train or promote
anything. See the Phase 3 gap note below.

**Revisit:** VFIO passthrough (D001) is still the eventual path for a single-box story on Fury,
but the host-split is the pragmatic Phase 2 answer on the desktop and may well be what ships.

---

## D014 — Building a deliberately-imperfect v1 ACT baseline (the "sometimes succeed, sometimes fail" hunt)

**Date:** 2026-09-03 (records work through the OpenCode session ending 2026-09-02 17:03)
**Context:** The flywheel demo has to *show the robot getting better* — collect → curate → train
→ promote → visibly improved. That requires a **v1 policy with a real quality gap** for v2 to
close. The pre-trained upstream ACT policy from `ros-physical-ai/demos` is **too good for this**:
it either places all three cubes cleanly or gets *catastrophically stuck* retrying one learned
correction forever — a binary outcome, no natural mix of partial successes and diverse failures.

**Approach:** Train our **own intentionally-undertrained ACT policy from scratch** on a small set
of collected episodes, directly via LeRobot on the host RTX 5090 (not the KFP pipeline — that
infra is still unbuilt). An undertrained policy fails *diversely* (overshoots, mis-times the
grasp, drops the cube) which is the natural success/failure distribution the demo needs. We also
offset the green cube (`cube_medium`, randomized ±2–3cm from nominal each episode via
`RANDOMIZE_ONLY`) to add controlled difficulty.

**Sweep so far** (episodes × train-steps; checkpoints live on the host, not in the repo):

| Config | ~Epochs | Result |
|---|---|---|
| 5 ep × 500 steps | ~0.4 | loss 15.2 — nowhere near converged |
| **5 ep × 5k steps** | **~4.5** | **loss 0.305 — closest to grasping (best so far)** |
| 5 ep × 15k steps | ~13.5 | overfit — arm barely moves |
| 10 ep × 10k steps | ~5 | better — approaches/jitters at the cube |
| 20 ep × 10k steps | ~2.5 | all 0/3, managed the first cube once |
| 30 ep × 10k steps | ~2.5 | mostly 0/3 — still can't grasp the first block |

**Working theory:** the sweet spot needs **both** enough episodes (diversity) **and** enough
epochs (~4.5 = convergence depth for precise grasps). More episodes at shallow depth (~2.5 epochs)
never converges enough to execute the first grasp; too many epochs on few episodes overfits into a
frozen arm.

**Where we left off:** a **20 ep × 20k steps (~4.5 epochs, ~30 min)** run was kicked off right as
the 2026-09-02 session ended — combining the best-converging epoch depth with more diversity. **Its
result was never recorded.** First action on resume: check whether that checkpoint exists on the
host and how it behaves (does it finally get a natural sometimes-succeed/sometimes-fail mix?).

**Still unbuilt (real Phase 3):** no KFP/DSP training pipeline, no KServe/vLLM serving, no
cosign/RHTAS, no promotion-PR mechanism in `gitops/`. Today's training is manual LeRobot runs to
find the v1 baseline; the governed pipeline that *promotes* v1→v2 is the next build once we have a
baseline policy that produces a workable success/failure distribution.

> Supersedes an earlier draft of this decision that claimed "no training has been run" — that was
> wrong. Manual LeRobot training runs were the entire focus of the 2026-09-01→02 session.

---

## D015 — Stay with ACT (imitation), don't pivot to reward-based RL; prove improvement via curated dataset size

**Date:** 2026-09-03

**Question raised:** Is the "weak ACT v1 → strong ACT v2" approach the right way to *prove
improvement* for the demo, or should we pivot to reward-based RL (many sessions, a learning curve),
or something easier?

**Decision:** Stay with **ACT / behavior cloning via LeRobot**. Do **not** pivot to RL.

**Rationale:**
- The demo's value is the **governed pipeline** (curate → train → sign → GitOps-promote →
  blue/green), not ML sophistication. The ML only has to produce a clear, reproducible, believable
  "v1 → v2 improved" moment. ACT clears that bar at far lower risk.
- **RL is the wrong pivot here:** contact-rich manipulation RL from scratch is sample-inefficient
  and unstable (a multi-week research effort, not a demo build); a live learning curve is
  stage-unreliable; and it **abandons the upstream-alignment credibility anchor** — the whole
  anti-"vendor land-grab" story is aligning to `ros-physical-ai/demos`, which is ACT/LeRobot.
  Pivoting to a bespoke RL stack makes it "Red Hat's weird thing" instead of governance added to
  the community's thing.
- The user's instinct that RL would be "another rabbit hole" is correct; it's a longer road to a
  demo that's harder to run and off-message.

**Honest caveat (the reframe):** As pursued so far, "v1→v2" is really *undertrained ACT →
fully-trained ACT* — a **staged** before/after we construct, not organic self-bootstrapping. True
self-bootstrapping (training a policy on its *own* curated rollouts) is genuinely weak for BC: a
policy that fails generates mostly failures, so curating its own output yields thin, low-quality
data exactly where it's weak, and BC can't exceed its demonstrations. So:

- **Make the improvement axis the curated dataset SIZE, not training-step tuning.** v1 = ACT on a
  small curated set; v2 = ACT on a larger curated set the flywheel accumulated. "More curated good
  episodes → better policy" is a *true* BC property, honest, maps onto the flywheel story, and
  shows as a clean success-rate-vs-dataset-size chart.
- Training demonstrations must be **good** (the strong policy's successes, or the upstream teleop
  set filtered through the curator) — not the weak policy's lucky rollouts.
- Drop the "sometimes-succeed/sometimes-fail weak policy" balancing act as a *proof* requirement —
  it was chasing demo aesthetics. The proof needs **two policies with a clear success-rate gap and
  a working metric**, which we now have.

**Concrete proof protocol (Phase 3):**
1. **Metric (done today):** task success = 3/3 cubes on tray, via the fixed scorer. Also log
   partial-placement count and mean smoothness.
2. **Eval harness:** run a fixed N (~50) episodes per policy under a **fixed, repeatable cube
   layout set** (randomization off, or a fixed seed list) so comparisons are apples-to-apples.
   Record success rate, partial rate, smoothness distribution.
3. **Dataset ladder:** build curated training sets of increasing size from good demonstrations
   (upstream 60-demo corpus, and/or strong-policy rollouts the curator passed): e.g. {5, 10, 20,
   40} episodes. **Hold epochs ≈ constant** (~5 epochs; scale `--steps` with dataset size) so the
   variable is *data*, not training length.
4. **Train ACT** at each rung → v1 = smallest, v2 = largest.
5. **Artifact:** success-rate vs dataset-size bar chart + before/after video (v1 fumbling, v2
   clean) + smoothness distributions. This is the "you can see it get better" moment.
6. **Governance wiring (still to build):** v2 checkpoint → cosign sign → KServe modelcar OCI →
   GitOps promotion PR → blue/green swap. This is the actual product story the improvement showcases.

---

## D016 — Task-success scorer was blind; fixed via pose topic. Coordinator early-stop added.

**Date:** 2026-09-03

**Scorer bug (root cause + fix):** `task_eval.py` read cube poses via `gz model -m <cube> --pose`,
which first resolves the world through the generic **`/gazebo/worlds` service. That service does not
respond in this gz build (Kilted / gz-sim9)** — every query timed out, `get_cube_pose` returned
`None`, and **every cube scored as not-placed → 0/3 always.** It went unnoticed because until a
policy actually placed cubes (the 40×40 run), the broken scorer was never exercised against a real
success. The world-scoped services/topics *do* respond. **Fix:** read poses directly from the
`/world/pai_world/pose/info` topic (`gz topic -e`), parse `name`/`position` blocks. Tray footprint
and `is_on_tray` thresholds unchanged. Verified live: peak-poll now logs real counts and episodes
record `SUCCESS cubes=3`.

**Early-stop (new coordinator feature):** the coordinator now ends an episode as soon as the task
is complete (3/3 cubes) **and the arm has settled** — instead of waiting out the full `EPISODE_LEN`.
"Settled" = joint-position motion below `REST_EPS` sustained for `REST_HOLD_S`, past an `EARLY_MIN_S`
floor. **Rest is motion-based, not a fixed home pose — the SO-ARM rest pose is not all-zeros.** The
costly cube check runs only once the arm has held still. Env-tunable (`EARLY_STOP`, `REST_EPS`,
`REST_HOLD_S`, `EARLY_MIN_S`). Confirmed firing; good runs now end in ~1300–2000 steps instead of
the full window.

**Findings (observed, scorer now agreeing with the operator's eyes):**
- **40ep×40k weak-v1** is directionally right: consistently **2/3**, reliably fails the third-block
  grasp. Smooth, deliberate motion.
- **Upstream known-good policy is robust to a 3 cm green-cube offset** — still lands 3/3.
  Randomization at 3 cm alone will *not* manufacture a failure mix for the strong policy; need a
  larger `RANDOM_RADIUS` or all-cube randomization to challenge it. (A weak policy is tripped much
  sooner.)

**⚠️ Deployment status — NOT yet permanent:** both fixes are running but **hot-patched**, not baked
into images or committed:
- `task_eval.py` → `docker cp`'d into the running `so-arm-sim` container.
- `coordinator.py` + `task_eval.py` → bind-mounted into `act-inference` from `/home/jary/patches/`.
A container rebuild-from-image or a `/tmp` wipe (host reboot) reverts them. **Follow-up:** commit
both files, rebuild the sim image (`:sim-only`) and the `act-inference` image, and redeploy so the
fixes persist. Also copy the kept weak checkpoints out of `/tmp/weak-training/` (root-owned;
needs the operator's sudo) to persistent storage.

> **Resolved 2026-09-03 (same day):** both fixes committed (`c9faf22`), `task_eval.py` added to the
> GPU inference image (`abff9b1`), both images rebuilt, both containers recreated from images with
> no bind-mounts, and the loop verified end-to-end (early-stop firing, real cube counts). The weak
> checkpoints in `/tmp` remain a pending operator action.

---

## D017 — The flywheel moves scores, not training data; restore episode recording (Phase 2.5)

**Date:** 2026-09-03

**Finding:** the data plane as built curates **episode metadata only**. `episode_emitter.py` emits
a JSON record — `task_success`, `cubes_placed`, `avg_smoothness`, `steps` — and the sync-agent
ships that to MinIO/Kafka. No observation frames or action vectors are recorded anywhere. Every
training run to date (D014) used the upstream HuggingFace corpus
`francocipollone/rospai_sim_arm101_place_cubes_on_tray`, not anything the flywheel produced. The
loop today is *sim → score → store score*.

**This is a divergence from the design, not the design.** The episode contract in
`THOR-TESTING-REUSE.md` already carries a `rosbag_path` field — *"relative path to the MCAP rosbag
for training"* — and states that the full rollout data is stored separately for training while the
JSON is the lightweight metadata the curator scores on. `PROJECT-BRIEF.md`'s architecture reads
*"LeRobot ACT fine-tune on curated data … sim picks up v2 policy → better rollouts → loop closes."*
`BUILD-PLAN.md` Phase 3 lists *"Input: curated episodes from MinIO."* The recording path was in
the contract and the brief; the implementation dropped it, and it went unnoticed because the
upstream corpus was always available to train on.

**Why it matters:** without recording, the demo's central claim — "we retrain on the curated
episodes" — is not true; the honest description would be "we score the sim and train on a
third-party dataset." It also blocks every genuinely self-improving variant (`BOOTSTRAP-LOOP.md`),
all of which need the loop's own episodes to be trainable.

**Decision:** restore episode recording as a dedicated phase — **Phase 2.5, Close the Data Loop**
(`BUILD-PLAN.md`) — before the governance work in Phase 3:
- Record each rollout in **LeRobot format** (frames + joint states + actions), aligned to the
  coordinator's `start`/`end` signals, reusing the upstream recorder (`pai_data_collection` /
  Rosetta) rather than writing one.
- Replace the never-populated `rosbag_path` with a populated `dataset_path`; keep the JSON metadata.
- Sync-agent uploads the episode **data** alongside the curated JSON; the Kafka manifest carries
  the data URI.
- Add a **dataset assembler** that builds a training LeRobot dataset from curated shards in MinIO.
- Make `MODEL_VERSION` reflect the running policy on every swap — lineage becomes load-bearing.

**Consequence for D015:** the dataset-size ladder stands, but the dataset becomes
**flywheel-captured curated episodes** (teacher = the upstream policy running in our sim, gated by
the curator), not the HuggingFace corpus. The proof is then honest end-to-end: the loop recorded
the data, the curator selected it, training consumed it.

**Sequencing:** Phase 2.5 is a prerequisite for Phase 3 (real input) and Phase 3+ (autonomous
frontier data). The plan is written without schedule constraints; the operator decides what to
take on before Fury and what to defer.
