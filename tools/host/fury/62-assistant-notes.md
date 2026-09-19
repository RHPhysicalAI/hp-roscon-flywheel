<!-- This project was developed with assistance from AI tools. -->
# The coding assistant as a governed quadlet — design notes and first-run plan

**Status: drafted and checked offline, nothing has run.** Phase 6, second half (D154 "not yet done": the governed
form). Pieces: application `llm-assistant` in `gitops/rhem/fleet-act-inference.yaml`, lifecycle in
`tools/hub/fury-switch.sh`, operator steps in `41-device-enroll.md` section 8. Flags, environment and mounts are
those of `61-rhaiis-smoke.sh`; that script stays the place to try a flag before it goes into the Fleet.

## Design

| Question | Answer | Basis |
|---|---|---|
| Where | A second application in Fleet `act-inference`; a device belongs to one Fleet. | flightctl docs |
| Only on the GPU host | flightctl has no per-application condition: every application is rendered for every device. The condition is inside `llm-assistant.container`: one `if/else` on label `llm_gpu_device`. Unlabelled devices get a placeholder that runs `/bin/true` from the runtime image they already hold. | `internal/tasks/fleet_rollout.go` |
| Why not `ConditionPathExists=` | The agent pulls every `Image=` of a rendered quadlet before anything starts, stopped or not, so the fleet VMs (default-reject pull policy) would fail their update on the server image. The placeholder names no new image. | `provider/quadlet.go` `collectOCITargets` |
| Tags on comment lines | The API parses the unrendered template as a unit file; a bare `{{ else }}` line inside a section is a parse error there. `# {{ else }}` is skipped by that parser and still acted on by the template engine. | `api/core/v1beta1/application_validation.go`, go-systemd lexer |
| The slice | `AddDevice=nvidia.com/gpu=<label value>`, a `MIG-<uuid>` CDI name (`0:0` is not a legal label value). UUIDs are stable (D141). | — |
| The image | One label-free `Image=` line, pinned by digest. `Entrypoint=python3` with `-m vllm.entrypoints.openai.api_server` names the server explicitly, so no other line depends on the image. | smoke script |
| Caches | `llm-cache.volume` → podman volume `llm-assistant-300457-llm-cache` at `/tmp`. An update is remove-then-add; on removal the agent deletes image-driver and tmpfs volumes only. The volume outlives updates, label removal and the application. | `lifecycle/quadlet.go` `removeEphemeralVolumes` |
| Quiet with MIG off | `ExecCondition=` looks for the slice in `nvidia-ctk cdi list`; exit 1 is a skip: unit inactive, not failed, and `Restart=always` does not restart a skipped start. `ExecStartPre=` with `RestartPreventExitStatus=` cannot do this — that setting is compared with the main process's exit status only. RHEM shows `Unknown`, or `Stopped` when the application is stopped. | systemd v257 `service.c` `service_shall_restart` |
| Stopped by default | `desiredState` is read-only in the API; it cannot be written in the Fleet file. `flightctl app stop fleet/act-inference --name llm-assistant --yes` sets a fleet-wide default (kept in a Fleet annotation, which a ResourceSync update does not replace); a later device-level start wins. The agent still starts a freshly installed application and then stops it — the `ExecCondition=` covers that moment. | `internal/service/fleet/application_lifecycle.go`, `podman_monitor.go` |
| Start and health | Started = container running (`Notify=` default), so the agent's blocking `systemctl start` returns in seconds: `TimeoutStartSec=300`. Loading belongs to the health check: `HealthStartPeriod=20m` (measured cold start 5 min 46 s), then 3 × 30 s, `HealthOnFailure=kill`, `Restart=always`, `RestartSec=30`, five starts an hour. | D154, podman docs |
| Stop | SIGTERM (vLLM handles SIGTERM and SIGINT alike), `StopTimeout=60`, `TimeoutStopSec=120`, `SuccessExitStatus=143`. | vLLM v0.24.0 `entrypoints/launcher.py` |
| The switch | Both applications are stopped before `fury-mode`, one is started after. The assistant is stopped first even when it should be down: the agent acts on a change of desired state, and a start on top of `running` does nothing. | `podman_monitor.go` `QueueLifecycle` |

