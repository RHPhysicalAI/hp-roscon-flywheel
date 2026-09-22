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
`training-watch.sh` running, and a shell in the checkout (`KUBECONFIG` set, `cosign` installed).

---

## 0 · Set the scene

`▶ README on GitHub, title in view.`

So this is the project. Red Hat and HP, physical AI from bench to fleet.

The short version: one workstation takes a robot-learning project all the way from an engineer's desk out to a
managed fleet of robots.

There are two things I want to show you today.

First, one GPU doing four jobs at once. It's split in hardware into four slices, and each slice has its own tenant.

Second, what we call the flywheel. A robot's model getting better on its own work, and only shipping once it's
proven it's better.

`▶ UPSTREAM DEMO PAGE.`

We didn't build the robot, the sim, or the model. That's all upstream work from the ROS physical AI community.

It's an SO-ARM101 arm in Gazebo, with a pretrained ACT policy that puts three cubes on a tray. That's the task
you'll see the whole way through.

What we built is everything around it.

`▶ THE ARM'S PAGE.`

The arm itself is the SO-101 out of LeRobot. Open hardware, six motors. It's what most people in this space are
learning on.

Everything today is that arm in simulation. Thirteen of them.

`▶ HP ZGX FURY PAGE.`

And the machine. An HP ZGX Fury.

**72**-core Grace CPU. A Blackwell Ultra GPU with **252** gigs of memory. **496** gigs of system memory.

Everything you're about to see is running on this one box. The robots, the fleet manager, the training, the coding
assistant. None of it is in a cloud.

*Bridge:* So let's go look at what that GPU is doing right now.

---

## 1 · One machine, four tenants

`▶ GPU DASHBOARD. Hands off the mouse for the first two lines.`

Here's the GPU.

One GPU, split into four isolated slices, and each slice has its own tenant.

`▶ POINT at the four names in "All slices", top to bottom.`

Top to bottom: a coding assistant on the big slice.
A robot's control model.
A training job.
And a renderer drawing the cameras for a whole fleet of robots.

All four are running right now, and nothing gets switched on or off during this demo.

There's also an OpenShift hub in a VM on the same machine. That's what manages the **13** devices: this host, and
twelve robots.

*Bridge:* Let's start with the robot.

---

## 2 · Collect

`▶ FLYWHEEL PAGE. Let an episode play under the words.`

This is robot zero.

The GPU host is itself a managed device. Edge Manager delivered this signed policy to it, and a label on the device
is what put it on its own slice.

`▶ POINT at the two cameras.`

Both of these cameras are ray-traced by the rendering tenant, on another slice.

`▶ POINT at the verdict and the log.`

Every episode gets judged live, and the checks are the real ones the curator uses: did the cubes end up on the tray,
and was the motion smooth.

Here's a pass - `«read the latest PASS line»`. And a reject - `«read the latest REJECT line and its reason»`.

`▶ POINT at "live lane: judged, not kept".`

This is what we call the live lane. Judged, not kept. Nothing you're watching here is being recorded, and none of it
becomes training data.

`▶ POINT at the counter.`

Passed episodes count up to **160**. 160 is where a governed training run would kick off.

*(pause)*

In a few minutes you'll watch a model get promoted. This is the system that produced that promotion, still running.

`▶ LIVE EPISODES.`

Same robot, its last **300** episodes as the curator judged them, with success by model. Right now that's
`«read the rate»`.

*Bridge:* Judged episodes are one input. Training is the other.

---

## 3 · Train

`▶ GPU DASHBOARD, the "Training tenant" group. POINT at the loss curve.`

This is the training tenant. Real ACT fine-tunes, one round after another, on one small slice.

A round is **9,000** steps, roughly fifteen minutes, and then the next one starts.

`▶ TRAINING TERMINAL. Let a line or two arrive.`

About **10** steps a second, with three other tenants working right next to it.

Nothing out of these rounds gets promoted. That's not the point. The point is that training shares this GPU and
nobody else notices.

*Bridge:* So how does a model actually get promoted?

---

## 4 · Evaluate and gate

`▶ PAIRED EVALUATION.`

This is the paired evaluation from the governed run. Candidate against incumbent, the same **360** seeded scenes for
both.

`▶ POINT at the success rates.`

**295** successes went to **333**. **82** percent to **92**.

`▶ POINT at fixed and broken.`

**57** scenes got fixed. **19** got broken. And there's a sign test on those pairs.

`▶ POINT at PASS.`

The gate passed. That's a rule, not somebody's opinion.

These are recorded results in a live page. An evaluation takes hours, so it's never going to be a live part of this.

*Bridge:* That result went into a pipeline.

`▶ OPENSHIFT AI, the run promote-act-v2-ft160, Graph tab. Every step green.`

This is the pipeline, on OpenShift AI. One run, top to bottom.

`▶ POINT down the graph as you name the steps.`

The gate, which is the decision you just saw.
Package the model as an image.
Sign it.
Register it.
Open a pull request.

Every step green. And notice the pipeline doesn't deploy anything. It proposes.

`▶ OPENSHIFT AI, Model Registry, soarm-act, Versions tab.`

