<!-- This project was developed with assistance from AI tools. -->
# Recording the Fury demo

The plan for the demo's video: what is recorded, in which order, what every clip must show and say, and what is
checked before anyone sees it. The demo itself - beats, checks, resets, what to do when something fails - is in
`docs/FURY-DEMO.md` (short form: `docs/FURY-DEMO-LITE.md`); every page's URL is in `docs/internal/FURY-URLS.md`; the
reasoning is in `docs/internal/DECISIONS.md`, D166 to D169. This document says how to point a recorder at them.

## 1. What we are making

Two outputs, recorded in the same sessions.

| | A. The full demo | B. The booth loop |
|---|---|---|
| What | one continuous run-through of the eight beats, as given live | short segments the demo owner stitches together with title cards and transitions |
| Who tells the story | one presenter, narrating | the screen and its captions: no presenter, and it must work with the sound off |
| Length | about 18-22 min | a loop of about 5-6 min, from segments of 20-60 s |
| For | people who want to watch the real thing | the booth when there is no network at all |

Rules for both:

- **Everything shown is the real system running.** Real pages, real devices, a real rollout. Nothing is mocked up
  and nothing is re-enacted with made-up data.
- **Timings are real.** An elapsed time in a caption is the time that take took, read off the recording's timeline.
- **Anything sped up says so:** a visible label with the factor ("x8") for as long as the sped-up picture runs. A
  timer laid over a sped-up clip still shows real elapsed time.
- **The one re-showing says what it is.** The pull request merged on camera re-proposes a recorded promotion. Its
  title and its first sentence say so; they stay legible, or a caption repeats them.
- **The list of what is not claimed** (section 7) binds every caption, card and spoken sentence.

**The counter's 160 moment.** A rehearsal-only shortcut sets the count by hand. Do not use it for a recording,
unless the caption on that clip says the count was set by hand (the page gives a hand-set count away by its start
time anyway). Better, in this order:

1. Record the moment when it really happens. At the lane's usual pace the count reaches 160 roughly every three
   hours - read the counter and the start time beside it to see how far off it is. The full bar and its sentence
   stay up for five minutes. Start a long capture when the counter passes about 150.
2. Or leave the moment out: show the counter mid-climb and let the caption say what happens at 160 (segment B04).

## 2. Before recording

### Capture setup

| Setting | Use |
|---|---|
| Resolution, frame rate | 1920x1080 at 30 fps, the same window size for every take. For the loop, make the thing being shown fill the window before recording (browser zoom, collapsed dashboard sections), so the edit does not have to zoom |
| Browser zoom | one zoom for every page (propose 125%); check every page at that zoom before the first take. If one page needs another zoom, write it in the take log and use it on every take of that page |
| Theme | light, everywhere. The flywheel page and the evaluation pages take `?theme=light` and remember it per hostname; pages with their own theme switch (GPU dashboard, Edge Manager, consoles, the workspace editor) are set once in the recording profile |
| Browser profile | a new, clean profile: no bookmarks bar, no extensions, no autofill or password-manager popups, no other tabs, no personal history behind the address bar |
| Certificates | a clean profile has accepted nothing. Accept the self-signed certificate **once per hostname, before recording** - the fleet wall's host too, or the flywheel page's cameras stay empty |
| Desktop | notifications off (do-not-disturb); capture the window's area only, so the menu bar, its clock and the dock are outside the frame. If a clock must show, it shows in every take |
| Cursor | a click-highlight tool, the same for every take; park the cursor off the content when nothing is clicked |
| Terminal | light theme, a font that reads from three metres (propose 20-22 pt at 1080p), a short prompt with no user or host name, a window about 100 columns wide, scrollback cleared before every take |
| Caption band | leave the bottom fifth of the frame free of anything that matters: captions go there |

### What may and may not be in frame

| Never in frame | Fine in frame |
|---|---|
| a password being typed (cluster login, Edge Manager login, host sudo) | the lab hostnames (`*.sno-flywheel.local`, `hp-fury`) |
| a token, the kubeconfig, or the output of any login command | the repository and its pull requests |
| private bookmarks, history suggestions, other tabs | the pages listed in `docs/internal/FURY-URLS.md` |
| chat, mail or calendar notifications | the terminal view (`tools/hub/training-watch.sh`) and the status scripts' tables |

