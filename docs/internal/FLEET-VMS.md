<!-- This project was developed with assistance from AI tools. -->
# Fleet VMs — stage A of the fleet tenant (D163)

**Status 2026-09-20: designed and drafted, nothing run.** Every script passes `bash -n` and `shellcheck`; the hub
scripts were exercised against a local stand-in for `flightctl`, the host scripts only in their refusing paths
and in pieces (seed rendering, the `libvirt-guests` edit). No image has been built, no VM started, no request
approved, the Fleet is not live. **UNVERIFIED** marks what could not be checked without running something on the
host or the hub. `79-bootc-spike.sh` is with the operator; its result decides several of those marks. An
independent security and operability review (2026-09-20) has been applied; what it changed is marked *(review)*.

What stage A shows (`DECISIONS.md:5517-5519`): a fleet of RHEM-managed devices scaled up and down with one command
per machine, enrolled and approved with labels, a Fleet with a canary-then-batches rollout, signature
verification on every device. No sim yet. **The base image is RHEL image mode (bootc)** (D163 addendum,
`DECISIONS.md:5486-5515`).

| Piece | File |
|---|---|
| proof that image mode works on this host | `tools/host/fury/79-bootc-spike.sh up <key.pub> \| status \| remove` |
| the OS image | `tools/host/fury/fleet/` — `Containerfile`, `flightctl.repo`, `RPM-GPG-KEY-flightctl` |
| golden image | `tools/host/fury/80-fleet-golden.sh build \| status \| remove` |
| scale, the enrolment config's home, guest shutdown | `tools/host/fury/81-fleet-scale.sh <N> \| status \| destroy-all \| enrol-config <file> \| guests-shutdown` |
| enrolment config, made on the laptop | `tools/hub/fleet-enrol-config.sh [days]` |
| approval | `tools/hub/fleet-approve.sh [--watch \| --dry-run]` |
| fleet view, decommission, stale requests | `tools/hub/fleet-status.sh [decommission <NN>… \| decommission all --yes \| delete <NN>… \| drop-pending [<name>…]]` |
| the robots' Fleet, **not live** | `gitops/rhem/fleet-robots.yaml.draft` |

## 1. Golden image → copy-on-write clones

`80-fleet-golden.sh build`, three steps, no guest and no secret involved:

1. **An image store of its own** (`/data/libvirt/fleet/imagestore`): the two digests the Fleet draft names, pulled
   by podman natively. The host's `policy.json` is the Fleet's (`DECISIONS.md:5091-5093`), so both are signature-
   and Rekor-verified here — **and, for these two images, only here** (section 7). Kept between builds: it is what
   spares a rebuild the download.
2. **The OS image** `localhost/fleet-os`: `fleet/Containerfile` on `registry.redhat.io/rhel10/rhel-bootc:10.2`,
   following flightctl v1.3.0 `docs/user/building/building-images.md` for a virtual device — the agent from the
   project's repository (pinned, repository left disabled, no weak deps: as `40-device-provision.sh:132-137`;
   *(review)* repository file and signing key vendored and pinned, below), `podman skopeo cloud-init
   qemu-guest-agent firewalld`, cloud-init enabled the way that doc (`:404-406`) and Red Hat's
   image-mode guide do it, the agent enabled and ordered after cloud-init (section 4), RHEL's automatic image
   updates masked (the doc asks for it, `:138`), the image store copied in (section 7), `bootc container lint`.
   *(review)* The build **fails** if the image holds anything per-device — a non-empty machine-id, an ssh host
   key, an agent key, an enrolment config: every clone is a copy of what the build leaves behind.
