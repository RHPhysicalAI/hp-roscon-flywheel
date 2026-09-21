<!-- This project was developed with assistance from AI tools. -->
# Recording the booth loop

The voiceless video: captions and title cards, no presenter, about six minutes, made to repeat. This page is the whole
plan for it. Part 1 is what you record, in the order you record it. Part 2 is what the edit does with it. At the
recorder only Part 1 matters.

Set-up (clean browser profile, light theme, logins done before the recorder starts, what may never be in frame) and
the review afterwards are sections 2 and 7 of `docs/internal/FURY-RECORDING.md`. Page addresses:
`docs/internal/FURY-URLS.md`.

## Three rules

1. **You record the whole frame, every time.** One fixed 16:9 region of the screen - the browser full screen with
   its toolbar hidden is the easiest - and the same region for every capture. You never record a part of a page.
   When the loop shows one panel large, the edit punches in. For that to stay sharp, record at the screen's native
   resolution (a 1920x1080-point region on a Retina screen records as 3840x2160) and export the loop at 1920x1080.
2. **Every shot is a held frame.** No smooth scrolling. When a capture says to move - scroll, click, change tab -
   move, then hold still for the seconds it says. The moves are cut out.
3. **One capture per page, recorded long. The loop uses short pieces of it.** "Record for 12 minutes" and "25 s in
   the loop" are about the same capture: the first is your job, the second is the edit's.

Eleven captures, about 40 minutes of recording. C1 to C9 change nothing and can be redone freely. C10 uses up the
coding workspace (a reset puts it back). C11 is the promotion: one take per reset cycle, so it comes last.

## Part 1 - what you record

Before the first capture: `tools/hub/fleet-status.sh` shows 13 devices healthy, the promotion's pull request is open,
the fleet wall shows `r00` to `r12`, and the flywheel page's counter is somewhere under 150 and was not set by hand.

### C1 - GPU dashboard, all four slices (1 min)

- **Open:** GPU tenants dashboard. Time range: last 15 minutes. Auto-refresh on.
- **Frame:** section "All slices" open - "Compute busy" and "Memory used", each with its four-line legend (assistant,
  robot zero, training, rendering). Collapse every section under it, so their headers read as a list of the tenants.
- **Do:** nothing. Record 60 s.
- **Gives the loop:** B01.

### C2 - Training tenant, dashboard and terminal together (3 min)

- **Frame:** the arrangement of the README's training picture: the dashboard's section "Training tenant - slice 0:2"
  in the upper part of the frame, a terminal in the lower part. Terminal: light theme, large font, a prompt with no
  user or host name, scrollback cleared.
- **Start it before recording:** `FURY_SSH=gb300@hp-fury tools/hub/training-watch.sh`, and wait for the first step
  line. Pick a moment when the round is between 10% and 90%, so no round ends in the take.
- **Do:** nothing. Record 3 min (a line prints about every ten seconds).
- **Gives the loop:** B06. Dashboard and terminal show the same round and the same step because they were recorded
  together - do not record them separately.

### C3 - Flywheel page (12 min, hands off)

- **Open:** flywheel page, `?theme=light`.
- **Frame:** the whole page as it loads - cameras on the left, rollout, task performance and the episode log on the
  right, "Flywheel progress" along the bottom. It fits without scrolling; if the progress bar is cut off, zoom the
  browser out one step and keep that zoom for this page.
- **Do:** nothing. An episode ends about every 80 s.
- **Stop when** you have seen all three: a PASS arrive in the log, a REJECT arrive, and one clean run on camera
  with three cubes on the tray. About 12 minutes.
- **Gives the loop:** B02 (the cameras), B03 (the verdict and the log), B04 (the progress bar) - three punch-ins
  from this one capture.
- The model badge at the top reads `upstream-act-teacher`. That is the "before" of the promotion.

### C4 - Live episodes (1 min)

- **Open:** "Live episodes - live lane" page, light.
- **Do:** hold the top of the page 20 s. Page down once. Hold 15 s.
- **Gives the loop:** B05.

### C5 - Paired evaluation (1 min)

- **Open:** paired evaluation page, light.
- **Frame:** "Paired result" (PASS, fixed 57, broken 19, net +38, 92.5% vs 81.9%) and under it the two policy
  cards. They fit in one frame.
- **Do:** nothing. Record 45 s.
- **Gives the loop:** B07.

### C6 - The pipeline run and the model registry (1 min)

- **Open:** OpenShift AI. Two pages: the run `promote-act-v2-ft160` on its "Graph" tab, and the model registry's
  `soarm-act` on its "Versions" tab.
- **Do:** hold the run graph 25 s - every step green, from `eval-gate` to `open-promotion-pr`. Go to the registry
  page. Hold 15 s.