## Not verified — find out on the first run

- The Fleet passes the API's validation with the two-branch unit (checked here with a port of the parser, not with flightctl).
- The quadlet generator on podman 5.8.2 accepts the rendered unit, and the JSON `HealthCmd=` reaches podman unchanged.
- `--gpu-memory-utilization 0.90` on the 3g slice; D154 ran 0.5 of the whole GPU.
- Adding the application does not restart `act-inference`: the agent diffs per application (`provider.GetDiff`) — read
  in the source, not observed. Likewise the placeholder's status where it is not stopped: `Completed`, because podman 5.6
  puts the exit code on the `remove` event too (`libpod/events.go`); stopped, it is `Stopped` either way.
- The server image is still in root's storage (pulled by digest for the smoke test). If not, the agent pulls it,
  and the agent's pull timeout applies.
- A tailnet peer's source address, as the host sees it, is its `100.64.0.0/10` address also when it targets `10.20.0.1`.
- After a reboot in tenants mode the unit is ordered after `mig-config` and `nvidia-cdi-refresh`; whether the agent's
  start comes late enough is untested. A skipped start is not retried: `flightctl app restart`.
- `nvidia-smi --query-compute-apps`, `fury-mode`'s last check, lists the server while it serves from a MIG instance.
- The secret-backed API key below, end to end.

## Left to the operator

- **API key or not.** Default: none. With a key: the `secretRef` entry drafted in the Fleet file (commented out).
  The file is delivered to every device of the Fleet, mode 0644, and is part of the rendered spec. A per-device
  variant exists because `secretRef.name` is templated: `name: {{ getOrDefault .metadata.labels "llm_env_secret" "llm-assistant-env-none" }}`
  with a second Secret whose `env` key is a comment line.
- **Exposure.** `--host 0.0.0.0` with host networking; firewalld decides (section 8). No TLS. Binding to one address
  instead would need that address as a label.
- **Which image.** One line. After changing it, remove the cache volume once: compiled kernels belong to one build, and
  the two builds may run as different users. An image from `registry.redhat.io` also needs registry credentials on the device.
- **`fury-mode.sh`** does not know the assistant's unit. Its last check (compute processes on the GPU) still refuses a
  switch under a serving assistant, without naming the fix. A proposed change that refuses first and says how to stop it
  was handed over with this draft; it is not applied.
- CPU and memory limits (`PodmanArgs=`) so a cold start does not starve its neighbours; `/metrics` for Phase 8.

## API key from a Secret (untested)

Hub, as cluster admin. The service account name is the one flightctl's documentation uses; check it with the third line.

```
KEY=$(openssl rand -hex 24)
oc -n flightctl create secret generic llm-assistant-env --from-literal=env="VLLM_API_KEY=$KEY"
oc -n flightctl get deploy -o custom-columns=NAME:.metadata.name,SA:.spec.template.spec.serviceAccountName
oc -n flightctl create role llm-assistant-env-reader --verb=get --resource=secrets --resource-name=llm-assistant-env
oc -n flightctl create rolebinding llm-assistant-env-reader --role=llm-assistant-env-reader --serviceaccount=flightctl:flightctl-worker
oc auth can-i get secret/llm-assistant-env -n flightctl --as=system:serviceaccount:flightctl:flightctl-worker
```

Then swap the two `llm-assistant-env` entries in the Fleet file and merge. The application is not restarted by a
config change: `flightctl app restart device/$DEV --name llm-assistant --yes`. A changed Secret reaches devices with
the next template version unless auto-sync is set up (flightctl `auto-syncing-dependencies.md`).

## First run, in tenants mode — order, what to watch, what a failure means

