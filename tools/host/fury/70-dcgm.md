<!-- This project was developed with assistance from AI tools. -->
# GPU telemetry: the tenants on one screen — operator page

NVIDIA's DCGM exporter runs on the GPU host as a root quadlet (`flywheel/dcgm-exporter.container`) and listens on
`10.20.0.1:9400` only, the host's side of the VM network. The hub scrapes it with a Prometheus of its
own (a Cluster Observability Operator `MonitoringStack`), and Perses draws the dashboard **GPU tenants**:

- **MIG on (tenants):** one row per GPU instance — compute busy and memory used, named by profile
  (`3g.126gb / instance 1`) — above one overview of all slices. This is Phase 8's exit screen.
- **MIG off (flywheel):** the same panels show one line, `whole GPU`. Nothing to switch on the dashboard.
- **Both:** power draw and temperature of the board.

The exporter follows the mode by itself: its default device selector watches the whole GPU without MIG and each
GPU instance with it, and MIG series carry `GPU_I_ID` and `GPU_I_PROFILE` [1]. Per-slice utilisation is
`DCGM_FI_PROF_GR_ENGINE_ACTIVE`, a profiling field: NVML does not attribute utilisation to MIG devices [4].

The command blocks are meant to be pasted as they are: no comments inside them.

## Install: the host first, then the hub

On the host. The first line installs the `fury-mode` that knows the exporter (it installs only, nothing is
stopped); `install` refuses without it (*The mode switch*, below). `install` pulls the pinned image (once, needs
the uplink), lets quadlet check the unit, starts it, and only once it answers with samples opens `9400/tcp` in
firewalld's `libvirt-to-host` policy; then it prints the label set and three samples. If it ends in an error after
the unit was installed, its last line is the way back: `./70-dcgm.sh remove`.

```
cd ~/flywheel-setup
./14-flywheel-services.sh
./70-dcgm.sh install
```

Then the hub: commit the four new files in `gitops/observability/` (`monitoring-stack.yaml`, `fury-gpu-scrape.yaml`,
`prometheus-datasource.yaml`, `gpu-tenants-dashboard.yaml`). The Argo application is a plain directory with
`prune` and `selfHeal`; it picks new files up without any other change. Argo CD reporting *Synced* means the
custom resources exist, not that Perses accepted the dashboard: that is hop 4 below.

## Verify each hop

**1. The exporter**, on the host. Loopback does not answer by design — the exporter takes one listen address:

```
./70-dcgm.sh status
curl -s -m 5 http://10.20.0.1:9400/metrics | grep -c '^DCGM_'
```

**2. The firewall**, from the hub node (expect `200`), and from any machine on the lab network against the
host's uplink address (expect `000`: nothing listens there, and that zone does not open the port). The first
line proves the firewall from the node's network namespace, not the pod-egress path; hop 3 proves that.

```
oc debug $(oc get nodes -o name | head -1) -- chroot /host curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://10.20.0.1:9400/metrics
curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://<uplink-address>:9400/metrics
```

A third one, from a laptop on the operator's tailnet. Expect `200` — accepted: this host routes `10.20.0.0/24`
for the tailnet, so its peers reach `10.20.0.1` with or without the opening in `libvirt-to-host`, and the tailnet
is the access-controlled admin network:

```
curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://10.20.0.1:9400/metrics
```

**3. Prometheus has the target up.** One terminal holds the port-forward, a second one asks:

```
oc -n observability get monitoringstack edge-metrics -o jsonpath='{range .status.conditions[*]}{.type}={.status} {end}{"\n"}'
oc -n observability get pods -l app.kubernetes.io/part-of=edge-metrics
oc -n observability port-forward svc/edge-metrics-prometheus 9090:9090
```

```
curl -s -G http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=up{job="fury-gpu"}' | jq -r '.data.result[] | "\(.metric.instance) up=\(.value[1])"'
curl -s -G http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=count by (GPU_I_PROFILE) (DCGM_FI_PROF_GR_ENGINE_ACTIVE{job="fury-gpu"})' | jq -c '.data.result[] | [.metric.GPU_I_PROFILE, .value[1]]'
```