- **Gives the loop:** B07b.

### C7 - Fleet wall (3 min)

- **Open:** fleet wall. Nothing else in the frame.
- **Do:** nothing. Record 3 min, and check that all thirteen tiles are moving before you start.
- **Gives the loop:** B12. Real speed only.

### C8 - One robot in Edge Manager (1 min)

- **Open:** Red Hat Edge Manager, "Devices". All 13 rows in the frame - zoom the browser out a step if they are
  not, and keep that zoom for Edge Manager.
- **Do:** hold the list 15 s. Click one robot that is Online, Up-to-date and Healthy. Hold its page 20 s.
- **Gives the loop:** B13, and the list at rest as spare footage.

### C9 - Argo CD (30 s)

- **Open:** Argo CD's application list, every application Synced and Healthy.
- **Do:** nothing. Record 20 s.
- **Gives the loop:** the second half of B15.

### C10 - The coding agent (about 5 min; uses up the workspace)

- **Before recording:** in the workspace run `reset-demo`. Maximise the terminal panel. In a second browser tab open
  the GPU dashboard with "All slices" and "Coding assistant - slice 0:0" open, the rest collapsed, last 15 minutes.
- **Do, in one capture:**
  1. In the terminal: `python -m pytest tests/eval_dashboard -q`. Wait for `7 failed, 342 passed, 5 skipped`.
     Hold 5 s.
  2. Type `opencode`. Type the prompt: `Read DEMO-TASK.md and do what it says.` Hands off until it reports
     `349 passed, 5 skipped` - about three minutes. Hold 5 s.
  3. Change to the dashboard tab. Hold 20 s. The run is the hump in "Generated tokens per second"; across the same
     minutes the training and rendering lines in "Compute busy" stay flat. If the two sections do not fit in one
     frame, hold "All slices" 15 s, then "Coding assistant" 15 s.
- **Gives the loop:** B14. The dashboard's own time axis ties the run to the flat neighbours, so nothing has to be
  recorded side by side.
- **Another take:** `reset-demo`, then `/new` in the agent.

### C11 - The promotion (about 7 min; one take per reset cycle; last)

- **Before recording:** three tabs, in this order - the open pull request ("... re-opened for a showing"), Edge
  Manager's "Devices" with all 13 rows in frame, the flywheel page. On the pull request, the merge button's menu is
  set to "Create a merge commit". `tools/hub/fleet-status.sh`: 13 healthy, all twelve robots running.
- **Do, in one capture, without stopping:**
  1. Pull request, top of the page: the title and the first sentence readable. Hold 15 s.
  2. Move down to where the description names the transparency-log entry. Hold 10 s.
  3. "Files changed": both Fleet files, one digest and one version each. Hold 15 s.
  4. Back to the conversation, down to the merge box. Hold 3 s. Click "Merge pull request", then "Confirm merge".
     Hold on "Merged" 3 s. **This click is 0:00.**
  5. Change to the Edge Manager tab. Hands off for four minutes: the GPU host updates first, then the robots in
     waves of 1, 2, 3, 5 and 1. Wait until all 13 rows read Up-to-date and Healthy again.
  6. Change to the flywheel page tab. The badge reads `act-v2-ft160`. Hold 15 s.
- **Gives the loop:** B08, B09, B10, B11 and the first half of B15. Every time in a caption is read off this
  capture's own timeline.
- **If it fails after the merge,** or for another take: `tools/hub/reset-promotion.sh --yes` (about 5 min until 13
  healthy), then `tools/hub/reopen-promotion.sh --open`. Never record the reset and show it as the promotion.
- Leave the flywheel page alone for a quarter of an hour afterwards: the policy's restart interrupts an episode,
  and the lane shows that reject.

**Spare footage,** if there is time: C1 again with each tenant's section open in turn, 20 s each; C7 again during
C11's rollout from a second take.

## Part 2 - what the loop uses

In loop order. "Show" says whether the edit uses the full frame or punches in. A caption is at most two lines, held
5 s or longer; " / " separates one caption from the next. Every sped-up stretch carries its factor on screen ("x5")
for as long as it runs.