0. **Before the branch is pushed** — the ResourceSync follows it, so a push is a rollout. Let the API validate a copy
   that selects no device: the Fleet file with `metadata.name` and the selector's `fleet:` value both changed to
   `act-inference-validate`, `flightctl apply -f` on that copy, then `flightctl delete fleet/act-inference-validate`.
   A validation error here costs nothing; the same error after a push stops the sync of the real Fleet until fixed.
   This step is itself untested: it rests on reading that apply and the sync share one validation path.
1. **Merge, sync.** Watch the rollout finish; every device shows `llm-assistant` `Completed`, `act-inference` keeps
   running with its `restarts` unchanged. *Sync error naming `llm-assistant.container`:* the API did not accept the
   template — the comment-line tags or the doubled sections. *Device update error from the quadlet generator:* a key
   that device's podman does not know (`Entrypoint=` needs 5.0).
2. **Fleet-wide stop** (section 8). `Stopped` everywhere. *`Application not found`:* the Fleet has not synced.
3. **Tenants mode, read the UUID, smoke container down, image and model present, port free** (section 8).
4. **Label.** `UpToDate`, `Stopped`, the UUID in `AddDevice=` and `ExecCondition=`. Before any start, on the host:
   `sudo /usr/libexec/podman/quadlet -dryrun 2>&1 | grep -A 30 'llm-assistant-300457-llm-assistant.service'` and
   `systemctl cat llm-assistant-300457-llm-assistant.service` — `--health-cmd` must still be the JSON array, the
   server arguments complete. *Update failed on a pull:* image not in storage, or the `pull_default` label is gone.
5. **Start by hand, not through the switch:** `flightctl app start device/$DEV --name llm-assistant --yes`, with
   `sudo journalctl -fu llm-assistant-300457-llm-assistant.service` open. Expect weights in about 2 min 40 s, compile,
   warm-up, `Application startup complete`; `podman ps` goes from `(starting)` to `(healthy)`. *Exits within seconds:*
   an argument or import error, in the journal. *Permission denied under `/models`:* relabel or `o+rX` (the smoke
   script's check). *Permission denied under `/tmp`:* a cache volume written by another user — remove it. *Address in
   use:* the smoke container. *Killed at 20 minutes:* raise `HealthStartPeriod=`. *Killed about 90 s after it was
   serving:* the health command — `sudo podman healthcheck run llm-assistant-300457-llm-assistant; echo $?`.
6. **Reach it:** host loopback first, then firewalld, then the laptop (section 8). `ask` and `bench` of the smoke
   script work against it unchanged (same port, same served name) and give the slice's numbers to compare with D154.
7. **Stop and time it:** `flightctl app stop …`. Under 60 s, exit 0 or 143, the slice's memory free in `nvidia-smi`.
   *SIGKILL after 60 s:* raise `StopTimeout=`; the agent resets the failed unit either way.
8. **Warm start:** start again and time it — what the cache volume saves.
9. **Both directions with `fury-switch.sh`.** Then, in flywheel mode, start the assistant on purpose: the journal says
   `is not there (MIG off): not starting`, the unit is inactive, `systemctl show -p NRestarts` stays 0, RHEM says
   `Unknown`. Stop it again so the next switch finds it stopped.
10. **Optional:** a Fleet change to the unit while it serves — the volume is still there afterwards
    (`sudo podman volume ls --filter name=llm-assistant`) and the restart is warm. A reboot in tenants mode.

## Rollback

- **One device:** remove the label (section 8). The server and its unit go; the application stays as the placeholder,
  because it cannot be conditional. The cache volume stays until `sudo podman volume rm llm-assistant-300457-llm-cache`.
- **Everything:** revert the Fleet commit. The application leaves every device; `fury-switch.sh` then reports the
  assistant as not handled and behaves as before.
- **Firewall:** the same two `firewall-cmd --permanent` lines with `--remove-rich-rule=` / `--remove-port=`, then `--reload`.
