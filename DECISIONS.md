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
~~The coordinator still publishes the eval `model_version` (latched) and signals `start`/`end`, so the
emitter self-labels any eval episode it happens to see~~ — **revised 2026-09-08:** those emitted eval
episodes reached the curator and MinIO (135 `eval-*` objects across both buckets, polluting the
lineage grouping the eval dashboard relies on). They were deleted; eval mode now sends **no**
signals to the emitter, and the curator discards any `eval-*` label. The coordinator's `/data/eval`
file was always the authoritative record.

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
  > **REVERTED within ~15 min (2026-09-04).** In the running loop `RESET_ARM=true` produced 0 curated /
  > 6 rejected and the operator saw the arm misbehave in Gazebo; the original container was restored
  > (`RESET_ARM=false`, cubes-only reset). It had *not* misbehaved in the eval container (v1 = 74%),
  > so the v1/v2 comparison stands, but the loop stays on cubes-only reset and collection stays at
  > ~35%. The `RESET_ARM=true` container is parked as `act-inference-resetarm-bak` for diagnosis.
  > Cause undiagnosed (operator's call). It is **not** "zeros is out-of-distribution": a fresh
  > sim spawns the arm at all-zeros (measured), so zeros is a valid start. Resolution: the
  > coordinator now does **rest-pose recovery on failure** (fec35b4, 5e93833) — publishes in-node
  > (not via `ros2 topic pub`), bootstrapped with `REST_POSE=0,…` (the spawn pose), refined by
  > the pose the policy settles into after a success, persisted to `/data/rest_pose.json`, and
  > run on startup too, so a restart into a rough spot self-heals and failures don't chain.
  > **Verified live (2026-09-04):** first success → learned settled pose (shoulder_lift −1.73,
  > elbow 1.46, wrist_flex 1.33 — a tucked pose, not zeros: D016 was about the *settled* pose);
  > the next two failures (1/3, 2/3) each triggered a recovery that converged with max error
  > **0.000 rad**. The zeros bootstrap pin did *not* converge on the first cycle (0.65 rad off in
  > 5 s), so it is only a fallback. Operator tool `src/pose-ui/pose_ui.py` (host :8090) shows both
  > cameras + live joints and can pin a chosen pose (`pinned: true`), which learning never
  > overwrites (5cdb33b).

**Incident (2026-09-04 night) — disk full during the round-2 ladder.** With failure recovery the
loop collected ~50 successes/hr and was left running for ~8 h after the 160 target: 514 bags,
798 GB, root filesystem at 100%. Consequences: the rung-160 training's log append hit ENOSPC, the
driver mis-read that as a training failure and skipped the eval phase (rungs 20/40/80 trained
fine; the 160 run itself kept going and finished once space was freed); the SNO VM (qcow2 on the
same disk) went unreachable — libvirt pauses a VM on ENOSPC; resume needs the operator (sudo).
Freed ~55 GB of safe space (docker build cache, dangling images, 8 partial bags, 3 superseded
rollback containers, anonymous dangling volumes). **Not** deleted: curated bags, the operator's
older rollback containers (~16 GB), other projects' named volumes. **The loop is parked and must
not be restarted until a bag-retention policy exists** — at ~1.6 GB/bag the D019 "keep every
curated bag on the host" hedge does not survive a recovery-rate loop. Options: keep only bags
referenced by an assembled dataset; cap bag count (FIFO); or archive to the hub (D019 deferred
this to Fury-prep — it just arrived early).

> **Resolved 2026-09-08 (operator-decided): bag retention = port everything, keep the proof bags.**
> The hub is back (VM resumed; guest clock had frozen 3d 9h at the pause → MinIO
> `RequestTimeTooSkewed`; fixed by setting the node clock from the host). All curated teacher
> successes (~453 JSONs; 507 bags on disk) are being ported into one LeRobot dataset
> (`flywheel-teacher-all-2026-09-08`, pushed to `episodes-data` with a Kafka manifest per D019).
> Once verified, raw bags whose episode is in that manifest are deleted; the **172 bags that
> predate the ladder assembly** (a superset of the 161 the proof trained on) are kept as the
> re-port hedge, as are any bag whose JSON arrived after the pull (ported separately later).
> Expected: ~520 GB freed. Going forward the loop must not accumulate raw bags unbounded: port
> and prune on a schedule (or cap bag count). Docker rollback containers from earlier sessions
> removed (+17.5 GB).
> **Executed 2026-09-08 12:15:** corpus assembled — `flywheel-teacher-all-2026-09-08`, **450 episodes /
> 709,879 frames**, verified (lerobot loads), pushed to `episodes-data` (1.5 GB) with its manifest.
> Prune classes: **proof 172 kept (288 GB), ported 290 deleted (482 GB), unported 0, orphan 46 kept
> (75 GB)** — the orphans are real 3/3 bags whose curated JSON never reached MinIO during the outage;
> port them directly with `rosetta.port_bags` later. Disk: 18 GB → 467 GB free. The resident
> `bag_watchdog.sh` parks the loop at the bag cap / 60 GB free.

**Round 2 — ladder result (2026-09-05; fine-tune the teacher on N seeded-nested curated successes,
~2 epochs each, LR 1e-5; every rung on the same 50 seeds, radius 0.03):**

| curated successes | success | mean cubes | 0/1/2/3 | fixed / broken vs v1 | net | sign p |
|---|---|---|---|---|---|---|
| 0 (teacher v1) | 74% (37/50) | 2.54 | 0/10/3/37 | — | — | — |
| 20 | 60% (30/50) | 2.28 | 0/16/4/30 | 7 / 14 | **−7** | 0.19 |
| 40 | 56% (28/50) | 2.32 | 0/12/10/28 | 6 / 15 | **−9** | 0.08 |
| 80 | 76% (38/50) | 2.64 | 0/6/6/38 | 8 / 7 | +1 | 1.00 |
| **160** | **86% (43/50)** | **2.70** | 1/6/0/43 | **8 / 2** | **+6** | 0.11 |

- **The curve is not monotonic, and that is the finding.** Fine-tuning a capable policy on *too
  little* of its own data **degrades** it: 20 and 40 break 14–15 seeds the teacher already solved
  while fixing 6–7 (overfitting toward a small subset / forgetting). 80 is neutral. **160 is a clean
  gain: +12 points, 8 fixed vs only 2 broken**, and smoothness on jointly-solved seeds improved
  (0.0057 → 0.0053). More curated data → the same policy gets better — but only past a threshold.
- **This is the eval-gate's justification in one table.** A gate on "success-rate improvement vs
  the incumbent" refuses 20/40/80 and promotes 160. The demo story becomes stronger, not weaker:
  the pipeline *protects* the fleet from a bad retrain.
- **Small-N fine-tunes are high-variance, subset-dependent.** Round 1's 40-episode fine-tune (a
  different 40, same recipe) scored 80%; this ladder's 40 scored 56%. Which episodes you get matters
  at small N; at 160 the outcome is robust. Do not read a single small-N result as a trend.
- **Statistics:** at N=50, 160's +12 points was ≈2σ with a paired sign test of p≈0.11 — strong,
  not conclusive. Per the pre-committed rule the endpoints were re-scored on **50 new seeds
  (1050–1099)**: teacher **72%** (36/50), 160ep **86%** (43/50) — the first block replicated
  almost exactly (74% / 86%). **Merged N=100: teacher 73/100 → 160ep 86/100, +13 points; paired
  20 fixed / 7 broken, net +13, sign-test p = 0.019.** Mean cubes 2.51 → 2.73. The improvement is
  real and stable across independent scene sets.
- **v2 = the 160-success fine-tune** (`~/flywheel-data/train/ft-ladder-160ep`). Phase 3 exit
  criterion "v1 vs v2 improvement demonstrable on the fixed eval set" is **met and confirmed**.
  Records: `docs/eval-records/phase3-ladder/` in the repo; report: `src/eval-report/ladder_report.py`.
- **Nested, regime-balanced rungs via seeded shuffle** (`~/rung_plan.py`): rung N = the first N of a
  seeded shuffle of the final 160, so 20 ⊂ 40 ⊂ 80 ⊂ 160 and every rung samples uniformly across
  collection time. Chronological nesting was rejected because it would make small rungs = old
  regime and large rungs = old+new, confounding size with regime. Round 1's 40-rung (80%) is a
  preliminary point; the ladder retrains 40 from the fixed 160 set.
- **Epochs constant at ~2** (`steps = round(0.25 × frames)`, batch 8), LR 1e-5, from teacher weights.
- **Eval:** identical D020 harness for every rung (N=50, seeds 1000–1049); loop parked once for the
  batch of four. If rung deltas sit inside N=50 noise, bump to N=100 for the top and bottom rungs.


---

## D022 — The governed promotion pipeline: build for the target (GB10/GB300), shim the desktop

**Date:** 2026-09-08
**Context:** Phase 3 steps 4–5 (BUILD-PLAN). The v1→v2 improvement is proven (D021); what remains
is the governance that carries a candidate from curated data to a running policy: assemble →
train → eval-gate → package → sign → promotion PR → blue/green swap. thor-testing supplies the
shape (surveyed: `tekton/`, `pipeline/cosmos3_finetune_pipeline.py`, `gitops/vllm-cosmos3/`,
`hub-training/manifest-consumer`). Two facts from that survey drive this design: its Gate 1 was
a **training-loss floor** (not task success), and its one real outage was a **partial blue/green
flip** (two of three files edited — commit 5e3e87a made the flip atomic). The operator's steer:
the deliverable lands on GB10 (proof) and GB300 Fury (demo), where the GPU is *inside* the
cluster — the desktop's host-GPU split (D013) is a stand-in, not the architecture.

## Design principle: build for the target, shim the desktop

The deliverable lands on **GB10 (proof) and GB300 Fury (demo)** — single boxes with the GPU
**inside the cluster**. On those, thor-testing's pipeline shape transfers almost directly:
in-cluster training job → eval gate → `crane append` modelcar → `cosign sign` (RHTAS Rekor) →
promotion PR that flips blue/green → human merge → Argo sync → `Recreate` rollout on the one GPU.

The desktop is the stand-in where the GPU is *outside* the cluster (D013). Everything
desktop-specific below is marked **[desktop shim]** and is deleted by the Fury port. The pipeline
definition, gate, packaging, signing, PR, and gitops manifests are written once, for the target.

## Stages (one pipeline, KFP v2 on RHOAI Data Science Pipelines — see fork 1)

