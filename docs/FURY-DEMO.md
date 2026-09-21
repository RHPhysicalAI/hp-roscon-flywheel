<!-- This project was developed with assistance from AI tools. -->
# The Fury demo: full runbook

For a presenter who did not build this system and runs the demo from their own laptop: about 20 minutes, eight
beats, each of which stands alone. Addresses are from `docs/internal/FURY-URLS.md`, the reasoning is in
`docs/internal/DECISIONS.md` (D157-D169). Shorter: `docs/FURY-DEMO-LITE.md`. Recorded: `docs/FURY-RECORDING.md`.

## 1. What this demo is

One HP ZGX Fury workstation (NVIDIA GB300, aarch64, RHEL 10) in a lab, reached over a private network; it is
never at the venue. Its one GPU is split with MIG into four slices, one tenant each. An OpenShift single-node
hub runs in a VM on the same machine. Red Hat Edge Manager (RHEM) on that hub manages 13 devices: the GPU host
itself and twelve RHEL image mode micro-VM "robots". Everything is delivered by GitOps (Argo CD). Images are
signed with cosign, recorded in a transparency log, and verified on the devices.

| Slice | Tenant | What the audience sees |
|---|---|---|
| `0:0` (3g) | coding assistant: a coder model served by vLLM | a Dev Spaces workspace whose agent uses it |
| `0:1` (1g) | robot zero: the RHEM-delivered, signed ACT policy, placed on this slice by a device label | the flywheel page's cameras; `r00` on the fleet wall |
| `0:2` (1g) | training tenant: real ACT fine-tunes, in rounds | a loss curve; a terminal view |
| `0:3` (1g) | fleet rendering tenant: ray-traces every robot's cameras with CUDA | the fleet wall |

Two stories come from this one running system. **The flywheel:** collect, train, evaluate, promote - how a better
model reaches the robots under governance. **The tenants:** one GPU, four isolated workloads, a managed fleet.
The system stays in this state all day. Nothing is switched on stage; pieces are shown and reset one by one.

## 2. Prerequisites

Ask the demo owner for every credential **out of band**. None is in this repository; none belongs in a file.

**Network**
- [ ] You are a member of the demo owner's tailnet, with access to the machine (`docs/NETWORK-ACCESS.md`; if
      your checkout does not have it, ask the demo owner).
- [ ] The cluster's names resolve through the tailnet: on macOS the check below answers `10.20.0.10` (on Linux,
      any resolver lookup of that name must). If your `/etc/hosts` pins `sno-flywheel.local` names - the
      development stand-in uses the same ones - comment those lines out first.
- [ ] `ssh gb300@hp-fury` gives you a shell on the host.

```
dscacheutil -q host -a name api.flightctl.apps.sno-flywheel.local
```

| Credential, from the demo owner | Needed for | Without it |
|---|---|---|
| tailnet access | everything | no live demo: the recorded video |
| the cluster account and its password | `oc login`, `flightctl login`, the RHEM page, Dev Spaces: beats 5, 6, 7 and every reset | beats 1-4 still work: their pages need no login |
| write access and merge rights on the GitHub repository | beat 5's merge; the reset pushes to branch `fury`, the re-open pushes a branch | the demo owner resets, re-opens and merges on your cue |
| ssh to the host as `gb300` | beat 3's terminal view, `robot-zero.sh status` | show beat 3 on the dashboard only |
| the host's sudo password - **optional** | beat 6's optional part; `robot-zero.sh reset`; `fury-mode status`; recovery after a host reboot | skip those; everything else works without it |

**Tools:** `oc` (the hub is OpenShift 4.22; a 4.21 client made the checks in this document); `flightctl`
**1.3.0**, the server's version (`flightctl version` prints both; the scripts look on `PATH`, then
`~/.local/bin/flightctl`, or take `FLIGHTCTL=<path>`); `gh`, `git`, `jq`, `ssh`; `bash` (the macOS system bash
3.2 is enough, the scripts are written for it); a Chromium-family browser with one profile kept for the demo.

**The repository on branch `fury`, then the logins.** Log in to GitHub first (`gh auth login`, interactive). A
separate kubeconfig keeps the hub login apart from your others; `oc login` asks for the account and the password
itself. The `flightctl` login lapses within a day: repeat the last two blocks on every show day, and the two
`export` lines in every new terminal. Good: the last command lists `act-inference` and `robots`, `VALID` `True`.

