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

> **Resolved (observed 2026-09-04):** MinIO is now backed by a PVC — `minio-data` (RWO, 50Gi,
> bound to `minio-pv`) in the `minio` namespace, provisioned since this decision. The episode
> corpus now persists across pod restarts. The 50Gi sizing is fine for curated JSON + small
> ported LeRobot datasets, but tight if raw MCAP bags (~2Gi each) are ever uploaded — size up
> before wiring the bag-data upload (Phase 2.5 step 6).

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

---

## D018 — Wire the upstream Rosetta recorder (not a hand-rolled one); two-stage MCAP → LeRobot

**Date:** 2026-09-04

**Context (Phase 2.5, step 1 — recorder recon):** the loop records no training data (D017). The
task was to map `pai_data_collection`'s interface and decide between wiring the upstream recorder
or scripting our own (`ros2 bag record`). Reconnaissance of the running `so-arm-sim` /
`act-inference` containers established the full upstream recording pipeline:

- **`pai_data_collection` is a *contract*, not a recorder.** It ships one file that matters:
  `config/rosetta/so_arm101.yaml` — a LeRobot recording spec mapping ROS topics → dataset
  features: two cameras (`/wrist_camera/image_raw`, `/static_camera/image_raw`, resized 480×480),
  `observation.state` from `/joint_states` (6 joints), `action` from
  `/forward_position_controller/commands`, `fps: 50`, `rad2deg`, feature names matched to LeRobot
  `so101_follower` for `lerobot-replay` compatibility, and `recording: {storage: mcap}`. No launch
  files, no node. (This is the same contract the inference entrypoint feeds the policy runner after
  stripping `recording`/`max_duration_s`, which the runner's loader rejects.)

- **The recorder is `rosetta`'s `episode_recorder_node`** (`episode_recorder_launch.py`). A
  lifecycle node (auto-configure + auto-activate) that loads the contract, subscribes to its
  topics, and exposes **three start/stop interfaces**:
  1. **`RecordEpisode` action** at `/episode_recorder/record_episode` (`{prompt}`) — start on goal
     accept, stop on **cancel** or `default_max_duration` timeout; feedback carries
     `seconds_remaining` + `messages_written`. Result carries `bag_path` + `messages_written`.
  2. Service `~/start_recording` (`rosetta_interfaces/srv/StartRecording`, `{prompt}`) — non-action
     start (for Foxglove clients).
  3. Service `~/cancel_recording` (`std_srvs/srv/Trigger`) — stop the active recording.
  Plus `~/delete_last_bag`. Output: **one MCAP rosbag directory per episode** at
  `bag_base_dir/<sec>_<nsec>/` (+ `metadata.yaml`, prompt stored under `lerobot.operator_prompt`).
  Default `bag_base_dir` is `/workspaces/rosetta_ws/datasets/bags`, `storage_id: mcap`.

- **The MCAP → LeRobot converter is `rosetta.port_bags`.**
  `python -m rosetta.port_bags --raw-dir <bags> --repo-id <name> --contract so_arm101.yaml` walks a
  directory of bags and writes a **LeRobot v2 dataset** (parquet + video) using the *same* contract
  decoders as live inference (so recorded data is guaranteed schema-consistent with what the policy
  consumes). Supports sharding and `--push-to-hub`; can produce a local dataset root. This is
  exactly the "dataset assembler" seam Phase 2.5 step 4 calls for.

**Decision:** **Wire the upstream recorder** — do not script our own. Concretely:
- Run `rosetta episode_recorder_node` with `contract_path` = the `pai_data_collection` `so_arm101.yaml`.
- Drive it from the **inference coordinator** via the **`RecordEpisode` action**, aligned to the
  existing episode lifecycle: send the record goal at episode `start` (right after the RunPolicy
  goal), cancel it at episode `end` (alongside the RunPolicy cancel). This reuses the
  `/flywheel/episode_control` phasing already in place — recording boundaries become exactly the
  scored-episode boundaries. `default_max_duration` stays a safety cap only.
- Convert curated episodes to a training LeRobot root with `rosetta.port_bags` (Phase 2.5 step 4).

**Placement — host the recorder in `act-inference`:** verified that image already contains
`rosbag2_py`, the `rosetta episode_recorder_node` executable, **and** `lerobot 0.5.1`, so one
container can record *and* port *and* train. It also runs the coordinator that triggers recording,
so the trigger is in-process-adjacent. Both host containers share the box with `--network host`, so
the contract topics (incl. the two 480×480 image streams) are visible over loopback zenoh — no real
network hop. Bags will be written to a **host-mounted volume** so they survive container recreation
(the D016 hot-patch lesson: nothing load-bearing lives only inside an ephemeral container).

**Why not script Rosetta / `ros2 bag record`:** the episode_recorder already does contract-driven
topic selection, per-episode segmentation via the action, transient-local (`/tf_static`) buffering,
sim-time `/clock` capture, and prompt metadata — and `port_bags` guarantees the recorded features
decode identically to inference. A hand-rolled recorder would reimplement all of that and risk a
train/serve schema skew. The upstream path is strictly better and is the credibility anchor (D015).

**Verified live (not assumed):** `episode_recorder` is absent from the running node graph (so
recording is genuinely off today, per D017); the contract's camera topics exist on the live graph
under the exact names above; `act-inference` imports `rosbag2_py` and `lerobot` and lists the
`episode_recorder_node` executable.

**Open sub-decisions for the wiring steps (2–4):** whether to run the recorder as its own process
in the inference entrypoint vs. a sidecar container; the host bag directory + how port_bags reads
it; `port_bags` local-root vs. synthetic `repo_id` for `lerobot-train` (BUILD-PLAN open question);
and rejected-episode bag retention (Phase 2.5 step 6). These are settled as those steps are built.

**Verified end-to-end 2026-09-04 (steps 2–5 of Phase 2.5):**
- **Step 2 — recording.** The recorder runs in the inference entrypoint (its own launched process,
  full contract, bags to host-mounted `/data/bags` → `~/flywheel-data/bags`); the coordinator
  drives it via the `RecordEpisode` action (resolved server name `/record_episode`). Captured real
  episodes: e.g. a 37.5 s bag with 1083 wrist + 1083 static frames, 1787 `/joint_states`, 1664
  `/forward_position_controller/commands` — a valid ~1.9 GiB MCAP (raw images) with the prompt in
  `metadata.yaml`. `_stop_recording` waits for the action result so the bag is finalized before the
  next episode.
- **Step 3 — contract.** Coordinator publishes `bags/<name>` on `/flywheel/episode_dataset`; the
  emitter stamps `dataset_path`. Confirmed in the curated JSON on the SNO node (curator preserves
  the field, sync-agent uploads it to MinIO under `episodes-curated/<model_version>/`). Also fixed
  the stale `MODEL_VERSION` on `so-arm-sim` → `upstream-act-teacher` (the teacher, per D015).
- **Step 4 — assembler.** `src/dataset-assembler/assemble_dataset.py` selects curated
  (pass + 3/3 + `dataset_path` + bag present) episodes, stages their bags, and runs
  `rosetta.port_bags`. Ported 4 curated episodes → a valid LeRobot v2 dataset (fps 50, 6254 frames,
  `observation.images.{wrist,static}` 480×480 video + `observation.state`[6] + `action`[6]). The
  ~8 GiB of raw bags compressed to **18 MiB** via h264 (note: lerobot 0.5.1 rejects `libx264`; use
  `h264`). port_bags uses the same contract decoders as inference, so recorded data is
  schema-consistent with what the policy consumes.
- **Step 5 — train + run.** `lerobot-train` (ACT, 5000 steps, cuda) on the flywheel-captured
  dataset produced a complete checkpoint (206 MB `model.safetensors` + config + processors).
  Mounted it into `act-inference` (`POLICY_PATH=/model`); the policy server loaded it
  (`Policy type: act | path: /model | Device: cuda`) and drove the arm in the sim (manipulating
  cubes). **"Trained on episodes the loop recorded, running in the sim" is now literally true.**
  The v1 policy is deliberately small (4 episodes) — quality/size is Phase 3's dataset-size ladder.

**Step 6 progress (2026-09-04):**
- **Rejected-bag retention — decided & implemented.** The coordinator tracks peak cubes per
  episode (mirrors the emitter → agrees with the curator's `task_success` gate) and deletes the
  just-recorded bag at episode end unless it reached 3/3. Only curated episodes persist; rejected
  episodes keep their JSON, not their frames. Runs as root inside `act-inference` (owns
  `/data/bags`). Env-gated (`PRUNE_REJECTED`, `CUBES_TARGET`). Backlog also cleaned (kept the 14
  curated bags, deleted ~25 rejected/orphan bags, freed ~66 GB). Note: bags are **root-owned** on
  the host (recorder runs as root), so host-user deletes silently fail — prune from inside the
  container.
- **MinIO persistence — already done.** MinIO is on a PVC (`minio-data`, RWO 50Gi), not emptyDir
  (D009 updated). 50Gi is fine for JSON + small ported datasets; size up before uploading raw bags.

**Step 6 completed (2026-09-04, later):**
- **Curated data + manifest in the hub (D019).** The assembler's `--from-minio` pulls the curated
  selection from `episodes-curated`, ports it, `--push-dataset` uploads the LeRobot dataset tarball
  to `episodes-data/<model_version>/<repo_id>.tar.gz`, and it publishes a dataset manifest to the
  `dataset-manifests` Kafka topic. All verified end-to-end. Raw bags stay on the host; raw-in-hub
  is deferred to Fury (D019). Re-assembly is idempotent (clears the target dataset dir first —
  `LeRobotDataset.create` refuses to overwrite).
- **`MODEL_VERSION` lineage on swap.** The coordinator (co-located with the served policy)
  publishes the label latched on `/flywheel/model_version`; the emitter adopts it over its own env
  default. Verified: with the sim emitter's fallback set to `unstamped`, episodes were stamped
  `upstream-act-teacher` from the coordinator — a checkpoint swap on `act-inference` alone now
  re-labels every episode.

---

## D019 — The hub stores the ported LeRobot dataset; raw bags stay on the host

**Date:** 2026-09-04

**Question (Phase 2.5 step 6):** the curated JSON reaches MinIO but the recorded frame data does
not. In what form should curated *data* land in the hub, given the MinIO PVC is 50Gi and a raw
MCAP bag is ~2Gi (≈400× the ported form)?

**What full bags buy over the ported LeRobot dataset:** re-portability. The bag is the negative,
the LeRobot dataset the print — `port_bags` is lossy and contract-locked (h264 video, resized to
480×480, resampled to fps=50, joint *position only*, frozen to the current `so_arm101.yaml`). Keep
the raw bag and you can re-port under a changed observation space (different resolution/fps, keep
velocity/effort, new joint mapping) or replay it faithfully. For the project's actual method
(ACT/LeRobot BC, D015) the ported form is exactly what training consumes — full bags add nothing
to training; they are a hedge for schema change and the Phase 3+ bootstrap experiments.

**Olga's dashboard does not change this.** Her read-only eval dashboard is metadata-only — it
groups the episode JSON records (success rate, cube-count distribution, smoothness, side-by-side)
by `model_version`, consuming `episodes-curated` + `episodes-rejected` (already in MinIO) and Kafka
manifests. It needs neither raw bags nor the LeRobot dataset. (Her MinIO/Kafka access was already
provisioned 2026-09-04: external NodePorts 30900/30903, a `rejected-mirror` CronJob, scoped
`olga-readonly` creds.) So the raw-vs-ported choice is independent of her.

**Decision (operator-approved):**
- **The hub's canonical trainable artifact is the ported LeRobot dataset**, uploaded as a single
  gzip tarball to `episodes-data/<model_version>/<repo_id>.tar.gz`. Small, directly trainable, fits
  50Gi with room to spare (~4.5 MB/episode). The assembler's `--push-dataset` does this; its
  `--from-minio` pulls the curated *selection* from `episodes-curated`, so the training-data
  lifecycle is hub-centric — the only host dependency is the raw frames, by design.
- **Raw curated bags stay on the desktop host** (1.8 TB, effectively free) as the re-porting hedge,
  pruned to curated-only (D018).
- **Archiving raw bags in the hub is deferred to Fury-prep.** It's the only thing that argues for
  growing the PVC, and it matters only for the multi-machine story, not the single-box demo. The
  operator doesn't mind growing the PVC when that time comes.

**Consequence:** "we retrain on the curated episodes the loop recorded" is fully satisfied by the
ported dataset in the hub. If a future method needs richer observations, re-port from the host bags
(or, on Fury, from bags archived in a grown bucket) — no re-collection required.

---

## D020 — Eval harness: fixed seed list (randomization ON), self-contained in the coordinator

**Date:** 2026-09-04

**Context (Phase 3, step 1 — D015 proof protocol):** the "you can see it get better" claim needs a
repeatable eval so v1…vk are compared apples-to-apples: a fixed N (~50) episodes per policy against a
**fixed scene set**, recording success rate (3/3), partial-placement distribution, and mean
smoothness. D015 sanctions either "randomization off, or a fixed seed list." The scorer
(`task_eval.evaluate_task`) and the emitter already produce every metric; what was missing is a driver
that runs a fixed N over repeatable scenes and aggregates, without feeding the training corpus.

**Decision — fixed *seed list*, randomization ON (not a single nominal scene).**
- `sim_reset.py` already randomizes the green cube (`cube_medium`) but seeds an *unseeded* `Random()`.
  The coordinator now passes a per-episode seed `EVAL_SEED_BASE + i` into `sim_reset --seed`, drawing
  the layout deterministically. Episode *i* of **every** policy evaluated at the same
  `(EVAL_SEED_BASE, EVAL_EPISODES, randomization ranges)` sees the **identical** scene — so
  success-rate deltas between checkpoints are attributable to the policy, not the luck of the draw.
- **Why ON, not a single nominal layout:** our trained checkpoints (like the teacher, D016) solve the
  one nominal layout trivially → success saturates at ~100% for every rung → **no curve**. A graded
  scene set is what gives the success-rate-vs-dataset-size proof any resolution, and it's the only way
  to get a real partial-placement/smoothness *distribution* out of the eval (one scene → N
  near-identical outcomes). A fixed seed list keeps full repeatability while spanning difficulty.

**Scene distribution (pinned starting config, all env-tunable and recorded per run):** match the
training-data collection distribution so the eval measures *in-distribution* BC quality —
`RANDOMIZE_CUBES=true`, `RANDOMIZE_ONLY=cube_medium`, `RANDOM_RADIUS=0.03`, `RANDOM_YAW_DEG=180`,
`RESET_ARM=true` (home the arm each episode for an identical clean start), `EVAL_EPISODES=50`,
`EVAL_SEED_BASE=1000`. Difficulty is calibrated empirically against the **first** checkpoint's spread:
if the smallest rung already scores near 0% or near 100%, widen/narrow `RANDOM_RADIUS` (or add
`cube_small`/`cube_large`) and re-run the whole ladder at the new pinned config. The eval config is
written into every results file so a re-run is verifiably identical.

**Harness placement — a self-contained eval mode in the coordinator (`act-inference`).** Selected by
`EVAL_MODE=true`; runs `run_eval()` instead of `run_forever()`, then exits (batch job). It owns
`/data` (host-mounted → `~/flywheel-data`, survives restart, on the GPU host where charts are made),
already imports `task_eval` (the *same* cube metric as production — the headline success signal) and
subscribes to `/joint_states` (smoothness, accumulated exactly as the emitter does). It writes
`/data/eval/<model_version>.json` (per-episode rows + aggregate: success_rate, cubes_hist,
mean_smoothness) and touches **no** curator / MinIO / Kafka:
- eval scenes can therefore **never contaminate the training corpus** (and the assembler's
  `--model-version` filter ignores the eval label even if an eval episode reaches the curator), and
- the eval doesn't depend on the hub being healthy — it's a reproducible measurement, not a producer.
The coordinator still publishes the eval `model_version` (latched) and signals `start`/`end`, so the
emitter self-labels any eval episode it happens to see; the coordinator's `/data/eval` file is
authoritative and complete regardless (fast early-stopped successes that trip the emitter's
`MIN_EPISODE_S` debounce are still scored by the coordinator).

**No recording during eval:** `run_eval` never calls the RecordEpisode action and pruning is moot —
50 episodes × ~2 GB of bags per policy would be pointless (we don't train on eval scenes). Run the
eval container with `RECORD=false` so the entrypoint skips launching the recorder too.

**Operational shape:** to eval a policy, recreate `act-inference` with the checkpoint mounted at
`/model` (`POLICY_PATH=/model`), `MODEL_VERSION=eval-<policy>`, `EVAL_MODE=true`, `RECORD=false`, and
the pinned scene env. It runs N seeded episodes and writes `~/flywheel-data/eval/eval-<policy>.json`,
then exits. Repeat per rung; the Phase 3 step-6 chart reads every `eval-*.json`.

**Files:** `src/inference-coordinator/coordinator.py` (`EVAL_MODE` path: `run_eval`, `_policy_window`,
`_write_eval_results`, seeded `_reset`, window-scoped smoothness accumulation) and
`src/sim-reset/sim_reset.py` (`--seed` → deterministic layout). Baked into `act-inference:latest`.

**Next (D015 step 3 — the dataset-size ladder):** assemble curated teacher sets of increasing size via
the assembler's `--limit` ({5, 10, 20, 40} episodes as the corpus allows), train ACT at each rung with
**epochs held ≈ constant** (steps scaled to `total_frames`: `steps = round(E · total_frames / batch)`,
E and batch pinned, read `total_frames` from each rung's `meta/info.json`), then run this harness on
each checkpoint. v1 = smallest rung, v2 = largest; the expected rising success-rate curve is the
honest BC property (D015).

> **Superseded the same day by D021.** The from-scratch ladder was started (40-ep dataset assembled,
> rung 5 trained + partially evaluated) and abandoned: its v1 scored 0/3 on every evaluated seed with a
> timid arm — a from-scratch network whose entire experience is 5 episodes. The harness, seed
> mechanism, and pinned scene config above all stand; only the *subject* of the comparison changed.

---

## D021 — Self-improvement, not distillation: fine-tune the teacher on its own curated successes (revises D015)

**Date:** 2026-09-04

**The challenge (operator):** the flywheel's claim is that *the policy improves itself* — it runs, the
curator keeps its good rollouts, we retrain **that policy** on them, it gets measurably better. The
D015 ladder trains **fresh, random-init** ACT students on the teacher's demonstrations and shows
success rising with dataset size. Framed honestly that is *distillation*: "the good policy trained a
worse copy of itself, and with more data the copy gets closer to the original." Its v1 is a network
that has seen 5 episodes and can't do the task (0/3, verified live). That is not the flywheel story,
and it's a strange thing to put on stage. Why not do additional training on the **known-good policy**
and compare before/after?

**Where D015 over-reached.** D015 rejected self-improvement with: *"a policy that fails generates mostly
failures, so curating its own output yields thin data exactly where it's weak, and BC can't exceed its
demonstrations."* That is correct for a policy that **cannot** do the task (the weak-v1 world D015
was written in — curating a ~0%-success policy's output gives nothing to learn from). It does **not**
apply to a **capable** policy under a **harder condition**. Under one-random-cube (radius 0.03) the
teacher succeeds **38%** of the time (68 curated / 110 rejected over 178 loop episodes, measured from
MinIO). Curation is a *filter*, and filtering changes the distribution: the teacher's behavior contains
both the actions that handle an offset cube and the actions that fumble it; keep only the successes and
fine-tune, and the policy shifts toward the subset of its own behaviors that work on the hard cases.
This is **self-imitation / rejection-sampling fine-tuning** (the STaR pattern: sample → filter by
correctness → fine-tune on the filtered set → repeat). Its one precondition — *the base policy must
sometimes succeed on the hard cases* — is met, with 68 proofs. D015 conflated the two regimes.

**Decision (operator-approved):** the Phase 3 proof is **v1 = the teacher as shipped; v2 = the same
policy fine-tuned on its own curated successes under randomization**, compared on the identical seeded
eval (D020). Same weights lineage, measurably better on the exact condition it was weak on — that is
self-improvement, and it is what governance promotes.

**Protocol:**
1. **Condition:** one-random-cube, `RANDOMIZE_ONLY=cube_medium`, `RANDOM_RADIUS=0.03`. Already
   mid-range for the teacher (38%) — real headroom; no radius change. D020's pinned config holds,
   with `EPISODE_LEN=60` (the production window the 38% was measured under).
2. **v1 baseline:** eval the teacher with the D020 harness (N=50, seed_base 1000). Expect ≈38%.
3. **Fine-tune:** `lerobot-train --policy.path=<teacher snapshot>` on the assembled curated dataset
   (`flywheel-ladder`, 40 teacher successes; the corpus is now 68). Default ACT LR (1e-5), modest
   steps (~2 epochs) — conservative, because the eval-gate is the safety net.
4. **v2 eval:** identical harness, identical seeds. Compare success rate, cubes histogram, smoothness.
5. **Promote** only on measured improvement → sign → GitOps PR → blue/green swap (Phase 3 steps 4–5).
6. **Iterate (the strongest version):** run v2 in the loop, curate *its* successes, fine-tune → v3. A
   rising curve across flywheel rounds is the literal "each turn of the wheel it gets better."

**Honest risks, and what guards them:**
- *Success bias toward easy offsets* — the teacher succeeds more on small offsets, so the curated set
  under-represents the hardest scenes. One round improves within the range the successes cover, not
  beyond it. The fix is iteration (step 6), which is even more the flywheel.
- *Fine-tuning can degrade a strong policy* (overfit to a few dozen episodes, forget generality) if done
  sloppily. Low LR, modest steps — and the **eval-gate refuses to promote a round that got worse**.
  That is a feature of the governed-pipeline story, not a hole in the method.
- *The gain must clear noise.* A few points at an 85% baseline would not resolve; at a 38% baseline
  with N=50 it will. That is why the condition is pinned where the teacher is mid-range.
- *Normalization stats on fine-tune* — loading a pretrained policy for training may recompute input
  normalization from the new dataset. If v2 degrades unexpectedly, this is the first suspect.

**What carries over untouched:** the seeded eval harness and scene mechanism (D020), the assembler and
the 40-ep assembled dataset, the curated corpus in MinIO, training inside `act-inference`. The only
change is that training **starts from the teacher's weights instead of random init**, and v1/v2 are
teacher-before/after. The dataset-size angle survives as a bonus (fine-tune on 10 vs 40 successes →
"more curated data, bigger gain") with the good policy as the subject.

**Consequences:** BUILD-PLAN Phase 3 steps 2–3 ("dataset-size ladder", "v1 = smallest rung") are
superseded by this protocol; the from-scratch v1 (0/3) is kept as a documented negative result. The
1st Phase 3 exit criterion ("v1 vs v2 improvement demonstrable on the fixed eval set") is unchanged
in wording and now means the *right* thing.

**Round 1 — v1 baseline measured (2026-09-04, N=50, seeds 1000–1049, radius 0.03):** success
**74% (37/50)**, mean cubes 2.54, histogram 0/1/2/3 = 0/10/3/37, mean smoothness 0.0048.

- **Higher than the 38% loop estimate — and that gap is real, not noise.** The eval homes the arm
  every episode (`RESET_ARM=true`); the production loop runs `RESET_ARM=false` (cubes-only reset), so
  after a failure the next episode starts with the arm mid-reach and failures cascade. 38% is the
  loop's *operational* rate; 74% is the teacher's *clean-start* rate for this condition and is the
  honest v1 baseline. (Worth remembering when the loop's curated/rejected ratio is read as a
  success rate — it understates the policy.)
- **Failure mode is the right one.** 10 of the 13 failures are 1/3: the first cube is placed and the
  *randomized* green cube is fumbled. That is precisely the behavior success-filtered fine-tuning
  targets — v1 "fumbles" rather than face-plants.
- **Headroom ≈ 26 points.** Binomial SE at p=0.74, N=50 is ≈ 6%, so a ≥12-point gain is ~2σ on the
  rate alone; the **per-seed paired comparison** (same scene, v1 → v2) is the primary read because
  it removes scene difficulty from the variance. If round 1's gain is inside noise, round 2 should
  widen the condition (e.g. `RANDOM_RADIUS=0.04`) to open the gap rather than train longer.
- **Negative result kept for the record:** the from-scratch 5-episode policy (D020 ladder rung 1)
  scored 0/3 on 30 of 31 evaluated seeds (one 1/3), smoothness ≈ 0.0015 — a timid arm that never
  grasps. Same harness, same seeds. That is what "trained by the teacher from scratch on 5
  episodes" looks like, and why D021 replaced it.

**Round 1 — result (2026-09-04; fine-tune 15k steps ≈ 2 epochs on 40 successes, LR 1e-5; v2 eval on the
identical 50 seeds):**

| | success | 3/3 | 2/3 | 1/3 | 0/3 | mean cubes | smoothness |
|---|---|---|---|---|---|---|---|
| v1 teacher | **74%** (37/50) | 37 | 3 | 10 | 0 | 2.54 | 0.0048 |
| v2 fine-tuned | **80%** (40/50) | 40 | 3 | 7 | 0 | 2.66 | 0.0052 |

- **Paired per seed (same scene):** 10 improved, 6 regressed, 34 unchanged. **Failures fixed = 8**
  (seeds 1010, 1012, 1017, 1018, 1019, 1035, 1038, 1043); **successes broken = 5** (1003, 1007, 1008,
  1029, 1049). Net **+3**. The shift is exactly the targeted one — three 1/3 "fumbled the green cube"
  episodes became 3/3.
- **Motion quality unchanged:** on the 32 seeds both policies solved, smoothness is identical
  (0.0057 vs 0.0057). Fine-tuning did not degrade the policy's motion.
- **Fine-tuning genuinely moved the weights:** 153/234 tensors changed (the rest are frozen
  backbone/BatchNorm buffers), mean |Δw| ≈ 6e-4 — a sane update, not a no-op and not a blow-up.
- **Honest verdict: directionally positive, statistically inconclusive.** +6 points on the rate is
  ~1σ at N=50; the sign test on fixed-vs-broken (8 vs 5) gives p ≈ 0.58. The eval-gate should
  **not** promote on this alone. This is the "gain inside noise" case the baseline note anticipated.

**Round 2 — levers, in the order they preserve the flywheel story:**
1. **More curated data (cheapest, most on-message).** Fine-tune on *all* current successes (68+, and
   growing while the loop runs) instead of 40. Directly tests "more curated data → bigger gain"
   with the good policy as the subject — the dataset-size angle, kept.
2. **Iterate — the true flywheel.** Run v2 in the loop, curate *its* successes, fine-tune → v3.
   Small per-round gains that *compound* across rounds are the honest version of "each turn of
   the wheel it gets better," and a rising v1→v2→v3 curve is a stronger artifact than one jump.
3. **Harder condition (`RANDOM_RADIUS=0.04`)** to lower the baseline and open headroom — but the
   corpus was collected at 0.03, so in-distribution data needs re-collection at 0.04 first. Bigger
   change; reach for it only if (1)+(2) stall.
4. **Longer fine-tune (~4 epochs).** Cheap but the most likely to overfit; the eval-gate guards it.
5. **N=100 evals** to resolve a small true effect — sharpens the *measurement*, doesn't improve the
   policy. Worth it once a round is believed to be real.

*Not recommended:* declaring victory on round 1. The number is real but it isn't proof yet.

**Round 2 plan (operator-decided 2026-09-04) — the fine-tune ladder, then close the loop:**
- **Two questions, two experiments.** (A) *the ladder*: fine-tune the **original** teacher on
  {20, 40, 80, 160} of its curated successes, each rung from the teacher's weights, epochs held
  constant — one variable (data quantity), clean chart. (B) *the self-improving loop*: promote the
  best rung, blue/green-swap it into the loop (Phase 3 step 5), let **it** collect, fine-tune v3 from
  its weights on the union corpus — that is step 7, "close the loop". A isolates the data effect; B
  shows compounding. In B two things change per round (collector *and* corpus), so A is what makes
  the data claim attributable. Sequence: **A first** (the loop is collecting the teacher corpus now),
  then B as the remaining Phase 3 build.
- **Top rung 160.** Collection target: 160 curated teacher successes at radius 0.03.
- **`RESET_ARM=true` on the collection loop** (was `false` — cubes-only reset). Homing the arm each
  episode roughly doubles the success rate (38% → ~74%, D021 baseline note) so collection is ~2×
  faster, and training episodes start the way eval episodes do. The ~70 already collected under
  cubes-only reset stay in the corpus: they are successes-only (valid demonstrations either way; the
  mid-reach starts are if anything more diverse). Rollback container: `act-inference-prereset-bak`.
- **Nested, regime-balanced rungs via seeded shuffle** (`~/rung_plan.py`): rung N = the first N of a
  seeded shuffle of the final 160, so 20 ⊂ 40 ⊂ 80 ⊂ 160 and every rung samples uniformly across
  collection time. Chronological nesting was rejected because it would make small rungs = old
  regime and large rungs = old+new, confounding size with regime. Round 1's 40-rung (80%) is a
  preliminary point; the ladder retrains 40 from the fixed 160 set.
- **Epochs constant at ~2** (`steps = round(0.25 × frames)`, batch 8), LR 1e-5, from teacher weights.
- **Eval:** identical D020 harness for every rung (N=50, seeds 1000–1049); loop parked once for the
  batch of four. If rung deltas sit inside N=50 noise, bump to N=100 for the top and bottom rungs.

