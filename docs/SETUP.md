<!-- This project was developed with assistance from AI tools. -->
# Setup: from a fresh RHEL install to tenants mode

How the machine behind the demo was built, in the order it was built: from a fresh RHEL install on the workstation
to the state the demo is shown from, "tenants mode", with every tenant and the fleet running. A reference to follow
over days. The demo itself is [`FURY-DEMO.md`](FURY-DEMO.md); the system's shape is [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 1. Scope, and what you bring

> **A worked example, not an installer.** The scripts under [`tools/host/fury/`](../tools/host/fury/README.md)
> prepared and operate ONE particular machine: an HP ZGX Fury (NVIDIA GB300, aarch64, RHEL 10.2, 64k-page kernel).
> Their checks were written for that host. They format a disk, change the boot target, partition the GPU, replace
> the resolver and create VMs. **Do not run them on anything else**; read them as the record of what it took. Each
> phase ran once, on that machine, with fixes in between. The whole sequence was never replayed from zero (section 5).

| Where | What runs there |
|---|---|
| **The host, with sudo**: a human at a shell on the workstation | everything under `tools/host/fury/`. The scripts re-run themselves under `sudo`, which asks for a password here, so a human runs each one and reads what it prints. They log next to themselves in `log/` (git-ignored: the logs carry serial numbers and device ids) |
| **A laptop, against the hub**: whoever holds the cluster account | `argocd/`, `rhem/bootstrap/`, everything under `tools/hub/`. Needs `oc`, `flightctl` 1.3.0, `jq`, `git`, `gh`, `ssh`, and a checkout of this repository on branch `fury`. Changes in Red Hat Edge Manager (approvals, labels, decommissioning) are made from here; a host script that needs one prints the laptop command |

**No secret is in this repository.** A builder brings each of these out of band; each lives in a Secret on the hub
or in a root-only file on the host. Names only here; keys, and commands that create the Secrets without echoing a
value, are in [`argocd/README.md`](../argocd/README.md).

| You bring | Used for | Where it lives |
|---|---|---|
| The host's RHEL registration | `dnf`; the fleet's OS image build sees RHEL's repositories through it | the host's own subscription. No activation key in any script; robots are never registered |
| A pull secret for `registry.redhat.io` | the hub install; the image mode base and builder; the transparency log's base images | root-only `/root/sno-install/pull-secret` on the host, handed to podman by path |
| Registry push credentials | the hub's image pipeline, the promotion pipeline | Secret `quay-push` (`flywheel`) |
| A signing key pair and its passphrase | signing every image and model image | Secrets `cosign-signing`, `cosign-signing-key` (`flywheel`), from `tools/hub/create-cosign-secrets.sh` |
| The transparency log's signer key | the log's public key, which devices pin | Secret `rekor-signer-key` (`trusted-artifact-signer`) |
| A GitHub token (contents, pull requests) | the promotion pull request | Secret `github-token` (`flywheel`) |
| Object storage accounts | the hub's object storage and its clients | Secrets `minio-credentials` (`minio`), `hub-credentials` (`flywheel`), `minio-eval-readonly-credentials` (both namespaces); on the host, root-only `/etc/flywheel-runner/env` |
| A database password | the model registry | Secret `model-registry-db` (`rhoai-model-registries`), from `tools/hub/create-model-registry-db-secret.sh` |
| The cluster admin password | `oc login`, `flightctl login`, the Edge Manager page, Dev Spaces | printed once by the hub install; kept under `/root/sno-install`, root-only |
| Tailnet administration | the route to the VM network, split DNS | the tailnet's admin console |

**Conventions.** Host commands run from `~/flywheel-setup`: a copy of `tools/host/fury/` (scripts, `flywheel/`,
`fleet/`, `sno/`, the support files) plus `tools/host/disk-guard.sh`, which `14-flywheel-services.sh` wants next to
it; the operator pages expect that path. Install scripts that need source code read a checkout at
`~/hp-roscon-flywheel` (or `SRC=<checkout>`). Laptop commands run from the root of the checkout. Command blocks are
pasted as they are. "One-time" means bring-up only: nothing so marked is a demo-time step.

## 2. The shape you are building

One workstation: a 72-core Grace CPU and one GB300 GPU, with memory in two pools joined by NVLink-C2C (about 250 GB
of HBM on the GPU, 492 GB of LPDDR5X on the CPU); as delivered, a workstation image with GNOME running, on the
64k-page kernel. Its one GPU is split with MIG into four slices, one tenant each. A single-node Red Hat OpenShift
4.22 hub runs as a KVM guest on the same machine (32 vCPU, 128 GiB, 600 GB) on a routed libvirt network,
`fury-net`, `10.20.0.0/24`: the host is `10.20.0.1`, the hub `10.20.0.10`, robot `NN` is `10.20.0.(20+NN)`. The
host's own dnsmasq serves the cluster's names (`*.sno-flywheel.local`) to the guests, to itself and to the tailnet.
On the hub: Red Hat OpenShift AI 3.5 (pipelines, model registry), OpenShift Pipelines, Red Hat OpenShift Dev
Spaces, object storage, Kafka, a transparency log, and Red Hat Edge Manager 1.3.0 from Red Hat's chart
`redhat-rhem`. Argo CD delivers `gitops/` from branch `fury`; Edge Manager's own sync delivers the Fleets in
`gitops/rhem/`. Edge Manager manages 13 devices: the GPU host itself and twelve RHEL image mode micro-VM "robots",
each of which pulls and verifies its own signed images. Twelve physics-only worlds run as pods on the hub. The
system stays in this state; pieces are shown and reset one by one.

| Slice | Tenant | What is seen |
|---|---|---|
| `0:0` (3g) | coding assistant: a coder model served by vLLM | a Dev Spaces workspace whose agent uses it |
| `0:1` (1g) | robot zero: the Edge Manager-delivered, signed ACT policy, placed on this slice by a device label | the flywheel page's cameras; `r00` on the fleet wall |
| `0:2` (1g) | training tenant: real ACT fine-tunes, in rounds; nothing from them is promoted | a loss curve; a terminal view |
| `0:3` (1g) | fleet rendering tenant: ray-traces every robot's cameras with CUDA | the fleet wall |

## 3. Phases, in order

### Phase 0 - Host preparation

**Goal:** a host that boots to a console, never suspends, keeps its journal, has `/data`, the virtualization stack
and the container toolkit, four MIG slices that return after a reboot, the VM network, DNS and the tailnet route.
**Who:** host, sudo; all one-time. **Prerequisites:** RHEL 10.2 installed and registered; the NVIDIA driver present
(Red Hat ships it, with the toolkit, in the RHEL 10 Supplementary repository, so no NVIDIA package repository is
needed; no script here installs the driver); the machine joined to a tailnet (no script here does that either).