```
gh repo clone RHPhysicalAI/hp-roscon-flywheel
cd hp-roscon-flywheel
git switch fury
```

```
export KUBECONFIG=~/.kube/fury-login
export FURY_SSH=gb300@hp-fury
oc login https://api.sno-flywheel.local:6443 --insecure-skip-tls-verify
```

```
flightctl login https://api.flightctl.apps.sno-flywheel.local --token "$(oc whoami -t)" --insecure-skip-tls-verify
flightctl get fleets
```

**Browser preparation.** The routes use the cluster's self-signed certificate, and a browser has to accept it
**once per hostname**. Open every address below once and accept. The first login also passes through the
cluster's login page, one more hostname: accept it when the browser asks. These are also the tabs, in show order.

| Tab | Page | Address | Beats |
|---|---|---|---|
| 1 | GPU tenants dashboard | https://edge-perses-observability.apps.sno-flywheel.local/projects/observability/dashboards/gpu-tenants | 1, 3, 7 |
| 2 | Flywheel page | https://dashboard-flywheel.apps.sno-flywheel.local | 2, 5 |
| 3 | Live episodes - live lane | https://eval-dashboard-show-flywheel.apps.sno-flywheel.local | 2 |
| 4 | Paired evaluation | https://eval-dashboard-flywheel.apps.sno-flywheel.local | 4 |
| 5 | The promotion pull request | https://github.com/RHPhysicalAI/hp-roscon-flywheel/pulls | 5 |
| 6 | Red Hat Edge Manager | https://ui.flightctl.apps.sno-flywheel.local | 5, 6 |
| 7 | Fleet wall | https://fleet-wall-flywheel.apps.sno-flywheel.local/ | 2, 6 |
| 8 | Dev Spaces workspace | https://devspaces.apps.sno-flywheel.local | 7 |
| 9 | Transparency log | https://rekor-server-trusted-artifact-signer.apps.sno-flywheel.local | 8 |
| 10 | Argo CD | https://openshift-gitops-server-openshift-gitops.apps.sno-flywheel.local | 8 |

The fleet wall matters even if you skip beat 6: the flywheel page **embeds** the wall's camera streams. A browser
never asks about a certificate for embedded content; it shows nothing. The flywheel page's camera panel stays
empty until the fleet wall's own address has been opened and accepted in the same browser.

## 3. Thirty minutes before

**1. The checkout and the hub.** From the root of the checkout, in a shell that has run section 2's login block.

```
git switch fury
git pull --ff-only
oc get applications.argoproj.io -n openshift-gitops
flightctl get devices
tools/hub/fleet-status.sh
tools/hub/fleet-worlds-scale.sh status
tools/hub/robot-zero.sh status
gh pr list --base fury --state open
```

| Command | Good |
|---|---|
| `oc get applications...` | every row `Synced` and `Healthy` |
| `flightctl get devices` | 13 rows: `fury-host` (owner `Fleet/act-inference`) and `fleet-vm-01` to `-12` (owner `Fleet/robots`), all `Online`, `UpToDate`, `Healthy` |
| `fleet-status.sh` | twelve robots running: ends `12 devices in fleet=robots: 12 reporting, 0 not reporting ... 12 with healthy applications` and `Fleet robots: Valid=True RolloutInProgress=False` |
| `fleet-worlds-scale.sh status` | `worlds: 12 ready of 12 wanted (r01-r12)`, every world `Running  ready=true`, `motion: recorded` |
| `robot-zero.sh status` | `act-inference: Running / Healthy`; label `MIG-...`, `on the host`; `mig: Enabled`; four `robot-zero-*` units `active`; policy `active/running`; `wall   r00: live ... (13 robots live, 15.0 frames/s)` |

**2. On the host** (`ssh gb300@hp-fury`). `slices` needs no sudo: every row must end in `ok` (the dashboard's
tenant names match the GPU). `fury-mode status` asks for the sudo password - skip it without one. Good:
`mig mode:  Enabled`, layout `9,19,19,19`; `llm-assistant`, `training-tenant`, `fleet-renderer` and the
`robot-zero-*` units `active`; the policy `active/running`, its placement `MIG-... = slice 0:1`.

```
cd ~/flywheel-setup
./75-tenant-metrics-install.sh slices
fury-mode status
```

