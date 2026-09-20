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

This is robot zero (D164), and it is wrapped: `tools/hub/robot-zero.sh up` and `down` do what follows in the
right order and check each step, `tools/hub/fury-switch.sh` calls them around a mode switch, and
`74-robot-zero.md` is the operator page. The policy needs something to talk to in tenants mode - the Zenoh
router in `robot-zero-sim` (`74-robot-zero-install.sh`); without it the policy never passes its health check and
is killed and restarted every five minutes or so. What follows is the same thing by hand, for when the script
cannot be used.

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
flightctl get device/$DEV -o json | jq --arg v $GPU_DEVICE 'del(.status, .metadata.annotations, .metadata.generation, .metadata.owner, .metadata.creationTimestamp, .metadata.deletionTimestamp) | .metadata.labels.gpu_device = $v' > $d/device.json
flightctl apply -f $d/device.json
rm -f $d/device.json
rmdir $d
flightctl get device/$DEV -o json | jq -r '.spec.applications[].inline[] | select(.path == "act-inference.container") | .content' | grep AddDevice=
flightctl get device/$DEV -o json | jq '.status.updated.status, .status.config.renderedVersion, .metadata.annotations["device-controller/renderedVersion"]'
flightctl app start device/$DEV --name act-inference --yes
```

Start only when the first line names the slice and the two versions of the second are equal (`UpToDate`): with
`AddDevice=nvidia.com/gpu=all` and MIG on, the policy would land on the assistant's slice. (flightctl 1.3.0 has
no `get --rendered`; the device's spec is the Fleet's template already rendered with this device's labels.)

The write is the whole device document, so it carries as little as it can: everything the hub manages is taken
out (it keeps what it is not sent), `metadata.resourceVersion` stays in — it is the hub's lock, and a device that
changed between the `get` and the `apply` is then a refused write, not a silently overwritten label map. Afterwards
`flightctl get device/$DEV -o json | jq .metadata.labels` still shows `alias`, `fleet` and `pull_default` as they were.

Going back to the whole GPU is the same dance with the label removed — the same `del(...)` followed by
`| del(.metadata.labels.gpu_device)` in place of the `jq` above — and `fury-mode flywheel` on the host between the stop and the start.
`flightctl edit device/$DEV` does the same label change in an editor.

## 7. Later: switching modes with the policy under RHEM

The policy is the hub's to stop and start now; `fury-mode` handles the sim, the recorder and MIG. The stop is
a device-level override that survives Fleet rollouts, so a promotion merged while the machine is in tenants
mode does not bring the policy back up on the wrong device.

`tools/hub/fury-switch.sh tenants | flywheel` is the wrapped way, and with robot zero (section 6) the only
complete one: it also moves the policy's placement. By hand, without robot zero —

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

## 8. If something is off

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
