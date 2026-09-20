<!-- This project was developed with assistance from AI tools. -->
# Tenant numbers: what each slice is doing, on the dashboard and in a terminal — operator page

`70-dcgm.md` shows what the GPU says about each slice: busy, memory. This adds what each **tenant** says about
itself, so that "the training slice is busy" becomes "it is at step 4,800 of round 26, loss 0.043, 9.9 steps a
second":

| Tenant | Headline number | From |
|---|---|---|
| coding assistant, slice `0:0` | generated tokens per second, requests running | vLLM's own `/metrics` on `10.20.0.1:8000` — configuration only, nothing new on the host |
| robot zero, slice `0:1` | its slice's compute busy | the DCGM exporter (no new source) |
| **training tenant**, slice `0:2` | loss, step of the round, steps per second, seconds a step computes and waits for data, learning rate, round, rounds finished, the last finished round's final loss | the tenant's own files, read-only |
| rendering tenant, slice `0:3` | frames per second against the target, robots live and stale, seconds per batch | its `GET /status` |

The training and rendering numbers come from one small exporter, `flywheel/tenant-metrics.container` (code:
`src/tenant-metrics/`), on `10.20.0.1:9401`. The training tenant's rounds are real fine-tunes and **nothing from
them is evaluated, signed or promoted**: on the dashboard and in the terminal it is *the training tenant*, never the
flywheel's governed training.

The command blocks are meant to be pasted as they are: no comments inside them.

## One-time bring-up: the host, then the hub

**1. Operator, on the host (sudo).** In either GPU mode. It checks the unit against its rules, lets quadlet check
it, installs unit and code, starts it, and only once it answers opens `9401/tcp` in firewalld's `libvirt-to-host`
policy; then it prints what it serves. It pulls nothing: the image is the training tenant's, already in root's
storage. The training tenant is **not** reinstalled or restarted — it already writes everything the exporter reads.

```
cd ~/flywheel-setup
./75-tenant-metrics-install.sh install
```

**2. Orchestrator, laptop/hub.** Commit and push `gitops/observability/fury-tenants-scrape.yaml`,
`fury-assistant-scrape.yaml` and `gpu-tenants-dashboard.yaml`. The Argo application is a plain directory with
`prune` and `selfHeal`; it picks new files up by itself. There is no Service and no EndpointSlice: both scrapes
name the host's address, as the GPU scrape does, so nothing under `tools/hub/manual/` changes.

Nothing here is a demo-time step. The unit holds no GPU, so `fury-mode` does not own it: it runs in both modes,
through every switch, and comes back at boot.

## Before a demo: do the dashboard's names still fit the slices?

The dashboard names slices by the driver's GPU instance id through a **hand-written map** (*The slice-to-tenant
mapping*, below). After any change of the MIG layout those ids shift, and the dashboard would put a wrong tenant's
name on a slice without a word. On the host, no root — it reads `nvidia-smi` and nothing else:

```
cd ~/flywheel-setup
./75-tenant-metrics-install.sh slices
```

Every row must end in `ok` (exit status 0): GPU instance id, MIG device, profile, the tenant whose unit names that
device (`0:N`), and the name the dashboard gives that id. `WRONG NAME`, `WRONG SIZE` or `NOT ON THIS GPU` come with
the two ways out: put the layout back (`fury-mode tenants`), or edit the ids. `status` prints the same table.

## At demo time: the terminal

