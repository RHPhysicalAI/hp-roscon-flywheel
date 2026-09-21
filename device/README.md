<!-- This project was developed with assistance from AI tools. -->
# Device plane — the devices Red Hat Edge Manager manages

The ACT policy runs on devices managed by Red Hat Edge Manager, never as a cluster workload. On the
Fury there are thirteen, of two kinds. What each kind receives - the signed policy and model, the
trust files, the rollout batches - is in the two Fleets, `gitops/rhem/`.

| Device | What it is | How it is made and enrolled |
|---|---|---|
| The GPU host | the RHEL 10.2 host itself, aarch64. The policy runs on the GPU (CDI); in tenants mode Edge Manager places it on MIG slice `0:1` through the device's `gpu_device` label - robot zero | `tools/host/fury/40-device-provision.sh` on the host, then the operator's steps in [`tools/host/fury/41-device-enroll.md`](../tools/host/fury/41-device-enroll.md) |
| Twelve robots | RHEL image mode micro-VMs on the same host, copy-on-write clones of one golden image. The policy runs on CPU; each robot pulls and verifies its own signed images | [`docs/internal/FLEET-VMS.md`](../docs/internal/FLEET-VMS.md): the design, and the bring-up runbook in its section 15 |

Labels are set at approval and read by the Fleet templates (`site`, `role`, `gpu`, `gpu_device`,
`policy_device`, `zenoh_router`, `zenoh_port`, `threads`); each Fleet file's header comment has the
table.

`device/spike/bench_cpu_forward.py` measures one ACT forward pass on CPU threads. It runs unchanged
on aarch64 and is the benchmark the fleet design names for sizing a robot's vCPUs.

`provision.sh`, `enroll.sh` and the scripts under `device/vm/` are from the development stand-in and
are not used on the Fury.