```
cd ~/flywheel-setup
./00-journald.sh
./01-base.sh
./02-data-disk.sh
./03-packages.sh
./04-mig.sh
./05-smoke.sh
./06-network.sh
./07-netprobe.sh up
./07-netprobe.sh down
```

| Script | Does | Good |
|---|---|---|
| `00-journald.sh` | creates `/var/log/journal` so the journal survives a reboot | the directory listed |
| `01-base.sh` | masks the sleep targets, sets `multi-user.target`, disables `gdm` and `nvidia-fabricmanager` | prints `multi-user.target` |
| `02-data-disk.sh` | finds the one blank 4T disk, shows it and asks (`mkfs.ext4 on ...? [y/N]`); mounts it as `/data`; moves rootful container storage onto it | `/data` and `/var/lib/containers` in `findmnt` |
| `03-packages.sh` | libvirt, qemu-kvm, virt-install, dnsmasq, tmux, skopeo, jq, `nvidia-container-toolkit-1.20.0`, the running kernel's `modules-extra`; enables the modular libvirt sockets | `xt_mark` in `lsmod` |
| `04-mig.sh` | slices the GPU (profile ids `9,19,19,19`: 3g + 1g + 1g + 1g); installs `mig-config.sh` and `mig-config.service` so the layout returns at boot. Refuses while something computes on the GPU. Does not reboot | four `nvidia.com/gpu=0:N` in `nvidia-ctk cdi list` |
| `05-smoke.sh` | `nvidia-smi -L` from a confined container on each slice. Run it **before a reboot and again after**: the second run is the "did it all come back" check | each slice sees exactly one MIG device |
| `06-network.sh` | defines `fury-net`, lets guests ask the host for DNS, installs `dnsmasq-fury.conf`, makes the host resolve through its own dnsmasq, advertises `10.20.0.0/24` to the tailnet | ends `now: approve 10.20.0.0/24 and add split dns sno-flywheel.local -> ...` |
| `07-netprobe.sh up`, `down` | a network namespace on the bridge at `10.20.0.99`, in place of a guest before any VM exists: gateway, DNS from the host, the way out | it answers a ping from a laptop on the tailnet, the real test of the subnet route |

Then by hand, in the tailnet's admin console: approve the `10.20.0.0/24` route and add split DNS for
`sno-flywheel.local` pointing at the host's tailnet address. A laptop on the tailnet then resolves
`api.sno-flywheel.local` to `10.20.0.10`, although nothing listens there yet. `recon.sh` and `recon2.sh` are
read-only looks; `copy-vmedia.sh` takes a checked copy of an installer image attached through the BMC (not run here).

| Trap found on this machine | What it does, and what to do |
|---|---|
| **The graphical session fights the GPU** | The GNOME greeter asks for suspend after 15 idle minutes; the NVIDIA driver refuses, the attempt fails, consoles freeze and the VPN rebinds. A display server also holds the GPU, which blocks MIG changes. `01-base.sh` is the fix. `nvidia-fabricmanager` failing on a single-GPU station is expected: disable it |
| **The BMC's virtual media** | Eject it before any reboot. An installer ISO left attached shows up as a USB disk that stops answering; the initramfs waits minutes for the device manager to give up on it before it looks for the root volume. Boots took 4 to 17+ minutes until this was understood |
| **The journal is volatile as delivered** | A failed boot leaves no logs: `00-journald.sh` before the first planned reboot |
| **The kernel console is serial-only** | The BMC's KVM shows a blank screen during boot. Add `console=tty0` at GRUB to see boot text there (by hand; no script does it) |
| **Two same-model 3.7 TB disks, one already holding data** | `nvmeXn1` names are enumeration order: address disks through `/dev/disk/by-id`. `02-data-disk.sh` takes no name by default and refuses a disk with any signature |
| **The 64k kernel lacks `xt_mark`** | until `kernel-64k-modules-extra` is installed; Tailscale's subnet-router rules need it |
| **libvirt is modular on RHEL 10** | enable the `virtqemud`, `virtnetworkd`, ... sockets, not the monolithic `libvirtd` |
| **MIG mode does not survive a reboot** on this GPU generation | a boot-time unit re-enables and re-slices, ordered before the toolkit's unit. Profiles are given by id because their names change between driver branches |
| **Let the toolkit own the CDI spec** | Red Hat's `nvidia-container-toolkit` enables a unit that writes `/var/run/cdi/nvidia.yaml` at boot, and that directory outranks `/etc/cdi`. Do not hand-write a second spec. After a manual reslice: `sudo systemctl restart nvidia-cdi-refresh.service` |
| **libvirt filters guest-to-host traffic** | A routed libvirt network lets guests out and the tailnet in, but its `libvirt-to-host` firewalld policy rejects guests talking to the host except for a short list (dns, dhcp, ssh, icmp). Every later phase that gives the hub something to reach on `10.20.0.1` opens its one port in that policy (`15-`, `64-`, `70-`, `73-`, `75-`) |
| **Give `/etc/resolv.conf` one owner** | NetworkManager and Tailscale both rewrite it. Here the host uses its own dnsmasq, which also serves the cluster names to guests and to the tailnet; the switch happens only after dnsmasq has answered a cluster name and an outside name |

### Phase 1 - The GPU and the robot loop on the bare host

**Goal:** proof, before any cluster exists, that CUDA, the simulator and the policy run on this silicon; then the
robot loop under systemd, with `fury-mode` installed. **Who:** host, sudo; one-time. **Prerequisites:** Phase 0;
the checkout at `~/hp-roscon-flywheel`. Run the build inside `tmux`.

```
cd ~/flywheel-setup
./10-cuda.sh
./11-sim-build.sh
./12-sim-smoke.sh
./13-first-inference.sh
./13-first-inference.sh down
./14-flywheel-services.sh
fury-mode flywheel
fury-mode status
```

| Script | Does | Good |
|---|---|---|
| `10-cuda.sh [slice]` | arm64 torch from the flywheel's runtime image on one MIG slice (default `0:1`), on the 64k-page kernel, SELinux-confined | CUDA answers from the slice |
| `11-sim-build.sh` | builds the sim image natively as `localhost/soarm-sim:arm64` (ROS 2, Gazebo, the upstream demos workspace; about six minutes). No pipeline builds that tag; the host's sim unit and the renderer's build use it | the image in root's storage |
| `12-sim-smoke.sh` (`down` removes it) | starts the sim headless; checks joint states, both cameras, the camera bridge on `:8081` | it reports; it fixes nothing |
| `13-first-inference.sh [episodes] [cdi-device] [label] [image]` | the policy as its own container, driven by a short seeded evaluation against the running sim | `cubes_placed > 0` with goals accepted: a healthy container alone proves nothing |
| `14-flywheel-services.sh` | installs only: sim and recorder as quadlets behind `fury-flywheel.target`, the disk guard, `mig-config.sh`, `/usr/local/sbin/fury-mode` | ends with `fury-mode status` and the next command to type |

