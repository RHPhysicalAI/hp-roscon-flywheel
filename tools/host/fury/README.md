<!-- This project was developed with assistance from AI tools. -->
# Host scripts for one specific machine

These scripts prepared and operate **one particular HP ZGX Fury (NVIDIA DGX Station GB300, RHEL 10.2 aarch64)**
for the flywheel demo. They are kept as the record of what was done and why — `docs/internal/FURY-PLAN.md` is the
plan, `docs/internal/DECISIONS.md` (D139 onwards) the reasoning, `docs/FURY-SETUP.md` the lessons.

**Do not run them on anything else.** They re-run themselves under `sudo` and change the host: boot target and
suspend (`01`), format a disk and move container storage (`02`), install packages (`03`), partition the GPU with
MIG (`04`, `fury-mode.sh`, `21`), replace the host's resolver, libvirt networks and Tailscale routes (`06`), create
VMs (`30`, `32`). Each one refuses when its preconditions are wrong, but those checks were written for this host.

Numbering follows the plan's phases: `0x` host preparation, `1x` the arm64 flywheel pieces on the host, `2x`
rendering experiments, `3x` the OpenShift hub VM. `flywheel/` holds the systemd/quadlet units of the robot loop,
`sno/` the secret-free install templates, `mjwarp-spike/` a rendering spike. Addresses of the machine, its lab
network and the operator's tailnet are deliberately not recorded here.
