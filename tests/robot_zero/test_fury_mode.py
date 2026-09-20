# This project was developed with assistance from AI tools.
"""tools/host/fury/fury-mode.sh with the host stubbed out: what a switch stops, what it must not stop, when it keeps the policy, how it ends on Ctrl-C."""

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "host" / "fury" / "fury-mode.sh"
SLICE_1 = "MIG-b0c96aad-8a73-5005-b745-26150350160e"
POLICY = "act-inference-128875-act-inference.service"

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")

NVIDIA_SMI = r'''#!/bin/bash
case "$*" in
*mig.mode.current*) echo "${STUB_MIG:-Enabled}" ;;
*--query-compute-apps*) cat "$STUB_DIR/apps" 2>/dev/null ;;
*-L*) echo "GPU 0: NVIDIA GB300 (UUID: GPU-5fd52ada-b5ca-524a-0b09-10f8b1d4fcef)"
      if [[ ${STUB_MIG:-Enabled} == Enabled ]]; then
          echo "  MIG 3g.126gb    Device  0: (UUID: MIG-923ec369-2618-51cd-bb2f-ad6c252f4ec5)"
          echo "  MIG 1g.31gb     Device  1: (UUID: MIG-b0c96aad-8a73-5005-b745-26150350160e)"
      fi ;;
esac
'''

SYSTEMCTL = r'''#!/bin/bash
echo "$*" >> "$STUB_DIR/calls"
case "$1" in
list-units) [[ -z ${STUB_POLICY:-} ]] || echo "act-inference-128875-act-inference.service loaded ${STUB_POLICY/\// } ACT policy" ;;
is-active)  if [[ $2 == so-arm-sim.service ]]; then echo "${STUB_SIM:-inactive}"; else echo inactive; fi ;;
cat)        exit 0 ;;
restart)    if [[ -n ${STUB_SLOW:-} && $2 == mig-config.service ]]; then sleep 20; fi ;;
esac
exit 0
'''

OTHERS = {"podman": "#!/bin/sh\nexit 1\n", "pgrep": "#!/bin/sh\nexit 1\n",
          "nvidia-ctk": "#!/bin/sh\nfor i in 0 1 2 3; do echo \"nvidia.com/gpu=0:$i\"; done\n"}


@pytest.fixture
def host(tmp_path):
    """A copy of fury-mode that does not ask for root and looks at a sandbox instead of /etc and /proc."""
    text = SCRIPT.read_text()
    elevate = '[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"\n'
    assert text.count(elevate) == 1
    text = (text.replace(elevate, "").replace("/etc/sysconfig/mig-config", f"{tmp_path}/mig-config")
            .replace("/etc/containers/systemd/", f"{tmp_path}/quadlets/").replace('"/proc/$p/cgroup"', f'"{tmp_path}/proc/$p/cgroup"'))
    (tmp_path / "fury-mode").write_text(text)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"nvidia-smi": NVIDIA_SMI, "systemctl": SYSTEMCTL, **OTHERS}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    (tmp_path / "quadlets" / "act-inference").mkdir(parents=True)

    def place(device: str) -> None:
        (tmp_path / "quadlets" / "act-inference" / POLICY.replace(".service", ".container")).write_text(
            f"[Container]\nAddDevice=nvidia.com/gpu={device}\n")

    def gpu_apps(apps: dict[int, str]) -> None:
        (tmp_path / "apps").write_text("".join(f"{pid}, python3\n" for pid in apps))
        for pid, cgroup in apps.items():
            (tmp_path / "proc" / str(pid)).mkdir(parents=True, exist_ok=True)
            (tmp_path / "proc" / str(pid) / "cgroup").write_text(f"0::{cgroup}\n")

    def env(**extra: str) -> dict[str, str]:
        return {"PATH": f"{bin_dir}:/usr/bin:/bin", "STUB_DIR": str(tmp_path), **extra}

    def run(verb: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(tmp_path / "fury-mode"), verb], capture_output=True, text=True, timeout=60,
                              check=False, env=env(**extra))

    def calls() -> list[str]:
        path = tmp_path / "calls"
        return path.read_text().splitlines() if path.exists() else []

    return run, calls, place, gpu_apps, env, tmp_path


def stops(calls: list[str]) -> set[str]:
    return {unit for line in calls if line.startswith("stop ") for unit in line.split()[1:]}


def test_tenants_to_tenants_keeps_the_policy_and_still_stops_the_recorder(host):
    """M1b: with the policy kept on slice 0:1 every loop unit is stopped - the recorder above all - except the two whose stop systemd would hand on to the policy."""
    run, calls, place, gpu_apps, _, _ = host
    place(SLICE_1)
    gpu_apps({4242: f"/system.slice/{POLICY}/libpod-payload-abc"})
    done = run("tenants", STUB_POLICY="active/running")
    assert done.returncode == 0, done.stderr
    assert "the RHEM-managed policy stays up on its slice" in done.stdout
    assert {"act-coordinator.service", "flywheel-runner.service", "act-inference.service", "robot-zero-sim.service",
            "robot-zero-emitter.service", "llm-assistant.service", "training-tenant.service", "fleet-renderer.service"} <= stops(calls())
    # PartOf=so-arm-sim.service on the policy, PartOf=fury-flywheel.target on the sim: a stop of either reaches the policy
    assert "so-arm-sim.service" not in stops(calls())
    assert not [line for line in calls() if "fury-flywheel.target" in line and not line.startswith("is-active")]
    assert "start --no-block robot-zero-sim.service robot-zero-frames.service robot-zero-episodes.service robot-zero-emitter.service" in calls()