Log in to everything **before** the recorder starts: the cluster, `flightctl` (its login lapses within a day), Edge
Manager's page, the workspace, the repository's site. Do it in a terminal window that is then closed. Run the
resets of section 3 in a window that is never recorded.

### Audio, for recording A

A quiet room with soft surfaces; a microphone with a pop filter, a hand's width from the mouth; record the voice as
a separate track from the screen, so a cough can be cut without cutting the picture. Record ten seconds of room
tone at the start. No system sounds.

### System state

Run the pre-show checklist of `docs/FURY-DEMO.md` in full. For a recording, in addition:

| Check | Why |
|---|---|
| all 13 devices Online, UpToDate and Healthy | the promotion is refused otherwise, and a red device in frame needs explaining |
| twelve robots plus `r00` on the fleet wall, all moving | a shut-off robot stalls its batch and leaves a hole in the wall |
| the live lane has run for an hour or more, and the count is not hand-set | the flywheel page and the live episodes page need real numbers |
| the dashboard's slice names match the GPU | a wrong tenant name on a slice is wrong in every frame |
| the promotion is reset and its pull request is re-opened | the promotion takes come last but must be ready |
| the coding workspace is reset, the agent's conversation is new | the first thing in frame is the failing tests |
| the training tenant is in the middle of a round | the loss curve and the terminal view have something to show |

From the laptop, in the repository checkout (logged in, `KUBECONFIG` set):

```
export FURY_SSH=gb300@hp-fury
tools/hub/robot-zero.sh status
tools/hub/fleet-status.sh
tools/hub/fleet-worlds-scale.sh status
```

On the host, no root needed (`fury-mode status` asks for the sudo password the demo owner holds: off camera only):

```
./75-tenant-metrics-install.sh slices
```

## 3. Recording order

Arranged so that nothing recorded early is disturbed by what comes later, and so that waiting is spent recording.

| Block | What | Disturbs | Between takes |
|---|---|---|---|
| 1 | The loop's clips 1 to 9 (`docs/internal/FURY-LOOP-RECORDING.md`) | nothing | reload the page |
| 2 | The loop's coding clip, 10 (about 4 min a take) | the workspace | the `reset-demo` command, then a new agent conversation (`/new`) |
| 3 | The loop's promotion clip, 11: one take from the pull request to all 13 healthy | both Fleets, the pull request | reset (about 5 min), re-open (seconds), check |
| 4 | Recording A, the full run-through. It contains one coding run and one promotion | both of the above | both of the above |

The promotion comes **last**, and there is **one take per reset cycle**:

```
tools/hub/reset-promotion.sh --yes
tools/hub/reopen-promotion.sh --open
```

- The reset takes about 5 minutes until all 13 devices are healthy again; the re-open takes seconds, and the pull
  request can stay open until the take. Merge with a **merge commit**, not squash or rebase: the next reset looks
  for that merge.
- Both scripts need the checkout on branch `fury`, `KUBECONFIG`, a logged-in `flightctl`, `gh` and `jq`. Both refuse
  while a rollout is in progress or a robot is shut off. **All twelve robots must be running** before a take: a
  shut-off robot stalls its batch for half an hour.
- The merge needs the repository's site to be reachable. The rollout itself pulls nothing.
- A cycle is about 15 minutes: the lead-in, 3.5 minutes of rollout, 5 minutes of reset, the re-open, a look at
  `tools/hub/fleet-status.sh`. **Plan three promotion takes a session: one for B, one inside A, one spare.** Four
  fit only if nothing goes wrong.
- A take of A that fails before the merge costs nothing: start again. After the merge it costs a reset cycle and a
  workspace reset.
- The reset is itself a real rollout, to the previous model. Do not record it and pass it off as the promotion.
- The minutes after a promotion or a reset are when the live lane shows rejects (the policy's restart interrupts
  an episode, and the lane records that truthfully). Record the loop's flywheel page clip before any promotion take, or a quarter of an hour after one.

## 4. Shot list for B (the loop)

In `docs/internal/FURY-LOOP-RECORDING.md`: eleven short clips, what to set up for each and what each one says.

## 5. The loop's storyboard

