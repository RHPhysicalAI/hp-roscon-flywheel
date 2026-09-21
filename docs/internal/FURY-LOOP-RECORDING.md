<!-- This project was developed with assistance from AI tools. -->
# Booth loop: the clips to record

Eleven short clips. Record each one about as long as it will run; a few seconds either side is plenty. Only two of
them involve waiting: the coding agent (about 3 min) and the rollout (about 4 min). Speed those up in the edit.
Nothing here waits for the counter to reach 160.

To get a clip that needs no zooming later: make the thing fill the window before recording - browser zoom
(Cmd +/-), and on the GPU dashboard collapse the sections you are not showing. Same window size for every clip.
Addresses are in `docs/internal/FURY-URLS.md`.

Clips 1 to 9 change nothing; record them in any order. Clip 10 uses up the workspace. Clip 11 promotes the model, so
do it last, and record clip 2 before it (the flywheel page's badge changes, and the lane shows a reject or two right
after a rollout).

| # | Clip | Open and set up | Record | Says |
|---|---|---|---|---|
| 1 | Four slices | GPU tenants dashboard, last 15 min. "All slices" open, every other section collapsed | 20 s | One GB300, four isolated slices: coding assistant, robot zero, training, rendering. All running now |
| 2 | Robot zero and the flywheel | Flywheel page, whole page in the window | 45 s, while the arm is mid-episode. The log already shows passes and rejects; if a new verdict lands, good, but do not wait for one | Robot zero runs the signed policy Edge Manager delivered, on its own slice. Every episode is judged by the curator's gates. Passes count toward 160, where a training run starts |
| 3 | Live episodes | "Live episodes - live lane" page, top of the page | 15 s | Robot zero's last 300 episodes as judged, success by model |
| 4 | Training | The README's training picture, live: dashboard section "Training tenant - slice 0:2" on top, a terminal under it running `FURY_SSH=gb300@hp-fury tools/hub/training-watch.sh` | 30 s, not across a round's end | ACT fine-tunes round after round: 9000 steps, about 10 steps/s, a quarter of an hour a round, on a 1g slice |
| 5 | Paired evaluation | Paired evaluation page: "Paired result" and the two policy cards in one window | 20 s | 360 paired scenes. 82% -> 92%. 57 fixed, 19 broken. Gate: PASS |
| 6 | Pipeline and registry | OpenShift AI: run `promote-act-v2-ft160`, "Graph" tab. Then Model Registry, `soarm-act`, "Versions" | 10 s each (one recording or two) | The pipeline: gate, package, sign, register, open the pull request |
| 7 | Fleet wall | Fleet wall, nothing else in the window | 30 s | Twelve robots and robot zero; every camera ray-traced on one slice, 15 fps |
| 8 | A managed robot | Edge Manager, "Devices", all 13 rows visible. Then click one robot | 10 s on the list, 10 s on the robot | 13 devices under Red Hat Edge Manager. Each robot is a RHEL image mode VM that enrolled itself and verifies its own signed images |
| 9 | GitOps | Argo CD application list | 10 s | Everything delivered from git |
| 10 | Coding agent | Workspace after `reset-demo`, terminal maximised. Second tab: GPU dashboard with "All slices" and "Coding assistant - slice 0:0" open | see below, about 4 min | A coding agent in Dev Spaces, its model on the largest slice. Failing tests to green from one prompt. 245 tokens/s; the neighbours' slices do not move |
| 11 | Promotion | Three tabs: the open pull request, Edge Manager "Devices", the flywheel page. Merge menu on "Create a merge commit" | see below, about 6 min | A promotion is a pull request. One merge; the GPU host serves the new model in about a minute and a half, all twelve robots in about three and a half |

## Clip 10, the coding agent

One recording:

1. `python -m pytest tests/eval_dashboard -q` and let it end on `7 failed, 342 passed, 5 skipped`.
2. `opencode`, then the prompt: `Read DEMO-TASK.md and do what it says.` Leave it alone until `349 passed, 5 skipped`
   (about 3 min).
3. Switch to the dashboard tab, 15 s: the run is the hump in "Generated tokens per second", and the training and
   rendering lines in "Compute busy" are flat across it.

Another take: `reset-demo`, then `/new` in the agent.

## Clip 11, the promotion

Check first: `tools/hub/fleet-status.sh` shows 13 healthy, and the pull request is open.

**Time the merge.** Edge Manager reads git every two minutes, so after the click nothing moves for anything between
a few seconds and two minutes. The last read, and so the next one two minutes later:

```
oc logs deploy/flightctl-periodic -n flightctl --since=3m | grep -i resourcesync | tail -1
```

Click "Confirm merge" 15 to 20 s before the next read and the devices start to update about half a minute after the
click. From the read to all 13 up to date is about three minutes.

One recording:

1. Pull request, top of the page (title and first sentence), 10 s. "Files changed", 10 s.
2. Back to the conversation, merge box: "Merge pull request", "Confirm merge".
3. Straight to the Edge Manager tab. Leave it until all 13 rows are Up-to-date and Healthy again, about 4 min: the
   GPU host first, then the robots in waves of 1, 2, 3, 5, 1.
4. Flywheel page tab, 10 s: the badge now reads `act-v2-ft160`.

Reference times from the measured rollout, if you caption them: both Fleets at 0:45, host serving at 1:32, twelve
robots at 3:23, all healthy at 3:39.

Another take, or to put the system back afterwards: `tools/hub/reset-promotion.sh --yes` (about 5 min), then
`tools/hub/reopen-promotion.sh --open`.

## Order in the loop, and the cards

1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 11 -> 7 -> 8 -> 10 -> 9. About five minutes once clips 10 and 11 are sped up.

Opening card: *One workstation. One GPU. Four tenants. A robot and its fleet, a training tenant and a coding
assistant, all running at once on an HP ZGX Fury.*

Closing card: *OpenShift · OpenShift AI · Red Hat Edge Manager · Dev Spaces · RHEL image mode. One workstation, 13
managed devices, everything delivered by GitOps, every image signed and verified.*