**3. The promotion pull request, open ahead of time.** Start at least ten minutes before the show. If step 1's
`gh pr list` showed a pull request whose title ends in `re-opened for a showing`, leave it open: done. If it
showed nothing, reset, then re-open:

```
tools/hub/reset-promotion.sh --yes
tools/hub/reopen-promotion.sh --open
```

The reset takes about 5 minutes (the host is back on the previous model about 50 s after the push, all 13
devices healthy in about 5 minutes; the script watches for up to 15) and ends `reset: both Fleets serve
<previous model>, robots 12/12 UpToDate`. The re-open takes seconds and ends `opened <address>`. Both refuse
while a rollout is in progress or a robot is shut off; the message says what to wait for. Do **not** merge now.
**On stage, merge with a merge commit - never squash or rebase:** the next reset finds a promotion by its merge
commit. This repository allows all three, and GitHub remembers your last choice.

**4. The coding workspace.** In Dev Spaces, log in with the cluster account, open the workspace
`flywheel-coding-task`, wait for the editor. Put the task back with the `reset-demo` command (*Terminal > Run
Task > devfile > Put the demo task back to its failing state*), then show the starting point in its terminal.
Expected last line: `7 failed, 342 passed, 5 skipped`.

```
python -m pytest tests/eval_dashboard -q
```

Open `tests/eval_dashboard/test_demo_task.py` in one pane and a terminal in the other; start the agent there
with `opencode`. The editor keeps its layout per browser and workspace: arrange it once in the demo browser. In
the agent's view, paste with the keyboard.

**5. Tabs and terminals.** Open section 2's tabs in that order: tab 5 on the open pull request, tab 6 logged in
and on *Devices*, tab 8 on the prepared workspace. One terminal with a large font for beat 3, running the
command below (it changes nothing; Ctrl-C leaves). Good: `[watch] unit: active`, then a loss line about every
ten seconds. A second terminal at the root of the checkout, for beat 5. Last, ask the demo owner to confirm that
the training trigger is disarmed for the show day (a GitOps setting the owner makes): nothing starts unattended.

```
tools/hub/training-watch.sh
```

## 4. The demo, beat by beat

Everything runs from the browser and the laptop. One on-stage step needs the host's sudo password, and it is
optional: the second half of beat 6. Quote only the numbers that appear under "Say"; add no others.

### Beat 1 - Open: one machine, four tenants (1-2 min)

- **Open:** tab 1.
- **Do:** point at the four slices, named by tenant, each with its own headline number.
- **Say:**
  - "This is one workstation. One GPU, split into four isolated slices. Each slice has its own tenant."
  - "A coding assistant on the large slice. A robot's policy. A training job. A renderer for a fleet's cameras."
  - "All four are working right now. Nothing gets switched on or off during this demo."
  - "An OpenShift hub runs in a VM on the same machine. It manages 13 devices: this host and twelve robots."
- **Don't say:** anything about Red Hat supporting an arm64 hub. Say what runs.

### Beat 2 - Flywheel: collect (3 min)

- **Open:** tab 2; then tab 3, which is also the page's *Live episodes* link.
- **Do:** point at robot zero's two cameras, the model label, the latest verdict, the counter with "live lane:
  judged, not kept". On tab 3 read the running success rate by model label **off the page**.
- **Say:**
  - "This is robot zero. The GPU host is itself a managed device. Edge Manager delivered this signed policy."
  - "A device label placed the policy on its own slice. Its cameras are ray-traced by the rendering tenant."
  - "With no tuning, the first full episode placed all three cubes in 59 seconds."
  - "Every episode is judged live by the curator's real gates: cubes on the tray, smooth motion."
  - "This is the live lane: judged, not kept. No recording is kept; nothing you watch becomes training data."
  - "The counter climbs to 160. At 160 the page says: this is where a governed training run would start. This
    is the system that produced the promotion you are about to see, still running."
- **Don't say:** that these episodes trained the promoted model, or any model. No fixed success rate. Do not
  wait for 160: at about one pass a minute it comes round every few hours. Never edit the counter.

### Beat 3 - Flywheel: train (2 min)

- **Open:** tab 1, the *Training tenant* group; then the large-font terminal.
- **Do:** point at the loss curve, one line per round, then at the loss lines arriving in the terminal.
- **Say:**
  - "The training tenant runs real ACT fine-tunes, round after round, on one small slice."
  - "About 10 steps a second, while three other tenants work next to it."
  - "A round is 9000 steps, about a quarter of an hour. Then the next one starts."
  - "Nothing from these rounds is promoted. The point: training shares this GPU and disturbs nobody."