Also in `docs/internal/FURY-LOOP-RECORDING.md`: the clips' order in the loop and the text of the opening and closing
cards. Cards, captions and transitions are the owner's to make.

## 6. Script for A

Not word for word. For each beat: what is on screen, what must be said, the target time. Say the numbers as they
are written here; read a live number off the page instead of quoting one from memory. About 21 minutes in all.

**1. Open - one machine, four tenants** (1-2 min). Screen: GPU tenants dashboard.
- This is one HP ZGX Fury workstation. Its one NVIDIA GB300 GPU is split with MIG into four slices, one tenant each.
- A coding assistant, a robot's policy, a training tenant and a renderer for a robot fleet: all four are running
  right now, each with its own headline number.
- An OpenShift single-node hub runs in a VM on the same machine. Red Hat Edge Manager on it manages 13 devices:
  the GPU host and twelve RHEL image-mode robots.
- Nothing is switched on for this demo. It runs like this all day.

**2. Flywheel: collect** (3 min). Screen: flywheel page; then the "Live episodes - live lane" page.
- This is robot zero, its two cameras live. Its policy is the signed image Edge Manager delivered, placed on its
  own slice by a device label.
- Every episode is judged live by the curator's real gates: did the cubes land on the tray, was the motion smooth.
- **"What you see is judged and not kept - this is the system that produced that promotion, still running."**
  (Lead in with: "in a few minutes you will see a model promoted".)
- The counter climbs to 160, and at 160 the page says: this is where a governed training run would start. On the
  live lane nothing is kept and nothing starts; the count begins again.
- The live episodes page shows success by model label. Read the rate off the page.

**3. Flywheel: train** (2 min). Screen: dashboard panel "Training tenant - loss (round N)"; then the terminal:

```
FURY_SSH=gb300@hp-fury tools/hub/training-watch.sh
```

- This is the training tenant: real ACT fine-tunes, in rounds of 9000 steps, about a quarter of an hour each.
- About 10 steps a second on a 1g slice, while three other tenants work.
- Nothing from these rounds is promoted. The promotion you are about to see comes from the governed run.

**4. Flywheel: evaluate and gate** (2 min). Screen: paired evaluation page.
- This is how a model earns promotion: the pipeline's paired evaluation, from the governed run `9fb233e8`.
- Candidate and incumbent on 360 identical seeded scenes: 295 to 333 successes, 82% to 92%.
- 57 scenes fixed, 19 broken; a sign test on those pairs; the gate says PASS.
- These are recorded results in a live page. An evaluation takes hours and is never a live beat.
- Quote the governed run's results as the evaluation page shows them, and no numbers beyond the ones this document lists.

**5. Flywheel: promote - live** (4 min). Screen: the open pull request; its changed files; merge; Edge Manager.
- **"This pull request re-proposes a promotion the pipeline made earlier: same signed image, same gate record -
  nothing was re-measured."**
- A promotion is this: a signed model image's digest and a version, in git, for both Fleets. (What ran on this
  hub, and may be said: the gate decision, the packaging and signing of the model image, the pipeline's pull
  request, the merge, the rollout.)
- I merge it, with a merge commit. That is the only thing a person does.
- About 45 seconds later both Fleets carry it. The host serves the new model on its slice about a minute and a
  half after the merge (measured: 1 min 32 s). The twelve robots follow in batches: canary, 25%, 50%, the rest.
- Nothing is pulled: every device already holds both models. All twelve robots are done about 3 min 23 s after the
  merge, all 13 devices healthy at 3 min 39 s. Use the wait: go to beat 6 and come back to the finished rollout.

**6. Fleet** (4 min). Screen: fleet wall; then Edge Manager, Fleet `robots`, one robot's page.
- Twelve robots and robot zero. Every camera is ray-traced with CUDA on one 1g slice: 13 robots at 15 frames a
  second, two 480x480 cameras each, about 228 camera pairs a second.
- Each robot is a RHEL image-mode micro-VM. It enrolled itself, was approved, and pulls and verifies its own
  signed images.
- Here is the rollout from a minute ago, finishing batch by batch.
- One golden image: the OS image builds in 34 s, the disk in 65 s, under 1 GB. Twelve robots were enrolled,
  approved and healthy about 25 minutes after the first one booted, each with its own identity.
