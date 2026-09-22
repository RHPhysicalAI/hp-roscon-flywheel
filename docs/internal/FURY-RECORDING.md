<!-- This project was developed with assistance from AI tools. -->
# The Fury demo - show script

The words, in order, for the full demo. Set-up, tabs and recovery are in `docs/FURY-DEMO.md` (sections 3 and 7);
this page is what you read while you present. About 21 minutes.

**How to read it.** Every beat starts with the running clock and the tab. Lines in `▶ CAPS` are what you do; plain
lines are what you say. One thought per line - breathe at the blank lines. **Bold** numbers are said exactly as
written. `«…»` means read the live number off the screen. The last line of each beat is the bridge into the next.

Tabs, left to right: **1** GPU dashboard · **2** flywheel page · **3** live episodes · **4** paired evaluation ·
**5** pull request · **6** Edge Manager · **7** fleet wall · **8** workspace · **9** transparency log · **10** Argo CD.
Terminal A: `training-watch.sh`, running. Terminal B: the checkout.

---

## 1 · Open - one machine, four tenants `00:00` → `01:30`

`▶ TAB 1. Hands off the mouse for the first two lines.`

This is one workstation.

One GPU, split in hardware into four isolated slices. Each slice has its own tenant.

`▶ POINT at the four names in "All slices", top to bottom.`

A coding assistant on the large slice.
A robot's control model.
A training job.
A renderer that draws the cameras for a whole fleet of robots.

All four are working right now. Nothing gets switched on or off during this demo.

An OpenShift hub runs in a VM on the same machine. It manages **13** devices: this host, and twelve robots.

Two stories today. One GPU, four tenants - and how a robot's model gets better, under control. We call that the
flywheel.

*Bridge:* Let's start with the robot.

---

## 2 · Collect `01:30` → `04:30`

`▶ TAB 2. Let an episode play under the words.`

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

`▶ TAB 3.`

The same robot's last **300** episodes, as the curator judged them. Success by model: `«read the rate»`.

*Bridge:* Judged episodes are one input. Training is the next.

---

## 3 · Train `04:30` → `06:30`

`▶ TAB 1, the "Training tenant" group. POINT at the loss curve.`

This is the training tenant. Real ACT fine-tunes, round after round, on one small slice.

A round is **9,000** steps - about a quarter of an hour. Then the next one starts.

`▶ TERMINAL A. Let a line or two arrive.`

About **10** steps a second, while three other tenants work next to it.

Nothing from these rounds is promoted. The point is simpler: training shares this GPU and disturbs nobody.

*Bridge:* So how does a model actually earn promotion?

---

## 4 · Evaluate and gate `06:30` → `08:30`

`▶ TAB 4.`

This is the governed run's paired evaluation. Candidate against incumbent, on **360** identical seeded scenes.

`▶ POINT at the success rates.`

**295** successes became **333**. **82** percent to **92**.

`▶ POINT at fixed and broken.`

**57** scenes fixed. **19** broken. A sign test on those pairs.

`▶ POINT at PASS.`

The gate says pass. A rule, not an opinion.

Recorded results, in a live page. An evaluation takes hours; it is never a live beat.

What this hub did with that result: the gate decision, packaging and signing the model image, and a pull request.

*Bridge:* And a pull request is something we can merge.

---

## 5 · Promote - LIVE `08:30` → `12:30`

`▶ TAB 5, the open pull request. Title and first sentence on screen.`

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

`▶ TAB 6, Devices. Stay until the host leaves "Up-to-date".`

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

`▶ Do not wait. Go to beat 6 and come back to tab 6 when it is done.`

*Bridge:* While the batches roll, the fleet itself.

---

## 6 · Fleet `12:30` → `16:30`

`▶ TAB 7.`

Thirteen robots. `r00` is robot zero; the other twelve are the fleet.

Two cameras each, **480** by **480**, **15** frames a second - all ray-traced on one small slice.

That slice manages about **228** camera pairs a second.

`▶ TAB 6. OPEN one robot that is Online, Up-to-date, Healthy.`

Each of the twelve is a RHEL image mode virtual machine.

It enrolled itself, was approved, and pulls and verifies its own signed images.

One golden image. The OS image builds in **34** seconds, its disk in **65**, under a gigabyte.

Twelve robots were enrolled, approved and healthy about **25** minutes after the first one booted - each with its
own identity.

`▶ BACK to the device list. ⏱ all 13 "Up-to-date" and "Healthy".`

And there is the rollout, finished: twelve of twelve. All thirteen healthy.

`▶ TAB 2, if the badge is in view.`

The flywheel page now shows the promoted model.

*Bridge:* One slice is still unaccounted for - the big one.

---

## 7 · Coding tenant `16:30` → `20:30`

`▶ TAB 8, the prepared workspace, agent ready.`

A coding agent in a Dev Spaces workspace. Its model runs on the large slice of this same GPU.

`▶ TYPE, and nothing else:  Read DEMO-TASK.md and do what it says.   ENTER.`

One prompt. There is a failing test suite in this checkout - **7** failures.

`▶ While it works, narrate what is on screen.`

It reads before it writes: the test file first, then the code.

It runs the tests itself, reads the failures, and corrects its own work. Nobody pastes an error back.

It may edit files and run the tests, and nothing else. Web access is off. If you see a refusal, that is the guard
rail working.

`▶ TAB 1 while it runs. POINT at "Generated tokens per second", then at the training and rendering lines.`

About **245** tokens a second, first token in about **0.13** seconds.

The assistant is busy - and the training and rendering slices did not move.

Measured with training running next door: **246.6** against **245.7** tokens a second. Unchanged. That is isolation.

`▶ TAB 8. Wait for "349 passed, 5 skipped".`

All green - `«read the last line»`. The existing tests are what shows it broke nothing.

*Bridge:* One more thing, underneath all of it.

---

## 8 · Close `20:30` → `21:30`

`▶ TAB 9.`

Every image you saw is signed. Every signature has an entry in a transparency log.

Each device checks both before it runs anything.

`▶ TAB 10.`

Everything is delivered from git: the platform, the Fleets, the promotion you just watched.

`▶ Face the audience.`

What was on screen: OpenShift, OpenShift AI, Red Hat Edge Manager, Dev Spaces, RHEL image mode.

One workstation. One GPU. Four tenants. Thirteen managed devices.

Ask us about it.

---

## If the clock slips

- Behind at beat 4: say only the four bold numbers and PASS.
- Behind at beat 6: skip the single robot's page; keep the wall and the finished rollout.
- Behind at beat 7: type the prompt, say the isolation lines on tab 1, close - do not wait for green.
- The rollout has not started two minutes after the click: say "Edge Manager checks git every two minutes; the
  rollout starts at the next check", stay on beat 6, look again.

## Not said, ever

Nobody's name. No storage product's name - "object storage". Nothing about where the governed run was carried out.
No support statement for anything shown - say what runs. Never that the episodes on screen trained the model.
