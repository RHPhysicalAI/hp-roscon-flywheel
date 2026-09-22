<!-- This project was developed with assistance from AI tools. -->
# The Fury demo's URLs

As of 2026-09-20, tenants mode. The `*.sno-flywheel.local` names resolve on the tailnet (the host's dnsmasq).
The routes use the cluster's self-signed certificate: a browser has to accept it **once per hostname** - a page
that embeds another host's pictures (the flywheel page embeds the fleet wall's streams) shows nothing until that
other host's certificate has been accepted too.

## What is shown

| What | URL | Notes |
|---|---|---|
| Flywheel page | https://dashboard-flywheel.apps.sno-flywheel.local | live lane: robot zero's cameras, episodes judged and not kept, counter to 160 |
| Paired evaluation | https://eval-dashboard-flywheel.apps.sno-flywheel.local | pinned to the governed run `9fb233e8` (295 -> 333 of 360) |
| Live episodes - live lane | https://eval-dashboard-show-flywheel.apps.sno-flywheel.local | robot zero's last 300 episodes as the curator judged them, success rate by model label; judged, not kept - a throwaway volume, no recordings. The flywheel page's "Live episodes" link |
| Live episodes (collection) | https://eval-dashboard-live-flywheel.apps.sno-flywheel.local | the governed collection's record: success by lineage from object storage and the manifest topic. Never fed by the live lane - in tenants mode it does not move |
| Fleet wall | https://fleet-wall-flywheel.apps.sno-flywheel.local/ | every robot's overhead camera, ray-traced on slice `0:3`; `r00` is robot zero |
| One robot's cameras | https://fleet-wall-flywheel.apps.sno-flywheel.local/robot/r00/static.mjpg | also `/wrist.mjpg`, `.jpg` for one frame, any `rNN` |
| GPU tenants dashboard | https://edge-perses-observability.apps.sno-flywheel.local/projects/observability/dashboards/gpu-tenants | four slices by tenant, training loss, renderer and assistant headlines |
| Red Hat Edge Manager | https://ui.flightctl.apps.sno-flywheel.local | devices, fleets, rollouts; log in with the cluster account |
| Coding workspace (Dev Spaces) | https://devspaces.apps.sno-flywheel.local | the workspace on branch `demo/coding-task`; prompt: "Read DEMO-TASK.md and do what it says." |
| OpenShift console | https://console-openshift-console.apps.sno-flywheel.local | |
| OpenShift AI | https://data-science-gateway.apps.sno-flywheel.local | pipelines runs, model registry |
| Argo CD | https://openshift-gitops-server-openshift-gitops.apps.sno-flywheel.local | every app under GitOps |
| Object storage console | https://minio-console-minio.apps.sno-flywheel.local | say "object storage" on stage |
| Transparency log (Rekor) | https://rekor-server-trusted-artifact-signer.apps.sno-flywheel.local | the signatures' entries; the landing page shows only a count - `tools/hub/verify-signed.sh` verifies the pinned model and prints its entry's address (`/api/v1/log/entries?logIndex=N`) |
| The repository, the promotion PRs | https://github.com/RHPhysicalAI/hp-roscon-flywheel/pulls | PR #7 is the recorded promotion |
| Images | https://quay.io/repository/jary/soarm-flywheel , https://quay.io/repository/jary/soarm-act-modelcar | runtime and model images, signed |

## APIs and endpoints (not pages)

| What | URL |
|---|---|
| OpenShift API | https://api.sno-flywheel.local:6443 |
| Edge Manager API (`flightctl login`) | https://api.flightctl.apps.sno-flywheel.local |
| Edge Manager agent endpoint | https://agent-api.flightctl.apps.sno-flywheel.local |
| Model registry REST | https://flywheel-rest.apps.sno-flywheel.local |
| Model catalog | https://model-catalog.apps.sno-flywheel.local |
| Pipelines API | https://ds-pipeline-dspa-flywheel.apps.sno-flywheel.local |
| Sim cameras (flywheel mode only) | https://sim-cameras-flywheel.apps.sno-flywheel.local |

## On the host's hub-side address (tailnet or hub network only, plain http)

| What | URL |
|---|---|
| Fleet wall, direct | http://10.20.0.1:9702/ (`/status`, `/healthz`, `/wall.mjpg`) |
| Coding assistant (OpenAI-style API) | http://10.20.0.1:8000/v1 - in the cluster: `http://assistant.flywheel.svc:8000/v1` |
| GPU metrics | http://10.20.0.1:9400/metrics |
| Tenant metrics (training, renderer) | http://10.20.0.1:9401/metrics |
| Flywheel page by NodePort | http://10.20.0.10:30801 |
| Live lane's curator (the GPU host only) | http://10.20.0.10:30812/episode |
| The flywheel's own curator | http://10.20.0.10:30802/episode |

Host shell: `ssh gb300@hp-fury` (tailnet). Terminal view of the training tenant:
`FURY_SSH=gb300@hp-fury tools/hub/training-watch.sh`.
