# This project was developed with assistance from AI tools.
"""tools/hub/robot-zero.sh against stand-ins for flightctl and ssh: the order of stop, label and start is the point."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "hub" / "robot-zero.sh"
UUID = "MIG-b0c96aad-8a73-5005-b745-26150350160e"

pytestmark = pytest.mark.skipif(not (shutil.which("jq") and shutil.which("bash")), reason="needs bash and jq")

# A hub in a file: the device's label, the application's state, and whether a label change gets rendered. It keeps
# a log of every call that changes something, in order. Like the real one it replaces the WHOLE label map with what
# it is sent, honours resourceVersion as a lock, and refuses a changed spec - so a careless write shows. It fails
# the apply outright when the document carries what only the hub may write.
FLIGHTCTL = r'''#!/usr/bin/env python3
import json, os, sys
state_file = os.environ["STUB_STATE"]
state = json.load(open(state_file))
args = sys.argv[1:]
def save():
    json.dump(state, open(state_file, "w"))
def log(line):
    open(os.environ["STUB_LOG"], "a").write(line + "\n")
def device():
    labels = dict(state.get("labels") or {"alias": "fury-host", "fleet": "act-inference", "gpu": "nvidia",
                                          "pull_default": "insecureAcceptAnything", "site": "fury"})
    if state["label"]:
        labels["gpu_device"] = state["label"]
    quadlet = "[Container]\nImage=x\nAddDevice=nvidia.com/gpu=%s\nHealthCmd=/healthcheck.sh\n" % state["rendered"]
    return {"apiVersion": "flightctl.io/v1beta1", "kind": "Device",
            "metadata": {"name": "dev1", "labels": labels, "owner": "Fleet/act-inference", "generation": 3,
                         "resourceVersion": str(state.get("resource_version", 41)),
                         "creationTimestamp": "2026-09-19T18:40:00Z",
                         "annotations": {"device-controller/renderedVersion": str(state["hub_version"]),
                                         "device-controller/applicationLifecycle": "{}"}},
            "spec": {"applications": [{"name": "act-inference", "inline": [
                {"path": "models.volume", "content": "[Volume]\n"},
                {"path": "act-inference.container", "content": quadlet}]}]},
            "status": {"applications": [{"name": "act-inference", "status": state["app"]}],
                       "applicationsSummary": {"status": "Healthy"},
                       "updated": {"status": "UpToDate"},
                       "config": {"renderedVersion": str(state["agent_version"])}}}
if args[:2] == ["get", "devices"]:
    print(json.dumps({"items": [device()]}))
elif args[:2] == ["get", "device/dev1"]:
    # the agent catches up with the hub one read after a render, so the script has to wait for it
    doc = device()
    if state["agent_version"] != state["hub_version"]:
        state["agent_version"] = state["hub_version"]
        save()
    print(json.dumps(doc))
elif args[0] == "apply":
    if state.get("refuse_apply"):
        log("apply refused while app " + state["app"])
        sys.exit("Error: 409 the object has been modified; please apply your changes to the latest version")
    sent = json.load(open(args[args.index("-f") + 1]))
    now = device()
    problems = []
    if "status" in sent:
        problems.append("the status was sent back")
    for managed in ("annotations", "generation", "owner", "creationTimestamp"):
        if managed in sent["metadata"]:
            problems.append("metadata." + managed + " was sent back: only the hub writes it")
    if sent["metadata"].get("resourceVersion") != now["metadata"]["resourceVersion"]:
        problems.append("resourceVersion was not sent back: the write is not locked against a concurrent change")
    if sent.get("spec") != now["spec"]:
        problems.append("the spec is not what the hub returned: a Fleet's device refuses that")
    for keep in ("alias", "fleet", "pull_default", "gpu", "site"):
        if sent["metadata"]["labels"].get(keep) != now["metadata"]["labels"].get(keep):
            problems.append("label " + keep + " was changed or dropped")
    if sent["metadata"]["name"] != "dev1" or sent.get("kind") != "Device":
        problems.append("not this device")
    if problems:
        log("apply REJECTED BY STUB: " + "; ".join(problems))
        sys.exit("stub hub: " + "; ".join(problems))
    labels = dict(sent["metadata"]["labels"])
    state["label"] = labels.pop("gpu_device", "")
    if state.get("hub_drops"):
        labels.pop(state["hub_drops"], None)
    state["labels"] = labels
    state["resource_version"] = state.get("resource_version", 41) + 1
    log("label " + (state["label"] or "(removed)") + " while app " + state["app"])
    if state["renders"]:
        state["rendered"] = state["label"] or "all"
        state["hub_version"] += 1
    save()
elif args[0] == "app":
    log("app " + args[1] + " with quadlet " + state["rendered"])
    state["app"] = {"stop": "Stopped", "start": "Running"}[args[1]]
    save()
else:
    sys.exit("stub flightctl: unexpected " + " ".join(args))
'''

SSH = r'''#!/usr/bin/env bash
# the host: tenants mode unless STUB_MIG=off, robot zero's world running unless STUB_SIM says otherwise
case "$*" in
*"nvidia-smi -L"*)
    echo "GPU 0: NVIDIA GB300 (UUID: GPU-5fd52ada-b5ca-524a-0b09-10f8b1d4fcef)"
    if [[ ${STUB_MIG:-on} == on ]]; then
        echo "  MIG 3g.126gb    Device  0: (UUID: MIG-923ec369-2618-51cd-bb2f-ad6c252f4ec5)"
        echo "  MIG 1g.31gb     Device  1: (UUID: MIG-b0c96aad-8a73-5005-b745-26150350160e)"
        echo "  MIG 1g.31gb     Device  2: (UUID: MIG-7965c8a8-0130-5c6a-858a-b1c229f3587c)"
    fi ;;
*"is-active robot-zero-sim.service"*) echo "${STUB_SIM:-active}" ;;
*"/usr/local/sbin/fury-mode zero"*) echo "host $*" >> "$STUB_LOG"; echo "robot-zero-sim.service  active" ;;
*) echo "stub ssh: unexpected $*" >&2; exit 1 ;;
esac
'''


@pytest.fixture
def hub(tmp_path):
    """Run the script against the stand-ins; returns (run, state, log)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("flightctl", FLIGHTCTL), ("ssh", SSH), ("sleep", "#!/bin/sh\nexit 0\n")):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    state_file, log_file = tmp_path / "state.json", tmp_path / "calls.log"
    log_file.write_text("")

    def run(verb: str, state: dict, **env: str) -> subprocess.CompletedProcess:
        state_file.write_text(json.dumps({"hub_version": 6, "agent_version": 6, "renders": True, **state}))
        return subprocess.run(
            ["bash", str(SCRIPT), verb], capture_output=True, text=True, timeout=60, check=False,
            env={"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(tmp_path), "FURY_SSH": "user@host",
                 "FLIGHTCTL": str(bin_dir / "flightctl"), "STUB_STATE": str(state_file), "STUB_LOG": str(log_file), **env})

    return run, lambda: json.loads(state_file.read_text()), lambda: log_file.read_text().splitlines()