Here it is in the Model Registry, with the gate's result right there in the description - `«read the line under
the version name»`.

One entry, written once by the pipeline. Nobody touches it after that.

*Bridge:* And a pull request is something we can merge.

---

## 5 · Promote - LIVE

`▶ PULL REQUEST, title and first sentence on screen.`

This pull request is a re-run of a promotion we already did, for the demo.

Same signed image, same gate record. The pipeline didn't run again and nothing got re-measured.

What's live is the merge, and the rollout.

`▶ SCROLL to "Files changed".`

A promotion is two things in git: the model image's digest, and a version. For both Fleets, the host's and the
robots'.

`▶ BACK to the merge box. Check the button reads "Create a merge commit".`

`▶ TIMING: Edge Manager reads git every two minutes, on the even minute. Click about ten seconds before one.`

A human merges it. That's the only thing a person does in this whole loop.

`▶ CLICK "Confirm merge". START THE TIMER.`

Nobody logs into a robot. Nothing gets downloaded either - every device already has both models on it.

`▶ EDGE MANAGER, Devices. Stay until the host leaves "Up-to-date".`

Edge Manager picks this up the next time it checks git.

`▶ ⏱ host "Updating"`

There's the host.

`▶ ⏱ host "Up-to-date" again`

And it's now serving the promoted model on its slice.

`▶ ⏱ first robot "Updating"`

Now the robots, in batches. A canary, then a quarter of the fleet, then half, then the rest. Waves of **1, 2, 3, 5**
and **1**.

When we measured this, the host was serving the new model about a minute and a half after the merge, and all twelve
robots were done inside three and a half.

`▶ Do not wait. Go to beat 6 and come back to Edge Manager when it is done.`

*Bridge:* While that rolls, let's look at the fleet.

---

## 6 · Fleet

`▶ FLEET WALL.`

Thirteen robots. `r00` is robot zero, the other twelve are the fleet.

Two cameras each, **480** by **480** at **15** frames a second, all ray-traced on one small slice.

That one slice is handling about **228** camera pairs a second.

`▶ EDGE MANAGER. OPEN one robot that is Online, Up-to-date, Healthy.`

Every one of the twelve is a RHEL image mode VM.

It enrolled itself, got approved, and it pulls and verifies its own signed images.

They all come from one golden image. The OS image builds in **34** seconds, the disk in **65**, and it's under a gig.

We had twelve robots enrolled, approved and healthy about **25** minutes after the first one booted, each with its
own identity.

`▶ BACK to the device list. ⏱ all 13 "Up-to-date" and "Healthy".`
`▶ EDIT POINT: if they are not all there yet, stop talking here; the recording jumps to the finished list.`

And there's the rollout, done. Twelve of twelve, all thirteen healthy.

`▶ FLYWHEEL PAGE, if the badge is in view.`

And the flywheel page is now showing the promoted model.

*Bridge:* That leaves one slice we haven't looked at. The big one.

---

## 7 · Coding tenant

`▶ WORKSPACE, agent ready.`

This is a coding agent in a Dev Spaces workspace. Its model is running on the big slice of the same GPU.

`▶ TYPE, and nothing else:  Read DEMO-TASK.md and do what it says.   ENTER.`

One prompt. There's a failing test suite in this checkout, **7** failures. Watch what it does.

`▶ While it works, narrate what is on screen.`

It reads before it writes. The test file first, then the code.

It runs the tests itself, reads the failures, and fixes its own work. Nobody's pasting errors back to it.

It's allowed to edit files and run tests, and that's it. No web access. If you see it get refused on something,
that's the guard rail doing its job.

`▶ GPU DASHBOARD while it runs. POINT at "Generated tokens per second", then at the training and rendering lines.`

About **245** tokens a second, first token in about **0.13** seconds.

The assistant's busy. Now look at the training and rendering slices. They didn't move.

We measured this with training running next door: **246.6** tokens a second, versus **245.7** with the GPU idle.
Same number. That's the isolation.

`▶ WORKSPACE. Wait for "349 passed, 5 skipped".`
`▶ EDIT POINT: if the agent is still working, stop talking here; the recording jumps to the green line.`

All green - `«read the last line»`. The existing tests passing is what tells you it didn't break anything.

*Bridge:* One more thing, and it's underneath all of it.

---

## 8 · Close

`▶ MAC TERMINAL, in the checkout. TYPE:  tools/hub/verify-signed.sh   ENTER. Takes a few seconds.`

Every image you've seen today is signed, and every signature has an entry in a transparency log.

This is the same check every device runs before it starts anything. Same key, same log.

`▶ POINT at the three check lines, then at "log entry: index N".`

Claims validated. Entry found in the log. Signature verified against the key.

And that's its entry number in the log: `«read the index»`.

`▶ TRANSPARENCY LOG, optional: paste the entry's address from the terminal into the browser.`

That's the entry itself, with the inclusion proof. That's the log's guarantee it was there and can't be quietly
removed.

`▶ ARGO CD.`

And all of it is delivered from git. The platform, the Fleets, the promotion you just watched.

`▶ Face the audience.`

What you saw was OpenShift, OpenShift AI, Red Hat Edge Manager, Dev Spaces and RHEL image mode.

One workstation, one GPU, four tenants, thirteen managed devices.

Come find us. We're happy to talk about it.

---

## Not said, ever

Nobody's name. No storage product's name - "object storage". Nothing about where the governed run was carried out.
No support statement for anything shown - say what runs. Never that the episodes on screen trained the model.