**`fury-mode` owns the GPU's two modes** (no `sudo` in front: it asks for it itself). `fury-mode flywheel`: MIG off;
the sim renders its cameras on the GPU and shares it with policy serving and governed training. `fury-mode tenants`:
MIG on, four slices; it starts the coding assistant, the training tenant, the renderer and robot zero's host side,
each if its unit is installed. `fury-mode zero` restarts robot zero's host side alone; `fury-mode status` changes
nothing. A switch needs the GPU idle, so it stops the robot loop, every tenant and the GPU exporter first; the choice
is written to `/etc/sysconfig/mig-config`, so a reboot returns in the same mode. It refuses while a governed training
or an evaluation runs and, once the host is enrolled, while the Edge Manager-managed policy is up on the whole GPU:
the hub stops that (Phase 4). Scripts `21-` to `25-` are the rendering experiments behind this design; a rebuild does
not run them.

**Traps:** **a MIG slice has no graphics.** Compute only: the simulator cannot render its cameras on one. Hence two
modes, and in tenants mode sims run physics-only while one slice ray-traces every camera with CUDA
([`internal/FLEET-RENDER-CONTRACT.md`](internal/FLEET-RENDER-CONTRACT.md)). **Rootless containers cannot open the
GPU here** (SELinux); GPU workloads are root-run units. The first episode after a fresh policy start is always a
miss: the model loads onto the GPU with the first goal.

### Phase 2 - The hub VM

**Goal:** single-node OpenShift as a KVM guest on `fury-net`. **Who:** host, sudo; one-time. **Prerequisites:**
Phase 0; the pull secret at `/root/sno-install/pull-secret`. Run `wait` inside `tmux`.

```
cd ~/flywheel-setup
./30-vm-pretest.sh up
sudo virsh console rhcos-pretest
./30-vm-pretest.sh down
./31-sno-hostprep.sh
./32-sno-install.sh fetch
./32-sno-install.sh image
./32-sno-install.sh vm
./32-sno-install.sh wait
./32-sno-install.sh finish
```

| Step | Does | Good |
|---|---|---|
| `30-vm-pretest.sh up`, `down` | no pull secret needed: boots the public live image as a throwaway 32 vCPU / 128 GiB guest, to find out whether a 4k-page guest runs on this 64k-page host, with UEFI, routing, DNS and NTP from a guest | the console logs in as `core` by itself; leave with Ctrl-] |
| `31-sno-hostprep.sh` | installs `nmstatectl` (the installer fails without it); adds the node's forward and reverse DNS records | `host is ready for the install` |
| `32-sno-install.sh fetch [channel]` | installer and `oc` for aarch64, checksums verified; default `stable-4.22` | `channel ... is at <version>` |
| `... image` | renders the secret-free templates in `sno/` with the pull secret and an ssh key; builds the agent ISO | `image built. it carries the pull secret: root-only, and 'finish' deletes it` |
| `... vm` | defines and starts the guest: 32 vCPU, 128 GiB, 600 GB | `started. next: ...` |
| `... wait` | follows the install to the end and prints the cluster admin password | the header allows about an hour; here the console answered about 28 minutes after the guest started |
| `... finish` | ejects and deletes the ISO, autostart, clean guest shutdown with the host, first `oc` checks | `kubeconfig: /root/fury-sno.kubeconfig` |

Everything lives in `/root/sno-install`, mode 700: the rendered config, the ISO and the installer's state all carry
the pull secret. **Traps:** `rendezvousIP`, the node's address, the dnsmasq records and the MAC in the VM definition
all have to agree (`sno/agent-config.yaml`). The guest runs the default 4k-page kernel under the 64k-page host; the
memory balloon is left out. RHEL 10 ships no `/etc/sysconfig/libvirt-guests`; `finish` creates it. If the laptop's
`/etc/hosts` pins any `sno-flywheel.local` name, comment those lines out: a pinned name beats DNS.

### Phase 3 - Hub bootstrap

**Goal:** every Argo CD application Synced and Healthy from branch `fury`, the transparency log up, the signing
Secrets in place, Edge Manager installed and syncing its Fleets from git. **Who:** laptop, except the log's image
builds (host, ordinary user); one-time. **Prerequisites:** Phase 2; the credentials of section 1.

**1. Operators, applications, EndpointSlices**, in the order of [`argocd/README.md`](../argocd/README.md)'s bootstrap
table. Each file is applied once by hand; after that Argo CD syncs its `gitops/<dir>` with prune and self-heal. Do
not paste the three blocks as one: between them something has to be true. First the operators; the `oc adm` line
works once the `openshift-gitops` namespace exists.

```
export KUBECONFIG=~/.kube/fury-login
oc login https://api.sno-flywheel.local:6443 -u kubeadmin --insecure-skip-tls-verify
oc apply -f argocd/bootstrap-operators.yaml
oc adm policy add-cluster-role-to-user cluster-admin -z openshift-gitops-argocd-application-controller -n openshift-gitops
oc apply -f argocd/storage-app.yaml
oc apply -f argocd/operators-app.yaml
oc get csv -A
```

When every operator is `Succeeded`, and each hand-made Secret exists before its consumer syncs
(`minio-credentials` and `minio-eval-readonly-credentials` for `minio-app.yaml`; `hub-credentials` and
`minio-eval-readonly-credentials` for `flywheel-app.yaml`; `quay-push` and `cosign-signing` before a pipeline run;
`model-registry-db` in the namespace the DataScienceCluster creates):

```
oc apply -f argocd/operators-config-app.yaml
oc apply -f argocd/minio-app.yaml
oc apply -f argocd/flywheel-app.yaml
oc apply -f argocd/observability-app.yaml
oc apply -f argocd/rhem-app.yaml
oc apply -f argocd/tekton-app.yaml
oc apply -f argocd/devspaces-app.yaml
oc get applications.argoproj.io -n openshift-gitops
```

`argocd/rhem-app.yaml` installs Edge Manager 1.3.0 from Red Hat's chart, `redhat-rhem` on `charts.openshift.io`:
every image is the product's and the page reads "Red Hat Edge Manager". `argocd/repo-flightctl-charts.yaml` is only
needed to go back to the project's own chart; `argocd/fleet-worlds-app.yaml` belongs to Phase 8. Once the `flywheel`
application has synced, the three EndpointSlices, once per hub (why: section 4):