def test_up_stops_then_labels_then_starts_on_the_slice(hub):
    """From a running policy on the whole gpu: never a label change under a running policy, never a start before the render."""
    run, state, log = hub
    done = run("up", {"label": "", "rendered": "all", "app": "Running"})
    assert done.returncode == 0, done.stderr
    assert log() == ["app stop with quadlet all", f"label {UUID} while app Stopped", f"app start with quadlet {UUID}"]
    assert state()["app"] == "Running" and state()["label"] == UUID


def test_up_from_the_state_the_host_is_in_today(hub):
    """Tenants mode, policy stopped, no label: no second stop, label, start."""
    run, _, log = hub
    done = run("up", {"label": "", "rendered": "all", "app": "Stopped"})
    assert done.returncode == 0, done.stderr
    assert log() == [f"label {UUID} while app Stopped", f"app start with quadlet {UUID}"]


def test_up_again_changes_no_label(hub):
    """Already placed: the label is left alone and the start is only asserted again."""
    run, _, log = hub
    done = run("up", {"label": UUID, "rendered": UUID, "app": "Running"})
    assert done.returncode == 0, done.stderr
    assert log() == [f"app start with quadlet {UUID}"]


def test_up_does_not_start_a_policy_whose_quadlet_was_not_rendered(hub):
    """A hub that takes the label but does not render it: the policy stays stopped and the refusal says what to look at."""
    run, state, log = hub
    done = run("up", {"label": "", "rendered": "all", "app": "Stopped", "renders": False})
    assert done.returncode != 0
    assert "was NOT started" in done.stderr and "flightctl get device/dev1" in done.stderr
    assert not [line for line in log() if line.startswith("app start")]
    assert state()["app"] == "Stopped"


def test_up_refuses_in_flywheel_mode(hub):
    """No slice 0:1 on the host: nothing is changed on the hub, and the mode switch is named."""
    run, _, log = hub
    done = run("up", {"label": "", "rendered": "all", "app": "Stopped"}, STUB_MIG="off")
    assert done.returncode != 0 and "fury-switch.sh tenants" in done.stderr
    assert log() == []