From a laptop, or on the host itself with `--local`. It follows the training tenant's journal — which the host's
login account reads without sudo — and shows each loss line as step of the round, loss, steps per second and data
wait, with every round's start and end called out. `--raw` shows the lines as lerobot printed them. Ctrl-C leaves. The formatted view takes terminal control
sequences out of what it prints (a training process's output is not to be trusted with the projected screen);
`--raw` does not.

```
FURY_SSH=<user>@<host> tools/hub/training-watch.sh
```

## Verify each hop

**1. The exporter**, on the host. Loopback does not answer by design — one listen address:

```
./75-tenant-metrics-install.sh status
curl -s -m 5 http://10.20.0.1:9401/metrics | grep -E '^tenant_(training|renderer)_(up|loss|step|render_fps) '
```

In tenants mode: `tenant_training_up 1`, a `tenant_training_loss{round=...}` that matches the last loss line of
`training-watch.sh`, `tenant_renderer_up 1`. In flywheel mode both `up` series are 0 and the rest is absent.

**2. The firewall**, from the hub node (expect `200`), and the assistant's metrics over the opening it already has:

```
oc debug $(oc get nodes -o name | head -1) -- chroot /host curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://10.20.0.1:9401/metrics
oc debug $(oc get nodes -o name | head -1) -- chroot /host curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://10.20.0.1:8000/metrics
```

**3. Prometheus has both targets up.** One terminal holds the port-forward, a second one asks:

```
oc -n observability get scrapeconfigs.monitoring.rhobs
oc -n observability port-forward svc/edge-metrics-prometheus 9090:9090
```

```
curl -s -G http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=up{job=~"fury-tenants|fury-assistant"}' | jq -r '.data.result[] | "\(.metric.job) up=\(.value[1])"'
curl -s -G http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=tenant_training_loss' | jq -c '.data.result[] | [.metric.round, .metric.start, .metric.lr, .value[1]]'
curl -s -G http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=count({job="fury-assistant"})' | jq -r '.data.result[0].value[1]'
```

Both `up=1`; one loss series with the round that is running; and a small count for the assistant's job (a handful
of series — the scrape keeps four of vLLM's metric names and drops the rest).

**4. Perses took the dashboard.** *Synced* in Argo CD means the custom resource exists, not that Perses accepted
it. Then open the route, project `observability`, dashboard *GPU tenants*:

```
oc -n observability describe persesdashboard gpu-tenants
oc -n openshift-cluster-observability-operator logs deployment/perses-operator --tail=200 | grep -i gpu-tenants
oc -n observability get route edge-perses -o jsonpath='https://{.spec.host}{"\n"}'
```

What to look for: the *All slices* legend names tenants (`training (1g)`), not instances; the group *Training
tenant* has six numbers over a loss curve titled `Training tenant - loss (round N)` with the running round as N;
the variable *Training round* at the top shows the same N.

## What is on the dashboard, and how it behaves

- **All slices** — as before, the series now named by tenant.
- **Training tenant** — loss, steps per second, round, progress in the round, rounds finished, the last finished
  round's final loss; the loss curve, one line per round (a sawtooth: every round starts again from a checkpoint);
  steps per second; a step's time split into computing and waiting for data; the slice's compute and memory.
- **Coding assistant**, **Robot zero**, **Rendering tenant** — a headline number, the tenant's own curves, the
  slice's compute beside them.
- **Slice detail** — the repeated per-instance rows from before, closed by default: the tenant groups show the
  same slices by name. With MIG off it is where the whole GPU's detail is.
- **Stop a tenant** and its rate reads 0 at once (steps per second, frames per second, tokens per second) and its
  curves end in a gap. The training *loss* number holds its last value until it leaves the 15-minute range: a stat
  shows the last point of a range, and there is no honest zero for a loss.
- **Between two rounds** (saving, pruning, loading the dataset: about twenty seconds, seen in the journal) loss and
  steps per second are absent until the new round's first loss line; the new round's log is already being written,
  so `tenant_training_up` stays 1 and *Round* moves on at once.

## The slice-to-tenant mapping

DCGM labels a slice with the driver's GPU instance id, not with `0:2`. The dashboard maps `1` = assistant, `11` =
robot zero, `12` = training, `13` = rendering — in its header comment, in the shared panels' queries and in the
tenant groups' GPU panels. The ids follow from profile and placement, and `mig-config.sh` makes the layout with one
command in one order (`-cgi 9,19,19,19`), so they repeat — like the MIG UUIDs D164 relies on. Read, no root:

```
nvidia-smi | sed -n '/MIG devices/,/Processes/p'
```

Columns `GI ID` and `MIG Dev` must pair up as 1-0, 11-1, 12-2, 13-3 — which is what
`./75-tenant-metrics-install.sh slices` checks row by row, profile included (*Before a demo*, above). With another
`MIG_LAYOUT` they will not: an id the dashboard does not know falls back to `<profile> / instance <id>` and the
tenant groups' GPU panels go empty (their own numbers do not — those do not depend on ids); an id it does know,
now on another tenant's slice, is shown under the **wrong tenant's name**. The four ids in
`gpu-tenants-dashboard.yaml` and the same map in the installer's `slices()` are what to edit; a test holds the two
copies together.

## When it fails: symptom → cause → what to do

| Symptom | Likely cause | Do |
|---|---|---|
| `install` refuses the unit file | the file asks for more than the exporter gets (a device, a mount, a capability, another address) | the refusal names the line and the file; `./75-tenant-metrics-install.sh check` runs the same rules without root |
| `install` or `status`: `a HuggingFace token lies at ...` | the training tenant's cache (`cache/huggingface`, its `HF_HOME`) is inside the directory the exporter mounts, and the exporter has the host's network | the `sudo shred -u` line it prints; revoke the token if it was real. The training tenant has no network and never needs one |
| `slices`: `WRONG NAME`, `WRONG SIZE` or `NOT ON THIS GPU` | the MIG layout is not the one the dashboard's id map was written for | `fury-mode tenants` to put `9,19,19,19` back, or edit the ids (*The slice-to-tenant mapping*) |
| a scrape gets `503` | eight requests were already in flight: somebody holds connections open | `sudo ss -Htn 'sport = :9401'` names the peers; each is cut after 8 s by itself |
| `install`: `... is not in root's storage` | the training tenant's image was removed | the `sudo podman pull` line it prints (once, with the uplink) |
| `install`: `no /data/flywheel/tenant-train` | the training tenant was never installed | `./72-training-tenant-install.sh install`, then this again |
| `install`: `nothing useful from http://10.20.0.1:9401/metrics` | the unit did not come up | `sudo journalctl -u tenant-metrics -n 30`. `10.20.0.1 is not up yet`: `sudo virsh net-start fury-net`. `cannot listen`: something else holds 9401 — `sudo ss -Hltnp 'sport = :9401'` |
| journal: `python3: executable file not found`, or a syntax error in `exporter.py` | this image has no `python3` on its path, or an older one than the code needs (3.8) | `sudo podman run --rm --pull=never --entrypoint sh <image> -c 'command -v python3; python3 -V'`; put the path it prints into `Entrypoint=` of the unit, `install` |
| `tenant_training_up 0` while the tenant trains | the exporter cannot read the tenant's files: the directory's SELinux label is not `container_file_t:s0`, or the log is older than `TRAINING_STALE_S` | `ls -Zd /data/flywheel/tenant-train`; the training tenant's start relabels it (`sudo systemctl restart training-tenant.service` drops the round in progress). `sudo ausearch -m avc -ts recent \| grep tenant-metrics` |
| loss and steps per second present, `tenant_training_step` missing or only in thousands | lerobot's progress bar is not in the log (its format changed), so the step is the abbreviated one from the loss line | compare `tail -c 300 /data/flywheel/tenant-train/round-*/train.log` with `_PROGRESS` in `src/tenant-metrics/metrics_core.py` |
| `tenant_renderer_up 0` while the renderer runs | `/status` did not answer within 1.5 s, was larger than 256 kB, or was not a JSON object | `curl -s -m 4 -o /dev/null -w '%{http_code} %{time_total}s %{size_download}B\n' http://10.20.0.1:9702/status` |
| hop 2 gives `000` for 9401 | the port is not open at runtime, or `virbr-fury` left its zone | `./75-tenant-metrics-install.sh status` (runtime and permanent must both say open); `sudo firewall-cmd --get-zone-of-interface=virbr-fury` must say `libvirt-routed` |
| hop 2 gives `000` for 8000 | the assistant is down (flywheel mode, or still loading), or its port was never opened | `./64-assistant-expose.sh status` |
| hop 3: no `fury-tenants` or `fury-assistant` target | the `ScrapeConfig` is not selected: label `app.kubernetes.io/part-of: edge-metrics` missing, or Argo has not synced | `oc -n observability get scrapeconfigs.monitoring.rhobs`; the application's sync status |
| hop 3: `count({job="fury-assistant"})` is empty, `up=1` | this vLLM names its metrics differently, and the scrape's `keep` rule dropped them all | `curl -s http://10.20.0.1:8000/metrics \| grep -c '^vllm:generation_tokens_total'` on the host; adjust the regex in `fury-assistant-scrape.yaml` and the three assistant queries |
| `persesdashboard gpu-tenants` exists, the dashboard did not change | Perses refused the new version: its plugin schemas are closed | hop 4's `describe` and the operator's log name the field. The new kinds are `StatChart` and `PrometheusPromQLVariable`; the new fields are `collapse`, `stack`, `querySettings` |
| the loss panel's title says `round $round` | the variable has no value: the exporter is down, or no round directory exists yet | hop 1; it fills by itself at the next refresh |
| the loss number and *Round* lag a new round by a few seconds | a round's first loss line comes 100 steps in | nothing |
| `training-watch.sh`: `this account cannot read the journal` | the login account is in neither `wheel` nor `systemd-journal` | the `usermod` line it prints |
| `training-watch.sh` shows the heading and then nothing for minutes | the tenant is stopped (it says so in red at the top), or between rounds | `fury-mode status` |

## What the choices rest on

- **Files, not the journal.** The training tenant already writes `round-<n>-<config>/train.log` (everything
  lerobot printed) and `rounds.jsonl` into `/data/flywheel/tenant-train`, world-readable and labelled
  `container_file_t:s0` by its own `:z` mount — seen on this host. The exporter mounts that directory read-only
  without relabelling and needs no journal, no socket and no change to the tenant.
- **The exact step is the progress bar's.** lerobot abbreviates `step:` above 999 (`step:1K` for 1100 — seen in a
  round's log), so `tenant_training_step` is the last `n/total` of the `Training:` bar in the log's tail, which
  also gives the round's length. The abbreviated field is only the fallback.
- **Steps per second is `1 / (updt_s + data_s)`**, lerobot's own means over its last 100 steps: 9.8 on this slice,
  which is what D165 measured. The ledger's `steps_per_s` is lower (9.7) because it counts a round's start and
  save.
- **Running means the log moves.** `tenant_training_up` is 1 while the newest round's `train.log` was written in
  the last `TRAINING_STALE_S` (120) seconds — the bar writes several times a second. When it is 0 the round's
  numbers are left out rather than repeated, so the curves show a gap.
- **Bounded.** The last 64 kB of the log, the last 4 MB of the ledger (about 300 bytes a round), 256 kB for
  `/status` under one wall-clock deadline of 1.5 s from connect to last byte (a plain HTTP/1.0 socket: a library's
  timeout is per read, and a peer that drips bytes never reaches it), one collection per 2 s however often it is
  asked, 128 MB and half a core for the unit. Nothing it reads can end it (`tests/tenant_metrics/`).
- **Whoever reaches the port cannot hold it** — the hub and every fleet VM can. A thread per request, eight at
  most, the ninth refused with `503` at once by an accepting thread that never waits for a peer; every request is
  cut 8 s after it began, however slowly it drips (the hub's scrape gives up after 5). A stalled client costs one
  thread for 8 s, not the exporter.
- **Posture.** No GPU, no credentials of its own (no `EnvironmentFile=`, no `Secret=`), uid 65534, read-only root,
  all capabilities dropped, no new privileges, SELinux-confined, two read-only mounts — its code and the tenant's
  output — one command (`python3 -u /opt/tenant-metrics/exporter.py`) and one outbound address (the renderer's
  `/status`), both held string-exact. `install` measures all of it on the file it is about to install.
- **What the mount exposes, exactly.** The training tenant's whole output directory, because round directories
  come and go: logs, each kept round's checkpoint, the ledger, and the tenant's caches — `cache/huggingface` is its
  `HF_HOME`, where a HuggingFace token would be kept. The tenant has no network, so none is expected; but this
  container has the host's network, so `install` refuses while `cache/huggingface/token` or `stored_tokens`
  exists and `status` warns. The training tenant is not changed for this.
- **Image:** the training tenant's signed runtime image, by the same digest (`install` refuses when the two units
  differ): it is certainly in root's storage and nothing new is pulled. It is used for its `python3` only; the
  code is standard library only (tested on Python 3.9, 3.10 and 3.12). **Not verified before the first start:** that
  `python3` resolves as the entrypoint for uid 65534 — the training tenant's own script calls `python3` in this
  image, but as root, under bash and a ROS environment.
- **Host network, one address, no published port**, the port opened only behind a service that answers, runtime
  and permanent, no `--reload` — all as for the DCGM exporter, for the reasons in `70-dcgm.md`. The tailnet
  reaches `10.20.0.1:9401` with or without the opening; accepted there, and here it is a page of numbers.
- **The assistant's scrape is configuration only.** vLLM 0.24.0 serves `/metrics` on the API's port — read on the
  host, names included (`vllm:generation_tokens_total`, `vllm:num_requests_running`, `vllm:num_requests_waiting`).
  `8000/tcp` is already open for the guests (`64-assistant-expose.sh`). The observability namespace has no
  NetworkPolicy (read with `oc get`), and the path is the GPU scrape's: out through the node to `10.20.0.1`. The
  scrape keeps four metric names of about 470 series, because this Prometheus keeps 7 days on the node's disk.
  The Perses datasource's `allowedEndpoints` are about the Prometheus API paths Perses may call, not about scrape
  targets: unchanged, and the new round variable uses `/api/v1/query`, which is listed.
- **Dashboard against the running Perses (v0.53.0), from its source, not tried yet:** `StatChart` 0.12.0
  (`calculation`, `format`, `sparkline`), `PrometheusPromQLVariable` (`expr`, `labelName`; an instant query),
  variables substituted in panel titles, a selected value that is no longer an option replaced by the first one,
  `display.collapse.open`, `visual.stack`, `querySettings[].lineStyle`. No query uses `$1`: Perses reads a `$` as
  one of its variables.