`up=1`; then `["3g.126gb","1"]` and `["1g.31gb","3"]` in tenants mode, a single `[null,"1"]` in flywheel mode.

**4. Perses has the panel.** This is the real check of the hub side: Argo CD reporting *Synced* means the two
custom resources exist, not that Perses accepted them. Whether it did is in `describe` (status and events) and in
the perses-operator's log. That operator was installed by hand: the fourth line says in which namespace, the fifth
assumes the usual one. Then open the route, project `observability`, dashboard *GPU tenants*:

```
oc -n observability get persesdatasource prometheus-edge
oc -n observability get persesdashboard gpu-tenants
oc -n observability describe persesdashboard gpu-tenants
oc get deployments -A --field-selector metadata.name=perses-operator
oc -n openshift-cluster-observability-operator logs deployment/perses-operator --tail=200 | grep -i -E 'gpu-tenants|prometheus-edge'
oc -n observability get route edge-perses -o jsonpath='https://{.spec.host}{"\n"}'
```

## The mode switch

The exporter embeds a DCGM host engine and holds the driver open. NVIDIA's MIG guide: *"All daemons holding
handles on driver modules need to be stopped before MIG enablement"*, naming DCGM, and its example stops the
`dcgm` service around `nvidia-smi -mig 1` [4][5]. NVIDIA's own MIG manager stops `dcgm-exporter.service` before a
mode change **and** before a change of instances, and starts it again afterwards [6]. It is not a compute process,
so `fury-mode`'s own check (`--query-compute-apps`) does not see it — the same trap as the idle policy.

So `fury-mode` stops it with the loop and starts it after the CDI refresh, which is also what makes it see the new
layout: entities are enumerated once, at start. An exit trap brings it back on every way out, a refused switch
included, and every call is quiet when the unit is not installed. The installed copy has this when the first line
prints a number above zero; after a change in the checkout, `./14-flywheel-services.sh` installs it (it installs
only, nothing is stopped). `./70-dcgm.sh install` looks for the same line and refuses without it:

```
grep -c '^telemetry=' /usr/local/sbin/fury-mode
cd ~/flywheel-setup
./14-flywheel-services.sh
```

`./70-dcgm.sh install force` installs next to a `fury-mode` that does not know the exporter. Then, and only
then, by hand around every switch:

```
sudo systemctl stop dcgm-exporter.service
fury-mode tenants
sudo systemctl start dcgm-exporter.service
```

On the dashboard a switch is a gap of about its length. The per-slice rows of the old layout stay for one time
range (15 min): Perses looks label values up over the range shown. The *All slices* row uses no variable and is
right at once.

## When it fails: symptom → cause → what to do

