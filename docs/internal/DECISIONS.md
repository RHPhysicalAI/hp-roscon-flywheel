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

---

## D063 — A resumed VM is not recovered until its clock is stepped and the policy action server is restarted

**Date:** 2026-09-09
**Context:** D062 records the archive, the resume and the clock finding. Two further facts from
the same recovery: (1) `virsh domtime --sync` fixed `sno-flywheel` (11:00:47Z, node heartbeat
11:01:01Z, still Ready, COs clean) but not `act-device`; `virsh -c qemu:///system domtime --now
act-device` did (11:15:24Z, chrony 0.09 µs of NTP). (2) After the resume the device container was
`(healthy)` (podman probe + `Published model_version`) and flightctl said Online/UpToDate/Healthy,
yet the rosetta client answered every `/run_policy` goal with `Rejected: already running` — the
goal that was executing at 00:44:45Z when qemu paused the guest was never cancelled or timed out,
so the first part-3 attempt (11:15:56Z) recorded 11 seeds as `goal rejected — episode recorded as
aborted` before it was stopped (log kept as `~/eval-cpu-v2-ft160-c3-INVALID-stale-goal.log`). The
coordinator's own 2188 `Goal rejected` lines during the outage were the same symptom.
`sudo systemctl restart act-inference-128875-flightctl-quadlet-app.target` on the VM (11:21:04Z,
`(healthy)` in 57 s) cleared it; the eval was re-run from seed 1000.
**Decision:** the post-resume checklist for the desktop stand-in is: resume → `virsh
domtime --now` on each guest (verify `date -u` in the guest) → restart the Fleet app target on the
device → confirm `Published model_version` → only then start the coordinator. And `healthcheck.sh`
is blind to a wedged action server: it compares the latched `/flywheel/model_version` with
`$MODEL_VERSION`, which stays true while every goal is refused. Candidates, cheapest first: the
coordinator cancels all goals on the server (`CancelGoal` with a zero goal id) when it sees N
consecutive rejections, or the policy role's health probe fails after the server has rejected
goals for longer than one episode. Not chosen here — the fix owner is the runtime image (F).
**Consequences:** the runbook's contingency section should carry the checklist; the eval JSON's
`steps=1`/`steps=7–11` rows with `task_success=false` are the signature of a rejected goal, not a
policy result; the per-episode `goal_accepted` field already flags them, but `aggregate.success_rate`
still counts them as failures — it should exclude (or separately report) non-accepted episodes.

---

## D064 — Part 2 read-out does not change D053's conclusion

**Date:** 2026-09-09
**Context:** the C3 probe log (`~/spike/cadence-c3-235449.log`, 23:54:49–00:01:49Z, 100/0.5) was
read after the resume: eight full 24.5 s windows, 9347 intervals, **max 312.8 ms, 32 (0.34 %) over
40 ms**, wall rate 46.8–48.0 Hz, p50 20.1 ms, p99 25.5–29.9 ms. C2 at 30/0.95 (D053): 6.21 % over
40 ms, max 335.2 ms, ~36 Hz, p99 ≈ 225 ms. Excluding the first window after the coordinator
start: 25 of 8203 (0.30 %), max 112.4 ms; two windows had no gap over 40 ms.
**Decision:** D053 stands as written — part 2 is still a FAIL on the letter of "no gap
> 40 ms" — with the note that D024's first fallback (chunk toward `n_action_steps`, D058) removed
18× of the over-threshold share and all of the sustained > 200 ms stalls; the residual gaps are
single slow forwards at chunk boundaries. Part 3 remains the arbiter (next entry). Full table in
`docs/eval-records/cpu-spike.md`, "C3 part 2".
**Consequences:** none beyond the standing D053 verdict; see D065 for the criterion-2 wording
question this leaves open.

---

## D065 — Part 3 passes: the CPU stand-in at 100/0.5 scores 18/20 on the D020 seeds; D024 criterion 3 closed, criterion 2 stays a documented residual

**Date:** 2026-09-09
**Context:** `IMAGE=<2ad1fb1c…> MODE=eval MODEL_VERSION=eval-cpu-v2-ft160 tools/host/run-coordinator.sh
20 1000`, 11:15:46–11:40:03Z, guard armed, 266 GB free, loop stopped, device app target restarted
first (stale goal, D063). Result `aggregate.success_rate` 0.90 (18/20, mean cubes 2.85,
hist 0/1/1/18), `served_model_version: act-v2-ft160`, all 20 `goal_accepted: true`; failures at
seeds 1007 (1/3) and 1013 (2/3), both at the 60 s cap. GPU same-seed baseline (30/0.95): 17/20,
failures 1000, 1010, 1018 — all three passed on CPU. JSON copied to
`docs/eval-records/eval-cpu-v2-ft160.json` (commit `c06f391`); write-up in `cpu-spike.md`, "C3
part 3".
**Decision:** D024 criterion 3 is met (≥ 15/20); together with criterion 1 this
qualifies the desktop VM as the stand-in device for the demo on task success. D053's "success rate
is the arbiter" resolves in favour of the stand-in; criterion 2 remains FAIL on the letter
(0.34 % of intervals > 40 ms at 100/0.5, D064) and should be restated in D024 as a monitored
residual rather than a gate, or re-cut to a rate/percentile form (e.g. ≤ 1 % of intervals > 40 ms,
no sustained > 200 ms stall outside the first episode) that the current numbers pass. The wording
decision is the operator's — recorded as an inbox todo — and D053 stands meanwhile. The
non-overlapping failure sets (CPU 1007/1013 vs GPU 1000/1010/1018) are one run's noise at n = 20
on a ~85–90 % policy, not a CPU-over-GPU claim; the GPU baseline also predates D059's 100/0.5
defaults. No further D024 fallback (RTF, vCPU pinning, VFIO) is needed.
**Consequences:** the runbook's Beat 6 may quote a device-served success rate again (18/20 with
the role split), replacing the "lineage proof only" caveat from C2. The loop coordinator is left
stopped after the eval per D062; restart is `IMAGE=<digest> tools/host/run-coordinator.sh` and
requires the guard.

---

## D066 — The promotion edit, as implemented: two-regex Fleet edit plus three-regex manifest-consumer bump (supersedes D022's three-file flip; realises D025)

**Date:** 2026-09-09
**Context:** D025 says the promotion becomes a two-regex edit of `gitops/rhem/fleet-act-inference.yaml`
plus the same-commit `COLLECTOR`/`INCUMBENT` bump in `gitops/flywheel/manifest-consumer.yaml`.
`open_promotion_pr` in `pipeline/act_flywheel_pipeline.py` still edited the three `act-serving` files.
**Decision:** `open_promotion_pr(image_ref, candidate, checkpoint_uri, report_json, github_repo,
gitops_branch, fleet_file, consumer_file, fleet_ui_url)`:
- Fleet, two regexes, each required to match exactly once:
  `(soarm-act-modelcar)@sha256:[0-9a-f]{64}` → the new digest (anchored on the repo name, so the
  runtime-image `soarm-flywheel@sha256:` line is untouched), and
  `^(\s+MODEL_VERSION:\s*)\S+` (multiline) → the candidate. The old `MODEL_VERSION` is captured first
  and goes into the commit message / PR body.