| # | Title on screen | From | Show | Loop | Captions |
|---|---|---|---|---|---|
| - | Opening card | - | card | 6 s | below |
| B01 | One GPU, four tenants | C1 | full frame | 25 s | One HP ZGX Fury workstation. One NVIDIA GB300 GPU, split into four isolated slices. / A coding assistant. A robot's policy. A training tenant. A renderer for a whole robot fleet. / All four are running right now. |
| B02 | Robot zero | C3 | punch in: the two cameras | 25 s | Robot zero, working from its two cameras. / Its policy is a signed image, delivered by Red Hat Edge Manager, on a GPU slice of its own. / Both cameras are ray-traced on another slice. |
| B03 | Every episode is judged | C3 | punch in: rollout, task performance, episode log | 35 s | Every episode goes through the curator's real gates: did the cubes land on the tray, was the motion smooth. / PASSED: all three cubes on the tray. / REJECTED: (the reason the page gives, word for word). |
| B04 | The counter | C3 | punch in: "Flywheel progress" | 20 s | Passed episodes are counted up to 160. / At 160 a governed training run would start. / On the live lane nothing is kept and nothing is started. The count begins again. |
| B05 | Live episodes | C4 | full frame | 15 s | Robot zero's last 300 episodes, as the curator judged them. Success by model label. |
| B06 | The training tenant | C2 | full frame, x4 | 25 s | The training tenant: real ACT fine-tunes, round after round. 9000 steps a round, about a quarter of an hour. / About 10 steps a second on a 1g slice, while three other tenants work. |
| B07 | How a model earns promotion | C5 | full frame, then punch in: "Paired result" | 30 s | The governed run's paired evaluation: candidate against incumbent on 360 identical seeded scenes. / 295 -> 333 successes. 82% -> 92%. / 57 scenes fixed, 19 broken. Sign test. Gate: PASS. |
| B07b | Gated, signed, registered | C6 | full frame | 20 s | The pipeline: gate, package, sign, register, open the pull request. / The promoted model in the Model Registry. |
| B08 | A promotion is a pull request | C11 steps 1 and 3 | full frame | 20 s | This pull request re-proposes a promotion the pipeline made earlier: same signed image, same gate record. / A promotion is a change in git: a signed model image's digest and a version, for both Fleets. |
| B09 | Merge | C11 step 4 | punch in: the merge box | 8 s | A person merges. Red Hat Edge Manager does the rest. |
| B10 | The host follows | C11 step 5, first 1 min 40 s | full frame, sped up | 20 s | (time) - both Fleets carry the new version. / (time) - the GPU host serves the promoted model on its slice. |
| B11 | Twelve robots, in batches | C11 step 5, the rest, then step 6 | full frame, sped up; the badge at real speed | 30 s | The twelve robots follow in batches: canary, 25%, 50%, the rest. / (time) - all twelve robots updated. (time) - all 13 devices healthy. / Nothing was pulled: every device already holds both models. / The flywheel page now shows the promoted model. |
| B12 | The fleet | C7 | full frame | 20 s | Twelve robots and robot zero. Every camera is ray-traced with CUDA on one GPU slice. / 13 robots at 15 frames a second, two cameras each. |
| B13 | One robot | C8 | full frame | 15 s | Each robot is a RHEL image mode virtual machine. / It enrolled itself, was approved, and pulls and verifies its own signed images. |
| B14 | A coding tenant on the same GPU | C10 | full frame, steps 1-2 sped up; step 3 real speed | 50 s | A coding agent in a Dev Spaces workspace. Its model is served on the largest slice of the same GPU. / One prompt: "Read DEMO-TASK.md and do what it says." / Failing tests. The agent reads, writes, runs the tests, corrects itself. All passing. / The assistant's tokens per second rise. The training and rendering slices do not move. / Measured: 246.6 tokens a second while the slice next door trains, 245.7 with it idle. |
| B15 | Signed, logged, verified | C11 step 2, then C9 | full frame | 20 s | Every image is signed. Every signature has an entry in a transparency log. / Each device checks both before it runs anything. / Everything here is delivered by GitOps. |
| - | Closing card | - | card | 8 s | below |

B09 to B11 carry one burned-in timer that starts at the merge click and shows real elapsed time, also while the
picture is sped up. Reference times from the measured rollout: both Fleets at 0:45, the host serving at 1:32, twelve
robots at 3:23, all healthy at 3:39. The captions use this take's own times.

About 6 min 30 s. To get under six minutes drop B05 and B13 first. Never drop B08's first caption.

**What stays on screen.** A strap, top left, small, for the whole loop: `Live system, recorded - one workstation,
one GPU, four tenants`. For B07 and B07b it reads `Recorded results - the governed run`. Under it, the segment's
title from the table. Full-screen chapter cards, 3 s each, only before B01, B02, B06, B08, B12, B14 and B15.

**Opening card**

> One workstation. One GPU. Four tenants.
> A robot and its fleet, a training tenant and a coding assistant - all running at once on an HP ZGX Fury.
> Everything in this video is the real system, recorded as it ran. Sped-up parts are labelled.

**Closing card**

> OpenShift · OpenShift AI · Red Hat Edge Manager · Dev Spaces · RHEL image mode
> One workstation. 13 managed devices. Everything delivered by GitOps; every image signed, logged and verified.
> Ask us about it.

The closing card fades to the background the opening card starts from, so the loop's seam does not show.
