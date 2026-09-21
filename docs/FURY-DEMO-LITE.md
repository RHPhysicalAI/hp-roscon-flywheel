<!-- This project was developed with assistance from AI tools. -->
# Fury demo-lite: showing the running system

For a presenter who did not build this: a partner-facing colleague, a manager, a booth volunteer. The system is
already running. Someone technical - **the demo owner** - prepared it, opened the browser tabs in order and accepted
the certificates. You show what is running and talk to it. The full technical demo is `docs/FURY-DEMO.md`.

- **Browser only.** No terminal, no commands, nothing to merge, reset, approve or delete. Click only what a stop
  names. You never need a password: if a page asks for one, skip that stop.
- **7 to 10 minutes** for stops 1-7 and the close. Every stop works alone: with two minutes, do stops 1 and 2.
- **Numbers come off the screen or off this page,** never from memory.
- **Anything deeper than this page: the demo owner.** Saying so is a good answer.

| Stop | Tab, left to right | Min | The point |
|---|---|---|---|
| 1 | GPU tenants dashboard | 1 | one GPU, four tenants, all running now |
| 2 | Flywheel page | 1-2 | robot zero working, every attempt judged live |
| 3 | Live episodes - live lane | under 1 | the running score, by model |
| 4 | Paired evaluation | 1-2 | how a model earns promotion |
| 5 | GitHub pull requests, then Red Hat Edge Manager | 1-2 | the promotion's evidence |
| 6 | Fleet wall, then Edge Manager again | 1-2 | twelve managed robots and robot zero |
| 7 | GPU tenants dashboard again | 1 | training shares the GPU |
| 8 | OPTIONAL: the coding workspace | 3-5 | a coding agent using the big slice |
| 9 | no tab needed | under 1 | close |

## The story in 60 seconds (no screen needed)

> This is one workstation with one GPU. The GPU is split in hardware into four slices, and each slice has its own
> tenant, all running right now: a coding assistant, a robot's control model, a training job, and a renderer that
> draws the camera pictures for a fleet of robots.
>
> The second story is how a robot's model gets better under control. We call it the flywheel. A robot works, and
> every attempt is judged. A governed run produces a candidate model and tests it against the current one on 360
> identical scenes. The result was 82 percent to 92 percent, and it passed the gate - a rule, not an opinion. The
> new model was packaged and signed, and proposed as a change in git. A person merged it. Red Hat Edge Manager
> rolled it out: the GPU host was serving the new model a minute and a half after the merge, and all twelve robots
> within three and a half minutes. Every device checks the signature itself before it runs anything.
>
> All of it is delivered from git, on OpenShift, OpenShift AI, Red Hat Edge Manager, OpenShift Dev Spaces and RHEL
> image mode. What I am showing is the system that produced that promotion, still running.

## Words to use, words to avoid

| Say | Never say | Why |
|---|---|---|
| "judged, not kept" | that the episodes on screen trained the model, or are training data | They are judged live and thrown away. The line is: "this is the system that produced that promotion, still running". |
| "the governed run", "the governed run's evaluation" - and its results as the page shows them | numbers or details that are not on the page or in this document | Say what the page shows. Anything about how the run was carried out goes to the demo owner. |
| "the training tenant" | "the flywheel's training", "this is training the next model" | Its rounds are real fine-tunes, and nothing from them is promoted. |
| "a re-showing of a recorded promotion: same signed model, same gate record" | that a re-opened pull request is a new promotion or a new measurement | Its first sentence says so. Say it out loud. |
| "object storage" | any storage product's name | Audience-facing wording. |
| "this is what is running" | "this is supported", "Red Hat supports this configuration" | Promise no support statement for anything shown. Support questions go to the demo owner. |
| roles: "the demo owner", "a presenter" | any person's name | |
| a success rate read off the page | a fixed success rate for robot zero | The page keeps counting; the number moves. |

## Stop 1 - One GPU, four tenants (1 min)

- **Tab:** GPU tenants dashboard -
  https://edge-perses-observability.apps.sno-flywheel.local/projects/observability/dashboards/gpu-tenants