| # | stage | target (GB10/GB300) | [desktop shim] | contract out |
|---|---|---|---|---|
| 0 | **trigger** | `manifest-consumer` (thor-testing shape) on `episode-manifests`; fires when ≥ `TRIGGER_THRESHOLD` new curated successes since the last promoted version | same (runs in SNO already-compatible) | pipeline run with `model_version`, `incumbent` |
| 1 | **assemble** | `assemble_dataset.py --from-minio --model-version <collector>` inside the pipeline pod; pushes tarball + manifest (already built, Phase 2.5) | same, but bags are on the host → run the assembler on the host, push to MinIO; the pipeline pod *pulls the tarball* | `episodes-data/<mv>/<repo_id>.tar.gz` + Kafka manifest |
| 2 | **train** | KFP component, `nvidia.com/gpu: 1`, `lerobot-train --policy.path=<incumbent checkpoint> --dataset.root=<pulled tarball>`; steps = 0.25 × frames (D021 recipe); emits `training_meta.json` | **host runner** (`~/ft_chain.sh` lineage) trains on the RTX 5090 and uploads the checkpoint dir to MinIO `checkpoints/<mv>/`; the pipeline's train component becomes "wait for + pull checkpoint" | checkpoint dir (~200 MB) + `training_meta.json` |
| 3 | **eval gate** | KFP component drives the D020 harness against the sim **in-cluster** (sim + policy both in-cluster on the target): candidate and incumbent, same seeds, N=100; writes `eval_report.json`; **`sys.exit(1)` on fail** (thor's hard-stop pattern — downstream never runs) | harness runs on the host (`eval_policy.sh`), report uploaded; component pulls and judges | `eval_report.json` {rates, paired fixed/broken/net, p, verdict} |
| 4 | **package** | `crane append` (pinned version, not `:debug`/latest): flat copy of the checkpoint dir → `models/act/` on `ubi-micro`, **`--platform linux/arm64`** (Grace is aarch64; desktop is amd64 → build both or per-target) | same component; amd64 on the desktop | image by **digest** |
| 5 | **sign** | `cosign sign --key --rekor-url <RHTAS Rekor> --tlog-upload=true`, **cosign v2.x pinned** (v3's OCI-1.1 referrers scheme breaks the internal registry — thor D014/D022; our brief says 2.4.1, thor moved to 2.6.5 for a CVE) | same | Rekor entry |
| 6 | **promote** | PyGithub: branch `promote/<mv>`, **one commit** editing the three files atomically (green digest+replicas 1, blue replicas 0, service selector) + PR body carrying `eval_report.json` | same | PR URL; **human merge = Gate 3** |
| 7 | **swap** | Argo syncs; KServe-style modelcar Deployment pair, `strategy: Recreate`, `revisionHistoryLimit: 0`, one side `replicas: 1` at a time; Service selector `color:` picks the live one | **host swap agent**: a small host loop watches `gitops/act-serving/` for the promoted digest and recreates `act-inference` from it (Recreate semantics; the zenoh action server is the "service") | new policy live; its rollouts flow with the new `MODEL_VERSION` (Phase 3 step 7) |

## The eval gate — specified from our own data (D021)

- **Predicate:** candidate vs incumbent on the **same fixed seed set** (D020), **N=100**
  (seeds 1000–1099). Promote iff `net = fixed − broken > 0` **and** paired sign-test `p < 0.05`.
  Also record success-rate delta and mean-cubes delta; a demo-friendly restatement is
  "≥ +10 points and it must not break more than it fixes" — both rules pass v2, both reject the
  20/40/80 rungs.
- **Why paired, why N=100:** at N=50 the +12-point v2 gain was p≈0.11; paired at N=100 it was
  p=0.019. Unpaired rate deltas at N=50 cannot resolve the effect sizes one round produces.
- **Why a gate at all is the story:** fine-tuning on 20 or 40 successes *degraded* the policy
  (net −7/−9). A flywheel without this gate makes the fleet worse. thor-testing's Gate 1 was a
  training-loss floor — necessary, not sufficient; ours is task success against the incumbent.
- **Trigger threshold:** `TRIGGER_THRESHOLD` ≈ **160 new curated successes** (thor's default was 10 —
  that would fire a degrading retrain every few minutes at our rates).

## What transfers from thor-testing unchanged
- Three-file atomic flip; `Recreate`; one replica at a time (the 5e3e87a outage is the lesson).
- `crane append` for the layer (D017), `cosign sign … --tlog-upload` (v2.x), `policy.json` **and**
  `registries.d` `use-sigstore-attachments: true` on the device (D015+D018).
- Promotion-PR shape (branch → commit → PR with evidence body); human merge as the last gate.
- `manifest-consumer` as the trigger.

## What changes
- Train/eval components are the D021 recipe, not Cosmos SFT; no HF-cache staging, no guardrail
  bundle; the artifact is a flat 200 MB directory (workspace 1 Gi, timeouts minutes not hours).
- The gate is a paired success-rate comparison, not a loss floor.
- Pin `crane` and `cosign` versions; no runtime `curl … latest`.
- Multi-arch: the modelcar and the runtime images must build for `linux/arm64` (GB10/GB300) and
  `linux/amd64` (desktop). `Dockerfile.gpu-inference` on aarch64 Blackwell is the Phase 4 risk item.

## Forks — decided 2026-09-08 (operator)

1. **Pipeline runtime.** (a) **RHOAI Data Science Pipelines (KFP v2)** — thor-testing's choice,
   one implementation for desktop and target, the on-message "same governed pipeline" story;
   cost: the `rhods-operator` install on SNO (headroom is fine: node at 7% CPU / 39% mem).
   (b) OpenShift Pipelines (Tekton) only — lighter, every stage a Task; but train/eval as Tekton
   tasks is awkward and it diverges from thor-testing. **Decided: (a) RHOAI DSP / KFP**, with Tekton tasks reused inside for build/sign as thor did.
2. **Signing.** (a) **RHTAS** (`rhtas-operator` in the catalog: Fulcio/Rekor/TUF in-cluster) —
   the brief's choice, transparency log for the demo; (b) key-only cosign, no Rekor — simpler,
   weaker story. **Decided: (a) RHTAS.**
3. **Desktop serving swap.** (a) **host swap agent** watching gitops (keeps "GitOps promote →
   swap" honest on the desktop with no in-cluster GPU); (b) CPU KServe in-cluster (works, slow,
   and not what ships). **Decided: (a) host swap agent**, deleted on Fury.
4. **Gate rule.** (a) **paired net > 0 with p < 0.05 at N=100**; (b) "≥ +10 points and broken ≤
   fixed" at N=50. **Decided: (a)** as the rule, (b) as the plain-English restatement on stage.
5. **Trigger threshold.** 160 new curated successes (evidence-based). Tunable. **Decided: 160.**

## Build order (once decided)
1. Install operators: OpenShift Pipelines, RHTAS, RHOAI (DSP + KServe). Clean the stale
   `dreamer`/`robot-sim` resources so the `flywheel` Argo app is Synced.
2. `gitops/act-serving/`: blue + green Deployments (modelcar initContainer, `Recreate`), Service
   with `color` selector — written for the target; [desktop shim] host swap agent reads the same
   files.
3. `pipeline/act_flywheel_pipeline.py` (KFP): the 7 stages above; components thin, calling the
   scripts we already have (assembler, harness, report).
4. `tekton/`: package-modelcar + cosign-sign tasks adapted (flat layer, pinned images, arm64+amd64).
5. `manifest-consumer` re-pointed at our Kafka with the new threshold.
6. Run it end-to-end on the desktop: v2 (already trained + evaluated) as the first candidate —
   package → sign → PR → merge → swap. Then let the loop run v2 (round B) and let the pipeline
   fire on its own for v3.

**Built 2026-09-08 (same day):**
- Operators subscribed via GitOps (`gitops/operators/`, Argo app `operators`): OpenShift Pipelines
  `latest`, RHTAS `stable`, RHOAI `stable`. Stale Cosmos-era `dreamer`/`robot-sim` leftovers removed;
  `flywheel` app Synced/Healthy.
- `gitops/act-serving/` (Argo app `act-serving`): blue/green Deployment pair + color Service, target-
  shaped, both `replicas: 0` on the desktop. **Registry for the desktop: `quay.io/jary/soarm-act-modelcar`**
  (no internal-registry route on SNO; host has quay creds) — a pipeline parameter; the target uses the
  internal registry. **cosign v2.6.5** (v2.x as required; thor moved off 2.4.1 for a CVE fix).
- Seeds packaged on the host with `crane append` (flat `models/act/` on ubi-micro, amd64) and signed:
  v1 `upstream-act-teacher` → `sha256:d5e5897f…` (blue now pins it), v2 `act-v2-ft160` →
  `sha256:d1337aa5…` (the first promotion candidate). Both `cosign verify` clean.
- **Desktop shim contract** (identical artifacts to the in-cluster components on the target):
  pipeline → Kafka `training-triggers` `{run_id, candidate, incumbent, collector, incumbent_checkpoint,
  steps_per_frame, eval_n, eval_seed_base}`; host runner (`src/host-runner/host_runner.py`) assembles,
  trains (D021 recipe), parks the loop, runs the D020 harness on candidate **and** incumbent (N=100,
  same seeds), restores the loop, writes the paired `eval_report.json` (rule: net > 0 ∧ p < 0.05),
  uploads `checkpoints/<candidate>/pretrained_model.tar.gz` + `eval/<run_id>/…` to MinIO, emits
  `training-results`. `src/swap-agent/swap_agent.py` applies the three act-serving files to the host
  container (cosign verify → crane export → Recreate) — verified in check mode against blue.
- `pipeline/act_flywheel_pipeline.py` (KFP v2, compiled): trigger+wait → gate (`sys.exit(1)` on
  fail) → package → sign → **one-commit** promotion PR. `gitops/flywheel/manifest-consumer.yaml`:
  trigger at 160, inert until `DSP_URL`/`TRAINING_PIPELINE_ID` are set.
- Gotcha recorded: lerobot writes checkpoints root-owned 0600; the runner `chmod -R a+rX`s them so the
  host user can tar/package them.

**Verified end-to-end on the cluster (2026-09-08, afternoon):**
- Operators installed via GitOps after two SNO-specific blockers: OLM bundle unpacks need a longer
  deadline on this VM (`operatorframework.io/bundle-unpack-timeout: 60m` on the OperatorGroups) and the
  cluster had **no StorageClass** — `gitops/storage/` adds `local-path-provisioner` as the default
  (lab only; the target uses the platform's storage). RHOAI `DataScienceCluster` (pipelines + KServe
  only) and the `DataSciencePipelinesApplication` in `flywheel` are Ready; RHTAS `Securesign` is fully
  Ready (Rekor in-cluster at `http://rekor-server.trusted-artifact-signer.svc`, now the pipeline's
  default `rekor_url`; sub-CRs that failed before storage existed had to be recreated).
- Pipeline `act-flywheel-promotion` uploaded to DSP (id `99ec0aab-…`); `manifest-consumer` running
  against it (threshold 160). Access recipe in the ops runbook (port-forward + SA token, https).
- **First governed promotion run (`act-v2-ft160` vs `upstream-act-teacher`):** the host runner received
  the trigger over Kafka in seconds, reused the trained checkpoint and the N=100 records, and the
  pipeline's **gate PASSED on the real report (73% → 86%, 20 fixed / 7 broken, p = 0.019)**; the
  modelcar was **packaged by `crane append` in-cluster and pushed to quay, then signed by cosign** in
  the pipeline. The run pauses at the promotion-PR step until the `github-token` Secret exists.
  Earlier attempts failed on, in order: host boto3 inheriting an AWS config (S3 Accelerate), the
  dockerconfigjson Secret key name, and the host docker config using `credsStore: pass` with empty
  `auths` (the quay login had to be extracted from the credential helper). All recorded in the ops
  runbook gotchas.
- **Run 5 signed into Rekor** — `tlog entry created with index: 0`, the first entry in this cluster's
  transparency log (RHTAS), for `sha256:83984b5f…`. The PR step then failed with GitHub 403 *"Resource
  not accessible by personal access token"* on `git/trees`: the fine-grained PAT lacked Contents write
  (and org access) — a token-scope issue, not the pipeline. Re-issued token → run 6.
- **Run 6 (2026-09-08) — the governed promotion, end to end, unattended:** trigger → host runner →
  gate PASS (73% → 86%, 20/7, p = 0.019) → `crane append` → `quay.io/jary/soarm-act-modelcar@sha256:bdb513ca…`
  → cosign sign with **Rekor index 1** → **PR #1** `Promote act-v2-ft160 (73% -> 86%)`
  (https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/1): one commit editing the three
  `act-serving` files atomically, evidence table in the body. Human merge = Gate 3; Argo then syncs
  the flip and, on the desktop, the swap agent applies it to the host container. **Phase 3 steps 4–5
  are built and exercised.** Note for the desktop: the PR sets green `replicas: 1` (correct for the
  target); on the desktop that pod stays Pending (no in-cluster GPU) — a visible shim artifact, not
  a fault; the swap agent keys on the Service color + digest, not on replicas.
- **PR #1 merged (operator, Gate 3) → Argo synced (Service → green, green = `bdb513ca`) → the swap
  agent verified the signature, exported `models/act`, recreated `act-inference` serving
  `act-v2-ft160` → v2's rollouts flow through the emitter/curator/sync-agent under the new lineage
  (`episodes-curated/act-v2-ft160/`). Phase 3 step 7 — the loop is closed.** Round B is autonomous:
  `manifest-consumer` now counts v2's successes and, at 160, starts the pipeline with v2 as incumbent
  (its checkpoint from MinIO) — v3 is fine-tuned *from v2 on v2's curated successes*, gated against
  v2 on the same 100 seeds, and lands on **blue**.
- **One GPU, blue/green:** the PR step now reads the live side from the Service and targets the
  *other* Deployment — candidate → 1, live → 0, Service flipped, all in one commit; both sides
  `Recreate`, so the incumbent's pod releases the GPU and the candidate's pod (briefly Pending on
  `nvidia.com/gpu`) schedules. Rollback is the same three edits reversed; the other side keeps the
  previous digest. (Fixed 2026-09-08: the first version always wrote green, which would have
  overwritten the live side in place on the second promotion.)
- **Artifacts on Hugging Face (private, `jeremyary/`, 2026-09-08)** — LeRobot-native, so they load the
  same way the upstream ones do (`LeRobotDataset("jeremyary/…")`, `--policy.path=jeremyary/…`),
  and the only copies that don't share the desktop's single disk: datasets
  `soarm-flywheel-teacher-all-2026-09-08` (450 eps) and `soarm-flywheel-ladder-160` (the proof corpus,
  with `rung_plan.json`); models `soarm-act-v2-ft160` (the promoted v2), `soarm-act-ft-ladder-{20,40,80}ep`
  and `soarm-act-teacher-ft40-round1` (the ladder evidence). Raw bags stay on the host (355 GB, D019).
  Phase 4 idea: the package step pushes each *promoted* checkpoint to HF as part of promotion.

---

## D023 — Demo runbook: the Full Live cut promotes a pre-trained, pre-evaluated candidate live

**Date:** 2026-09-08
**Context:** Phase 4 item 1 — re-skin thor-testing's six-beat runbook for SO-ARM (`docs/DEMO_RUNBOOK.md`).
BUILD-PLAN asks for a Short Cut (4–5 min, pinned run) and a Full Live (10–12 min, "live training +
promotion"). Measured on the desktop: fine-tuning on 160 episodes takes ~25 min on the RTX 5090, and
the D022 gate is a **paired N=100 eval per policy** at up to 60 s per episode — over an hour per
candidate. Neither fits a 12-minute slot; thor-testing hit the same wall (30-min SFT) and showed
the real artifacts instead of the training.

**Decision — Full Live = the governed pipeline runs live on a candidate whose training and eval
already happened.** The run is submitted on stage through the same DSP API and parameters the
`manifest-consumer` uses, with `candidate=act-v2-ft160`; the host runner recognises the existing
checkpoint and the two N=100 records and reuses them (its documented idempotent path, D022), so
trigger → gate → `crane append` → `cosign sign` (a **new** Rekor entry) → a **new** promotion PR
takes ~3 min (run 6: 15:01 → 15:04). The operator merges live; Argo (hard refresh) and one
swap-agent pass land v2 in ~2–3 min. **Live:** sim, curation, the pipeline, the signature, the
transparency-log entry, the PR, the merge, the sync, the swap, the badge flip, v2's first episodes.
**Pre-baked:** the checkpoint and the eval records (real artifacts from 2026-09-05/08). The runbook
says so in one paragraph ("the one honest shortcut") and the narration never claims training
happened on stage. Rejected: (a) train live — 25 min of nothing to watch, then the eval wall;
(b) shrink `eval_n` for the show — changes the gate rule and misrepresents the evidence.

**Consequences:**
- The Full Live needs a **reset to v1** before the show (blue live, `upstream-act-teacher`): the
  D022 rollback — three edits reversed in one commit on `desktop-gpu-split`, Argo hard refresh, one
  swap-agent pass. It also needs the stale `promote/act-v2-ft160` head branch deleted on GitHub, or
  the PR step fails on `create_git_ref`. While v1 collects, round B is paused (the consumer counts
  only `act-v2-ft160` episodes); it resumes when the demo re-promotes v2.
- The runner parks the collection loop around the eval even when it reuses records, so **the arm
  pauses ~1 min during the gate step**; the runbook narrates it rather than hiding it.
- **Beat 4 uses the Phase 3 static chart as the primary screen** until the eval dashboard exists
  (separate owner). Whatever replaces it must run from the frozen records in
  `docs/eval-records/phase3-ladder/` — the booth has no cluster and no Kafka (BUILD-PLAN rule).
- The operational dashboard's `TRIGGER_THRESHOLD` was 10 (thor default) while the consumer fires
  at 160; its bar read "44 / 10" with a permanent "training threshold reached" banner — set to
  **160** so Beat 2's screen doesn't contradict Beat 3's narration. The bar still counts local
  `sent/` files since the last Clear; the consumer's count is authoritative.
- Verified against the live system while writing (2026-09-08): the sim and camera bridge run on the
  **host** (`10.0.0.48:8081`), not the NodePort 30881 the Phase-0 ops extract lists (the in-cluster
  `so-arm-sim` is `replicas: 0`); there is **no graphical DSP run view** (RHOAI `dashboard`
  component is `Removed`) — Beat 3's screen is the runner/poller logs and the KFP API task list;
  `cosign verify --key` passes, tlog verification against RHTAS needs its TUF root on the host
  (follow-up); the Rekor UI needs a `rekor-server-…` hosts entry on the presenting laptop.
- Not executed in this session (documented as **[not rehearsed]**): the reset-to-v1 →
  live-run → merge → swap cycle end to end, and the sim-container restart. Both belong to the
  fallback-recording rehearsal (Phase 4 item 2).

> **Revised the same day (operator).** Nothing live is promised at the booth. Once the Fury
> arrives the committed work is standing the flywheel up on it; a GB300 training run, a coding
> agent or NVIDIA playbooks are stretch only. The **Short Cut backed by contingency recordings and
> durable artifacts is the plan of record**; the Full Live is conditional on the flywheel standing
> on the presenting box and a same-day rehearsal there. `docs/demo-kit/` holds the text artifacts
> that back each beat (run 6 logs and task states, PR #1, Rekor entry 1); the recordings and
> screenshots are Phase 4 item 2, now framed as the kit rather than as insurance.

---

## D024 — RHEM (flightctl 1.3) is the device plane; a RHEL 10 VM stands in for the device on the desktop

**Date:** 2026-09-08
**Context:** D007 dropped RHEM along with the OSD hub. An RHEM engineer asked how the demo integrates
with RHEM, and the honest answer after auditing both repos: thor-testing used RHEM for **enrollment +
the OS plane only** — no Fleet CR, no device labels, no application delivery, hub installed by hand
with Helm, nothing under GitOps; model + runtime went Argo → MicroShift over the ACM cluster-proxy.
This project has none of it. The original goal was *more correct* integrated use, not none, and RHEM
engineering has a Fleet → runtime + OCI ModelCar → edge device flow proven. The brief's Red Hat value
is the governed lifecycle (`PROJECT-BRIEF.md:10`); a model plane that never reaches a managed device
undersells it.

**Decision:** RHEM (flightctl 1.3) becomes the device plane, and Beats 5/6 run on it.
- **Topology.** Fury: the RHEL 10.2 host is the managed device — package-mode `flightctl-agent`,
  podman + NVIDIA CDI, GPU on the host; the SNO VM on the same host is the hub (RHEM, RHOAI DSP,
  RHTAS, Argo). Desktop: Ubuntu cannot run the agent, so a RHEL 10 KVM VM (plain qcow2 + cloud-init,
  **not bootc** — the Fury host is package-mode, the stand-in mirrors it) is the device and runs ACT
  inference on **CPU**. The Gazebo sim, the camera bridge and the train/eval host runner stay on the
  host RTX 5090 untouched; only the policy container moves, and only on the desktop.
- **The CPU stand-in is gated by a latency spike.** ACT amortises one forward pass per action chunk;
  on the VM with `policy_device=cpu` measure p95 forward latency (two 480×480 cams), `ros2 topic hz`
  on the commanded-action topic, and a 20-seed D020 eval vs GPU v2. Pass: p95 <
  0.5 × (`n_action_steps`/50 s), no gap > 40 ms, success within 10 points of 86%. Fallbacks in
  order: raise `n_action_steps`; lower the Gazebo real-time factor; **VFIO passthrough of the 5090
  last** (D001's headless objection stands).
- **Hub under GitOps.** Argo Application with a Helm OCI source (`quay.io/flightctl/charts`, chart
  `flightctl` 1.3.0, ns `flightctl`, UI on a Route). Fleets and CatalogItems are flightctl API
  objects, not k8s CRs, so Argo cannot apply them: a `Repository` + `ResourceSync` against
  `gitops/rhem/` renders them, applied once with `flightctl apply` and documented like the
  `argocd/*-app.yaml` bootstrap steps.
- **SNO goes 4.17 → 4.18 → 4.19 first.** The chart declares `kubeVersion: '>= 1.32.0-0'`; RHEM 1.3
  wants OpenShift 4.19+. Desktop SNO is 4.17.56 (k8s 1.30) and Helm will refuse. Do not dodge it
  with Argo's `helm.kubeVersion` override — that hides real incompatibilities on the box that
  matters. The Fury SNO is a fresh install: 4.19+ there regardless.
- **In the ROSCon demo:** Beat 5 = the promotion PR edits the Fleet in git → ResourceSync → RHEM
  rolls it out; Beat 6 = device application health in the RHEM UI + `flightctl console`. This
  replaces the Argo selector flip + host swap agent (D022 stage 7).

**Alternatives:** keep the in-cluster blue/green Deployment pair as the target (rejected — on the
Fury the device is the host, not the cluster, D025); bootc image-mode device (rejected for the
stand-in — it would not mirror the Fury host, and thor's greenboot/bootc lessons are the cost);
CPU KServe in-cluster on the desktop (D022 fork 3b, still rejected — not what ships).

**Consequences:**
- BUILD-PLAN gains **Phase 4.5 — RHEM device plane**, sequenced *after* the contingency kit (Phase
  4 item 2): today's Beat 5/6 clips are the fallback while the desktop is destabilised, and Beats
  5/6 are re-recorded once RHEM works on the desktop.
- New: `argocd/rhem-app.yaml` (+ an Argo repo Secret with `enableOCI: "true"`, hand-created),
  `rhem/bootstrap/{repository,resourcesync}.yaml`, `gitops/rhem/`, `device/provision.sh` (one
  arch-neutral script for VM and Fury), `docs/eval-records/cpu-spike.md`.
- Device labels carry the per-site differences (`site`, `gpu`, `policy_device`, `zenoh_router`,
  `arch`) so one Fleet template serves both boxes; `/etc/hosts` entries for the new routes on the
  Mac and the device VM (same failure mode as the Rekor UI, D006).
- Rootful quadlet for the demo (host networking + CDI + `/dev/shm` for zenoh have fewer unknowns);
  rootless is noted as the product recommendation, not built.
- Lineage contract unchanged: `MODEL_VERSION` env → coordinator → `/flywheel/model_version` →
  emitter → curator → MinIO → `manifest-consumer` `COLLECTOR`.

---

## D025 — `gitops/act-serving/`, the `act-serving` Argo app and the host swap agent are retired once RHEM has promoted

**Date:** 2026-09-08
**Context:** D022 built serving as a blue/green Deployment pair in the cluster (target-shaped, both
`replicas: 0` on the desktop) with a **[desktop shim]** host swap agent that recreates the host
container from the promoted digest. D024 moves serving onto the RHEM device — on the Fury that is
the host, not the cluster — so the in-cluster KServe-style path is no longer the target and the
shim has nothing left to shim.

**Decision:** retire `gitops/act-serving/`, `argocd/act-serving-app.yaml`, `src/swap-agent/` and
the `act-serving` Argo app — **after the RHEM path has promoted once**, not before. Git history
keeps them. The promotion step becomes a two-regex edit of one file,
`gitops/rhem/fleet-act-inference.yaml`: `soarm-act-modelcar@sha256:…` → the new digest and
`MODEL_VERSION: <old>` → the candidate. Same branch/commit/PR-body shape as PR #1; the body adds
`Rollback: git revert <sha>` and the Fleet URL. **The same commit bumps `COLLECTOR`/`INCUMBENT` in
`gitops/flywheel/manifest-consumer.yaml`** (hand-edited after PR #1 today) so the next round fires
on the new lineage — the D022 lesson (5e3e87a: a partial flip is an outage) applied to the new
file set.

**Why:** one file, one commit, one merge is a stronger Beat 5 than three files and a shim; the
Fleet is the product's rollout object and the RHEM UI shows the rollout without narration.

**Consequences:**
- Rollback = `git revert` + merge; the image volume is `reclaimPolicy: Retain`, so rolling back
  does not re-pull. Rehearse once.
- Human merge stays Gate 3; ResourceSync renders the Fleet; the device pulls the modelcar as an
  image volume, the container restarts with the new `MODEL_VERSION`, health reports back.
- D022's "target-shaped Deployment pair" section and D023's reset-to-v1 procedure (three edits
  reversed) describe the retired path; the runbook is rewritten in Phase 4.5 G.

---

## D026 — Product-fidelity drift acknowledged and scheduled: what is repaired before the Fury, what is deferred past ROSCon

**Date:** 2026-09-08
**Context:** the same audit that produced D024 inventoried where this project sits relative to
the Red Hat stack thor-testing ran. Some of the gap is (a) legitimate single-box simplification,
some is (b) tagged `[desktop shim]` with a Fury replacement, and some is (c) **silent drift with
no decision recorded**. Putting the whole table on record is the point of this entry.

| # | Area | thor-testing | flywheel today | Class | Fixed in |
|---|---|---|---|---|---|
| 1 | Node trust | `policy.json` sigstoreSigned + `registries.d` (D015/D018) | none; prose only in `gitops/act-serving/README.md` | (c) | C |
| 2 | Rekor at verify | pull path honours policy | `swap_agent.py:66` `--insecure-ignore-tlog` — signs into RHTAS, never reads it | (c) | C |
| 3 | Build system | Tekton in-cluster, signed | no `tekton/`; `docker buildx` on Ubuntu → quay; Pipelines operator installed unused; `gitops/operators/README.md:8` still claims Tekton | (c) | F |
| 4 | Fleet/device mgmt | RHEM enrollment + OS plane | none (D007) | (a) | A–D |
| 5 | GitOps coverage | ApplicationSets (live-only) | `flywheel`, `minio`, `observability` Argo apps not in git; `prune: false`; 4 secrets by hand | (c) | F |
| 6 | Telemetry | OTel with `model.version` → Tempo | zero OTel; Perses/Tempo/COO installed with no data | (c) | F (post-ROSCon) |
| 7 | Messaging | hub AMQ Streams + MM2 TLS | DIY Strimzi-image Deployment, privileged, plaintext NodePort; brief still says "AMQ Streams" | (c) | F (post-ROSCon) |
| 8 | Model registry | MLflow (hand-backfilled) | none; version/digest/eval/dataset join only in PR body | (c) | E |
| 9 | Serving | Deployment on MicroShift | Deployment `replicas: 0` in SNO; real serving = `docker run` on host; KServe enabled, unused | (b) | D (retired) |
| 10 | Training | DSP/KFP | DSP triggers host runner | (b) | unchanged (Phase 4 `mode=cluster`) |
| 11 | Host OS | CS10 bootc + podman | Ubuntu + docker; Fury RHEL 10.2 with no host-management story | (c) | B, G |
| 12 | Multi-arch | arch-derived cosign download | `cosign-linux-amd64`, `Linux_x86_64` hardcoded (`pipeline/act_flywheel_pipeline.py:86,105`); `:latest` base images despite D022 | (c) | F |
| 13 | Secrets | Secret-sourced | `minioadmin` in git; `COSIGN_PASSWORD=""` | (c) | F |
| 14 | Sign time | cosign + RHTAS Rekor | cosign v2.6.5 + in-cluster Rekor, `--tlog-upload=true` — **correct, keep** | — | — |

Letters in "Fixed in" are the Phase 4.5 items (BUILD-PLAN). The one that stings: **today the
pipeline signs into RHTAS Rekor (index 0, 1 — D022) but the only verifier in the loop passes
`--insecure-ignore-tlog`** (`src/swap-agent/swap_agent.py:66`; the runbook's manual check at
`docs/DEMO_RUNBOOK.md:307` does the same), and **no container `policy.json` or `registries.d`
existed anywhere in this repo** — the transparency log was written and never read.

**Decision — fix before the Fury (Phase 4.5 C and F):**
- **Node trust, for real.** The Fleet writes `/etc/containers/policy.json` (`sigstoreSigned` for
  the modelcar and runtime repos with `keyPath` **and `rekorPublicKeyPath`**, so the Rekor SET is
  enforced on the device, `signedIdentity: matchRepository`) and `registries.d` with
  `use-sigstore-attachments: true` (thor D015/D018), plus `cosign.pub` and `rekor.pub`. Exit: a
  deliberately unsigned tag fails to pull with a signature error; a tag signed *without*
  `--tlog-upload` also fails; `--insecure-ignore-tlog` no longer exists in the repo.
- **Tekton builds + signs the runtime images** (D028).
- **Arch-derived tool downloads** (`platform.machine()` for crane/cosign URLs); **pinned base
  images** (`ubi9/python-312`, `ubi-micro` — D022 said so, the code didn't); `platform` becomes a
  list for multi-arch modelcars.
- **Argo apps committed** (`argocd/{flywheel,minio,observability}-app.yaml` from `oc get app`),
  `prune: true` everywhere, so `argocd app list` equals the files in `argocd/`.
- **MinIO root creds out of git** (`gitops/flywheel/hub-credentials.yaml` → hand-created Secret).
- **Docs corrected**: `gitops/operators/README.md` Tekton/KServe claims, RHEM + Model Registry
  rows; `PROJECT-BRIEF.md:46` "Kafka (AMQ Streams)" → honest wording.

**Decision — deferred past ROSCon, recorded here so they are deferrals rather than drift:**
- **OTel re-emission** (row 6): a second quadlet app `otel-collector` in the Fleet (thor
  `derived-image/config/otel-collector.yaml` shape, `model.version` resource attribute from
  `MODEL_VERSION`, otlphttp → Tempo gateway); coordinator emits an episode span; Perses panels
  stop being empty. ~1 day.
- **AMQ Streams** (row 7): the operator + a `Kafka` CR (KRaft NodePool, external NodePort
  listener) replacing `gitops/flywheel/edge-kafka.yaml`, and the consumers re-pointed. ~1 day.
- **Why deferred:** neither changes what the booth shows, and both touch the running loop's
  transport and telemetry in the two weeks before the Fury window. Booth stability wins; the brief
  wording is corrected now so nothing claims them.

**Consequences:** Phase 4 item 4's "node-side signature enforcement applied, not just documented"
line is delivered by 4.5 C; rows 6–7 become open-question rows tagged post-ROSCon; the drift
table is the checklist for the pre-Fury `F` day, and anything still (c) after the Fury gets its
own decision or its own deferral.

---

## D027 — The pipeline's CatalogItem write is a deliberately removable seam; the Model Registry record is the durable handoff

**Date:** 2026-09-08
**Context:** BUILD-PLAN Phase 4 item 5 asked for a structured promotion record because RHEM
engineering is building an RHOAI Model Registry → RHEM Catalog bridge, so Fleet rollout policies
can carry promoted models. That bridge does not exist yet; the RHEM Catalog API is **v1alpha1
upstream**. Phase 4.5 E puts both the registry record (E1) and a Catalog version graph (E2,
stretch) in the promotion, and the question is which one the pipeline should own.

**Decision:**
- **Model Registry is the durable handoff.** New KFP component `register_model` between
  `sign_modelcar` and the PR: `register_model("soarm-act", uri=<digest ref>,
  model_format_name="lerobot-act", version=<candidate>, metadata={dataset_uri, incumbent, rates,
  fixed/broken/net, p, rekor_index, pr_url})` — the five-way join item 5 asked for, pipeline-emitted
  (the thor-testing gap was hand-backfilled MLflow entries). RHOAI `modelregistry` component
  `Managed` in `gitops/operators-config/dsc.yaml`; `ModelRegistry` CR + MariaDB in
  `gitops/operators-config/model-registry.yaml`.
- **The CatalogItem write is one isolated function, `append_catalog_version(item_yaml, version,
  digest, replaces)`**, editing `gitops/rhem/catalogitem-soarm-act.yaml` in the same promotion
  commit. It is **designed to be deleted** the day RHEM engineering's registry → Catalog bridge
  lands: nothing else in the pipeline depends on it, and removing it changes no other file.
- The Fleet pins `volumes[].image.catalogItemRef {catalog, item, version}`; `channel: stable` is
  the follow-on, not the demo.

**Alternatives:** pipeline writes only the Fleet digest and skips the Catalog (keeps the product
seam invisible on stage); pipeline writes only the Catalog and the Fleet follows a channel (moves
the promotion decision out of git — Gate 3 is the human merge, keep it there).

**Consequences:**
- Flag to verify: docs show `references: {container: "<tag>"}`; **digest-form references are
  unverified.** If tag-only, the digest-pinned Fleet stays the product path and the Catalog is
  shown as the version graph only. Say "v1alpha1" on stage.
- Flag: confirm the RHOAI version on SNO ships the `modelregistry` component
  (`oc get csv -n redhat-ods-operator`) before E1 is scheduled.
- Closes BUILD-PLAN Phase 4 item 5 when E1 lands; the PR body stays the human-readable view.

---

## D028 — OpenShift Pipelines (Tekton) reinstated for runtime image build + sign; ModelCar packaging stays in KFP

**Date:** 2026-09-08
**Context:** D022 fork 1 decided RHOAI DSP/KFP as the pipeline runtime "with Tekton tasks reused
inside for build/sign as thor did", and `gitops/operators/README.md:8` says the Pipelines operator
is there for exactly that. What actually happened: the modelcar is packaged and signed inside KFP
(correct — `crane append` + cosign, Rekor index 0/1), but the **runtime images**
(`Dockerfile.gpu-inference` and the rest) are built by `docker buildx` on the Ubuntu host and
pushed to quay **unsigned**, amd64 only. The Pipelines operator was installed and never used; the
README claimed otherwise (drift row 3, D026).

**Decision:** Tekton builds and signs the runtime images, multi-arch, in-cluster.
- `gitops/tekton/{buildah-cross-arch-task,cosign-sign-task,runtime-image-pipeline}.yaml` lifted
  from thor `tekton/00-*.yaml` and `01-*.yaml`, namespace `flywheel`; `gitops/tekton/qemu-binfmt.yaml`
  DaemonSet for the arm64 cross-build.
- The `pipeline` SA is bound to the **privileged SCC** — thor D009: qemu segfaults under
  `pipelines-scc`.
- Build `--platform linux/amd64,linux/arm64 --manifest`; sign the **manifest digest** with cosign
  v2.x `--rekor-url <RHTAS> --tlog-upload=true`. The Fleet references the runtime image **by
  digest**, and the device's `policy.json` verifies it (D026).
- ModelCar packaging stays a KFP component: it is one `crane append` on a ~200 MB directory and
  belongs to the promotion run, not to an image build.

**Alternatives:** keep `docker buildx` on the host and sign there (rejected — the host is the
[desktop shim], the Fury has no Ubuntu host, and it leaves the operator installed for show);
build runtime images inside KFP too (rejected — a colcon build is a Tekton-shaped job, and the
thor tasks exist).

**Consequences:**
- Risk: colcon under qemu takes hours. Mitigation: a native `podman build` on the Fury during the
  visit pushed to the same tag; Tekton remains the recorded path.
- Flag: `torch==2.9.1+cu130` aarch64 wheels unverified (BUILD-PLAN open question).
- Exit: `tkn pipelinerun` builds both arches, a Rekor entry is created, `crane manifest` shows
  both platforms, the Fleet pins the digest. `gitops/operators/README.md` then tells the truth
  (Phase 4.5 F).

---

## D029 — Pre-upgrade VM snapshot: libvirt internal qcow2 snapshot of the shut-off domain

**Date:** 2026-09-08
**Context:** D024 says "VM snapshot first" but not how. The SNO VM (`sno-flywheel`, qemu:///system)
boots UEFI (`<loader type='pflash'>` + nvram). libvirt refuses internal snapshots of a *running*
pflash domain, and `sudo` is unavailable on the desktop (so no direct `qemu-img snapshot` on the
root-owned image). The host had ~180 GB free on `/`; the image is 235 GB allocated.
**Decision:** graceful `virsh shutdown` (took ~2 min), then `virsh snapshot-create-as sno-flywheel
pre-4.18-upgrade-2026-09-08` with the domain **shut off** — libvirt 10.0 accepts an internal
disk-only snapshot of an inactive pflash domain (no memory state, NVRAM not captured). Verified with
`virsh snapshot-list`. Then `virsh start` and waited for the node Ready + all clusteroperators
Available/not Progressing/not Degraded before touching the upgrade.
**Revert:** `virsh -c qemu:///system shutdown sno-flywheel` → wait for `shut off` →
`virsh -c qemu:///system snapshot-revert sno-flywheel pre-4.18-upgrade-2026-09-08` → `virsh start`.
Host `/etc/hosts`, the desktop containers and the Argo apps are unaffected (the snapshot is the
whole guest disk, 4.17.56 + everything installed on it at 14:58 CDT).
**Alternatives:** external overlay snapshot (`--disk-only --diskspec vda,snapshot=external`) —
works on running pflash domains but revert is a manual XML edit + blockcommit on libvirt 10;
`qemu-img snapshot -c` on the file — needs root; no snapshot — rejected by D024.
**Consequences:** the qcow2 grows by every block rewritten after the snapshot (the two upgrades
write tens of GB); free space on `/` must be watched. Delete the snapshot once 4.19 has been
stable for a few days (`virsh snapshot-delete sno-flywheel pre-4.18-upgrade-2026-09-08`, domain
running is fine for delete) to stop paying for it. A future snapshot needs the same shutdown.

---

## D030 — Upgrade path: `stable-4.18` → `--to-latest`, then `stable-4.19` → `--to-latest`; no admin-ack was required

**Date:** 2026-09-08
**Context:** D024 fixes the hops (4.17 → 4.18 → 4.19); channel and version within a hop were open.
`oc adm upgrade` on 4.17.56 listed `stable-4.18`/`eus-4.18` (4.19 channels only appear once on
4.18). `admin-gates` in `openshift-config-managed` was empty, so no acknowledgement gate exists on
the 4.17→4.18 edge; the 4.18→4.19 gate check is repeated after the first hop.
**Decision:** `stable-*` channels (not `fast-*`, not `eus-*`: EUS only matters for a skip-hop
we are not doing on SNO), `--to-latest=true` inside each channel so the target is the newest
*recommended* edge — if the newest is only "conditional", switch to `--to <version>` of the
newest recommended one rather than `--allow-not-recommended`. One gate existed on the 4.18→4.19
edge (`ack-4.18-kube-1.32-api-removals-in-4.19`); the cluster made zero requests to any API
removed in 1.32, so it was acknowledged.
**Operator compatibility evidence (checked before starting):** no installed CSV carries an
`olm.maxOpenShiftVersion` property (all six checked via `operatorframework.io/properties`), so OLM
has no block. Vendor matrices: RHOAI 2.25 supports OCP 4.16–4.20 (x86_64,
access.redhat.com/articles/rhoai-supported-configs); GitOps 1.21.4 supports 4.18–4.22; Pipelines
1.22 supports 4.14, 4.16–4.22; COO 1.5 / Tempo 0.22 are 4.x-generic; RHTAS 1.4 is documented for
4.14+ (release-notes landing page did not carry the matrix; not a blocker on any 4.x we touch).
Subscriptions stay on their current channels (`latest`/`stable`) — nothing needs a channel bump.
**Consequences:** two SNO reboots (~15–20 min each of API absence). If `stable-4.19` offers a
newer z than the one tested here, it is fine to take it — the RHEM chart constraint is
`kubeVersion >= 1.32`, i.e. any 4.19.

---

## D031 — Argo renders the flightctl chart with `helm template`: pin every `lookup`-derived value, hold generated secrets with `ignoreDifferences`

**Date:** 2026-09-08
**Context:** D024 mandates the chart via an Argo Helm-OCI source. The chart is written for
`helm install`: it `lookup`s the cluster `DNS` object for the base domain, the `oauth-openshift`
Route for the OAuth URLs (and calls `fail` if not found), the `default-ingress-cert` ConfigMap for
the auth CA, and the previously generated `flightctl-db-*`/`flightctl-kv-secret` Secrets and the
`OAuthClient` so passwords survive upgrades. Argo CD's `helm template` returns nothing from
`lookup`, so unpinned the render either fails outright (OAuth) or re-randomises passwords on every
3-minute refresh and, with `selfHeal`, rotates them under the running DB.
**Decision** (all in `argocd/rhem-app.yaml`, `helm.releaseName: flightctl`):
- `global.baseDomain: flightctl.apps.sno-flywheel.local` — the chart's own OpenShift layout
  (`<ns>.apps.<cluster domain>`), just made explicit. Routes are `ui.`, `api.`, `agent-api.` etc.
  under it; one `/etc/hosts` line covers all eight.
- `global.auth.type: openshift` with `authorizationUrl`/`tokenUrl` set to the SNO's
  `oauth-openshift.apps.sno-flywheel.local` endpoints; `insecureSkipTlsVerify: true` (API and UI)
  because the ingress CA is not `lookup`-able and the cluster is self-signed (D006 pattern).
  `createAdminUser: true` keeps the chart's `flightctl-admin` SA.
- `routeExternalCertificate: "false"` — `Route.spec.tls.externalCertificate` is behind a feature
  gate that is not GA on 4.19; `helm template` sees `Release.IsInstall=true` and would emit it.
- `generateCertificates: builtin`, `exposeServicesMethod: route`, `enableOpenShiftExtensions:
  "true"`, `enableMulticlusterExtensions: "false"` — the `auto` values happen to resolve the same
  way but depend on `.Capabilities`; pinning removes the dependence on how Argo passes
  `--api-versions`.
- `storageClassName: local-path` (DB PVC 60Gi, alertmanager PVC).
- `imageBuilderApi/Worker.enabled: false` — bootc image building is not in scope (package-mode
  devices, D024) and the worker needs a privileged SCC + RHSM secrets.
- `ignoreDifferences` on `/data` of the four generated Secrets, on `/secret` of
  `OAuthClient/flightctl-flightctl`, and on `/data` of the three ConfigMaps that embed the OAuth
  client secret (`flightctl-api-config`, `flightctl-remote-access-config`,
  `flightctl-alertmanager-proxy-config`), with `RespectIgnoreDifferences=true` so a sync writes
  the live values back rather than the freshly randomised ones.
- `upgradeHooks.databaseMigrationDryRun: false` and `upgradeHooks.scaleDown.condition: never` —
  the chart's Helm `pre-upgrade` hooks become Argo PreSync hooks that also run on install: the
  migration dry-run deadlocks on a fresh install (its ServiceAccount is created by the main sync)
  and the scale-to-zero job re-renders on every sync because it relies on `lookup`.
- `managedNamespaceMetadata.labels: io.flightctl/instance=flightctl` — the namespace→organisation
  label thor set by hand (`DEPLOYMENT_GUIDE.md:112`).
- `prune: true` (D026 direction) — new app, nothing hand-made to lose.
**Alternatives:** put `global.auth.openshift.clientSecret` in values (rejected: a credential in a
public repo); `global.auth.type: k8s` (no OAuth client at all, token-paste UI login — kept as the
fallback if the OAuth flow misbehaves on the demo box); `none` (rejected — it removes the RBAC
story). Argo config-management plugin to run real `helm install` (rejected — heavy).
**Consequences:** a chart bump that changes the service config needs the three ConfigMaps and
the OAuthClient deleted together so Argo recreates them from one render (documented in the app
file). Chart hooks (`pre-install`/`pre-upgrade` cert + encryption-key Jobs) run as Argo PreSync
hooks; `post-delete` cleanup hooks are ignored by Argo — deleting the app leaves the PVC behind
(intended). The `/etc/hosts` line in `argocd/README.md` is required on the Mac and the device VM.

---

## D032 — ResourceSync field is `targetRevision`, and `gitops/rhem/` may carry a README

**Date:** 2026-09-08
**Context:** BUILD-PLAN/D024 describe the ResourceSync loosely (`revision`). The flightctl 1.3
core OpenAPI names the field `spec.targetRevision` (required, with `repository` and `path`;
`type` defaults to `fleet`). The sync reads a directory flat, `*.yaml|*.yml|*.json` only, and
skips other files (`internal/tasks/resourcesync.go`, `validFileExtensions`).
**Decision:** `rhem/bootstrap/resourcesync.yaml` uses `targetRevision: desktop-gpu-split`,
`path: gitops/rhem`, `type: fleet`; `gitops/rhem/README.md` stays in the directory. A second
ResourceSync (`type: catalog`) is added in E2 if CatalogItems land in the same directory —
a single sync only handles one type.
**Consequences:** nothing until `gitops/rhem/` is pushed; until then the ResourceSync reports the
path missing (expected).

---

## D033 — Device labels carry the zenoh router as two labels: `zenoh_router=<host>` + `zenoh_port=<port>`

**Date:** 2026-09-08
**Context:** D024 lists `zenoh_router=10.0.0.48:7447` as a device label. flightctl 1.3.0 validates
label values server-side with Kubernetes `IsValidLabelValue`
(`internal/util/validation/validation.go:91`; `api/core/v1beta1/validation.go` calls it for every
resource's `metadata.labels` and for the approval's `labels`): ≤ 63 chars, `[A-Za-z0-9]` at both
ends, `[-A-Za-z0-9_.]` inside. `:` is rejected, and so is `/`. The OpenAPI only says
`additionalProperties: string`, so the CLI (`flightctl approve -l k=v`, `internal/cli/approve.go:62`)
would accept it and the server would 400.
**Decision:** two labels, `zenoh_router=10.0.0.48` and `zenoh_port=7447`; the Fleet's inline
`/etc/act-inference/env` template joins them (`ZENOH_ROUTER={{ .zenoh_router }}:{{ .zenoh_port }}`).
`arch` values are `amd64`/`arm64` (Go/OCI platform names, derived from `uname -m` in
`device/enroll.sh`). Approval uses repeated `-l` flags (`docs/user/using/managing-devices.md:36`).
**Alternatives:** encode as `10.0.0.48-7447` (rejected — a Go template would have to split it,
and it reads as a hostname); put the router in the Fleet config instead of a label (rejected — the
per-site difference is exactly what labels are for in D024).
**Consequences:** BUILD-PLAN Phase 4.5 B/G label lists and D024's label example should read
`zenoh_router=… zenoh_port=…`; `device/enroll.sh` validates every value against the rule before
calling the API.

---

## D034 — CPU spike part 1 passes; the desktop stand-in runs ACT on 8 P-core threads

**Date:** 2026-09-08
**Context:** `act-v2-ft160` has `chunk_size = n_action_steps = 100` at 50 Hz → one forward pass
per 2 s; D024's threshold is p95 < 1000 ms. Measured in a throwaway container from the production
image with the VM's budget (`--cpus=8 --memory=16g`, `--cpuset-cpus=0,2,4,6,8,10,12,14`,
`--network none`), mirroring lerobot's `policy_server` pipeline: forward p50/p95/p99 =
75.0/83.2/101.0 ms at 8 threads; 78.2/98.2/150.0 ms at 16 threads (SMT siblings). Record:
`docs/eval-records/cpu-spike.md`.
**Decision:** criterion 1 is met with a 12× margin; proceed with the CPU stand-in (no VFIO, no
RTF change, no `n_action_steps` change). The VM runs the policy container with
`OMP_NUM_THREADS=8`; the VM stays unpinned by default (the SNO VM floats across all 32 threads),
with `VCPU_CPUSET=0,2,4,6,8,10,12,14` available in `create-vm.sh` if part 2 shows chunk-boundary
gaps. Criteria 2–3 stay open until a scheduled stop of the host `act-inference` (procedure in the
record; GPU baseline on the same 20 seeds is 17/20 = 85%, pass is ≥ 15/20).
**Alternatives:** measure inside the guest first (deferred — the guest has no image yet, F builds
it; the script is in `device/spike/` to re-run there); 16 threads (rejected — slower and a fatter
tail).
**Consequences:** Phase 4.5 C is un-gated on compute grounds; the two live criteria move to the
C cut-over window, where the host container stops anyway.

---

## D035 — The stand-in VM's disk is a libvirt-side copy of the guest image, not a backing chain

**Date:** 2026-09-08
**Context:** the `images` pool (`/var/lib/libvirt/images`) is root-owned and the desktop user has
no sudo; a qcow2 backing file under `/home/jary` would also need to stay readable by the qemu
user forever.
**Decision:** `device/vm/create-vm.sh` does `virsh vol-create-as` (qcow2, 60 G) →
`virsh vol-upload` of the RHEL 10.2 KVM guest image → `virsh vol-resize`, then
`virt-install --import --cloud-init user-data=…,meta-data=…` with `--cpu host-passthrough`
(the guest sees the full ISA for oneDNN), `--osinfo rhel10.1` (newest rhel10 entry in the
desktop's osinfo db), bridged on `br0`, `--autostart`. cloud-init only creates the operator user
with the desktop's key and grows the root fs; provisioning is a separate, explicit step.
**Alternatives:** backing chain (rejected above); bootc image mode (rejected in D024).
**Consequences:** rebuilding the VM is `virsh destroy; virsh undefine --remove-all-storage` then
re-run; the base qcow2 at `/home/jary/images/` is untouched.

---

## D036 — Registration input is a root-only env file, sourced by `provision.sh` and shredded after use

**Date:** 2026-09-08
**Context:** the activation key must reach `subscription-manager register` on every device
without ever appearing in a transcript, log, or repo file.
**Decision:** `provision.sh --env-file <path>` (default `/root/activation-key` if present) sources
`ORG_ID=`/`ACTIVATION_KEY=`; `RHSM_USER`/`RHSM_PASS` remain the documented alternative. The file
is copied to the device as `root:root 0600`, and shredded on the device once
`subscription-manager identity` succeeds (the desktop copy at `/home/jary/activation-key` stays).
Registration output is piped through a redaction of UUIDs and org lines.
**Consequences:** re-running `provision.sh` on a registered device skips registration and needs no
key; the Fury run uses the same file.

---

## D037 — Benchmark methodology for CPU policy latency (reusable on the Fury)

**Date:** 2026-09-08
**Decision:** `device/spike/bench_cpu_forward.py` is the standard: same image as production,
`--network none`, CUDA hidden, cgroup-limited to the device budget, one SMT thread per physical
core, synthetic observation with the checkpoint's real keys/shapes, the `policy_server` pipeline
(`preprocessor` → `predict_action_chunk` → `postprocessor`), 10 warm-up + 200 timed passes,
nearest-rank percentiles, threshold computed from the checkpoint's `n_action_steps` and the
contract's fps. Reports pre/forward/post/end-to-end and PASS/FAIL. Runs unchanged on aarch64.

---

## D038 — The ModelCar is a quadlet `.volume` with `Driver=image`, not an app-level `volumes[].image`

**Date:** 2026-09-08
**Context:** BUILD-PLAN flagged that flightctl's app-level image volumes are described as OCI
*artifacts*. The v1.3.0 agent source confirms it and adds a harder problem: flightctl 1.3.0
app-level `volumes[].image` on quadlet apps uses podman-artifact semantics, not a mount. For a
quadlet app, `extractVolumeTargets` (`internal/agent/device/applications/provider/provider.go:1264`)
sets the pull type of every `volumes[].image` entry to `OCITypePodmanArtifact` unconditionally, so
the prefetch is `podman artifact pull <ref>` (`internal/agent/device/dependency/dependency.go:481-482`,
`internal/agent/client/podman.go:187-233`). At install, `ensureArtifactVolumes`
(`internal/agent/device/applications/lifecycle/quadlet.go:379-438`) creates a *local-driver* volume
and runs `podman artifact extract <ref> <mountpoint>` (`client/podman.go:240-256`), i.e. each layer
is copied out as a file named by its `org.opencontainers.image.title` — not mounted as a rootfs.
The `mount` variant (`ImageMountVolumeProviderSpec`) is rejected for quadlet apps
(`provider/app_handler.go:10-20`, `ErrUnsupportedVolumeType`). Our modelcar
(`quay.io/jary/soarm-act-modelcar@sha256:bdb513ca…`) is a 2-layer Docker-v2 container image
(ubi-micro + a `crane append` layer, no layer annotations at all), so title-based extraction has
nothing to work with — this artifact-pull/extract path fails for a signed multi-layer container
image regardless. Worse: on the VM (podman 5.8.2), `podman artifact pull` of a *signed* container
image fails before extraction — `Error: copying system image from manifest list: Can not copy
signatures to oci:/var/lib/containers/storage/artifacts:…: Pushing signatures for OCI images is not
supported` (probed with `registry.access.redhat.com/ubi9/ubi-init:9.5`; the artifact store is an OCI
layout and containers/image refuses to drop the signatures it just verified). Since D026 makes both
of our repos sigstoreSigned, the artifact path is a dead end for a signed modelcar.

The quadlet-native path works as documented: a `models.volume` with `[Volume] Driver=image
Image=quay.io/jary/soarm-act-modelcar@sha256:…` plus `Volume=models.volume:/modelcar:ro` in the
`.container`. The agent still tracks it as an image-backed application volume
(`provider/volume.go:316-332`, `ReclaimPolicy: Retain`) and pre-pulls the reference with plain
`podman pull` (the `.volume` `Image=` is collected by `extractQuadletTargets`), so `policy.json` is
enforced and the signature is stored with the image. Verified on the VM: the volume unit creates
`systemd-models-test` (`driver=image`), the container sees the modelcar rootfs read-only at the
mount point, and the checkpoint is at `<mount>/models/act/{config.json,model.safetensors,…}`.
`--mount type=volume,src=<vol>,dst=/models,subpath=models` also works on podman 5.8.2 if a
`/models/act` path is ever wanted, but the documented `Volume=` form is what the Fleet uses.
**Decision:** `fleet-act-inference.yaml` carries the modelcar as an inline `models.volume`
(`Driver=image`, digest-pinned) mounted at `/modelcar`, with `POLICY_PATH=/modelcar/models/act`.
Therefore the Fleet uses a quadlet `.volume` with `Driver=image` pinned by digest, and
`catalogItemRef` (E2) is unavailable on that path — so E2's CatalogItem, if it lands, is a version
graph shown beside the digest-pinned Fleet: documentation/provenance, not a live reference (the
D027 fallback).
**Alternatives:** repackage the modelcar as a real OCI artifact with titled layers (rejected — it
would have to be unsigned to pass `podman artifact pull`, which defeats D026, and KFP would need a
second packaging path); app-level `volumes[].image` (rejected above).
**Consequences:** `reclaimPolicy: Retain` semantics come from podman: the image-driver volume is an
overlay of the image and is removed by the agent on app removal (`lifecycle/quadlet.go:253-295`,
"ephemeral" image-driver volumes), but the *image* stays in local storage, so a `git revert` rollback
re-creates the volume from the already-present digest with no re-pull. Image pruning
(`docs/user/using/managing-devices.md` "Image and Artifact Pruning") can remove the previous digest
after a *successful* update — rollback within the same rollout is safe, rollback after a later
promotion re-pulls. BUILD-PLAN "Flags to verify" bullet 2 is resolved. Cross-reference D027: this
entry confirms the CatalogItem write remains the removable seam D027 designed for — the Fleet's
digest pin is the durable path, the CatalogItem is not load-bearing.

---

## D039 — `quay.io/jary/soarm-act-modelcar` must be pullable by the device: make it public (or pre-place a pull secret)

**Date:** 2026-09-08
**Context:** the repo is private (`crane ls` unauthenticated → `UNAUTHORIZED`; `podman pull` on the
VM → `unauthorized: access to the requested resource is not authorized`). `soarm-flywheel` is
already public. flightctl does not deliver registry credentials: "Authentication must exist on the
device before it can be consumed" — `/root/.config/containers/auth.json` for a rootful quadlet app
(docs "Using Image Pull Secrets"). Putting an `auth.json` in the Fleet's inline config would put a
quay token in git.
**Decision:** make the modelcar repo public. It holds a sim-trained ACT checkpoint for an open
upstream task; the security property we care about is integrity (signature + Rekor), not
confidentiality, and a public repo keeps the Fury story "device pulls from quay.io, no mirror"
(Phase 4.5 G) free of a secrets step.
**Alternatives:** `device/provision.sh` gains an optional `--auth-file` (root-only, D036 pattern) that
installs `/root/.config/containers/auth.json`; the Fury run then needs the token on site.
**Consequences:** until one of these is done, the C exit criterion "an unsigned tag fails to pull with
a signature error" is only half-provable: the negative half passes today on the VM
(`quay.io/jary/soarm-flywheel:sim-only` → `Source image rejected: A signature was required, but no
signature exists`); the positive half (signed + Rekor-logged digest pulls) is blocked on access.

---

## D040 — `/etc/act-inference/env` carries only what varies per device; coordinator settings are Fleet envVars; no `CURATOR_URL`

**Date:** 2026-09-08
**Context:** the episode emitter does not run in `act-inference` — it runs in the sim container
(`docker/entrypoint.sh:72`, `so-arm-sim` on the host) and POSTs to the curator NodePort
`http://10.0.0.49:30802/episode` (`src/episode-emitter/episode_emitter.py:31`,
`gitops/flywheel/curator.yaml:228`). It takes the label from the latched `/flywheel/model_version`
topic, which the VM's coordinator publishes over zenoh. The host `act-inference` had no
`CURATOR_URL` either.
**Decision:** the label-templated env file holds `ZENOH_ROUTER=<zenoh_router>:<zenoh_port>`,
`POLICY_DEVICE`, and — only when `gpu=none` — `OMP_NUM_THREADS=8`/`TORCH_NUM_THREADS=8` (D034).
`getOrDefault .metadata.labels "gpu" "none"` guards a device approved without the label (renders
the CPU branch instead of failing the Fleet render). The lineage pair `MODEL_VERSION` + `POLICY_PATH`
and the coordinator's sim-randomisation settings copied from the host `docker run`
(`RECORD=true RESET_ARM=false EPISODE_LEN=60 RANDOMIZE_CUBES=true RANDOMIZE_ONLY=cube_medium
RANDOM_RADIUS=0.03 REST_POSE=0,0,0,0,0,0`) are application `envVars`, so the promotion regex
(D025) touches `MODEL_VERSION` in exactly one place. No `CURATOR_URL` is set anywhere in the Fleet.
**Consequences:** if the emitter ever moves onto the device (post-ROSCon OTel/telemetry work),
`CURATOR_URL` joins the env file as a per-site value (`curator_host` label) — not before.

---

## D041 — No `ShmSize=`; rootful `Network=host`; `HealthOnFailure=kill` with `Restart=always`

**Date:** 2026-09-08
**Context:** the host container runs with docker's default 64 MiB `/dev/shm`
(`HostConfig.ShmSize=67108864`) and rmw_zenoh in client mode over TCP to the router; zenoh
shared-memory transport is off by default and nothing in the loop uses it. podman's default is also
64 MiB.
**Decision:** omit `ShmSize=`; keep `Network=host` (zenoh client → `10.0.0.48:7447`, no port mapping
to maintain). The container health is the image `HEALTHCHECK` (`docker/healthcheck.sh`: latched
`/flywheel/model_version == $MODEL_VERSION` and `/run_policy` on `ros2 action list`), repeated in
the quadlet as `HealthCmd`/`HealthStartPeriod=240s`/`HealthInterval=30s`/`HealthTimeout=10s`/
`HealthRetries=3`; `HealthOnFailure=kill` + `Restart=always` makes an unhealthy container restart
without an operator, which is what a demo wants and what RHEM's application status will show as a
restart rather than a hang. The bind mount `/var/lib/act-inference/data:/data:z` (SELinux is
Enforcing on the VM) gives the recorder `/data/bags` — i.e. `/var/lib/act-inference/data/bags`, not
the `/var/lib/act-inference/bags` directory `provision.sh` also creates.
**Alternatives:** `HealthOnFailure=none` (podman default) — RHEM would report Unhealthy but nothing
would recover; rejected for the demo window. `ShmSize=1g` "just in case" — rejected, no evidence.
**Consequences:** `healthcheck.sh` runs two `ros2` CLI calls under a 10 s budget (4 s each); if zenoh
discovery makes it flaky on the VM, raise `HealthTimeout`/`HEALTH_STEP_TIMEOUT` in the Fleet, not
the check. `provision.sh` may drop the unused `/var/lib/act-inference/bags` directory.

---

## D042 — Fleet trust files replace the RHEL defaults, so they must carry them

**Date:** 2026-09-08
**Context:** RHEL 10.2 ships `/etc/containers/policy.json` with `sigstoreSigned` entries for
`registry.access.redhat.com` and `registry.redhat.io` (Red Hat release key + Rekor key under
`/etc/pki/sigstore/`). An inline config file overwrites the whole file.
**Decision:** the Fleet's `policy.json` keeps those two entries verbatim and adds
`quay.io/jary/soarm-act-modelcar` and `quay.io/jary/soarm-flywheel` (`keyPath` + `rekorPublicKeyPath`,
`signedIdentity: matchRepository`); `default` stays `insecureAcceptAnything` (ubi-micro, alpine and
the flightctl test images keep pulling); `docker-daemon` stays open (thor). `registries.d/quay-jary.yaml`
scopes `use-sigstore-attachments: true` to the `quay.io/jary` namespace so other quay pulls are not
forced to look up attachments. The same four files are on the VM now, written by hand with
identical content — the Fleet's first apply is a no-op there. The pre-existing distro file is kept at
`/root/policy.json.rhel-default` on the VM.
**Consequences:** a Fury host with a different RHEL point release could ship different default
entries; the Fleet's copy wins — check `rpm -qf /etc/containers/policy.json` diffs on the Fury before
enrolling.

---

## D043 — Desktop device VM records bags into the host's bag directory over virtiofs; bag port/prune stays a host flow

**Date:** 2026-09-08
**Context:** the C plan moves bag-watchdog semantics to `/var/lib/act-inference` on the device. The
stand-in VM has a 60 GB disk; an episode bag is ~1.3 GB (408 bags = 644 GB on the host today), so
the VM would fill in ~40 episodes. `assemble_all.sh` (ports curated successes into a LeRobot
dataset, last run 2026-09-08 12:14, 450 episodes) and `prune_bags.py` (classifies bags
proof/ported/unported/orphan; `--yes` deletes only the ported class) read bags from the host path
`~/flywheel-data/bags`, and the curated JSON's `dataset_path` points at that host path.
**Decision:** on the desktop the VM mounts the host's `~/flywheel-data/bags` via a libvirt virtiofs
filesystem share at `/var/lib/act-inference/bags` (memoryBacking shared; no sudo needed for the
domain XML edit as a libvirt-group user); the recorder inside the quadlet writes there, so
`dataset_path` values stay valid for the host-side assembler and prune. Port-as-you-go:
`after_assemble.sh` runs `prune_bags.py --yes` after a successful assembly instead of writing a
dry-run file. On the Fury the host is the device with local disk; the bag directory is local and
the same scripts run there. Operator authorization 2026-09-08: prune ported bags; keep
proof/orphan.
**Alternatives:** NFS from host (needs sudo to install the server); grow the VM disk (same physical
disk, nothing gained, breaks the host-path contract); stream bags to the hub (post-ROSCon idea).
**Consequences:** `device/vm/create-vm.sh` gains the virtiofs share; the Fleet's `.container` mounts
`/var/lib/act-inference/bags:/data/bags`; `provision.sh`'s `/var/lib/act-inference/bags` becomes the
mountpoint; bag-watchdog cap logic moves to the host-side prune, not the device.

---

## D044 — Interim: bag recording disabled on the desktop device VM (`RECORD=false` in the Fleet) until the virtiofs share is mounted

**Date:** 2026-09-08
**Context:** `virtiofsd` is not installed on the Ubuntu 24.04 desktop — it is a separate package
(`1.10.0-1ubuntu0.1`, candidate but not installed) — and AppArmor blocks both a non-package binary
path and the unprivileged socket mode; `<seclabel type='none'>` was rejected because it drops VM
confinement (D043-a). Installing the package needs the operator's sudo. The VM has a 60 GB disk
with the 11.4 GB runtime image already on it; a bag is ~1.3 GB.
**Decision:** the Fleet ships `RECORD: "false"` for now; the episode emitter still posts episodes
with `dataset_path: null` (contract in `docs/data-contract-eval-dashboard.md`), so the C exit
criteria still hold — the curator receives `act-v2-ft160` episodes from the VM and
`episodes-curated/<mv>/` fills — and D's promotion uses a pre-trained candidate (D023 path), so no
new bags are needed before then. Flip `RECORD` to `"true"` in the same Fleet once
`sudo apt install virtiofsd` + `device/vm/bags-share.sh` + the guest mount are done and the write
test shows host ownership.
**Alternatives:** record to the VM's local disk (rejected — fills after ~25 episodes, and the bags
would be hidden under the later virtiofs mount); grow the VM disk (rejected — same physical disk,
nothing gained).
**Consequences:** spike part 3 (20-seed eval) runs unrecorded; a follow-up todo tracks re-enabling
`RECORD` once the share is mounted.

---

## D045 — Interim amd64 runtime image built on the host, signed into Rekor, digest-pinned in the Fleet until Tekton (F) replaces it

**Date:** 2026-09-08
**Context:** D028 makes OpenShift Pipelines the recorded build + sign path for the runtime images,
but Phase 4.5 C (Fleet-delivered application) needs a signed, digest-pinned `act-inference` image
now — the Fleet's `Image=quay.io/jary/soarm-flywheel@sha256:TODO-F` is the last blocker to a
`Healthy` application on the stand-in device, and F is scheduled days later (BUILD-PLAN 4.5, days
5–7). The only existing runtime image is local docker `act-inference:latest` on the desktop,
unsigned, never pushed. D026 row 3 already names host `docker buildx` as drift; doing it once more
*without a record* would deepen that.
**Decision:** build the image **once** on the desktop host from the same context that produced
`act-inference:latest` (host checkout `~/redhat/git/hp-roscon-flywheel`, `docker build --platform
linux/amd64`, default build args, today's `POLICY_DEVICE` + `HEALTHCHECK` changes rsync'd in), push
it under a **dated tag** (`act-inference-amd64-2026-09-08`, never `:latest`), sign the manifest
digest with the same cosign key the pipeline uses into the RHTAS Rekor with `--tlog-upload=true`,
verify with the transparency log (no `--insecure-ignore-tlog`), and pin the Fleet to the digest:
`quay.io/jary/soarm-flywheel@sha256:29955e4e9422b194f950ba66d43689a2815a3670e5e0b398921d78501d5bb140`
(Rekor index 2). Evidence: `docs/eval-records/interim-runtime-image.md`.

Rules that make it an interim rather than a new path:
- Dated tag + digest pin; the tag is never moved and no `:latest` is pushed from the host.
- Signed with the *same* key and *same* Rekor as the pipeline, so the device `policy.json` written by
  the Fleet (`keyPath` + `rekorPublicKeyPath`) verifies it exactly as it will verify the Tekton image
  — no second trust root, no policy exception.
- Nothing about it lands in code: no build script, no Makefile target, no docs claiming host builds
  are supported. The build command lives only in the eval record.
- Superseded the moment F's multi-arch manifest is signed: the Fleet re-pins to that digest; this tag
  and Rekor entry stay as history.

**Alternatives:** wait for Tekton (rejected — C, D and the cut-over stall behind it, and the
contingency-kit recording (Phase 4 item 2) is downstream of C); `docker save | podman load` onto the
VM as the cpu-spike did (rejected — bypasses the registry, the signature and `policy.json`, i.e. the
exact thing C exists to prove); sign with `--tlog-upload=false` because the SNO was mid-upgrade
(rejected — the device policy requires the Rekor SET; an unlogged signature fails to pull by design).
**Consequences:**
- amd64 only: the Fury still needs F (or the native `podman build` fallback in D028); this image
  cannot be a Fury fallback.
- Host-side cosign needs `--add-host` for the Rekor route (no sudo, not in `/etc/hosts`), the ingress
  CA via `SSL_CERT_FILE`, and `SIGSTORE_REKOR_PUBLIC_KEY` for `verify` with a custom `--rekor-url` —
  without it, `verify` consults the public Sigstore TUF root instead of the in-cluster Rekor and
  fails with "rekor log public key not found for payload". F's Tekton verify step needs the same env
  var — record it in `cosign-sign-task.yaml`.
- The upstream `demos` checkout inside the image is the morning's cached layer, not a fresh clone;
  F rebuilds from scratch.
- D026 row 3 stays open until F; this decision does not close it.

---

## D046 — The desktop virtiofs share needs the host `virtiofsd` package (one sudo step); the VM's own XML edit stays unprivileged

**Date:** 2026-09-08
**Context:** D043 says the domain-XML edit needs no sudo, which holds, but libvirt must also launch
`virtiofsd` and Ubuntu 24.04 does not ship it with QEMU 8.2.2 (`apt-cache policy virtiofsd`:
candidate 1.10.0-1ubuntu0.1, not installed). Everything tried without sudo failed for the same
reason, AppArmor:
- `<binary path='/home/jary/act-device/virtiofsd/virtiofsd'/>` (binary extracted from the .deb with
  `apt-get download` + `dpkg-deb -x`): libvirtd's enforced profile only allows
  `/usr/{lib,lib64,lib/qemu,libexec}/virtiofsd PUx` (`/etc/apparmor.d/usr.sbin.libvirtd:97`) →
  "virtiofsd died unexpectedly".
- Unprivileged socket mode (`virtiofsd --sandbox=none` as jary in a user unit + `<source
  socket=…>`): DAC verified OK as uid 64055 (`DAC-write-ok`), yet QEMU got `Permission denied` on
  the socket — the per-VM QEMU profile (virt-aa-helper) adds no rule for a virtiofs socket source,
  and the shared abstraction deliberately gives no blanket rw under /tmp or /home. Disabling the
  VM's AppArmor label (`<seclabel type='none' model='apparmor'/>`) would work but weakens the host's
  policy; not applied.
**Decision:** `sudo apt install virtiofsd` on the desktop (the .deb is already at
`~/act-device/virtiofsd/virtiofsd_1.10.0-1ubuntu0.1_amd64.deb` for `sudo dpkg -i`), then
`device/vm/bags-share.sh` attaches the share unprivileged. libvirt-managed virtiofsd runs as root
with `--sandbox namespace`, so guest-root writes land as **host root** — the same ownership the
host `docker run` produces today (every bag dir under `~/flywheel-data/bags` is `root:root`), so the
assemble/prune flow, which already runs inside root containers, is unchanged.
**Consequences:** `create-vm.sh` and `bags-share.sh` exit 2 with that instruction when the package is
missing (same pattern as the missing base image). `<memoryBacking memfd/shared>` is already defined
on `act-device` (cold restart done 2026-09-08), so attaching the share later is define + one more
cold restart. All socket-mode artefacts were removed from the host; `/home/jary` is back to 0750.
Port-as-you-go log and safety details recorded alongside this: `after_assemble.sh` keeps writing to
`~/prune-dryrun.txt` (name kept so the retention log stays in one file; entries are now appended
under a timestamp header) and judges the **last** `[assemble-all] DONE|FAILED` marker, not any DONE
in the log's history. `prune_bags.py --yes` prints each deleted bag with its size, so that file is a
real record of what went. Credentials only from `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` (fail-clear,
verified); the host keeps them in `~/.minio-env` (0600), sourced by `after_assemble.sh` and passed
into the container with `-e`. Not run today (SNO MinIO/Kafka down) — `~/assemble-all.log` already
ends with the 12:14 DONE, so `~/after_assemble.sh` will prune immediately when started.

---

## D047 — SELinux: label the bags mount with `context=` and mount it without `:z`

**Date:** 2026-09-08
**Context:** the guest is Enforcing. libvirt does not pass `--xattr` to virtiofsd by default, so the
guest cannot store SELinux labels on the share and podman's `:z` relabel has nothing to write to.
**Decision:** the fstab entry carries `context=system_u:object_r:container_file_t:s0`; the Fleet's
bags line is `Volume=/var/lib/act-inference/bags:/data/bags` (no `:z`, unlike `data`). On the Fury,
where the directory is local disk, `provision.sh` labels it with `chcon -R -t container_file_t` so
the same Fleet line works there.
**Status:** untested on the device because the share could not be brought up (D046). Verify once
attached: `sudo podman run --rm -v /var/lib/act-inference/bags:/data/bags
registry.access.redhat.com/ubi9/ubi-micro:9.5 touch /data/bags/.podman-test` then
`ausearch -m avc -ts recent`. If `:z` turns out to be harmless on virtiofs, the two Volume lines can
be made uniform again.

---

## D048 — flightctl CLI identity on the hub: a real OpenShift user (kubeadmin), not the chart's ServiceAccount

**Date:** 2026-09-08
**Context:** the chart creates `ServiceAccount/flightctl-admin` bound to
`ClusterRole/flightctl-admin-flightctl` (`createAdminUser: true`) and its NOTES suggest `flightctl
login --token`. On this cluster the SA token validates but `GET /api/v1/organizations` returns an
empty list and login fails with "You do not have access to any organizations": with `auth.type:
openshift`, organisations are the projects labelled `io.flightctl/instance=flightctl` that the
*caller* can read, and the SA has no `get` on projects (`oc auth can-i get projects --as=system:
serviceaccount:flightctl:flightctl-admin` → no). kubeadmin (in `system:cluster-admins`) logs in,
auto-selects the `Default` organisation, and `flightctl get fleets` answers — no 403, so thor's
D005 RBAC workaround is **not** ported.
**Decision:** bootstrap and demo CLI work use a user token (kubeadmin via a throwaway kubeconfig,
since the install kubeconfig is certificate-based and `oc whoami -t` is empty there). The SA stays
as the chart ships it; nothing is added to `gitops/rhem-config/`. Neither the desktop nor the Mac
resolves the `api.` route without sudo, so `~/.config/flightctl/client.yaml` on the desktop now
targets `localhost:3443` via `oc port-forward svc/flightctl-api 3443:3443`. The thor-era OSD-hub
client config was kept rather than overwritten — backed up as `client.yaml.bak-osd-hub-2026-09-08`.
**Alternatives:** grant the SA `get` on the flightctl project (RoleBinding to `view`) so it maps to
the organisation — plausible, untested, and adds hand-made RBAC the chart does not own; drop
`createAdminUser` — no benefit.
**Consequences:** the user token expires (24 h); re-login before a demo. Routes still need the
`/etc/hosts` line on the Mac; until then `oc port-forward svc/flightctl-api 3443:3443` on the
desktop is the working path (used today).

---

## D049 — Device provisioning installs `skopeo`; flightctl-agent 1.3.0 needs it to prefetch application images

**Date:** 2026-09-08
**Context:** the first rendered spec on the enrolled VM failed before anything was written:
`Marking template version 1 as failed: before update: prefetch: prefetch collector 0 failed:
extracting oci: act-inference: application dependency: required commands not found: "skopeo"`
(`agent/device/device.go:213`, 22:46:21Z). The device rolled back to renderedVersion 0 and reported
`OutOfDate` / `NoApplications`. `flightctl-agent-1.3.0-1.el10` does not *require* skopeo in its RPM
metadata (device/README.md "Flags verified" lists its Requires: bash, jq, sudo, flightctl-selinux,
libresolv, glibc) — the dependency is a runtime `exec.LookPath` in the application prefetch path, so
`provision.sh`'s pinned, weak-deps-off install left it out.
**Decision:** `device/provision.sh` installs `podman skopeo` in one `dnf` call (working-tree edit,
uncommitted, for the runner to fold in). On the live VM `skopeo-2:1.22.2-5.el10_2` was installed by
hand (22:46:47Z) and the agent restarted; the same rendered version was then retried without a spec
change (`New spec version received: 0 -> 1`, `Started quadlet application`, 22:46:51Z).
**Alternatives:** wait for the agent to retry on its own (rejected — a version marked failed is not
retried until the spec changes or the agent restarts); mark skopeo as a Fleet-delivered package
(rejected — package mode has no package provider; this belongs to provisioning).
**Consequences:** the Fury provisioning gets skopeo for free from the same script. A failed
prefetch on an otherwise healthy device leaves the app *absent*, not degraded — the runbook should
read `flightctl get device -o yaml` `.status.updated.info` before anything device-side.

---

## D050 — While `RECORD=false`, the Fleet ships no bags `Volume=`; a quadlet host-path volume is a hard mount dependency

**Date:** 2026-09-08
**Context:** with skopeo present, renderedVersion 1 wrote the config and the quadlet, but
`act-inference-128875-act-inference.service` stayed `inactive (dead)`: `Dependency failed …
Job act-inference-128875-act-inference.service/start failed with result 'dependency'`. The podman
quadlet generator turns every host-path `Volume=` into `RequiresMountsFor=<path>`; the VM's fstab
carries the D043 virtiofs entry `bags /var/lib/act-inference/bags virtiofs …,nofail,…`, so systemd
owns a `var-lib-act\x2dinference-bags.mount` unit, tries it, and fails (no virtiofs tag — the share
is blocked on D046). `nofail` only keeps the boot from failing; it does not make `RequiresMountsFor=`
tolerate a failed mount. The Fleet was correct for the desktop-with-share and for the Fury (no fstab
entry, `RequiresMountsFor` satisfied by the root fs); the pre-placed fstab line was the defect.
The agent's guest `/etc/fstab` edit was refused by the session's permission classifier, so the
device-side fix was not available to this run.
**Decision:** `Fleet fix:` commit `ffff8a3` removes `Volume=/var/lib/act-inference/bags:/data/bags`
for the D044 interim, with the line kept as a comment to be restored **in the same commit** that
flips `RECORD` to `"true"`. Nothing writes bags while `RECORD=false`, so the interim Fleet is
self-consistent. The fstab line stays on the VM: it becomes correct the moment `virtiofsd` +
`bags-share.sh` attach the share (D046), and `provision.sh` only ever appends it when the tag exists.
Rollout: ResourceSync observed `ffff8a3` at 22:51:59Z, Fleet generation 2, device renderedVersion 2
at 22:52:08Z, container up 22:52:08Z, `applicationsSummary: Healthy` at 22:52:55Z.
**Alternatives:** `PodmanArgs=-v …` to dodge `RequiresMountsFor=` (rejected — a failed share would
silently record into the VM disk under the mountpoint, exactly what D043/D044 exist to prevent);
`systemctl mask` the mount unit (rejected — a masked `Requires=` dependency fails the same way).
**Consequences:** re-enabling recording is now a three-line Fleet change (`RECORD`, the `Volume=`
line, and nothing else); the operator step list for D046 gains "confirm the mount is active before
flipping RECORD". For the Fury, where the bags dir is local disk, the restored line needs no fstab.
With the C3 role split the recorder no longer runs on the device, so the desktop bags share
(D043/D044/D046/D047) is no longer needed; those entries stay as history.

---

## D051 — Interim health probe runs `/healthcheck.sh` with its `set -u` neutralised; the image script is fixed for F

**Date:** 2026-09-08
**Context:** with the app running, every podman health probe exited 1:
`/opt/ros/kilted/setup.bash: line 8: AMENT_TRACE_SETUP_FILES: unbound variable`.
`docker/healthcheck.sh` runs `set -u` *before* sourcing the ROS and colcon setup scripts, which
reference unbound variables (`AMENT_TRACE_SETUP_FILES`, then `AMENT_PYTHON_EXECUTABLE`,
`AMENT_PREFIX_PATH`, … — presetting them just walks the chain). With the Fleet's
`HealthOnFailure=kill` + `Restart=always` that is a kill 240 s after every start (observed: start
22:52:08Z → killed and restarted 22:57:28Z) — a 6-minute crashloop that RHEM still reported as
`Healthy` (its summary follows container state, not the podman health status). The runtime image is
digest-pinned (D045) and cannot change before F.
**Decision:** `Fleet fix:` commit `a8fbe3b` sets
`HealthCmd=sed 's/^set -u/set +u/' /healthcheck.sh | bash -s` (a string `HealthCmd=` runs under
`sh -c`; identical checks, trap disabled; no `$` so nothing for systemd/quadlet to escape). Verified
inside the container before committing: `healthy: model_version=act-v2-ft160 action=/run_policy`,
rc 0. Rollout: ResourceSync observed `a8fbe3b` 22:57:59Z, Fleet generation 3, renderedVersion 3,
container started 22:58:19Z, podman health `healthy` from 22:58:49Z, no further restarts.
`docker/healthcheck.sh` is fixed at the source (sources first, then `set -u`; working-tree edit,
uncommitted) so F's image carries the correct script and the Fleet line reverts to
`HealthCmd=/healthcheck.sh` in the same F commit that re-pins the digest.
**Alternatives:** `HealthOnFailure=none` until F (rejected — hides the failure and loses the
self-recovery D041 chose); env-var presets in `envVars` (rejected — the chain of unbound names is
open-ended).
**Consequences:** two `Fleet fix:` iterations were used of the three allowed. RHEM's
`applicationsSummary: Healthy` is necessary but not sufficient for "the probe passes" — the runbook
screen should pair it with `podman ps` showing `(healthy)` (via `flightctl console … sudo -n podman ps`).

---

## D052 — `flightctl console` runs as `flightctl-console` (uid 990); device commands that need podman go through `sudo -n`

**Date:** 2026-09-08
**Context:** the console session the agent spawns is unprivileged: `id` → `uid=990(flightctl-console)`,
`HOME=/var/lib/flightctl`. A bare `podman logs …` fails with `stat /var/lib/flightctl/.config: no such
file or directory` (podman looks for a per-user config in `$HOME`), and `HOME=/root` gives
`permission denied`. `sudo -n` works for that user (the agent package ships the sudoers rule).
Separately, each console session tears down the `oc port-forward` that stands in for the
unresolvable `api.` route on the desktop (`error: lost connection to pod`).
**Decision:** the runbook's Beat-6 invocation is
`flightctl console device/<name> --notty -- sudo -n podman logs --tail 20 act-inference-128875-act-inference`
(quadlet-namespaced container name: `<app>-<id>-<ContainerName>`); `--notty` for scripted
capture, `--tty` for a live tail. The port-forward on the desktop is kept in a retry loop
(`while true; do oc port-forward -n flightctl svc/flightctl-api 3443:3443; sleep 1; done`, log
`~/flightctl-pf.log`) so it survives console sessions.
**Alternatives:** resolve `api.flightctl.apps.sno-flywheel.local` on the desktop (needs sudo for
`/etc/hosts` — not available to the agent); run the console from the Mac (same resolution gap).
**Consequences:** the `/etc/hosts` entries for the three routes on the demo laptop remain an
operator step (D006 port); until then every `flightctl` demo command runs on the desktop over ssh.

---

## D053 — Spike part 2 fails as written: CPU inference on the VM leaves 6% of command intervals over 40 ms; success rate is the arbiter (part 3)

**Date:** 2026-09-08
**Context:** measured inside the Fleet-managed container on the VM (probe subscribed to
`/forward_position_controller/commands`, windowed by the coordinator's `start`/`end` signals, first
59.5 s of each window; `device/spike` probe, run 23:00:50Z–23:05:50Z), three full episodes:

| ep | msgs | rate (wall) | gap p50 | p95 | p99 | max | > 40 ms | > 100 ms | > 200 ms |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 2156 | 36.3 Hz | 20.2 ms | 57.1 | 211.1 | 335.2 | 134 | 64 | 28 |
| 2 | 2134 | 35.9 Hz | 20.2 ms | 54.9 | 229.2 | 313.4 | 135 | 62 | 46 |
| 3 | 2121 | 35.8 Hz | 20.2 ms | 54.6 | 229.9 | 325.3 | 129 | 62 | 42 |

All episodes: 6408 intervals, max 335.2 ms, 398 (6.21 %) over 40 ms. `ros2 topic hz -w 3000`
over the same period: average 33.1 Hz (window spans an inter-episode idle, so its `max` 5.24 s is
not a within-window number). The rosetta client's own watchdog agrees: `Action timeout - sending
safety action` (`rosetta.py:324`, 2 frame periods = 40 ms at the contract's 50 Hz) fired 67 times
in episode 1's window, ~1.1/s. Mechanism: the policy server logs ~4 forward passes per second
(`Running inference for observation #… (must_go: True)`), i.e. the live node parameters `actions_per_chunk=30` and
`chunk_size_threshold=0.95` (from the launch file's `params_file`; node defaults 50 / 0.5) re-request
a chunk almost as soon as one lands, while an in-guest forward takes 170–250 ms (cpu-spike.md part 1, in-guest row) — the queue runs dry at every chunk boundary.
The 2 s / 1000 ms budget in D024 assumed one forward per `n_action_steps=100`; the client asks for
30 at a time, so the real chunk window is 0.6–0.8 s and the compute margin is ~3×, not 12×.
The host GPU container had no usable baseline in its logs (every instance since 20:xx was cycled
by the watchdog inside 18–42 s). The sim itself runs at speed: `/clock` RTF 0.990 and
`/joint_states` 49.6 Hz wall measured from the same VM (23:09Z), so the gaps are policy-side
wall-time stalls, not a slow sim or the VM's network.
**Decision:** record part 2 as **FAIL** against the criterion as written, and let part 3 (20-seed
D020 eval, ≥ 15/20) decide the stand-in: the live loop since cut-over produced 10 curated successes
and 1 reject in its first 13 minutes, so the gaps are not obviously costing task success. D024's
first fallback maps to `actions_per_chunk` (rosetta client parameter, default 50, launched at 30 —
`rosetta_client_node.py:169`), not to the policy's `n_action_steps`: raising it to 100 (the
checkpoint's chunk) and lowering `chunk_size_threshold` toward 0.5 turns the ~4 forwards/s into
~0.5/s with ~1 s of queue to hide a 250 ms forward. Both live in the `params_file` that
`rosetta_client_launch.py` consumes (the entrypoint's `key:=value` arguments are not declared by
that launch file), so the change rides with F's image; the Fleet can then carry it per `gpu` label.
**Alternatives:** pin the VM's vCPUs (`VCPU_CPUSET`, D034) — expected ~2× on the forward, still
inside the 0.6 s chunk only marginally; lower the Gazebo RTF (changes the sim for the host GPU path
too); VFIO (last resort, D024).
**Consequences:** cpu-spike.md parts 2–3 carry these numbers; the runbook should not promise
"no visible stutter" for the desktop stand-in. On the Fury (GPU, `policy_device=cuda`) the same
Fleet has no such gap. Superseded the same evening by the C3 role split (see the next decisions):
the coordinator, recorder and sim reset move back to the host beside the simulator; the device runs
the policy role only; chunk params move to the params file (`actions_per_chunk=100`,
`chunk_size_threshold=0.5`).

---

## D054 — Enrollment identity and Fleet label set for the desktop stand-in (as enrolled)

**Date:** 2026-09-08
**Context:** `device/enroll.sh` ran unchanged against the live hub (flightctl CLI/server 1.3.0):
certificate request `act-device-8e8e4333` (signer `flightctl.io/enrollment`, embedded config),
agent started 22:46:10Z, enrollment request `s28p3s5ln7o5m1bccplipa4v5eqmqetqelg9ltqdii92rco95hdg`,
approved 22:46:10Z with `fleet=act-inference site=desktop gpu=none policy_device=cpu arch=amd64
zenoh_router=10.0.0.48 zenoh_port=7447` (plus the agent's `alias=act-device.localdomain`), device
visible at 22:46:10Z, owner `Fleet/act-inference`. Cut-over on the host preceded the label:
swap agent (pid 3483061) and bag watchdog (pid 3507013) killed and `docker stop act-inference`
at 17:45:46–17:45:58 CDT (22:45:46–22:45:58Z); `docker ps` then showed only `so-arm-sim`,
`pose-ui` and the unrelated `competent_chatelet`.
**Decision:** the device name RHEM uses is the agent's fingerprint, not the CSR name; the runbook
addresses it by alias in the UI and by name on the CLI. `enroll.sh` needs no change for the Fury.
**Consequences:** the `flightctl login` on the desktop still uses the item-A user token (the
kubeconfig is cert-based, `oc whoami -t` returns none); when it expires, mint a token from the OCP
OAuth route or `oc create token` for a user that maps to an org.

---

## D055 — `POLICY_DEVICE` does not reach the rosetta client node; CPU on the VM is the CUDA-unavailable fallback

**Date:** 2026-09-08
**Context:** the managed container logs `[inference] Launching Rosetta client with ACT on cpu...`
(entrypoint, `policy_device:=${POLICY_DEVICE}` on the `ros2 launch rosetta rosetta_client_launch.py`
line) and then `[rosetta_client]: Requested policy device 'cuda' but the requested backend is
unavailable; using 'cpu' instead.` (`rosetta_client_node.py:568-572`: the node reads its own
`policy_device` parameter, default `cuda`, and `_resolve_policy_device` downgrades it). The
`rosetta_hil_launch.py` file declares `policy_device` / `actions_per_chunk` /
`chunk_size_threshold` launch arguments; `rosetta_client_launch.py` (the one the entrypoint uses)
declares `params_file`, `contract_path`, `pretrained_name_or_path`, `server_address`,
`launch_local_server`, `use_sim_time`, `log_level`, `configure`, `activate` — no `policy_device`
(`ros2 param get /rosetta_client policy_device` → `cuda` on the live VM, 23:12Z; likewise
`actions_per_chunk=30`, `chunk_size_threshold=0.95`).
The checkpoint's `config.json` also carries `"device": "cuda"`. Net effect: on the VM the policy
runs on CPU because CUDA is absent, not because the Fleet's `POLICY_DEVICE=cpu` was honoured; on a
GPU host `POLICY_DEVICE=cpu` would silently run on CUDA. The BUILD-PLAN 4.5 C bullet "`POLICY_DEVICE`
env replaces the hard-coded `policy_device:=cuda`" is therefore only half true.
**Decision:** F's image fixes the plumbing where the node actually reads it — pass
`--ros-args -p policy_device:=$POLICY_DEVICE` (and `actions_per_chunk`, see the part-2 decision)
to the client node, or a generated params file, and assert the setting from the log line the node
prints. Until then the stand-in is correct by accident and the Fury (`policy_device=cuda`) is
unaffected.
**Consequences:** `docs/eval-records/cpu-spike.md` notes the fallback; the runbook's "Device: cpu"
screen should quote the *node's* resolved-device line, not the entrypoint echo.

---

## D056 — The coordinator's sim reset and cube judge use Gazebo transport, which is host-local; from the device they are silent no-ops

**Date:** 2026-09-08
**Context:** spike part 3 on the VM returned `cubes=0/3 success=False` for five consecutive seeds
(1000–1004; the GPU baseline passes four of them) with ~400 `steps` per 64 s window, and was
stopped. `sim_reset.py` repositions cubes with `gz service …/set_pose`; `task_eval.evaluate_task()`
reads `gz topic -e -t /world/pai_world/pose/info -n 1` (8 s timeout, returns `{}` on any failure);
the coordinator's peak-cube poll swallows exceptions. From inside the VM container: `gz topic -l`
lists the pose topic (multicast discovery crosses `br0`) but `gz topic -e … -n 1` receives nothing
in 20 s and `gz service -l` shows no `set_pose` — the data/service path back to the guest never
comes up (`so-arm-sim` is `--network host` on `jary-ubuntu`). Consequences already visible in the
live loop: after the first post-cut-over episode the cubes were never re-randomised, so
`ba0d930c` (3/3 two seconds after `start`, 30 steps) and the later "successes" inherited cubes
already on the tray. Separately, in eval mode the coordinator counts `/joint_states` itself from a
starved Python thread (~6 Hz on the torch-saturated VM vs 20–43 Hz on the GPU host), so `steps`
is not comparable either.
**Decision:** criterion 3 stays open; the desktop stand-in's episodes are lineage evidence
(VM coordinator → `/flywheel/model_version` → host emitter → curator → MinIO) and not task
evidence, and the runbook must not quote a success rate from them. Fix owner: the reset + judge
must run where Gazebo transport is local or move behind ROS services — options in order of
preference: (c) host-side ROS services for reset and cube poses that the device coordinator calls
over zenoh (also the right contract for the Fury, where the sim is on another box); (a) a
host-only "coordinator" mode driving the device's `/run_policy` while the device runs the rosetta
client only (entrypoint switch, F); (b) `GZ_IP`/`GZ_PARTITION` plumbing across the bridge
(untested, touches `so-arm-sim`). Interim for the demo: the host GPU `act-inference` can be left
stopped only if a reset path exists; otherwise Beat 6's live loop from the VM shows lineage but a
static scene.
**Alternatives:** run the 20-seed eval on the host against the VM's action server with two
coordinators alive (rejected — both send `/run_policy` goals; the device container cannot drop its
coordinator without an image change).
**Consequences:** Phase 4.5 C exit "curator receives `act-v2-ft160` episodes from the VM" is met
in the lineage sense only; D024's part-3 criterion moves to whichever of (a)/(b)/(c) lands; the
`eval-*` discard (D020) and `RECORD=false` (D044) kept the corpus clean throughout — the five
invalid episodes never reached the emitter.

---

## D057 — RHEM delivers model serving only; the sim harness runs with the simulator (role split of the ACT runtime)

**Date:** 2026-09-08 (orchestrator decision, recorded from the C3 run)
**Context:** D056: the Fleet-managed container on the desktop VM ran coordinator + recorder + policy,
and the coordinator's cube reset (`sim_reset.py` → `gz service …/set_pose`) and cube judge
(`task_eval` → `gz topic -e`) use Gazebo transport, which is host-local — from the VM every reset was
a silent no-op and every verdict meaningless; bags on the VM needed a virtiofs share blocked on host
sudo (D046). On the Fury the device *is* the sim host, so the same code must also run as one box.
**Decision:** `docker/inference-entrypoint.sh` takes `ROLE=policy|coordinator|all` (default `all`,
today's behaviour):
- `policy` — rosetta client + policy server + a latched `/flywheel/model_version` publisher
  (`src/inference-coordinator/model_version_pub.py`, same TRANSIENT_LOCAL/KEEP_LAST QoS the
  coordinator used) carrying `$MODEL_VERSION`. This is what RHEM delivers: runtime image + ModelCar
  volume + lineage label + health. No bags, no `/data`.
- `coordinator` — coordinator + episode recorder + sim reset, no policy server. `coordinator.py`
  subscribes to `/flywheel/model_version` instead of publishing it and logs
  `Observed model_version: <v>` (warns if it differs from its own `MODEL_VERSION`); the eval JSON
  records it as `served_model_version`. Runs next to the simulator:
  `tools/host/run-coordinator.sh` (docker on the desktop, podman on the Fury; `MODE=loop|eval`).
- `all` — both, one container (host GPU path, Fury single box).
The emitter (in the sim container) and `healthcheck.sh` are unchanged: both only see the latched topic.
Fleet `act-inference` ships `ROLE=policy`, `MODEL_VERSION`, `POLICY_PATH`, `ACTIONS_PER_CHUNK`,
`CHUNK_SIZE_THRESHOLD`; the coordinator/sim env (`RECORD`, `RESET_ARM`, `EPISODE_LEN`,
`RANDOMIZE_*`, `REST_POSE`) and the `/data` volume are gone from the device.
Live result: ResourceSync observed `210e79e` at 23:44Z; device renderedVersion 4, `UpToDate`,
`applicationsSummary: Healthy`, podman `(healthy)` 43 s after the container start (23:45Z). The host
coordinator (started 23:54:11Z with the old `act-inference` container's env and mounts mirrored
exactly — `MODEL_VERSION=act-v2-ft160`, `~/flywheel-data:/data`, `~/flywheel-data/bags:/data/bags`,
host network, no GPU) logged `Observed model_version: act-v2-ft160`, found `/run_policy` and the
recorder, and at every observed episode start the tray held 0/3 cubes (`task_eval` snapshots at
starts #1–#4, including after a 3/3 success) — the reset works from the host. Every episode carried
`Dataset ref: 'bags/<sec>_<nsec>'`; 62 bags were kept (3/3 episodes) and the rest pruned by the
coordinator's own verdict; the emitter counted 20 SUCCESS / 47 FAIL between 23:54Z and 00:45Z (the coordinator's own peak-cube verdict kept 62 — the two judges differ at the margin) —
both verdicts, real scenes.
Caveat on the mirrored env: the old host container carried **no** randomisation env (D040's "copied
from the host `docker run`" set was never what the host ran — the coordinator defaults applied:
`EPISODE_LEN=25`, nominal cubes, `RESET_ARM=true`). Runner to decide whether the loop should adopt
the D040 set (`EXTRA_ARGS` on the script); this run kept the mirror.
**Alternatives:** (b) gz-transport bridging into the VM (`GZ_IP`/`GZ_PARTITION`, a route for the
publisher back to the guest) — rejected: untested, touches `so-arm-sim`, and does not describe the
Fury where sim and device are one box; (c) ROS-service wrappers for reset + cube poses on the host
called from a device coordinator — deferred: cleaner contract, but bags would still be on the device
and it needs a new package; (d) virtiofs bags share (D043/D046/D047) — superseded on the desktop: the
bags share is no longer needed because the recorder runs where the bags live.
**Consequences:** supersedes the *placement* half of D040 (coordinator settings are not Fleet
`envVars`; the env file's per-device values stay) and D041's `/data:z` volume; D043, D044, D046, D047
and the D050 `Volume=` interim are **superseded on the desktop — the bags share is no longer needed**
(on the Fury the coordinator role runs on the host disk with the same script). D051's `HealthCmd=`
workaround is retired (the image carries the fixed `healthcheck.sh`). D056's criterion-3 "fix owner"
is resolved by option (a). Runbook Beat-6 grep changes: the *device* log shows
`Published model_version:` (node `model_version_publisher`), the *host* coordinator shows
`Observed model_version:`. Two coordinators must never be alive at once (both send `/run_policy`
goals): `run-coordinator.sh` replaces any container of the same name, and the D020 eval is
`MODE=eval` on the same script with the loop stopped first.

---

## D058 — rosetta client chunking is a params-file setting from env: `actions_per_chunk=100`, `chunk_size_threshold=0.5`, `policy_device` likewise

**Date:** 2026-09-08
**Context:** D053/D055: the upstream `params/rosetta_client.yaml` ships `actions_per_chunk: 30`,
`chunk_size_threshold: 0.95`, `policy_device: cuda`; `rosetta_client_launch.py` only declares
`params_file`, `contract_path`, `pretrained_name_or_path`, `server_address`, `launch_local_server`,
`use_sim_time`, `log_level`, `configure`, `activate`, so the entrypoint's `policy_device:=` (and
`policy_type:=`) were silently ignored. With 30/0.95 the client re-requests a chunk almost
continuously; on CPU (170–250 ms per forward) the queue ran dry at every boundary (6.2 % of
intervals > 40 ms, max 335 ms).
**Decision:** the entrypoint loads the package params file, sets `policy_device=$POLICY_DEVICE`,
`actions_per_chunk=$ACTIONS_PER_CHUNK` (default **100** = the checkpoint's `chunk_size`),
`chunk_size_threshold=$CHUNK_SIZE_THRESHOLD` (default **0.5**), writes
`/tmp/rosetta_client_params.yaml` and passes it as `params_file:=`. Verified live on the device
(`ros2 param get /rosetta_client …`, 23:52Z): `actions_per_chunk` Integer 100, `chunk_size_threshold`
Double 0.5, `policy_device` String cpu; the node no longer logs the CUDA-unavailable fallback. The
Fleet carries the two chunk values as `envVars` (same on both boxes, D059).
**Alternatives:** `--ros-args -p` overrides on the launch line (the launch file does not forward
extra ros-args to the node); `sed` on the package file (what the Dockerfile used to do — fragile,
and baked at build time rather than set per device).
**Consequences:** part 2 was re-measured with the new values inside the device container
(23:54:49–00:01:49Z, 25 s windows); the probe log sits on the paused VM (`~/spike/cadence-c3-235449.log`)
and is read once the VM resumes. `POLICY_DEVICE=cpu` on a GPU host now really runs on CPU (D055's
"correct by accident" is closed).

---

## D059 — One chunking code path: 100/0.5 on the GPU path too; the 17/20 GPU baseline was measured at 30/0.95

**Date:** 2026-09-08
**Context:** the entrypoint defaults apply to `ROLE=all` on the host GPU and to the Fury
(`policy_device=cuda`) as much as to the CPU stand-in. Keeping 30/0.95 on GPU would mean a
per-`gpu`-label branch in the Fleet and two behaviours to explain.
**Decision:** same defaults everywhere (`ACTIONS_PER_CHUNK=100`, `CHUNK_SIZE_THRESHOLD=0.5`); the
Fleet sets them once, not per label. Caveat recorded: every GPU eval in `docs/eval-records/` before
this date (teacher 86 %, ft-160 17/20) ran at 30/0.95, i.e. the client re-planned from a fresh
observation nearly every tick; at 100/0.5 it re-plans about once per second. Part 3 compares
CPU@100/0.5 against that GPU@30/0.95 baseline (pass ≥ 15/20); if a future GPU re-baseline at 100/0.5
moves the number, the ladder JSONs must state the chunk params (the eval JSON does not carry them —
the coordinator role cannot see the node's params without a `ros2 param get`; open item, not a
speculative field).
**Alternatives:** Fleet branch `{{ if gpu == "nvidia" }}ACTIONS_PER_CHUNK=30…` (rejected — two code
paths for one demo); keep 30/0.95 everywhere and pin vCPUs instead (rejected — D053 shows that only
buys ~2×).
**Consequences:** `~/eval_policy.sh` on the host (GPU, `act-inference:latest`) is unaffected until that
image is rebuilt from this Dockerfile; when it is, it inherits 100/0.5.

---

## D060 — The D020 seeded eval of a device-served policy is the coordinator role in eval mode on the sim host

**Date:** 2026-09-08
**Context:** D056 rejected "two coordinators alive" for the eval; with the split the device has no
coordinator, so the host can run `EVAL_MODE=true` against the VM's `/run_policy` with reset + judge
local to Gazebo. `MODEL_VERSION` in eval mode is the eval label (results file name; `eval-*` is
discarded by the curator, and the eval never signals the emitter), while the device keeps
publishing `act-v2-ft160` — recorded as `served_model_version` in the results JSON.
**Decision:** `tools/host/run-coordinator.sh` `MODE=eval <N> <seed_base>` (foreground, `--rm`,
`--no-healthcheck`, the same pinned D020 config as `~/eval_policy.sh`: `EPISODE_LEN=60`,
`RESET_ARM=true`, `RANDOMIZE_ONLY=cube_medium`, `RANDOM_RADIUS=0.03`, `RANDOM_YAW_DEG=180`,
`RECORD=false`). The loop container must be stopped first (`docker stop act-coordinator`) and
restarted after. Part 3 did **not** run in C3 (see the next entry).
**Consequences:** `~/eval_policy.sh` stays the GPU/host-policy path; the VM path is the script above.
The `steps` metric in eval JSONs becomes comparable again (the coordinator counts `/joint_states` on
the host, not on the torch-saturated VM — D056's second artefact).

---

## D061 — The host coordinator filled the desktop disk in 50 minutes; both VMs paused on I/O error (blocker, operator decision)

**Date:** 2026-09-09
**Context:** with recording back on the host, every 3/3 episode keeps a ~1.3 GB MCAP bag
(`EPISODE_LEN=25`, two 480×480 cameras). 62 bags (79 GB) were kept between 23:54Z and 00:44Z on
top of 400 pre-existing bags (637 GB); the root filesystem (`/dev/nvme1n1p2`, 1.8 TB) hit 100 %
(605 MB free), and qemu paused **both** `sno-flywheel` and `act-device` (`virsh domstate --reason`:
`paused (I/O error)`; their qcow2 disks live on the same filesystem). Last good episode 00:44:45Z;
from then on the coordinator logged `Goal rejected — retrying next cycle` (2188×), the emitter
`POST to curator failed: No route to host`, and every episode failed 0/3 and was pruned (no further
growth). The coordinator container was stopped at 10:28:05Z. The ~84 GB "free after prune" figure in
`.plans/rhem-runner.md` was the whole budget; the C2 `RECORD=false` interim had hidden the rate.
**Decision (proposed):** none taken by the agent — freeing space means deleting or moving bags,
which the C3 brief forbids, and nothing else on the disk is both large and the agent's to remove
(docker reclaim < 100 MB; `~/.cache` 13 GB is user data; VM images cannot shrink). Options for the
operator, cheapest first: (1) delete or move the 62 unported C3 bags
(`~/flywheel-data/bags/1788911682_712266789` … `1788914576_041373221`, 79 GB; re-recordable, none
ported, none referenced by an assembled dataset) — enough to resume both VMs and finish C3; (2) run
the port-as-you-go flow once MinIO is back (assemble → `prune_bags.py --yes`) — needs the SNO
resumed first, so it cannot be the first step; (3) point `DATA_DIR` at a second disk before the loop
restarts. In all cases the loop needs a disk guard before it runs unattended again (the old
`bag_watchdog.sh` was killed at cut-over and never replaced): a minimum-free-space check in
`run-coordinator.sh` or `RECORD=false` until the assembler runs on a schedule.
**Consequences:** `virsh -c qemu:///system resume sno-flywheel act-device` after space is freed (an
I/O-error pause resumes cleanly once writes succeed); then re-check RHEM (`flightctl get device …`),
the device container (podman restarts it if the health probe lapsed), and MinIO reachability. C3
exit criteria not yet met: part 2 numbers are on the paused VM, part 3 has not run, the
curated/rejected MinIO recount could not be taken. D019's "raw bags stay on the host" now has a
hard number attached: ~1.3 GB per kept episode, ~100 GB per hour of successes.

---

## D062 — Recovery from the 2026-09-09 disk outage: proof bags archived to the media disk; a disk guard is mandatory before the host coordinator runs

**Date:** 2026-09-09
**Context:** D061's root-disk fill: the host disk hit 100 % (992 MB free) ~00:44Z after 62 bags
(79 GB) were recorded on the host in 50 minutes with no guard — the old `bag_watchdog.sh` was killed
at cut-over and never replaced, and it also had a silent-park bug; both VMs paused on I/O error.
**Decision:** operator authorized (2026-09-09): `docker image prune -a` (reclaimed 0 B — shared
layers) and moving the 172 proof bags (timestamp < 1788560700, 288 GB) to
`/media/jary/videos/flywheel-bags-archive/` via a root container with copy → size-list compare →
`cmp metadata.yaml` → rename → remove source (logged to `~/bag-archive-2026-09-09.log`); declined
deleting the night's 62 bags and mounting `sdb1` (4.5 TB ext4, unmounted — inbox todo). Free space:
991 MB → 198 GB by 11:01Z. VMs resumed 10:58Z; SNO Ready, COs clean, MinIO 200, Argo Synced; device
Online/UpToDate/Healthy without restart; guest clocks stayed at ~00:46Z (paused guests don't
advance) — `virsh domtime --sync` fixed SNO, `act-device` needs `domtime --now` or
`chronyc makestep`. `tools/host/disk-guard.sh` (poll 60 s, `MIN_FREE_GB=100`, `CAP=450`,
unconditional PARKED line) is armed on the host and `tools/host/run-coordinator.sh` refuses loop
mode without it or under the floor; the loop coordinator stays stopped after the C3 eval until the
operator starts it.
**Alternatives:** delete the night's bags (declined); rely on port-as-you-go alone (needs the hub up
and hours of assembly — not a guard).
**Consequences:** lesson recorded in brim: never retire a limiter without arming its replacement in
the same step. `sdb1` (4.5 TB ext4, unmounted) remains an inbox todo for a durable second-disk
solution.