- Leave the optional robots-leave-and-return step out of A: it needs a sudo password typed on the host.

**7. Coding tenant** (4 min). Screen: the workspace and the GPU dashboard, side by side.
- A Dev Spaces workspace. Its agent uses a Qwen coder model served by vLLM on the largest slice.
- The prompt, verbatim: "Read DEMO-TASK.md and do what it says."
- It reads the tests, writes the feature, runs the tests itself and corrects its own work until the suite passes.
- On the dashboard the assistant's tokens per second rise, and the training and rendering slices do not move:
  isolation. Measured: about 245 tokens a second, first token in about 0.13 s; 246.6 while the training tenant
  runs next door against 245.7 with the GPU idle - unchanged.

**8. Close** (1 min). Screen: the transparency-log entry; Argo CD; the closing card.
- Every image you saw is signed. Every signature has an entry in a transparency log. Each device's policy checks
  both before it runs anything.
- Everything is delivered by GitOps.
- OpenShift, OpenShift AI, Red Hat Edge Manager, Dev Spaces, RHEL image mode - on one workstation.
- Say what runs. Make no statement about what is supported on an arm64 hub.

## 7. After recording

Review every clip, frame by frame where a terminal or an address bar is in it, before anyone else sees it.

| Check | Look for |
|---|---|
| No secret in any frame | a typed password, a token, the kubeconfig, a login command's output, terminal scrollback, address-bar suggestions |
| No personal information | notifications, bookmarks, other tabs, a prompt with a user name, a clock that jumps between segments |
| No individual's name | in captions, cards, narration and file names: roles only. Account names that pages show by themselves: section 8 |
| Only listed numbers are quoted | every number in a caption, a card or the narration is one this document lists; the governed run is quoted as the evaluation page shows it: "the governed run", "the pipeline's paired evaluation", with its results |
| Live episodes are not training data | "judged, not kept"; nothing says or implies that what is watched trained the promoted model |
| The training tenant is called that | never "the flywheel's training run"; nothing from its rounds is promoted |
| The promotion is a re-showing | "re-opened for a showing" legible or captioned; same signed image, same gate record, nothing re-measured |
| No support statement for an arm64 hub | say what runs |
| "Object storage" | the storage product is never named in a caption, a card or the narration |
| Sped-up sections are labelled | the factor, for the whole sped-up stretch |
| Timings are real | every elapsed time in a caption matches that take's timeline; a number not measured in the take is one this document gives, and the caption says "measured" |
| The count was not hand-set | the start time beside the counter |

**File names.** `<date>_<id>_<slug>_take<N>_raw.<ext>` for takes (`20260925_B08_promotion-pr_take2_raw.mov`),
`<date>_A_full_take<N>_raw`, voice as `..._voice.wav`; edits as `fury-loop_v<N>.mp4` and `fury-demo-full_v<N>.mp4`.
Keep a take log beside them: take, time of day, what went wrong, and for a promotion the real times read off it.

**Keep the raw takes**, unedited, outside the repository. They are the proof that the timings are real, and a raw
take may hold what the edit cut out: treat the folder as not for sharing.

**Then put the system back** as `docs/FURY-DEMO.md` says for the time between showings: the promotion reset and
its pull request re-opened if another showing follows, the workspace reset, all 13 devices healthy.

## 8. Not decided - the owner's calls

- Music, or silent. (B must work silent either way.)
- A voice-over for B as well, or captions only.
- The title-card template and branding, and light or dark.
- Where the loop plays from with no network: propose a local file on the booth machine, played by a local player
  set to repeat, **tested with the network switched off** - no streaming site, no shared drive, no web fonts.
- Account names that pages show by themselves - the merging account on the pull request's page, the account
  namespace in an image reference: crop, blur or accept.
- Whether A is one unbroken take or may be cut between beats (a cut gets a visible transition either way).

**Not rehearsed yet**

- How a transparency-log entry is put on screen (B15, beat 8): this hub's log has no search page (its address is
  in `docs/internal/FURY-URLS.md`). Until that is rehearsed, show the pull request's own reference to the entry.
- The workspace and the GPU dashboard side by side at 1080p and the chosen zoom: whether both stay legible.
- The pace of the counter on the day - whether a real 160 moment falls inside a session.