```
oc apply -f tools/hub/manual/sim-cameras-endpointslice.yaml
oc apply -f tools/hub/manual/assistant-endpointslice.yaml
oc apply -f tools/hub/manual/fleet-renderer-endpointslice.yaml
```

**2. The transparency log.** Red Hat Trusted Artifact Signer 1.4.3 ships its server images for amd64 only, so this
hub runs Rekor on Trillian **rebuilt natively from Red Hat's public source of that release: not built by Red Hat,
not the product, not supported.** No Fulcio, CT log, TUF or TSA; signing is key-based. Follow the seven steps of
[`tools/host/fury/rhtas-arm64/README.md`](../tools/host/fury/rhtas-arm64/README.md): switch the cluster's own
registry on (on a single node this rolls the API server once: minutes of API errors), build and push the images from
the host (only `build.sh bases` needs a `registry.redhat.io` login), apply the operator outside OLM, create the
signer Secret, apply `rekor-trillian.yaml`. Measured here: six images in about four and a half minutes, the log
Ready about 90 seconds after its CRs. The log's public key goes, byte for byte, into
`gitops/tekton/rekor-public-key.yaml` and both Fleets' inline `rekor.pub`, unless the old signer key was reused.

**3. Signing and registry Secrets, the promotion pipeline.** The first script generates a key pair when there is
none (an existing pair is never overwritten), asks for the passphrase on the terminal, checks that it opens the key,
and creates both signing Secrets; its `cosign.pub` is the Fleets' inline `cosign.pub`. Use the cosign release the
pipeline signs with (`COSIGN=<path>`). The third uploads `pipeline/act_flywheel_pipeline.yaml` under the name the
training trigger looks it up by, with a ten-minute token of the account that starts the runs.

```
tools/hub/create-cosign-secrets.sh
tools/hub/create-model-registry-db-secret.sh
tools/hub/upload-pipeline.sh
```

**4. Edge Manager's own GitOps loop**, per [`rhem/bootstrap/README.md`](../rhem/bootstrap/README.md). Fleets are
Edge Manager API objects, not Kubernetes resources, so Argo CD cannot apply them. Log in with a real user's token
(a ServiceAccount token maps to no organisation):

```
flightctl login https://api.flightctl.apps.sno-flywheel.local --token "$(oc whoami -t)" --insecure-skip-tls-verify
flightctl apply -f rhem/bootstrap/repository.yaml
flightctl apply -f rhem/bootstrap/resourcesync.yaml
flightctl apply -f rhem/bootstrap/catalog.yaml
flightctl apply -f rhem/bootstrap/resourcesync-catalog.yaml
flightctl get repository,resourcesync
flightctl get fleets
```

Good: the Repository `Accessible`, both ResourceSyncs `Synced`, Fleets `act-inference` and `robots` listed and
valid. The sync renders **every** `.yaml` in `gitops/rhem/` on the pushed branch: nothing but Fleets belongs there.

**Traps:** **Argo CD does not manage `EndpointSlice` or `Endpoints`**, and reports the application Synced anyway:
"Synced" says nothing about what Argo CD was never going to create. On the OpenShift 4.22 catalog the OpenShift AI
subscription's `stable` channel resolves to an older release; the manifest says `stable-3.5`. The Edge Manager
chart's setup jobs default to an amd64-only CLI image (symptom: `ImagePullBackOff` and a sync that never finishes);
`rhem-app.yaml` names Red Hat's multi-arch one. Object storage, Kafka and the episode directory are `hostPath`
volumes whose node directories are made by hand (section 4). **An image digest goes into a Fleet only after that
digest has served on a GPU**: the trust chain proves who built an image, not that it starts.

### Phase 4 - Enrol the GPU host as a device

**Goal:** the host is a device of Fleet `act-inference`; the signed policy runs as the agent's quadlet. **Who:**
laptop and host, in turns; one-time. **Prerequisites:** Phase 3 (`flightctl get fleet/act-inference` answers); on
the host `fury-mode status` says MIG off and the sim is up, because the policy needs the sim's Zenoh router to
become healthy. Every command and what to expect:
[`tools/host/fury/41-device-enroll.md`](../tools/host/fury/41-device-enroll.md).

| # | Where | Step |
|---|---|---|
| 1 | laptop | make the enrolment config with `flightctl certificate request` and copy it to `~/flywheel-setup/agent-config.yaml` on the host (section 2 of that page). It carries the enrolment client key: it goes to the host and nowhere else, and the local copies are removed |
| 2 | host | the command below, with a copy of `gitops/rhem/fleet-act-inference.yaml` next to the script. It removes the hand-installed policy unit, keeps the distribution's `policy.json` as `/etc/containers/policy.json.rhel-default`, installs `flightctl-agent-1.3.0-1.el10` (pinned, repository left disabled), installs the config root-only as `/etc/flightctl/config.yaml` and shreds the copy. It ends by printing the device's name and the labels |
| 3 | laptop | approve the one pending request, whose name must be the one the host printed, with the labels of section 4 of that page: `fleet=act-inference`, `site=fury`, `gpu=nvidia`, `arch=arm64`, `policy_device=cuda`, `alias=fury-host`, `pull_default=insecureAcceptAnything`, the Zenoh router's address and port |
| 4 | both | verify as section 5 of that page says: owner `Fleet/act-inference`, `Online`, `UpToDate`, applications `Healthy`, `act-inference` `Running 1/1`. It takes a few minutes: two image pulls, then up to four minutes of health-check start period |
| 5 | host | `./14-flywheel-services.sh` once more, so the recorder's unit no longer asks for the hand-installed policy. It prints `enrolled device: act-inference.container left out, the policy comes from RHEM` |

```
cd ~/flywheel-setup
./40-device-provision.sh agent-config.yaml
```

**Traps:** `pull_default=insecureAcceptAnything` is for this host only, because it is also a build and tenant
machine; the two signed repositories stay signature- and log-enforced on it. A robot never gets the label and
renders `reject`; any other value is not a policy type, and podman then refuses every pull. Label values may hold
letters, digits, `-`, `_` and `.` only: a slice is named by its `MIG-<uuid>`, never `0:1`. From here on the policy
is the hub's to stop and start (`flightctl app stop|start`, an override that survives rollouts), and there is never
a second policy server on this host while the device serves. The aarch64 packages the device needs exist: the pinned
agent above, and RHEL 10.2's own podman (5.8.2) has the image-volume feature Edge Manager 1.3 needs.

### Phase 5 - The flywheel's host half