def test_a_real_switch_stops_everything(host):
    """With the policy out of service the whole loop goes down, the sim and the target included."""
    run, calls, place, gpu_apps, _, _ = host
    place("all")
    gpu_apps({})
    done = run("tenants", STUB_POLICY="inactive/dead", STUB_MIG="Enabled")
    assert done.returncode == 0, done.stderr
    assert {"so-arm-sim.service", "act-coordinator.service", "flywheel-runner.service"} <= stops(calls())
    assert "disable --now fury-flywheel.target" in calls()


@pytest.mark.parametrize("device, sim, mode, why", [
    ("all", "inactive", "tenants", "no slice named"),                 # up, but not placed on a slice
    ("MIG-923ec369-2618-51cd-bb2f-ad6c252f4ec5", "inactive", "tenants", "another tenant's slice"),
    (SLICE_1, "active", "tenants", "the flywheel's sim is up under MIG: stopping it takes the policy along"),
    (SLICE_1, "activating", "tenants", "the same while it restarts"),
    (SLICE_1, "inactive", "flywheel", "MIG changes: nothing may hold the gpu"),
])
def test_a_policy_that_is_up_is_only_kept_on_its_own_slice_when_mig_stays(host, device, sim, mode, why):
    """Every other case is the old refusal, before anything is stopped, with the command that repairs it."""
    run, calls, place, gpu_apps, _, _ = host
    place(device)
    gpu_apps({})
    done = run(mode, STUB_POLICY="active/running", STUB_SIM=sim)
    assert done.returncode == 1, why
    assert "the RHEM-managed policy is up and holds the gpu" in done.stderr and "tools/hub/robot-zero.sh down" in done.stderr
    assert not stops(calls()), why


def test_two_policy_quadlets_are_never_a_reason_to_keep(host):
    """The agent mid-update: two application directories. Ambiguous placement fails closed."""
    run, calls, place, gpu_apps, _, tmp_path = host
    place(SLICE_1)
    other = tmp_path / "quadlets" / "act-inference-next"
    other.mkdir()
    (other / "act-inference-999999-act-inference.container").write_text(f"[Container]\nAddDevice=nvidia.com/gpu={SLICE_1}\n")
    gpu_apps({})
    done = run("tenants", STUB_POLICY="active/running")
    assert done.returncode == 1 and not stops(calls())
    assert "more than one policy quadlet" in run("status", STUB_POLICY="active/running").stdout


def test_a_foreign_gpu_client_still_refuses_when_the_policy_is_kept(host):
    """Only the policy's own processes are excused from the idle check."""
    run, _, place, gpu_apps, _, _ = host
    place(SLICE_1)
    gpu_apps({4242: f"/system.slice/{POLICY}/libpod-payload-abc", 5555: "/user.slice/user-1000.slice/session-9.scope"})
    done = run("tenants", STUB_POLICY="active/running")
    assert done.returncode == 1
    assert "the gpu is still in use" in done.stderr and "5555" in done.stderr and "4242" not in done.stderr


def test_zero_restarts_robot_zero_alone(host):
    """fury-mode zero touches the four robot-zero units and nothing else."""
    run, calls, place, _, _, _ = host
    place(SLICE_1)
    done = run("zero", STUB_POLICY="active/running")
    assert done.returncode == 0, done.stderr
    changed = [line for line in calls() if line.split()[0] in ("stop", "start", "restart", "disable", "enable")]
    assert changed == ["restart robot-zero-sim.service",
                       "start robot-zero-sim.service robot-zero-frames.service robot-zero-episodes.service robot-zero-emitter.service"]


def test_zero_refuses_in_flywheel_mode(host):
    """MIG off: nothing is restarted and the switch is named."""
    run, calls, _, _, _, _ = host
    done = run("zero", STUB_MIG="Disabled")
    assert done.returncode == 1 and "fury-switch.sh tenants" in done.stderr
    assert not [line for line in calls() if line.startswith("restart")]


def test_ctrl_c_leaves_the_way_the_exit_trap_would(host):
    """S8: an interrupt in the middle of a switch restarts telemetry, says where to look, and exits 130."""
    _, calls, place, gpu_apps, env, tmp_path = host
    place("all")
    gpu_apps({})
    proc = subprocess.Popen(["bash", str(tmp_path / "fury-mode"), "tenants"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True, env=env(STUB_POLICY="inactive/dead", STUB_SLOW="1"))
    deadline = time.monotonic() + 20
    while "restart mig-config.service" not in calls() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert "restart mig-config.service" in calls(), "the switch never reached the slow step"
    os.killpg(proc.pid, signal.SIGINT)      # what a terminal does: the whole foreground group
    _, err = proc.communicate(timeout=20)
    assert proc.returncode == 130
    assert "fury-mode: interrupted - check: fury-mode status" in err
    after = calls()[calls().index("restart mig-config.service") + 1:]
    assert after == ["start --no-block dcgm-exporter.service"], "telemetry once, and nothing else after the interrupt"
