<!-- This project was developed with assistance from AI tools. -->
# Network access: what the machine needs, and how it is reached

The HP ZGX Fury stays in the partner's lab and never travels. The whole stack runs on it - the OpenShift hub, the
devices, the simulation - and every use of it, development and the conference demo alike, is remote. Two things
have to be arranged with the lab's network team: the machine's **outbound** access, and **one path in** for the
people who drive it. Nothing else: no public inbound, no per-service firewall rules, no DNS changes on the lab's side.

## 1. Outbound from the machine

All **TCP 443** unless noted. *Setup* is needed while the platform is built and staged; *running* is needed for as
long as the system does what it does today (every robot pulls its images from the registry, and a promotion is a
pull request on GitHub).

| Destination | Purpose | Needed |
|---|---|---|
| `quay.io` | container images: the platform's, the application's, the signed model images | setup + running |
| `registry.redhat.io`, `registry.access.redhat.com` | Red Hat operator, product and base images | setup |
| `cdn.redhat.com`, `subscription.rhsm.redhat.com` | RHEL entitlement and packages | setup |
| `charts.openshift.io` | Red Hat Edge Manager's chart | setup |
| `docker.io` (`registry-1.docker.io`) | a few upstream images | setup |
| `github.com`, `api.github.com`, `objects.githubusercontent.com` | source, release binaries, GitOps, the promotion pull request | setup + running |
| `huggingface.co`, `cdn-lfs.huggingface.co` | model weights and datasets (once, then cached) | setup |
| `pypi.org`, `files.pythonhosted.org`, `download.pytorch.org` | Python and PyTorch dependencies | setup |
| `rpm.flightctl.io` | the Edge Manager agent package | setup |
| `nvidia.github.io` | NVIDIA container toolkit | setup |
| `archive.ubuntu.com`, `packages.ros.org` | ROS build dependencies inside image builds | setup |
| an NTP server - **UDP 123** | clock sync (mandatory; any server) | always |

A wildcard per service (`*.redhat.io`, `*.huggingface.co`, `*.docker.io`, ...) is the robust way to allow these and
avoids a missed CDN subdomain. OpenShift may also try `console.redhat.com` / `cloud.redhat.com` for telemetry:
allow it or disable it, the lab's preference.

## 2. The one path in

| Option | How it works | Good for | Inbound rules |
|---|---|---|---|
| **A. A tailnet (Tailscale)** - what is used | the machine dials out (`login.tailscale.com` :443, DERP relays :443, UDP 41641); laptops on the same tailnet reach it from anywhere, a conference network included | development and the demo | **none** |
| B. Inbound SSH | TCP 22 to the machine from fixed source addresses | development only - a venue's address cannot be registered in advance | one source-restricted rule |
| C. The lab's VPN | connect to the VPN, then to the machine | development; the demo only if the venue allows the client | none public |

As built: a source-restricted SSH rule (option B) was used once, to install the tailnet client; everything since
runs over option A.

## 3. What rides inside that path

The machine is a **subnet router** on the tailnet: it advertises its internal VM network, `10.20.0.0/24`, and the
tailnet's **split DNS** sends the zone `sno-flywheel.local` to the machine's own resolver, which answers for the
cluster's names. So a laptop on the tailnet reaches every page by name, and every port below, with nothing opened
on the lab's firewall - it only ever sees the tunnel. Two approvals are made in the tailnet's admin console: the
advertised route, and the split-DNS entry (`tools/host/fury/06-network.sh` prepares the host side).

```
What the lab's firewall sees (one flow):
   the machine ──outbound 443 + UDP 41641──▶ the tailnet

What is reached inside it - none of it configured by the lab:
   laptop ─[tunnel]─▶ 10.20.0.10:443     every page: Edge Manager, OpenShift, OpenShift AI, Argo CD, Dev Spaces,
                                          the dashboards, the fleet wall, the object storage console
                     ▶ 10.20.0.10:6443    the OpenShift API
                     ▶ 10.20.0.10:30900   object storage (S3 API)
                     ▶ 10.20.0.10:30903   Kafka
                     ▶ 10.20.0.1:22       the host's shell
                     ▶ 10.20.0.1:8000, :9400, :9401, :9702   the assistant's API, GPU and tenant metrics, the wall
                     ▶ ...any port, and any service added later
```

The pages and their addresses are listed in [`internal/FURY-URLS.md`](internal/FURY-URLS.md). The routes use the
cluster's self-signed certificate: a browser accepts it once per hostname.

## 4. At the conference

- The machine is **not** at the booth. A presenter drives it over the tailnet from a laptop; the booth's network
  carries only that session. Everything the demo itself needs from the internet - image pulls, the promotion pull
  request - happens from the machine, in the lab.
- The system is left running; nothing is switched on stage ([`FURY-DEMO.md`](FURY-DEMO.md),
  [`FURY-DEMO-LITE.md`](FURY-DEMO-LITE.md)).
- **If the venue's network fails:** the recorded video of the demo plays from the laptop. A venue-network failure
  never ends the demo.

## 5. Checklist for a lab hosting such a machine

- The outbound list in section 1, NTP included.
- A tailnet client permitted on the machine - or VPN accounts for every presenter. Without one of the two, a
  conference demo is video-only.
- A one-time, source-restricted inbound SSH rule to bootstrap, or console access for the same purpose.
- A decision on OpenShift's telemetry: allowed, or disabled.