**Goal:** what a governed run needs on the host, and the hub's view of the sim's cameras. **Who:** host, sudo;
one-time. **Prerequisites:** Phase 4; flywheel mode.

```
cd ~/flywheel-setup
./15-camera-port.sh open
./50-runner-install.sh
./51-eval-rig.sh status
./52-seed-incumbent.sh
./53-stage-promotion.sh
./54-teacher-modelcar-check.sh
```

| Script | Does |
|---|---|
| `15-camera-port.sh open`, `status`, `close` | lists `8081/tcp` for the guests, so the hub's https Route `sim-cameras` can carry the camera streams (a browser will not load an http stream into an https page) |
| `50-runner-install.sh` | installs the host half of a governed training run as a root quadlet from the runtime image, plus the evaluation rig with its path and service units; starts nothing. Its first run writes the two object storage keys to `/etc/flywheel-runner/env` (root, 0600), taken from Secret `flywheel/hub-credentials` with root's kubeconfig, never echoed |
| `51-eval-rig.sh run`, `serve-one`, `status`, `down` | the evaluation rig: a candidate and the incumbent scored on the same seeds in a pod of its own, beside the running loop ([operator page](../tools/host/fury/51-eval-rig.md)) |
| `52-seed-incumbent.sh` (`force` replaces) | seeds the incumbent: re-packs the serving model's checkpoint from the signed model image already on the machine and uploads it where a training run looks for it. After `50-` |
| `53-stage-promotion.sh [staging-dir]` | stages a promotion's inputs from a staging directory the builder brings (default `/data/models/import-dev`; not in git). It also puts under `/data/flywheel` the dataset and the two checkpoints the training tenant reads (Phase 7) |
| `54-teacher-modelcar-check.sh` (`nopolicy` skips the last) | three checks before a Fleet is pointed at a model image: it pulls by digest under the device's own signature policy, the weights match, they serve on the GPU. About ten minutes |

From the laptop a run is started by hand with `tools/hub/start-promotion-run.sh <candidate> [steps_per_frame]
[eval_n]`. The training trigger (`TRAINING_PIPELINE_NAME` in `gitops/flywheel/manifest-consumer.yaml`) starts one by
itself at 160 curated successes, and is disarmed on show days.

### Phase 6 - Images

Everything that runs from the two signed repositories is built, pushed, signed and verified by the hub's
`runtime-image` pipeline (clone, buildah, push, cosign sign and verify into the hub's log). **Who:** laptop,
`KUBECONFIG` set; needs `oc`, `jq`, `git`; one-time per image. The build scripts refuse when the branch has unpushed
commits or the image's sources have uncommitted changes (the pipeline clones from the remote), follow the run, take
`--follow <run>` to pick one up again, and print the digest to pin. They build for `linux/arm64`, the hub's own
architecture. A digest that anything pins must own a tag that no later run will move: the promotion pipeline tags
every image of a run `<candidate>-<run id>` for good.

| Image | Built by | Pinned in |
|---|---|---|
| runtime image (policy, recorder, runner, training tenant) | `oc create -n flywheel -f gitops/tekton/runtime-image-pipelinerun.example.yaml`; the first native build took about 17 minutes here | both Fleet files, the host's units |
| fleet world, the physics-only sim (about 6 minutes) | `tools/hub/build-fleet-world.sh` | `gitops/fleet-worlds/world.yaml`, robot zero's units |
| evaluation pages | `tools/hub/build-eval-dashboard.sh` | `gitops/flywheel/eval-dashboard.yaml`, `eval-dashboard-show.yaml` |
| Dev Spaces workspace | `tools/hub/build-dev-workspace.sh` | `devfile.yaml` |
| an image built elsewhere (a model image packaged earlier) | `tools/hub/cosign-image.sh <repository>@sha256:<digest>`: a second signature under this hub's key; the digest does not change | the Fleets |
| host sim, fleet renderer | on the host: `11-sim-build.sh`, `73-fleet-renderer-install.sh build` | `localhost/` tags, unsigned (section 5) |

### Phase 7 - The tenants

**Prerequisites:** Phases 0-5, and tenants mode. From the laptop the switch is one command: it takes the policy out
of service through Edge Manager, runs `fury-mode tenants` on the host (the sudo password once), then places robot
zero. Before robot zero is installed that last step fails and says so; the machine is in tenants mode all the same.
Edit the first line to the host's ssh target.

```
export FURY_SSH=user@host
tools/hub/fury-switch.sh tenants
tools/hub/fury-switch.sh status
```

Fetch the coding model ahead: 47.6 GB at one pinned revision, no root, resumable, every file verified; hours on a
slow uplink. Host, ordinary user:

```
sudo install -d -o "$USER" -g "$USER" /data/models
tmux new -d -s model ~/flywheel-setup/60-model-fetch.sh
~/flywheel-setup/60-model-fetch.sh check
```

The installs: host, sudo, all one-time; `63-`, `72-`, `73-` and `74-` also install the current `fury-mode`. The
order inside the block is free except for three things: `73-` before `74-` (robot zero waits for the renderer to
show `r00`), `72-` before `75-` (the exporter reads the output directory `72-` creates, and runs from the training
tenant's image), a `fury-mode` that knows the exporter before `70-` (Phase 1). Every unit with a slice
carries a start condition on its CDI device, so a boot or a stray start in flywheel mode does nothing. `63-` and
`72-` start nothing, which is why `fury-mode tenants` follows them: in tenants mode it stops and starts every
installed tenant. The second block is the laptop's: robot zero's GPU half.

```
cd ~/flywheel-setup
./63-assistant-install.sh
./64-assistant-expose.sh open
./72-training-tenant-install.sh install
fury-mode tenants
./73-fleet-renderer-install.sh build
./73-fleet-renderer-install.sh install
./74-robot-zero-install.sh install
./70-dcgm.sh install
./75-tenant-metrics-install.sh install
./75-tenant-metrics-install.sh slices
```

```
tools/hub/robot-zero.sh up
tools/hub/robot-zero.sh status
```

| Tenant | Install | Needs first | Good |
|---|---|---|---|
| Coding assistant, `0:0` ([smoke test page](../tools/host/fury/61-rhaiis-smoke.md)) | `63-` installs `flywheel/llm-assistant.container` and its cache volume and starts nothing. `64- open`, `status`, `close` lists `8000/tcp` for the guests | the model in `/data/models`; the serving image in root's storage: the unit pins it by digest with `Pull=never`, and `63-` prints the one `sudo podman pull` line when it is missing | `installed; nothing was started. The assistant comes up with:  fury-mode tenants`. Then `./61-rhaiis-smoke.sh ask` and `bench` (no root). On the slice: ready in under three minutes after a start, first token in about 0.13 s, about 245 tokens a second single stream |
| Training tenant, `0:2` ([page](../tools/host/fury/72-training-tenant.md)) | checks the unit against the tenant's rules (read-only inputs, one writable directory, `Network=none`, no environment file, the runner's image, its own slice), creates `/data/flywheel/tenant-train`, starts nothing | the dataset and both checkpoints under `/data/flywheel` (Phase 5); the runtime image in root's storage. On a WARNING about backbone weights, run the `podman run` line it prints, once, with the uplink: the tenant has no network | `./72-training-tenant-install.sh status`: about 10 steps a second; a round is 9000 steps, about a quarter of an hour |
| Rendering tenant, `0:3` ([page](../tools/host/fury/73-fleet-renderer.md)) | `build`: minutes, needs the uplink, ends by rendering a frame. `install` in tenants mode starts the unit, waits for `/healthz` (the first start on a slice compiles every kernel; `WAIT_S=1800` in front if it times out), and only then lists `9701/udp` and `9702/tcp` for the guests. In flywheel mode it starts nothing: run it again after the switch | the local sim image (Phase 1) | `./73-fleet-renderer-install.sh status`: `device` names the MIG slice, not `cpu`; `render rate` at the target of 15 |
| Robot zero, `0:1` ([page](../tools/host/fury/74-robot-zero.md)) | checks and installs four units without a GPU: `robot-zero-sim` (the physics-only world, the Zenoh router, `r00` to the renderer; the handle for the other three), `robot-zero-frames`, `robot-zero-episodes` (recording off), `robot-zero-emitter`. Refuses unit files that would let robot zero reach the flywheel's data. `./74-robot-zero-install.sh check` runs the rules anywhere | the renderer; the show curator (Phase 9); `src/robot-zero/` in the host's checkout; the fleet world image in root's storage (the page gives the pull line: about 5 GB, verified under the host's signature policy); no `/etc/containers/systemd/act-inference.container` | `status`: four units `active`, `r00` live. `robot-zero.sh up` names slice `0:1` in the device's `gpu_device` label, waits until Edge Manager has rendered `AddDevice=nvidia.com/gpu=MIG-...` and the agent has applied it, and only then starts the policy; it refuses a quadlet that still says `all`. Also `down`, `reset`, `status` |
| Numbers ([GPU page](../tools/host/fury/70-dcgm.md), [tenants page](../tools/host/fury/75-tenant-metrics.md)) | `70-dcgm.sh install`, `status`, `remove`: NVIDIA's pinned exporter image (pulled once, with the uplink) on `10.20.0.1:9400`, the port opened only once it answers with samples. `75-`: pulls nothing, serves on `10.20.0.1:9401`, runs in both modes | an installed `fury-mode` that knows the exporter, or `70-` refuses | both pages give a four-hop check up to the panel. `slices` (no root) must end every row in `ok`: the dashboard names slices by GPU instance id through a hand-written map, and a changed MIG layout shifts the ids |