- **Don't say:** "the flywheel's training run" - it is "the training tenant". Never imply that the promoted
  model came from these rounds.

### Beat 4 - Flywheel: evaluate and gate (2 min)

- **Open:** tab 4, pinned to the governed run `9fb233e8`.
- **Do:** point at candidate against incumbent, the fixed and broken counts, the verdict.
- **Say:**
  - "This is the governed run's paired evaluation: candidate against incumbent on 360 identical seeded scenes."
  - "295 successes became 333: 82 percent to 92. 57 scenes fixed, 19 broken. A sign test. The gate says PASS."
  - "Recorded results in a live page. An evaluation takes hours; it is never a live beat."
  - "What this hub did with the result: the gate decision, packaging and signing the model image, the pull
    request."
- **Don't say:** numbers or details of the run that are not on the page or in this runbook. "The governed run"
  and its results, as the page shows them; anything about how the run was carried out goes to the demo owner.

### Beat 5 - Flywheel: promote, LIVE (4 min)

- **Needs:** every robot running, GitHub reachable, the hub. Nothing is pulled: every device holds both models.
- **Open:** tab 5, then tab 6, and the second terminal.
- **Do - show the pull request (1 min):**
  1. The title ends in "re-opened for a showing". The first sentence says the pipeline did not run again and
     nothing was re-measured - read it out.
  2. *Files changed*, four files. The two Fleet files (`gitops/rhem/fleet-act-inference.yaml`,
     `gitops/rhem/fleet-robots.yaml`) each change two lines: the model image's digest and `MODEL_VERSION`. The
     other two set the trigger's lineage and the catalog entry.
  3. The original pull request's text, quoted: the gate's table and the signed model image's digest.
- **Say, before merging:** "This pull request is a re-showing of a recorded promotion: the same signed image,
  the same gate record. The pipeline did not run again and nothing was re-measured. What is live is the merge
  and the rollout."
- **Do - merge:** the arrow beside the merge button > **Create a merge commit** > confirm. Start a timer. From a
  terminal instead: list, then merge with the number shown.

```
gh pr list --base fury --state open
gh pr merge --merge NUMBER
```

| After the merge | Where | What |
|---|---|---|
| about 45 s | tab 6, *Fleets* and *Devices* | both Fleets carry the new model; devices leave `UpToDate`, the host first |
| about 1.5 min | tab 2; tab 6, device `fury-host` | the host serves the promoted model on its slice, `Healthy`; the flywheel page's label follows. An episode cut short by the restart is judged a reject - the page being honest |
| 1.5 to 3.5 min | tab 6, Fleet `robots`; or `tools/hub/fleet-status.sh` (column `RENDERED` moves) | the robots in batches: canary, 25%, 50%, the rest - waves of 1, 2, 3, 5 and 1 |
| about 3.5 min | the same | twelve of twelve `UpToDate`, all 13 `Healthy` |

- **Fill the wait:** after the 45 s mark say the lines below, go to tab 7 and start beat 6; come back to tab 6
  at about 3.5 minutes to show the fleet done.
- **Say:**
  - "A promotion is two things in git: a signed model image's digest and a version, for both Fleets."
  - "A human merges. Nobody logs in to a robot."
  - "Measured: the host serves the promoted model 1 minute 32 seconds after the merge; 1 minute 38 the first time."
  - "The robots follow in batches. All twelve were updated 3 minutes 23 seconds after the merge."
  - "All 13 devices were healthy at 3 minutes 39. Nothing is downloaded: every device already holds both models."
- **Don't say:** that the pipeline just ran or anything was re-measured. The Model Registry entry for this
  candidate is the one the governed run made; it is never rewritten. Beat 4's rule still holds.
- **No merge rights:** the demo owner merges on your cue. Nobody can merge: section 7, *GitHub unreachable*.

### Beat 6 - Fleet (4 min)

- **Open:** tab 7; then tab 6, Fleet `robots` and its devices.
- **Do:** on the wall point at `r00` (robot zero) and the twelve others. In RHEM open one robot - labels,
  application, events - then the device list with beat 5's rollout finishing.