def test_up_refuses_without_robot_zeros_world(hub):
    """The policy's health needs the world's router: without the unit nothing is changed, and both repairs are named."""
    run, _, log = hub
    done = run("up", {"label": "", "rendered": "all", "app": "Stopped"}, STUB_SIM="inactive")
    assert done.returncode != 0
    assert "robot-zero.sh reset" in done.stderr and "74-robot-zero-install.sh install" in done.stderr
    assert log() == []


def test_down_stops_then_removes_the_label(hub):
    """For the way back to flywheel mode: stopped first, then unplaced, and the whole-gpu quadlet awaited."""
    run, state, log = hub
    done = run("down", {"label": UUID, "rendered": UUID, "app": "Running"})
    assert done.returncode == 0, done.stderr
    assert log() == [f"app stop with quadlet {UUID}", "label (removed) while app Stopped"]
    assert state()["rendered"] == "all" and state()["app"] == "Stopped"


def test_down_when_there_is_nothing_to_do(hub):
    """Stopped and unplaced already: no call that changes anything."""
    run, _, log = hub
    done = run("down", {"label": "", "rendered": "all", "app": "Stopped"})
    assert done.returncode == 0, done.stderr
    assert log() == []


def test_no_verb_is_usage(hub):
    """Anything else prints the usage and changes nothing."""
    run, _, log = hub
    done = run("sideways", {"label": "", "rendered": "all", "app": "Stopped"})
    assert done.returncode == 1 and "usage:" in done.stderr and log() == []


def test_what_is_sent_is_the_device_minus_what_the_hub_manages(hub):
    """The stub hub rejects a write that carries status, annotations, generation or owner, that drops resourceVersion, a label or the spec: up and down both get through it."""
    run, state, log = hub
    assert run("up", {"label": "", "rendered": "all", "app": "Stopped"}).returncode == 0
    assert run("down", {"label": UUID, "rendered": UUID, "app": "Running"}).returncode == 0
    assert not [line for line in log() if "REJECTED" in line], log()
    assert state()["labels"] == {"alias": "fury-host", "fleet": "act-inference", "gpu": "nvidia",
                                 "pull_default": "insecureAcceptAnything", "site": "fury"}


@pytest.mark.parametrize("dropped", ["alias", "fleet", "pull_default"])
def test_a_label_lost_by_the_write_stops_everything_and_names_the_repair(hub, dropped):
    """The write replaces the whole label map: if one of the labels that make the device comes back changed, the policy is not started."""
    run, state, log = hub
    done = run("up", {"label": "", "rendered": "all", "app": "Stopped", "hub_drops": dropped})
    assert done.returncode != 0
    assert f"label {dropped} of device/dev1 was" in done.stderr and "edit device/dev1" in done.stderr
    assert "The policy stays stopped" in done.stderr
    assert not [line for line in log() if line.startswith("app start")] and state()["app"] == "Stopped"


def test_down_refused_by_the_hub_after_the_stop_leaves_the_policy_stopped(hub):
    """The label removal fails half way through a switch to flywheel mode: stopped, still placed, and told what to do."""
    run, state, log = hub
    done = run("down", {"label": UUID, "rendered": UUID, "app": "Running", "refuse_apply": True})
    assert done.returncode != 0
    assert log() == [f"app stop with quadlet {UUID}", "apply refused while app Stopped"]
    assert state()["app"] == "Stopped" and state()["label"] == UUID
    assert "the policy stays stopped" in done.stderr
    assert "robot-zero.sh down  again" in done.stderr and "edit device/dev1" in done.stderr


def test_up_refused_by_the_hub_never_starts_the_policy(hub):
    """The same for the way up: no label, no start."""
    run, state, log = hub
    done = run("up", {"label": "", "rendered": "all", "app": "Stopped", "refuse_apply": True})
    assert done.returncode != 0 and "robot-zero.sh up  again" in done.stderr
    assert log() == ["apply refused while app Stopped"] and state()["app"] == "Stopped"


def test_reset_is_the_hosts_fury_mode_zero_and_touches_nothing_on_the_hub(hub):
    """reset restarts robot zero's host side through the one wrapped command; no label, no application call."""
    run, _, log = hub
    done = run("reset", {"label": UUID, "rendered": UUID, "app": "Running"})
    assert done.returncode == 0, done.stderr
    (call,) = log()
    assert call.startswith("host ") and call.endswith("/usr/local/sbin/fury-mode zero") and "user@host" in call
