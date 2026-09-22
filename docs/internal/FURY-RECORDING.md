<!-- This project was developed with assistance from AI tools. -->
# The Fury demo - show script

The words, in order, for the full demo. Set-up, tabs and recovery are in `docs/FURY-DEMO.md` (sections 3 and 7);
this page is what you read while you present.

**How to read it.** Lines in `▶ CAPS` are what you do; plain lines are what you say. One thought per line - breathe
at the blank lines. **Bold** numbers are said exactly as written. `«…»` means read the live number off the screen.
The last line of each beat is the bridge into the next. `▶ EDIT POINT` marks the two places where the recording can
stop and jump ahead if the wait runs long.

Every cue names the page. Have them open in this order, left to right: the README on GitHub · the upstream demo
page · the arm's page · the HP ZGX Fury page · GPU dashboard · flywheel page · live episodes · paired evaluation ·
OpenShift AI (the pipeline run, then the Model Registry) · the pull request · Edge Manager · fleet wall ·
workspace · transparency log · Argo CD. Two terminals:
`training-watch.sh` running, and a shell in the checkout.

---

## 0 · Set the scene

`▶ README on GitHub, title in view.`

This is the project. Red Hat and HP: empowering physical AI from bench to fleet.

One workstation carries a robot-learning project from the engineer's bench to a managed fleet of robots.

It tells two stories.

One: one GPU, four tenants. The GPU is split in hardware into four isolated slices, and each slice does a different
job for a robotics team, at the same time.

Two: the flywheel. A robot's model gets better on its own work - and only ships when it proves it is better.

`▶ UPSTREAM DEMO PAGE.`

The robot, its simulation and the model are upstream work of the ROS physical AI community.

An SO-ARM101 arm in Gazebo, and a pretrained ACT policy that places three cubes on a tray. That is the task you
will see all the way through.

We did not build the robot. We built the platform around it.

`▶ THE ARM'S PAGE.`

The arm is the SO-101 from LeRobot: open hardware, six motors, the arm that most of the physical AI community learns
on.

Everything today is that arm, simulated - thirteen of them.

`▶ HP ZGX FURY PAGE.`

And the machine. An HP ZGX Fury.

A **72**-core Grace CPU. A Blackwell Ultra GPU with **252** gigabytes of memory. **496** gigabytes of system memory.

Everything you are about to see runs on this one box: the robots, the fleet manager, the training, the coding
assistant. Nothing is in a cloud.

*Bridge:* Let's look at what that GPU is doing right now.

---

## 1 · One machine, four tenants

`▶ GPU DASHBOARD. Hands off the mouse for the first two lines.`

This is one workstation.

One GPU, split in hardware into four isolated slices. Each slice has its own tenant.

`▶ POINT at the four names in "All slices", top to bottom.`

A coding assistant on the large slice.
A robot's control model.
A training job.
A renderer that draws the cameras for a whole fleet of robots.

All four are working right now. Nothing gets switched on or off during this demo.

An OpenShift hub runs in a VM on the same machine. It manages **13** devices: this host, and twelve robots.

*Bridge:* Let's start with the robot.

---

## 2 · Collect

`▶ FLYWHEEL PAGE. Let an episode play under the words.`

This is robot zero.

The GPU host is itself a managed device. Edge Manager delivered this signed policy to it, and a device label put it
on its own slice.

`▶ POINT at the two cameras.`

Both cameras are ray-traced by the rendering tenant, on another slice.

`▶ POINT at the verdict and the log.`

Every episode is judged, live, by the curator's real gates: did the cubes land on the tray, was the motion smooth.

Pass - `«read the latest PASS line»`. Reject - `«read the latest REJECT line and its reason»`.

`▶ POINT at "live lane: judged, not kept".`

This is the live lane: judged, not kept. Nothing you watch here is recorded and nothing becomes training data.

`▶ POINT at the counter.`

Passed episodes count up to **160**. At 160, this is where a governed training run would start.

*(pause)*

In a few minutes you will see a model promoted. This is the system that produced that promotion - still running.

`▶ LIVE EPISODES.`

The same robot's last **300** episodes, as the curator judged them. Success by model: `«read the rate»`.

*Bridge:* Judged episodes are one input. Training is the next.

---

## 3 · Train

`▶ GPU DASHBOARD, the "Training tenant" group. POINT at the loss curve.`

This is the training tenant. Real ACT fine-tunes, round after round, on one small slice.

A round is **9,000** steps - about a quarter of an hour. Then the next one starts.

`▶ TRAINING TERMINAL. Let a line or two arrive.`

About **10** steps a second, while three other tenants work next to it.

Nothing from these rounds is promoted. The point is simpler: training shares this GPU and disturbs nobody.

*Bridge:* So how does a model actually earn promotion?

---

## 4 · Evaluate and gate

`▶ PAIRED EVALUATION.`

This is the governed run's paired evaluation. Candidate against incumbent, on **360** identical seeded scenes.

`▶ POINT at the success rates.`

**295** successes became **333**. **82** percent to **92**.

`▶ POINT at fixed and broken.`

**57** scenes fixed. **19** broken. A sign test on those pairs.

`▶ POINT at PASS.`

The gate says pass. A rule, not an opinion.

Recorded results, in a live page. An evaluation takes hours; it is never a live beat.

*Bridge:* That result went into a pipeline.