- **Say:**
  - "Thirteen robots, two cameras each, 480 by 480, 15 frames a second - all ray-traced on one small slice."
  - "That slice manages about 228 camera pairs a second. Geometry matches the simulator's cameras to 0.09 pixels."
  - "Each of the twelve is a RHEL image mode micro-VM. It enrolled itself, was approved, and pulls and verifies
    its own signed images."
  - "The OS image builds in 34 seconds, its disk in 65, under a gigabyte. Twelve robots were enrolled, approved
    and healthy about 25 minutes after the first one booted."
  - "Every clone has its own identity: 12 distinct host keys, device identities and hardware ids."
- **Don't say:** that the twelve robots' policies move the arms on the wall. Their worlds replay recorded
  episodes of the policy's actions; each robot's computer runs the delivered, verified policy. Only `r00` is
  closed loop (D163).

**Optional - needs the host's sudo password, and is not rehearsed on the real fleet (section 8):** robots leave
and come back. **Only after the rollout has finished:** a shut-off robot stalls its batch. On the host, first
`8`: in RHEM `fleet-vm-09` to `-12` stop reporting and stay enrolled (the wall does not change: it shows the
robots' worlds, which run on the hub). Then `12`: `started fleet-vm-09` ..., no approval, no pull, `Online` again
in about a minute.

```
cd ~/flywheel-setup
./81-fleet-scale.sh 8
./81-fleet-scale.sh 12
```

### Beat 7 - Coding tenant (4 min)

- **Open:** tab 8, the prepared workspace; tab 1 within reach.
- **Do:** show `DEMO-TASK.md` if you like, then type into the agent, and nothing else:
  *Read DEMO-TASK.md and do what it says.* While it works, switch to tab 1: the assistant's tokens per second
  rise; the training and rendering slices do not move. It is finished when the whole suite is green,
  `349 passed, 5 skipped`. Then `git diff` in the terminal.
- **Say:**
  - "A coding agent in a Dev Spaces workspace closes a failing test: it reads first, writes, tests, corrects itself."
  - "Its model runs on the large slice of this GPU: about 245 tokens a second, first token in about 0.13 seconds."
  - "On the dashboard the assistant is busy, and the training and rendering slices did not move."
  - "Measured with training running next door: 246.6 against 245.7 tokens a second. Unchanged. That is isolation."
  - "The agent may edit files and run the tests, nothing else; web access is off. A refusal is the guard rail
    working."
- **Afterwards:** the `reset-demo` command; `/new` in the agent (or `/exit` and `opencode` again).

### Beat 8 - Close (1 min)

- **Open:** tab 9, then tab 10 with every application under GitOps.
- **Say:**
  - "Supply chain: every image is signed, every signature is in a transparency log, the policy sits on the device."
  - "GitOps for everything: the platform, the Fleets, the promotion you just saw."
  - "What was on screen: OpenShift, OpenShift AI, Red Hat Edge Manager, Dev Spaces, RHEL image mode."
- **Don't say:** a signing product's name (this log is built from source: say "cosign" and "a transparency
  log"); any product name for "object storage"; any support statement for an arm64 hub.

## 5. Short versions

| Length | Beats, in order | Notes |
|---|---|---|
| 10 min | 1 (1 min) > 2 (2 min) > 4 (1 min) > 5: show and merge (1.5 min) > 6 during the rollout (3.5 min) > 8 (1 min) | Drop 3 and 7; say their one-line points on tab 1 during beat 1. Merge by minute 6 so the fleet is done before the close |
| 10 min, developers | 1 > 7 (4 min) > 5 > 6 > 8 | Start the agent first and narrate over it |
| 5 min | 1 (45 s) > 5: the re-showing sentence, merge by 1:30 > 6 on tabs 7 and 6 until all 13 are healthy > one closing sentence | The rollout needs about 3.5 minutes: the time of the merge decides the ending |
| 5 min, nobody can merge | 1 > 2 > 4 > 6 | Tell beat 5 from the evidence (section 7, *GitHub unreachable*) |

Every "Don't say" applies to every version. The re-showing sentence is never cut.

## 6. After the demo, between showings

In this order. The system stays running; nothing is switched off at the end of the day. Steps 3 and 4 need the
checkout on `fury`, `KUBECONFIG`, a logged-in `flightctl`, `gh` and `jq`. When the show day is over, tell the
demo owner: re-arming the training trigger is the owner's step.

| # | What | Command | Takes |
|---|---|---|---|
| 1 | Let the rollout finish | `tools/hub/fleet-status.sh` until `RolloutInProgress=False`, twelve `UpToDate` | up to 3.5 min after the merge |
| 2 | Robots, if any were shut off | on the host: `./81-fleet-scale.sh 12` (sudo) | seconds to issue, about a minute until they report |
| 3 | Promotion: back to the previous model | `tools/hub/reset-promotion.sh --yes` | about 5 min (the host in about 50 s) |
| 4 | Promotion: the next pull request | `tools/hub/reopen-promotion.sh --open`; leave it open | seconds |
| 5 | Coding workspace | the `reset-demo` command, then `/new` in the agent | seconds |
| 6 | Robot zero alone, only if it misbehaved | `tools/hub/robot-zero.sh reset` (sudo password once), then `tools/hub/robot-zero.sh status` | `r00` on the wall within a minute |
| 7 | Fleet worlds, only if a tile is missing | `tools/hub/fleet-worlds-scale.sh 12`, then `... status` | a few minutes when worlds have to start |

## 7. When something goes wrong

| Symptom | Likely cause | Exact next step |
|---|---|---|
| A page shows a certificate warning | that hostname was not accepted in this browser | accept it; section 2 lists the hostnames |
| The flywheel page's camera panel, or any embedded picture, is empty | the embedded host's certificate was never accepted | open https://fleet-wall-flywheel.apps.sno-flywheel.local/ in its own tab, accept, reload the flywheel page |
| One tile `r01`-`r12` grey or missing on the wall | that robot's world went quiet (grey after 5 s, gone after 60 s) | `tools/hub/fleet-worlds-scale.sh status`, then `tools/hub/fleet-worlds-scale.sh 12` |
| `r00` grey or missing | robot zero's policy or world is down | `tools/hub/robot-zero.sh status`; policy stopped: `tools/hub/robot-zero.sh up`; world down: `tools/hub/robot-zero.sh reset` (sudo) |
| Every tile grey | the rendering tenant is down | on the host: `fury-mode status`, then `sudo systemctl start fleet-renderer.service` |
| Flywheel page: "Live lane - waiting for robot zero" | no verdict arrives: robot zero's episode reporter does not hear the loop, or is down | `tools/hub/robot-zero.sh status`, then `tools/hub/robot-zero.sh reset` (sudo). Without sudo: show tab 3, whose record stays, and move on |
| A script says `flightctl is not logged in to the hub`; `flightctl` answers 401; the RHEM page asks for a login | the login lapsed (within a day) | section 2's `oc login` and `flightctl login` lines, then the command again; in the browser, log in again with the cluster account |
| The merge button is greyed out or missing | no merge rights, or the branch conflicts with `fury` | the demo owner merges. A conflict: close that pull request; after the show `tools/hub/reopen-promotion.sh --open` makes a new one |
| The pull request was squash- or rebase-merged | the wrong option on the merge button | the rollout still runs: finish the beat. Afterwards the reset answers `already reverted - nothing to reset`, and no script repairs this: tell the demo owner, that commit on `fury` is reverted by hand |
| The rollout stalls: a batch does not move | a robot is shut off (`not reporting`); its batch waits for it, up to 30 minutes | on the host: `./81-fleet-scale.sh 12` (sudo). No sudo: say what is happening and carry on with beat 6 |
| Reset or re-open: `a rollout is in progress: ...` | a device of either Fleet is `Updating` or `OutOfDate` | wait; `tools/hub/fleet-status.sh` shows the robots. A shut-off robot stays `OutOfDate` until its VM is started |
| Reset: `already reverted - nothing to reset`. Re-open: `is in effect, not reverted` | the other script's turn | after the first message `tools/hub/reopen-promotion.sh --open`; after the second `tools/hub/reset-promotion.sh --yes` |
| Re-open: `branch ... is pushed, but the pull request was not opened` | the hub's token was refused, or GitHub was not reached | put that right and run it again; delete the leftover branch with the `git push origin --delete ...` line it prints |
| Reset: `the push to fury was refused` | the branch moved meanwhile, or your login may not push | nothing was changed: run it again, or ask the demo owner to run it |
| The coding agent stalls or loops | a bad path through the task | Ctrl+C, the `reset-demo` command, `/new`, the prompt again. A fresh conversation usually takes another path |
| The agent gets no answer from the model | the assistant is down or still loading (ready in under 3 minutes after a start) | workspace terminal: `curl -s "$ASSISTANT_BASE_URL/models"`. No answer: on the host `fury-mode status`, `sudo systemctl start llm-assistant.service`. Meanwhile skip beat 7 |
| Training tenant: no loss curve; `training-watch.sh` shows its heading, then nothing | between two rounds (about twenty seconds), or the tenant is stopped (the terminal says so in red) | wait half a minute. Still nothing: on the host `fury-mode status`; `sudo systemctl reset-failed training-tenant.service`, `sudo systemctl start training-tenant.service` |
| The GPU dashboard shows wrong tenant names, or `<profile> / instance <id>` | the MIG layout is not the one the dashboard's names were written for | on the host: `./75-tenant-metrics-install.sh slices` names the rows; `fury-mode tenants` (sudo) puts layout `9,19,19,19` back |
| GitHub unreachable | the venue's network, or GitHub | do not merge. Tell beat 5 from evidence: the merged promotion pull requests' history (offline: the command below), the device events in RHEM (or `flightctl get events --limit 20`), the pinned evaluation page. Or play the recorded video |
| Nothing resolves or answers | the tailnet path is down | reconnect the tailnet; if it stays down, the recorded video |
| The host rebooted | MIG, the tenants and the robots do not all come back by themselves | below |

Promotion history without GitHub - the newest lines are re-showings, PR #7 is the recorded promotion:

```
git log origin/fury --merges --grep='promote/' --date=iso --format='%h  %ad  %s' -n 3
```

**After a host reboot** (sudo password needed; the hub VM starts by itself). Wait until `oc get nodes` says
`Ready`, then on the host run the block below. Robot zero comes back by itself. If `fury-mode tenants` refuses
because the policy is up, its message names the command; from the laptop `tools/hub/fury-switch.sh tenants` does
the stop, the switch and the start in the right order. The assistant loads for minutes. Then section 3 again.

```
fury-mode tenants
cd ~/flywheel-setup
./81-fleet-scale.sh 12
```

## 8. Not rehearsed yet

- **Robots leaving and coming back on the real fleet** (`./81-fleet-scale.sh 8`, then `12`): "about a minute" is
  unverified (`docs/internal/FLEET-VMS.md` section 10). Rehearse it yourself before showing it.
- **A rollout meeting a shut-off robot:** the 30-minute batch wait is documented, not yet observed on this
  version (`docs/internal/FLEET-VMS.md` section 11).
- **The coding task's duration on the hub:** a comparable task took under three minutes; this one is not timed.
- **This runbook run by someone who did not build the system, on a second laptop** - above all a second person's
  push rights for the reset, and a fresh browser's workspace layout. Do one full dry run, resets included.
- **A squash-merged promotion:** no script repairs it (section 7).
- **Recovery after a host reboot as one procedure,** a boot in tenants mode included.
- **The 160 moment before an audience:** it comes round every few hours and cannot be waited for.
- **Falling back to evidence or to the recorded video** in the middle of a showing.

## 9. Glossary

| Term | Meaning |
|---|---|
| MIG slice | a hardware partition of the GPU with its own compute and memory: one 3g and three 1g slices, `0:0` to `0:3` |
| Tenant | the one workload that owns a slice |
| Hub | the single-node OpenShift cluster, in a VM on the same machine; it runs RHEM, Argo CD and the pages |
| Device | an operating system with the RHEM agent on it: the GPU host, and each of the twelve micro-VM robots |
| Fleet | RHEM's template for a group of devices: `act-inference` (the host) and `robots` (the twelve). A change to it in git starts a rollout; the canary (`fleet-vm-01`) goes first |
| Robot zero | the GPU host as a robot: its policy on slice `0:1`, cameras from the rendering tenant, `r00` on the wall |
| Live lane | robot zero's episodes on the flywheel page: judged by the curator's real gates, not kept |
| Governed run | the pipeline run `9fb233e8` that produced the promoted model: dataset, fine-tune, paired evaluation, gate, signed image, pull request |
| Model image (modelcar) | a container image that holds only the model's weights; signed, and pinned in the Fleet by its digest |
| Image mode | RHEL delivered as a bootable container image (bootc); the robots' disks are clones of one such image |