| Symptom | Likely cause | Do |
|---|---|---|
| `install` ends with `DCGM_FI_PROF_GR_ENGINE_ACTIVE is missing`; the log lines above it say `doesn't have sufficient privileges` | `CAP_SYS_ADMIN` did not reach the process [2] | `sudo podman inspect dcgm-exporter --format '{{.EffectiveCaps}}'`; the unit must keep `AddCapability=CAP_SYS_ADMIN` |
| the same, and `ausearch -m avc -ts recent` names `dcgm` or `nv-hostengine` | SELinux denies the confined container something profiling needs | In this order, stopping at the first that brings the series. After 1 and 2, `sudo systemctl restart dcgm-exporter.service` before installing again: `install` leaves an unchanged running unit alone. **1.** `sudo setsebool -P container_use_devices=1`, restart, `./70-dcgm.sh install`, check again. **2.** A module for exactly what was denied: `sudo ausearch -m avc -ts recent \| audit2allow -M dcgm-local`, `sudo semodule -i dcgm-local.pp`, restart, install, check again. **3.** Last resort: uncomment `SecurityLabelDisable=true` in the unit (NVIDIA's documented form for CDI [7]), `./70-dcgm.sh install`. Its cost: a root, host-network, `CAP_SYS_ADMIN` container with no SELinux confinement. To revert: comment the line again, `./70-dcgm.sh install` |
| the same, with neither of the above | DCGM 4.5.2 does not offer the field for this GPU and driver — the exporter asks GPU 0 what it supports and skips the rest [1]. **Not verified for this GPU before the first run.** | memory per slice still works; utilisation falls back to `nvidia-smi` in a terminal. Try a newer exporter: `4.6.1-4.8.4-distroless` has an arm64 build [8] |
| power and temperature are empty in tenants mode only | this exporter version watches GPU instances *or* the GPU, not both, and DCGM returned nothing for board fields on an instance. **Not verified either way.** | the combined selector `g+i` exists from exporter 4.6.0-4.8.3 [8]; with it, `Environment=DCGM_EXPORTER_DEVICES_STR=g+i` is only valid while MIG is on |
| the unit restarts every 15 s, journal: `10.20.0.1 is not up yet` | the libvirt network is down | `sudo virsh net-list --all`; `sudo virsh net-start fury-net` |
| journal: NVML or `libnvidia-ml` cannot be loaded, or `unresolvable CDI devices` | CDI spec out of date | `sudo systemctl restart nvidia-cdi-refresh.service dcgm-exporter.service` |
| `fury-mode`: `mig mode is pending` or `could not leave mig mode: something is holding the gpu` | the exporter was running through the switch: the installed `fury-mode` predates it (*The mode switch*) | `sudo systemctl stop dcgm-exporter.service`, then `fury-mode <mode>` again, then `./14-flywheel-services.sh` so that the next switch does this by itself. If MIG still reports pending with the exporter stopped, the change needs a reboot — shut the hub VM down cleanly first: `sudo virsh shutdown sno-flywheel`, and wait until `sudo virsh list` no longer shows it. After the reboot, `fury-mode <mode>` once more: the failed switch left the loop stopped |
| `status` warns that the entity count does not match the instances | the layout changed under a running exporter | `sudo systemctl restart dcgm-exporter.service` |
| hop 2 gives `000` from the node | the port is not open at runtime, or `virbr-fury` left its zone | `./70-dcgm.sh status` (runtime and permanent must both say open); `sudo firewall-cmd --get-zone-of-interface=virbr-fury` must say `libvirt-routed` |
| hop 2 gives `200` from the lab network | the listen address was widened, or 9400 was opened in another zone | `sudo ss -Hltn 'sport = :9400'` must show `10.20.0.1:9400` only; `sudo firewall-cmd --list-all-zones \| grep -B12 9400` |
| hop 3: no `fury-gpu` target at all | the `ScrapeConfig` is not selected: label `app.kubernetes.io/part-of: edge-metrics` missing, or group `monitoring.coreos.com` instead of `monitoring.rhobs` [9] | fix the file; `oc -n observability get scrapeconfigs.monitoring.rhobs` |
| hop 3: target there, `up=0` | `connection refused`: exporter down (expected during a switch). `context deadline exceeded`: firewall | hops 1 and 2 |
| the Prometheus pod stays `Pending` | `replicas` went back to the default 2; the operator's anti-affinity is *required* [9] | `prometheusConfig.replicas: 1` |
| `persesdashboard gpu-tenants` exists, the dashboard does not | Perses refused it: its plugin schemas are closed, and the running version may differ from the one these files were written against (v0.53.1) [10] | `oc -n observability describe persesdashboard gpu-tenants`; the operator's log names the field |
| panels say no data, hop 3 is fine | the datasource: name `prometheus-edge`, or the Service URL | `oc -n observability get persesdatasource prometheus-edge -o yaml` |
| tenants mode shows one row with four lines instead of four rows | the variable got a `customAllValue`, or a single instance is selected | remove it; pick *All* in the variable |

## What the choices rest on

- **Image** `nvcr.io/nvidia/k8s/dcgm-exporter:4.5.2-4.8.1-ubi9`, multi-arch list
  `sha256:e28d8e75…bf260` from the registry API; its `linux/arm64` entry is `sha256:cdd87eff…57ab`.
- **Host network, one address, no published port.** A published port is a DNAT rule, and firewalld accepts DNATed
  traffic before zones and policies are consulted — the opening in `libvirt-to-host` would decide nothing. With
  host networking that opening is what lets the guests (the hub) reach the port. The lab uplink does not reach it:
  no listener on its address, and its zone never opens 9400. The exporter takes exactly one listen address in
  this version [3], hence no loopback. *(Reasoned from how the pieces work, not tested on this host.)* Exactly two
  listeners would need the exporter's `--web-systemd-socket` and a `.socket` unit.
- **The tailnet reaches it, and that is accepted.** This host is the tailnet's subnet router for `10.20.0.0/24`,
  so a tailnet peer reaches host ports on `10.20.0.1` without any entry in `libvirt-to-host` — seen on this host
  with the camera bridge on 8081, which answered a laptop before its port was opened. The tailnet is the
  operator's access-controlled admin network; hop 2 checks this on purpose.
- **Sees every slice:** the exporter gets the whole GPU because it is the operator's observer outside the tenant
  boundary, not one of the tenants.
- **Capabilities:** all dropped, `CAP_SYS_ADMIN` added, no new privileges — NVIDIA's own manifest for this
  version [2]. DCGM reads the profiling counters only as an administrator [1].
- **Collectors:** `dcp-metrics-included.csv`. In this version it and `default-counters.csv` carry the same
  active fields; this is the one documented to include profiling [3].
- **Firewall:** the port is added only once the unit answers with samples — that fetch is local and does not
  traverse the policy — at runtime *and* permanently, without `--reload`: a reload discards
  runtime-only state [11], and the zone of `virbr-fury` is libvirt's runtime state.
- **5 s collection, 10 s scrape, 10 s refresh:** a slice that starts work shows within two scrapes.

## Sources

1. DCGM exporter metrics, labels, MIG, profiling: https://docs.nvidia.com/datacenter/dcgm/latest/reference/dcgm-exporter-metrics.html ; profiling module and MIG metrics: https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html
2. Running the container, `--cap-add SYS_ADMIN`: https://docs.nvidia.com/datacenter/dcgm/latest/installation/install-dcgm-exporter.html ; manifest at the pinned version: https://github.com/NVIDIA/dcgm-exporter/blob/4.5.2-4.8.1/dcgm-exporter.yaml
3. Command reference: https://docs.nvidia.com/datacenter/dcgm/latest/reference/command-line-reference/dcgm-exporter.html ; flags as built into the pinned version: https://github.com/NVIDIA/dcgm-exporter/blob/4.5.2-4.8.1/pkg/cmd/app.go ; collectors: https://github.com/NVIDIA/dcgm-exporter/tree/4.5.2-4.8.1/etc
4. MIG user guide, enabling MIG, driver clients, utilisation metrics: https://docs.nvidia.com/datacenter/tesla/mig-user-guide/getting-started-with-mig.html
5. MIG user guide, deployment considerations: https://docs.nvidia.com/datacenter/tesla/mig-user-guide/deployment-considerations.html
6. NVIDIA MIG manager, services stopped around a change: https://github.com/NVIDIA/mig-parted/blob/main/deployments/systemd/hooks.sh
7. NVIDIA Container Toolkit, CDI with podman: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html
8. Combined device selector: https://github.com/NVIDIA/dcgm-exporter/blob/4.6.0-4.8.3/pkg/cmd/app.go ; image tags and platforms: the registry API at `nvcr.io/v2/nvidia/k8s/dcgm-exporter`
9. Cluster Observability Operator, API and controller at v1.5.1: https://github.com/rhobs/observability-operator/blob/v1.5.1/docs/api.md ; https://github.com/rhobs/observability-operator/blob/v1.5.1/pkg/controllers/monitoring/monitoring-stack/components.go
10. Perses plugin schemas at the versions the operator builds against: https://github.com/perses/plugins/tree/prometheus/v0.57.0/prometheus/schemas ; https://github.com/perses/plugins/blob/timeserieschart/v0.12.1/timeserieschart/schemas/time-series.cue ; repeated groups: https://github.com/perses/shared/blob/v0.53.1/dashboards/src/components/GridLayout/GridLayout.tsx
11. `firewall-cmd --reload`: https://firewalld.org/documentation/man-pages/firewall-cmd.html