On the hub the assistant is Service `assistant.flywheel.svc:8000`, deliberately without a Route: the API has no
key. Its workspace: `argocd/devspaces-app.yaml` (Phase 3), the image of Phase 6, and branch `demo/coding-task`
(`fury` plus the task; its `DEMO-RUNBOOK.md` holds the presenter's notes). The workspace `flywheel-coding-task`
comes from `devfile.yaml`, with the commands `run-tests`, `start-agent` and `reset-demo`. The dashboard's hub side
is in git, `gitops/observability/` (a monitoring stack, three scrape configs, the datasource, the *GPU tenants*
dashboard): *Synced* there means the custom resources exist, not that Perses accepted the dashboard.

### Phase 8 - The fleet

**Goal:** twelve robots enrolled, approved and Healthy, and twelve worlds on the wall. **Who:** host and laptop, in
turns; one-time, ahead of any showing. The runbook as run, with expectations per step and the failure table, is
section 15 of [`internal/FLEET-VMS.md`](internal/FLEET-VMS.md). Host first: prove image mode here, then the golden
image (`fleet/` with its three files next to the script). Edit the first line to an ssh public key file.

```
KEY=/path/to/key.pub
./79-bootc-spike.sh up $KEY
./79-bootc-spike.sh remove
./80-fleet-golden.sh build
```

Good: the key and signature checks pass, `OS image built in ...`, `golden image ready in ...` (here 34 s and 65 s,
under 1 GB on disk). The registry login is the pull secret, by path. `remove` the spike before scaling: it holds
robot 32's address. Fleet `robots` is already live in git (`gitops/rhem/fleet-robots.yaml`), so Phase 3's sync
created it: `tools/hub/fleet-status.sh` prints a line starting `Fleet robots:`. Laptop, then host, **back to back**:

```
tools/hub/fleet-enrol-config.sh
```

```
./81-fleet-scale.sh enrol-config fleet-agent-config.yaml
./81-fleet-scale.sh 1
```

The enrolment config is one short-lived certificate for the whole fleet (default 14 days). Until `enrol-config` has
moved it under `/root/fleet` and shredded it, it is a live credential in a shared home: if the host step slips,
`shred -u ~/flywheel-setup/fleet-agent-config.yaml` and make a new one later. Never copy it into a git checkout.
Laptop, the canary alone, dry run first:

```
FLEET_EXPECT=1 tools/hub/fleet-approve.sh --dry-run
FLEET_EXPECT=1 tools/hub/fleet-approve.sh
tools/hub/fleet-status.sh
```

Then the rest, in steps of four (every new clone downloads its images through the one uplink): on the laptop
`FLEET_EXPECT="1-12" tools/hub/fleet-approve.sh --watch` (it stops by itself after 30 minutes), on the host
`./81-fleet-scale.sh 4`, wait until `./81-fleet-scale.sh status` says `images pulled: yes` for all four, then `8`,
then `12`. Here: the canary Healthy under ten minutes after approval, all twelve about 25 minutes after the first
one booted. `fleet-approve.sh` approves only a request whose label, hostname, address, MAC and architecture are what
the image and `81-fleet-scale.sh` gave that number; anything else stays pending for a human, never denied.
`81-fleet-scale.sh <N>` means N **running**: it starts what exists, creates what is missing, shuts down what is
above N, and never deletes.

The worlds and the wall. The recorded motion is made once from the training dataset, wherever that dataset and
`numpy` / `pyarrow` are; the second block is the laptop's:

```
tools/fleet/extract_trajectories.py /data/flywheel/datasets/flywheel-ladder-160 -o trajectories.npz
```

```
oc apply -f argocd/fleet-worlds-app.yaml
tools/hub/fleet-worlds-scale.sh trajectories trajectories.npz
tools/hub/fleet-worlds-scale.sh 12
tools/hub/fleet-worlds-scale.sh status
```

Good: `worlds: 12 ready of 12 wanted (r01-r12)`, `motion: recorded`. The wall's Service and https Route
(`gitops/flywheel/fleet-wall.yaml`) came with the `flywheel` application, its endpoint with Phase 3's EndpointSlice,
its listener with the rendering tenant.

| Trap found on this machine | What it does, and what to do |
|---|---|
| **Image mode guests needed no guest image and no activation key** | A rootful build on the registered host sees RHEL's repositories through the host's subscription, and the clones are never registered. `bootc-image-builder` wants the image in the store it is handed at `/var/lib/containers/storage`; podman refuses a store that shows up there under another path than it was created at, so build in the host's own store |
| **First boot of a cloud-init clone, three traps** | Spell the default route `0.0.0.0/0` (`to: default` is netplan's word; without netplan it fails the whole pre-network stage and the clone has no address). Do not order a `multi-user.target` service after `cloud-final.service`: that stage is itself after `multi-user.target`, and systemd breaks the cycle by not starting the service; order after `cloud-init.service`, where `write_files` runs. Set `prefer_fqdn_over_hostname: false` if the short hostname matters. With no login into the clones by design, the way to see any of this is to read a clone's disk from the host: `./82-fleet-vm-peek.sh <NN>` |
| **An image with whiteouts cannot be embedded** | not in an OS image's additional image store, by a container build. The robots pull and verify their images themselves; local speed is a registry mirror's job |
| **On the single-node hub, cpu *requests* run out before cpu does** | Sixteen one-core requests booked the node to 97% with a quarter of its cpu idle, and nothing else could schedule. Request what must be guaranteed, not what is used. A world costs about 1.5 vCPUs in the hub VM (1.0 on the host's own cores); the scale script caps at sixteen and the fleet's size is twelve |
| **A shut-off robot stays enrolled** | and stalls a rollout's batch: run rollouts with every enrolled robot running |

### Phase 9 - The live lane

Nothing to install beyond what is there already. `gitops/flywheel/curator-show.yaml` (a second Deployment of the
curator's own code: the real gates, its own throwaway volume, no object storage, no Kafka, no credential, nothing
out of its pod, only the GPU host let in) and `gitops/flywheel/eval-dashboard-show.yaml` (the "Live episodes - live
lane" page) sync with the `flywheel` application; the sender is robot zero's fourth unit; the flywheel page reads
the lane through `SHOW_LANE_DIR` in `gitops/flywheel/dashboard.yaml`. Robot zero's episodes are judged, not kept:
they never reach the dataset, the trigger count or the evaluation pages. Check from the host: the line below answers
`501` when the show curator is up, and `sudo journalctl -u robot-zero-emitter -n 30` shows no `POST to curator failed`.

```
curl -s -m 4 -o /dev/null -w '%{http_code}\n' http://10.20.0.10:30812/
```

### Phase 10 - The promotion tooling

**Who:** laptop; checkout on `fury`, `KUBECONFIG`, a logged-in `flightctl`, `gh`, `jq`. Between showings, never a
demo-time step. Without their flag the first shows what would be pushed and asks, and the second is a dry run.

```
tools/hub/reset-promotion.sh --yes
tools/hub/reopen-promotion.sh --open
```

`reset-promotion.sh` reverts the merge commit of the newest promotion on the GitOps branch and brings every Fleet
file to what the host Fleet's file pins; Edge Manager rolls the previous model back out (here: the host about 50 s
after the push, all 13 devices healthy in about 5 minutes). `reopen-promotion.sh --open` proposes the same signed
model again in a new pull request that says so; it runs no pipeline and writes no image, signature, registry entry
or evaluation record. It opens the pull request with the hub's token (Secret `flywheel/github-token`), read into the
one call that needs it. Both refuse while a rollout is in progress. Merge with a **merge commit**: the next reset
finds a promotion by it. The model image digest and `MODEL_VERSION` in the Fleet files are the promotion's to
change, never edited by hand.

### Phase 11 - Final checks, and after a reboot

The checks are the first seven rows of section 6; the expected output of each is in section 3 of
[`FURY-DEMO.md`](FURY-DEMO.md). **After a host reboot:** the mode is kept in `/etc/sysconfig/mig-config`, and
`mig-config.service` restores the layout before the toolkit writes its spec; the hub VM autostarts; the tenants'
units are enabled and start when their slice exists; the two exporters start at boot; the Edge Manager agent brings
the policy back on its slice. Then, on the host:

```
fury-mode tenants
cd ~/flywheel-setup
./81-fleet-scale.sh 12
```

`fury-mode tenants` restores the layout and (re)starts every installed tenant: the assistant on `0:0`, the training
tenant on `0:2`, the renderer on `0:3` and robot zero's four host units; the policy stays on its slice, and restarts
with its world. The fleet VMs do not start by themselves; `./81-fleet-scale.sh 12` starts them in seconds, with no
approval and no pull. The worlds are pods and return with the hub.

## 4. Hand-made pieces a rebuild has to repeat

Not in git, or not applied by GitOps. A fresh bring-up does these again:

| Piece | Why, and what to do |
|---|---|
| The Secrets of section 1 | never in git; each before the application that consumes it |
| The three EndpointSlices in `tools/hub/manual/` (cameras, assistant, fleet wall) | Argo CD leaves `EndpointSlice` out of what it manages: `oc apply -f`, once per hub |
| The hub's switched-on registry, the rebuilt log images, the operator outside OLM, the signer Secret; the four `flightctl apply` objects of `rhem/bootstrap/` | Phase 3, steps 2 and 4 |
| Node directories behind `hostPath` volumes | the header of `gitops/minio/minio.yaml` gives the three commands for `/var/lib/minio` on the node; the manifests also name `/var/lib/episodes` and `/var/lib/edge-kafka` |
| The fleet worlds' recorded motion: ConfigMap `fleet-trajectories` | made from the training dataset with `tools/fleet/extract_trajectories.py` and `tools/hub/fleet-worlds-scale.sh trajectories <npz>`; without it the built-in motion runs, which reaches but rarely grasps |
| The number of worlds | `tools/hub/fleet-worlds-scale.sh 12`: Argo CD leaves replicas alone, on purpose |
| The fleet VMs after a host reboot | they do not start by themselves: `./81-fleet-scale.sh 12` (no approval, no pull). New clones need a valid enrolment certificate on the host, short-lived on purpose: `tools/hub/fleet-enrol-config.sh`, then `./81-fleet-scale.sh enrol-config` |
| MIG mode and the tenants after a reboot | `fury-mode tenants` |
| Robot zero's placement | the device's `gpu_device` label is set from the laptop (`tools/hub/robot-zero.sh up`), not from git |
| Fetched or built by hand on the host | the model weights in `/data/models`, the assistant's serving image, NVIDIA's exporter image, the local sim and renderer images, the training tenant's backbone weights, the staging directory that `53-stage-promotion.sh` reads |
| In the hub's object storage | the evaluation page's clips; the evaluation records the paired page is pinned to (`EVAL_RUN_ID` in `gitops/flywheel/eval-dashboard.yaml`); the seeded incumbent; the read-only account (its Secret is hand-made in two namespaces, a sync hook then creates the account) |
| The show curator's volume | throwaway: a rebuilt hub, or a deleted claim, starts the live lane at zero. A total written by hand for a rehearsal is put back before an audience ([robot zero's page](../tools/host/fury/74-robot-zero.md)) |
| The promotion pull request | opened ahead of a showing (Phase 10) and left open until its beat. The training trigger is disarmed for a show day by the demo owner, in git |
| The Dev Spaces workspace | created once in the Dev Spaces dashboard and then kept; it never idles out |
| The laptop's `flightctl` login | lapses within a day: `oc login`, then the `flightctl login ... --token` line of Phase 3. In the demo browser the cluster's self-signed certificate is accepted once per hostname, the fleet wall's included |
| The tailnet | the route approval and the split DNS entry (Phase 0). Who may reach the machine is described in `docs/NETWORK-ACCESS.md`, where a checkout has it |

## 5. Known gaps

- **Never replayed from zero.** Each phase ran once on one machine, over days, with fixes committed in between.
  Nobody has followed this document from a fresh install to the end.
- **A second hub has never been built against this branch.** `fury` pins this hub's public keys (both Fleets' inline
  `cosign.pub` and `rekor.pub`, `gitops/tekton/rekor-public-key.yaml`) and digests signed under them. A rebuild that
  restores the key pair and the log's signer key keeps them; one with new keys has to replace the public keys in git
  and sign every pinned digest again (`tools/hub/cosign-image.sh`) before a device will pull it. Neither was rehearsed.
