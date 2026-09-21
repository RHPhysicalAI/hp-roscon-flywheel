<!-- This project was developed with assistance from AI tools. -->
# gitops/rhem/ — the Fleets, rendered by Red Hat Edge Manager

This directory is synced by the flightctl `ResourceSync/rhem-fleets`
(`rhem/bootstrap/resourcesync.yaml`), **not** by Argo CD: Fleets are flightctl API objects, not
Kubernetes CRs, so there is no Argo `Application` for this path.

Rules for files in this directory: flat (the sync does not descend into subdirectories); the sync
renders **every** `*.yaml`, `*.yml` and `*.json` file it finds on the pushed branch and skips the
rest (this README); one or more flightctl resources per file, `apiVersion: flightctl.io/v1beta1`,
Fleets only (`type: fleet` sync). A Fleet goes live by its file being here, and the sync deletes a
Fleet it owns once its file is gone - so nothing else belongs in this directory. The CatalogItem
lives in `gitops/rhem-catalog/` (its own `ResourceSync/rhem-catalog`, `type: catalog`): a sync
handles one resource type.

## Two Fleets, one model

| File | Fleet | Selects | What it delivers |
|---|---|---|---|
| `fleet-act-inference.yaml` | `act-inference` | devices labelled `fleet=act-inference` - the GPU host, enrolled as a device | the signed ACT policy as a quadlet application, with the signed model image as an image volume; on the host it is placed on a MIG slice by the device's `gpu_device` label (robot zero) |
| `fleet-robots.yaml` | `robots` | devices labelled `fleet=robots` - the twelve RHEL image mode micro-VM robots (`docs/internal/FLEET-VMS.md`) | the same policy and model on CPU, plus a Zenoh router on each robot |

Both Fleets write the same trust files to their devices (`policy.json`,
`registries.d/quay-jary.yaml`, `cosign.pub`, `rekor.pub`), so every device pulls its images itself
and checks the cosign signature and the transparency-log entry of what it runs. Labels are set at
approval: `tools/host/fury/41-device-enroll.md` for the host, `tools/hub/fleet-approve.sh` for the
robots. Each file's header comment explains its labels, its trust chain and its choices.

Why two Fleets and not one: a slow micro-VM must not fail the host's rollout, and a CPU device on
two vCPUs needs other settings than the host. The two files cannot share a templated value
(placeholders read device labels only), so they are kept in step instead:

- **The model** - the model image's digest and `MODEL_VERSION` - is edited by the promotion pull
  request, never by hand. The pipeline's `open_promotion_pr` step edits **both files in one
  commit** and refuses when they do not hold the same model beforehand. One merge therefore rolls
  the signed model to the host and walks the robots. `tools/hub/reset-promotion.sh` and
  `tools/hub/reopen-promotion.sh` keep the same rule: every Fleet file pins what the host Fleet's
  file pins. Rollback is a `git revert` of that merge - the previous image is still in each
  device's storage.
- **The runtime image digest and the four trust files** are edited by hand, in both files.
- `tests/pipeline/test_open_promotion_pr.py` fails when the two files disagree on the model
  image's digest, `MODEL_VERSION` or the runtime image digest. No test compares the trust files.

## Rollout

| Fleet | Batches | Thresholds |
|---|---|---|
| `act-inference` | the device carrying `role=canary` (one), then everything labelled `site=fury` | success 100%, update timeout 30 min |
| `robots` | the robot carrying `role=canary` (one), then up to 25% of the Fleet, then up to 50%, then the rest (the implicit last batch) | canary 100%, every other batch 90%; never more than five robots updating at once (`disruptionBudget`, `maxUnavailable: 5`); update timeout 30 min |

A percentage counts all devices the selector matches, and devices updated by earlier batches count
towards it. With twelve robots a model rollout walks them in waves of 1, 2, 3, 5 and 1: the last
six are split by the five-at-a-time budget.

The batches govern template **changes**. A newly approved robot gets the current template at once;
what throttles first pulls is how many VMs are created at a time
(`tools/host/fury/81-fleet-scale.sh`). A robot that is shut off cannot update and holds its batch
until the timeout, so a rollout is run with every enrolled robot running.

## Known limit

`HealthOnFailure=kill` with `Restart=always` restarts a container whose health check fails. The
check (`docker/healthcheck.sh`) asks whether the expected model version is published and the
policy's action server is on the graph - not whether the server answers. A crashed server is
restarted; a wedged one never trips the restart.