3. **The disk**: `registry.redhat.io/rhel10/bootc-image-builder:10.2 --type qcow2`, run as the image-mode guide
   runs it (rootful, `--privileged`, `label=type:unconfined_t`) — *(review)* except for **which store it is
   handed**. The documented form mounts the host's whole rootful store read-write into a privileged, unconfined
   container; on a machine other people build on, that is reach into everybody's images. `80` builds the OS image
   in a store of its own (`/data/libvirt/fleet/buildstore`) and mounts only that: read-only first, and — since
   the builder mounts the image, which writes under the store — most likely read-write on the second attempt,
   on a store that holds nothing but this script's base and OS image. (`79`, which had already run, used the
   documented form.) Root filesystem 20 GiB. Result:
   `fleet-golden.qcow2`, mode 0440. Clones are `qemu-img create -b` overlays; `remove` refuses while one exists,
   `status` warns if the image is newer than a clone.

Both repositories, the tag `10.2` and arm64 builds (newest 2026-09-17) were confirmed in the public Red Hat
catalog API. **No activation key**: the host carries `/usr/share/containers/mounts.conf` →
`/usr/share/rhel/secrets` → `/etc/pki/entitlement`, with a certificate in it (read-only check, file names only),
so a rootful build is entitled through the host — which is what the flightctl doc requires (`:161`, "a registered
RHEL build host"). The registry login is the operator's pull secret, passed to podman by path
(`32-sno-install.sh:12`); no script reads it. VM shape as the hub's:
UEFI, `virt`, `host-passthrough`, `--memballoon none`, `--numatune 0` (`32-sno-install.sh:77-92`).
**UNVERIFIED** until the spike reports: the pull secret's entitlement to the two repositories; the builder on a
64k-page aarch64 kernel with SELinux enforcing; the builder's output file names. **UNVERIFIED** and new: the
builder working from a dedicated store (`podman --root`), read-only or read-write.

*(review)* **What signs the agent package.** The Containerfile no longer fetches the repository definition at
build time. `flightctl.repo` is the project's file as served on 2026-09-20 with two lines changed (`gpgkey` → the
vendored file, `enabled=0`); the repository **is** signed upstream (`gpgcheck=1` as served). The key is Fedora
COPR's for the project's builds: fingerprint `AB7A FBCB AAB5 B4EA 1554 2BBA 2AC4 5CAD 374E BF59`, RSA 2048,
created 2024-02-07, user id `@redhat-et_flightctl`. Obtained over HTTPS from the URL the project's own repo file
names, fingerprint computed from the key packet, and cross-checked against this host: the
`flightctl-agent-1.3.0-1.el10` already installed there carries a signature by key id `2ac45cad374ebf59` — the
same key. The build pins it three ways: the key file's sha256, the key rpm then holds
(`gpg-pubkey-374ebf59-65c39893`), and the key id on both installed packages. `80` and `81` take one `flock`, so
two people on this host cannot run them into each other.

## 2. Sizing

Proposal: **2 vCPU / 3 GiB / 20 GiB thin disk** per VM, 24 VMs as the ceiling to aim at.

- CPU: the policy's budget is p95 < 1000 ms per forward pass; 83 ms on 8 x86 P-core threads, 184 ms inside an
  8-vCPU guest (`docs/eval-records/cpu-spike.md:13`, `:100`). Two Grace vCPUs are **UNVERIFIED** —
  `device/spike/bench_cpu_forward.py` runs unchanged on aarch64 (D037); run it in the first VM. If 2 is too few:
  `81-fleet-scale.sh <N> 4` and `THREADS=4 fleet-approve.sh`. **Open until that benchmark (operator).**
- Memory: no measurement of the container's resident size exists in the repo; 3 GiB is a guess with room,
  D163's "about 2 GiB" the floor to try. No balloon, so what is given is held.
- Totals at 24: 48 vCPU + the hub's 32 on 72 cores (1.1 : 1), 72 GiB of the 309 GiB available with the hub up
  (2026-09-20). The script keeps 32 GiB back and refuses beyond 32 VMs.
- **The fleet's size is bounded by host CPU, not by the renderer**: the rendering tenant does about 228 camera
  pairs a second per 1g.31gb slice whatever the batch size (`DECISIONS.md:5507-5508`) — 7 robots at 30 fps, about
  20 at 480×480 and 15 fps.