- **Object storage starts empty on a new hub.** The paired evaluation page is pinned to one pipeline run's records.
  Restoring them, or starting a run from staged inputs and re-pinning the page, was not rehearsed.
- **Every robot pulls its images from the public registry; there is no mirror.** With the uplink down a new clone,
  or a rollout of a new image, cannot finish. Several hub pods also install Python packages at every start
  (`dashboard`, `manifest-consumer`, `sync-agent`, `rejected-mirror`), Argo CD and Edge Manager read GitHub, and the
  promotion is a GitHub pull request. The running system is not self-contained.
- **An arm64 hub is outside what the Edge Manager chart declares** (x86_64). The images exist for arm64 and run;
  nothing here is a statement about support. The transparency log is a rebuild of the product's source, not the product.
- **Two images are local and unsigned:** `localhost/soarm-sim:arm64` and `localhost/fleet-renderer:arm64` are built
  on the host, not by the hub's pipeline. The sim's Dockerfile installs unpinned Python packages: every build is its own.
- **Enrolment was done in flywheel mode**, then the machine was switched. Enrolling a host already in tenants mode
  was never tried.
- **Not run here:** `./81-fleet-scale.sh guests-shutdown` (parallel guest shutdown with the host), the first-VM
  checks that need the debug key, `copy-vmedia.sh`, and whether a per-device `flightctl app stop` holds across a
  host reboot.
