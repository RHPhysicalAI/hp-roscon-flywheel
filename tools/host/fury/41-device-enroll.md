<!-- This project was developed with assistance from AI tools. -->
# Enrol the host as the Fleet's device — operator steps

Phase 4 of `docs/internal/FURY-PLAN.md`. `device/enroll.sh` cannot be used here: it drives the device over
ssh with passwordless sudo and parks the enrollment key in `/tmp`. On this machine the host half is
`40-device-provision.sh`, run by the operator, and the hub half is the steps below, run from a laptop on
the tailnet with `flightctl` 1.3.0 and `jq`.

The command blocks are meant to be pasted into an interactive shell as they are: no comments inside them,
no placeholders. Where a value is yours, it is a variable you set first.

Before starting, on the host: `fury-mode status` says MIG is off (flywheel mode) and the sim is up. The
policy needs the sim's Zenoh router to become healthy, and the exit criterion needs episodes.

## 1. Laptop: point at this hub and log in

The development stand-in's hub uses the same names. If the laptop pins them in `/etc/hosts`, comment those
lines out first — everything below would otherwise talk to the wrong cluster without complaining. On macOS:

```
dscacheutil -q host -a name api.flightctl.apps.sno-flywheel.local
```

Expect `10.20.0.10`. Then log in as a real OpenShift user (a ServiceAccount token maps to no organisation).
`oc login` asks for the password itself; run it in your own terminal.

```
export KUBECONFIG=$(mktemp)
oc login https://api.sno-flywheel.local:6443 -u kubeadmin --insecure-skip-tls-verify
flightctl login https://api.flightctl.apps.sno-flywheel.local --token "$(oc whoami -t)" --insecure-skip-tls-verify
flightctl get resourcesync
flightctl get fleet/act-inference
```

Expect `rhem-fleets` synced and the Fleet present. If the Fleet is missing, stop: `rhem/bootstrap/README.md`
comes first.

## 2. Laptop: make the enrollment config and copy it to the host

Set `FURY` to the ssh target you normally use for the host (edit the first line), then paste the rest.
The command writes the config to stdout and drops a key, a certificate and the CA next to it, hence the
throwaway directory. The config carries the enrollment client key: it goes to the host and nowhere else.

```
FURY=user@host
d=$(mktemp -d)
flightctl certificate request --signer=flightctl.io/enrollment --expiration=365d --output=embedded --name fury-host --output-dir $d > $d/agent-config.yaml
chmod 600 $d/agent-config.yaml
grep -c client-key-data $d/agent-config.yaml
scp $d/agent-config.yaml $FURY:flywheel-setup/agent-config.yaml
rm -f $d/agent-config.yaml $d/fury-host.key $d/fury-host.crt $d/ca.crt
rmdir $d
```

`grep -c` prints `1`. If `rmdir` complains, look at what is left in `$d` and delete it by hand.

## 3. Host: provision

`40-device-provision.sh` and a copy of `gitops/rhem/fleet-act-inference.yaml` have to be in `~/flywheel-setup`.
If the policy is still running from the hand-installed unit the script stops and says what to do; it does
not stop it for you.

```
cd ~/flywheel-setup
./40-device-provision.sh agent-config.yaml
```

It ends by printing this device's name and the labels. Keep that output for the next step. What it changed:
the hand-installed `act-inference.container` is gone, the distro's `policy.json` is kept as
`/etc/containers/policy.json.rhel-default`, `flightctl-agent-1.3.0-1.el10` is installed (repo left disabled)
and running, `agent-config.yaml` is installed as `/etc/flightctl/config.yaml` and shredded.

## 4. Laptop: approve, with the labels

```
flightctl get enrollmentrequests
ER=$(flightctl get enrollmentrequests -o json | jq -r '.items[] | select(.status.approval == null) | .metadata.name')
echo $ER
```

`$ER` must be exactly one name, and the one the script printed on the host. If it is not, stop here.

```
flightctl approve -l fleet=act-inference -l site=fury -l gpu=nvidia -l arch=arm64 -l policy_device=cuda -l zenoh_router=10.20.0.1 -l zenoh_port=7447 -l alias=fury-host -l pull_default=insecureAcceptAnything enrollmentrequest/$ER
DEV=$ER
flightctl get devices -o wide
```