- Disk: the golden image is about 7 GB (OS + 4.5 GiB of embedded images); a clone starts at a few hundred MB.

## 3. Names, addresses, DNS

`fleet-vm-NN`, NN = 01…32 → `10.20.0.(20+NN)`, MAC `52:54:00:14:01:<NN hex>` (`FURY-PLAN.md:79`; the hub is
`…:00:0a`, `32-sno-install.sh:21`). `fury-net` has **no DHCP and no libvirt DNS** (`fury-net.xml`): address,
gateway and resolver `10.20.0.1` come from the clone's cloud-init `network-config`, matched by MAC. The host's
dnsmasq already answers the hub's names to guests (`dnsmasq-fury.conf`, `06-network.sh:34-38`). The spike holds
`10.20.0.52` (fleet-vm-32's) until it is removed.

## 4. Identity per clone

Image mode does most of it: machine-id and ssh host keys are made on first boot, the image has none. The agent's
key pair is made when it first starts. Its unit is only `After=network.target` (v1.3.0
`packaging/systemd/flightctl-agent.service`), so without help it starts before cloud-init has written its config,
exits on the missing file and retries until it appears; a drop-in in the image orders it `After=cloud-final.service`
instead. cloud-init switches itself off after that boot (`/etc/cloud/cloud-init.disabled`), which also makes the
ordering inert. cloud-init is not in the bootc base — the Containerfile installs it. `81-fleet-scale.sh`'s seed
is as designed before, less the line that started the agent. *(review)* This is **not a hardware-identity
story**: the VMs have no TPM (`--tpm none`), so a device's management key is a file on a disk that is `root:qemu
0660` on a shared host.

An optional `/root/fleet/debug.pub` adds a login (`fleet`, passwordless sudo) to new clones, for the benchmark.
The key is public; *(review)* what it opens is not small: whoever holds its private half is root on those clones,
and a clone's root can read the fleet's enrolment config (section 5). Remove the file after step 10, so later
clones get no login.

## 5. Enrolment: late binding, and where the secret lives

flightctl names two ways (`building-images.md:54-60`). **Early binding** bakes `config.yaml` into the image:
devices share one "typically long-lived" certificate. **Late binding** injects it at provisioning — and for
virtual devices the doc itself says to use it, through cloud-init (`:389`, `:465`). Late binding here: a
certificate baked into a qcow2 on a shared partner machine would be a secret at rest with an expiry date.

Every place the key exists, in order *(review: the first version of this list left three out)*:

1. **The laptop**, seconds: a 0700 temp directory, files removed by name on exit — overwritten first where
   `shred` exists, plainly unlinked on a Mac (no `shred`; overwriting in place means nothing on APFS). 14 days'
   validity (**decided**).
2. **The host's shared login account**, `~/flywheel-setup/fleet-agent-config.yaml`: created 0600 from its first
   byte (`umask 077` + `cat` over ssh, not `scp -p`), the mode printed back. The account is shared, so the mode
   keeps other *accounts* out, not the other people who log in as the same one. **Steps 5 and 6 are run back to
   back; if 6 slips, shred the file and issue a new certificate later.** Never copy it into a git checkout.
3. **`/root/fleet/agent-config.yaml`**, 0600 in a 0700 directory (precedent `/root/sno-install`,
   `32-sno-install.sh:10-12`), for as long as the fleet may grow. `enrol-config` first checks that its server
   resolves to `10.20.0.10` (`40-device-provision.sh:62-68`), then shreds hop 2.
4. **A clone's seed ISO**, `root:qemu 0640` in a `0750` directory, base64 in `user-data` — and, while it is
   attached, readable inside the guest as `/dev/sr0`. `81` waits for the clone to answer on its address (cloud-init
   reads the whole seed in one go, before the network is up), then **ejects it for now and for later boots
   (`change-media --eject --config --live`) and shreds the file**; a clone that was slow is caught by the next run,
   and `status` shows `seed:ATTACHED` until then. Later boots do not miss it: cloud-init is off by then.
5. **cloud-init's own copies in the clone** — `user-data.txt`, `user-data.txt.i`, `cloud-config.txt`, `obj.pkl`
   under `/var/lib/cloud/instance`, and `/run/cloud-init/instance-data-sensitive.json`: shredded by the seed's
   `runcmd` once the config is installed. **UNVERIFIED** that this list is complete on RHEL 10's cloud-init —
   on VM 01: `sudo grep -rl client-key-data /var/lib/cloud /run/cloud-init` must print nothing.
6. **`/etc/flightctl/config.yaml` in every clone**, 0600, for the clone's life — as on any flightctl device. The
   Fleet's two containers are rootful with host networking, so a compromise of either reaches it: what a robot
   holds is the *fleet's* enrolment credential until it expires, not only its own identity.

It is worth the right to *ask* for enrolment; approval is the gate (section 6).

## 6. Approval: recognising a fleet VM

Approved only if: the agent proposes `enrol=fleet-vm` (a `default-labels` drop-in in the image, v1.3.0
`internal/agent/config/config.go:145-146`, `:514`, `:592`) *(review)* **and nothing else** — except
`alias=<its own hostname>`, which the 1.3 agent adds by itself (`config.go:154`; `41-device-enroll.md:93`), so
"exactly one label" would have refused every real clone. Whether `approve -l` merges or replaces what a requester
proposes is unverified, so a request proposing `pull_default`, `role`, `site`, `zenoh_router` or anything else is
refused outright; hostname `fleet-vm-NN`; default address and MAC
exactly what `81-fleet-scale.sh` gives VM NN; arm64; no live device holds `alias=fleet-vm-NN`; no second pending
request claims NN; *(review)* made within `MAX_AGE_MIN` (30) minutes — a missing or unreadable timestamp counts
as too old; and, when `FLEET_EXPECT="1-8"` is set, a number the host was asked to create. `--watch` stops after
`WATCH_MINUTES` (30) and says so when `FLEET_EXPECT` is unset. Everything else is left pending and printed,
never denied — so stale requests stay: `fleet-status.sh drop-pending` lists them with full names,
`drop-pending <name>…` deletes them, and `fleet-approve.sh` warns when the hub cuts the list short. A jq error
now stops the pass instead of reading as "nothing to approve". All of it is self-reported
(OpenAPI `:6948-6962`): a consistency check. Authentication is the enrolment certificate; and approval hands over
nothing secret — public keys, a policy file, two public signed images. *(review)* That is true of the Fleet
**today**; it is also the delivery channel for whatever the Fleet carries later (the mirror's CA, an OS image,
remote console), which is why the loop is bounded in time and in numbers. **UNVERIFIED:** live systemInfo values; a
wrong field name fails closed. `--dry-run` first.

Labels: `fleet=robots site=fury gpu=none policy_device=cpu arch=arm64 alias=fleet-vm-NN robot=NN threads=2`,
`role=canary` on the lowest-numbered VM approved while the fleet has none (the count is re-read right before
one is appointed). No `pull_default` (D150).

## 7. Images: no 4 GB per device, and the fallback

The CPU branch needs the runtime image (`fleet-act-inference.yaml:236`; one 4.2 GiB layer, `DECISIONS.md:5066`) and
the modelcar (`:224`), which the agent pre-pulls with `podman pull` (`:65-71`). **Chosen: both embedded in the OS
image, in an additional read-only image store** at `/usr/lib/containers/storage`, listed in the image's
`storage.conf` (`additionalimagestores` is empty by default on RHEL 10.2 — checked). Why not logically bound
images: they live in `/usr/lib/bootc/storage`, podman sees them only per command, both docs say not to enable that
store globally, and the agent pulls with podman's defaults. What the choice is: "physically bound" images are
**not a finished bootc feature** (bootc issue 644, open). podman cannot make an overlay store inside a build
container and a vfs store is unreadable by overlay, so the store is filled on the host and copied in with `cp -a`;
`80` refuses images with whiteouts, which such a copy would lose (neither has one: the runtime image is a single
layer).

*(review)* **What this costs, stated plainly.** `policy.json` is applied when an image is *pulled*. An image podman
finds in the embedded store is run **with no signature check on the device**. For these two digests the check
happens exactly once — on the host, under the same `policy.json`, when `80` pulls them to embed them — and the
trust anchor for them moves from each device to the golden-image build. An earlier version of this section and of
the Fleet draft said each device "still verifies both"; that was wrong. Everything else a robot is ever told to
run — a promoted modelcar, the negative tests — is pulled, and verified on the device, as before. (On the mirror
fallback below, devices pull and therefore verify all of it again.)

**UNVERIFIED:** (a) that the agent's `podman pull` of a digest present in a read-only store skips the
download; (b) the store's SELinux label — the policy has rules for `/var/lib/containers/storage/overlay*` only, so
the image adds an equivalence line; (c) the list-digest reference resolving from the embedded store.

**Which case am I in — one command, first VM:** `./81-fleet-scale.sh status`. A clone's own disk is the witness:
a few hundred MB if the store was used; over 3 GB the line ends `<- PULLED its images`.

**Fallback if it pulled: a mirror on the hub**, the simplest local source here — the internal registry is up with
a route and unused (`FURY-PLAN.md:138`); a pull-through cache is not set up. One more inline file in the Fleet,
`/etc/containers/registries.conf.d/50-fleet-mirror.conf`, names
`default-route-openshift-image-registry.apps.sno-flywheel.local/fleet-mirror` as a mirror of `quay.io/jary`.
**Image references: unchanged. `policy.json`: unchanged** — the identity stays `quay.io/jary/…`, so the existing
signatures still match `matchRepository`. **`registries.d`: one more entry** enabling sigstore attachments for the
mirror's host. Also: the ingress CA under `/etc/containers/certs.d/`, anonymous pull on that one namespace, and
both images copied there *with* their signature attachments — the same open question as Phase 8b's spike
(`FURY-PLAN.md:416`). Re-signing under the route name (8b's plan) would change all three instead. **UNVERIFIED:**
that attachments are looked up at the mirror. Cost: 4.5 GiB per robot across the bridge and on its disk, no uplink.

**A later model rollout:** the new modelcar only, small, N times from quay. A new *runtime* image is 4.2 GiB × N
until it is built with `--layers` or mirrored: rebuild the golden image instead.

## 8. 4k guest on the 64k host

The bootc base boots a 4k-page kernel; the host runs 64k. D146 closed the question for the hub's guest
(`DECISIONS.md:4850-4851`, `FURY-PLAN.md:446`; balloon left out, `32-sno-install.sh:77`). Red Hat's notes want
matching page sizes (`FURY-PLAN.md:454`); the image-mode guide lists ARMv8.0-A as supported and says nothing about
page size. New here is the *builder* (mkfs, loop devices) on a 64k host — the spike prints both page sizes.

## 9. SELinux, firewall, ports

Guests enforcing. The agent wrote `policy.json` on the host without denials (`DECISIONS.md:5109`); a bootc guest is
**UNVERIFIED** — first VM: `ls -Zd /usr/lib/containers/storage/overlay`, `ausearch -m avc -ts boot`. Host
firewall: `libvirt-to-host` rejects what it does not list (`15-camera-port.sh:6-7`). **No new host port in stage
A**: DNS is open (`06-network.sh:36`), the hub is a neighbour on the bridge, quay goes out through the masquerade
(`31-sno-hostprep.sh:34`), NTP is public (`:4`). **Stage C:** each robot's computer runs its own Zenoh router
(decided); the robot's world pod connects to it **as a client** at `10.20.0.(20+NN):7447` — pod to guest, so the
hub needs no NodePort per robot and the host no port.

*(review)* **That router is unauthenticated.** `rmw_zenohd` listens on every address with no authentication and no
TLS, and the unit uses host networking: "every robot has its own graph" is address separation, not isolation.
Whoever reaches a robot's 7447 can publish into its graph — inject actions. Without a filter that is every other
clone, the hub, the host, and **every peer on the operator's tailnet**, because the host routes `10.20.0.0/24` for
it (`15-camera-port.sh:10-11`). Accepted for a demo, and narrowed: the image enables firewalld with `ssh` open and
7447 open **from the hub's address only** (`10.20.0.10`, where a world pod's traffic arrives from) — not from the
subnet, since tailnet traffic is source-NATed to the host's `10.20.0.1` by tailscale's default. The policy reaches
its router over loopback, which firewalld does not filter. The rule is in the image, not the Fleet: a zone file
delivered later needs a reload, and it does not vary per device. **UNVERIFIED** on a bootc guest; if ssh or 7447
misbehave on VM 01: `sudo firewall-cmd --list-all`.

## 10. Boot time

D163 expects about half a minute; the spike measures it (virt-install to ssh). A pending request about a minute
after `virt-install`, `Healthy` a few minutes after approval (start period 240 s, `fleet-act-inference.yaml:243`).

## 11. Scale-down, and shutting the host down

flightctl 1.3 has `decommission device/NAME` (only target `Unenroll`; local CLI help) and `delete`. The agent wipes
its management certificate on request; lifecycle `Decommissioning` → `Decommissioned`; delete only then (v1.3.0
`managing-devices.md`). The host holds no hub credential (`DECISIONS.md:5033`), so: laptop
`fleet-status.sh decommission 24 23 …` (`all` needs `--yes`; without it, it lists and stops), then host
`81-fleet-scale.sh 22`. `81` removes only domains whose one disk is under its own pool — a same-named VM of
somebody else's stops the run before anything changes. A VM that went first:
`fleet-status.sh delete NN`, refused while `Online`. Highest number first, the canary last. **UNVERIFIED:**
whether deleting a device removes its enrolment request.

`81-fleet-scale.sh guests-shutdown` **(parallel shutdown: decided)**: `libvirt-guests` stops guests one after
another, up to 300 s each (`32-sno-install.sh:113`). It sets `PARALLEL_SHUTDOWN=40`, shows before and after, keeps
a timestamped backup, guards against a file with no trailing newline, restores the SELinux label, prints the
file's last lines, and does not restart the unit. **Host-wide — it also governs the hub VM.** In parallel mode `SHUTDOWN_TIMEOUT` becomes one
countdown for all guests (the host's `/usr/libexec/libvirt-guests.sh`), so 40 exceeds any guest count: all are asked
in the first second and the hub keeps its full 300 s.

## 12. Failure modes

| What | How it shows | Repair |
|---|---|---|
| pull secret not entitled to the bootc repositories | `unauthorized` in the spike or the build | `sudo podman login registry.redhat.io` with an account that is; run again |
| `dnf` finds no repositories in the build | build fails at the first `RUN` | `sudo subscription-manager status`; the spike's `RESULT` line says early |
| an embedded image has whiteouts | `80` stops before building and lists them | the mirror fallback (section 7) |
| builder fails on this host | the spike, or `80`, stops with the builder's output | that output is the finding; `remove` clears up |
| clones pull 4 GB anyway | `status`: `<- PULLED its images` | works, slowly; then the mirror fallback |
| golden image rebuilt under clones | clones corrupt | `status` warns; `destroy-all`, `remove`, `build` |
| Fleet digests changed after the build | clones pull the new image | rebuild, or accept it (small for a modelcar) |
| enrolment certificate expired | `81` refuses to create VMs | `fleet-enrol-config.sh`, then `enrol-config` |
| the agent's signing key changed, or the vendored key file was altered | build fails at the key or signature check | stop and find out why before trusting a new key; never loosen the check to get a build |
| something per-device ended up in the image | build fails at the identity assertions | the message names the check; fix the Containerfile step that ran a service or generated keys |
| the builder cannot use a dedicated store at all | `80` fails read-only **and** read-write | keep the output; the documented form (the host's main store) is the known-working fallback — a deliberate decision, not a quiet edit |
| MAC or address already somebody's (the spike holds fleet-vm-32's pair, even shut off) | `81` refuses before creating anything and says whose | `79-bootc-spike.sh remove`; `ip neigh show <addr>` |
| a VM named `fleet-vm-NN` that is not this script's | `81` stops before anything changes and shows its disk | rename or remove that VM |
| `/data` short of space | `81` refuses: needs the 100 GB floor plus 6 GB per new clone; prints the worst case (N × 20 GiB) | free space, or fewer VMs |
| two people run `80`/`81` at once | the second stops: "another run is in progress" | wait; `sudo fuser -v /run/fleet-stage-a.lock` |
| create fails part-way | the message lists the VMs already up and says to re-run | the same command continues from the failed number |
| a clone never answers, so its seed stays attached | `status`: `seed:ATTACHED` | `virsh console`; fix or remove the VM — the next `81` run ejects what is left |
| half-removed VM | scale stops and names its files | remove them by hand (the message gives the commands) |
| a request proposes its own labels | `PROPOSES-ITS-OWN-LABELS`, printed even under `--watch` | not a clone of this image: find out who; `fleet-status.sh drop-pending <name>` |
| a real clone is "too old" or "not expected" | left pending with that reason | `MAX_AGE_MIN=…` or the right `FLEET_EXPECT` for one run |
| the hub cuts the request list short | `WARNING: the hub cut the request list short` | `fleet-status.sh drop-pending`, delete the stale ones |
| duplicate claim on a number | both printed, neither approved | find the stray VM; `delete` a dead device |
| VM removed before decommission | device stays disconnected | `fleet-status.sh delete NN` |
| host reboot | fleet VMs do not autostart | `81-fleet-scale.sh <N>` starts the existing ones |

## 13. Decided, and still open

Decided by the operator 2026-09-20: a Zenoh router on each device (section 9); clones stay unregistered, only the
build touches the host's subscription; 14-day enrolment certificate; parallel guest shutdown. **Open:** 2 vs 4
vCPUs, after the first VM's benchmark. **Not this design's call:** when the Fleet goes live (a `git mv` to `.yaml`
by the main session).

## 14. What image mode adds, and costs

Adds: an **OS update rolled out by RHEM** as a later beat — build `fleet-os` v2, push it where devices can pull
(the hub's registry, plus its trust entry in `policy.json`), set `spec.template.spec.os.image` in the Fleet; same
canary and batches, each device stages it, reboots, and bootc rolls back a failed boot. The disruption budget then
limits reboots. It also ends the agent's `/sysroot` log noise (`DECISIONS.md:5097`).
Costs: a 7 GB image and a 20–30 min first build (about 10 after); an OS update re-ships the embedded 4.5 GiB to
every robot unless that layer comes out byte-identical (issue 644's "timestamp churn"); physically bound images
and their SELinux label are unproven here; nothing known-bad for aarch64 or a 64k host was found in either doc.

## 15. Bring-up runbook

| # | Who, where | Command | Expect | Time |
|---|---|---|---|---|
| 0 | operator, host | `./79-bootc-spike.sh up <key.pub>`, paste; then `remove` | `RESULT` lines: repositories visible, ssh after N s, guest page size 4096, `bootc status` | 15 min |
| 1 | operator, host | copy `80-`, `81-fleet-*.sh`, the whole directory `fleet/` (three files), and `gitops/rhem/fleet-robots.yaml.draft` as `fleet-robots.yaml` to `~/flywheel-setup`; check the scripts arrived executable (`ls -l`) | — | 1 min |
| 2 | operator, host | `./80-fleet-golden.sh build` — paste the output | key and signature checks pass, `OS image built …`, whether the builder managed read-only or needed read-write, `golden image ready` | 20–30 min |
| 3 | operator, host | `./81-fleet-scale.sh guests-shutdown` | before: unset; a backup file; after: `PARALLEL_SHUTDOWN 40`, timeout unchanged, the file's last lines | 1 min |
| 4 | main session, laptop | make the Fleet live (`git mv`, commit, push); `tools/hub/fleet-status.sh` | a line starting `Fleet robots:` instead of "does not exist on the hub yet" | 5 min |
| 5 | laptop | `FURY_SSH=… tools/hub/fleet-enrol-config.sh` — **only when step 6 can follow at once** | `600 …` for the file, then "NOW, on the host" | 1 min |
| 6 | operator, host, **back to back with 5** | `./81-fleet-scale.sh enrol-config fleet-agent-config.yaml`. If it cannot happen now: `shred -u ~/flywheel-setup/fleet-agent-config.yaml` and redo 5 later. **The enrolment config is never copied into the git checkout**, on the laptop or the host. Optional: an ssh public key as `/root/fleet/debug.pub` | `installed … shredded …`, an expiry date | 1 min |
| 7 | operator, host | `./81-fleet-scale.sh 1` | the disk line (free space, worst case), `fleet-vm-01: seed ejected … and shredded`, `fleet-vm-01 running 10.20.0.21 ping:yes seed:gone` | 3 min |
| 8 | laptop | `FLEET_EXPECT=1 tools/hub/fleet-approve.sh --dry-run`, then without `--dry-run` | `would approve fleet-vm-01 … role=canary`, then `approved … (the canary)`. If it says `PROPOSES-ITS-OWN-LABELS` for a real clone, the agent proposes more than `enrol` and `alias` — paste it, do not work around it | 2 min |
| 9 | laptop, then host | `fleet-status.sh` until healthy; then **`./81-fleet-scale.sh status`** | `act-inference:Running`; disk a few hundred MB — or `<- PULLED its images` (section 7) | 5–10 min |
| 10 | operator, first VM | with the debug key: the two SELinux checks of section 9; `sudo podman images --digests`; `sudo grep -rl client-key-data /var/lib/cloud /run/cloud-init` (section 5); `sudo firewall-cmd --list-all`; `ls -la /etc/flightctl/certs`; `/usr/libexec/podman/quadlet -dryrun`; the CPU benchmark and the container's memory (section 2) | both images listed; no denials; the grep prints nothing; 7447 only from `10.20.0.10`; no key under `/etc/flightctl/certs`; p95 under 1000 ms | 15 min |
| 10b | operator, host + VMs 01 and 02 | **before scaling past 2:** `./81-fleet-scale.sh 2`, approve 02, then on **both** VMs: `cat /etc/machine-id`; `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`; `sudo openssl x509 -noout -fingerprint -in /var/lib/flightctl/certs/agent.crt` | all three **differ** between the two VMs. If any is the same, stop: every clone would share it. Then `sudo rm -f /root/fleet/debug.pub` | 10 min |
| 11 | laptop, then host | `FLEET_EXPECT="1-8" fleet-approve.sh --watch` (then `"1-16"`, `"1-24"`; it stops by itself after 30 min); `./81-fleet-scale.sh 8`, then `16`, then `24`, watching `free -g`, `df -h /data` and the hub | one `approved` line per VM; every VM `seed:gone` | 10 min a step |
| 12 | laptop | the rollout beat: edit digest + `MODEL_VERSION` in the Fleet, merge; `fleet-status.sh` | RENDERED moves: canary, 25 %, 50 %, the rest | per rollout |
| 13 | laptop, then host | `fleet-status.sh decommission 24 23 …`, then `./81-fleet-scale.sh 22` | `device … deleted`, then `removed fleet-vm-24` | 3 min |
| — | laptop, any time | `fleet-status.sh drop-pending` | the pending requests with full names; delete stale ones by name | 1 min |

At demo time the operator's path is steps 11–13 only: wrapped commands, no raw `flightctl`, `oc` or `virsh`.
The agent's certificate path in step 10b is the one the host's own script relies on with this agent version
(`40-device-provision.sh:32`).