`▶ OPENSHIFT AI, the run promote-act-v2-ft160, Graph tab. Every step green.`

This is that pipeline, on OpenShift AI. One run, top to bottom.

`▶ POINT down the graph as you name the steps.`

The gate - the decision you just saw.
Package the model as an image.
Sign it.
Register it.
Open a pull request.

Every step green. The pipeline does not deploy anything. It proposes.

`▶ OPENSHIFT AI, Model Registry, soarm-act, Versions tab.`

And the Model Registry: the version it registered, with the gate's result in its description - `«read the line
under the version name»`.

One entry, written once by the pipeline. Nobody edits it afterwards.

*Bridge:* And a pull request is something we can merge.

---

## 5 · Promote - LIVE

`▶ PULL REQUEST, title and first sentence on screen.`

This pull request is a re-showing of a recorded promotion.

Same signed image, same gate record. The pipeline did not run again, and nothing was re-measured.

What is live is the merge, and the rollout.

`▶ SCROLL to "Files changed".`

A promotion is two things in git: a signed model image's digest, and a version - for both Fleets. The host's, and
the robots'.

`▶ BACK to the merge box. Check the button reads "Create a merge commit".`

`▶ TIMING: Edge Manager reads git every two minutes, on the even minute. Click about ten seconds before one.`

A human merges. That is the only thing a person does.

`▶ CLICK "Confirm merge". START THE TIMER.`

Nobody logs in to a robot. Nothing is downloaded - every device already holds both models.

`▶ EDGE MANAGER, Devices. Stay until the host leaves "Up-to-date".`

Edge Manager picks the change up at its next check of git.

`▶ ⏱ host "Updating"`

There - the host first.

`▶ ⏱ host "Up-to-date" again`

The host is serving the promoted model on its slice.

`▶ ⏱ first robot "Updating"`

Now the robots, in batches: a canary, then a quarter of the fleet, then half, then the rest. Waves of **1, 2, 3, 5**
and **1**.

In the measured run the host was serving the new model a minute and a half after the merge, and all twelve robots
within three and a half minutes.

`▶ Do not wait. Go to beat 6 and come back to Edge Manager when it is done.`

*Bridge:* While the batches roll, the fleet itself.

---

## 6 · Fleet

`▶ FLEET WALL.`

Thirteen robots. `r00` is robot zero; the other twelve are the fleet.

Two cameras each, **480** by **480**, **15** frames a second - all ray-traced on one small slice.

That slice manages about **228** camera pairs a second.

`▶ EDGE MANAGER. OPEN one robot that is Online, Up-to-date, Healthy.`

Each of the twelve is a RHEL image mode virtual machine.

It enrolled itself, was approved, and pulls and verifies its own signed images.

One golden image. The OS image builds in **34** seconds, its disk in **65**, under a gigabyte.

Twelve robots were enrolled, approved and healthy about **25** minutes after the first one booted - each with its
own identity.

`▶ BACK to the device list. ⏱ all 13 "Up-to-date" and "Healthy".`
`▶ EDIT POINT: if they are not all there yet, stop talking here; the recording jumps to the finished list.`

And there is the rollout, finished: twelve of twelve. All thirteen healthy.

`▶ FLYWHEEL PAGE, if the badge is in view.`

The flywheel page now shows the promoted model.

*Bridge:* One slice is still unaccounted for - the big one.

---

## 7 · Coding tenant

`▶ WORKSPACE, agent ready.`

A coding agent in a Dev Spaces workspace. Its model runs on the large slice of this same GPU.

`▶ TYPE, and nothing else:  Read DEMO-TASK.md and do what it says.   ENTER.`

One prompt. There is a failing test suite in this checkout - **7** failures.

`▶ While it works, narrate what is on screen.`

It reads before it writes: the test file first, then the code.

It runs the tests itself, reads the failures, and corrects its own work. Nobody pastes an error back.

It may edit files and run the tests, and nothing else. Web access is off. If you see a refusal, that is the guard
rail working.

`▶ GPU DASHBOARD while it runs. POINT at "Generated tokens per second", then at the training and rendering lines.`

About **245** tokens a second, first token in about **0.13** seconds.

The assistant is busy - and the training and rendering slices did not move.

Measured with training running next door: **246.6** against **245.7** tokens a second. Unchanged. That is isolation.

`▶ WORKSPACE. Wait for "349 passed, 5 skipped".`
`▶ EDIT POINT: if the agent is still working, stop talking here; the recording jumps to the green line.`

All green - `«read the last line»`. The existing tests are what shows it broke nothing.

*Bridge:* One more thing, underneath all of it.

---

## 8 · Close

`▶ TRANSPARENCY LOG.`

Every image you saw is signed. Every signature has an entry in a transparency log.

Each device checks both before it runs anything.

`▶ ARGO CD.`

Everything is delivered from git: the platform, the Fleets, the promotion you just watched.

`▶ Face the audience.`

What was on screen: OpenShift, OpenShift AI, Red Hat Edge Manager, Dev Spaces, RHEL image mode.

One workstation. One GPU. Four tenants. Thirteen managed devices.

Ask us about it.

---

## Not said, ever

Nobody's name. No storage product's name - "object storage". Nothing about where the governed run was carried out.
No support statement for anything shown - say what runs. Never that the episodes on screen trained the model.