Notes on the labels:

- No `gpu_device` in flywheel mode: without it the Fleet renders `AddDevice=nvidia.com/gpu=all`, the whole GPU.
- No `role`: the canary is a fleet VM (Phase 7). This device lands in the `site=fury` batch.
- `alias=fury-host` replaces the alias the agent proposes, which is the hostname — unset on this machine.
- `zenoh_router` and `zenoh_port` default to `127.0.0.1` and `7447` in the template; they are set here so the
  device's labels say what it uses.
- `pull_default=insecureAcceptAnything` is for this host only. It is also a shared build and tenant machine, so
  podman keeps pulling from anywhere, while the two `quay.io/jary` repositories stay signature- and
  Rekor-enforced. A device without the label renders `reject` (the fleet VMs). Any other value is not a
  policy type, and podman then refuses every pull.
- Values may hold letters, digits, `-`, `_` and `.` only, 63 characters at most. `10.20.0.1:7447`, `0:1` and
  `nvidia.com/gpu=0:1` are all refused by the API; a slice is named by its `MIG-<uuid>` (section 6).

## 5. Verify the exit criterion

Laptop — the device as RHEM sees it. It takes a few minutes: two image pulls, then up to four minutes of
health-check start period.

```
flightctl get device/$DEV -o json | jq '{labels: .metadata.labels, owner: .metadata.owner, device: .status.summary.status, updated: .status.updated.status, applications: .status.applicationsSummary.status, apps: [.status.applications[] | {name, status, ready, restarts}]}'
flightctl get device/$DEV --rendered | grep -e AddDevice= -e MODEL_VERSION
```

Expect owner `Fleet/act-inference`, device `Online`, updated `UpToDate`, applications `Healthy`, and
`act-inference` `Running` `1/1`. ("Healthy" in the plan's exit line is the applications summary; the device
summary says `Online`.) The rendered spec shows `AddDevice=nvidia.com/gpu=all` and `MODEL_VERSION: act-v2-ft160`.

Host — the quadlet is the agent's, it is the only policy, and the trust policy is the Fleet's:

```
sudo podman ps --format '{{.Names}}  {{.Status}}'
systemctl is-active act-inference-128875-flightctl-quadlet-app.target act-inference-128875-act-inference.service
sudo grep -h -e AddDevice -e Image= /etc/containers/systemd/act-inference/*.container /etc/containers/systemd/act-inference/*.volume
sudo grep -v -e '^$' /etc/act-inference/env
sudo podman logs --tail 40 act-inference-128875-act-inference 2>&1 | grep -e 'Published model_version' -e 'Device:'
sudo jq -r '.default[0].type' /etc/containers/policy.json
nvidia-smi --query-compute-apps=pid,name,used_memory --format=csv
```

Expect `act-inference-128875-act-inference` `Up … (healthy)` and no container called plain `act-inference`;
both units `active`; the Fleet's two digests and `AddDevice=nvidia.com/gpu=all`; `ZENOH_ROUTER=10.20.0.1:7447`
and `POLICY_DEVICE=cuda` with no thread caps; `Published model_version: act-v2-ft160`; `insecureAcceptAnything`
(the `pull_default` label at work). The policy shows up in `nvidia-smi` only after its first goal — the model is
loaded lazily. That the enforcement is still there: `sudo jq '.transports.docker | keys' /etc/containers/policy.json`
lists the two `quay.io/jary` repositories.

Host — episodes stamped with the Fleet's model version. The recorder is an operator-started unit; re-run
`14-flywheel-services.sh` once after enrolling so that its unit no longer asks for the hand-installed policy.

```
sudo systemctl start act-coordinator.service
```

Give it three or four episodes (a minute or so each; the first one after a fresh policy start is a miss), then:

```
sudo sh -c 'ls -t /data/flywheel/episodes/raw/*.json | head -3 | xargs jq -r "[.timestamp, .model_version, .cubes_placed, .rollout.status] | @tsv"'
sudo systemctl stop act-coordinator.service
```

Expect `act-v2-ft160` — the Fleet's `MODEL_VERSION` — on episodes timestamped after the approval, with cubes
placed. The hand-installed unit published the same string, so the string alone proves nothing: it counts
because the agent's container is the only policy on the graph (the `podman ps` line above).