- **Not written down anywhere in the repository:** how the Dev Spaces workspace was created in its dashboard;
  whether the namespaces of the hand-made Secrets were created by hand before their applications or the applications
  were left waiting; the node-directory commands for the `hostPath` volumes other than object storage's.
- **Inherited:** object storage on a `hostPath` volume, with its root account also used by its clients; no
  authentication on the assistant's API, the wall or the metrics ports beyond the address they bind (the VM network
  and the tailnet reach them, the lab uplink does not).

## 6. Day-2 quick reference

The demo, its beats and "when something goes wrong" are in [`FURY-DEMO.md`](FURY-DEMO.md); every address is in
[`internal/FURY-URLS.md`](internal/FURY-URLS.md). Laptop commands need `KUBECONFIG`, a `flightctl` login and
`FURY_SSH`; host commands run from `~/flywheel-setup`.

| Want | Where | Command |
|---|---|---|
| Everything under GitOps healthy | laptop | `oc get applications.argoproj.io -n openshift-gitops` |
| 13 devices Online, UpToDate, Healthy | laptop | `flightctl get devices` |
| The fleet as Edge Manager sees it | laptop | `tools/hub/fleet-status.sh` |
| The worlds | laptop | `tools/hub/fleet-worlds-scale.sh status` |
| Robot zero: hub, host and wall | laptop | `tools/hub/robot-zero.sh status` |
| Dashboard names still fit the slices | host | `./75-tenant-metrics-install.sh slices` |
| Mode, units, the policy's placement | host (sudo) | `fury-mode status` |
| One tenant in detail | host (sudo) | `./72-training-tenant-install.sh status`, `./73-fleet-renderer-install.sh status`, `./74-robot-zero-install.sh status`, `./70-dcgm.sh status`, `./75-tenant-metrics-install.sh status`, `./64-assistant-expose.sh status` |
| The fleet's VMs | host (sudo) | `./81-fleet-scale.sh status` |
| The training tenant in a terminal | laptop | `tools/hub/training-watch.sh` (`--local` on the host, `--raw` for the untouched lines) |
| **Reset** the promotion, then re-open it | laptop | `tools/hub/reset-promotion.sh --yes`, then `tools/hub/reopen-promotion.sh --open` |
| **Reset** robot zero alone | laptop | `tools/hub/robot-zero.sh reset` (on the host: `fury-mode zero`) |
| **Reset** the coding task | the workspace | its `reset-demo` command |
| Robots that are off, back on | host (sudo) | `./81-fleet-scale.sh 12` |
| Worlds back to twelve | laptop | `tools/hub/fleet-worlds-scale.sh 12` |
| After a host reboot | host (sudo) | `fury-mode tenants`, then `./81-fleet-scale.sh 12` |
| Stale or bogus enrolment requests | laptop | `tools/hub/fleet-status.sh drop-pending` |
| Retire a robot for good | laptop, then host | `tools/hub/fleet-status.sh decommission <NN>`, then `./81-fleet-scale.sh remove <NN>` |
| Off-stage: the whole GPU for the flywheel, and back | laptop | `tools/hub/fury-switch.sh flywheel`, `tools/hub/fury-switch.sh tenants` |