- manifest-consumer, three regexes on the `- {name: X, value: "…"}` lines: `INCUMBENT` and
  `COLLECTOR` → candidate (D025), **and `INCUMBENT_CHECKPOINT` → the pipeline's own `checkpoint_uri`**
  (`s3://episodes-data/checkpoints/<candidate>/pretrained_model.tar.gz`, the trigger step's output).
  Not in D025's text, but it is the same 5e3e87a lesson: without it round B would count the new
  lineage yet fine-tune *from the previous* incumbent's weights — a partial flip on the trigger side.
- Match counts `(1, 1, 3)` are asserted; any other count exits 1 before anything is written.
- One commit (two tree elements), branch `promote/<candidate>`, PR title `Promote <cand> (x% -> y%)`,
  body = PR #1's evidence table + signed modelcar + what the merge does on RHEM + `Fleet: <UI URL>`
  + `Rollback: git revert <sha>` phrased as "revert the merge commit of this PR", with the
  `reclaimPolicy: Retain` no-re-pull note.
- New pipeline params (all defaulted; the runbook's trigger body is unchanged): `fleet_file`,
  `consumer_file`, `fleet_ui_url` (`https://ui.flightctl.apps.sno-flywheel.local/devicemanagement/fleets/act-inference`
  — path taken from the UI bundle's route table), `modelcar_base`.
- Every `gitops/act-serving/*` reference is gone from the pipeline (the compiled YAML has none).
**Hygiene ride-alongs (BUILD-PLAN F items, same file):** `platform.machine()`-derived download URLs
(crane `Linux_x86_64`/`Linux_arm64`, cosign `linux-amd64`/`linux-arm64`); `PY_IMG` pinned to
`ubi9/python-312:9.8-1788919789` and the modelcar base to `ubi9/ubi-micro:9.8-1787778798` (newest 9.8
tags at the time, both manifest lists with amd64+arm64). Not done: `platform` as a list (multi-arch
modelcar) — that is F proper. `gitops/flywheel/manifest-consumer.yaml` still runs `python-312:latest`
(not touched: it is the promotion target file and a hand edit there would collide with the PR).
**Consequences:** offline regex test against the live files confirmed exactly 2 Fleet lines and 3
consumer lines change, runtime-image line intact; `py_compile`; KFP compile; uploaded as DSP version
`v-202609090650-rhem` (`940ef682-9565-4b72-b6a0-10bf56bb2cba`) of pipeline `99ec0aab-…`.

---

## D067 — The candidate `act-v2-ft160-rhem`: a declared re-release of v2's weights, not a new checkpoint

**Date:** 2026-09-09
**Context:** the run must promote a real, already-trained, already-evaluated checkpoint (D023: no
training on the path). The only N=100 paired record that passes the gate is `act-v2-ft160` vs
`upstream-act-teacher` (73% → 86%, 20/7, p=0.0192). But the Fleet already serves `act-v2-ft160`: a
promotion under that name would change the digest only, leave `MODEL_VERSION` untouched, make the
manifest-consumer bump a no-op, and give D2 nothing observable in `Published model_version:`.
No round-B checkpoint exists (host `~/flywheel-data/train/` holds only v2 and the Sept-4 ladder rungs;
`act-v2-ft160`'s `model.safetensors` md5 `ba5f5125…` equals `ft-ladder-160ep`'s — the promoted v2 was
itself the ladder-160 rung relabelled).
**Decision:** candidate `act-v2-ft160-rhem`, incumbent `upstream-act-teacher`, collector
`upstream-act-teacher`, `incumbent_checkpoint: hf` — D023's exact parameters with the candidate
renamed. Staged on the host without copying weights: `train/act-v2-ft160-rhem -> act-v2-ft160`
(symlink) and `eval/eval-act-v2-ft160-rhem.json` = a copy of `eval-act-v2-ft160.json` carrying
`relabel_of` / `relabel_note` fields that say so. The runner's idempotent path (checkpoint dir exists;
record with `seed_base 1000`, `episodes 100` exists) reuses both. The name says what it is — v2's
weights, RHEM release — and does not claim a v3.
**Consequences:** the PR body says "`act-v2-ft160-rhem` replaces `upstream-act-teacher`" (the gate's
incumbent) while the Fleet line it edits replaces `act-v2-ft160` — the commit message carries the
real `MODEL_VERSION` transition. After the merge, episodes are stamped `act-v2-ft160-rhem`, the
consumer counts that lineage from 0, and the round-B candidate will be named
`act-v2-ft160-rhem-ft160-<ts>` by the consumer. The three `-rhem` staging artefacts on the host and the
MinIO `checkpoints/act-v2-ft160-rhem/` copy are the price; a real v3 (round B) replaces all of this.
**Rejected:** `act-v3-…` (claims a generation that was never trained); re-promoting `act-v2-ft160`
(one-line diff, no observable version change); incumbent `act-v2-ft160` (v2 vs itself → net 0 → the
gate fails, correctly — there is no documented override and none should be added).

---

## D068 — First RHEM promotion run succeeds end to end: gate PASS, signed modelcar, PR #2 open, verified pull under enforcing policy

**Date:** 2026-09-09
**Context:** two operational blockers surfaced ahead of the run. The host runner was not resident
(it died on `KafkaConnectionError 113 EHOSTUNREACH`, consistent with the SNO reboot during the
4.18→4.19 upgrade); restarted with the runbook's command. Its `loop_park()` only looks for a docker
container named exactly `act-inference` (stopped since the C2 cut-over), so the D023 run never
touches `act-coordinator` — the loop stayed stopped throughout (worth a `Restart=always` user unit on
the host; the runbook "If it breaks" section already lists the symptom). `flightctl` lives in
`~/.local/bin` on the desktop and is not on the non-interactive ssh PATH.
**Decision (run record):** DSP run `192f3ec5-c0f2-459d-8e84-6e8e32e90b35` (pipeline version
`v-202609090650-rhem`) SUCCEEDED on the first attempt, 2026-09-09 06:53–06:57 CDT: the runner reused
the checkpoint plus both N=100 records (25 s), gate PASS (0.73 → 0.86, 20/7, p=0.0192), `crane append`
→ `quay.io/jary/soarm-act-modelcar@sha256:1375d0bcc2c7c81867365b55a08bdd5fa03bf31d20cc7bde04044fdcf1a0784e`,
cosign → **Rekor index 4** (tree size 5), commit `529912e`, **PR #2**
https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/2 with exactly the 2 + 3 lines. `cosign
verify` with `SIGSTORE_REKOR_PUBLIC_KEY` (no `--insecure-ignore-tlog`) clean; the VM pulled the digest
under its enforcing `policy.json` ("Storing signatures"). Full record:
`docs/eval-records/promotion-2.md`. Not merged — Gate 3 is the operator's.
**Consequences:** follow-ups for inbox, not decisions: host runner as a `Restart=always` user unit;
`manifest-consumer.yaml` still on `python-312:latest` (F, after PR #2 merges to avoid a conflict);
`platform` as a list for a multi-arch modelcar (F); the three `-rhem` staging artefacts on the host
(`train/act-v2-ft160-rhem` symlink, `eval/eval-act-v2-ft160-rhem.json`, MinIO
`checkpoints/act-v2-ft160-rhem/`) go away with the first real round-B candidate.

---

## D069 — RHEM rollout timing for a promotion is ~2.5 min merge-to-Healthy; the ResourceSync poll is the only wait

**Date:** 2026-09-09
**Context:** the demo (Beat 5/6) needs "how long after the merge does the device switch". First
measured on PR #2 (merged 12:04:27Z, merge commit `4ce6a8a5`).
**Record:** ResourceSync `rhem-fleets` detected the merge commit at 12:06:01Z (+1:34 — the RS had
polled 26 s *before* the merge, at 12:04:01Z, so this is one full RS poll interval of ~2 min, the
worst case). Everything downstream is seconds: Fleet `spec.template` updated, TemplateVersion v5
created and `FleetRolloutStarted` in the same second (12:06:01Z); batch dispatched + device spec
updated 12:06:10Z (+9 s); agent `New spec version received: 4 -> 5` 12:06:10.129Z; container
died/removed, image volume removed + re-created, container created/started 12:06:20.4–20.98Z
(+10 s); `DeviceContentUpToDate` 12:06:20.99Z; `Published model_version: act-v2-ft160-rhem`
12:06:37.8Z (+17 s, ROS launch + policy server); `FleetRolloutCompleted` 12:06:40Z;
`DeviceApplicationHealthy` 12:07:09Z (+49 s from start; the 240 s `HealthStartPeriod` is a ceiling,
not the actual). **Merge -> device serving the new version: 2 min 10 s; merge -> Healthy: 2 min 42 s.**
**Consequence for the runbook:** narrate "about two minutes"; the visible waits are the RS poll and
the health start period. If a tighter demo is wanted, the RS poll interval is the lever (flightctl
`ResourceSync` reconciles on a fixed ~2 min cadence in 1.3.0; not changed here).

---

## D070 — Fleet rollout success is counted on `UpToDate`, not on application health; watch the device's `applicationsSummary`, not the Fleet condition, for "serving"

**Date:** 2026-09-09
**Context:** `FleetRolloutBatchCompleted … 100% success` and `FleetRolloutCompleted` fired at
12:06:40Z, 29 s *before* `DeviceApplicationHealthy` (12:07:09Z), while the device still reported
`applicationsSummary: Degraded` (`Not started: act-inference`, then `Preparing 0/1`). The Fleet's
`successThreshold: 100%` is evaluated on the device's `updated.status` reaching `UpToDate` (spec
applied), not on the application's health. `fleet-controller/batchNumber` stayed at `3` across the
whole v5 rollout (it is the *sequence position*, not a monotonic counter) and
`deployingTemplateVersion` appeared alongside `templateVersion: v5`.
**Decision:** the demo and runbook treat `flightctl get device … applicationsSummary: Healthy` (or the
`DeviceApplicationHealthy` event) as the "serving" signal, and the Fleet's `RolloutInProgress:
Inactive` only as "spec delivered". For a multi-device batch sequence this means a batch could
advance to the next site with an unhealthy app in the previous one; on the Fury port, consider
whether `HealthStartPeriod` should be part of `defaultUpdateTimeout` reasoning, and revisit if
flightctl gains an application-health gate on `successThreshold`.

---

## D071 — Retain works as documented: the promotion rollout pulled nothing, and both digests stay in device storage for the rollback

**Date:** 2026-09-09
**Record:** during the v4 -> v5 rollout the device emitted no `image pull` event for the modelcar and
the agent journal has no `Copying blob`/`pulling` lines. The only pull event is the runtime image's
local resolve (`soarm-flywheel@sha256:2ad1fb1c…`, 12:06:20.868Z, 17 ms before `container create`) —
podman emits `image pull` on a local hit too, so "no pull event" is the wrong test; the test is *no
modelcar pull event and no `Copying blob`*. The agent removed and re-created the quadlet `.volume`
(`volume remove` / `volume create systemd-act-inference-128875-models`, 200 ms apart) rather than
editing it in place; the container keeps its name (`act-inference-128875-act-inference`) and gets a
new id — the watch list's "new container name" expectation was wrong. `podman images --digests`
after the rollout lists both `bdb513ca…` and `1375d0bc…` (230 MB each).
**Consequence:** the rollback rehearsal is recorded in commit `e165837` (D2 observation, rollback PR
opened) and staged as **PR #3**, https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/3 (commit
`ff39942`, reverting merge commit `4ce6a8a5`), which states the Retain evidence verbatim and expects
the same no-pull rollout for `bdb513ca…`. Device disk: each retained modelcar is 230 MB; a prune
policy for retired digests is a Fury-window question, not a demo blocker.

---

## D072 — Argo `flywheel` auto-syncs the promotion within its 3-minute poll; no refresh needed; the consumer restart is the lineage cut

**Date:** 2026-09-09
**Record:** Argo `flywheel` (automated, `prune: false`, `selfHeal: true`) started its sync on
`4ce6a8a5` at 12:07:38Z — 3 min 11 s after the merge, one default repo-poll interval after its
previous reconcile (12:03:13Z). No `argocd.argoproj.io/refresh` annotation was needed. The
`manifest-consumer` Deployment rolled a new ReplicaSet (`77fc96cbfb`, pod started 12:07:40Z, 0
restarts) with `INCUMBENT=COLLECTOR=act-v2-ft160-rhem` and
`INCUMBENT_CHECKPOINT=s3://episodes-data/checkpoints/act-v2-ft160-rhem/…`, logging
`[consumer] threshold=160 cooldown=3600s pipeline=set`. The device switched 1 min 18 s before the
consumer did; any curated episode in that window would be stamped `-rhem` and land on the topic
while the old consumer still filtered for `act-v2-ft160` (the loop was stopped here, so none did).
**Decision:** acceptable for the demo — the consumer's `pending` count is in-memory and restarts at
0 on every pod restart anyway (D022 shape). Note for the runbook: the consumer prints nothing per
manifest; "count advances" is evidenced by the consumer group offset (lag 0 on all three partitions
after the first `-rhem` manifest at p0@481, 12:11:53Z), not by a log line. A per-manifest
`[consumer] pending=<n> collector=<c>` log line would make Beat 6 narratable — one-line change to
`consumer.py`, do it with the `python-312:latest` pin in F.

---

## D073 — Loop lineage re-stamp after promotion: ~1 min to the first rejected manifest, ~2.5 min to the first curated one; the dashboard's `model_version` field is not the lineage

**Date:** 2026-09-09
**Record:** loop coordinator started 12:09:17Z with `MODEL_VERSION=act-v2-ft160-rhem` (the script's
default is still `act-v2-ft160`; the value must be passed explicitly after a promotion — it must equal
the device's `MODEL_VERSION`, D057). `Observed model_version: act-v2-ft160-rhem` on the first
episode; first `episodes-rejected/act-v2-ft160-rhem/` object 12:10:04Z (a `rollout-error(truncated)`
reject from the first cold-start episode), first `episodes-curated/act-v2-ft160-rhem/` object
12:11:53Z (`verdict: pass`, 3/3 cubes, `dataset_path: bags/1788955880_371180261`). In the 12-minute
window: curated 0 -> 1, rejected 0 -> 13, `act-v2-ft160` prefixes unchanged (214 / 104). The
dashboard `/api/status` `model_version` field reads `soarm-act-v2` (a static label from the Phase 3
UI), `counts.curated` reads 0, and `trigger.triggered: true` is stale from the 160-trigger of PR #1 —
none of these reflect the new lineage; the per-episode `log[]` entries carry `model_version: null`.
**Decision:** `run-coordinator.sh` default `MODEL_VERSION` should follow the Fleet (read
`gitops/rhem/fleet-act-inference.yaml` at start, or make the variable required like `IMAGE`); the
dashboard `model_version`/`counts` wiring is a Phase 4 G runbook item (the demo screen for lineage is
MinIO prefixes + `flightctl console`, not the dashboard) — inbox, not a blocker.

---

## D074 — Rollback via `git revert` + merge is symmetric with promotion: 1:28 to serving, 1:59 to Healthy, no pull

**Date:** 2026-09-09
**Context:** Phase 4.5 D3 — the rollback rehearsal staged as PR #3 (D071) actually run.
**Record:** PR #3 (revert of merge `4ce6a8a5`) merged 12:35:10Z (merge commit `1eab0824`).
ResourceSync detected it at +0:51 (the merge landed 51 s before a poll — best case, vs D069's 26 s
after one, worst case), Fleet v5 -> v6 and device rv 5 -> 6 at +1:00, container recreated at +1:10,
`Published model_version: act-v2-ft160` at +1:28 (12:36:37.9Z), `DeviceApplicationHealthy` at +1:59,
Argo `flywheel` synced the consumer back at +2:16 (pod on the pre-PR #2 ReplicaSet `6bb589887b` —
identical pod template, so Argo/k8s reused it rather than creating a new one). Every hop after
`ResourceSyncCommitDetected` matched D069's promotion run to the second; the spread between the two
runs is entirely the poll phase. Agent journal: 0 `Copying blob`/`pulling` lines; `podman events`:
only the runtime image's local-resolve pull event; both modelcar digests still in storage — D069/D071
hold for rollback in the reverse direction. D070 held again: `FleetRolloutCompleted` (+1:30) preceded
`Healthy` (+1:59).
**Decision:** the runbook's rollback/reset procedure is `git revert -m 1 <merge>` + push, then watch
`flightctl get device` — no hand step, no refresh. Timing to quote: "under two minutes, worst case
~3 with the poll."

---

## D075 — Deleting the `act-serving` Application did not cascade; the retirement deleted its resources explicitly (D025 executed)

**Date:** 2026-09-09
**Context:** with the RHEM promotion/rollback path proven (D068–D074), the desktop's original
swap-agent-based serving path (`act-serving`) is retired — this is D025 ("retire the pre-RHEM serving
path once RHEM promotion is proven") executed.
**Record:** the `act-serving` Argo Application was bootstrapped without
`resources-finalizer.argocd.argoproj.io` (and `prune: false`), so `oc delete applications.argoproj.io
act-serving` orphaned rather than cascaded: `Service/act-policy`, `Deployment/act-policy` and
`Deployment/act-policy-green` in ns `flywheel` were left behind (verified via `status.resources`
before the delete) and had to be deleted by name. Nothing else in `flywheel` changed (13 Deployments
/ 12 Services before and after, minus exactly those three). Git: `gitops/act-serving/`,
`src/swap-agent/`, `argocd/act-serving-app.yaml` removed in commit `9190e2a`. Host: `docker rm
act-inference`; `~/swap_agent.py`, `~/bag_watchdog.sh` moved to `~/retired-2026-09-09/` rather than
deleted.
**Decision:** D025 is now executed — the pre-RHEM serving path is retired. The other bootstrap apps
(`argocd/*-app.yaml`) should be checked for the same missing finalizer before any of them is ever
deleted (F ride-along); an Argo app without `resources-finalizer.argocd.argoproj.io` is not a
delete-to-clean-up object, and this project has now hit that gap once in practice.

---

## D076 — Retirement side effects: dashboard badge regression, stopped `pre-` containers, tlog-bypass narrative cleanup, and the runbook edits that no longer bypass the transparency log

**Date:** 2026-09-09
**Context:** fallout from D075's retirement, plus the runbook edits made to reflect D074's rollback
and D071/D069's no-pull evidence.
**Record:**
- **Dashboard badge regression:** `gitops/flywheel/dashboard.yaml` `get_model_version()` read the
  deleted `act-policy` Service selector and now always falls back to `soarm-act-v1` on the 404 (the
  RBAC comment at line 18 is stale too). D073 already established the field is not the lineage; not
  edited here (live Argo-synced ConfigMap, not on the D3 file list) — the runbook's *Known screen
  artifacts* and *Failure recovery* rows now say so instead, and the real lineage screen is
  `flightctl get device/<name> -o json | jq .status.applications` or MinIO prefixes.
- **Stopped `pre-` containers:** 15 stopped `act-inference-pre-act-v2-ft160-<ts>` containers remain on
  the desktop (the swap agent's per-pass backups, all `Exited (137)`, image `act-inference:latest`).
  Left as a prune candidate, not cleaned up here.
- **`--insecure-ignore-tlog` grep:** clean of code, config, and procedure. Two prose lines remain in
  `docs/eval-records/interim-runtime-image.md` (67, 98) that record the flag's *absence* ("no
  `--insecure-ignore-tlog`", "is *not* the fix") — historical evidence, left as-is; not on the D3 file
  list.
- **Runbook edits:** `docs/DEMO_RUNBOOK.md` now runs `cosign verify` against the transparency log —
  `SIGSTORE_REKOR_PUBLIC_KEY=~/rekor-live.pub ~/bin/cosign verify --key ~/cosign/cosign.pub
  --rekor-url http://localhost:8090 <image@digest>` (no `--insecure-ignore-tlog`) — and the Beat 6
  device-status commands are the `flightctl get device/<id> -o json | jq …` forms (`.status.
  applicationsSummary`, `.status.config.renderedVersion`, `.status.updated.status`). The rollback
  procedure is `git revert -m 1 --no-edit <merge commit of the promotion PR>` + push, watch
  `flightctl get device` — no hand step, no refresh (D074).
- **Runbook narrative not yet updated:** item G still owns the places that describe the retired path
  by name — § *Desktop vs. target* (swap agent, `gitops/act-serving/`), the *On GB10 / GB300 Fury*
  table rows for the `act-policy` Deployment and swap agent, and the Beat 5/6 *Say* text; D3 replaced
  only the procedures that could no longer run (state check, verify line, Beat 6 commands,
  reset-to-start, Part 5, Q&A line, two failure-recovery rows). `gitops/flywheel/dashboard.yaml` and
  `docs/demo-kit/*` (historical evidence) still mention `act-policy`/`swap_agent` by name on purpose —
  those are frozen records, not live procedure.
**Decision:** none of these are demo blockers. Dashboard badge fix (read the Fleet's `MODEL_VERSION`
via the flightctl API, or drop the badge) and the remaining narrative rewrite are item G's call;
container prune and the two historical prose lines are inbox items, not scheduled here.

---

## D077 — Every Argo app is in git with `prune: true`; hand-created Secrets are untracked, not ignored

**Date:** 2026-09-09
**Context:** D026 row 5. `flywheel`, `minio`, `observability` existed only on the cluster; every
branch-tracking app ran `prune: false`; `observability` tracked `main`.
**Record:** evidence before flipping (per app, `status.resources[?(@.requiresPruning==true)]`):
operators, operators-config, storage, flywheel, minio, observability — all Synced, zero prune
candidates, so the flip deleted nothing; no app was withheld. Committed in `85e9176`:
`argocd/{flywheel,minio,observability}-app.yaml` from `oc get application` (spec only, same shape
as `operators-app.yaml`); `observability` now tracks `desktop-gpu-split` like every other app —
`gitops/observability` is byte-identical on `main` and the branch, so no behaviour change today,
and later branch edits (OTel, post-ROSCon) will actually reach the cluster. `prune: true` applied
by `oc apply -f` to operators, operators-config, storage, flywheel, minio, observability (rhem
already; act-serving deleted by D3 the same day); all 8 apps Synced/Healthy. App count (8) equals
`argocd/*-app.yaml` count: `flywheel, minio, observability, operators, operators-config, rhem,
storage, tekton` — 8 files. `4a03801` carries the companion doc fixes (`gitops/operators/README.md`
RHEM + Model Registry rows, KServe-unused note; `PROJECT-BRIEF.md` "AMQ Streams" wording, D026 row
7).
**Decision:** hand-created Secrets are *untracked* (no `argocd.argoproj.io/tracking-id` annotation)
rather than listed in `ignoreDifferences`. With annotation tracking Argo only prunes what it
stamped, so an untracked Secret is invisible to sync and prune; `ignoreDifferences` would still
leave it in the app's resource tree and subject to prune once it left git. `argocd/README.md`
carries the bootstrap order and a hand-created Secrets table (names, namespaces, keys, consumers —
never values).
**Consequences:** drift found on the way: the Perses/Tempo CRDs come from the Cluster Observability
Operator v1.5.2 and tempo-operator v0.22.0-1, both installed by hand and absent from
`gitops/operators/` — recorded in the README cell; a `gitops/operators/` Subscription pair is the
fix (not done — outside F's scope, and a bad apply would re-install COO under the running Perses
instance).

---

## D078 — MinIO root credentials left git by removing the manifests and untracking the live Secrets; values were not rotated

**Date:** 2026-09-09
**Context:** D026 row 13. `gitops/flywheel/hub-credentials.yaml` and the `minio-credentials` Secret
inside `gitops/minio/minio.yaml` held `minioadmin`/`minioadmin`; `assemble_dataset.py` and
`host_runner.py` defaulted to the same.
**Record (ordering mattered):**
1. Push the git removal first, with `prune: false` still live → Argo marks both Secrets
   `requiresPruning` but deletes nothing (observed: `OutOfSync … prune-candidates:
   Secret/hub-credentials`). Hand-applying first would have raced `selfHeal`, which re-applies from
   git and re-stamps the annotation.
2. Untrack the live Secrets by removing `argocd.argoproj.io/tracking-id` **and**
   `kubectl.kubernetes.io/last-applied-configuration` (the latter carried the plaintext `stringData`
   from the original apply). Metadata-only: the Secret values were never rewritten. `sha256` of
   `~/.minio-env` `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` equals the live `hub-credentials` and
   `minio-credentials` values, so "hand-created from `~/.minio-env`" and "live" are the same bytes;
   the README's `oc create secret … --dry-run=client -o yaml | oc apply -f -` pair is the
   fresh-cluster path.
3. Only then `prune: true`. After sync: both apps Synced, zero prune candidates, Secrets present
   with their original `creationTimestamp`, MinIO `/minio/health/live` 200, sync-agent Ready.
   Committed in `85e9176` (`gitops/flywheel/hub-credentials.yaml` removed; `gitops/minio/minio.yaml`
   and `gitops/minio/minio-readonly-user.yaml` edited; `argocd/README.md` and
   `gitops/flywheel/README.md` document the hand-created Secrets).
**Not rotated:** the values are still the historical defaults. Rotating means restarting MinIO, the
sync-agent, rejected-mirror, the DSPA, the host runner and the resident containers — that touches
the loop in the pre-Fury window. Proposed follow-up: rotate at the Fury rebuild (Phase 4.5 G), where
every consumer is recreated anyway.
**Host side:** `host_runner.py` and `assemble_dataset.py --from-minio` now refuse to start without
`MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` (the `prune_bags.py` pattern); `in_image()` and
`tools/host/assemble_all.sh` forward them — committed in `d240ec6`. The resident runner and the
baked `act-inference:latest` image are unaffected until restart/rebuild; `docs/DEMO_RUNBOOK.md`
line ~451's restart one-liner needs `set -a; source ~/.minio-env; set +a;` prepended (not edited —
the file was mid-edit by D3).
**Verification:** `grep -rn minioadmin` across the repo returns exactly two hits, both prose:
`DECISIONS.md:1284` (the D026 drift-table row recording the historical state) and
`BUILD-PLAN.md:441` (the item-F exit-criteria wording "no `minioadmin` in git") — no live
credential, manifest, or default carries the value.

---

## D079 — Multi-arch modelcar: one OCI index per candidate, per-arch `crane append` + `crane index append`, signed `--recursive`

**Date:** 2026-09-09
**Context:** D026 row 12; D066 left `platform` as a single string.
**Record:** pipeline param `platform: List[str] = ["linux/amd64", "linux/arm64"]`.
`package_modelcar` runs `crane append --platform <p> -b ubi-micro -f layer.tar -t
<repo>:<candidate>-<arch>` per entry (the `--platform` global flag picks that arch's `ubi-micro`
from the manifest list), then `crane index append -m … -m … -t <repo>:<candidate>` and returns the
index `repo@sha256:…`. Verified against a throwaway `crane registry serve` on 127.0.0.1 with the
pinned `ubi-micro`: result is `application/vnd.oci.image.index.v1+json` with `linux/amd64` and
`linux/arm64/v8`; the model layer digest is shared, only the base layer differs. Per-arch tags
(`<candidate>-amd64`, `<candidate>-arm64`) remain in quay as a side effect — acceptable, they are
what the index points at. `sign_modelcar` adds `--recursive`: containers/image resolves the index
to one instance and looks up the sigstore attachment by that **instance** digest, so signing only
the index would fail the device's `policy.json`. The Fleet keeps pinning the index digest
(promotion regex unchanged). Committed in `d240ec6` (`pipeline/act_flywheel_pipeline.py`,
`pipeline/act_flywheel_pipeline.yaml`, `device/README.md`).
**Decision:** ship as described; the Fleet's promotion regex is unchanged since it still pins a
single digest (the index).
**Unverified until the next DSP run** (none run — a promotion run opens a PR): the recursive
signature under the device's Rekor-enforcing policy, and the Rekor entries (expect one per instance
plus the index).

---

## D080 — `COSIGN_PASSWORD` comes from the Secret; the key stays unencrypted for now

**Date:** 2026-09-09
**Context:** D026 row 13 (signing-hygiene half); companion to D079 in the same `d240ec6` commit.
**Record:** `sign_modelcar` no longer hardcodes `COSIGN_PASSWORD=""`: `use_secret_as_env(
cosign-signing-key, {"cosign.password": "COSIGN_PASSWORD"}, optional=True)` — same key name as the
Tekton agent's `cosign-signing` Secret. If the key is absent the component logs it and assumes an
unencrypted key.
**Blocked:** adding the key to the live Secret was blocked by the session's write classifier; the
operator runs `oc patch secret cosign-signing-key -n flywheel --type merge -p
'{"stringData":{"cosign.password":""}}'` (empty because the current key has no passphrase — proven
by every signing run to date) as a manual follow-up.
**Decision:** ship the optional secretKeyRef now; re-keying with a passphrase would change
`cosign.pub` on the device's `policy.json` and is a Fury-rebuild item.

---

## D081 — Model Registry CR shape on RHOAI 2.25: `v1beta1`, `mysql`/MariaDB backend, OAuth-proxy Route

**Date:** 2026-09-09
**Context:** Phase 4.5 E1 (D027) — the durable version → digest/dataset/eval/Rekor/PR join.
**Record:** `modelregistries.modelregistry.opendatahub.io` on RHOAI 2.25.11 serves `v1alpha1` +
**`v1beta1`** (storage). `v1beta1` has no `istio` block; auth is `spec.oauthProxy`
(`ose-oauth-proxy` sidecar on 8443 with an OpenShift serving cert, Route `<name>-rest.apps.<domain>`
created by default, `--openshift-delegate-urls` → SAR `get services/<name>` in the registries ns).
`rest.serviceRoute` stays `disabled` (plain 8080 never leaves the pod). `gitops/operators-config/dsc.yaml`:
`modelregistry: {managementState: Managed, registriesNamespace: rhoai-model-registries}` — the
namespace is operator-owned, not in git (labels `platform.opendatahub.io/part-of: modelregistry`);
`ModelRegistryReady=True` ~10 s after the Argo sync. Committed `e368afe`. CR `flywheel` in
`rhoai-model-registries` (`gitops/operators-config/model-registry.yaml`): `grpc: {}`, `rest: {}`,
`oauthProxy: {serviceRoute: enabled}`, `mysql: {host: model-registry-db.rhoai-model-registries.svc,
port: 3306, database: model_registry, username: mlmduser, passwordSecret: {name: model-registry-db,
key: database-password}}`. MariaDB `registry.redhat.io/rhel9/mariadb-1011:9.8-1788409987` (pinned,
multi-arch), PVC `model-registry-db` 10Gi `local-path`; password in the hand-created Secret
`model-registry-db` (`tools/hub/create-model-registry-db-secret.sh`, listed in `argocd/README.md`).
Service/Route live at `flywheel.rhoai-model-registries.svc:8443` (`https-api`, serving cert) and
`https://flywheel-rest.apps.sno-flywheel.local` (reencrypt, new `/etc/hosts` entry, `10.0.0.49`); REST
base `/api/model_registry/v1alpha3` (`/v1` → 404 on this server). Conditions after sync:
`Available=True (DeploymentAvailable)`, `OAuthProxyAvailable=True`, `Progressing=False`. Committed
`067564b`. Argo: `operators-config` app, sync waves 2 (DB) / 3 (CR, RoleBinding, NetworkPolicy) after
the DSC (wave 1).
**Decision:** ship the `v1beta1` CR with the MariaDB/mysql backend and OAuth-proxy Route now; the
alternative istio-authorizer path doesn't exist on `v1beta1` so there was no choice to make.
**Consequences:** enabling the component also deploys RHOAI's **model catalog** (`model-catalog`
Deployment + Route `model-catalog.apps.sno-flywheel.local`) as a side effect — not configured, not
used. First bootstrap on a fresh cluster: push the DSC change, wait for `ModelRegistryReady`, run the
Secret script, then the rest syncs.

---

## D082 — Pipeline auth path to the registry: pod SA token through the OAuth proxy, in-cluster Service, service CA

**Date:** 2026-09-09
**Context:** Phase 4.5 E1 (D027), companion to D081.
**Record:** `register_model`/`record_pr_url` call `https://flywheel.rhoai-model-registries.svc:8443`
(pipeline param `model_registry_url`) with the pod's own token
(`/var/run/secrets/kubernetes.io/serviceaccount/token`, SA `pipeline-runner-dspa`) and
`custom_ca=.../service-ca.crt`. The Route is not resolvable in-cluster, so the Service is the only
option. Auth chains through the OAuth proxy's `--openshift-delegate-urls` → SAR `get
services/flywheel`; the operator creates Role `registry-user-flywheel` (+ Group binding
`flywheel-users`), and git adds RoleBinding `registry-user-flywheel-pipeline` binding that Role to SA
`flywheel/pipeline-runner-dspa` (`gitops/operators-config/model-registry.yaml`, committed `067564b`).
**Forced:** the operator's NetworkPolicy `flywheel-https-route` admits 8443 only from the ingress
router; a second policy `flywheel-https-pipeline` (`gitops/operators-config/model-registry.yaml`,
committed `d26290c`) admits the `flywheel` namespace — without it the DSP pods have no path to the
registry at all.
**Verification:** proven read-only from a pod in `flywheel` before the proof run: `GET
…/registered_models` → 403 without a token, 200 with the runner SA token (proxy response header
`Gap-Auth: system:serviceaccount:flywheel:pipeline-runner-dspa`); from the desktop via the route with
the same token: 200 `{"items":[]…}`, no token → 403.
**Decision:** Service + SA token + service CA, not the Route and not a long-lived credential —
matches the pattern already used for the RHEM hub and keeps the registry's auth surface identical to
every other in-cluster RHOAI consumer.

---

## D083 — Ordering (a): register before the PR, `record_pr_url` after; client pinned to `model-registry==0.3.11`

**Date:** 2026-09-09
**Context:** Phase 4.5 E1 (D027); D027's "between sign and PR" ordering choice, now implemented.
**Record:** pipeline is `trigger+wait -> gate -> package -> sign -> register_model ->
open_promotion_pr -> record_pr_url`. `register_model` runs after `sign_modelcar`, `open_promotion_pr`
runs `.after(reg)`, and `record_pr_url` updates the version's `pr_url` custom property once the PR
exists. A version with `pr_url: ""` therefore reads as "signed and registered, PR not opened" —
visible, not hidden. `register_model(model_name="soarm-act", image_ref=<index@digest>, candidate,
checkpoint_uri, dataset_uri, eval_report_uri, report_json, rekor_index, rekor_url, run_id,
model_registry_url)` builds `ModelRegistry(server, 8443, author="act-flywheel-pipeline",
user_token=<pod SA token>, custom_ca=<service-ca.crt>)` and calls `.register_model(name, uri,
model_format_name="lerobot-act", model_format_version="1", version=candidate, metadata={...})`.
Custom properties: `dataset_uri`, `checkpoint_uri`, `eval_report_uri`, `incumbent`,
`incumbent_success_rate`, `candidate_success_rate`, `n_paired`, `fixed`, `broken`, `net`,
`sign_test_p`, `gate_rule`, `verdict`, `rekor_index`, `rekor_url`, `pr_url`, `dsp_run_id`. Artifact
`uri` = the signed index digest. Ride-along fixes in the same commit: `sign_modelcar` now returns
`(image_ref, rekor_index)` parsed from cosign's `tlog entry created with index: N` lines (first line
= the index's own entry; `-1` without Rekor); `trigger_and_wait` gained a third output `dataset_uri`
(the host runner already reports it; `"reused"` on the D023 path). Committed `599bd97`
(`pipeline/act_flywheel_pipeline.py`, `pipeline/act_flywheel_pipeline.yaml`).
**Client pin:** the RHOAI 2.25 server serves `/api/model_registry/v1alpha3` only (`/v1` → 404).
Client `0.3.12`, `0.3.15`, `0.3.16` target `/api/model_registry/v1` (`0.3.14` oddly reverts); `0.3.11`
is the last of the consistent `v1alpha3` line — verified in-cluster (`0.3.16` → `404 page not found`;
`0.3.11` → OK). Versions uploaded while landing this: `v-202609090836-registry` (0.3.16, superseded),
`v-202609090839-registry` (`e372c403-…`, env-secret path, superseded), **`v-202609090850-registry`
(`29400621-5531-429b-80b5-8e24f613f240`)** — the version the proof run uses.
**Decision:** ship ordering (a) and the `0.3.11` pin now; bump the client pin only when the server
moves off `v1alpha3`, not before.

---

## D084 — Forced: rhods-operator CPU request 500m → 100m per replica (node was 99% CPU-requested)

**Date:** 2026-09-09
**Context:** Phase 4.5 E1; the MariaDB pod for D081 could not schedule.
**Record:** the node had 15454m/15500m CPU *requested* (12% real use); the rhods-operator CSV runs 3
replicas × 500m request. `gitops/operators/rhoai-operator.yaml` `spec.config.resources` now sets
`requests: {cpu: 100m, memory: 256Mi}`, `limits: {cpu: 500m, memory: 4Gi}` — limits unchanged, only
the request is cut. After the override: 68% requested; the DB pod scheduled. Committed `3e05768`.
**Decision:** apply the OLM `SubscriptionConfig.resources` override rather than resizing the node or
evicting something else — it's the only lever that changes *requested* without changing *real* usage.
**Consequences:** other 250–500m requesters are candidates for the same treatment if the Fury SNO node
turns out smaller: `flightctl-db` (512m), `flightctl-alertmanager` (500m), six `openshift-gitops`
pods (250m each) — not done, no pressure to do it yet.

---

## D085 — Forced: `sign_modelcar` reads `cosign.password` from the Secret volume, not an optional env

**Date:** 2026-09-09
**Context:** Phase 4.5 E1; D080 shipped `use_secret_as_env(cosign-signing-key,
{"cosign.password": "COSIGN_PASSWORD"}, optional=True)`.
**Record:** `use_secret_as_env(..., optional=True)` compiles to `optional: true` in the YAML, but the
DSP launcher on RHOAI 2.25 does not honour `secretKeyRef.optional`, and the live
`cosign-signing-key` Secret has no `cosign.password` key (D080's README lists it as a possible key;
D1 signed with an empty password because the key is unencrypted). First execution of that code path →
`CreateContainerConfigError`, run `f1785799` died at `sign-modelcar` and was terminated.
**Fix:** the Secret is already mounted at `/etc/cosign`; the step now reads `cosign.password` from
`/etc/cosign/cosign.password` when present, else falls back to `""`. D080's contract (password
sourced from the Secret's key, not hardcoded) holds; only the mechanism changed. Committed `d12f5de`
(`pipeline/act_flywheel_pipeline.py`, `pipeline/act_flywheel_pipeline.yaml`).
**Blocked:** copying the key from Tekton's `cosign-signing` Secret into `cosign-signing-key` (so the
`cosign.password` key actually exists) was blocked by the session's write classifier — left to the
operator if they want the two Secrets identical; not required for the fallback to work.

---

## D086 — E1 status: registry live under GitOps end to end; proof run parked on a Multus stale-token fault, exit criterion NOT yet met

**Date:** 2026-09-09
**Context:** Phase 4.5 E1 exit criterion (BUILD-PLAN.md item E) — "the registry shows the promoted
version with digest + metrics for at least one candidate."
**Record:** `docs/eval-records/model-registry.md` (committed `e12c5a6`, alongside `gitops/operators/
README.md` rows made true — D081's CR shape, D082's auth/network path, D083's client pin and
metadata now described as live rather than planned) is the authoritative record; **State:** registry
live on the hub under GitOps; pipeline `register_model` + `record_pr_url` written, compiled, uploaded
(`v-202609090850-registry`); the proof run **`3afee844-007a-4c97-ab70-5f9cc38b9853`** (created
13:50:42Z, D1's parameters — `candidate=act-v2-ft160-rhem incumbent=upstream-act-teacher
collector=upstream-act-teacher incumbent_checkpoint=hf`) is **parked on a cluster fault**: since
**13:45:03Z** every new pod sandbox in `flywheel` fails `Multus: […]: error waiting for pod:
Unauthorized` (586 such lines in the `multus-4rzbb` log in 40 min; the `rejected-mirror` CronJob pods
fail identically) — a stale Multus API token. The registered-version JSON and PR #4 sections in the
eval record are explicitly left as "pending the run."
**Blocked / for the operator:**
- **Multus repair:** `oc delete pod -n openshift-multus -l app=multus` (daemonset recreates it in
  ~20 s), then confirm `FailedCreatePodSandBox` events stop in `flywheel`. Blocked by the session's
  write classifier — an operator action, not resubmission of the run (kubelet retries the sandbox and
  Argo continues on its own once Multus is healthy).
- **303 pods on a 250-max node:** 114 Succeeded + 13 Failed pods linger (KFP driver/executor pods from
  every run) — not the sandbox cause, but a DSPA/Argo TTL (`spec.apiServer.…`/`workflowTTL`) or a
  periodic `oc delete pod --field-selector=status.phase=Succeeded -n flywheel` belongs on the inbox.
**Decision:** do not claim the E1 exit criterion met on the strength of the CR/auth/pipeline being
live — the criterion requires a registry entry for an actual promoted candidate, and none exists
until `3afee844-…` (or a resubmission) completes past the Multus fault.
**Status: exit criterion NOT met.** Verification commands once the run completes, from
`docs/eval-records/model-registry.md`:
```bash
H=flywheel-rest.apps.sno-flywheel.local; T=$(oc create token pipeline-runner-dspa -n flywheel --duration=10m)
curl -sk --resolve $H:443:10.0.0.49 -H "Authorization: Bearer $T" https://$H/api/model_registry/v1alpha3/registered_models
curl -sk --resolve $H:443:10.0.0.49 -H "Authorization: Bearer $T" "https://$H/api/model_registry/v1alpha3/model_versions"
curl -sk --resolve $H:443:10.0.0.49 -H "Authorization: Bearer $T" "https://$H/api/model_registry/v1alpha3/model_artifacts"
```

---

## D087 — Promotion branch made unique per run; ref creation is idempotent (amends D066)

**Date:** 2026-09-09
**Context:** Phase 4.5 E1b, following D086. The proof run **`3afee844-007a-4c97-ab70-5f9cc38b9853`**
registered the version (id 2, `rekor_index` 5) and then died in `open_promotion_pr`:
`github.GithubException 422 Reference already exists` at
`repo.create_git_ref("refs/heads/promote/act-v2-ft160-rhem")`. Merging a PR does not delete its head
branch on this repo, so D066's fixed branch name `promote/<candidate>` collides with itself on any
second run for the same candidate (rerun after a fault, rehearsal, re-release) — and because ordering
(a) (D083) opens the PR after registration, the failure left a registered version with `pr_url: ""`
and no PR: exactly the visible-not-hidden state D083 wanted, but caused by the pipeline's own naming.
**Record:** `open_promotion_pr` now takes `run_id` (the same `dsl.PIPELINE_JOB_ID_PLACEHOLDER` value
`register_model` already records as `dsp_run_id`) and names the head
**`promote/<candidate>-<run_id[:8]>`** (UTC `%Y%m%d%H%M%S` if the id is empty). The branch name now
carries the DSP run that produced it, so PR ↔ registry version ↔ run join without a lookup. If the ref
exists anyway (a retried task within one run), it is force-moved to the new commit
(`get_git_ref(...).edit(sha, force=True)`) instead of failing; an open PR for the same head is reused
rather than duplicated. `register_model` → `open_promotion_pr` → `record_pr_url` are now all safe to
retry. Committed **`8d90222`** (`pipeline/act_flywheel_pipeline.py`,
`pipeline/act_flywheel_pipeline.yaml`); pipeline version **`v-202609091102-registry2`**
(`3f700dc3-7f0f-4366-9073-a05624e7722a`); proven by rerun
**`9015ecd4-5524-45cf-b6c7-f045a17860bc`** — SUCCEEDED, 4 m 10 s, seven tasks, PR #4 opened, `pr_url`
recorded.
**Rejected:** deleting the stale branch first (destroys the merged PR's head, which GitHub shows as
"branch deleted" and breaks the PR's compare view); keeping `promote/<candidate>` and appending `-2`,
`-3` (needs a listing round-trip and encodes nothing useful).
**Decision:** ship the run-scoped branch name and force-move/reuse semantics now; amends D066.

---

## D088 — `register_model` made idempotent on the version name (realises D027's "one version per candidate")

**Date:** 2026-09-09
**Context:** Phase 4.5 E1b, same rerun as D087.
**Record:** client `model-registry==0.3.11` (D083) checks `get_model_version_by_params(rm.id, version)`
inside `register_model` and raises `StoreError("Version <v> already exists")` — it never updates. A
rerun for a candidate whose version record already exists (the E1 situation: version id 2 from
`3afee844`) would therefore fail at `register_model`, before the PR, on every retry. `register_model`
now looks the version up first: missing → register as before; present → overwrite the version's custom
properties with this run's values (`dsp_run_id`, `rekor_index`, `eval_report_uri`, metrics,
`pr_url: ""`), update the description, and update the model artifact's `uri` if the signed digest
changed (it does not on the D023 path — same weights, same modelcar). The log line reads
`updated (previous dsp_run_id=…)`. `record_pr_url` then fills `pr_url` as before. Same commit as D087
(`8d90222`).
**Consequence:** the registry keeps one version row per candidate name; the row reflects the latest run
for that candidate, and the previous run's ids are recoverable from the PR history and the DSP run
list, not from the registry. That matches D027's intent (a durable promotion record, not a run ledger).
If a run ledger is ever wanted, add `dsp_run_ids` (append-only list) to the custom properties rather
than creating extra version rows.
**Decision:** ship idempotent-on-name registration now.

---

## D089 — Recommended, not applied: GitHub "Automatically delete head branches" (operator's call)

**Date:** 2026-09-09
**Context:** Phase 4.5 E1b, hygiene follow-on to D087.
**Record:** `gh api repos/RHPhysicalAI/hp-roscon-flywheel --jq .delete_branch_on_merge` →
`null`/false. With it enabled, `promote/*` and `rollback/*` branches disappear on merge and the
collision that caused D087's failure cannot recur even for a fixed name. D087 stands on its own without
it — the unique name is the real fix; deletion is hygiene. Path: Settings → General → Pull Requests →
"Automatically delete head branches."
**Blocked:** not changed by the agent — repo settings are the owner's, and the write classifier would
block it anyway.
**Also for the inbox:** stale `promote/act-v2-ft160`, `promote/act-v2-ft160-rhem`,
`rollback/act-v2-ft160-rhem` branches on GitHub are harmless; delete them by hand or leave them once
this setting is on. PR #4 is a rehearsal for the same weights already on the Fleet
(act-v2-ft160-rhem, D1/D2): close it without merging once the registry record is captured, or leave it
open as the demo's "PR waiting for the human gate" screen.

---

## D090 — E1 exit criterion met: registry shows `act-v2-ft160-rhem` with digest, full eval metrics, Rekor index, and PR #4; multi-arch digest is not run-to-run reproducible; Phase 4 item 5 closed

**Date:** 2026-09-09
**Context:** Phase 4.5 E1 exit criterion (BUILD-PLAN.md item E) — "the registry shows the promoted
version with digest + metrics for at least one candidate." D086 left this **NOT met**, parked on a
Multus stale-token fault; D087 and D088 fixed the branch-collision and registration faults that the
fault's rerun then hit.
**Record:** rerun **`9015ecd4-5524-45cf-b6c7-f045a17860bc`** completed past all of the above. Registry
now shows: registered model `soarm-act` (id 1), one version `act-v2-ft160-rhem` (id 2), artifact
`uri` `quay.io/jary/soarm-act-modelcar@sha256:18cc4412a21bfdd48d27b654558d1268e7c95f168f28b50873c4e2417369e61a`,
customProperties including `rekor_index=8`, success rates 0.73/0.86, fixed 20 / broken 7 / net 13,
`sign_test_p` 0.0192, `verdict` PASS, and `pr_url` pointing to **PR #4**
(https://github.com/RHPhysicalAI/hp-roscon-flywheel/pull/4, head
`promote/act-v2-ft160-rhem-9015ecd4`, commit `b33b5133`, not merged). `docs/eval-records/
model-registry.md` updated to this completed state; committed **`712d26e`**.
**Observation (multi-arch digest, D079):** the OCI-index digest is not byte-reproducible across
runs — this rerun's `18cc4412…` vs the failed run's `2879ddae…` vs D1's `1375d0bc…`; each run is
signed fresh and gets its own Rekor triple (the failed run at indices 5–7, this rerun at 8–10).
Expected given D079's per-arch layer timestamps; D088's update path already rewrites the artifact
`uri` to match on every registration. Consequence: "same weights" is not "same digest" — the registry's
`checkpoint_uri` is the stable identity, the digest is only the deployable one. Not worth fixing; noted.
**Decision:** the registry holds a durable promotion record — digest, full eval metrics, Rekor index,
and PR — for an actual promoted candidate. **E1's exit criterion is met.** Phase 4 item 5 (structured
promotion record / Model Registry) is closed.

---

## D091 — The standing negative-trust artifacts: an unsigned tag and a tag signed without a Rekor entry, both rejected on the device

**Date:** 2026-09-09
**Context:** Phase 4.5 C's exit line required showing the Fleet's `policy.json` rejects not only an
unsigned image but one signed with the flywheel key and never uploaded to RHTAS Rekor — proving
`rekorPublicKeyPath` enforcement, not just `keyPath`. Copying image b's digest to a new tag would have
inherited its already-logged signature (signatures key on the digest), so a distinct digest was made
by appending one 226-byte layer on top of image b (`crane append`) and signing it with the same
containerised cosign recipe but `--tlog-upload=false` and no `--rekor-url`.
**Record:** scratch tag `quay.io/jary/soarm-flywheel:negtest-notlog-2026-09-09` =
`sha256:d9996e7b1b779ba94fc4f5699cd0dbc4dc43c96c70e69b1b861e28f63e51d2cb` (image b plus the appended
226-byte layer, signed with `--tlog-upload=false`). Device-side failure strings, verbatim: unsigned →
`Source image rejected: A signature was required, but no signature exists` (exit 125); signed-no-tlog
→ `Source image rejected: missing dev.sigstore.cosign/bundle annotation` (exit 125). Control image b
(`2ad1fb1c…`) pulls clean under the same policy. `cosign verify` against the no-tlog signature exits 12
either way but with different bodies: `signature not found in transparency log` over the route, an
undecodable body over plain HTTP (`oc port-forward` to `svc/rekor-server`) — assert on exit code, not
message text. Full record: `docs/eval-records/negative-trust-tests.md`, commit `73df307`.
**Decision:** keep `negtest-notlog-2026-09-09` in quay as the demo's standing negative-test artifact.
Two rules govern it: never sign it into Rekor, and never move a moving tag onto it — its only value is
that it fails.

---

## D092 — CatalogItem version names are SemVer-derived from the candidate name (`act-v<N>-<suffix>` → `<N>.0.0-<suffix>`)

**Date:** 2026-09-09
**Context:** Phase 4.5 E2 (D027). The flightctl 1.3.0 Catalog API (v1alpha1) types `versions[].version`,
`replaces` and `skips` as `SemVer` with a strict OpenAPI pattern, and the server's own `validateSemver`
(`api/core/v1alpha1/validation.go:307`) additionally rejects a `v` prefix. The first apply of a version
named `act-v2-ft160` failed: `400` — `Error at "/spec/versions/0/version": doesn't match schema due to:
string doesn't match the regular expression "^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(…))?(?:\+(…))?$"`.
Our candidate names (`act-v2-ft160`, `act-v2-ft160-rhem`) are the Fleet's `MODEL_VERSION` and the
registry's version name; they cannot be catalog version names verbatim.
**Decision:** the seam maps `act-v<N>-<suffix>` to the pre-release semver `<N>.0.0-<suffix>` (so
`2.0.0-ft160`, `2.0.0-ft160-rhem`; pre-release identifiers may contain `-`) and keeps the candidate name
plus the full digest reference in the version's `readme`. Names outside the convention become
`0.0.0-<sanitized>`. The mapping lives in one place (inside `append_catalog_version`); the registry and
the Fleet keep the candidate name unchanged.
**Alternatives:** encode the fine-tune count as MINOR (`2.160.0`) — loses the `-rhem` suffix and any
non-numeric lineage; a lookup table in the pipeline — one more thing to edit per candidate.
**Consequences:** semver precedence between pre-releases is lexical (`ft160` < `ft160-rhem`), which
happens to match our chain, but the graph is defined by `replaces`, not by precedence. If a candidate
naming scheme ever changes, change the regex in the seam and the comment block in the CatalogItem file
together.

---

## D093 — `references.container` accepts the bare digest (`sha256:<hex>`) verbatim; D027's digest-form flag closed

**Date:** 2026-09-09
**Context:** D027 flagged digest-form references as unverified ("docs show `references: {container:
"<tag>"}`; digest-form references are unverified").
**Record:** verified on the live 1.3.0 API: `sha256:<hex>` → `201 Created`, stored verbatim; `@sha256:<hex>`
also accepted — the server does not validate the reference string at all, only that the key matches an
artifact type and the value is non-empty (`validateCatalogItemVersion`,
`api/core/v1alpha1/validation.go:205-217`). The v1alpha1 OpenAPI's own example (`CatalogItemSpec.versions
.example` / `CatalogItemVersion.references.example`) uses the bare `sha256:` form.
**Decision:** write the bare form — the same string `open_promotion_pr` already splits off `image_ref`
and pins in the Fleet — so the Fleet line and the catalog node are the same bytes and can be compared
with `grep`. D027's "if tag-only" fallback (digest in a description) is not needed; the `readme` still
carries `<uri>@<digest>` for humans.
**Flag closed:** D027's digest-form-references flag is resolved — accepted, verbatim, verified live.

---

## D094 — The Catalog object is a bootstrap object; its CatalogItems are synced by a second ResourceSync of type `catalog`

**Date:** 2026-09-09
**Context:** D032 said a second `type: catalog` sync would be needed; the docs recommend keeping the
Catalog and its items together in the synced directory. `internal/tasks/resourcesync.go:968-1009` rejects
an item only when its parent Catalog is owned by a *different* ResourceSync; an unowned (hand-applied)
Catalog is fine.
**Decision:** `rhem/bootstrap/catalog.yaml` (Catalog, hand-applied once, unowned) +
`rhem/bootstrap/resourcesync-catalog.yaml` (`ResourceSync/rhem-catalog`, `type: catalog`,
`path: gitops/rhem-catalog`) + `gitops/rhem-catalog/catalogitem-soarm-act.yaml` (owned by the sync,
read-only via API/CLI/UI once synced). Fleets stay in `gitops/rhem/` under `rhem-fleets` — one directory
per sync type, since `syncCatalogResources` errors on any kind other than Catalog/CatalogItem
(`resourcesync.go:157`).
**Record (live objects):** `Catalog/physical-ai-models` (hand-applied, `201 Created`);
`ResourceSync/rhem-catalog` — `Synced=True`, `Accessible=True`, `ResourceParsed=True`;
`CatalogItem physical-ai-models/soarm-act` (`type: container`, owner `ResourceSync/rhem-catalog`) carrying
versions `2.0.0-ft160` → `sha256:bdb513ca…` and `2.0.0-ft160-rhem` → `sha256:18cc4412…` with
`replaces: 2.0.0-ft160`, both `channels: [stable]`. `flightctl get catalogs` also lists a **pre-existing
`default` Catalog** from the chart install (age ~17h at observation time) — ours sits beside it, not in
place of it. UI: a top-level **`/catalog`** page exists in the bundle's route table (plus
`/devicemanagement/fleets/catalog` and `/devicemanagement/devices/catalog` "add from catalog" flows); not
screenshotted this session. Commits: `0c2dbbf` (bootstrap objects + CatalogItem + READMEs), `5a93103`
(eval record).
**Alternative:** put `catalog.yaml` in `gitops/rhem-catalog/` so the sync owns the Catalog too (the docs'
recommended layout). Rejected for now, only to keep the bootstrap set symmetric with `argocd/*-app.yaml`;
switching later is a file move plus `flightctl delete catalog physical-ai-models` before the first sync,
since the sync cannot adopt an object it did not create if ownership rules tighten.

---

## D095 — The `append_catalog_version` seam ships as designed (D027 realised); E2 exit criterion met

**Date:** 2026-09-09
**Context:** Phase 4.5 E2 exit line (BUILD-PLAN.md item E): "if E2 lands, the CatalogItem version graph
matches the Fleet's pin." Cross-references D027 (seam design, digest-form flag) and D038 (the Fleet pins a
quadlet `.volume` digest, not a `catalogItemRef`, so the Catalog is provenance beside the Fleet, not a
live reference).
**Record:** `append_catalog_version` is a nested function inside `open_promotion_pr` (KFP lightweight
components serialise only the component function, so a module-level helper would not reach the pod) —
fenced `# ---- Catalog seam (D027) BEGIN … END`, documented for deletion, with a defaulted
`catalog_item_file` param and a `PyYAML>=6,<7` pin marked "catalog seam only". Two behaviours D027 did not
specify: (1) **idempotent** — an existing version's reference is refreshed in place (the D087/D088 rerun
story applies here too); (2) **not load-bearing** — a `404` on the item file skips the seam with a log
line instead of failing the promotion, per D038 (the Fleet's digest pin is the product path). Removal
recipe: delete the fenced block, the `catalog_item_file` param (both places), the PyYAML pin, and the
`body += catalog_note` line; `gitops/rhem-catalog/` and the two bootstrap objects stay as the future
bridge's target. Proven locally against the committed file (no run — would open PR #5): byte-identical
rebuild from a one-version file, no-op on rerun, in-place reference update, append with a `replaces` edge,
no dangling edge when the incumbent is itself, and a name outside the convention falling back to
`0.0.0-sim-only.v9`. `py_compile` + KFP compile (`kfp 2.17.0`) clean; uploaded as pipeline version
**`v-202609091125-catalog`** (`119c51a3-6b71-4fdd-8755-9be65034b86f`) on pipeline `99ec0aab-…`; not run.
Commit: `0141efb` (the seam).
**Observation:** the DSP upload on the port-forwarded service port went through with an **empty** bearer
token (`oc create token` failed in the non-interactive ssh); the runbook's route-side calls still need the
token — noted, not fixed.
**Decision:** **E2's exit criterion is met** — the CatalogItem version graph matches the Fleet's pin: node
`2.0.0-ft160` = the Fleet's `sha256:bdb513ca…`; head node `2.0.0-ft160-rhem` = PR #4's unmerged digest,
exactly what the seam would leave once merged. **Stage sentence:** *v1alpha1; the Fleet pins the digest,
the Catalog shows the version graph.*
**Consequences:** Phase 4.5 E (item E) closes for E2 the same way D090 closed it for E1 — a durable,
verifiable record beside the Fleet, not a live dependency the demo relies on.

---

## D096 — Spike criterion 2 restated as a percentile (operator, 2026-09-09)

**Date:** 2026-09-09
**Context:** D024 criterion 2 read "no gap > 40 ms"; measured at 100/0.5 (D064): 8 windows, p50 20.1 ms,
p95 22.3–23.0, p99 25.5–29.9, max 312.8 ms, 32/9347 = 0.34 % of intervals > 40 ms, while part 3 passed
18/20 (D065).
**Decision (operator):** criterion 2 becomes "p99 inter-command gap ≤ 40 ms and ≤ 1 % of intervals >
40 ms" — the CPU stand-in passes it with margin; the max-gap outlier is recorded, not gated.
**Consequences:** D053/D064's "FAIL as written" verdicts stand as history; `docs/eval-records/cpu-spike.md`
summary should carry the restated criterion (note it as a follow-up for the G-prep docs pass, do not edit
that file).

---

## D097 — The dashboard's lineage source is the manifest-consumer's `COLLECTOR`, not the device

**Date:** 2026-09-09
**Context:** D073/D076: `get_model_version()` read the deleted `act-policy` Service and the badge was
stuck at `soarm-act-v1`; `counts.curated` read 0 (sync-agent moves curated → sent within seconds) and
`trigger.triggered` was stale from PR #1's 160. Options weighed: (a) subscribe to the device's latched
`/flywheel/model_version` — the dashboard is a plain Flask pod (`pip install flask kubernetes`) with no
ROS or zenoh, so this means a new image; (b) the flightctl API — needs a hub token in the pod; (c) read
`COLLECTOR` from the `manifest-consumer` Deployment, which Argo syncs from the same promotion commit that
edits the Fleet (D072) — the dashboard SA already has `deployments get/list` in `flywheel`.
**Decision:** (c). `_lineage()` returns `(COLLECTOR, Progressing.lastUpdateTime)` from the Deployment
(10 s cache); `counts.curated` and the trigger bar count curated + sent episode files stamped with that
lineage and newer than that epoch — the same thing the consumer counts (its pending count restarts at 0
on every rollout, D072); `promoted` = lineage ≠ `BASELINE_MODEL_VERSION` (`upstream-act-teacher`);
`flywheel_running` = an episode landed in the last `LOOP_ACTIVE_SECONDS` (180) instead of the in-cluster
sim replicas (always 0 on the desktop). `DASHBOARD_CODE_REV` env on the Deployment is bumped with
`dashboard.py` so Argo rolls the pod (the ConfigMap is read once at start).
**Record:** `/api/status` before → `{"model_version":"soarm-act-v1","counts":{"curated":0,"sent":196},
"trigger":{"triggered":true}}`; after the G-prep rollout the badge reads `model_version: act-v2-ft160`,
`promoted: true`, `trigger: {"threshold":160,"progress":0,"pct":0,"triggered":false}` (the bar resets to
0/160 by design at the new lineage's epoch) and a populated `lineage_since` (the consumer Deployment's
`Progressing.lastUpdateTime`). Commit: `a3cd943`.
**Consequences:** the badge follows the *hub's* lineage cut, which lands ~1–3 min after the device serves
the new version (Argo's poll; D072); the runbook says "the badge is the consumer's view, the device's
is the console log." Right after a promotion the bar reads 0 / 160 by design. Residual: a consumer crash
restart inside the same ReplicaSet does not move the epoch, and the consumer's post-trigger reset is not
observable — both are noted, neither matters for the demo.

---

## D098 — Runbook plan of record on RHEM: the pinned promotion is PR #2 (run `192f3ec5`), the Full Live candidate is `act-v2-ft160-rhem`

**Date:** 2026-09-09
**Context:** D023's Short Cut pinned run 6 / PR #1, whose diff edits the retired `gitops/act-serving/`
files (D025/D075). The RHEM promotion that was actually merged, rolled out and measured is PR #2 (run
`192f3ec5`, Rekor index 4, D068–D070), rolled back by PR #3 (D074). The registry proof run `9015ecd4`
(PR #4, closed unmerged) is what the Model Registry row and the Catalog head node point at (digest
`18cc4412…`, Rekor 8 — per-run digests, D090).
**Decision:** the Short Cut's Beat 3 pinned run is `192f3ec5` (kit: `run-192f3ec5-*`), Beat 5 is PR #2 +
Rekor 4 + the RHEM Fleet page, Beat 6 is the device Applications tab + `flightctl console` log + the
registry row + the Catalog graph, narrated as "one registry row per candidate, refreshed by the latest
run." The Full Live's reset state is *today's* state (`act-v2-ft160` @ `bdb513ca…`), its candidate is
`act-v2-ft160-rhem` (D067) and its rollback is `git revert -m 1` of the merge — no reset to the teacher
(the Fleet has never served `upstream-act-teacher`; there is no teacher modelcar). Run 6 / PR #1 / Rekor 1
stay in the kit as history.
**Consequences:** the narration's "v1 → v2" is the *gate's* comparison (teacher vs. the fine-tuned
weights); what the Fleet flips on stage is `act-v2-ft160` → `act-v2-ft160-rhem`, and the runbook says so
in "the one honest shortcut." The kit's Beat 5/6 clips (`docs/demo-kit/rhem-kit-script.md`) are the one
Full Live rehearsal on RHEM, filmed. Commit: `3095ced` (runbook), `ec08e73` (kit script + artifacts).

---

## D099 — Runbook "known artifacts" on RHEM (replaces the "Argo Degraded" line)

**Date:** 2026-09-09
**Record:** (1) the Fleet banner goes green ~30 s before the app is Healthy (D070) — show the
Applications tab, not the banner; (2) the dashboard badge lags the device by Argo's poll (D072, D097)
and, until the G-prep ConfigMap is rolled, reads `soarm-act-v1`; (3) the CPU stand-in's cadence residual
(0.34 % of intervals > 40 ms) passes the restated criterion (D096) — say "the stand-in serves on CPU,
the Fury on the GPU"; (4) the registry/Catalog head version carries PR #4's digest, not PR #2's (D090);
(5) `flightctl` may answer `connection refused 127.0.0.1:3443` for a few seconds while the desktop
port-forward loop re-establishes; (6) the trigger bar reads 0 / 160 right after a promotion by design.
**Gap (Mac):** the presenting Mac's `/etc/hosts` does not yet resolve `ui.flightctl…` or
`flywheel-rest…` — an operator, sudo task, not scriptable from the agent side; noted as a BUILD-PLAN
carry-over rather than fixed here.
**Consequences:** runbook commit `3095ced` carries all six lines; the Mac `/etc/hosts` gap blocks the
kit recording session's pre-conditions checklist (item 6) until the operator adds the entries.

---

## D100 — Contingency-kit clip list for Beats 5/6 on RHEM, and what's verified vs. marked unverified in the runbook

**Date:** 2026-09-09
**Context:** the runbook (`3095ced`) and the kit script (`ec08e73`, `docs/demo-kit/rhem-kit-script.md`)
were both rewritten for the RHEM path in the same G-prep pass; every command in the runbook is marked
run-today unless flagged otherwise, so the two files needed to agree on what's actually been exercised.
**Record — verified today (2026-09-09):** the runbook's state check; `flightctl get`/`flightctl console`;
the registry `curl`; the KFP task-list read; `cosign verify` with a Rekor lookup; the RHEM UI routes
including `/catalog`; `gh` (PR list/view); the dashboard `/api/status` read.
**Record — marked unverified/not-run in the runbook:** `count_curated.py` (Beat 2 terminal alternative);
`oc delete pod -n flywheel -l app=dashboard`; the host-runner restart line (only needed because the
runner was resident today); the Full Live `POST /runs` trigger path; a re-run of the device negative-pull
commands (D091's strings are quoted from the same-day run in `negative-trust-tests.md`, not re-executed
today); the first `run-coordinator.sh` invocation on the Tekton runtime digest (last loop ran on the
interim digest `2ad1fb1c…`); the `so-arm-sim` container restart fallback; `ENGINE=podman` on the Fury.
**Record — clip list (`docs/demo-kit/rhem-kit-script.md`):** `5a-pr`, `5b-rekor` (+ optional
`5b2-verify`), `6d-registry`, `6e-catalog`, `N1-negtrust` as the static pre-merge set; `5c-merge`,
`5d-fleet`, `5e-watch` as the one continuous promotion take; `6a-device`, `6b-console`, `6c-dashboard`
(+ optional `6f-minio`) as the post-merge set; `R1-revert-pr`, `R2-rollback` for the rollback. The
recording session doubles as the one Full Live rehearsal (BUILD-PLAN Phase 4.5 exit line).
**Decision:** the runbook's per-command markers stand as the source of truth for what's rehearsed vs.
scripted-but-unexercised; the kit script's clip table is the recording checklist; neither list is
re-verified by this pass — this entry only cross-indexes them for the record.
**Consequences:** the unverified items above are the ones a live Q&A or a Full Live attempt could
surface as untested; the operator's one recording session (kit script pre-conditions) is where the
Full Live rehearsal actually happens, closing the BUILD-PLAN Phase 4.5 exit criterion together with the
recording itself.

---

## D101 — torch cu130 aarch64 wheels exist, but only `torch` carries `+cu130` on arm64 → per-arch `TORCH_SPEC` build args

**Date:** 2026-09-09
**Context:** `https://download.pytorch.org/whl/cu130/` publishes `manylinux_2_28_aarch64` wheels for
torch 2.9.1 (`+cu130`), torchvision 0.24.1 and torchaudio 2.9.1 — but on aarch64 only `torch`
carries the `+cu130` local version; torchvision/torchaudio are plain `0.24.1` / `2.9.1`. The x86
pins (`torchaudio==2.9.1+cu130`, `torchvision==0.24.1+cu130`) therefore fail to resolve on arm64
(attempt 3, `runtime-image-hl7l8`: "No matching distribution found for torchaudio==2.9.1+cu130").
Ubuntu 24.04 glibc 2.39 satisfies manylinux_2_28.
**Decision (commit `aa7392f`):** `ARG TARGETARCH`; `TORCH_INDEX` (default the cu130 index) and
`TORCH_SPEC` (override) plus `TORCH_SPEC_AMD64`/`TORCH_SPEC_ARM64` defaults; one `pip3 install`
line picks the arch default. The same Dockerfile builds under `docker` on the host and `buildah`
in-cluster.
**Consequences:** the BUILD-PLAN "torch==2.9.1+cu130 aarch64 wheels" flag and the Phase 4 open
question close as "wheels exist, pins are per-arch" — CUDA on the Fury itself is proven on the
Fury (item G), not by this build.

---

## D102 — Dockerfile base image fully qualified, and `buildah` builds in docker manifest format (not OCI)

**Date:** 2026-09-09
**Context:** attempt 1 (`runtime-image-sfgmc`) failed in 9 s: `buildah` on the rhel9 image enforces
short-name resolution and "cannot prompt without a TTY" for `FROM ros:kilted`. Attempt 2
(`runtime-image-2fh8n`) then failed on both arches at the colcon step with exit 127: buildah's
default `--format oci` drops `SHELL` (OCI has no such config field), so `source` ran under
`/bin/sh`; OCI also has no `HEALTHCHECK`, which the Fleet quadlet relies on.
**Decision:** qualify the base in the Dockerfile (`docker.io/library/ros:${DISTRO}`) rather than
loosen `registries.conf` in the Task — the same line builds identically under `docker` on the host
and `buildah` in-cluster. A `FORMAT` param on the Task defaults to `docker`
(`buildah bud --format docker`); the pushed manifest list is therefore a Docker manifest list (as
the D045 interim images were `manifest.v2+json`), not an OCI index — same behaviour as
`docker buildx` on the host.
**Consequences:** `buildah bud --platform a,b --jobs 1` builds the platforms sequentially, arm64
first (attempt 2's log shows arm64 STEP 1–10 before amd64 STEP 1) — a Dockerfile error therefore
surfaces only after the qemu leg reaches it; attempt 2 lost ~26 min that way.

---

## D103 — cosign v2.6.5 release binary (sha256-verified, arch-derived) on a digest-pinned ubi9-minimal, not the RHTAS client image

**Date:** 2026-09-09
**Context:** the in-cluster RHTAS operator is 1.4.3; its client image
`registry.redhat.io/rhtas/cosign-rhel9:1.4.3` ships **cosign v3.0.4** (verified with
`cosign version` on the desktop). cosign v3 defaults to the new bundle + OCI 1.1 referrers layout
that containers-image on the device does not read (thor D014/D022). The 1.3.1 image is amd64-only
with an opaque commit version. Upstream `ghcr.io/sigstore/cosign/cosign:v2.6.5` is distroless (no
shell), so a script step cannot parse the Rekor index out of it.
**Decision:** the `cosign-sign` Task runs on `ubi9/ubi-minimal@sha256:34880b64…` and fetches
`cosign-linux-<arch>` v2.6.5 from the GitHub release, checked against `cosign_checksums.txt`
(amd64 `c3b4f541…`, arm64 `426193b4…`, embedded as Task param defaults) — the same cosign version
the KFP promotion and the D045 interim signing use, so one signing behaviour across the repo.
**Revisit:** when the device's containers-image reads cosign v3 bundles, or RHTAS ships a v2.x
client image again.

---

## D104 — `--recursive` signing of the manifest list; Tekton Chains stays unused

**Date:** 2026-09-09
**Decision:** cosign signs the list digest *and* each platform manifest (`--recursive=true`), one
Rekor entry each. podman's `sigstoreSigned` check runs against the platform manifest it resolves
to; cosign's `verify` runs against the list digest; both must hold. The Fleet pins the list digest.
Rekor indexes recorded in the run record (`docs/eval-records/runtime-image-tekton.md`).
**Consequences:** Tekton Chains is enabled on the cluster (`artifacts.oci.storage: oci`) but has no
`signing-secrets` configured — it does not sign or push anything for these runs, so the
`cosign-sign` Task (D103) is the sole signing path.

---

## D105 — qemu binfmt: `docker.io/tonistiigi/binfmt` (qemu v10.2.3) pinned by digest, DaemonSet in `flywheel`

**Date:** 2026-09-09
**Context:** `quay.io/multiarch/qemu-user-static` does not exist (401 = no such repo);
`docker.io/multiarch/qemu-user-static` tops out at qemu 7.2.0 (2023).
**Decision:** use the image `docker buildx` itself installs qemu from,
`tonistiigi/binfmt:qemu-v10.2.3@sha256:400a4873…`, as a privileged init container
(`--install arm64`) with a ubi-minimal `sleep infinity` holder. Registered `qemu-aarch64` with
flags `POCF` (F = fix-binary: the interpreter is loaded at registration, build containers need no
qemu binary). Re-running the init container is idempotent ("installing: arm64 OK" on a pod
restart). Its own SA (`qemu-binfmt`) carries the privileged SCC binding; nothing else in the
namespace gains it.

---

## D106 — Privileged SCC for the `pipeline` SA via a namespaced RoleBinding (thor D009)

**Date:** 2026-09-09
**Decision:** `RoleBinding pipeline-privileged-scc` → `ClusterRole system:openshift:scc:privileged`
in `flywheel` (what `oc adm policy add-scc-to-user` creates), in git under Argo. The build pod was
admitted with `openshift.io/scc: privileged` on the first run; no TektonConfig change
(`scc.default` stays `pipelines-scc`, no `maxAllowed`, no namespace annotation).

---

## D107 — one PVC per run (60 Gi local-path) holds both the checkout and buildah's overlay storage

**Date:** 2026-09-09
**Context:** Tekton's affinity assistant (`coschedule: workspaces`, the default) allows one
PVC-backed workspace per TaskRun. The buildah image's `storage.conf` uses fuse-overlayfs.
**Decision:** the build step writes its own `storage.conf` (native overlay, `graphroot`
`<workspace>/.buildah`, `runroot` in `/tmp`) via `CONTAINERS_STORAGE_CONF`; `git-clone` checks out
into `<workspace>/source`. The step empties the storage before exiting; deleting the PipelineRun
deletes the PVC. Node `/var` had 87 GB free at start (not the ~130 GB in the brief).
**Consequences:** a cluster-wide Multus stale-token incident (13:45Z, not Tekton-specific) left new
pods in `flywheel` and the local-path `helper-pod-create-pvc-*` failing sandbox creation with
`Unauthorized`; attempt 3's PVC stayed Pending until the operator restarted the multus pod (also
tracked as a BUILD-PLAN carry-over). Residual: the 60 Gi PVC is only released on PipelineRun
delete.

---

## D108 — Secrets handling for the build/sign Tasks

**Date:** 2026-09-09
**Decision:**
- `cosign-signing` (cosign.key, cosign.pub, cosign.password) created by hand from the desktop
  `~/cosign/` files; the password file is empty by construction (the key has no passphrase — the
  KFP sets `COSIGN_PASSWORD=""`, D026 row 13). The Task reads the password from the workspace
  file, never from a param or a printed env.
- `quay-push` (existing dockerconfigjson) is projected into the run as `config.json` through the
  workspace `items:` binding — no copy of the credentials in a ConfigMap or a param.
- The Rekor public key is a ConfigMap in git (public material), byte-identical to the Fleet's
  `/etc/pki/containers/rekor.pub` and to the live `/api/v1/log/publicKey` (md5 `e34fa270…`).

---

## D109 — PipelineRun template lives in git, excluded from Argo; `tkn` 0.46.0 installed and verified

**Date:** 2026-09-09
**Decision:** `gitops/tekton/runtime-image-pipelinerun.example.yaml` (`generateName`) started with
`oc create -f`; `argocd/tekton-app.yaml` sets `directory.exclude: '*.example.yaml'`. `tkn` 0.46.0
installed on the desktop at `~/.local/bin/tkn` from the GitHub release with `checksums.txt`
verified (an older 0.31.1 sat unnoticed in `/usr/local/bin`).

---

## D110 — Platform digests are read from the registry API, not from buildah

**Date:** 2026-09-09
**Context:** attempt 4's `PLATFORM_DIGESTS` result carried the local pre-push instance digests
(`b3b2ce60…`/`34be6793…`) — buildah compresses layers on push, so the pushed platform manifests
(`be0004b1…` arm64, `af06b989…` amd64) have different digests, and buildah 1.43.2's
`manifest inspect` refuses remote references ("unsupported transport docker"). The `--tls-verify`
flag is also per-subcommand, not global (the remote-inspect call had failed on that first).
**Decision (commit `9d7013e`):** after `manifest push` the Task reads the pushed index over the
registry v2 API (python3 in the buildah image, token auth from the same `config.json`) and only
falls back to the local list with an explicit `LOCAL-PRE-PUSH` marker. The pushed index is an OCI
index wrapping two Docker v2s2 manifests (`--format docker` keeps `HEALTHCHECK`); podman and
cosign handle that combination — the device pull and `cosign verify` are the proof.

---

## D111 — ubi-minimal has no `xargs`; `SIGN=false` verify-only mode; verify asserts on exit code only

**Date:** 2026-09-09
**Context:** attempt 4's sign step signed all three digests (Rekor 11/12/13) then died on
`xargs: command not found` before verify.
**Decision (commit `7bf70fb`):** bash-native join replaces `xargs`; a `SIGN` param lets the verify
half re-run on an already-signed digest without adding Rekor entries (`TaskRun
cosign-verify-3d67f424`). Verify asserts on the exit code only, using `SIGSTORE_REKOR_PUBLIC_KEY`
from the ConfigMap (D108) — the coordinator's negative-test finding is that the error text differs
between the plain-HTTP Service and the TLS route, so the exit code is the only reliable signal.

---

## D112 — F-Tekton (Phase 4.5 item F) exit criteria met; D028 executed

**Date:** 2026-09-09
**Context:** D028 (2026-09-08) decided that OpenShift Pipelines would build and sign the runtime
images multi-arch, in-cluster, replacing the unsigned amd64-only `docker buildx` build on the
host. D101–D111 record the build-up across four PipelineRun attempts; this entry closes the arc.
**Record:** PipelineRun `runtime-image-a4` built tag `act-inference-aa7392f` for both arches and
pushed manifest list `sha256:3d67f4246fd0915278b419bf4a3c67c3c9e0f8a07c553305a7445f65fc2cb4af`
(Rekor 11), with platform manifests amd64 `sha256:af06b989…` (Rekor 13) and arm64
`sha256:be0004b1…` (Rekor 12) — all three signed `--recursive` (D104) and verified on exit code
(D111).
Durations: build-and-push 69.3 min total = arm64 53.0 min (qemu-emulated, built first — D102) +
amd64 6.3 min + push 8.0 min; sign 12 s; verify 11 s.
Four attempts:

| Run | Failure | Fixed by |
|---|---|---|
| `runtime-image-sfgmc` | short-name base image, no TTY | D102 (qualified base) |
| `runtime-image-2fh8n` | `--format oci` drops `SHELL`/`HEALTHCHECK` | D102 (`--format docker`) |
| `runtime-image-hl7l8` | torchaudio/torchvision `+cu130` pins don't resolve on arm64 | D101 (per-arch `TORCH_SPEC`) |
| `runtime-image-a4` | `xargs` missing after signing all three digests | D111 (bash-native join, `SIGN=false`) |

Commits across the pass: `12cbc83`, `2f9e174`, `aa7392f` (D101), `7bf70fb` (D111), `9d7013e`
(D110), `9e982c0` (Fleet re-pinned to the new manifest-list digest), `b2c38df`.
Device rollout on the re-pin: rv 6 → 7, container +1:58, Healthy +4:09, `model_version` unchanged
(same candidate, new signed multi-arch image).
Full record: `docs/eval-records/runtime-image-tekton.md`.
**Decision:** the F-Tekton exit criteria are met — `tkn pipelinerun` builds both arches with a
Rekor entry; `argocd app list` count equals the files in `argocd/`, every app Synced with
`prune: true`; no `minioadmin` in git — and D028 is executed.
**Loose ends:** the 60 Gi PipelineRun PVC (D107) is released only on PipelineRun delete; a single
clean end-to-end run (no retries) is still to be exercised.

---

## D113 — Four BUILD-PLAN carry-overs closed: lineage-aware prune, Fleet-following MODEL_VERSION default, a `pending=` log line, and the eval-gate's blindness to a wedged action server

**Date:** 2026-09-09
**Context:** four rows from the Phase 4.5 carry-overs table (BUILD-PLAN.md), all owner "—"/"planned",
none destructive, none needing the operator:
1. `prune_bags.py`'s lineage-aware scan across every `episodes-curated/<mv>/` prefix and manifest.
2. `run-coordinator.sh`'s default `MODEL_VERSION` following the Fleet.
3. `consumer.py`'s per-manifest `pending=<n>` log line (named in D072 as narratable for Beat 6).
4. `healthcheck.sh cannot see a wedged action server; aggregate.success_rate should exclude
   goal_accepted: false episodes` — the second half only (see Rejected below for the first).

**Decision:**
1. `tools/host/prune_bags.py`: the curated-JSON scan now paginates the whole `episodes-curated`
   bucket instead of a hardcoded `upstream-act-teacher/` prefix, and the `dataset-manifests`
   consumer unions `episode_ids` across every message on the topic instead of filtering to one
   `DS`. `DS` is kept as an opt-in restriction to the old single-lineage behaviour. Verified by
   diffing the host's `~/prune_bags.py` against repo HEAD before editing (identical — no
   undocumented host drift) and syncing the fixed copy back (`scp` + `bash -n`).
2. `tools/host/run-coordinator.sh`: `MODEL_VERSION` now defaults to a `sed` read of
   `gitops/rhem/fleet-act-inference.yaml`'s `MODEL_VERSION:` line (same anchor pattern as D066's
   promotion regex), falling back to the old hardcoded `act-v2-ft160` only if the file is missing
   or unparsed; an explicit `MODEL_VERSION=` still wins (D020 eval labels use this). Synced to the
   host the same way as (1).
3. `gitops/flywheel/manifest-consumer.yaml`'s embedded `consumer.py`: one `print` per accepted
   manifest, `[consumer] pending=<n>/<THRESH> collector=<c>`. Because the Deployment mounts the
   ConfigMap as a file read once at process start (same shape as the dashboard's stale-code bug,
   D097), a `CONSUMER_CODE_REV` env — mirroring `DASHBOARD_CODE_REV` — is bumped alongside it so
   Argo's `selfHeal` rolls the pod instead of leaving the old `consumer.py` running.
4. `src/inference-coordinator/coordinator.py` `_write_eval_results`: rows with `goal_accepted:
   false` (the action server rejected the goal — an infra fault, not the policy failing the task)
   are excluded from `n`, `successes`, `cubes_hist` and `mean_smoothness`; a new
   `aggregate.goal_rejected` count reports how many were dropped, and the `[eval] DONE` log line
   appends it when non-zero. Full per-episode rows (including rejected ones) stay in `episodes[]`
   for diagnosis. Motivation: today a wedged action server would silently count every subsequent
   episode as `task_success: false` at 0 cubes, which could sink a real candidate's paired eval —
   the same D020/D022 gate this project depends on to keep bad retrains off the fleet.
**Effect timing:** (1) and (2) are host-side scripts, not baked into any image — live immediately,
confirmed synced to `10.0.0.48`. (3) lands on the next commit + Argo `flywheel` poll (~3 min,
D072), no rebuild. (4) is baked into the runtime image at Tekton build time (`docker/Dockerfile.
gpu-inference` `COPY`s `coordinator.py`) — **not live** until the next Tekton runtime-image build +
sign + Fleet re-pin; this is deliberately not triggered here (a live rebuild touches Rekor and the
serving device, outside this pass's scope) and stays a carry-over until that build happens.
**Rejected (scope, not this decision):** `healthcheck.sh`'s first half — detecting a wedged action
server that is still registered on the ROS graph — needs either a cheap non-invasive liveness probe
(none exists on `run_policy`) or a heartbeat/staleness signal from the coordinator, either of which
is a live-system design-and-test cycle, not a same-pass fix; the Tekton `PipelineRun` PVC cleanup
and "one clean end-to-end run" (D112's loose end) is a live-cluster action with its own Rekor/Fleet
footprint and stays deferred alongside it; the DSP empty-bearer-token check and the Mac `/etc/hosts`
entries stay operator-owned per the carry-overs table; Perses/Tempo Subscriptions stay a documented
deferral (`argocd/README.md` row 6 already states they're hand-installed, not in `gitops/operators/`)
rather than new Subscription manifests against operator versions this pass didn't verify compatible.
**Verification:** `bash -n` on both shell scripts, `ast.parse` on `prune_bags.py` and
`coordinator.py`, and an `ast.parse` of the embedded `consumer.py` block extracted from the YAML —
all clean. No live cluster or device state changed by this decision.

---

## D114 — DSP empty-bearer-token finding closed: real gap, bounded blast radius, NetworkPolicy would not have fixed it

**Date:** 2026-09-09
**Context:** carry-over row "DSP API accepted an empty bearer token via port-forward to the service
port — confirm the route enforces OAuth / consider a NetworkPolicy" (owner: operator). `svc/
ds-pipeline-dspa` exposes three ports: `8443/oauth` (fronted by the oauth-proxy container, the only
one the Route uses) and `8888/http` + `8887/grpc` (the raw `ml-pipeline-api-server`, no auth of its
own — the runbook's own `oc port-forward … 8888:8888` hits this port directly).
**Record (verified live, 2026-09-09):**
- Route, no `Authorization` header: **403**. Route, `Authorization: Bearer ` (empty): **403**. The
  oauth-proxy path is correctly gated.
- Port-forward to `8888`, `Authorization: Bearer ` (empty): **200**, full `pipelines` list returned
  — the finding reproduces exactly as reported.
- The operator-managed `NetworkPolicy/ds-pipelines-dspa` (owned by the `DataSciencePipelinesApplication`
  CR, `manifestival: new`) already restricts ingress to 8888/8887 to a specific set of in-cluster pod
  selectors — and it does not stop this. `oc port-forward` tunnels apiserver → kubelet → pod directly
  and never traverses the pod network a NetworkPolicy governs, so **a NetworkPolicy cannot close this
  gap**, regardless of how it's written. This corrects the carry-over's suggested remedy.
- `oc auth can-i create pods/portforward -n flywheel` is **no** for `system:authenticated` and for
  the namespace's default ServiceAccount. Enumerating every non-`system:` RoleBinding/
  ClusterRoleBinding in the cluster: the only User/Group bindings are `kube:admin` (cluster-admin —
  the credential `~/sno-flywheel/auth/kubeconfig` already holds) and `rhods-admins` (RHOAI dashboard
  admin group, **membership empty**). Nobody can reach the unauthenticated port who doesn't already
  hold cluster-admin.
**Decision:** accept as a documented, bounded defense-in-depth gap — the actual mitigating control is
RBAC on `pods/portforward` (already correctly scoped to cluster-admin only), not a NetworkPolicy. No
manifest changes made: writing a NetworkPolicy for this would be theater, and locking `pods/
portforward` down further than "cluster-admin only" is not meaningful on a single-operator SNO. If
DSP/RHOAI ever gains a way to disable the plain-HTTP backend port or front it with its own auth,
revisit; that would be an RHOAI product change, out of this repo's scope to hack around.
**Consequences:** the carry-over row is closed (not "planned"); the runbook's own use of `oc
port-forward … svc/ds-pipeline-dspa 8888:8888` (Beat 3, Full Live Part 3) is unaffected — it was
always run from the same cluster-admin kubeconfig this finding shows is the only principal who can
reach it anyway.

---

## D115 — First full-project multi-agent review (6 personas); FAILURE_RATE ground-truth override removed; host_runner.py `sys` import fixed

**Date:** 2026-09-09
**Context:** the orchestrator ran a full-project (not diff) review against `PROJECT-BRIEF.md`'s
stated intent, using the `review` skill's multi-agent pipeline with six personas tailored to this
project's actual audience (security, demo reliability, ROS/robotics domain credibility, Red Hat
platform/GitOps correctness, code quality, stakeholder & audience alignment). 42 findings after
de-duplication (14 Critical). Full reports: `.changes/reviews/code-review-*.md`; consolidated
triage table: `.changes/reviews/code-review-consolidated.md`. Note for future reviews on this repo:
the `consolidate-reviews` skill's automated script silently dropped two of six reports (a bullet
format mismatch — `` **`file:line`** `` vs. the required `**[file:line]**`) and part of a third;
the consolidated table was rebuilt by hand. Reviewer prompts should be told the exact bracket
format is load-bearing, not stylistic.
**Record — two findings independently verified by the orchestrator, not just reviewer-asserted:**
- The domain reviewer found `episode_emitter.py`'s `FAILURE_RATE` (default `0.1`, pinned `"0.1"` in
  `so-arm-sim.yaml`) rolling a coin flip that overwrites real ground-truth `task_success`/
  `cubes_placed` before the curator sees it — contradicting the runbook's Beat 2 "scored on ground
  truth, not a heuristic" claim. The orchestrator checked the live host `so-arm-sim` container
  directly (`docker inspect ... | grep FAILURE_RATE`): `FAILURE_RATE=0` today, so production data
  was not actively being corrupted — but the code default and the tracked in-cluster manifest
  (itself vestigial, D097: 0 replicas) were both live landmines.
- The security reviewer's RCE claim (`host_runner.handle()` f-strings a Kafka-sourced `candidate`
  into a `docker run ... bash -lc` string, over a PLAINTEXT NodePort with no SASL/ACL) was confirmed
  real by reading the code directly — and `host_runner.py`'s own docstring documents "Kafka,
  PLAINTEXT NodePort 30903" as the *intended* contract (the desktop's GPU is outside the cluster,
  D013), so this is an authentication gap on a load-bearing wire, not dead/incidental exposure.
  Left for a dedicated fork (tracked separately, not this decision) because a fix has to preserve
  that legitimate contract, not just remove the port.
**Decision (this entry covers only what the orchestrator fixed directly; the rest of the 42
findings are tracked in the consolidated review, dispositioned per D116+):**
1. `episode_emitter.py`: deleted the `FAILURE_RATE` env var, the coin-flip block, and the
   conditional overwrite of `task_success`/`cubes_placed`. `has_failure` stays in the emitted
   schema, hard-set `False` — `curator.yaml`'s Gate 0 comment ("injected failures... for demo") and
   the observability dashboard's `episode.has_failure=true` panel both still read the field; leaving
   it in place at a permanent `False` is a smaller, safer diff than removing it from the schema and
   touching every consumer. Not live yet — this file is baked into `docker/Dockerfile` (the
   *sim* image, `quay.io/jary/soarm-flywheel:sim-only` on the host, distinct from the
   Tekton-built RHEM policy image `docker/Dockerfile.gpu-inference`) — needs a host-side
   `docker build` + `so-arm-sim` restart, tracked as a follow-up alongside C10's camera-health fix
   (same image).
2. `host_runner.py`: added the missing `import sys`. Live-verified the host's resident copy
   (`~/host_runner.py`, not containerized) actually predates this bug entirely — no guard clause,
   just a hardcoded `minioadmin`/`minioadmin` fallback if the env vars are unset. Synced the repo's
   current version (the `sys.exit` guard plus the `import sys` fix) to the host file, matching the
   runbook's already-documented practice of sourcing `~/.minio-env` before starting the runner.
   File updated; the already-resident process was not restarted (no need to interrupt it — the
   guard only matters on next start).
**Consequences:** three forks dispatched for the remaining Critical findings (security hardening
C1/C2; GitOps hardening C6-C8; reliability/domain code fixes C10/C12), each to record its own
decision entry on completion. C9 (MinIO creds in git history) and C13 (stakeholder sign-off)
explicitly waived by the operator — not tracked as open work. C14 (contingency kit) deferred to
its own already-scheduled session. Warnings and Suggestions dispositioned per the consolidated
table's own "Suggested Disposition" column, accepted as-is by the operator.

---

## D116 — C1/C2 closed: validated request fields at the injection point instead of touching the Kafka broker or the demo's external consumer

**Date:** 2026-09-09
**Context:** D115's review found two related unauthenticated-network findings. C1: `host_runner.
handle()` (desktop, outside the cluster per D013) reads `candidate`/`incumbent`/`collector`/
`run_id` off the `training-triggers` Kafka topic — a PLAINTEXT NodePort (30903), no SASL/ACL — and
f-strings them into `in_image()`'s `docker run ... bash -lc` string and into filesystem/S3 paths.
C2: the curator's HTTP receiver (`0.0.0.0:8082`, NodePort 30802, no auth) builds
`RAW_DIR / f"{eid}.json"` from an unvalidated `episode_id`, so a crafted id can write outside
`raw/` — including straight into `curated/`, which `sync-agent` ships to MinIO/Kafka as trusted
training data, bypassing `score_episode()` entirely.
**Investigated before touching anything:** whether the Kafka NodePort could simply be deleted or
locked down. `docs/data-contract-eval-dashboard.md` documents a real external consumer — Olga
Lavtar's read-only eval dashboard (APPENG-6295) — but it only *consumes* `episode-manifests` and
`dataset-manifests`; it never publishes to `training-triggers`, the topic the RCE path actually
reads. Adding broker-level SASL/ACLs would be the complete fix, but it requires rotating
credentials for a consumer whose code lives in a separate, inaccessible repo — not verifiable
without a live end-to-end test against Olga's dashboard, which risks breaking a real external
integration blind. Deferred (see Consequences); fixed the actually-exploitable path instead.
**Decision:**
1. `src/host-runner/host_runner.py`: added `_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]
   {0,63}$")` and `_require_safe()`, called on `run_id`/`candidate`/`incumbent`/`collector` as the
   first lines inside `handle()`'s existing `try:` block — a rejected value now produces a clean
   `training-results` failure message (the same path any other exception already takes) instead of
   ever reaching a shell string or a filesystem path. Verified every real value in the project
   (`upstream-act-teacher`, `act-v2-ft160`, `act-v2-ft160-rhem`, `<collector>-ft<n>-<timestamp>`,
   KFP's `run_id`) matches the charset. Did not rewrite `in_image()`'s `bash -lc` calling
   convention — the entry-point validation is structurally sufficient (a valid token can't contain
   shell metacharacters or path separators) and a sweeping rewrite of the training/assemble command
   construction risked more than it protected.
2. `gitops/flywheel/curator.yaml`: the receiver now rejects any `episode_id` failing
   `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$` (HTTP 400) before it touches a path, re-asserts
   `out.resolve().parent == RAW_DIR.resolve()` as defense in depth, and caps request bodies at 1 MB
   (HTTP 413). The real emitter's `episode_id` is `str(uuid.uuid4())` — always matches.
**Consequences:** the Kafka NodePort (30903) stays open, PLAINTEXT, unauthenticated — an attacker
can still publish garbage to `training-triggers`, but the worst outcome now is a rejected trigger
in the log, not code execution. Broker-level SASL/ACL hardening (and coordinating a credential for
Olga's dashboard) is a real residual, tracked as a follow-up for a session that can reach and test
against her actual consumer — not closed here. C9 (MinIO creds in git history, same NodePort-family
risk class) was separately waived by the operator as out of scope for this project.

---

## D117 — C6/C7/C8 closed: Argo finalizers on all 8 bootstrap apps, a Securesign namespace manifest, and four `:latest` tags pinned

**Date:** 2026-09-09
**Context:** review D115's platform findings (`.changes/reviews/code-review-platform.md`).
**Decision:**
- **C6.** Added `metadata.finalizers: [resources-finalizer.argocd.argoproj.io]` to all 8
  `argocd/*-app.yaml` Application manifests (the review said 9; the actual count of `kind:
  Application` files in `argocd/` is 8 — `repo-flightctl-charts.yaml` is a Repository Secret, not
  an Application). Live-patched all 8 Application objects on the desktop cluster immediately
  (`oc patch applications.argoproj.io <name> ... --type=merge`), not just committed for next sync,
  since these bootstrap Applications are applied by hand once and not themselves continuously
  GitOps-reconciled. `rhem`'s live object already carried
  `post-delete-finalizer.argocd.argoproj.io` + `.../cleanup` (handles the flightctl API objects
  Argo can't natively manage, D024) — a naive merge patch would have replaced, not appended, and
  wiped those; patched with all three finalizers explicitly to preserve them. One operational
  hazard found and worth recording: `oc get application <name>` (singular, unqualified) resolves
  ambiguously on this cluster — there's a second, unrelated `applications.app.k8s.io` CRD installed
  (likely from an OLM/KubeVirt-adjacent operator) that the short name matches first, giving a
  false "not found" for a real Argo Application. Always use the fully-qualified
  `applications.argoproj.io` on this cluster.
- **C7.** Added `gitops/operators-config/namespace-securesign.yaml` (`Namespace/trusted-artifact-signer`,
  sync-wave `"0"`, one wave ahead of `securesign.yaml`'s `"1"`) rather than a documented manual
  step — self-contained, no new bootstrap instruction needed. Server dry-run against the live
  cluster confirms it reconciles as a no-op against the namespace that already exists there
  (created by some earlier, undocumented means); a from-scratch Fury bring-up now creates it
  declaratively instead of leaving `Securesign/securesign` stuck.
- **C8.** Pinned four of the five floating tags the review found, using digests read from what's
  actually live on the cluster (not just "any recent release"):
  - `gitops/minio/minio.yaml`: `quay.io/minio/minio@sha256:14cea493…` — confirmed via
    `docker buildx imagetools inspect` that this digest *is* the multi-arch manifest list (amd64/
    arm64/ppc64le), matching the live pod's `imageID` exactly.
  - `gitops/minio/minio-readonly-user.yaml`: `quay.io/minio/mc@sha256:a7fe349e…` — same
    verification, matches the completed `minio-olga-readonly-setup` Job's `imageID`.
  - `gitops/storage/local-path-provisioner.yaml`: `registry.access.redhat.com/ubi9/ubi-minimal:9.8`
    — a real, resolvable version tag, not a digest: no helper pod has run recently to read a live
    `imageID` from, and this is baked into a ConfigMap template rather than a running container.
  - `gitops/flywheel/so-arm-sim.yaml`: `quay.io/jary/soarm-flywheel@sha256:9b0e0123…` (`:latest`'s
    current resolved digest). **Important nuance, not a full fix**: this Deployment is scaled to 0
    replicas and vestigial (D097) — the real sim producer runs as a host Docker container tagged
    `:sim-only`, built locally via `docker buildx build --load` and **never pushed to quay** (no
    `RepoDigests` recorded — confirmed via `docker inspect`). So this pin removes the
    floating-tag anti-pattern but pins to content that is stale relative to what's actually
    running; it does not and cannot make the manifest describe reality, because the real image was
    never published under any tag. If this Deployment is ever meant to run for real (e.g. an
    in-cluster sim on the Fury), `:sim-only` needs to be pushed to quay first — flagged in a code
    comment at the pin site, not silently left implicit.
  - Also dropped the now-dead `FAILURE_RATE` env var from `so-arm-sim.yaml` while in the file for
    the image pin (the code that read it was removed in D115) — small, same-file, low-risk.
  - Left `gitops/flywheel/manifest-consumer.yaml`'s `python-312:latest` untouched, per the
    already-tracked D066/D068 deferral.
**Verification:** all five touched YAML files parse (`yaml.safe_load`); server-side dry-run
(`oc apply --dry-run=server`) against the live desktop cluster succeeded for `minio.yaml`,
`local-path-provisioner.yaml`, and `so-arm-sim.yaml`. `minio-readonly-user.yaml`'s dry-run reports
"field is immutable" on the completed `minio-olga-readonly-setup` Job — expected Kubernetes
behavior (Job pod templates are immutable once created; this Job finished successfully 5 days ago)
and unrelated to the digest pin itself. Actually applying this file requires deleting the completed
Job first, a one-time step left for the operator/orchestrator rather than done here (out of this
fork's scope to delete a live cluster object). The 8 live-patched Application finalizers were
verified via `oc get applications.argoproj.io -n openshift-gitops -o custom-columns=...`.
**Not done, out of scope for this fix:** the git-committed finalizer change and the live-patched
Application objects are two separate actions taken together deliberately (see C6 above); no
Application was deleted or resynced to test the finalizer, per the "nothing destructive" constraint
on this pass.

---

## D118 — C10/C12 closed: camera health checks liveness not presence; joint-index gripper/wrist bug fixed by name across three call sites plus sim_reset.py's own arm-homing parse bug

**Date:** 2026-09-09
**Context:** consolidated review findings C10 (`.changes/reviews/code-review-reliability.md`) and
C12 (`.changes/reviews/code-review-domain.md`). The operator asked specifically to "be careful with
C12" since it touches robot motion-settle logic; this entry documents the exact joint set used and
the live verification performed, so a future reader can audit it without re-deriving the
alphabetical-order reasoning from scratch.

**C10 — `src/camera-bridge/camera_bridge.py`:** `/health` reported `_latest[cam] is not None`,
which stayed true forever after the first frame, even if the sim froze. Added `_last_seen[cam] =
time.time()` on every successfully decoded frame; `/health` now reports freshness
(`now - last_seen < HEALTH_STALE_S`, default 3.0s — generous against the "up to 30 fps" source rate
so a transient hiccup doesn't false-positive, tight enough to catch a real freeze in a few seconds)
instead of presence, returns HTTP 503 (not 200) when neither camera is fresh, and adds an `age_s`
field to the JSON body. Checked for other consumers of this endpoint first (`docs/DEMO_RUNBOOK.md`,
`gitops/flywheel/dashboard.yaml`) — only human `curl` usage in the runbook's recovery flow, no
scripted status-code dependency, so changing the status code semantics is safe.

**C12 — joint-index bug, three call sites in `coordinator.py` plus `sim_reset.py`:**
`/joint_states` publishes joints alphabetically: `elbow_flex_joint(0), gripper_joint(1),
shoulder_lift_joint(2), shoulder_pan_joint(3), wrist_flex_joint(4), wrist_roll_joint(5)`.
`CTRL_JOINTS` (the controller's expected order) is `shoulder_pan, shoulder_lift, elbow_flex,
wrist_flex, wrist_roll, gripper`. A raw `positions[:5]` slice against the alphabetical order
therefore contains `{elbow_flex, gripper, shoulder_lift, shoulder_pan, wrist_flex}` — includes the
gripper, excludes `wrist_roll_joint` entirely — the opposite of the "skip gripper" comment's intent.
- `coordinator.py`: added `_arm_joint_positions(positions)`, a name-keyed helper (`dict(zip(names,
  positions))`, filtered to `CTRL_JOINTS` minus `GRIPPER_JOINT`) mirroring the pattern already
  proven correct in `_learn_rest_pose`/`_recover_arm` in the same file. Applied it to all three
  call sites: `_arm_at_home()` (used by `_wait_for_home`), `run_forever()`'s step-4 "is the arm
  still moving" rest-detector, and `_policy_window()`'s identical eval-harness copy. The fixed
  joint set for every "home/settled" check: `shoulder_pan_joint, shoulder_lift_joint,
  elbow_flex_joint, wrist_flex_joint, wrist_roll_joint` (5 joints, gripper excluded, wrist_roll
  included).
- `sim_reset.py`'s `arm_is_home()` had no name source at all — it shells out to `ros2 topic echo
  --once --field position /joint_states` with no `name` field. It cannot import `coordinator.py`'s
  `CTRL_JOINTS` (this script ships in a separate image, `docker/Dockerfile`, which does not carry
  `coordinator.py`), so a local `ARM_JOINTS`/`GRIPPER_JOINT` pair was added with the identical
  default string and the same `CTRL_JOINTS`/`GRIPPER_JOINT` env-var names, so a shared override
  stays consistent across both scripts. Added a second `ros2 topic echo --field name` call and
  paired the two outputs by name via the same `dict(zip(...))` idiom.
- **Live-verified against the real running sim** (`so-arm-sim` container, read-only): `ros2 topic
  echo --once --field name /joint_states` confirmed the alphabetical order exactly as documented.
  Discovered along the way — `ros2 topic echo` appends a trailing `---\n` YAML document separator
  even in `--once --field` mode; the position field's existing loose regex (`[-\d.eE]+`) captured
  it as a spurious `"---"` token, and the pre-existing filter (`not in ("", ".", "-")`) never
  excluded a 3-character string, so `float("---")` raised inside every call and `arm_is_home()` fell
  through to `except: return False` **on every invocation, unconditionally, independent of this
  fix** — a second, pre-existing bug, not introduced here. Fixed by parsing each numeric candidate
  defensively (try/except per token) rather than trusting the character class. Copied the current
  file into the live container at a throwaway path (`/tmp/sim_reset_test.py`, not overwriting the
  resident copy) and ran the real `arm_is_home()` function against the live topic twice — once
  before the `---` fix (returned `False`, arm was actually at ~1e-10 rad, i.e. home) and once after
  (returned `True`, correct) — then deleted the test file from both the container and the host.
**Decision:** both fixes land as-is. Not live yet — `camera_bridge.py` and `sim_reset.py` are baked
into `docker/Dockerfile` (the host sim image, `quay.io/jary/soarm-flywheel:sim-only`); `coordinator.py`
and `sim_reset.py` are also baked into `docker/Dockerfile.gpu-inference` (the Tekton-built RHEM
image). Deployment is tracked separately: a host-side `docker build` for the sim image (batched with
D115's `episode_emitter.py` fix, same image) and the Tekton multi-arch rebuild the operator
authorized as C11.
**Confidence:** high on C10 (simple, verified logic, low blast radius). High on C12's joint-set
correctness (verified against real captured live data twice, including the newly-found `---` bug —
not just code-read reasoning). Medium-high on behavioral impact: the "is it still moving"
rest-detector fix could plausibly change early-stop timing in either direction (previously ignored
real wrist-roll motion and reacted to gripper motion instead) — worth one supervised sim run before
the next rehearsal to confirm episode timing looks sane, not just that the code is correct.
**Consequences:** the `RECOVER_ON_FAIL` comment block (coordinator.py:86-89) notes a previously
undiagnosed loop-breakage when `RESET_ARM=true` was tried; this joint-index bug is a plausible
contributor (gripper motion during grasp/release could have falsely extended "still moving," or
missed wrist-roll settling), not confirmed as the root cause — worth revisiting if that path is
tried again.

---

## D119 — Polish pass for an outside technical audience: role-based terminology, pre-RHEM demo-kit artifacts archived, one stale operational README corrected

**Date:** 2026-09-09
**Context:** the user's framing: the repo has to be readable front to back by a random ROSCon
attendee or HP partner engineer and represent both parties well, not just function. Scoped
deliberately to *presentation*, not history — `DECISIONS.md` and `docs/eval-records/` stay exactly
as written; this project's honest, warts-and-all record is a strength, not something to sand down.
**Decision:**
1. **"Mac" → role-based terminology.** Every operational doc that distinguishes which physical
   machine a step runs on (`docs/DEMO_RUNBOOK.md`, `docs/demo-kit/rhem-kit-script.md`,
   `argocd/README.md`, `device/README.md`, `rhem/bootstrap/README.md`, `device/enroll.sh`,
   `BUILD-PLAN.md`'s carry-over table) now says "the presenting laptop" instead of "the Mac," with
   a one-time definition on first use in `docs/DEMO_RUNBOOK.md`'s topology cheat-sheet ("the
   operator's machine at the booth — browser, phone, ssh/gh client; no cluster access or
   kubeconfig of its own"). The distinction itself is load-bearing (the runbook only works if
   followed on the right box) and stays; only the "whose personal laptop" framing goes.
   `BUILD-PLAN.md`'s Phase 0 exit-criterion checkbox (historical) and every mention in
   `DECISIONS.md` are untouched on purpose.
2. **Pre-RHEM demo-kit artifacts archived, not deleted.** `run6-task-states.txt`,
   `run6-host-runner.log`, `run6-pipeline-run.log`, `run6-swap-agent.log`, `pr1.md`,
   `rekor-entry-1.json` — all already marked superseded/history-only in `docs/demo-kit/README.md`'s
   own table (the project fully moved to the RHEM device-plane path, Phase 4.5) — moved via `git
   mv` into `docs/demo-kit/archive-pre-rhem/` (history preserved), with a one-paragraph README
   explaining what the subdirectory is. `docs/demo-kit/README.md`'s table updated to the new
   paths. The current Short Cut's real artifacts (`run-192f3ec5-*`, `pr2.md`, `rekor-entry-4.json`)
   were not touched.
3. **`gitops/rhem/README.md`'s "Open before the live apply" table was stale**, describing three
   blockers (unsigned/unpushed runtime image, private modelcar repo, no verified positive pull
   test) that Phase 4.5-D/F actually closed weeks of decisions ago (D068, D091, D101-D112). Marked
   resolved with the closing evidence cited; left the two genuinely still-open items (`gpu=nvidia`
   SELinux branch, untestable without the Fury; `HealthOnFailure=kill` masking a wedged — not
   crashed — server, tied to the still-open BUILD-PLAN carry-over) marked as such instead of
   silently dropping them.
4. **General cruft sweep**: no stray debug prints, `pdb`/`breakpoint()` calls, or unexplained
   commented-out code found anywhere in the tracked tree. No further changes made under this item.
**Consequences:** a separate, parallel pass is assembling new front-door documentation (README,
architecture/data-flow diagrams, bill of materials) — not part of this decision.

---

## D120 — Front-door documentation added: README, architecture, data flow, bill of materials, Fury setup guide

**Date:** 2026-09-09
**Context:** the repo had no top-level `README.md` — `AGENTS.md` exists but is written for an AI
agent's onboarding read order, not a human landing on the GitHub page. The user asked for content
that would let "randoms go through it front to back" and represent both the project and HP well,
and specifically asked for material an engineer standing up the Fury would want: a data-flow
diagram, an architecture chart, a bill of materials (what's running where), and Fury-specific
setup guidance.
**Decision:** five new files, all synthesis of existing source-of-truth material (manifests,
pipeline code, `DECISIONS.md`, `docs/DEMO_RUNBOOK.md`) rather than new claims — every fact traces
to something already in the repo, verified against a couple of live checks (`docker ps`,
`oc get applications.argoproj.io`) rather than only the YAML:
- `README.md` — the actual front door: the pitch, the architecture summary, a table pointing to
  every other doc, and a status line kept current as of today.
- `docs/ARCHITECTURE.md` — component diagram (Mermaid, GitHub-native rendering) covering the hub,
  the managed device, and the sim/producer, plus the desktop-vs-Fury topology table.
- `docs/DATA-FLOW.md` — a sequence diagram plus stage-by-stage prose from episode generation
  through promotion to the loop closing, including the eval-gate's actual gating rule and an
  explicit, undisguised statement of the one place a step is time-compressed for the demo (matches
  `docs/DEMO_RUNBOOK.md`'s own "one honest shortcut" framing rather than restating it differently).
- `docs/BILL-OF-MATERIALS.md` — every Argo app, host container, and device workload, which box it
  runs on, and what it's built from. Two live-verified findings worth carrying forward: the `minio`
  Argo app currently shows `OutOfSync` (a completed Job's immutable pod spec blocking an image-pin
  update — the same class of issue the GitOps fork hit and fixed for a different Job this session);
  and an unidentified container (`competent_chatelet`, up 7 days) is running on the desktop host
  that this pass could not attribute to anything in the repo — flagged, not investigated further
  (out of this decision's scope).
- `docs/FURY-SETUP.md` — a pre-flight guide for whoever has hands-on Fury access: what "the device
  is the host" actually means there, the bring-up sequence, and an explicit split between what's
  verified on aarch64 today (the multi-arch signed build, the per-arch torch pin) versus what's
  designed but unrehearsed (the `ENGINE=podman` coordinator path, any real inference on Blackwell
  silicon, a from-scratch SNO bring-up done back-to-back). Also surfaces two untracked external
  dependencies (Rick Gosalvez's access window, Manny's on-site logistics) as open, not assumed.
Terminology matches the parallel cleanup pass: "the presenting laptop," not personal-device
references.
**Consequences:** the `minio` OutOfSync finding and the unidentified container are new information
for the orchestrator to act on or dismiss — not resolved here. `PROJECT-BRIEF.md`'s architecture
section still describes the pre-RHEM blue/green design (out of this decision's scope to edit; the
new `docs/ARCHITECTURE.md` reflects the current RHEM-based design and should be treated as
authoritative over `PROJECT-BRIEF.md`'s diagram until that's reconciled).

---

## D121 — Engineering history moved under `docs/internal/`; README rewritten as a public front door; Phase 4 status reconciled

**Date:** 2026-09-09
**Context:** the operator's direction as the project nears its final phases: `PROJECT-BRIEF.md`,
`BUILD-PLAN.md`, `DECISIONS.md`, `THOR-TESTING-REUSE.md` and `BOOTSTRAP-LOOP.md` "cannot sit at the
root of the repo and draw attention nor should they be referenced more than necessary," but they
stay versioned as durable record (an earlier option of untracking them was withdrawn — untracking
would not have removed them from history anyway). The D120 README linked all five prominently, led
with internal partnership framing, cited the internal `thor-testing` reuse, and carried no AI-
assistance note; the operator rejected it.
**Decision:**
1. The five documents move to `docs/internal/` via `git mv` (history preserved). `AGENTS.md`'s
   read-order now points there; prose references in `docs/*.md` and three GitOps YAML comments
   updated. References between the five files themselves are bare sibling filenames and stay
   valid; `DECISIONS.md` is not edited retroactively.
2. `README.md` rewritten: one-line description, the Red Hat AI-assistance note immediately after
   it, overview, the loop as an eight-stage table, evidence-linked results, three-plane
   architecture with a development-vs-Fury table, platform versions, upstream attribution,
   repository layout, documentation index, deploy pointers, dated status. Internal history gets one
   sentence pointing at `docs/internal/`. No personal names, no workstation details, no internal
   repo references.
3. `BUILD-PLAN.md` Phase 4 reconciled with reality: "arm64 images build," "Fury porting checklist
   written," and "promotion record emitted" checked with references (D101–D112, D120, D090); Beat 5's
   narration line no longer says "blue/green swap"; item 4's node-side-enforcement bullet now cites
   the Fleet inline config and D091 instead of the retired `gitops/act-serving/README.md`; the
   status snapshot's Phase 4.5 reference range extends to D120. Phase 3+ is deliberately left as-is
   pending the operator's decision on its scope.
4. `PROJECT-BRIEF.md`'s architecture diagram replaced with the RHEM-era flow, with a note that it
   was updated and that `docs/ARCHITECTURE.md`/`docs/DATA-FLOW.md` are current.
**Also noted:** every commit this session carried `Co-Authored-By: Claude Sonnet 5`, contrary to
the project rule of bare `Co-Authored-by: Claude` (no model name). Corrected from this commit on;
pushed history is not rewritten.
**Consequences:** `docs/internal/` is the only place engineering history is linked from the front
door. C3/C10/C11/C12 from the review are fixed in source but still not deployed (host sim image
rebuild; Tekton runtime rebuild + Fleet re-pin) — tracked as the next action, not closed.

---

## D122 — Front-door docs trimmed to what does not go stale; eval-dashboard contract and Phase 3 chart moved to `docs/internal/`

**Date:** 2026-09-09
**Context:** operator review of D121's README and the four architecture-family docs.
**Decision:** `README.md` loses the development-workstation mentions, the engineering-history
pointer, and the Deploying and Status sections; it now ends at the documentation index.
`docs/ARCHITECTURE.md`, `docs/DATA-FLOW.md`, `docs/BILL-OF-MATERIALS.md` and `docs/FURY-SETUP.md`
lose every decision-record reference, the "where to verify" section, the drift note, named external
contacts, and "today / target / as of this writing" phrasing — anything that would need chasing as
the system moves. Evidence links point at `docs/eval-records/` only. `docs/data-contract-eval-
dashboard.md` and `docs/phase3-ladder.html` move to `docs/internal/` (`git mv`); the runbook,
demo-kit index and `device/README.md` are repointed. The `docs/eval-records/phase3-ladder/` JSON
records stay where they are — the README's results link to them.
**Consequences:** the public surface is `README.md`, `docs/` minus `docs/internal/`, and the
operational READMEs. Anything that describes the state of the system at a point in time lives
under `docs/internal/` or `docs/eval-records/`.

---

## D123 — Phase 3+ (bootstrap loop) is post-ROSCon: designed, prerequisites complete, not scheduled

**Date:** 2026-09-09 (operator decision)
**Context:** `BUILD-PLAN.md` listed Phase 3+ as "Not started," which read as unfinished work rather
than a scope choice, and `BOOTSTRAP-LOOP.md` still claimed the flywheel curated metadata only and
that all training used the upstream dataset — both false since Phase 2.5 (D017–D019) and Phase 3
(D020–D022).
**Decision:** Phase 3+ is not built before ROSCon. The status row now says so and names what is
left (privileged expert, held-out randomized eval set, curriculum controller). `BOOTSTRAP-LOOP.md`'s
"gap that blocks any real loop" section is rewritten as closed, its component table marks
recording and assembly as done, and its scope call records this decision. Rationale: it competes
with the kit recording and the Fury bring-up on the same sim host, and nothing in the booth
narrative depends on it — the runbook already claims only one round of self-improvement.
**Consequences:** the Q&A answer is "one round proven; the next step is a privileged expert,
designed, with every prerequisite built." First upgrade after the event.

---

## D124 — Dashboard finished for the booth: Beat 4 panel from the frozen Phase 3 ladder, configurable camera host, operator-only Clear, honest empty state, probes

**Date:** 2026-09-09 (operator-approved punch list)
**Context:** the dashboard's bottom card was the Cosmos-era "Policy comparison" video player,
waiting on rollout videos that never existed here; the camera bridge address was hardcoded; "Clear
Data" sat in the header the runbook says must never be pressed; an idle loop looked like a broken
page (`0 / 160`, `idle`, blank cards); the Deployment had no probes. BUILD-PLAN Phase 4 item 1
intended Beat 4 to run on the dashboard from frozen records.
**Decision:**
1. The comparison card becomes **"Policy improvement — same policy, same 100 scenes, more of its
   own curated data"**: CSS bars of success rate per rung (teacher 73% dashed baseline; 20 → 60%,
   40 → 56%, 80 → 76%, 160 → 86%) and a table with N, mean cubes, fixed/broken, net, sign-test p
   and the gate verdict, plus the headline the runbook narrates. Data is a new ConfigMap
   `gitops/flywheel/phase3-ladder-summary.yaml` transcribed from `docs/eval-records/phase3-ladder/`
   (regeneration command in its header), mounted at `/app/ladder` and served at `/api/ladder`.
   No chart library; the page stays dependency-free. All dream/rollout code, the `/api/rollout`
   route and the `/var/lib/dreams` hostPath mount are removed.
2. `CAMERA_HOST` env (default `10.0.0.48`, the Fury host's booth address goes there) is
   substituted into the page by the Flask app in place of the hardcoded host.
3. Clear Data leaves the header; it is a footer link shown only with `?ops=1`, still
   `confirm()`-gated, hitting the unchanged `/api/control/clear`.
4. Empty state says what is true: header indicator "Loop stopped" (grey) vs "Flywheel running"
   (green pulse — the old header always said running), progress state "loop stopped", and the
   rollout/log cards read "Collection loop stopped — episodes appear here once it runs."
5. Readiness and liveness probes on `/api/status` (readiness 20 s initial delay for the pip
   install, liveness 60 s / 4 failures).
6. Card titles lose the Cosmos-era "1 ·" / "2 ·" numbering. `DASHBOARD_CODE_REV` → `2026-09-09-h`.
7. Runbook: Beat 4's primary screen is the dashboard panel, the static chart
   `docs/internal/phase3-ladder.html` is the fallback; the "Policy comparison card is empty" known
   artifact is gone; the kit table's Beat 4 row and the "What's real" line updated.
**Verification:** YAML parses, `dashboard.py` `ast.parse`, extracted page JS `node --check`,
ladder JSON parses; live after Argo sync — see the commit's follow-up check.

---

## D125 — The dashboard's Beat 4 ladder panel (D124) removed: Beat 4 belongs to the eval dashboard, and the real A/B artifact is paired episode video

**Date:** 2026-09-09 (operator decision)
**Context:** D124 replaced the dead Cosmos-era "Policy comparison" video card with a panel rendering the
frozen Phase 3 ladder. On review the operator judged it a stopgap: it duplicates the read-only eval
dashboard's scope (APPENG-6295 — success rate, cube distribution, smoothness per lineage, success vs.
dataset size, replayable from a frozen file directory), and it is not the comparison the demo wants.
The comparison that would actually land is paired *video*: the eval harness already runs identical
seeded scenes per policy, so recording a chosen seed set for v1 and v2 (and later lineages), porting
the bags to LeRobot, and showing the clips side by side with the per-episode numbers — designed
tomorrow, not built tonight.
**Decision:** the ladder card, its CSS/JS, the `/api/ladder` route, the `LADDER_FILE` env, the
`/app/ladder` mount and the `phase3-ladder-summary` ConfigMap are removed; `DASHBOARD_CODE_REV`
bumped to roll the pod. Everything else from D124 stays: `CAMERA_HOST` injection, readiness/liveness
probes, the honest "Loop stopped" states and empty-state copy, the ops-only Clear control, the
dropped card numbering. The old video card is not reinstated — the page simply has no Beat 4 card.
The runbook's Beat 4 section, kit-table row and "What's real" line revert to the static chart
(`docs/internal/phase3-ladder.html`) as primary with the eval dashboard taking the slot when it lands;
the "Policy comparison card is empty" known-artifact bullet stays removed.
**Consequences:** Beat 4 is the eval dashboard's (Olga's) or the static chart; the ops dashboard is
Beats 1, 2 and 6 only. The paired-video design is the next Beat 4 conversation.

---

## D126 — Review batch, part 1: source hardening and documentation items (W-1, W-3, W-5, W-12, W-14, W-15, W-17, S-1, S-6)

**Date:** 2026-09-09 (unattended runner, operator-approved batch)
**Decision:**
- W-12: `coordinator.py` counts consecutive `/run_policy` goal rejections; at `REJECT_ESCALATE_N`
  (default 5) it logs at error level and cancels every goal on the server through
  `/run_policy/_action/cancel_goal` with an all-zero goal_info (D063's cheapest unwedge), then resets.
- W-17: the coordinator publishes its peak cube count on `/flywheel/episode_cubes` alongside the
  dataset ref before `end`; the emitter prefers it over its own poll, so the curator record and the
  bag keep/prune decision use one ground truth.
- S-1: `task_eval.read_cube_poses()` returns `None` when the gz query fails or yields no cube;
  `evaluate_task()` then returns `(False, None)`. The emitter records `cubes_placed: null` +
  `score_reason: sensor-unavailable` only when no ground-truth read succeeded all episode and the
  coordinator sent nothing; the curator rejects that as `sensor-unavailable` (Gate 2, new first check)
  rather than `task-failed`. The eval harness logs and treats an unavailable end snapshot as 0.
- W-1: a second DaemonSet `qemu-binfmt-arm64-host` (nodeSelector arm64, `--install amd64`) so the
  Fury's aarch64 SNO registers the amd64 handler; the original stays amd64→arm64. One schedules per
  cluster, the other stays pending with no pods. Server dry-run clean on the desktop.
- S-6: CatalogItem `2.0.0-ft160-rhem` moves to channel `rolled-back` with its readme stating PR #2/
  PR #3 (D074); the newest `stable` version is `2.0.0-ft160` = the Fleet's pin. Header invariant
  reworded to "newest stable version".
- W-3: `docker/zenoh-connect.json5` deleted (no live reference; entrypoints generate the session
  config inline). W-5: AI-assistance marker added to the 12 unmarked files; `tools/ci/check-ai-marker.sh`
  greps every tracked `*.py|*.sh|Dockerfile*` and exits non-zero on a miss (27/27 marked).
- W-14/W-15: runbook — the Applications tab's Healthy inherits `healthcheck.sh`'s blind spot; the
  headline eval numbers were measured at chunking 30/0.95 vs the deployed 100/0.5, re-measure pending.
**Not live:** W-12, W-17 and S-1 are baked into the runtime and sim images — they ride the next
Tekton build and the next host `docker/Dockerfile` build. The curator's `sensor-unavailable` gate
lands with the gitops/flywheel roll in the next batch commit.

---

## D127 — Review batch, part 2: the KFP signing path gets the Tekton path's checks (W-7, W-8)

**Date:** 2026-09-09 (unattended runner)
**Decision:** `pipeline/act_flywheel_pipeline.py`:
- W-7: the crane tarball and the cosign binary are SHA-256-checked against pinned release digests
  (`crane_sha256_{amd64,arm64}` from go-containerregistry v0.20.3's `checksums.txt`,
  `cosign_sha256_{amd64,arm64}` = the values `gitops/tekton/cosign-sign-task.yaml` already pins) before
  either runs; a mismatch aborts the component. Both are pipeline parameters beside the versions.
- W-8: `sign_modelcar` fails when `cosign sign` reports no Rekor entry (no more `-1`), then runs
  `cosign verify --key cosign.pub --rekor-url <rekor> --output json` with `SIGSTORE_REKOR_PUBLIC_KEY`
  pointed at the `rekor-public-key` ConfigMap (newly mounted at `/etc/rekor`) — pass/fail on exit
  code, and the returned `rekor_index` is the verified bundle's `logIndex` on the index itself, the
  same thing the Tekton task records. Without a Rekor URL the verify step is skipped and no bypass
  flag exists anywhere in the file (the D3 grep still returns nothing).
**Record:** compiled with kfp 2.17.0 and uploaded as DSP pipeline version
`v-202609092137-hardening` (`fc9b8161-925d-4772-bb1c-60aae1291559`) of pipeline `99ec0aab-…`; no run
started. The manifest-consumer selects the newest version on its next trigger, so the next
promotion exercises these checks for real.

---

## D128 — Review batch, part 3: the flywheel namespace roll (W-9, W-11, W-2, W-4, S-2, S-1 curator gate)

**Date:** 2026-09-09 (unattended runner; loop confirmed stopped before the push)
**Decision:**
- W-9: `manifest-consumer.py` verifies DSP's oauth-proxy cert against the service CA the pod
  mounts (`/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt`, confirmed present) instead
  of `CERT_NONE`; `sync_agent.py`'s SSL branch likewise (unverified context removed). `CONSUMER_CODE_REV`
  bumped so the pod rolls.
- W-11: SCC grants scoped per workload in `scc-rolebinding.yaml`: new `edge-kafka` ServiceAccount
  with a namespaced RoleBinding to `privileged`; the `default` SA drops privileged for a namespaced
  `hostmount-anyuid` binding — the admission controller was already admitting curator and
  rejected-mirror under `hostmount-anyuid` (verified on the live pods), so nothing loses a capability
  it used; the dashboard's binding becomes a RoleBinding. The cluster-wide `flywheel-privileged-scc`
  ClusterRoleBinding is pruned by Argo.
- W-2: `privileged` + root on the broker are kept and justified in the manifest: the log dir is a
  hostPath with the host's SELinux label, writable only from a privileged (spc_t) container — the
  same reason the local-path helper carries.
- W-4: the dashboard Role loses its unused `services` verb (Role-only change; no pod roll).
- S-2: `python:3.12-slim` pinned by digest (`sha256:78387bc3…`, the multi-arch manifest list resolved
  on the host) in curator, sync-agent and rejected-mirror; the dashboard still uses the tag (out of
  this finding's scope, same fix applies).
- S-1 (curator half): Gate 2 rejects `cubes_placed: null` as `sensor-unavailable` before the
  task-success check.
**Consequences:** one push, one broker restart (new SA), curator and sync-agent re-pulled on the
pinned digest; verified below in the runner's report.
**Verified live:** Argo `flywheel` Synced/Healthy on `98933ab` in ~75 s; curator, sync-agent, edge-kafka
and manifest-consumer rolled, all Ready with 0 restarts; SCC annotations curator/sync-agent
`hostmount-anyuid`, edge-kafka `privileged` (SA `edge-kafka`), manifest-consumer `restricted-v2`; the
`flywheel-privileged-scc` ClusterRoleBinding pruned; Kafka's GroupCoordinator stabilised the
`manifest-consumer` group on the restarted consumer (generation 17). One more grant turned up that git
never knew about: a hand-created RoleBinding `system:openshift:scc:privileged` (8 days old, the
`oc adm policy add-scc-to-user` form, no Argo tracking) still bound the `default` SA to privileged —
deleted live, since every default-SA pod was already admitted under `hostmount-anyuid`; `oc auth can-i
use scc/privileged --as=system:serviceaccount:flywheel:default` is now **no**, `edge-kafka` **yes**. Its
sibling `system:openshift:scc:hostmount-anyuid` (default + dashboard) duplicates the git-tracked bindings
and was left in place.

---

## D129 — Runtime image rebuilt in one clean Tekton run and re-pinned; C11/C12 now on the device path

**Date:** 2026-09-09
**Record:** PipelineRun `runtime-image-plt5s` on `ea513fa`: git-clone, build-and-push (amd64 + arm64),
sign, verify — all Succeeded in a single run, 20:29Z → 21:38Z. This is the "one clean end-to-end
run" D112 left open. Manifest list `sha256:02e66d895ed4ba328aa43263561027c18406d774887f465ab7acbd81e4c42d08`
(tag `act-inference-ea513fa`), platform digests arm64 `a7adc041…` / amd64 `6713857a…`, Rekor
14/15/16. Verified independently from the host with `cosign verify --rekor-url` +
`SIGSTORE_REKOR_PUBLIC_KEY` (claims validated, tlog existence verified, key verified; exit 0) and
`crane manifest` (both platforms).
**Decision:** Fleet `Image=` re-pinned to the new digest; the runbook's two digest references
updated to match. The image carries D113's eval-gate fix (C11), D118's name-keyed joint checks and
`sim_reset.py` parse fix (C12), and D115's emitter change is on the sim image already (D124-era
host rebuild, `sim-only` re-tagged, previous image kept as `sim-only-prev-20260904`). Expected
rollout: rv 7 → 8, container recreated, Healthy within ~4 min (D112's re-pin timing); modelcar
unchanged, so no modelcar pull.
**Consequences:** the sim (`so-arm-sim`) and the device now both run images built from `ea513fa`.
A 10-minute validation loop follows to exercise the whole path; the loop is stopped afterwards
and the disk guard stays armed. The `runtime-image-a4` PipelineRun and its 60 Gi PVC remain to
be deleted after the kit recording (inbox).

---

## D130 — Re-pin outcome and validation loop: device Healthy on the clean build; the rebuilt sim and runtime run the loop end to end

**Date:** 2026-09-09
**Record (re-pin, D129):** ResourceSync picked up `934371e`; device rv 7 → 8, `UpToDate`,
`applicationsSummary: Healthy` at +8:33 from the push (one RS poll + container recreate + health
start period); the app container's `ImageDigest` is `02e66d89…`; no modelcar pull.
**Record (validation loop, 10 min):** `run-coordinator.sh` started with no `MODEL_VERSION` and
followed the Fleet (`act-v2-ft160`, D113 verified live) on the new runtime digest. 13 episodes,
0 goal rejections, 0 errors; coordinator logged `Observed model_version: act-v2-ft160`; emitter
verdicts 12 FAIL / 1 SUCCESS with **no `INJECTED-FAIL`** (D115's removal is live on the sim
image); dashboard `flywheel_running: true`, counts 0 → 1 curated / 12 rejected / 1 sent, trigger
1/160; device stayed rv8 Healthy throughout. The 1-in-13 warm-up ratio equals D073's first-12-
minute window (1 curated / 13 rejected) — consistent, not a regression signal; the window is too
short to compare success rates. Loop stopped afterwards; disk guard resident.
**Decision:** C11 and C12 are closed as deployed, not just fixed in source; D112's "one clean
end-to-end run" is closed by `runtime-image-plt5s`.
**Consequences:** host root disk 110 G free with 319 bags after the loop — the guard parks the
loop at < 100 G or ≥ 330 bags, so a port + prune (`assemble_all.sh` → `prune_bags.py --yes`,
now lineage-aware) is due before the next longer loop (operator). Two finished PipelineRuns
(`runtime-image-a4`, `runtime-image-plt5s`) each hold a 60 Gi PVC until deleted (after the kit
recording, per the inbox).

---

## D131 — Review batch, part 4: the device trust policy fails closed (W-10)

**Date:** 2026-09-09 (unattended runner; run last, after the D-re-pin rollout to renderedVersion 8
and a 15-minute window with no collection loop)
**Context:** review W-10 — `policy.json`'s `default` was `insecureAcceptAnything`, so signature +
Rekor enforcement covered only the four enumerated entries and any other reference (renamed repo,
promotion typo, mirror, docker.io) would pull unsigned without an error; D091's two negative tests
never exercised the default branch.
**Decision:** `gitops/rhem/fleet-act-inference.yaml`'s inline `policy.json` now has `"default":
[{"type": "reject"}]`; the enumerated entries are unchanged (`registry.access.redhat.com`,
`registry.redhat.io` with the RHEL keys; `quay.io/jary/soarm-act-modelcar`, `quay.io/jary/
soarm-flywheel` with `cosign.pub` + `rekor.pub` and `signedIdentity: matchRepository`); the file's
trust-chain header says so. Commit `366ac14`.
**Record:** ResourceSync → device renderedVersion 9, `UpToDate` / `Healthy` at +105 s; the device's
own `/etc/containers/policy.json` reads `default: reject`. Same session on the device (verbatim in
`docs/eval-records/negative-trust-tests.md` Case 4): `docker.io/library/alpine:3.20` → `Source image
rejected: Running image docker://alpine:3.20 is rejected by policy.` (exit 125); Cases 1 and 2 still
rejected with their D091 strings; the pinned modelcar digest still pulls and stores its signature
(exit 0); device Healthy afterwards. The first attempt hit the desktop's flightctl port-forward
re-establishing (`127.0.0.1:3443 connection refused`, the runbook's known artifact) — retried, not
a device fault.
**Residual:** the "right signature, wrong repository" artifact
(`quay.io/jary/soarm-flywheel-negtest:policy-default-2026-09-09`, the modelcar's bytes copied
unsigned) was created but quay made the repository private, so its pull fails on authorization
before policy — an operator action (make the repository public in quay) turns it into the standing
Case 4b; expected result `rejected by policy`.
**Consequences:** every registry the device may pull from is now an explicit allow-list entry; adding
a registry (a mirror on the Fury, for example) is a Fleet edit, reviewed like a promotion.

---

## D132 — Loop bags archived to the media HDD; paired A/B episode video recorded for Beat 4 (v1 vs v2, same seed)

**Date:** 2026-09-10
**Context:** the operator asked for a real same-scene A/B (v1 teacher vs v2 fine-tuned) video for the
dashboard's Beat 4, replacing the removed ladder-panel stopgap (D125). Two prerequisites surfaced:
the host root disk was at 99 GB free (below the loop guard's 100 GB floor) with 319 loop bags
(485 GB), and the eval harness records nothing.
**Bag archive:** all 319 bags (485 GB) moved to `/media/jary/videos/flywheel-bags-archive/
loop-bags-2026-09-04_09` — the same NTFS HDD the D062 proof bags live on. Two gotchas, both handled:
`rsync -a` fails on that fuseblk mount (it rejects ownership/time ops — `mkstemp: Operation not
permitted`), so the copy uses `rsync -rlD --no-perms --no-owner --no-group --no-times --size-only`;
and `--remove-source-files` couldn't delete the source because the bags are root-owned (written by
the recorder container), so after a byte-for-byte verification (total bytes + per-file name/size
manifest md5 identical, 520,057,530,590 bytes each side) the source was deleted via a root
container (`find /bags -mindepth 1 -delete`). Root: 98 GB → 583 GB free. The bags stay re-portable
from the archive by pointing `--bags-root` there.
**Recording mechanism:** `coordinator.py` `run_eval` gains optional `EVAL_SEEDS` (an explicit seed
list) and, when `RECORD=true`, records each attempt (mirrors `run_forever`'s recorder-availability
setup — the missing piece: `_recording_available` was only set in `run_forever`, so the first cut
recorded nothing) and stamps `bag_path` into each result row. Built as a one-layer overlay image
(`act-inference:eval-record`, `FROM` the deployed runtime + `COPY coordinator.py`).
**The RHEM-era conflict, and how it was resolved:** a host eval container (ROLE=all, local policy)
registers a second `/run_policy` on the sim's zenoh graph, which collides with the RHEM device that
is *always* serving the policy into that same sim — every goal is rejected (`/rosetta_client/
change_state` timeout). This is new since Phase 3 (pre-RHEM there was no device). Resolved by
suspending the device VM for the recording window (`virsh suspend act-device`, operator — needs
sudo; jary can't control the system domains), which detaches its policy from the sim. Confirmed no
impact on Olga's eval dashboard (APPENG-6295): her sources are the SNO hub (Kafka/MinIO, a separate
VM) and the loop was stopped, so no live flow. The sim had also stalled (~2.6 h stale camera) and
was restarted. After recording, `virsh resume` + the D063 recovery (chrony resync stepped the
~27-min clock skew, policy container restarted to reconnect to the restarted sim) returned the
device to Online / UpToDate / Healthy / rv9.
**Recording result (at the deployed 100/0.5 chunking):** v1 and v2 each ran seeds 1002/1019/1040/
1024 (+ a warmup) with recording. Paired outcomes: **seed 1002 and 1019 both v1 1/3 ✗ → v2 3/3 ✓**
(two vivid same-scene fixes); 1040 both-fail, 1024 both-pass. Note (W-15): these per-seed outcomes
differ from the 30/0.95 eval records the seeds were chosen from — the deployed 100/0.5 config
genuinely behaves differently per scene; no clean v1✓→v2✗ "broke" example landed on these seeds
(the ladder chart still carries the honest aggregate 20-fixed/7-broken story). Bags ported to
overhead-camera mp4 via `rosetta.port_bags` (staged with a *copied* mcap, not a symlink — a symlink
to an unmounted host path dangles in the port container) and uploaded to MinIO
`episodes-data/paired-ab/` (manifest + 4 clips, ~2–5 MB each). The dashboard card that serves them
is a separate change.
**Consequences:** `coordinator.py`'s `EVAL_SEEDS`/eval-recording is committed here but, like the
other coordinator changes, is only live in a host overlay image — not in the device's Tekton
runtime until the next build. Cleanup pending on the host: the recorded bags (~20 GB in
`~/flywheel-data/bags`), the `ab-stage-*`/`ab-upload-*` temp dirs and `ab-datasets/`, and the
`act-inference:eval-record` image (inbox). The C/D extension (ladder rungs as more columns) and a
"broke" counterexample need another exclusive-sim window; deferred.

---

## D133 — Beat 4 paired A/B video card on the ops dashboard (served from MinIO)

**Date:** 2026-09-10
**Context:** the Beat-4 "policy improvement" story needed a real same-scene A/B, not the static
ladder chart. Live-recorded clips (v1 teacher vs v2 act-v2-ft160 on identical seeds, overhead
camera) were ported to mp4 and uploaded to MinIO `episodes-data/paired-ab/` with a manifest.
**Decision:** `gitops/flywheel/dashboard.yaml` gains a paired-video card. Two routes in
`dashboard.py`, backed by a read-only boto3 MinIO client: `GET /api/paired` streams
`paired-ab/manifest.json`; `GET /api/paired/<seed>/<policy>.mp4` streams the clip after
validating `seed` (digits) and `policy` (`v1`/`v2`) — with single-range (206) support and a
graceful 404 when MinIO/the object is unavailable. The card (two `<video>` players, a seed
selector, per-policy outcome badges, a "play both" button) fetches the manifest on load and stays
hidden if it 404s. MinIO endpoint `minio.minio.svc:9000`; credentials from the existing
`hub-credentials` Secret (`s3-access-key`/`s3-secret-key`) as env, never logged. `boto3` added
to the pod's pip install; `DASHBOARD_CODE_REV` bumped so Argo rolls the pod.
**Consequences:** live/desktop card only — at the booth (no cluster) Beat 4 still falls back to the
static chart. The featured seeds (1002, 1019) each show v1 1/3 fail vs v2 3/3 success on the
identical scene, measured at the deployed 100/0.5 chunking.

---

## D134 — RHOAI dashboard re-enabled; the rest of the RHOAI component set audited (deliberately off)

**Date:** 2026-09-11
**Context:** the operator noticed there was no RHOAI UI on the cluster and asked whether we should
run it to see pipeline results, and what other RHOAI integration was overlooked. Audited the live
DSC: Managed = `datasciencepipelines`, `kserve` (RawDeployment, `serving: Removed`), `modelregistry`;
Removed = `dashboard`, `workbenches`, `modelmeshserving`, `ray`, `kueue`, `codeflare`,
`trainingoperator`, `trustyai` (DSCI `monitoring`/`serviceMesh` also Removed). The dashboard was
Removed during the Phase 4.5 build crunch (D084, node ~99% CPU-requested); runs were read via the
kfp client / terminal.
**Decision:** re-enable `dashboard` (`Managed`) — the node recovered to ~67% CPU / 37% mem after the
KFP-pod cleanup and act-serving retirement, so it fits. It gives the DSP run DAG/logs (Beat 3) and
the Model Registry UI (Beat 6) as real product screens instead of terminal/curl. For the DSP runs to
appear, `flywheel` had to be labeled a Data Science Project (`opendatahub.io/dashboard: "true"` on
the namespace — added to `gitops/flywheel/namespace.yaml` and applied live); without it the
dashboard lists no pipeline server for the namespace. Route:
`rhods-dashboard-redhat-ods-applications.apps.sno-flywheel.local` (OpenShift OAuth); added to the
runbook cheat-sheet, the Mac `/etc/hosts` line, and Beat 3 (the "no graphical run view" caveat is
replaced). 2/2 dashboard pods Ready; route serving (403 unauthenticated = oauth-proxy gating).
Reversible (set back to Removed).
**The rest, and why they stay off (not overlooked):**
- `kserve` serving + `modelmeshserving` — the model serves at the **edge via RHEM** (D024), not
  in-cluster; kserve is Managed only for the modelcar format/CRDs, serving nothing.
- `ray`/`codeflare`/`kueue`/`trainingoperator` — training is a single-GPU LeRobot fine-tune;
  distributed training is at most a GB300 stretch (PROJECT-BRIEF), not core.
- `trustyai` — hooks KServe-served models for monitoring/bias; N/A while serving is at the edge.
  The one genuine "new scope" option if post-deployment model monitoring is ever wanted.
- `workbenches` — Jupyter; not needed (could optionally host exploratory/eval work).
- DSCI `monitoring` — RHOAI's own metrics; we use Perses/Tempo instead.
**Consequences:** dashboard enable committed as `043b852`; namespace label + runbook here. Visual
confirmation (the flywheel project's pipeline runs rendering in the UI) is the operator's browser
check — the cluster-side mechanism (dashboard Managed + pods Ready + route + DS-project label +
live DSPA) is in place.

---

## D135 — ACM (integrated-console RHEM) prepared under GitOps but NOT installed: the SNO has no room

**Date:** 2026-09-11
**Context:** the operator chose the product path — run RHEM as the RHACM **edge-manager** component
so fleets/devices appear inside the OpenShift console (as thor-testing had it), rather than the
standalone `flightctl` UI route we run now. ACM 2.17 (`advanced-cluster-management`, channel
`release-2.17`, CSV v2.17.1) and its MCE dependency are in the catalog. Mechanism (RHACM docs,
2.13-era, to re-validate on 2.17): enable the `edge-manager-preview` component in the
`MultiClusterHub` CR, then add the flightctl console plugin to `console.operator/cluster`.
**Resource finding (the blocker):** baselined the node before installing anything — it is at **87%
CPU requested (13499m of 15500m allocatable), ~2 cores free**, memory 46%. That is up from ~67%
earlier today because the RHOAI dashboard (D134) was enabled (`redhat-ods-applications` is now the
top requester at 3410m; then `flightctl` 2412m, `openshift-gitops` 1875m, `flywheel` 1615m). ACM's
MultiClusterHub base needs ~4+ CPU cores. It does not fit on the current 16-vCPU VM; forcing it
would leave MCH components `Pending` and risk starving the running demo (RHOAI/RHTAS/pipelines/
MinIO/Kafka/flywheel). Per the resource guardrail, **nothing was applied live.**
**Decision:** staged the install under GitOps, unapplied: `gitops/acm/operator.yaml` (Namespace +
OperatorGroup + Subscription), `gitops/acm/multiclusterhub.yaml` (MCH with `edge-manager-preview`
enabled and heavy components trimmed), `gitops/acm/README.md` (apply order + the migration phase),
`argocd/acm-app.yaml` (manual-sync, finalizer; deliberately no `automated:` block). The standalone
`flightctl` (`argocd/rhem-app.yaml`) and the enrolled `act-device` are untouched and stay live.
**Operator action required:** increase the SNO VM vCPU (≈16 → 24) and restart it (host `virsh`/
sudo + SNO reboot — only the operator can do this) so the node has ~10 cores free. Then apply
`argocd/acm-app.yaml`, validate the edge-manager component name on 2.17, bring up the MCH watching
CPU, enable the console plugin. Migration to ACM's flightctl (re-enroll `act-device`, recreate
Fleet/Catalog) and retirement of the standalone flightctl is a **later phase**, only after the ACM
edge-manager is proven — the two coexist during migration, so peak demand needs the bigger node.
**Consequences:** no change to the running cluster; the integrated-console RHEM is one VM resize +
`oc apply` away, reproducibly. If the resize isn't wanted before ROSCon, the standalone flightctl
UI remains the working (capability-complete) view.

### D135 addendum (2026-09-11) — ACM backed out: 2.17 dropped RHEM

ACM 2.17 was installed to test the integrated-console RHEM path, but the MultiClusterHub validating
webhook **rejected `edge-manager-preview`** ("not a known component") — Edge Manager has graduated
out of RHACM into the standalone **Red Hat Edge Manager** product, so ACM is the wrong vehicle and
gets us nothing for RHEM. The maxPods stall (node hit kubelet's 250-pod cap; ACM added ~147 pods on
top of ~176) was all ACM add-ons we never needed. Backed out cleanly: deleted the MultiClusterHub
(finalizer-stuck on the `local-cluster` ManagedCluster / klusterlet / MCE chain — force-cleared
finalizers since we were removing it), removed the ACM operator (CSV + OperatorGroup + Subscription),
deleted all `open-cluster-management*` / `multicluster-engine` / `local-cluster` namespaces, and
removed the `acm`/`mce` entries from `console.operator/cluster` `spec.plugins` (back to
networking+monitoring). Removed `gitops/acm/` and `argocd/acm-app.yaml` from git. The standalone
flightctl and the enrolled device were untouched throughout. **Next:** the integrated console comes
from the standalone RHEM's own `flightctl-plugin` ConsolePlugin — productized chart
charts.openshift.io `flightctl` 1.0.2 — layered on the flightctl/RHEM install we KEEP; no ACM.

## D136 — Integrated-console RHEM deferred; keep the standalone install; loop restarted

**Date:** 2026-09-11
**Context:** pursuing D135's "next" — layer the productized RHEM chart's `flightctl-plugin`
ConsolePlugin onto the install we keep. Inspected the 1.0.2 chart template
(`templates/ui/flightctl-ui-console-plugin.yaml`): the ConsolePlugin is **gated on
`enableMulticlusterExtensions == "true"`** (or `"auto"` with the MultiClusterEngine CRD present).
The planned swap set it `"false"` ("we're not on ACM"), under which **no ConsolePlugin renders at
all** — so the swap would have torn down the proven 1.3.0 install and still yielded only a
standalone UI. Converging evidence (ACM 2.17 dropped edge-manager per D135; the standalone chart
gates its console plugin behind the multicluster flag; the plugin displayName is "FCTL Plugin", not
"Red Hat Edge Manager") indicates a **cleanly-supported, RHEM-branded, in-console experience does
not exist at the versions we have** — it reads as an ACM 2.13–2.15-era capability the current
split has pulled apart. Remaining paths (force the flag off-label standalone; reinstall MCE-only to
satisfy `"auto"`; keep standalone; defer) are all compromises.
**Decision:** **keep the working standalone RHEM 1.3.0 install as-is** (UI route + enrolled
`act-device` intact), defer the integrated-console pursuit. No teardown; `argocd/rhem-app.yaml`
already points at the 1.3.0 standalone, so **no repo change was needed**. Revisit when the RHEM
productization/console-plugin story settles (or if MCE-only is later judged worth the weight).
**Also:** restarted the flywheel collection loop on the desktop host — `act-coordinator` from the
Tekton-signed runtime digest `sha256:02e66d89…`, `MODEL_VERSION=act-v2-ft160` (matches the Fleet;
`Observed model_version` clean, no D073 mismatch). Preflight green: disk-guard resident, 540 GB
free, no prior coordinator (D057), sim/pose up, zenoh on 7447. Verified live: recorder activated,
device policy action server reachable, episodes recording. First loop run on the Tekton digest
(prior runs were the interim `2ad1fb1c…`).
**Consequences:** the demo shows the standalone RHEM UI (capability-complete), not the OCP-console
plugin. flightctl CLI token is expired (401) — device-management view only; re-login is a
browser-OAuth step (`flightctl login --web`) for the operator, does not affect the loop.

## D137 — sdb1 mounted, flywheel-data relocated off the root disk (D061 root cause fixed)

**Date:** 2026-09-11
**Context:** the root disk (`/dev/nvme1n1p2`, 1.8 TB) held BOTH the raw episode bags
(`~/flywheel-data`) and the qcow2 images of the `sno-flywheel`/`act-device` VMs, so a bag flood
paused MinIO/Kafka/the sim together — the D061 outage mechanism (9/09) and the D132 brush with the
floor. `disk-guard.sh` only reacts (stops recording at the 100 GB floor), never reclaims. The
4.5 TB ext4 `sdb1` had sat unmounted as the `flywheel-mount-sdb1-relocate-data` inbox todo.
**Decision:** mounted `sdb1` (by UUID) at `~/flywheel-data` via
`/etc/fstab` (`defaults,noatime,nofail`) and relocated all 231 GB of flywheel-data onto it — now
253 GB used / 4.3 TB free. **New bags land on the 4.5 TB disk, no longer competing with the VM
qcow2 files** — the structural fix D061 flagged as option 3. All script paths stay `~/flywheel-data`
(mounted over), so no code change. Kept `~/flywheel-data.old` (231 GB, on root) as rollback until a
full loop cycle validates the new mount, then `sudo rm -rf` reclaims it (root ~85% → ~72%).
**Gotcha (root cause of a mid-op scare):** `sdb1` carried a label and was NOT blank —
GNOME had auto-mounted it under `/media`, so the first explicit `mount /dev/sdb1 /mnt/flywheel-new`
silently missed and `rsync` wrote 231 GB to a root-disk folder instead of the disk. Caught by the
recovery script's mountpoint/device asserts (`findmnt … == /dev/sdb1`, SRC≠DST filesystem) before
any delete; corrected by copying `~/flywheel-data.old` onto the correctly-mounted disk. The drive's
pre-existing 23 GB `models/` was moved aside to `~/flywheel-data/_preexisting-20260911T183207Z/`,
not merged. Also hit: running the recovery script under `sudo` made `$HOME=/root`; fixed by running
as the user. Lesson: verify a mount actually took (mountpoint check) before rsyncing, or a failed
mount writes to the underlying dir.
**Consequences:** the D061-class outage can't recur the same way (bags off the VM disk).
`disk-guard.sh` stays as the floor. The weekend re-baseline runner's `disk-drain.sh`
`HIGH_WATER_GB=300` was calibrated for the old shared 1.8 TB disk and should be retuned before it is
armed. Follow-up: optionally neuter the GNOME auto-mount so it can't re-grab sdb1 on boot (fstab is
authoritative now).

## D138 — Standalone MinIO/Kafka for Olga while SNO was down, and a fresh clean paired-eval (r2) to replace contaminated comparison data

**Date:** 2026-09-13/14
**Context:** the SNO VM was shut down over the weekend to free the desktop's RAM/cores for other GPU
work. Olga's eval dashboard (APPENG-6295) reads MinIO + Kafka off the SNO node IP `10.0.0.49`
(NodePorts 30900 / 30903), which die with the VM. Separately, v2's *operational* curated data was
contaminated — after a cut-over the sim stopped re-randomising cubes, so episodes inherited
tray-placed cubes and logged inflated "successes" (the D2155-area finding) — making the v1/v2
comparison unfair. The fair method is the eval harness (homes the arm each episode, pins the scene),
not the loop's operational rate.
**Decision A — standalone data plane for Olga (no cluster):** stood up plain host containers
`olga-minio` (`quay.io/minio/minio`, digest-pinned to the cluster's) and `olga-kafka`
(`quay.io/strimzi/kafka:0.45.0-kafka-3.9.0`, KRaft) via `~/olga-stack/up.sh`, mirrored Olga's two
buckets + Kafka log dir off the (still-up) cluster, recreated the scoped `olga-readonly` user, and
**gave the host the freed `10.0.0.49` as a br0 secondary IP** so her endpoints/ports are byte-for-byte
unchanged (the desktop already routes that range for remote users, so her tailnet
traffic terminates there). Creds live in mode-600 files under `~/olga-stack/`, never printed.
Kafka's external listener hard-codes `advertised.listeners=10.0.0.49:30903`, so the standalone maps
host `10.0.0.49:30903 → container 9094` to match.
**Decision B — fresh clean paired-eval (r2):** ran teacher (v1, `upstream-act-teacher`) vs
`act-v2-ft160` (v2) on the **same seeds**, homed arm, pinned `RANDOM_RADIUS=0.03`, **served locally
on the RTX 5090** (`ROLE=all POLICY_DEVICE=cuda`, device off — no `/run_policy` collision, D132) via
`tools/host/local/paired-eval-shifted.sh` wrapped by `~/olga-stack/r2/run-r2.sh` (resumable, chunked).
Started 160, appended to **360 each** in one continuous re-run (resume skips finished chunks).
**Result (360 paired): teacher 81.9% (295/360), v2 92.5% (333/360); fixed=57 broken=19 net=+38
sign_p=0.0000 verdict=PASS** (D022 rule). Report at `~/flywheel-data/eval/r2-clean/paired-report.md`.
**Bridge to the dashboard's contract:** `~/olga-stack/r2/explode.py` converts the eval JSON's
`episodes[]` into the dashboard's per-episode record schema — clean labels (NOT `eval-*`, which the
dashboard drops), `task_success`→`curation_verdict` pass/reject, `rollout.{steps,duration_s}` — and
lands them in **new isolated buckets `episodes-curated-r2` / `episodes-rejected-r2`** (both pass and
reject per version, so the dashboard's success rate isn't hidden). Olga points her dashboard at those
two buckets (2 env vars) + `versions.yaml` (`upstream-act-teacher: 0`, `act-v2-ft160: 160`); old
buckets untouched. Final clean state: 628 curated + 92 rejected = **720**.
**Gotchas (all fixed):** (1) the teacher HF-cache snapshot's files are **symlinks into `../../blobs/`**,
which dangle when only the snapshot dir is bind-mounted — materialized a flat copy at
`~/olga-stack/r2/teacher-ckpt` via `cp -rL`. (2) Re-run seeds (1150–1159) **flipped outcome** between
runs, leaving stale duplicate object copies across buckets (723 vs 720); reconciled by re-exploding
from the authoritative raw chunk files into `records-final` and `mc mirror --overwrite --remove`.
(3) `pkill -f run-r2.sh` matched its **own** SSH command line → self-kill; and `ssh -n` + a heredoc
silently no-ops (stdin is `/dev/null`). (4) the `minio/mc` image lacks `grep`/`awk`, and parens in an
`echo` break its `sh`. **Fallback preserved:** `~/olga-stack/r2/records.bak160` (the verified 160-each
state) + `r2-clean.bak160`.

### D138 addendum — reclaim ordering (bring SNO + the loop back; tear the standalone down)

The r2 eval data lives **only** in the standalone MinIO, so the reclaim must preserve it. Order
(⚠️ the IP release must precede the VM start, or node and host fight over `10.0.0.49`):
1. r2 data is already on disk (`~/olga-stack/r2/records-final`, 720) — no export needed.
2. Stop `olga-minio` + `olga-kafka` (docker).
3. **`sudo ip addr del 10.0.0.49/24 dev br0`** (operator), then **`sudo virsh start sno-flywheel`**
   (+ `act-device` for the loop). Wait ~10–15 min for the single node to stabilise.
4. Re-create `episodes-curated-r2` / `episodes-rejected-r2` in the **cluster** MinIO and upload
   `records-final`; extend the cluster `olga-readonly` policy to those buckets — so Olga's endpoint
   (`10.0.0.49:30900`) and data are unchanged, now served by the cluster.
5. Bring the collection loop back (device clock-step after suspend per D132/D063, sim already up,
   `run-coordinator.sh` on the Tekton digest, disk-guard first).

## D139 — Fury pre-flight re-check: Phase 0 amended, no locked decision changed

**Date:** 2026-09-18
**Context:** `docs/internal/FURY-PLAN.md` was written from a read-only inventory of the real HP ZGX
Fury earlier the same day. Before the first privileged step, the box was re-inventoried (read-only,
21:10 UTC) to catch drift — the machine is shared.
**Decision:** the thirteen locked decisions stand. Phase 0 is amended in six places:
1. **The blank data disk is addressed by identity, not by name.** The blank 3.7 TB disk and the
   staged `models` disk are the same Samsung model; `nvme0n1`/`nvme1n1` are enumeration order and may
   swap across a reboot. Step 2 resolves the blank disk through `/dev/disk/by-id` (by serial) and
   requires `wipefs -n` to print nothing before `mkfs`. Same class of mistake as D137
   (a mount that silently missed) — assert the target before the destructive step.
2. **`kernel-64k-modules-extra` joins the package step.** RHEL 10 ships `xt_mark` in
   `kernel-modules-extra`; the image has it only for the 4k `211.49.1` kernel, not for the running
   64k `211.56.1`. tailscaled (iptables-nft mode) already reports its `ts-forward` MARK rule failing,
   which would break the `10.20.0.0/24` subnet router (decision 7/8). The matching package is in
   BaseOS. Fallback if it still fails: `TS_DEBUG_FIREWALL_MODE=nftables`. Tracked as unknown 9.
3. **dnsmasq answers `sno-flywheel.local` authoritatively and serves `api-int`.** The host's resolver
   is MagicDNS (`/etc/resolv.conf` is Tailscale's); split DNS sends `sno-flywheel.local` back to this
   host, so a name dnsmasq does not know would loop host → MagicDNS → host. `local=/sno-flywheel.local/`
   closes it. `api-int.sno-flywheel.local` is added because the SNO node resolves it during install.
4. **The BMC address is not recorded in git** — the repo is public. The operator holds it.
5. **`nvidia-container-toolkit` comes from Red Hat, not from NVIDIA's repo.** `1.20.0-1` (the version
   `device/provision.sh` pins) is in `rhel-10-for-aarch64-supplementary-rpms`, already enabled on the
   box, alongside the driver and its precompiled kmods. Step 3 installs it from there and adds no
   third-party repo. Follow-up for Phase 4: `provision.sh` still drops NVIDIA's `.repo` file
   unconditionally — make it skip that when the pinned version is already installable or installed.
6. **Modular libvirt daemons, not `libvirtd`.** RHEL 10.2 still ships the monolithic unit, but the
   modular sockets are the default and the two must not both be enabled. `mig-config.service` orders
   `Before=virtqemud.service`.
**Also learned:** the lab uplink's prefix had been recorded wrongly (corrected). `gdm` is already disabled; no suspend attempt since
20:21:41 UTC, after the masks. Decision 5's 4k fallback is cheap: precompiled `kmod-nvidia-open` is
installed for both page sizes of both kernel versions, and `dnf-plugin-nvidia` filters kernel updates
that lack one. Unknown 3 is retired: `flightctl-agent-1.3.0-1.el10.aarch64` and
`nvidia-container-toolkit-1.20.0-1.aarch64` both exist in their repos. A 2.7 GB USB mass-storage
gadget (`sda`, RHEL 10.2 BaseOS ISO) is BMC virtual media — left alone.
**Gotcha:** an unprivileged `dnf list` over a `bash -s` heredoc prompted to import a repo GPG key
and consumed the rest of the script as its answers (nothing was imported). Give `dnf`/`curl`
`</dev/null` inside piped scripts, or use `ssh -n` with a quoted command. The known-hosts entry is
under the node's name, so by-IP SSH needs `-o HostKeyAlias=<that name>` rather than relaxing host-key checks.
**Consequences:** Phase 0 step 2 cannot hit the `models` disk by name drift; step 6 has a working
forwarding path to verify rather than a known-broken one.

### D139 addendum — the host's hostname is left unset in Phase 0

Step 1 originally set the hostname to the machine's tailnet name. Checked before running it: nothing in `device/`,
`tools/host/` or the Fleet reads the host's name; the tailnet name is pinned in Tailscale's prefs
(a pinned `Hostname`), independent of the OS; no X session or vendor agent is keyed on it. So it is
cosmetic — and it has two side effects on a machine that is HP's and shared: NetworkManager would
start sending the name to the lab's DHCP server (`dhcp-send-hostname` is at its default), and with
`hosts: files dns myhostname` plus no `/etc/hosts` entry the box would resolve its own name through
MagicDNS first, stalling `sudo` and friends whenever tailscaled is down — exactly during step 6.
Deferred to just before Phase 4 (where the name becomes visible in the RHEM UI), with HP's agreement
and an `/etc/hosts` line. Reversible at any time with `hostnamectl set-hostname ""`.

## D140 — The Phase 0 reboot stalled in the initramfs on the BMC's virtual media, not on anything Phase 0 changed

**Date:** 2026-09-18
**Context:** the step 4 reboot (22:20 UTC) did not come back: no SSH, no tailnet, KVM showing a black
screen with a cursor, SOL refusing to connect. A Ctrl+Alt+Del from the KVM at 22:38 rebooted it (so the
OS was alive), the second boot was force-restarted from the BMC at ~22:46, and the third came up at
22:52 with no intervention at GRUB.
**Finding:** on the boot that came up, `systemd-analyze` reads 10.7 s kernel + **3 min 43 s initrd** +
24.7 s userspace. Inside the initramfs, `dracut-initqueue` logs "Timed out while waiting for udev queue
to empty" at 166 s and only scans LVM (finding root at once) at 232 s. What udev is stuck on is the
BMC's virtual-media USB disk (`OpenBMC Virtual Media Device`, the RHEL 10.2 installer ISO left attached
since the install): the kernel resets it 10 s after it attaches and eight times during the boot, with
an I/O error. The initramfs for this kernel is dated 18:09, before any Phase 0 work, and the 18:45
boot had already spent ~2 min 20 s before userspace. So the stall predates Phase 0 and its length
depends on how the BMC serves that image. BMC POST codes confirm the firmware side was identical to
the day's good boots (ReadyToBoot to ExitBootServices in 4.4 s, which also rules out having booted
the installer).
**What Phase 0 added to boot:** everything it changed runs after the root switch, and that whole phase
took 24.7 s. `mig-config.service` ran, the four MIG devices came back **with the same UUIDs**, `/data`
and the container-storage bind mounted. The early suspicion of the MIG unit was wrong.
**Not proven:** why the first boot sat for 17 minutes. journald on this image is volatile
(`/var/log/journal` does not exist), so the failed boots left no logs.
**Decision:** no further reboots until the virtual media is ejected (the BMC and the image belong to the machine's owner:
ask, then eject under Operations -> Virtual media). Make journald persistent so the next incident
leaves evidence. SOL is unusable ("Connection closed unexpectedly"); the KVM is the console, and
because the kernel's console is serial-only the KVM shows nothing during boot unless `console=tty0`
is added at GRUB.
**Lesson:** on a remote box, read `systemd-analyze` and `journalctl --list-boots` *before* the first
planned reboot. A two-minute initrd and a one-boot journal were both visible beforehand.

### D140 addendum — the virtual media stays attached for now (operator's call)

D140 said no reboots until the BMC's virtual media is ejected. The operator overrode that the same
evening: once ejected it cannot be re-attached from outside the lab. So it stays. Consequences accepted: every boot spends minutes in the initramfs
(3 min 43 s measured; 17+ min seen once, cause unproven), so reboots are kept to the necessary ones,
given 5-20 minutes before anyone worries, and watched through the KVM with `console=tty0` added at
GRUB. If it bites a second time in a way that is verified, the OS-side mitigation is to make the
kernel ignore that one USB storage device, which needs no BMC change. The image itself is 2.74 GiB -
neither the stock boot image nor the stock DVD;
`tools/host/fury/copy-vmedia.sh` exists to take a checked copy to `/data/iso/`, but the operator
chose not to run it: no copy has been made. Revisit only if a boot problem recurs.

## D141 — Fury Phase 0 closed: what was built, and what differs from the plan as written

**Date:** 2026-09-18
**Context:** Phase 0 of `docs/internal/FURY-PLAN.md` (host preparation) was run by the operator from
scripts in `tools/host/fury/`, one step at a time, each checked before the next.
**State of the host:** boots to `multi-user.target`, sleep targets masked, `gdm` and
`nvidia-fabricmanager` off; hostname left unset (D139 addendum). `/data` is ext4 on the blank 3.7 TB
NVMe, mounted by label, with rootful container storage bind-mounted onto it. libvirt (modular
daemons), `nvidia-container-toolkit` 1.20.0 from RHEL Supplementary, `kernel-64k-modules-extra`.
MIG `9,19,19,19`: `0:0` = 3g.126gb, `0:1`-`0:3` = 1g.31gb; restored at boot by `mig-config.service`;
MIG UUIDs are identical across a reboot. `fury-net` (routed, `virbr-fury`, host `10.20.0.1`), libvirt's
`default` network stopped. dnsmasq serves the cluster zone on `lo`, `virbr-fury` and `tailscale0`.
Tailscale advertises `10.20.0.0/24`; split DNS sends `sno-flywheel.local` to the host.
**Differences from the plan as first written, all exercised:**
1. **The CDI spec belongs to the toolkit.** Red Hat's build enables `nvidia-cdi-refresh`, which writes
   `/var/run/cdi/nvidia.yaml` at boot, and that directory outranks `/etc/cdi`. `mig-config.service`
   orders itself before it; no `/etc/cdi` file is written. After a manual reslice:
   `systemctl restart nvidia-cdi-refresh`. The spec carries index names and MIG-UUID names.
2. **The host resolves through its own dnsmasq** (`/etc/resolv.conf` -> `127.0.0.1`, NetworkManager
   `rc-manager=unmanaged`, `tailscale set --accept-dns=false` on the Fury only). Cluster names resolve
   on the host without the tailnet's control plane, and NetworkManager and Tailscale no longer take
   turns rewriting the file. Decision 8 is unchanged for guests and laptops.
3. **Guest -> host traffic is filtered by libvirt's `libvirt-to-host` firewalld policy** (reject, with
   a short allow list that includes dns). Anything a guest must reach on `10.20.0.1` — the Zenoh
   router, the camera stream (D124), a DCGM exporter — needs its port added to that policy in the
   phase that introduces it. Inbound to the routed network and guests outbound are already accepted
   (`libvirt-routed-in` / `-out`), and outbound leaves through the masquerade on the `public` zone.
4. **Containers reach a MIG slice while still SELinux-confined** (`container_use_devices` off) — shown
   for NVML only (`nvidia-smi -L`). Whether a CUDA workload also does is Phase 1.1's question, and
   decides whether the Fleet's `SecurityLabelDisable=true` can go.
**Also learned:** `.local` works through Tailscale split DNS on macOS. The operator's Mac pins ~17
`*.apps.sno-flywheel.local` names to the desktop cluster in `/etc/hosts`, which beats DNS; they must
be commented out to use the Fury cluster by name — one cluster per name at a time (decision 8). The
tailnet path to the Fury is relayed (about 100 ms), so bulk data should be pulled by the Fury from
registries, not pushed through the tailnet. Boot hazard and its handling: D140.
**Unknowns retired:** 3 (aarch64 rpms) and 9 (subnet routing on the 64k kernel).

## D142 — On the Fury, MIG and a working sim are mutually exclusive; measured, with options (decision 1 needs the operator's call)

**Date:** 2026-09-19
**Context:** Phase 1 on the real machine. The arm64 runtime image, CUDA on a MIG slice, the 64k-page
kernel, SELinux confinement, the native sim build and the whole ROS 2 + Zenoh + policy stack all work
(unknowns 1 and 2 retired). The first seeded eval (D020 config, seeds 1000-1004, `act-v2-ft160`) then
scored **0/5, mean 0.4 cubes** where the desktop scores 92.5 %.
**Finding:** Gazebo's cameras (ogre2, OpenGL, with voxel global illumination in the upstream world) need a
graphics API. NVIDIA's MIG guide: "No graphics APIs are supported (for example, OpenGL, Vulkan and so on).
The exception to this is RTX Pro 6000 Blackwell GPUs…"; `+gfx` profiles are "new in GB20X" and data-centre
Blackwell has none; MIG mode is per GPU and "without creating GPU instances… CUDA workloads cannot be
run". The Fury has one NVIDIA GPU, no add-in card (NVIDIA's DGX Station design puts display on a PCIe
add-in GPU; none is fitted in this unit), and the BMC's ASPEED chip is 2D only. Reproduced on the
box with a control: same container, same injected NVIDIA graphics libraries — with MIG on, EGL offers
only Mesa's software device (cameras **1.66 Hz**, one sim ≈ 7 cores); with MIG off, `GL_VENDOR = NVIDIA`
and both cameras run at **30 fps**, and the same five seeds score **4/5, mean 2.6 cubes**. The render
rate, not arm64 / 64k pages / the unpinned upstream sim, caused the collapse. Decision 3's "Gazebo on
CPU" does not hold: software rendering would need ~18x.
**Measured capacity with MIG off** (`tools/host/fury/22-sim-scale.sh`, isolated sims, each its own network
namespace and `GZ_PARTITION`): 1 sim 30 fps at real time, GPU ~40 %, 5.2 cores; **2 sims 30 fps each,
real time, GPU ~68 %**; 4 sims 19 fps each, GPU ~90 %; 6 → 13.6 fps; 8 → 10.7 fps. Total throughput
saturates around 150-170 camera frames/s, i.e. **two full-rate sims**; beyond that the sims fall behind
real time (the single-sample RTF reading flips between ~1.0 and ~0.02-0.05 as physics stalls on rendering —
measure sim-time advance over a window next time). A CUDA job saturating the GPU next to 4 sims costs a
further ~22 % (19 → 14.8 fps). CPU is not the limit.
**Options on this hardware:** (a) **two modes** — MIG off for the robot loop (sim + serving + training share
the GPU as on the desktop), MIG on for a tenancy showcase; switching needs the GPU idle, about a minute
plus workload restarts. (b) **no MIG**, soft sharing (MPS / time-slicing): everything at once, no hardware
isolation, and only two full-rate sims. (c) **render the cameras with CUDA instead of OpenGL** so the sim
side fits inside MIG slices: maintained CUDA-only ray tracers exist on NVIDIA Warp (MuJoCo-Warp's batch
renderer, Newton's tiled camera); upstream already ships a MuJoCo model of the whole scene and a MuJoCo
bringup with the same ros2_control controllers and camera topics, so a separate renderer node (scene +
joint/cube poses in, two images out) could serve Gazebo physics unchanged. Nobody reports Warp rendering
under MIG — `tools/host/fury/23-warp-probe.sh` tests that gate. Cost of (c): new pixels, so a new teacher;
bootstrap by running the current policy in GPU-rendered Gazebo (MIG off) while the new renderer draws the
same episodes, then fine-tuning on the new images. (d) an RTX PRO add-in GPU — not an option for this
demo.
**Status:** decision 1 ("MIG on, everything built against slices from day one") cannot stand as written.
Proposed: run the flywheel phases (1-5) with MIG off — needed under every option, including (c)'s
bootstrap; keep the MIG layout one command away; decide the demo structure (two modes vs CUDA-rendered
sims under MIG) once the Warp probe and, if it passes, a MuJoCo-Warp spike have numbers. Pending the
operator's decision.
**Also fixed along the way:** `docker/healthcheck.sh` hung forever when it ran before the model version was
published (`ros2` ignores SIGTERM under rmw_zenoh, so plain `timeout` never returned) — now `timeout -k`.
The pinned modelcar digest is amd64-only (mounts fine, podman warns). Native sim build: 6 min.

### D142 addendum — CUDA-only camera rendering works inside a MIG slice on the Fury

`tools/host/fury/23-warp-probe.sh` (NVIDIA Warp 1.17 from PyPI, CUDA 12.9 runtime, kernels JIT-compiled
for `sm_103` in 0.6 s, ray-casting a stand-in tabletop scene for two 640x480 cameras, frames copied back
to the host): **6,449 frame pairs/s on the whole GPU, 2,096 on a MIG 1g.31gb slice** — against 30
needed. No public report of Warp rendering under MIG was found beforehand; this is the gate for option
(c) and it is open. A toy scene with single-hit shading is not the real arm with textures and shadows,
so the next measurement is the MuJoCo-Warp batch renderer on upstream's MJCF scene
(`tools/host/fury/mjwarp-spike/`). Switching MIG off and back on took two seconds each way with the GPU
idle, and the MIG UUIDs came back unchanged.
**Shape the operator wants kept in view:** MIG stays on and **one slice is the rendering tenant** for a
fleet of robots (physics on CPU cores, cameras ray-traced with CUDA), next to the LLM, serving and
training tenants — which would remove mode switching from the demo entirely. The flywheel with MIG off
and a GPU-rendered Gazebo remains act one and the bootstrap for the new teacher.
**Meanwhile, natively:** the robot loop is no longer hand-started containers. `fury-mode flywheel|tenants`
moves the GPU between the two states (the choice persists across reboots through `MIG_LAYOUT`, `none`
meaning MIG off); sim and policy are quadlets behind `fury-flywheel.target`, the sim refusing to start in
MIG mode; the recorder is a unit bound to `disk-guard.service` and gated on 1.5 TB free, replacing the
`pgrep` check, and the guard stops the unit rather than a container systemd would restart.

## D143 — Demo structure on the Fury: two acts on one GPU, host workloads governed through RHEM (operator's direction)

**Date:** 2026-09-19
**Context:** D142 showed that MIG tenancy and a GPU-rendered Gazebo cannot coexist on the one GB300, and
that CUDA-only rendering does run inside a MIG slice. The operator asked where OpenShift belongs in the
picture, since every GPU workload had ended up as a host container outside it.
**Direction:**
- **Act 1 — the governed flywheel, MIG off.** One GPU-rendered robot; sim, policy serving and training share
  the GPU. Host workloads are podman quadlets **delivered and governed by RHEM** from the OpenShift hub (Git →
  GitOps → Fleet → device, signed images verified on the device) — hand-installed from
  `tools/host/fury/flywheel/` only until the host is enrolled. Also the bootstrap for a teacher on the new
  renderer's pixels.
- **Act 2 — tenancy, MIG on** (`fury-mode tenants`): Red Hat AI Inference Server serving a coding assistant on
  the 3g slice; **an OpenShift-managed robot fleet** — physics-only sims as pods on the hub (`oc scale`, one
  network namespace per robot, which also removes the `/run_policy` collision), cameras ray-traced with CUDA
  by a **rendering tenant** on a 1g slice, each robot paired with a RHEM-managed device; training on a 1g
  slice; the host device's own policy serving on the last. The fleet is the stretch goal: it needs the
  renderer node, a host↔VM image path and the new teacher, and the demo must stand without it.
- **Cheap, on-message additions:** per-slice metrics and an isolation proof in the OpenShift console; the MIG
  layout as a Fleet-delivered file so reslicing (and the act 1 → act 2 switch) is a governed Git change; the
  device refusing unsigned images for every tenant; optionally the coding assistant proposing a governed PR.
- The switch between acts needs the GPU idle and workloads restarted (minutes, with an LLM reload): scripted,
  rehearsed, narrated or done between sessions — never improvised.
**Research behind it (2026-09-19, primary sources):** host quadlets under RHEM are the closest thing to a
supported configuration on this hardware. RHAIIS 3.5 `vllm-cuda-rhel9` is published for arm64 and Red Hat's
supported-configurations list GB200/GB300 on AArch64 with CUDA 13 (unknown 7 retired as far as documents go;
RHEL 10 as the host is not named). RHOAI 3.5 supports aarch64 clusters (dashboard, pipelines, KServe, Kubeflow
Trainer). MicroShift on RHEL 10.2 is Technology Preview with no documented aarch64 GPU or MIG path and no
official ACM support — not now. Whole-GPU passthrough into the SNO VM is unsupported by Red Hat (GPU assignment
"only supported on Intel 64 and AMD64") and by NVIDIA (Grace-Blackwell "limited to bare metal"), and this GPU
reports PCI ID `10de:31c3`, which is not in the `nvgrace-gpu-vfio-pci` table upstream (`31c2` is) — closed.
RHEL workers were removed in OpenShift 4.19. Bare-metal SNO would mean a rebuild and the GPU Operator's ARM
table does not list DGX Station.
**New risk for Phase 2:** RHEL 10's ARM 64 virtualization rules list only RHEL guests as supported and require
host and guest page size to match; the host runs the 64k kernel, so the SNO guest may need
`kernelType: 64k-pages` (OpenShift ≥ 4.15). Tracked as unknown 13 — test before building on the VM.
**Still open:** decision 10 (no arm64 RHTAS), the MuJoCo-Warp spike on the real scene, and how far the
current policy is from the new pixels.

### D143 addendum — the real scene, ray-traced with CUDA inside a MIG slice: 152 camera pairs a second

`tools/host/fury/24-mjwarp-spike.sh` on slice `0:3` (MIG on): upstream's MuJoCo scene assembled exactly as
upstream's launch file does it (arm xacro + the SDF world converted by `sdformat_mjcf`, which worked in the
sim image — Gazebo's Python bindings are there), 58 geoms, 13 meshes, **322,564 faces**, one shadow-casting
spot light, two 640x480 cameras; MuJoCo-Warp 3.13 batch renderer on Warp 1.17, arm moving every frame, RGB
copied back to the host as a publisher would need. **152 frame pairs/s with shadows, 178 without** (render
4.6 ms / 3.1 ms per pair, read-back ~1.6 ms, kinematics + BVH refit ~0.5 ms); first-run kernel compilation
about 7 s, cached afterwards. That is five robots' cameras at 30 fps from one 1g.31gb slice rendered one
world at a time — the renderer is a batch renderer, so rendering N worlds per call should do better; not yet
measured. Unknown 12's first half is retired.
**The look:** same geometry and framing as Gazebo (the cameras come from the same model) but a visibly
different image — brighter cyan arm with black servo bodies, white tray, hard-edged shadow, mid-grey sky,
no global illumination. Expect the current policy to need a fine-tune on these pixels; material colours and
the light are plain numbers in the MJCF and can be moved toward Gazebo's palette first. Next in this track:
a renderer node that follows Gazebo's joint and cube poses and publishes the two images on side topics, so
act 1 records both image sets for the same episodes (the new teacher's dataset), then the current policy
zero-shot on the new pixels to size the gap.

### D143 addendum 2 — the fleet act is a showcase, not a second flywheel (operator's framing)

The operator's correction: act 1 (MIG off, GPU-rendered Gazebo) **is** the flywheel and uses the existing
policy as it is. The MIG-mode fleet only has to show robots running and doing the task under governance; no
flywheel is built from them, and no "new teacher" project is assumed. D143 overstated this by listing a new
teacher as a requirement before anything was measured. What is actually open is one measurement: whether the
**existing** policy performs acceptably on the CUDA-rendered pixels, after moving the renderer's colours and
light toward Gazebo's look (numbers in the MJCF) — the same five-seed eval used elsewhere. If it does, the
fleet act needs no training at all. If it does not, the choice at that point is between a single offline
fine-tune, a smaller fleet act, or openly labelled recorded motion — decided then, by the operator. None of it
blocks act 1, the hub, or the tenancy showcase.

## D144 — Fury Phase 1 closed: the arm64 flywheel pieces run on the host, as services, on the whole GPU

**Date:** 2026-09-19
**Exit as met:** nine successful episodes recorded by the arm64 stack on the Fury (the operator accepted nine
for the plan's ten) — sim, policy and recorder running as systemd quadlets, the recorder bound to the disk
guard, bags under `/data/flywheel/bags`, cameras at ~25-30 fps in the recordings. The first episode of a fresh
policy container is always a miss: the model is loaded onto the GPU lazily by the first goal.
**Deviation from the exit as written:** the policy is served from the **whole GPU with MIG off**, not from
slice `0:1` — the sim cannot render under MIG (D142). Serving from a slice is proven separately (first
inference ran on `0:1`, SELinux-confined, on the 64k kernel).
**Retired in this phase:** unknowns 1 (64k pages: stay on the 64k kernel), 2 (native sim build 6 min; rendering
needs the GPU), 9, 11, 12 (speed); `SecurityLabelDisable=true` is not needed on this host.
**Carried forward:** the loop's defaults keep fixed cube positions and the learned rest pose (the desktop's
cube-reset caveat applies) — set deliberately before these bags feed anything; pin the upstream sim source and
the pip versions before Phase 5; the image's health check fix lands with the next runtime image build;
stopping the units reported `failed` because ROS ignores SIGTERM — the quadlets now stop with SIGINT.

## D145 — Fury hub: OpenShift 4.22 (RHOAI 3.5 to follow), a 4k-page RHCOS guest on the 64k-page host

**Date:** 2026-09-19
**Context:** Phase 2. The plan said "OpenShift >= 4.19 aarch64"; the desktop's hub grew from 4.17 to 4.19 and
runs RHOAI 2.25. Red Hat's RHEL 10 ARM 64 virtualization notes list only RHEL guests as supported and want host
and guest page sizes to match; the host runs the 64k kernel.
**Decision:** install **OpenShift 4.22** (`stable-4.22`, 4.22.13 at install time, Kubernetes 1.35) rather than
4.20. RHOAI 2.25 — what the repo's four RHOAI resources were written for — stops at OpenShift 4.20, so 4.22
means **RHOAI 3.4/3.5** (supported on 4.19.9-4.22, aarch64: dashboard, AI Pipelines, KServe GA) and a small
port in Phase 3: the DataScienceCluster / DSCInitialization, the pipelines application, the ModelRegistry (D081's
OAuth-proxy shape) and the route names the pipeline code and docs refer to. Chosen because it is what a fresh
build would use today (OpenShift 4.22 + RHOAI 3.5 + RHAIIS 3.5 is one current story), both even releases are
extended-support releases, and it avoids two upgrade hops on a single node that cannot roll back. Cost accepted:
a second new variable (RHOAI major version) alongside the new architecture; RHOAI comes late in Phase 3.
**Guest page size:** the guest runs RHCOS's default **4k** kernel. RHCOS is outside RHEL's supported-guest list at
any page size, the installer's live image is 4k regardless, and KVM handles the mismatch. Kept out: the memory
balloon (unreliable when host pages are larger than 4k). Fallback if it misbehaves: a day-2 `master`
MachineConfig with `kernelType: 64k-pages`.
**Pre-test (`tools/host/fury/30-vm-pretest.sh`, no pull secret):** the 4.22 live image as a 32 vCPU / 128 GiB
guest on `fury-net` — RHEL CoreOS **9.8** (so a fresh 4.22 install lands on the RHEL 9 stream, not the preview
RHCOS 10 the payload also carries), page size 4096 in the guest and in a container, static address, `api` ->
`10.20.0.10`, no wildcard at the zone apex, quay.io reachable through the host's masquerade, 3.5 GB/s direct
writes to the qcow2, public NTP reachable. Unknown 5 retired; unknown 13 retired functionally.
**Install:** agent-based, `tools/host/fury/32-sno-install.sh` in stages, templates in `tools/host/fury/sno/`
(secret-free; the pull secret and a generated ssh key are appended under `/root/sno-install`, which also holds
the logs, because the ISO and the installer state carry the pull secret and the last stage prints the kubeadmin
password). Guest: 32 vCPU, 128 GiB pinned to NUMA node 0 (nodes 1-8 are the GPU driver's cpu-less nodes),
600 GB thin qcow2, disk-then-ISO boot order so the installer's reboot lands on disk by itself. Storage in
Phase 3 stays the local-path provisioner — no second disk.

## D146 — Fury Phase 2 closed: the hub is up (OpenShift 4.22.13, arm64, single node, KVM guest)

**Date:** 2026-09-19
**Result:** `tools/host/fury/32-sno-install.sh` (fetch, image, vm, wait, finish) installed the hub in one pass —
about 28 minutes from starting the guest to the console answering. Node `master-0` Ready at `10.20.0.10`,
Kubernetes 1.35.6, RHEL CoreOS 9.8 on the default 4k-page kernel under the 64k-page host, CRI-O 1.35, every
cluster operator available and not degraded. From a laptop on the tailnet: the API answers on
`api.sno-flywheel.local` through split DNS and the subnet route, and the console route returns 200. The agent
ISO (which carries the pull secret) was ejected and deleted; the guest autostarts; kubeconfig and the installer
state stay under `/root/sno-install`, root-only. Unknown 5 is retired in full.
**Snags:** RHEL 10 ships no `/etc/sysconfig/libvirt-guests`, so the last stage's `sed` aborted it after the
eject/delete/autostart steps — the script now creates the file (`ON_SHUTDOWN=shutdown`, `SHUTDOWN_TIMEOUT=300`,
because host boots take minutes — D140). A laptop whose `/etc/hosts` still pins the cluster's app names to the
development stand-in will land on the wrong cluster; the two share a domain, so only one is reachable by name
at a time.
**Findings that shape Phase 3 (research, 2026-09-19):** on the 4.22 catalog the RHOAI Subscription's
`channel: stable` still resolves to 2.25 — it must say `stable-3.5`; the v2 DataScienceCluster renames
`datasciencepipelines` to `aipipelines` and drops `modelmeshserving`/`codeflare`; the ModelRegistry moves from
`oauthProxy` to `kubeRBACProxy` with the REST path, Service and Route host unchanged; the dashboard moves to
`rh-ai.apps.<domain>` behind the Gateway API (Service Mesh 3 via the Ingress Operator — unproven on an arm64
single node). **Decision 10b is resolved:** Red Hat's product image `registry.redhat.io/rhem/flightctl-ui-rhel9:1.3.0`
is multi-arch (only the upstream quay UI image is amd64-only). **Decision 10 narrows:** RHTAS still has no arm64
server images and none are being built, but its clients (`rhtas/cosign-rhel9` and friends) are multi-arch, the
repo signs with keys so only Rekor + Trillian are exercised, and both a self-build from the `securesign`
midstream at `rhtas-v1.4.3` and the upstream `rekor` Helm chart are workable — operator's choice pending.

## D147 — Fury Phase 3, first half: the hub's stack is up on arm64; Rekor + Trillian get rebuilt from source

**Date:** 2026-09-19
**Result:** all eight Argo Applications (`storage`, `operators`, `operators-config`, `minio`, `flywheel`,
`observability`, `rhem`, `tekton`) are Synced/Healthy on the 4.22.13 arm64 single node, from branch `fury`.
Operators that reached Succeeded: OpenShift GitOps 1.21.4, Pipelines 1.24.0, Cluster Observability Operator
1.5.2, Tempo, RHOAI 3.5, RHEM 1.3. From a laptop on the tailnet the RHEM UI, the RHOAI dashboard
(`rh-ai.apps.<domain>`, Gateway API — it does work on an arm64 single node), the flywheel dashboard, the MinIO
console and Argo CD all answer; the model-registry REST route answers 401 (auth in front, as intended); the
MinIO and Kafka NodePorts are open on `10.20.0.10`. The node idles at 7% CPU and 15% memory with all of it
running. Unknown 4 is retired except for RHTAS.
**What had to change for arm64 / 4.22:** (1) the bootstrap operators (GitOps, COO, Tempo) are a manifest now,
`argocd/bootstrap-operators.yaml`, instead of console clicks; (2) RHOAI pinned to `stable-3.5` with the v2
DataScienceCluster and the `kubeRBACProxy` ModelRegistry (D146's findings, confirmed live); (3) the RHEM chart's
UI and its setup jobs both default to amd64-only images — `registry.redhat.io/rhem/flightctl-ui-rhel9:1.3.0`
and `registry.redhat.io/openshift4/ose-cli-rhel9:v4.22` replace them (`quay.io/openshift/origin-cli` is
amd64-only under every tag; the symptom is `ImagePullBackOff` on the setup jobs and an Argo sync that never
finishes); (4) the RHTAS Subscription and the `Securesign` CR were removed from GitOps — the operator's bundle
installs on arm64 but every server image it would start is amd64.
**Decision (operator's, 2026-09-19): rebuild Rekor + Trillian from Red Hat's midstream source rather than go
straight to upstream sigstore.** `tools/host/fury/rhtas-arm64/`: native rootless builds on the host from
`github.com/securesign` at the 1.4.3 release tags (commits pinned), the operator deployed outside OLM with its
`RELATED_IMAGE_*` defaults pointing at the rebuilt images, standalone `Trillian` + `Rekor` CRs with our own
signer key (no Fulcio, CT log, TUF or TSA — signing here is key-based). Two of the images sit on
`registry.redhat.io` RHEL bases and may not be redistributed, so they live in the cluster's own registry,
switched on for this (RWO claim on the node-local provisioner, `Recreate`, default route; on a single node this
rolls the API server once — a few minutes of API flapping). The login to `registry.redhat.io` is needed for one
step only (`build.sh bases`). **Time box: one day.** Past that, or if one image eats more than two hours: the
upstream `rekor` Helm chart behind the same Service name and Route host, so nothing downstream changes.
This is a rebuild of the product's source by a different builder — not the product, and not supported; the
runbook and any demo narration must say so.
**Inherited, not yet redesigned:** MinIO and the other stateful pieces still use `hostPath` volumes, which needed
node directories created and labelled by hand (`oc debug node`) — they should move to claims on the node-local
provisioner that the `storage` app already installs. MinIO carries the development stand-in's credentials for
parity, which are MinIO's defaults — acceptable on a routed lab network behind a tailnet, to be changed before
anything else can reach it.
**Still owed for Phase 3's exit:** Rekor up and its public key pinned (`gitops/tekton/rekor-public-key.yaml`,
the Fleet's inline `rekor.pub`), a fresh cosign keypair (Fleet's `cosign.pub`), the push/sign/GitHub Secrets,
one native Tekton build signed into this Rekor, the `rhem/bootstrap` objects, and the rollout batches (unknown 6).

**Addendum (2026-09-19, same day): the rebuild worked first time; the fallback is not needed.** All six images
built natively with rootless podman in about four and a half minutes together (rekor-server 2 min 7 s, the two
Trillian servers 58 s and 31 s, the operator 42 s, database and redis a few seconds each). The operator runs
outside OLM from the rendered manifest (7 CRDs, one ClusterRole, the manager — nothing else), pulls from the
cluster registry, and brought `Trillian` and `Rekor` to Ready about 90 seconds after the CRs were applied. The
signer key was generated straight into its Secret and never touched a disk; the tree ID is pinned in the live
CR. A `cosign` v2.6.5 `sign-blob` from an arm64 pod created log entry 0 and `verify-blob` passed against the
log's public key, over the same in-cluster Service URL the Tekton Task and the promotion pipeline already use —
so nothing downstream changed except the key. The amd64-only `cli-server` stays at 0 replicas once scaled down.
**Pinned now:** the new log key in `gitops/tekton/rekor-public-key.yaml`. **Deliberately not yet:** the Fleet's
inline `rekor.pub` and `cosign.pub` — the Fleet on this branch still names images signed under the development
stand-in's trust root, so its two keys and its two digests change together, in one commit, once the first
native build is signed here. No device can receive the half-changed state: the Fleet is not applied to this
hub until the `rhem/bootstrap` step.

## D148 — Fury Phase 3: a fresh trust root, and the first image built and signed natively on the hub

**Date:** 2026-09-19
**Result:** the runtime-image Pipeline ran on the arm64 hub with `platforms=linux/arm64` (nothing on this
machine pulls amd64; the emulated amd64 leg would now be the slow one): **16 min 47 s** end to end — clone 31 s,
build and push 16 min 0 s, sign and verify 16 s — against 53 minutes for the arm64 leg alone under emulation on
the development stand-in. Tag `act-inference-e0716d0`, manifest list
`sha256:f5d3a91e9e15f47b99875d38b0020545a3a2135908cc886871a6a787f693c991`, platform image
`sha256:045b3d4ef70c5ea845fd1dfa659a67cb1ff8e0414e316f9ef45997475c2cef88`, this hub's Rekor entries 2 (list)
and 3 (platform image). The digest-pinned `buildah` and `ubi-minimal` task images are multi-arch and needed no
change; the `qemu-binfmt-arm64-host` DaemonSet was already in the repo for an amd64 leg, should one be wanted.
**Trust root:** a fresh cosign key pair (made with cosign v2.6.5, the release the pipeline signs with) and the
hub's own Rekor (D147). `tools/hub/create-cosign-secrets.sh` creates both signing Secrets from one passphrase
prompt, checks that the passphrase opens the key first, and hands it to `oc` on stdin. The push and GitHub
Secrets are the development stand-in's, copied cluster to cluster through a pipe.
**The modelcar was not rebuilt:** the digest the Fleet already pins got a second signature — this hub's key, this
hub's Rekor entry 1 — through the cluster's own `cosign-sign` Task. Checked from outside the cluster: that
digest verifies under both trust roots (the stand-in's devices are unaffected; a `sigstoreSigned` policy is
satisfied by any one valid signature), and the new runtime image verifies under the new root only.
**The Fleet changed in one commit:** inline `cosign.pub`, inline `rekor.pub`, the runtime digest, and the
rollout — batch 1 `role=canary` (`limit: 1`), batch 2 `site=fury`, then flightctl's implicit last batch.
**Unknown 6 is retired on paper:** flightctl 1.3's fleet documentation says explicit batches "might be none"
and that the implicit last batch takes every device the explicit ones did not select, and the stand-in ran for
weeks with a second batch that matched nothing. Proof on this hub comes with the first enrolled device, which
will meet an empty canary batch.
**Carried into Phase 4:** the new image has not driven the arm yet. The host's own units and probe scripts
(`tools/host/fury/flywheel/*.container`, scripts 10/13/21/22/23) still name the previous digest, which is signed
under the old root only — once enrollment writes the new `policy.json` on the host, podman there will refuse
it. Order: smoke the new image on the GPU (`13-first-inference.sh` now takes an image argument), move the host
units to it, remove the hand-installed `act-inference` unit, then enroll. The demo runbook's stand-in-versus-Fury
table still describes the old batch order; it gets rewritten with the rest of the runbook's Fury pass.

**Addendum (2026-09-19): Phase 3 closed.** The four `rhem/bootstrap` objects were applied by the operator with a
kubeadmin token: `Repository/hp-roscon-flywheel` Accessible, `ResourceSync/rhem-fleets` and `rhem-catalog`
Accessible and Synced from `fury`, `Fleet/act-inference` owned by the sync and Valid — so the hub renders the
Fleet with this hub's keys and digests and the retargeted batches. Exit criterion met: a runtime image and a
modelcar signed under the hub's own trust root, Rekor indexes 1, 2 and 3 recorded, RHEM UI up.

## D149 — The first natively built runtime image could not start: upstream moved under an unpinned clone

**Date:** 2026-09-19
**Found by:** the GPU smoke of `act-inference-e0716d0` on the host (`13-first-inference.sh` with the new digest).
The policy container connected to the sim, wrote its contract, then died:
`FileNotFoundError: /ws_pai/install/rosetta/share/rosetta/params/rosetta_client.yaml`. The script sat in its
"waiting for the model version" loop because the version is never published by a dead container.
**Cause:** both Dockerfiles cloned `ros-physical-ai/demos` at HEAD. On 2026-09-16 upstream bumped to rosetta
0.2.0 (`4d3564c`, "Bump to rosetta 0.2.0"), which renames the client the entrypoint launches
(`rosetta_client_launch.py` → `policy_runner_launch.py`, `params/rosetta_client.yaml` →
`params/policy_runner.yaml`). The last good image (`act-inference-ea513fa`, built 2026-09-09) was built when HEAD
was `80dc00c`, whose `pai.repos` pins rosetta `fb3860c` — the old names. Nothing about arm64 or this machine.
**Fix:** both Dockerfiles fetch upstream by commit (`ARG DEMOS_REF`): the runtime image at `80dc00c` (what the
last good image was built from), the sim image at `4d3564c` (what the running, proven sim image on this host was
built from; the sim does not run the rosetta client, and this exact pairing scored 4/5 here — D142). `pai.repos`
pins every other repository by commit, so the whole workspace is now reproducible. The runtime Dockerfile also
asserts after `colcon build` that the two files the entrypoint launches by name exist, so the next upstream
rename fails the build instead of a device. Porting the entrypoint, health check and coordinator to rosetta
0.2.0 is a separate piece of work and is not needed for this demo.
**What this says about the pipeline:** it built, pushed, signed and verified an image that cannot start, and
the Fleet was re-pinned to it (D148) before anything had run it. No device was enrolled, so nothing received
it — but the order was wrong. **Rule from here: an image digest goes into the Fleet only after that digest has
served on a GPU.** The trust chain proves who built an image, not that it works; a start-up smoke step in the
Pipeline (run the entrypoint far enough to import and find its files, no GPU needed) would close the gap and is
worth adding before Phase 5's promotions depend on this path.
**State:** rebuild `runtime-image-7mdrb` started from `c2560cf`; until its digest has passed the smoke and is
pinned, `gitops/rhem/fleet-act-inference.yaml` on `fury` names an image that cannot start. The host keeps
serving from the previous image under the hand-installed unit.

**Addendum (2026-09-19): the rebuilt image serves, and only then was it pinned.** `runtime-image-7mdrb` from
`c2560cf`: 14 min 15 s, tag `act-inference-c2560cf`, manifest list
`sha256:5eba6ca4ee8acf7be87ec8da852d314d6dd16d76cfbce09a1581dbf8c5c94837`, this hub's Rekor 4 and 5, verified from
outside the cluster. The build-time assertion passed. GPU smoke on the host (MIG off, whole GPU, 5 seeded
episodes): **4/5, mean 2.6 cubes, no goal rejected** — the same score as the previous good image in the same mode
(D142); `served_model_version` is null in eval mode for both, so that is not a regression. The Fleet, the host's
own units and the probe scripts now all name this digest; `docs/DEMO_RUNBOOK.md` still shows the old one and is
rewritten in the runbook's Fury pass. The smoke itself needed a fix on the way: `13-first-inference.sh` reused
any container called `act-inference`, including the dead one from the broken image, and then waited five minutes
on it — it now reuses only a running container of the image under test and stops waiting when the policy dies.

## D150 — Fury Phase 4 design: the host is a device *and* a shared machine, so the Fleet's pull default is label-driven; the hub owns the policy's lifecycle

**Date:** 2026-09-19
**Context:** on the development stand-in the device was a dedicated VM; here it is the bare-metal host, which is
also the build box, the hub's hypervisor, the tenant machine of act 2, and shared with partner staff. A review of
`device/provision.sh`, `device/enroll.sh`, the Fleet and flightctl 1.3's agent source against this host found:
- **`policy.json` `default: reject` (D131) would be system-wide here.** Root podman could pull only from the two
  Red Hat registries and the two signed `quay.io/jary` repositories: no `nvcr.io` (smoke tests, DCGM, whatever
  other people run), no `docker.io/library/ros` for sim rebuilds.
- **No unit-name clash.** The agent namespaces a Fleet quadlet as `<app>-<crc32 prefix>-<unit>`
  (`act-inference-128875-act-inference.service`, as the stand-in's records already show). The hand-installed unit
  still has to go before enrolling, for D132's reason: two `/run_policy` servers on one Zenoh graph.
- **flightctl 1.3 has `flightctl app stop|start`**: a per-device override that survives Fleet rollouts, with the
  agent still connected and the application reported `Stopped`, not failed.
- The plan's Phase 4 text is wrong for this host in three places: `provision.sh` would add NVIDIA's repo and
  write a static `/etc/cdi` spec that goes stale at the next mode switch (the toolkit is from RHEL Supplementary
  and `nvidia-cdi-refresh` owns the spec — D139, D141), it would pin the hub in `/etc/hosts` beside dnsmasq, and
  `enroll.sh` needs passwordless sudo and parks the enrollment key in `/tmp` on a shared login host.
**Decision (operator's, on recommendation): the Fleet's `policy.json` default is
`{{ getOrDefault .metadata.labels "pull_default" "reject" }}`.** Every device stays fail-closed unless it is
approved with `pull_default=insecureAcceptAnything`; only the Fury host is. The two `quay.io/jary` repositories
remain signature- and Rekor-enforced on it, so the trust demonstration (unsigned, wrong key, not logged → refused)
holds on the host; the "anything outside the allow-list is refused" case belongs on a fleet VM (Phase 7). A
mistyped label value is not a policy type, and podman then refuses every pull — it fails closed. The label is set
by whoever approves the device, the same trust as the approval itself.
**Decision: the hub owns the policy's lifecycle; `fury-mode` verifies.** To tenants: `flightctl app stop` from a
laptop, then `fury-mode tenants`. Back: `fury-mode flywheel`, then `flightctl app start`. `fury-mode` refuses to
switch while the agent's policy unit is up (a loaded, idle policy holds the device nodes without appearing as a
compute process) and prints the command. No hub credential lands on the shared host, and a promotion merged
during act 2 does not resurrect the policy. Relabelling `gpu_device` to a `MIG-<uuid>` stays the documented way
to *move* the policy onto a slice, for when tenants mode has a Zenoh router and a robot for it to drive — today
it has neither (the router lives in the sim container, and the sim cannot run under MIG).
**Also changed in the Fleet's unit:** `After=`/`PartOf=so-arm-sim.service` (a sim restart restarts the policy, as
the hand-installed unit did; inert where no such unit exists), `StopSignal=SIGINT`, `StopTimeout=15`,
`SuccessExitStatus=130 143` (a ROS 2 tree ignores SIGTERM: every stop was a timeout and a kill, and read as a
failure), and defaults for the `zenoh_router` / `zenoh_port` labels (a missing one failed the render).
**Host side:** `tools/host/fury/40-device-provision.sh` (operator, on the host) and `41-device-enroll.md`
(laptop) replace the two `device/` scripts on this machine; the recorder's unit no longer `Requires=` the
hand-installed policy; `14-flywheel-services.sh` leaves that unit out once the host is enrolled. No hostname
change is needed after all: approval sets `alias=fury-host`.
**To check live on enrollment day:** whether an app-stop override holds across a host reboot; what RHEM shows
after a by-hand stop; the agent's `Driver=image` pre-pull of the amd64-only modelcar on arm64; whether a newly
approved device goes through the batch sequence at all (unknown 6's real proof may be the first template change,
Phase 5); SELinux denials from the confined agent writing `/etc/containers/policy.json` on this host.

## D151 — A self-contained mode becomes a phase of its own; the act 2 model and image are fetched ahead

**Date:** 2026-09-19
**Trigger:** a 4.2 GiB image pull from quay crawled at 2–11 MiB/s over the lab's uplink during a smoke test, and
the operator asked whether moving to the internal registry and a local git would make the machine
disconnected-capable. **Answer: those two are the largest pieces, but not the whole list.** A sweep of what the
*running* demo reaches found five more: `cosign` and `crane` downloaded from GitHub releases at every pipeline
run (Tekton Task and `pipeline/act_flywheel_pipeline.py:119,164`), `pip install` at the start of six of the seven
KFP components (`:46,81,99,210,252,267`), possibly backbone weights at training start (unverified), the laptop's
Tailscale path, and the one-time fetches for act 2.
**Decision (operator's): it is wanted, and it is done at the appropriate time, not now** — recorded as Phase 8b
of `FURY-PLAN.md` with the full inventory, the boundary (running the demo, not rebuilding images or installing
operators) and an exit test (egress blocked on the host's firewall; a full promotion, both mode switches, act 2).
After Phases 4 and 5 work connected; the registry step may come forward because it also makes rollouts local.
Switching registries later is cheap: images copy with their digests, and a signature is per repository name, so
it is one `cosign sign` and a Fleet edit.
**Also found:** the runtime image ships as one 4.2 GiB layer (`buildah bud` without `--layers`, and no cache
between runs), so every rebuild is a full pull everywhere. The serving image's namespace changed — 3.4 and 3.5
are `registry.redhat.io/rhaii/vllm-cuda-rhel9`; `rhaiis/` stops at 3.3 — corrected in the plan. From this lab
Hugging Face delivered 34 MB/s where quay managed 2–11: the uplink is not the bottleneck, quay's path is.
**Fetch-ahead:** `tools/host/fury/60-model-fetch.sh` — the model at a pinned revision into `/data/models`, no
root, no Python, resumable, every file verified (sha256 for the LFS files, git blob id for the rest); tested on
the host with three small files including a corrupted one.

## D152 — Fury Phase 4: the host is an RHEM-managed device; the Fleet's policy serves on the whole GPU

**Date:** 2026-09-19
**How it ran:** the hub half from a laptop with the existing `flightctl` login (enrollment config made with
`flightctl certificate request --signer=flightctl.io/enrollment --output=embedded`, copied to the host 0600,
never displayed, local copies removed; approval by CLI), the host half as two operator commands (stop the
hand-installed policy, `40-device-provision.sh agent-config.yaml`). One pending request, its name matching the one
the script printed, approved with `fleet=act-inference site=fury gpu=nvidia arch=arm64 policy_device=cuda
zenoh_router=10.20.0.1 zenoh_port=7447 alias=fury-host pull_default=insecureAcceptAnything`; no `gpu_device`
(MIG off) and no `role`.
**Result:** device `Online`, owner `Fleet/act-inference`, `UpToDate` within 20 s of approval; applications
`Healthy`, `act-inference` `Running 1/1`, no restarts, under a minute after that (both images were already in
local storage, and both verified under the new trust root — the agent pulls under the `policy.json` it has just
written). On the host: `act-inference-128875-act-inference.service` and its `…-flightctl-quadlet-app.target`
active, the hand-installed `act-inference.service` gone; the rendered unit carries the smoke-tested runtime digest,
the co-signed modelcar, `AddDevice=nvidia.com/gpu=all`, `StopSignal=SIGINT`, and `After=`/`PartOf=so-arm-sim.service`
**left un-namespaced by the agent**, as intended; `/etc/act-inference/env` has `ZENOH_ROUTER=10.20.0.1:7447` and
`POLICY_DEVICE=cuda` with no thread caps; `/etc/containers/policy.json` is the Fleet's with default
`insecureAcceptAnything` (the label at work) and the two `quay.io/jary` repositories plus the Red Hat registries
enumerated; the distro file is kept as `policy.json.rhel-default`; `cosign.pub` and `rekor.pub` are in
`/etc/pki/containers`. The policy published `act-v2-ft160`.
**Checked live (D150's list):** the agent's `Driver=image` volume with the amd64-only modelcar works on arm64;
a newly approved device received the template at once — so unknown 6's real proof is still the first template
change (Phase 5). **Noise to tidy:** the agent logs `Failed to collect Disk usage for path: /sysroot` every
cycle — it assumes an image-mode host; a package-mode host has no `/sysroot`. Harmless; a resource-monitor path in
the agent config would quiet it. **Still to check:** SELinux denials from the agent's first render
(`ausearch -m avc`, needs root), the app-stop override across a reboot, RHEM's view of a by-hand stop.
**Also today:** the act 2 model is on the machine — `RedHatAI/Qwen3-Coder-Next-NVFP4` at revision `27a8f16f`, 26
files, 45 GB, every file verified, in `/data/models` (`60-model-fetch.sh`); the serving image pull
(`rhaii/vllm-cuda-rhel9` 3.5.1 by digest) was started alongside.
**Exit criterion:** met except for its last clause — episodes recorded with this policy as the only one on the
graph, stamped with the Fleet's model version. That needs the recorder started by the operator.

**Addendum (2026-09-19): Phase 4 closed.** With the agent's container the only policy on the graph, the recorder
ran for about five minutes: seven episode records stamped `act-v2-ft160`, all `rollout.status: ok`, two of them
with cubes placed (3 and 2). `ausearch -m avc -ts recent` printed nothing — no SELinux denials from the agent's
first render on this host. The serving image pull (`rhaii/vllm-cuda-rhel9` 3.5.1) finished as well, so both act 2
inputs are on the machine. **Worth watching, not a finding yet:** two of seven records placed cubes here against
six of ten in the morning's run under the hand-installed unit, with the same image scoring 4/5 in the seeded
smoke an hour earlier. Both runs show the same artefact — a zero-cube record 16–17 s after a successful one (the
cube-reset caveat, D056/D138) — so the raw ratio overstates the difference, and seven records prove nothing either
way. The only serving-side change is `ZENOH_ROUTER=10.20.0.1:7447` instead of `127.0.0.1`. Phase 5's collection
run produces the sample that settles it; if the rate is low there, try the loopback address first.

## D153 — Fury Phase 5, before anything trains: four blockers that are not about the GPU, and a recording bug

**Date:** 2026-09-19
**How training reaches a GPU (unchanged, and sound here):** the pipeline's first step is a GPU-less pod that
publishes to Kafka `training-triggers` and waits on `training-results`; a resident process on the host
(`src/host-runner/host_runner.py`) connects *outbound* to the hub's Kafka and MinIO NodePorts and does assemble,
fine-tune, paired eval and upload. No ssh, no port the cluster must reach on the host, nothing for the
`libvirt-to-host` reject policy to block. D022 called the runner a desktop shim "deleted by the Fury port"; with the
GPU staying on the host (decision 2) it is this machine's architecture. `docs/FURY-SETUP.md`'s "nothing about the
governed path needs Fury-specific changes" is wrong.
**What is wrong with the host half here:** `docker` and `--gpus all` hard-coded, `10.0.0.49` defaults, bind mounts
without `:z`, `~/flywheel-data`, a `nohup` in a login session of a shared account, `~/eval_policy.sh` which was
never in git, and a loop-park that looks for a container name that was already stale on the stand-in. And **no
governed run ever exercised the train or eval path**: every recorded run logged "checkpoint exists — skipping".
**Blockers found and fixed today (all needed under any design):**
1. *Episodes never left the host.* The sim unit set no `CURATOR_URL`, so the emitter only wrote local JSON — D152's
   seven records were local files, and the success count on the hub could not move. Added to
   `tools/host/fury/flywheel/so-arm-sim.container` (`http://10.20.0.10:30802/episode`; the NodePort answers).
2. *Phantom episodes, and lost real ones.* `episode_emitter.py` armed its 5 s idle timeout with the time of the last
   command seen *before* the episode started. After a success nothing publishes commands during the reset, so the
   next episode was ended within a second — a record with 0 cubes, 0.1–0.6 s, 6–32 steps, status `ok` — and the real
   episode's `end` was then ignored. Measured on the host: 8 of 17 records were phantoms, and 14 kept bags had only 9
   real records. It made D152's "2 of 7" look like a regression (real episodes: 2 of 3 under RHEM's policy, 5 of 6
   in the morning; the misses are the known first episode after a policy start), and it would have starved the
   160 count. Fix: `_start_episode` clears the stamp. Reproduced and verified with ROS stubbed out; needs the sim
   image rebuilt (six minutes, native). This is *not* the cube-reset caveat of D056/D138, which stays open.
3. *The trigger pointed at another cluster's pipeline and threw the count away.* `TRAINING_PIPELINE_ID` was the
   stand-in's id; the consumer zeroed its count even when the run failed to start; and the count lived in memory, so
   any pod roll during a collection of hours reset it. Now: the pipeline is found by name
   (`TRAINING_PIPELINE_NAME`), offsets are committed only when a run has started (a restart recounts everything
   since the last trigger), a failed start keeps the count and retries after five minutes, a malformed record
   cannot wedge the replay, and with no pipeline configured the count is simply kept. **Left unarmed on this hub**
   until the host runner exists — a run started now would wait ten hours for it and fail.
4. *The pipeline had never been uploaded here.* `tools/hub/upload-pipeline.sh` (new pipeline the first time, a new
   version after that, with a ten-minute token of the account that starts the runs). Uploaded: RHOAI 3.5 took the
   compiled file as is.
**Still open before a first run:** the incumbent checkpoint is not in this hub's MinIO
(`s3://episodes-data/checkpoints/act-v2-ft160/…`; the modelcar's flat `models/act/*` has to be re-tarred under
`pretrained_model/`); the model-registry client is pinned for RHOAI 2.25's API and has only seen a 401 from 3.5; the
gate may legitimately refuse a candidate (the incumbent scores 86–92 %), so "one promotion produced" is not assured;
the stand-in's only training figure ("~25 min for 160 episodes") has no log behind it and implies about 40 steps/s —
check it before comparing anything.
**Proposed, pending the operator's decision — the host half as native units:** keep the Kafka/MinIO contract and
the pipeline untouched; replace the nohup script with a root quadlet `flywheel-runner` from the pinned, signed
runtime image (it already carries lerobot, torch, the assembler, boto3 and the Kafka client), assemble and train
in-process on `nvidia.com/gpu=all`, data under `/data/flywheel`; run the paired eval in its **own rig** — a second
sim with its own Zenoh router in a private network namespace and its own `GZ_PARTITION` (the shape
`22-sim-scale.sh` already ran; two sims held 30 fps here, D142) — so the governed policy is never stopped and D132's
collision cannot happen. Rejected: the runner re-pointed as is (cannot stop root units or write `/data`, needs a
human with sudo at every eval, and is a script in a shell on a machine where everything else is a unit); a MinIO
object as trigger (no gain, loses the results channel); an RHEM-delivered job per run (flightctl applications are
services, every run would bump the Fleet's template version and walk the rollout batches in the very file the
promotion edits). **Verify first:** a five-seed eval in the isolated rig beside the running loop against today's
4/5, with no goal rejected in production meanwhile; if the scores diverge, fall back to an attended window with
`flightctl app stop`.

**Addendum (2026-09-19, evening): collection is live end to end on the Fury.** After the sim image was rebuilt with
the emitter fix (image 19:44 UTC, sim restarted 19:48) and the unit got its `CURATOR_URL`: twelve episodes closed in
about nine minutes, the shortest 1,211 steps — **no phantom records**, including after seven successes in a row,
which is exactly where they used to appear. Eight of the twelve were full successes (the first was the usual miss
after a policy restart; three placed two of three cubes). Every success was received by the hub's curator, scored
`PASS`, and counted by the manifest consumer: `pending=8/160`. With the address set the emitter posts to the hub and
no longer writes `/data/flywheel/episodes/raw` (that directory only fills when a POST fails). At this rate — about
46 s an episode, roughly 55 successes an hour — the threshold is some three hours away; the trigger stays unarmed
until the host runner exists (`TRAINING_PIPELINE_NAME`). Separate bug met on the way: `14-flywheel-services.sh`
stopped the systemd-run sim while "removing hand-started leftovers", then died on the already-removed container
before `daemon-reload`; it now reloads first and leaves alone any container that carries a `PODMAN_SYSTEMD_UNIT`
label. Still open: the sim does not exit on SIGINT within its stop timeout and gets killed.

## D154 — Fury Phase 6, first half: the coding model serves on this machine, with tool calling, at about 140 tokens/s

**Date:** 2026-09-19
**What ran:** `tools/host/fury/61-rhaiis-smoke.sh` (`up`, `ask`, `bench`, `probe`, `status`, `down`): a hand-started
container, loopback only, the model read-only and offline from `/data/models/RedHatAI/Qwen3-Coder-Next-NVFP4`
(revision `27a8f16f`, fetched and verified by `60-model-fetch.sh`), SELinux confined, CDI device
`nvidia.com/gpu=all` with `--gpu-memory-utilization 0.5` — **beside the running flywheel** (sim, RHEM-managed policy
and the recorder kept collecting throughout), MIG off. This run used the vLLM project's own image at v0.24.0
(`image=upstream`, pinned by digest in the script) — the plan's Phase 6 fallback; which image serves is one switch,
so the Red Hat AI Inference image takes its place without any other change. Settings that differ from vLLM's
defaults: `--linear-backend cutlass`, `--gdn-prefill-backend triton`, `--no-enable-flashinfer-autotune`, context
131072, `--enable-auto-tool-choice --tool-call-parser qwen3_coder`.
**Result:** cold start to ready **5 min 46 s** (weights 44.3 GiB in 2 min 41 s, torch.compile 49 s, profiling and
warm-up about 2 min, CUDA graphs 8 s); KV cache 78.7 GiB = 3.39 M tokens, 25.9 full-length requests. A correct code
answer; a well-formed tool call (`finish_reason: tool_calls`). Five sequential 256-token requests, single stream:
**time to first token 0.16 s mean (0.13–0.22), decode 142.5 tokens/s mean (136–158), end to end 131.5 tokens/s.**
**What was learned on the way:** (1) with vLLM's default kernel selection on this GPU the first start sat silent for
over 40 minutes with one core busy — a Python-side kernel compiler for the FP4 paths plus FlashInfer's start-up
autotuning, whose results vLLM 0.24.0 does not persist (`kernel_warmup.py`: `_FLASHINFER_USE_PERSISTENT_CACHE =
False`), so every start would pay it. The two flags above avoid both, and the numbers above are without them. Worth
an unattended run later to see what the default path costs cold and what it gains in tokens/s. (2) A server image
keeps compiled kernels either under `/tmp` or in the container's home directory; the script now points every image
at one named volume and copies a container's caches into it before removing it. (3) A one-minute, model-free
`probe` (one tiny call into each family of compiled GPU code in an image) answers "can this image run on this GPU
at all" before a 45 GB model load does.
**Not yet done for Phase 6:** the same on the 3g MIG slice in tenants mode (the act 2 shape, and the number to quote),
a warm restart to measure what the kept caches save, the governed form (a quadlet delivered through RHEM, D143),
exposure beyond loopback, and the isolation test (training on another slice while it serves). Unknown 7 stays
"retired on paper" for the product image and is retired in practice for serving this model on this GPU.

## D155 — The isolated eval rig is verified beside the running loop; the host half of training is ready to install

**Date:** 2026-09-19
**Test (D153's "verify first"):** `51-eval-rig.sh run verify-rig modelcar 5 1000` while production collected and an
LLM server sat idle on the same GPU. The rig's sim rendered on the GPU at a full 30 fps (301 and 304 frames in 10 s);
cube poses crossed from the sim to the runtime image over gazebo transport (3 of 3) — the first time `GZ_PARTITION`
has been exercised across containers; the rig's graph had exactly one `/run_policy` server; no goal was rejected in
the rig or in production; production scored 6 of 9 in those six minutes, no worse than before. The pod was removed.
**Score: 3/5, mean 2.4 cubes, against the seeded smoke's 4/5, mean 2.6 (D149 addendum) — and seed by seed four of
the five agree exactly:** seed 1000 places one cube in both (the first episode of a cold policy), 1001 / 1003 / 1004
place three with step counts within 8 %, only seed 1002 differs (2 against 3). One cube on one seed is inside the
policy's run-to-run variation, and a paired evaluation puts both policies through the same rig. The design holds:
the governed policy is never stopped for an evaluation, and D132's collision cannot occur.
**Built and tested off the machine, now on it and not yet run:** the runner in container mode
(`RUNNER_MODE=inprocess`, `EVAL_MODE=request`; 76 tests written blind from the spec by a separate agent pass),
`flywheel-runner.container` as part of `fury-flywheel.target`, `flywheel-eval.path` / `.service`,
`50-runner-install.sh` (asks for the two MinIO keys on the terminal; they never reach a log),
`52-seed-incumbent.sh` (re-packs the pinned modelcar's `models/act` as the incumbent tarball in the hub's MinIO and
creates `episodes-data`, which otherwise only appears with the assembler's first push). `fury-mode` refuses to
switch while a training or an eval is running.
**State of the collection:** 111 of 160 successes on the hub two hours after it started, about 50 an hour; the
trigger stays unarmed until the runner is installed, the incumbent is seeded and the registry step has been checked
against RHOAI 3.5 (FURY-PLAN, Phase 5).

## D156 — Act 2's coding assistant is a host service that `fury-mode` owns; RHEM is shown on the fleet tenant

**Date:** 2026-09-19
**Decision (operator's):** the assistant runs as the plan's Phase 6 says — a quadlet on the host — started by
`fury-mode tenants` on slice `0:0` and stopped for every mode switch. It is not delivered through RHEM.
**Why:** RHEM earns its place where versions roll out: signed promotion, canary batches, health gates. The robot
policy uses all of that; the assistant is a static tenant and would use none of it. Nothing else on the machine
except the promoted policy is RHEM-managed either (sim, recorder, runner and eval rig are host units from git), a
device belongs to exactly one Fleet and flightctl has no conditional applications (so every other device would
carry a placeholder), and the live mode switch is simpler with a unit the host starts itself. RHEM's management of
many devices is demonstrated on its own tenant in act 2 — the CUDA-rendered scaled fleet on one of the slices —
without coupling it to the assistant. An RHEM-delivered variant was drafted before this was decided and is kept on
branch `fury-assistant`, unmerged, in case the "one control plane, two workloads" beat is wanted later; merging it
into `fury` would be a rollout to the enrolled host.
**Built (not yet run):** `tools/host/fury/flywheel/llm-assistant.container` (+ `llm-cache.volume`): the image and
flags measured in D154 on `nvidia.com/gpu=0:0`, `--gpu-memory-utilization 0.90`, model read-only and offline, one
named volume for every compile cache, loopback only with no API key (a laptop uses an ssh tunnel; opening it to a
network is `--host` plus a firewalld rule for that network, an operator's decision), health by a Python one-liner
with a 20-minute start period, and `ExecCondition=` on the slice's CDI name — a non-zero condition skips the start
and leaves the unit inactive, neither failed nor restarted, so a boot or a stray start in flywheel mode does
nothing (a failed `ExecStartPre=` under `Restart=always` would loop for ever). `fury-mode` stops the unit in
`drain()` and starts it, non-blocking, once the four slices exist; `63-assistant-install.sh` installs the unit and
the new `fury-mode` and seeds the cache volume from the smoke test's. First run: the next time the flywheel can be
paused for an hour.


## D157 — The evaluation dashboard is this project's code, runs on the hub, and reads the pipeline's own evaluation records

**Date:** 2026-09-20 (UTC). **Status:** done on the Fury hub; first act 1 view still to come (FURY-PLAN Phase 5c, ledger L15).

**Decision (operator).** The dashboard that compares model versions was built for this project in a repository of
its own and has been handed back to it: the code is ours to change, it runs in the cluster, and nothing is named
after a person. It was imported unchanged (`hp-roscon-eval-dashboard` at `ee07e1f`) into `src/eval-dashboard/`,
its tests into `tests/eval_dashboard/`, and then changed here.

**Why it had to change, not just move** (from reading and running its code against our records): it could not read
the pipeline's evaluation records at all (no per-episode id or model version, the `eval-` label dropped on purpose,
flat `steps` / `duration_s`) and never read `eval_report.json` — so the screen showed two unpaired, whole-percent
rates while the gate, the PR and the registry carry the paired result; its Kafka path fetched from whichever bucket
a manifest named, not from the buckets it was configured with, so an instance pointed at separate comparison
buckets still took in live passes under the same label (separate buckets isolate writers, not that reader — the
D138 arrangement had this hole); episodes whose cube count could not be read counted as policy failures, which the
curator deliberately does not do; a development address was its default link.

**What it does now.** `SOURCE_MODE=eval` reads one promotion run — `eval/<run_id>/eval_report.json` and the two
per-policy records, from MinIO or from a directory — newest run or a pinned `EVAL_RUN_ID`, and *replaces* what it
holds when the run changes; a **Paired result** panel shows fixed / broken / net / p / verdict from the report
verbatim (the pipeline's result is authoritative and is never recomputed for display) with the fixed and broken
seeds; the Kafka path keeps to the configured buckets and counts what it skips; sensor-fault and unfinished
episodes are left out of the rate and shown as "not scored"; rates to one decimal; a version missing from the
versions file takes its size from the last `-ft<N>` in its name; UBI 9 Python base, non-root, read-only. Built
blind: tests written from the spec by one agent (275 cases), implementation by another that never saw them —
313 passed on the first run, no arbitration.

**On the hub.** Image built by the existing signed Tekton pipeline (`tools/hub/build-eval-dashboard.sh`; arm64, about
a minute, Rekor 6), pinned by digest in `gitops/flywheel/eval-dashboard.yaml`: `eval-dashboard` (paired evaluation)
and `eval-dashboard-live` (curated + rejected buckets and the manifest topic), each with a Service and an edge
Route, their own ServiceAccount under restricted-v2, linked to each other and from the flywheel dashboard's header.
First real data: the comparison instance shows tonight's rehearsal run (3 paired seeds, FAIL) read through the
read-only user; the live instance shows 271 live episodes for the serving model.

**The read-only MinIO user** is `eval-readonly` (Secret / ConfigMap / Job `minio-eval-readonly-*`; the Secret
hand-created in `minio` and in `flywheel`), and may also read `episodes-data/eval/*`. **Found on the way:** the
setup Job had never worked on this hub — under an arbitrary uid `mc` could not save its alias
(`mkdir /.mc: permission denied`), every later command failed, and without `set -e` the Job still ended "done" and
Complete; the user did not exist. Fixed (`MC_CONFIG_DIR`, `set -eu`, a checked attach) and made an Argo `PostSync`
hook, so a policy change is applied on the next sync and the immutable pod template is never patched. Lesson, again
(D149): a step that reports success is not evidence; the evidence here was a listing of the users.

**Still open.** For the show the comparison instance is pinned to the act 1 run (`EVAL_RUN_ID`) — otherwise the
newest run wins, whatever it is; the page has not yet been looked at by a human in a browser; the image lives as a
tag of the runtime image's repository (told apart by the `eval-dashboard-` prefix) until Phase 8b moves images to
the cluster's registry; other branches and the development cluster still carry the old resource names.

## D158 — The first promotion on the Fury hub: teacher -> act-v2-ft160, merged and serving in 1 min 38 s; modelcar tags made permanent

**Date:** 2026-09-20 (UTC). **Status:** done (FURY-PLAN ledger L1-L3); the reset for rehearsing it (L4) is still to build.

**What ran.** The Fleet and the trigger lineage were first put at the teacher (`6f69dd2`): its existing modelcar
(`d5e5897f…`) got this hub's signature through the cluster's own `cosign-sign` Task (`tools/hub/cosign-image.sh`,
Rekor 8) and, before the pin (D149), `tools/host/fury/54-teacher-modelcar-check.sh` showed the device pulling it
under its own signature policy, its weights byte for byte the staged checkpoint (`8388c067…`), and those weights
serving on the GPU (3 seeded episodes, goals accepted). RHEM rolled the teacher out in about a minute. Then one
governed run, `promote-act-v2-ft160` (`9fb233e8`): the runner reused the candidate's existing checkpoint and both
existing evaluation records for seeds 1000-1359 (its log says so: "skipping assemble/train", "reusing existing
record"), computed the paired report - **295/360 = 81.9 % -> 333/360 = 92.5 %, fixed 57, broken 19, net +38,
p < 0.0001, PASS** - and the pipeline packaged a multi-arch modelcar (`d741db2b…`, which also ends the amd64-only
data image), signed it, registered it and opened PR #7. Merged 11:30:21; the policy container restarted 11:31:41 and
published `act-v2-ft160` at **11:31:59 - 1 min 38 s from merge to serving**, the new image pulled fresh under the
device's policy; RHEM reported Healthy at 11:32:25. The evaluation page is pinned to the run (`918a1d3`).
Training and evaluation themselves were exercised on this machine by the unattended run of the night before
(`01c25f4e`: 189 episodes, 62,244 steps, 100 paired seeds, gate FAIL on wrong-scene data, as it should).

**What broke, and the rule that came out of it.** The pipeline tagged the index with the bare candidate name. The
candidate's name already carried an image (`bdb513ca…`, still pinned by the development cluster's Fleet), so the
push moved the tag - and the registry, within the same minute, stopped serving that image by digest (404) **and
deleted its signature tag**. Restoring took the registry's tag history and an owner's login, twice (image, then
signature). Fix (`77769e2`, with tests): every image of a run is tagged `<candidate>-<run id, 8 chars>[-<arch>]`
for good, and the bare candidate tag is only moved to the newest index afterwards. **Rule:** a digest that anything
pins must own a tag that no later run will move; a moving tag is a convenience, never the only reference.

**Smaller things from the same morning.** `53-stage-promotion.sh` took a lone `force` for its directory and, under
sudo, lost the refusal message with the terminal - both fixed; 28 other host scripts share the second pattern
(inbox). zsh expands `$VAR:a...` as a path modifier - braces, or a script file, for anything with a colon after a
variable. `oc run --rm` trips the local guard's recursive-delete pattern - create, read the log, delete by name.

## D159 — The sim's camera streams reach the browser through the hub's router, over https

**Date:** 2026-09-20. **Status:** built; needs one privileged step on the host (`tools/host/fury/15-camera-port.sh open`).
**Supersedes, on this hub, the viewer-side half of D124.**

**Problem.** The flywheel dashboard made the viewer's browser fetch the two MJPEG streams straight from the camera
bridge next to the sim (`http://<CAMERA_HOST>:8081`, D124). That design came from a dashboard that was itself served
over plain http on a NodePort. On this hub the dashboard is opened through its https Route, and a browser does not
load an http stream into an https page: the panels stayed at "Waiting for sim…" with the bridge up and answering
(checked from the host and from a laptop). The `sim-cameras` Route that already existed pointed at the in-cluster
sim Deployment, which has zero replicas here - 503.

**Decision.** The streams go through the router like everything else: Service `sim-cameras-host` without a selector,
an EndpointSlice naming the host's bridge (`10.20.0.1:8081`), the existing https Route `sim-cameras` in front, and
the dashboard takes the streams' base URL from `CAMERA_URL` (falling back to `http://CAMERA_HOST:8081`, so the
development arrangement is unchanged). One origin scheme for the whole demo, nothing to reach on the host from a
viewer's laptop, and it keeps working when the only path to the machine is the apps domain (Phase 8b). Cost: the
hub has to be allowed to open connections to the host on that one port - guests are refused by default
(`libvirt-to-host`, 06-network.sh). `15-camera-port.sh` adds `8081/tcp` to that policy, runtime and permanent,
without a reload (a reload drops libvirt's runtime zone binding under the running hub VM - the same care as the
metrics port).

**Stop-gap that worked meanwhile:** the dashboard's plain-http NodePort (`:30801`), where the http streams are not
mixed content.

**Addendum, same day - working, and one trap.** With the port open both streams come through the Route (200,
`multipart/x-mixed-replace`, about 1.2 MB in 5 s each). The trap: the EndpointSlice was first committed next to the
Service under `gitops/flywheel/`, the app went Synced, and the Route kept answering 503 - Argo CD excludes
`EndpointSlice` (and `Endpoints`) from what it manages, silently. It now lives in
`tools/hub/manual/sim-cameras-endpointslice.yaml`, applied by hand once per hub (`argocd/README.md`). "Synced" says
that what Argo manages matches git; it says nothing about what Argo was never going to create.

## D160 — Phase 6 on the slice: the assistant serves from MIG slice `0:0` at about 245 tokens/s, up in under three minutes

**Date:** 2026-09-20. **Status:** measured (FURY-PLAN ledger L9 done); reaching it from the booth and the isolation
beat are still open.

`63-assistant-install.sh` installed the unit and seeded its cache volume from the smoke test's compiled kernels
(1.6 GB); `tools/hub/fury-switch.sh tenants` stopped the RHEM-managed policy through flightctl, drained the loop,
turned MIG on (`9,19,19,19`) and `fury-mode` started `llm-assistant.service` on `nvidia.com/gpu=0:0` (3g.126gb).
From the switch to `Application startup complete`: **under three minutes** (weights 65 s, engine init 64 s; no
autotune, caches warm) - against 5 min 46 s for the first cold start in D154. KV cache available on the slice:
66.6 GiB.

Measured on the slice with `61-rhaiis-smoke.sh` (the unit's own endpoint, loopback): a short coding answer and the
model card's tool-call example (well-formed, `finish_reason: tool_calls`); then five single-stream requests of 256
tokens: **time to first token 0.132 s, decode 245.7 tokens/s, end to end 218.7 tokens/s**, identical across the
five. D154's 142.5 tokens/s was taken with MIG off while the flywheel's sim and policy shared the GPU; the slice
has its compute to itself, which is the point of act 2. **The number to quote for the slice is 245 tokens/s
single stream.**

## D161 — The assistant is reached from inside the cluster only: a Service in front of a host-side listener, no Route

**Date:** 2026-09-20. **Status:** built; the host step (`64-assistant-expose.sh open`) is the operator's.

D156 left the assistant on loopback and called opening it "an operator's decision". Decided with the operator: it
goes through the hub, the way the camera streams do (D159) - but one step tighter, because this is an API without a
key and not a read-only stream. **Host:** the API stays on `127.0.0.1:8000`; systemd listens on the hub-side address
`10.20.0.1:8001` and forwards (`llm-assistant-proxy.socket` / `.service`, `systemd-socket-proxyd`), and
`64-assistant-expose.sh` lists that port in `libvirt-to-host`. The mode switch does not manage it: with the
assistant down the forward is refused. **Hub:** Service `assistant.flywheel.svc:8000`, no selector, its endpoint
applied by hand (`tools/hub/manual/assistant-endpointslice.yaml`; Argo CD does not manage EndpointSlices, D159) -
and **no Route**. Browsers reach the model only through something in the cluster that serves them over https: the
dashboard's backend for the chat panel, a developer workspace for the editor. That also removes the private-CA
question for in-cluster clients (plain http on the cluster network to the host bridge). Known and accepted: peers
on the operator's tailnet reach `10.20.0.1:8001` directly, as they do every port on that address. If the API is ever
given a Route or a wider network, it gets a key first (`--api-key`, held server-side).

**Addendum, same day - the forwarder did not survive SELinux, and the panel is dropped.** The socket unit failed at
once: `Failed to create listening socket (10.20.0.1:8001): Permission denied` - systemd is not allowed that bind
for socket activation here, and the port's label cannot be read without root. No relabelling by guesswork: the
assistant's container now listens on `10.20.0.1:8000` itself, the pattern the camera bridge and the metrics
exporter already prove on this host (`2b805c3`); `64-assistant-expose.sh` only lists the port and removes the failed
forwarder. Cost: the API is no longer loopback-only by construction - what keeps the uplink out is the address it
binds and the firewall, as for the other two. Operator, same day: **no chat panel** - the tenant is shown doing
coding in a developer workspace, which is enough; the Service's only clients are workspaces.

## D162 — Act 2's coding tenant is shown doing coding: a Dev Spaces workspace whose agent uses the model on the slice

**Date:** 2026-09-20. **Status:** working end to end, walked through by the operator; polish items below.

**Decision (operator).** The large-model tenant is not shown as a chat box. It is shown doing the work it is for: a
coding agent in a developer workspace closes a failing test in this repository, with the model served from MIG slice
`0:0` of the same machine. The Red Hat way to show that is **OpenShift Dev Spaces** - the workspace is the product,
the assistant is brought along and pointed at a privately served OpenAI-compatible endpoint, which is also what Dev
Spaces 3.30 documents (its "AI provider" feature, Technology Preview, uses a terminal coding agent as the worked
example). No chat panel is built.

**What was checked before building.** A spike from a laptop first: the same agent against the slice (vLLM 0.24,
`qwen3_coder` tool parser, streaming, 131k context) finished a real multi-file task in a scratch clone in 166 s -
40+ tool calls, none malformed, recovered by itself from a regression it introduced, 349 tests green when re-run
independently. That retired the one risk that could not be engineered around (tool-call parsing between this
server and this client).

**What runs.** Dev Spaces 3.30.1 on the arm64 hub through GitOps (`argocd/devspaces-app.yaml`,
`gitops/devspaces/checluster.yaml`): both operators installed in under a minute, every operator image has an arm64
build, the gateway needed no patch; workspaces never idle, the extension registry is the embedded one, nothing is
fetched from outside. The workspace image (`src/dev-workspace`, built and signed by the hub's pipeline through
`tools/hub/build-dev-workspace.sh`) is UBI 9 Python with the agent and its search tool baked in by pinned,
checksum-verified release, its offline switches on, its state under `/tmp` for an arbitrary uid, the evaluation
dashboard's test dependencies installed, and a permission list: edit files, run pytest and a few read-only
commands, nothing else, no web. The model is `http://assistant.flywheel.svc:8000/v1` (D161). `devfile.yaml` pins
the image and carries `run-tests`, `start-agent`, `reset-demo`. The scenario is branch **`demo/coding-task`**:
`fury` plus one failing test file (seven tests for "longest failure streak" - how long a policy was stuck failing
before it recovered), `DEMO-TASK.md` (the task as the agent reads it) and `DEMO-RUNBOOK.md` (the presenter's
notes). The prompt on stage is one line: *Read DEMO-TASK.md and do what it says.* The operator ran it in the
workspace: all tests passing.

**Still to do.** Time the task on the hub and note terminal quirks in the runbook; pre-pull the image and the
editor for a cold node; an in-cluster git remote for the no-internet mode (Phase 8b) - a workspace start clones
from GitHub today; the link from the demo's landing page; the agent binary and its search tool are third-party
dependencies whose terms are the operator's to vet.

## D163 — The fleet tenant: micro-VM devices under RHEM, robots' worlds as pods, one slice rendering every camera

**Date:** 2026-09-20. **Status:** decided with the operator; nothing built yet. Builds on D142's addendum and D143
(MIG on, one slice is the rendering tenant, physics on CPU, a showcase and not a second flywheel).

**What the tenant is for (operator, restated):** show a fleet that is **scaled, running, and managed by RHEM in
correct ways**, with CUDA visibly at work. It is not a flywheel, and how well the existing policy does the task on
the ray-traced pixels does not matter. The five-seed zero-shot measurement D143's second addendum left open is
therefore no longer a gate, and look tuning and a separate evaluation rig are dropped
(`docs/internal/CUDA-RENDERER-PLAN.md` keeps its findings; its sections on tuning and the rig are not the plan).

**Shape.**
- **Devices are micro-VMs**, because a RHEM device is an operating system with an agent on it, and a pod is not:
  clones of one prepared RHEL base image on copy-on-write overlays (1-2 vCPU, about 2 GiB, boot in about half a
  minute), each the robot's *computer* - flightctl agent, the policy as a Fleet-delivered workload, labels, staged
  rollout (canary, then batches), signature verification on every device. One command scales the fleet up or down.
  The policy image is in the base image's store or comes from the hub's registry, never 4 GB per device from
  outside.
- **The robots' worlds are pods on the hub:** physics-only sims (no camera sensors), one per robot, scaled with
  `oc scale`, each in its own network namespace (no `/run_policy` collision, D132). The world is not the device.
- **One MIG slice is the rendering tenant:** MuJoCo-Warp ray-traces every robot's two cameras in a batch; frames go
  to the robots' computers and to a "fleet wall" page that shows every robot's view.
- Expected scale: 16-24 robots on this machine beside the hub (CPU bound). flightctl's device simulator stays a
  possibility for a bigger number in the fleet view, clearly labelled, **not** pursued until the real fleet's
  ceiling is known.

**Addendum, same day - the base image is RHEL image mode (bootc).** No aarch64 KVM guest image was at hand, and
the operator preferred the alternative on its merits: the devices' base is a bootc image (the RHEL 10 bootc
base + the flightctl agent + podman, the policy image pre-loaded), turned into a qcow2 on this host with
bootc-image-builder, and the micro-VMs are copy-on-write clones of that. It is the canonical RHEM device
flow, it needs nothing downloaded by hand, it takes care of first-boot identity, and it leaves room for an
OS image rollout as a later beat. First a short spike that the builder and the base behave on this aarch64
host with its 64k-page kernel. OpenShift Virtualization was looked at for the VMs and set aside with
evidence: the operator is offered for arm64, but the hub is itself a VM and its node has no `/dev/kvm` -
guests there would run under software emulation.

**Addendum, same day - image mode works on this host** (`tools/host/fury/79-bootc-spike.sh`). The pull secret is
entitled to `rhel10/rhel-bootc:10.2` and `rhel10/bootc-image-builder:10.2` (both pulled in 41 s, arm64); RHEL
repositories are visible inside a container on this host, so an image build needs **no activation key**;
bootc-image-builder ran on the 64k-page aarch64 host with SELinux enforcing - derived image plus qcow2 in **75 s**,
894 MiB on disk; the guest, in the micro-VM shape (UEFI, 2 vCPU, 3 GiB, no balloon), **answers on ssh 17 s after
`virt-install` starts** (systemd reports 5.4 s), runs a 4k-page kernel, SELinux enforcing, 208 MB used when idle,
and `bootc status` shows the booted image. The golden-image design with a throwaway guest, an activation key and
a sealing step is dropped for a Containerfile and one builder run.

**Addendum, same day - what one slice renders, measured** (`tools/host/fury/25-mjwarp-batch.sh`, slice `0:3`, the real
scene, two 640x480 cameras per robot, shadows on, every world with its own arm motion and cube placement, RGB
copied back): **about 228 camera pairs a second, whatever the batch size** - 195 with one world per call, 221 with
4, 226 with 8, 227 with 16, 228 with 32. The renderer is compute bound at about 4.3 ms per world; batching buys
15 %, not a multiple. That is **7 robots at 30 fps from one 1g.31gb slice**, 15 at 15 fps, and the slice's memory
is almost untouched (477 MiB of 31 GiB at 32 worlds; read-back 75 MB per batch in 4.6 ms). So the fleet's size is
set by render rate, resolution and how many slices render, not by memory: the policy looks at 480x480 and
decides about once every 1.7 s, so 480x480 at 15 fps serves about 20 robots from one slice, and a second slice
doubles whatever is chosen. The earlier geometry check (rootless, CPU device): the renderer draws the same state
as Gazebo to 0.09 px on cube centroids and 0.99 IoU on the arm, with the two corrections of the design plan
(field of view, wrist-roll offset) both confirmed necessary.

**Addendum, same day - the rendering tenant runs on slice `0:3`** (`73-fleet-renderer-install.sh`; unit
`fleet-renderer.service`, owned by `fury-mode` like the other tenants). CUDA works for the image's non-root user
with every capability dropped, SELinux-confined, on a UBI 10 base (UBI 9's glibc is too old for the ray tracer's
library - found by the build's own self-test). Twenty robots sent by the test sender, 480x480, two cameras each,
shadows on: **83 ms a batch, 12 frames a second per robot** - 480 ray-traced camera frames a second from one
1g.31gb slice, a little above the 640x480 benchmark, so the cost is not mostly pixels; the 15 fps target overruns
at twenty robots and holds at sixteen. Host memory peak 1.0 GiB, the wall's JPEG 57 ms. The operator looked at the
wall: every arm moving. What it showed was the test sender's synthetic motion (a cube sliding, nothing picked
up) - the worlds, physics with replayed recorded motion, are the next piece.

**Addendum, same day - stages A and B are live: twelve managed robots, twelve worlds, one wall.**
*Worlds (B).* A world costs **1.4-1.6 vCPUs and 540 MiB on the hub** - half again what the same world cost on the
host's own cores. Twelve put the 32-vCPU node at 64% and the render batch at 54 ms of its 66; sixteen ran (76%,
14.5 frames a second) but left the hub VM with every vCPU busy during a restart, and twenty did not schedule: the
node's cpu *requests* were 97% booked with a quarter of its cpu idle. The request is now 500m (the platform wins
when cpu is short, and a workspace or a pipeline run still schedules), the scale script caps at sixteen, and **the
fleet's size is twelve**. The arms replay **thirty recorded episodes of the policy's own actions** (extracted from
the training dataset into a hand-made ConfigMap), in the collection scene: only the green cube is re-placed, by
3 cm. Open loop, that lifts the two cubes that never move into the tray and the green one when it lies right - what
the operator recognised as the policy's usual two of three. The built-in motion is the fallback, in the policy's
order. The wall lays a fleet that divides evenly out as a full grid (4 x 3).
*Robots (A).* The golden image holds the OS, the agent, podman, cloud-init and a firewall - **no application
image**: the runtime image has whiteouts, which a container build cannot carry into an embedded store, so every
robot pulls its two images itself and verifies them itself, which is also the better story. OS image 34 s, disk
65 s, 940 MiB; 40 GiB thin root. The builder is handed the host's own image store, as the spike did: podman
refuses a store that shows up at another path inside the builder's container, and the builder is privileged either
way. Three first-boot faults, each of which would have hit every clone, found by reading the clone's disk from the
host (`82-fleet-vm-peek.sh` - there is no other way into these guests, by design): cloud-init 24.4 rejects
netplan's `to: default` route and with it the whole pre-network stage (the clone boots with no address); the agent
ordered after cloud-init's *final* stage closes a cycle through `multi-user.target`, which systemd breaks by never
starting the agent (it now waits for the network stage, where its config is written); RHEL's cloud-init makes the
fqdn the hostname, and the approval check accepts `fleet-vm-NN` only. After those: twelve clones enrolled by
themselves, were approved by the checked script (dry run first; the canary alone, then batches), joined
`Fleet/robots` (live in `gitops/rhem/fleet-robots.yaml`), and **all twelve were Online, UpToDate and Healthy about
25 minutes after the first one booted** - the canary's own pull-verify-start took under ten. Host with twelve VMs,
twelve worlds and three tenants: load about 50 of 72 cores, 267 GB of memory free.
*How it is shown (operator, same day).* The demo stays in tenants mode with everything running; act 1 is shown
from a recording, not by switching modes on stage. The fleet's beats therefore assume a
running fleet and reset only what they touch: robots stopped and started with `81-fleet-scale.sh <N>` (back in
about a minute - no approval, no pull), a rollout walking the Fleet's batches.

**Staging, each stage showable on its own:** (A) the micro-VM factory, enrolment and approval tooling, the robots'
Fleet - RHEM at scale, no sim yet; (B) world pods + the rendering tenant + the wall, the arms driven by recorded
motion; (C) the policy on each device closes the loop with its world.

## D164 — Tenants mode's four slices: coding assistant, robot zero, training, fleet rendering

**Date:** 2026-09-20. **Status:** decided with the operator; `0:0` runs, the other three are to build.

| Slice | Size | Tenant |
|---|---|---|
| `0:0` | 3g.126gb | the coding assistant, shown through a Dev Spaces workspace (D160-D162) |
| `0:1` | 1g.31gb | **robot zero**: the enrolled GPU host's own policy, delivered by RHEM and pinned to this slice |
| `0:2` | 1g.31gb | training: real ACT fine-tunes while everything else serves |
| `0:3` | 1g.31gb | the fleet's rendering tenant (D163; about 228 camera pairs a second) |

**Why robot zero and not a second renderer.** The fleet is bounded by host CPU (16-24 micro-VMs), and one rendering
slice already covers about twenty robots at 480x480 and 15 fps, so a second one would only smooth the wall - more
of the same. Robot zero adds what no other candidate does: **RHEM delivering a signed GPU workload and placing it on
a specific MIG slice** - governance of the partitioning itself, through the Fleet's `gpu_device` label, which was
built for this (D148) and never exercised - and **continuity between the acts**: the device and the signed model
the audience watched being promoted in act 1 keep serving in act 2 instead of going dark when MIG turns on. Robot
zero is the fleet's first robot: its world is a physics-only sim like the others, its cameras come from the
rendering tenant, it is on the wall; only its policy runs on a slice instead of a CPU. Considered and set aside: a
second training job (runner-up, cheap), a second served model (needs a purpose on stage), perception over the
fleet's camera streams (worth revisiting with time), a robot foundation model (days of risk). If robot zero slips,
`0:1` becomes a second renderer or a second training job at almost no cost.

**What it takes, after the renderer exists:** the host device gets `gpu_device=<MIG uuid of 0:1>` (MIG UUIDs are
stable across mode switches here); `fury-switch.sh tenants` starts the policy app instead of leaving it stopped;
`fury-mode` stops refusing a policy on a slice; the host's sim unit gains the physics-only variant for tenants mode.

**Addendum (2026-09-20, late) - robot zero runs: all four slices hold their tenants.** The policy stays what it was:
the Fleet's signed `act-inference`, placed on slice `0:1` by the device's `gpu_device` label (the slice's MIG UUID),
with no change to the Fleet. The label path, built for D148 and never exercised, worked the first time: label set
with the app stopped, RHEM rendered `AddDevice=nvidia.com/gpu=MIG-...` into the quadlet, the agent applied it, the
app started - `Running / Healthy`, and the process table shows one tenant per GPU instance (assistant, robot zero's
policy at 618 MiB, the training tenant, the renderer). The host adds three units without a GPU, owned by `fury-mode`
like the other tenants (`74-robot-zero-install.sh`): the physics-only world from the fleet worlds' signed image,
reporting to the renderer as `r00` and carrying the Zenoh router; a bridge that publishes the renderer's two
pictures on the policy's image topics, 15 a second, the 480x480 picture centred in a 640x480 frame and padded,
never stretched (same vertical field of view as the training camera); and the flywheel's coordinator with
recording off. **Robot zero records nothing**: recording off stops the bags, and what keeps the curator clean is
that a physics-only world starts no episode emitter; no unit mounts `/data` or holds a credential, all three run
with a read-only root and no capabilities, and the installer refuses units that would change any of that. An
independent review found the one way around it: robot zero's router puts a policy and camera topics on the host
under MIG for the first time, so a stray start of the flywheel's own recorder would have recorded ray-traced
pixels into the flywheel's bags. The recorder and the flywheel's sim now start only with MIG off, and the installer
refuses a checkout in which they do not. In a switch (`tools/hub/fury-switch.sh`, `tools/hub/robot-zero.sh`) the
policy is stopped first and started last and its placement changes only while it is stopped, so it never runs on
the whole GPU under MIG; the label change carries nothing the hub manages, keeps the hub's version lock and is
checked afterwards. Known cost: with the hub unreachable, flywheel mode has no policy until the label can be
cleared. **First episodes on rendered pixels, closed loop, no tuning: the first full episode placed all three cubes
in 59 s, the second too, the third did not** - a count, not yet a rate; D166's open question waits for a few
dozen. The flywheel page's camera panel now shows robot zero's two cameras.

## D165 — Isolation, measured: the assistant's numbers do not move while the slice next to it trains

**Date:** 2026-09-20 (tenants mode, MIG `9,19,19,19`). The training tenant (D164; `72-training-tenant-install.sh`)
fine-tuning ACT on slice `0:2`, the coding assistant serving from slice `0:0`, the same five-request bench as D160
run **while the tenant trains**: time to first token **0.135 s**, decode **246.6 tokens/s**, end to end 218.9 -
against 0.132 s / 245.7 / 218.7 on the idle GPU. No measurable difference: that is the isolation beat, and the GPU
tenants dashboard shows both slices busy at once.

**The training numbers from the same run** (1g.31gb slice, batch 8, 8 data-loader workers): about **10 steps/s** -
**0.091 s a step computing, 0.010 s waiting for data**. Two things follow. The data-loader fix works: the first full
fine-tune on this machine waited 0.18 s a step for data with the library's four workers (D158's night run, 4 h 21
min for 62,244 steps); with eight it waits a twentieth of that and the step is compute bound. And one seventh of
the GPU computes this model's step only about 1.4 times slower than the whole GPU did with the sim and the policy
beside it (0.064 s): the same 62,244 steps would take about 1 h 35 min on a 1g slice. The tenant's round is set to
9,000 steps, a quarter of an hour. The lerobot flags the tenant adds (`--policy.optimizer_lr`, `--seed`) are
accepted by the image's version.

## D166 — One running system, two stories: the demo stays in tenants mode and the flywheel is told from there

**Date:** 2026-09-20. **Status:** decided with the operator. Supersedes the "two acts, two GPU modes" staging for
the demo path; `fury-mode flywheel` stays for off-stage work.

**Why.** Switching modes on stage reconfigures MIG and stops and starts every tenant, the fleet and the worlds over
a network: minutes of dead air, and trust in everything coming back. Only one thing in the flywheel ever needed the
whole GPU - the simulator rendering its own cameras, which a MIG slice cannot do - and the rendering tenant (D163)
now does that with CUDA. So the system is left running in tenants mode, each piece shown is reset by itself, and
as little as possible is shown from a recording.

| Beat | From tenants mode | Live? |
|---|---|---|
| Collect: the policy at work, its cameras | **robot zero** (D164): the RHEM-delivered signed policy on slice `0:1`, cameras from the rendering tenant, on the wall as `r00`; the flywheel page's camera panel points at those streams | live |
| Train | the training tenant on `0:2`: real ACT fine-tunes (D165: a slice is no slower here, training is loader bound) | live |
| Evaluate, gate | the pinned governed run on the evaluation page - hours of machine time in any mode, never a live beat | recorded results in a live page |
| Promote | merge the PR; RHEM rolls the signed model to the host on its slice **and** through the robots' Fleet in batches - the fleet's rollout beat and act 1's promotion are one beat | live |

**What this does not claim.** The episodes seen live run on rendered pixels, not the pixels the model was trained
on, and they never enter the flywheel's storage, its trigger count or the live evaluation page (a hard requirement
on robot zero). On stage the statement is "this is the system that produced that promotion, still running" - not
"what you are watching trained the model".

**Open, decided by a measurement.** Robot zero's success rate on rendered pixels is unknown (geometry matches to
0.09 px, the look does not). It comes for free once robot zero runs. Poor: the collect beat leans on recorded
motion, as the fleet's worlds do, and on the page's clips. Good: moving the whole flywheel onto rendered pixels -
collect, train and evaluate in tenants mode, a closed loop with no mode switch at all - becomes worth its cost (a
re-collection, an overnight run, the governed trainer and the evaluation rig taught to use a slice and the
renderer). Not before the number is in.

**Follows from this.** The promotion must be repeatable (`tools/hub/reset-promotion.sh`, so far only run dry). A
recording is a fallback for the network-dependent promotion, not a required part. Two runbooks replace the act
structure: *setup* (a fresh RHEL install to tenants running) and *show* (what to present, what to say, how to
reset each piece). FURY-PLAN's phase text still speaks of two acts and is reworded when those runbooks are written.

## D167 — Tenant numbers beside GPU numbers: each slice is shown with what its tenant is doing

**Date:** 2026-09-20. **Status:** built, installed, verified end to end (`8dc87a3`).

The GPU tenants dashboard showed four slices being busy; "the training slice is actually learning" was visible only
in a terminal. Now each tenant has a headline beside its GPU panels, and the dashboard names each slice by its
tenant. A small exporter on the host (`75-tenant-metrics-install.sh`, `10.20.0.1:9401`; unprivileged, read-only
root, no capabilities, no GPU, not owned by `fury-mode`) publishes the training tenant's loss, step, steps per
second, update and data-wait seconds and round - read from the tenant's own round log and ledger, mounted read-only,
never the journal - and the rendering tenant's frames per second and robots live from its status page. The step
comes from the progress bar, because the trainer abbreviates `step:` above 999. The hub scrapes it like the GPU
exporter (a static target, no Service), and scrapes the assistant's own vLLM metrics on its existing port through
a keep-list of four series - configuration only. The loss curve is a sawtooth, one line per round, titled
"Training tenant - loss (round N)": these rounds are real fine-tunes, nothing from them is promoted, and nothing on
the page calls them the governed run. The slice names are a static map of GPU instance ids (1, 11, 12, 13), which
follow from the fixed MIG layout; because a changed layout would shift ids and put a wrong name on a slide rather
than blank a panel, the installer's `slices` verb prints instance id, MIG device, profile, the tenant on it and the
dashboard's name for it, row by row - a pre-demo check. `tools/hub/training-watch.sh` follows the training output
in a terminal for a projector (step and bar, loss, steps per second, data wait; control sequences stripped;
`--raw` for the untouched lines). First readings through the hub: round 28 ending at loss 0.061, 8-9 steps a
second with three other tenants working, renderer 15 of 15 frames a second with 13 robots live. An independent
review found nothing that had to be fixed before it ran; its four smaller findings (a slow client holding the only
thread, the mount exposing the path where a model-hub token would live, raw journal bytes reaching a projected
terminal, the id map) were fixed first.

**D166, addendum (2026-09-20, night) - the live lane: robot zero's episodes are judged, not kept.** In tenants
mode the flywheel page showed live cameras next to "Loop stopped", an empty log and a frozen counter. It now tells
the story live, all day, without weakening the requirement above (`b0a6514`).
*Host.* Robot zero has a fourth unit, `robot-zero-emitter`: the signed sim image's own episode emitter, a listener
beside the episode loop that moves and resets nothing. It posts each episode's summary - a small JSON,
`"dataset_path": null`, there being no bag - to one address: `curator-show`, a second Deployment of the curator's
own code on the hub. *Why the verdict is honest.* The gates judge physics (completeness, cubes on the tray,
smoothness), not pixels, and none looks at a bag, so the shared code needed no exception. *Why the separation is
structural, not a flag on the real lane.* `curator-show` has its own small volume, not the node's episode
directory; no sync agent, mirror or consumer mounts it; it runs under its own service account without the
namespace's host-mount grant, in a security profile that cannot mount a host path; it has no object-storage or
Kafka variable, no credential, no token, and a NetworkPolicy that lets nothing out of the pod. On the host the
installer holds the emitter's unit to exactly that one address - refusing the flywheel curator's port, a second
address, that line in any other unit, a fallback directory off the container's tmpfs, any proxy variable (the
emitter also switches proxies off), continued lines and quoted values - and checks the rule again on the unit
quadlet generated. D164's sentence that robot zero stays clean because no emitter exists is superseded: one
exists, and it can reach only a curator that keeps nothing. *What the open port let in, found by review and fixed
on both lanes.* Episode fields reached the page's markup unescaped - and that page has an unauthenticated control
that scales the real lane - so every field is now validated by the receiver (a body must be a JSON object of the
emitter's shapes, 64 KB at most on the live lane, or it gets a 400 naming the field) and escaped by the page; the
receiver is threaded with a ten-second timeout; a record that cannot be judged leaves the queue once; the live
lane prunes everything and the page reads only the newest small files however full a volume is; on the live lane
the page refuses its clear and scaling controls. Both curators move together on one code revision (the flywheel's
own was rolled once onto it). The Service keeps the client's address and the policy admits the GPU host alone;
tailnet members, forwarded by the host, are not told apart from it (accepted: the admin network). *What the page
says.* "Serving - live lane"; beside the counter "live lane: judged, not kept" and the time the count began; the
bar is passes modulo 160, and at 160 it holds for five minutes and says "this is where a governed training run
would start. On the live lane nothing was kept and nothing was started; the count begins again." The pinned
evaluation page and the clips stay the record. *What "not kept" means exactly.* The newest 300 verdict records
and a totals file sit on a throwaway volume so the page has a log; no bags; nothing reaches object storage, Kafka,
the trigger or a dataset. A hand-written total - the rehearsal-only shortcut for the 160 moment - shows itself by
its start time and is put back before an audience. **First minutes on the machine:** the emitter heard the episode
loop from a container of its own and took the policy's label (`act-v2-ft160`); the first episode after the restart
failed and was rejected (0 of 3 cubes), the next two placed all three and passed at score 1.000 - the same
outcomes the episode loop logged; the page read 2 passed, 1 rejected, 2 / 160; the flywheel's own curator received
nothing.

## D168 — Edge Manager from Red Hat's product chart: the screen says the product's name, and every image is the product's

**Date:** 2026-09-20. **Status:** done (`0457cfb`); 13 devices stayed enrolled and healthy through it.

The device-management page read "Flight Control", with the project's logo and documentation links. That was never
a decision: the chart sets the UI's branding flag from the chart's own NAME (`IS_RHEM` is true only for a chart
called `redhat-rhem`), and D024 had installed the project's chart from `quay.io/flightctl/charts`. Only the UI
image had been swapped for Red Hat's (D146/D147), and only because the project's UI image has no arm64 build;
D135/D136 had looked at console integration and set it aside. Nothing had looked at the name on the screen.

**Decision.** Install from Red Hat's chart, `redhat-rhem` 1.3.0 on `charts.openshift.io` - same templates, same
version, same release name and values, so the same 105 objects are updated in place. The standalone Route UI stays:
it is one of the product's two documented presentations; the other lives in the ACM console, needs ACM (D135: did
not fit this hub), and the console plugin's menu entries all attach to ACM's perspective, so without ACM it has
nothing to show. **Not claimed:** both charts declare x86_64 only, so an arm64 hub is outside what either declares;
the images exist for arm64 and run - whether that is supported is a question for the product team.

**How it went.** Before: no rollout in flight, 13 devices healthy, a SQL dump of the database written beside its
data directory (576 KB). A local render of both charts with our values: same objects and names, no selector
changes; every image from `registry.redhat.io`; two labels changed on everything (each pod restarts once); the
key-value store is redis-7 instead of valkey-8 (in memory, nothing kept); the database only changes image
(PostgreSQL 16 both); the generated secrets are held at their live values by D031's `ignoreDifferences`. One thing
the render does not show: the database-migration Job is an ordinary object and a Job's pod template cannot be
changed in place, so the sync refused that one object - deleting the completed Job let Argo CD create it from the
new chart, where it ran and completed (same version: nothing to migrate). The chart's certificate and
encryption-key jobs ran again and left every secret unchanged. After: app Synced and Healthy, 13 devices Online,
UpToDate and Healthy, both Fleets valid, the page titled "Red Hat Edge Manager". Going back is the same change in
reverse (the project chart's registry Secret is still there), with the dump as the last resort.

## D169 — The promotion beat across both Fleets, run and timed: reset, re-open, merge

**Date:** 2026-09-21 (UTC). **Status:** done once end to end; this is the rehearsal and the show path (D166).

**Reset** (`tools/hub/reset-promotion.sh`, one push: the revert of PR #7's merge plus the commit that brings the
robots' Fleet along - the merge predates the two-Fleet PR step). Host serving the teacher on its slice about 50 s
after the push. Then the first template change ever to walk the twelve robots: waves of 1, 2, 3, 5 and 1 (canary,
25 %, 50 %, the rest split by `maxUnavailable: 5`), each under a minute **including each robot's first pull of the
teacher model** (about 230 MB) - which also settles that the amd64-era teacher image mounts on the arm64 robots.
All 13 devices UpToDate and Healthy about 5 minutes after the push.
**Re-open** (`tools/hub/reopen-promotion.sh --open`): PR #8, "Promote act-v2-ft160 (82% -> 92%) - re-opened for a
showing", opening with "Re-proposes PR #7 (pipeline run 9fb233e8, 2026-09-20): same signed image and
transparency-log entry, same gate record. The pipeline did not run again; nothing was re-measured." and quoting
PR #7 unchanged; one commit, every Fleet file pinned alike. No pipeline run, no image, signature, registry version
or evaluation record written - the Model Registry entry for `act-v2-ft160` stays the one the governed run made
(operator: one pristine entry; no further full run for this candidate). The laptop's login may push a branch but not
open a pull request on this repository, so the script opens the PR **with the token the pipeline opens its own
with**, read from the hub's Secret into the one call - never a file, never printed - and before anything is pushed
(operator's instruction; tested).
**Merge** (operator, merge commit, 00:03:55 UTC):

| after the merge | what |
|---|---|
| 45 s | both Fleets carry the new template (the ResourceSync's poll) |
| 1 min 32 s | the host serves `act-v2-ft160` on slice `0:1`, Healthy (D158's first promotion, MIG off: 1 min 38 s) |
| 3 min 23 s | twelve of twelve robots UpToDate - waves of about 16 s each, nothing pulled: every device holds both models |
| 3 min 39 s | all 13 devices Healthy |

Robot zero's episode loop saw the new label and the flywheel page's badge followed; the live lane logged the
restart's failed episodes as rejects, truthfully. So the beat fits a stage: merge, narrate for a minute and a half
while the host comes back on its slice, and the fleet is done before the fleet has been introduced. It needs every
enrolled robot running (a shut-off robot stalls its batch for 30 minutes), a reachable GitHub for the merge, and
the hub; it does not need the registry once both models are on every device. **Between showings:** reset (about 5
minutes), re-open (seconds; the PR can sit open until the beat), merge.

**D166, addendum (2026-09-21) - the live lane gets its own episodes page.** The Live episodes page is the governed
collection's record and rightly shows nothing of robot zero, so a third instance of the evaluation page's image,
`eval-dashboard-show` (`2bfe0a5`), reads the show curator's `curated/` and `rejected/` directories - read-only
sub-path mounts, so unjudged records and the totals file are not in the pod at all - under its own service account
with no grant, credential, token or way out of the pod. It says what it is ("Live episodes - live lane ... judged,
not kept: the newest few hundred verdicts, no recordings") and links to the collection's page; the flywheel page
links "Live episodes" to it on the live lane only, and the paired page now calls the other one "Live episodes
(collection)". Two findings on the way: the show curator keeps the newest 300 records of *each* verdict, so both
directories read whole tend to 50 % whatever the policy does - the page reads the newest 300 files of the two
together, which are exactly the last 300 episodes judged (a test ties the window to the curator's setting); and the
evaluation page wrote `rollout.steps` into a table row unescaped (masked by the curator's validation, fixed for all
three instances). One rebuilt, signed image (Rekor 24), pinned by one digest in all three, which a test holds
equal. On the machine: admitted under the restricted profile; the paired page still pinned to `9fb233e8` with 720
episodes, the collection's page still on its own 18; the new page, an hour into the lane and across the promotion
cycle, shows `act-v2-ft160` with 28 of 37 episodes placing all three cubes on rendered cameras and the teacher 6 of
9 - small counts, but they are what D166's open question asked for, and they keep growing by themselves.