## 6. Later: serving from a MIG slice (`gpu_device`)

Only worth doing when tenants mode has something for the policy to talk to. Today it does not: the Zenoh
router lives in the sim container, the sim cannot run with MIG on, and a policy that cannot reach a router
never passes its health check and is killed and restarted every five minutes or so.

The order matters. A label change re-renders the quadlet and the agent restarts it at once, so the slice has
to exist before the label names it, and the policy has to be down before MIG can change at all.

Laptop:

```
flightctl app stop device/$DEV --name act-inference --yes
```

Host, then read the UUID of slice `0:1` (`Device  1`):

```
fury-mode tenants
nvidia-smi -L
```

Laptop — edit the first line, paste the rest:

```
GPU_DEVICE=MIG-00000000-0000-0000-0000-000000000000
d=$(mktemp -d)
flightctl get device/$DEV -o json | jq --arg v $GPU_DEVICE 'del(.status) | .metadata.labels.gpu_device = $v' > $d/device.json
flightctl apply -f $d/device.json
rm -f $d/device.json
rmdir $d
flightctl get device/$DEV --rendered | grep AddDevice=
flightctl app start device/$DEV --name act-inference --yes
```

Going back to the whole GPU is the same dance with the label removed — `jq 'del(.status) | del(.metadata.labels.gpu_device)'`
in place of the `jq` above — and `fury-mode flywheel` on the host between the stop and the start.
`flightctl edit device/$DEV` does the same label change in an editor.

## 7. Later: switching modes with the policy under RHEM

The policy is the hub's to stop and start now; `fury-mode` handles the sim, the recorder and MIG. The stop is
a device-level override that survives Fleet rollouts, so a promotion merged while the machine is in tenants
mode does not bring the policy back up on the wrong device.

To tenants — laptop, wait for `Stopped`, then host:

```
flightctl app stop device/$DEV --name act-inference --yes
flightctl get device/$DEV -o json | jq -r '.status.applications[] | [.name, .status, .ready] | @tsv'
```

```
fury-mode tenants
```

Back to the flywheel — host, then laptop:

```
fury-mode flywheel
```

```
flightctl app start device/$DEV --name act-inference --yes
```

If the hub is down, the local equivalent is
`sudo systemctl stop act-inference-128875-flightctl-quadlet-app.target` (and `start` afterwards). The agent
leaves a hand-stopped target alone, but RHEM will show the application in error until it runs again.

The same stop is what makes a local eval safe: never a second `/run_policy` on this host while the device
serves (D132) — stop the application first, start it again afterwards.

`tools/hub/fury-switch.sh tenants | flywheel | status` does all of this from the laptop in one command, and
handles the assistant of section 8 the same way.

## 8. Later: serving the assistant from the large slice (`llm_gpu_device`)

**Draft — not yet run.** The Fleet's second application, `llm-assistant`, is the governed form of what
`61-rhaiis-smoke.sh` started by hand (D154): the same server flags, on the 3g slice, in tenants mode only. The
design, what is assumed and the test plan for the first run are in `62-assistant-notes.md`; this section is the
operator's sequence. Every device of the Fleet gets the application. A device without the `llm_gpu_device` label
renders a placeholder that exits at once and pulls nothing new; the label turns it into the server.

The order matters: the application has to be stopped by default before the label arrives, and the smoke
container has to be gone — it uses the same port and the same GPU. `$DEV` and `$FURY` are the device name and
the ssh target from sections 4 and 2; the laptop blocks run from the root of this repository.

Laptop — the Fleet carries the application, then stop it fleet-wide. This is a default for every device now in
the Fleet and every device that joins later; a later start on one device overrides it for that device.

```
flightctl get fleet/act-inference -o json | jq -r '.spec.template.spec.applications[].name'
flightctl app stop fleet/act-inference --name llm-assistant --yes
flightctl get device/$DEV -o json | jq -r '.status.applications[] | [.name, .status, .ready] | @tsv'
```

Expect both names, then `llm-assistant` `Stopped` beside `act-inference` `Running`. The policy is not restarted
by any of this.

Laptop — into tenants mode. Without the label the switch stops the policy, turns MIG on and starts nothing:

```
FURY_SSH=$FURY tools/hub/fury-switch.sh tenants
```