- **What you are looking at:** a monitoring page for the one GPU in this workstation. The GPU is split with MIG
  (Multi-Instance GPU: the GPU's own hardware partitioning) into four slices. Each slice belongs to one tenant - one
  workload that has the slice to itself - and the page names every slice by its tenant.
- **Say:**
  - "One workstation, one GPU, four workloads. All four are running right now, not one after the other."
  - "The split is in the GPU's hardware. Each tenant has its own compute and its own memory."
  - "The tenants: a coding assistant, robot zero's control model, a training job, and a camera renderer."
  - "Beside each slice's GPU numbers you see what the tenant is doing with it: tokens, training steps, frames."
- **Point at:** the group "All slices" at the top: four lines named by tenant, all busy at once. Then the four
  groups below it, each titled with its tenant and slice, such as "Coding assistant - slice 0:0".
- **If asked:**
  - *Is it really isolated?* "Measured: 246.6 tokens a second with training running next door, 245.7 without."
  - *What machine is this?* "An HP ZGX Fury workstation with an NVIDIA GB300, running RHEL 10."
- **Hand off:** how the slices are made or changed: the demo owner.

## Stop 2 - Robot zero at work, judged live (1-2 min)

- **Tab:** Flywheel page - https://dashboard-flywheel.apps.sno-flywheel.local
- **What you are looking at:** two live camera views of "robot zero", a simulated robot arm that puts three cubes on
  a tray. The model moving it (the "policy": the robot's control model) is the signed one that Red Hat Edge Manager
  (RHEM: Red Hat's product for managing fleets of edge devices) delivered, on its own GPU slice. Each attempt is
  called an episode, and each is judged as it ends by the curator - the quality check that passes or rejects it.
- **Say:**
  - "This is robot zero. The model moving the arm is the signed model that Edge Manager delivered to this machine."
  - "Every attempt is judged live by the curator's real checks: all three cubes on the tray, and a smooth motion."
  - "This is the live lane: judged, not kept. Nothing you watch is stored, and nothing you watch trains a model."
  - "The counter climbs to 160. In a governed run, 160 good episodes is where training would start. On the live
    lane the page says so, and the count begins again."
  - "This is the system that produced the promotion I will show you, still running."
- **Point at:** the badge at the top, "Serving - live lane", with the model's name beside it. Then the "Flywheel
  Progress" counter and the words next to it: "live lane: judged, not kept".
- **If asked:**
  - *Do these episodes train the model?* "No. Judged, not kept. They are never stored, never part of a training run."
  - *How often does it succeed?* "The next tab has the running rate. I read it off the page."
  - *Is the picture real?* "The arm and the cubes are simulated physics. The pictures are ray-traced on the GPU by
    the rendering tenant, and the model runs on them with no tuning for them."
- **Hand off:** how robot zero is wired up: the demo owner.

## Stop 3 - Live episodes - live lane (under 1 min)

- **Tab:** Live episodes - live lane - https://eval-dashboard-show-flywheel.apps.sno-flywheel.local
- **What you are looking at:** the score for what you just watched: robot zero's most recent episodes, up to the last
  300, as the curator judged them, grouped by the model that was serving. The flywheel page's "Live episodes" link
  opens the same page.
- **Say:**
  - "This is the scoreboard for the robot you just saw: the last few hundred attempts, as judged."
  - "Success is strict: all three cubes on the tray."
  - "It is grouped by model, so after a promotion the old model and the new one sit side by side."
  - "Again: judged, not kept - verdicts only, no recordings. It does not decide a promotion; the next tab does."
- **Point at:** the note at the top ("Judged, not kept ..."), then the success cards, one per model, read aloud.
- **If asked:**
  - *Is this the 82 and 92 percent?* "No, that is the governed run's paired evaluation, next tab. A live count."
  - *What is the link "Live episodes (collection)"?* "The governed collection's own record. The live lane never
    feeds it, so it does not move during the demo."
- **Hand off:** the curator's checks in detail: the demo owner.

## Stop 4 - How a model earns promotion (1-2 min)

- **Tab:** Paired evaluation - https://eval-dashboard-flywheel.apps.sno-flywheel.local
- **What you are looking at:** the governed run's evaluation report, shown as the pipeline wrote it. The candidate
  (new model) and the incumbent (current model) each ran the same 360 scenes, cubes starting in the same places for
  both. Recorded results in a live page: an evaluation takes hours and is never a live step.
- **Say:**
  - "Before a new model goes anywhere, it is tested against the current one on 360 identical scenes."
  - "The current model succeeded on 295, the new one on 333: 82 percent to 92 percent."
  - "Scene by scene, the new model fixed 57 that used to fail and broke 19 that used to work. A statistical test, a
    sign test, checks that this is not luck."
  - "The gate is a rule, not an opinion: promote only if the gain is real. This one passed."
- **Point at:** the panel "Paired result": 57 fixed, 19 broken, verdict PASS. Then the two success cards, 82% and 92%.
- **If asked:**
  - *How was this run set up, how long did it take?* "I can speak to its results, on this page. The details of the
    run: the demo owner."
  - *What if a model fails the gate?* "Then it is not promoted."
  - *Is the page live?* "The page is live. The results are recorded, pinned to one governed run, id 9fb233e8."
- **Hand off:** the statistics, the pipeline, the model registry: the demo owner.

## Stop 5 - The promotion's evidence (1-2 min)

- **Tabs:** the promotion pull requests - https://github.com/RHPhysicalAI/hp-roscon-flywheel/pulls (click "Closed"
  to list the merged ones); then Red Hat Edge Manager - https://ui.flightctl.apps.sno-flywheel.local
- **What you are looking at:** a pull request is a proposed change to the files in git that say what every device
  must run. A promotion is exactly that: the new model's signed fingerprint (its "digest") and version, written for
  both fleets - a fleet is a group of devices managed alike; one holds the GPU host, the other the twelve robots.
  Number 7 is the recorded promotion. Number 8, "re-opened for a showing", proposes number 7 again so that the
  rollout could be shown again. The live merge belongs to the full demo, not to this one.
- **Say:**
  - "A promotion is a small change in git: the new model's signed fingerprint and version, for both fleets. Nothing
    reaches a device any other way. A person merges it - that is the approval."
  - "Number 8 is a re-showing of the recorded promotion, and its first sentence says so: the pipeline did not run
    again, nothing was re-measured. Same signed model, same gate record."
  - "When number 8 was merged, the GPU host was serving the new model a minute and a half later, and all twelve
    robots within three and a half minutes. Nothing was downloaded: every device already held both models."
    (Exact: host 1 min 32 s, twelve robots 3 min 23 s, all thirteen devices healthy 3 min 39 s.)
  - In Edge Manager: "And this is the result: both fleets carry the promoted model, every device up to date."
- **Point at:** on GitHub, open number 8: the "Merged" label and its first sentence, then the quoted table, 82% to
  92%, PASS. In Edge Manager, "Fleets" in the menu: `act-inference` (the GPU host) and `robots` (the twelve).
- **If asked:**
  - *Can you merge one now?* "Not in this walk-through. The live merge belongs to the full demo with the demo owner."
  - *What are the older promotions in the list?* "An earlier stage of the project. Today's is 7; 8 re-shows it."
  - *How do you go back?* "The same way in reverse, a change in git. Measured: the host back on the previous model
    about 50 seconds after the push, all thirteen devices healthy in about five minutes."
  - *Who signs the model?* "Packaging and signing happen on this machine's cluster. The signature goes into a
    transparency log - a record that cannot be quietly rewritten - and every device checks it before running."
- **Hand off:** anything deeper: the demo owner. **Never press Merge on anything.**

## Stop 6 - The fleet (1-2 min)

- **Tabs:** Fleet wall - https://fleet-wall-flywheel.apps.sno-flywheel.local/ ; then Edge Manager again, "Devices".
- **What you are looking at:** a wall of camera views: twelve robots and robot zero, tile `r00`. Every picture is
  ray-traced (computed from how light falls) by the rendering tenant on one small GPU slice. Each of the twelve
  robots' computers is a small virtual machine on RHEL image mode (the operating system delivered as one image).
- **Say:**
  - "Thirteen robots, all drawn by one small slice of the GPU, at 15 frames a second each."
  - "Each robot's computer is a small RHEL image mode machine. It enrolled itself with Edge Manager, was approved,
    and pulls and verifies its own signed software. They are clones of one image, each with its own identity."
  - "In Edge Manager they are the fleet called robots: twelve devices, all on the promoted model. The rollout went
    in waves of 1, 2, 3, 5 and 1: one robot first (the canary), then batches."
  - "From the first robot starting to all twelve enrolled, approved and healthy took about 25 minutes."
- **Point at:** the line under the wall: "13 live, 0 stale" and "15 of 15 frames/s". Tile `r00` is the robot from
  stop 2. In Edge Manager: thirteen devices, online, up to date, healthy. Do not open any "Actions" menu.
- **If asked:**
  - *Are these real robots?* "The arms and cubes are simulated. The computers are real managed machines, virtual."
  - *Is each arm driven live by its model?* "Robot zero is: tile r00. The other twelve arms replay recorded movements
    of the same model. What is live on those twelve is the rendering and the device management."
  - *How much can one slice draw?* "About 228 camera pairs a second. This wall uses thirteen robots at 15 frames."
- **Hand off:** how the robots are built, enrolled and scaled: the demo owner.

## Stop 7 - The training tenant (1 min)

- **Tab:** GPU tenants dashboard again (the tab of stop 1), group "Training tenant - slice 0:2".
- **What you are looking at:** a real model fine-tune running in rounds on one small slice while the other three
  tenants work. "Loss" is the training error: lower is better. It starts again every round, so the curve is a sawtooth.
- **Say:**
  - "This is the training tenant: real fine-tuning on one small slice while the other three tenants keep working."
  - "About ten training steps a second; the live number is on the page. A round is 9,000 steps, about a quarter of
    an hour, then the next round starts. Within a round the curve falls: it is learning."
  - "Nothing from these rounds is promoted. It shows training sharing the GPU - it does not make the next model."
- **Point at:** the panel "Training tenant - loss (round N)" and the number "Steps per second".
- **If asked:**
  - *Is this the next model for the robots?* "No. This is the training tenant: real fine-tunes, nothing promoted. A
    promoted model comes only from a governed run that passes the gate."
  - *Does it slow the others down?* "Measured: the coding assistant's speed did not change with this running."
- **Hand off:** what is being trained, and on what: the demo owner.

## Stop 8 - OPTIONAL: the coding workspace (3-5 min)

Only if the demo owner told you the workspace is reset and the agent is waiting. It is the one place you type, and
it can be shown **once**; after that it needs the demo owner's reset.

- **Tab:** Coding workspace (OpenShift Dev Spaces) - https://devspaces.apps.sno-flywheel.local
- **What you are looking at:** a developer's workspace in the browser. In one pane a coding agent waits for an
  instruction. It works with the coding model on the big GPU slice of this same machine. The project has one feature
  missing on purpose: 7 tests fail, 342 pass.
- **Do:** click into the agent's pane, type this sentence exactly, press Enter. Type it; do not paste by mouse.

  > Read DEMO-TASK.md and do what it says.

- **What will happen:** the agent reads the task, the tests and the code, writes the feature, runs the tests and
  corrects itself. Done when the screen shows `349 passed, 5 skipped`: about three minutes; have talk for five.
- **Say while it works:**
  - "It reads before it writes: the tests first, then the code. It runs the tests itself and fixes its own errors."
  - "It may edit files and run the tests, nothing else. If a refusal appears, that is the guard rail working."
  - "Nothing leaves the machine: the workspace, the agent and the model all run on it."
  - "The model answers at about 245 tokens a second; the first word comes in about an eighth of a second."
- **Point at:** on the GPU tenants dashboard, group "Coding assistant - slice 0:0": "Generated tokens per second"
  rises while the training and rendering slices do not move. That is the isolation.
- **If it stalls:** nothing new for a minute, or it goes in circles: say "it took a wrong turn; a fresh start usually
  takes a different path", and move on to the close. Do not try to fix it. The demo owner resets it later.
- **If asked:** *Which model?* "A Qwen coder model, served on the big slice." *Does the code go to a cloud
  service?* "No. The model runs on this machine's GPU."
- **Hand off:** the agent, its limits, the workspace's setup: the demo owner.

## Stop 9 - Close (under 1 min)

- **Say:**
  - "One workstation. One GPU, four isolated tenants, all running the whole time."
  - "A model earned its promotion by a rule, was signed, was approved by a person through git, and Edge Manager
    rolled it out to thirteen devices in about three and a half minutes. Every device verified the signature."
  - "Everything is delivered from git: the system follows what is written there."
  - "The products on screen: OpenShift, OpenShift AI, Red Hat Edge Manager, OpenShift Dev Spaces, RHEL image mode."
  - "This is the system that produced that promotion, still running."
- **If asked** *Is this supported? Can I buy it like this?* "This is what is running today. For what is supported on
  which hardware, the demo owner will put you in touch with the right people."

## If something looks wrong

| What you see | What to do |
|---|---|
| A certificate warning ("Your connection is not private") | It should have been accepted beforehand. For an address ending `.sno-flywheel.local` only: "Advanced", then proceed. It is the cluster's own self-signed certificate. |
| A camera panel on the flywheel page is blank | Reload once. Still blank: open the fleet wall tab once (its pictures are the ones embedded), go back, reload. Still blank: skip the stop. |
| A grey tile on the fleet wall, labelled "stale" | It is restarting. It comes back by itself; say so and carry on. Still grey when you finish: tell the demo owner. |
| A page will not load | Skip the stop. Tell the demo owner afterwards. |
| A page asks for a user name and password | You do not have one and do not need one. Skip the stop; tell the demo owner. |
| The counter is full and says "this is where a governed training run would start" | Nothing is wrong; that is the design. Say: "160 good episodes is where a governed run would start training. On the live lane nothing was kept and nothing was started; the count begins again." It clears after five minutes. |
| The screen has gone dark | Move the mouse or press a key. If the laptop wants a password, fetch the demo owner and tell the 60-second story meanwhile. |
| Anything else | Do not try to fix it. Note what you saw - a phone photo of the screen is ideal - and tell the demo owner. |

**Fallback:** if most pages fail, play the recorded video from the desktop and talk over it with the 60-second story.
Where a section of the video is sped up, a caption says so ("x8"). The timings it shows are real.

## Pre-flight card - for the DEMO OWNER

Before the laptop goes to a lite presenter, all of this is true. How to get there: `docs/FURY-DEMO.md`.

- [ ] One browser window, tabs in the order of the table at the top, every page loaded, every hostname's certificate
      accepted - the fleet wall's too, because the flywheel page embeds its pictures.
- [ ] GitHub tab on the "Closed" list, numbers 7 and 8 in view. No promotion pull request left open - or the
      presenter knows it is there and that it is not theirs to merge.
- [ ] Edge Manager logged in: thirteen devices online, up to date, healthy; both fleets on the promoted model; no
      rollout in progress. Fleet wall: twelve robots and `r00`, "13 live, 0 stale".
- [ ] Flywheel page: "Serving - live lane", both cameras moving, the count's start time honest (no hand-set total).
- [ ] GPU tenants dashboard: four slices named by tenant; the loss panel's title shows a round number.
- [ ] Coding workspace reset to 7 failing tests, the agent started on an empty conversation and waiting, panes
      arranged - or the presenter is told to leave stop 8 out.
- [ ] Show day: the training trigger is disarmed, so nothing fires unattended.
- [ ] Laptop on mains power; sleep, screen lock and screen saver off; notifications off; on the demo's network.
- [ ] The recorded video on the desktop, played once on this laptop (see `docs/internal/FURY-RECORDING.md`). The presenter
      has this document on paper or a second screen, and knows how to reach you.

## Not rehearsed yet

- This walk-through has not been given end to end by a non-technical presenter.
- The coding task has not been timed on this cluster: "about three minutes" is a comparable task in rehearsal.