Host — what the assistant needs, and the UUID of the 3g slice. It is the `MIG 3g.126gb  Device  0:` line; the
same name appears in the CDI list. The UUIDs survive mode switches and reboots (D141), so this is read once.

```
cd ~/flywheel-setup
./61-rhaiis-smoke.sh down
sudo podman image exists docker.io/vllm/vllm-openai@sha256:251eba5cc7c12fed0b75da22a9240e582b1c9e39f6fbc064f86781b963bd814f && echo image present
ls /data/models/RedHatAI/Qwen3-Coder-Next-NVFP4/config.json
sudo ss -Hltn 'sport = :8000'
nvidia-smi -L
nvidia-ctk cdi list | grep MIG-
```

Expect `image present`, the config file, nothing listening on 8000, and four `MIG-` names.

Laptop — add the label. Edit the first line, paste the rest:

```
LLM_GPU_DEVICE=MIG-00000000-0000-0000-0000-000000000000
d=$(mktemp -d)
flightctl get device/$DEV -o json | jq --arg v $LLM_GPU_DEVICE 'del(.status) | .metadata.labels.llm_gpu_device = $v' > $d/device.json
flightctl apply -f $d/device.json
rm -f $d/device.json
rmdir $d
flightctl get device/$DEV --rendered | grep -e 'Image=docker.io' -e 'AddDevice=nvidia.com/gpu=MIG' -e ExecCondition=
flightctl get device/$DEV -o json | jq -r '.status.updated.status, (.status.applications[] | [.name, .status, .ready] | @tsv)'
```

What to expect: the device re-renders, the agent removes the placeholder and installs the real unit, starts it and
stops it again at once because the default is stopped. Within a minute or two: `UpToDate`, `llm-assistant`
`Stopped`. No model is loaded. On the host the unit is `llm-assistant-300457-llm-assistant.service` and it is
inactive. From here on both switch directions handle the assistant.

Laptop — start it. The switch can be run again while already in tenants mode; it stops both applications,
re-applies the MIG layout and starts the assistant, then waits and prints a line every 30 s:

```
FURY_SSH=$FURY tools/hub/fury-switch.sh tenants
```

Host, in a second terminal — the load as it happens. A cold start was measured at 5 min 46 s; the first start
on an empty cache volume may take longer, and the health check allows 20 minutes before it counts failures.

```
sudo journalctl -fu llm-assistant-300457-llm-assistant.service
```

Host — open the port. The server listens on every address of the machine, port 8000; firewalld decides who
reaches it. A laptop on the tailnet arrives on `tailscale0`, whichever of the host's addresses it uses:

```
Z=$(sudo firewall-cmd --get-zone-of-interface=tailscale0)
[[ $Z == "no zone" || -z $Z ]] && Z=$(sudo firewall-cmd --get-default-zone)
echo $Z
sudo firewall-cmd --zone=$Z --list-all
```

If that zone's target is `ACCEPT` (the `trusted` zone), nothing has to be opened. Otherwise open the port for
tailnet source addresses only — the zone may also hold the uplink:

```
sudo firewall-cmd --permanent --zone=$Z --add-rich-rule='rule family="ipv4" source address="100.64.0.0/10" port port="8000" protocol="tcp" accept'
sudo firewall-cmd --reload
sudo firewall-cmd --zone=$Z --list-rich-rules
```

Only if something on the VM network (a pod on the cluster, a fleet VM) has to reach the assistant at
`10.20.0.1:8000`: guest-to-host traffic is filtered by libvirt's policy, the way `06-network.sh` opened DNS.

```
sudo firewall-cmd --permanent --policy=libvirt-to-host --add-port=8000/tcp
sudo firewall-cmd --reload
sudo firewall-cmd --info-policy=libvirt-to-host
```

Laptop — verify. `10.20.0.1` is the host on the routed network; its tailnet address works as well.

```
H=10.20.0.1
curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://$H:8000/health
curl -s http://$H:8000/v1/models | jq -r '.data[] | [.id, .max_model_len] | @tsv'
curl -s http://$H:8000/v1/chat/completions -H 'Content-Type: application/json' -d '{"model": "qwen3-coder-next", "max_tokens": 200, "messages": [{"role": "user", "content": "Write a Python function that clamps a joint angle to its limits. Code only."}]}' | jq -r '.choices[0].message.content, .usage'
```

Expect `200`, then `qwen3-coder-next  131072`, then code and a usage record. With an API key configured, `/health`
answers as before and the two `/v1` calls need `-H "Authorization: Bearer $KEY"`.

Laptop — the RHEM side:

```
flightctl get device/$DEV -o json | jq '{device: .status.summary.status, updated: .status.updated.status, applications: .status.applicationsSummary.status, apps: [.status.applications[] | {name, status, ready, restarts}]}'
FURY_SSH=$FURY tools/hub/fury-switch.sh status
```

Expect `llm-assistant` `Running` `1/1` and `act-inference` `Stopped`, applications `Healthy`. While the model
loads the application is `Running` `0/1` and the summary `Degraded`; that is the health check's start period,
not a fault.

Host — what the agent made of it:

```
sudo podman ps --format '{{.Names}}  {{.Status}}'
sudo grep -h -e Image= -e AddDevice= -e ExecCondition= /etc/containers/systemd/llm-assistant/*.container
sudo podman volume ls --filter name=llm-assistant
nvidia-smi
```

Expect `llm-assistant-300457-llm-assistant` `Up … (healthy)`, the label's UUID in `AddDevice=` and
`ExecCondition=`, the volume `llm-assistant-300457-llm-cache`, and the server's processes on the 3g instance only.

Back to act 1 — the assistant is stopped through RHEM, then MIG goes off and the policy comes back:

```
FURY_SSH=$FURY tools/hub/fury-switch.sh flywheel
```

If the hub is down, the local equivalent of the stop is
`sudo systemctl stop llm-assistant-300457-flightctl-quadlet-app.target`, as for the policy in section 7.

Taking it away again: remove the label, and the application falls back to the placeholder. The cache volume
stays — remove it by hand if the space is wanted, or after changing the server image.

```
d=$(mktemp -d)
flightctl get device/$DEV -o json | jq 'del(.status) | del(.metadata.labels.llm_gpu_device)' > $d/device.json
flightctl apply -f $d/device.json
rm -f $d/device.json
rmdir $d
```

```
sudo podman volume rm llm-assistant-300457-llm-cache
```

## 9. If something is off

- Nothing pending in step 4: `sudo journalctl -u flightctl-agent -n 50 --no-pager` on the host. `x509` errors
  mean the config was made against the other cluster — fix the laptop's name resolution, then on the host
  `sudo systemctl stop flightctl-agent`, delete `/etc/flightctl/config.yaml` and everything under
  `/var/lib/flightctl`, and redo steps 2 and 3.
- Approved, but no application arrives: `flightctl get device/$DEV -o json | jq '.metadata.owner, .status.updated'`
  and `flightctl get fleet/act-inference -o json | jq .status`. A failed render names the missing label. An
  update that failed once is not retried until the spec changes or the agent restarts.
- `rejected by policy` or `A signature was required` in the agent's journal: the digest in the Fleet is not
  signed under this hub's key and Rekor. That is a Fleet or pipeline problem, not a host one.
- `rejected by policy` on a pull from somewhere else (nvcr.io, docker.io): the device lost its `pull_default`
  label and rendered the fail-closed default. Put the label back (section 6 shows a label edit). For a single
  deliberate pull: `sudo podman pull --signature-policy /etc/containers/policy.json.rhel-default …`.
- `llm-assistant` stays `Unknown` after a start and its unit is inactive, with `is not there (MIG off): not starting`
  in `sudo journalctl -u llm-assistant-300457-llm-assistant.service`: the unit's `ExecCondition=` did not find the
  label's slice in `nvidia-ctk cdi list` — the machine is in flywheel mode, or the UUID in the label is not the 3g
  slice's. Nothing retries a skipped start; after fixing the cause, `flightctl app restart device/$DEV --name llm-assistant --yes`.
- `llm-assistant` never becomes healthy and `restarts` climbs: the journal of that unit has the server's own
  error. After five starts within an hour the unit stays `failed` (`start-limit-hit`); `flightctl app stop` and
  then `app start` clears that, because the agent resets the unit on every stop.
- The laptop's `curl` times out while `curl -s http://127.0.0.1:8000/health` on the host answers: firewalld, section 8.
