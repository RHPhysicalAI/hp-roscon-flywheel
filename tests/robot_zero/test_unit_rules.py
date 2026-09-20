# This project was developed with assistance from AI tools.
"""Robot zero's unit files against the installer's own rules: what ships passes, and every way into the flywheel's data is refused."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FURY = ROOT / "tools" / "host" / "fury"
UNITS = ("robot-zero-sim.container", "robot-zero-frames.container", "robot-zero-episodes.container")

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")


@pytest.fixture
def checkout(tmp_path):
    """A copy of the installer and the unit files it reads, to be spoiled one line at a time."""
    (tmp_path / "flywheel").mkdir()
    shutil.copy(FURY / "74-robot-zero-install.sh", tmp_path)
    for name in (*UNITS, "act-coordinator.container", "so-arm-sim.container"):
        shutil.copy(FURY / "flywheel" / name, tmp_path / "flywheel")

    def check() -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(tmp_path / "74-robot-zero-install.sh"), "check"],
                              capture_output=True, text=True, timeout=30, check=False)

    def edit(unit: str, old: str, new: str) -> None:
        path = tmp_path / "flywheel" / unit
        text = path.read_text()
        assert old in text, f"{unit} no longer has the line this test spoils: {old!r}"
        path.write_text(text.replace(old, new, 1))

    return check, edit


def test_the_units_in_the_repository_keep_the_rules(checkout):
    """What is committed is what install accepts."""
    check, _ = checkout
    done = check()
    assert done.returncode == 0, done.stderr
    assert not (FURY / "flywheel" / "robot-zero-sim.container").read_text().count("/data/flywheel")


def test_check_writes_no_log_and_needs_no_root(checkout, tmp_path):
    """The rules can be run anywhere: no sudo, no log directory."""
    check, _ = checkout
    assert check().returncode == 0
    assert not (tmp_path / "log").exists()


@pytest.mark.parametrize("unit, old, new, says", [
    # the flywheel's storage, by any door
    ("robot-zero-episodes.container", "Pull=never\n", "Pull=never\nVolume=/data/flywheel:/data:z\n", "mounts something"),
    ("robot-zero-episodes.container", "Pull=never\n", "Pull=never\nVolume=/data/flywheel/bags:/data/bags:ro\n", "mounts something"),
    ("robot-zero-sim.container", "Pull=never\n", "Pull=never\nVolume=/data/flywheel/episodes:/data/episodes:z\n", "mounts something"),
    ("robot-zero-frames.container", ":/opt/robot-zero:ro,z", ":/opt/robot-zero:z", "mounts something"),
    # recording, the emitter, an eval
    ("robot-zero-episodes.container", "Environment=RECORD=false\n", "", "RECORD=false"),
    ("robot-zero-episodes.container", "Environment=RECORD=false\n", "Environment=RECORD=true\n", "RECORD=false"),
    ("robot-zero-episodes.container", "Environment=RECORD=false\n", "Environment=RECORD=false\nEnvironment=EVAL_MODE=true\n", "EVAL_MODE"),
    ("robot-zero-episodes.container", "ROLE=coordinator", "ROLE=all", "ROLE=coordinator"),
    ("robot-zero-sim.container", "Environment=SIM_CAMERAS=off\n", "", "episode emitter"),
    ("robot-zero-sim.container", "Environment=SIM_CAMERAS=off\n", "Environment=SIM_CAMERAS=on\n", "episode emitter"),
    # the pipeline's addresses and credentials
    ("robot-zero-sim.container", "Environment=ROBOT_ID=r00\n",
     "Environment=ROBOT_ID=r00\nEnvironment=CURATOR_URL=http://10.20.0.10:30802/episode\n", "must not be able to feed it"),
    ("robot-zero-episodes.container", "Environment=RECORD=false\n",
     "Environment=RECORD=false\nEnvironment=KAFKA_BOOTSTRAP=10.20.0.10:30903\n", "must not be able to feed it"),
    ("robot-zero-episodes.container", "Pull=never\n", "Pull=never\nEnvironmentFile=/etc/flywheel-runner/env\n", "no credentials"),
    # the slice is the policy's, the images are the signed ones, the robot is r00
    ("robot-zero-frames.container", "Pull=never\n", "Pull=never\nAddDevice=nvidia.com/gpu=0:1\n", "slice 0:1 is the policy's alone"),
    ("robot-zero-sim.container", "Image=quay.io/jary/soarm-flywheel@sha256:", "Image=localhost/soarm-sim:arm64\n#", "not a sha256 digest"),
    ("robot-zero-episodes.container", "@sha256:5eba", "@sha256:0000", "act-coordinator.container"),
    ("robot-zero-frames.container", "Environment=ROBOT_ID=r00\n", "Environment=ROBOT_ID=r07\n", "another robot's pictures"),
    ("robot-zero-sim.container", "Environment=ROBOT_ID=r00\n", "Environment=ROBOT_ID=r01\n", "ROBOT_ID=r00"),
    ("robot-zero-episodes.container", "GZ_PARTITION=robot-zero", "GZ_PARTITION=elsewhere", "same Environment=GZ_PARTITION"),
    ("robot-zero-sim.container", "Pull=never\n", "", "Pull=never"),
    ("robot-zero-sim.container", "@sha256:179cbedc", "@sha256:*\n#", "not a sha256 digest"),
    ("robot-zero-frames.container", "Pull=never\n", "Pull=never\nImage=localhost/anything:latest\n", "more than one Image="),
    # a later line overrides an earlier one: the rule must see both
    ("robot-zero-episodes.container", "Environment=RECORD=false\n", "Environment=RECORD=false\nEnvironment=RECORD=true\n", "RECORD=false"),
    ("robot-zero-episodes.container", "Environment=RECORD=false\n", 'Environment=RECORD=false\nEnvironment="RECORD=true"\n', "RECORD=false"),
    ("robot-zero-episodes.container", "Environment=RECORD=false\n", "Environment=RECORD=false BAG_DIR=/data/bags\n", "sets BAG_DIR"),
    ("robot-zero-sim.container", "Environment=SIM_CAMERAS=off\n", "Environment=SIM_CAMERAS=off\nEnvironment=X=1 SIM_CAMERAS=on\n", "episode emitter"),
    # the hardening: every line, exactly, once - and nothing that takes it back
    *[(unit, line + "\n", "", f"has no  {line}  line") for unit in UNITS
      for line in ("ReadOnly=true", "DropCapability=all", "NoNewPrivileges=true")],
    ("robot-zero-sim.container", "ReadOnly=true\n", "ReadOnly=false\n", "has no  ReadOnly=true  line"),
    ("robot-zero-sim.container", "ReadOnly=true\n", "ReadOnly=true\nReadOnly=false\n", "sets ReadOnly= more than once"),
    ("robot-zero-episodes.container", "DropCapability=all\n", "DropCapability=all\nAddCapability=CAP_SYS_ADMIN\n", "a key robot zero's units do not use"),
    ("robot-zero-episodes.container", "Pull=never\n", "Pull=never\nPodmanArgs=--privileged -v /data/flywheel:/data\n", "a key robot zero's units do not use"),
    ("robot-zero-frames.container", "Pull=never\n", "Pull=never\nMount=type=bind,source=/data/flywheel,destination=/data\n", "a key robot zero's units do not use"),
    ("robot-zero-sim.container", "Pull=never\n", "Pull=never\nSecret=minio-keys\n", "a key robot zero's units do not use"),
    ("robot-zero-sim.container", "Pull=never\n", "Pull=never\nRootfs=/data/flywheel\n", "a key robot zero's units do not use"),
    ("robot-zero-sim.container", "Pull=never\n", "Pull=never\nTmpfs=/data\n", "a key robot zero's units do not use"),
    ("robot-zero-frames.container", "Pull=never\n", "Pull=never\nSecurityLabelDisable=true\n", "a key robot zero's units do not use"),
    # the pipeline's addresses on a line that is not Environment=, and an entrypoint of one's own
    ("robot-zero-frames.container", "exec python3 -u", "export CURATOR_URL=http://x; exec python3 -u", "must not be able to feed it"),
    ("robot-zero-frames.container", "Environment=FRAME_HZ=15\n", "Environment=FRAME_HZ=15\nEnvironment=RENDER_HTTP_ADDR2=10.20.0.10:30900\n", "hub's address"),
    ("robot-zero-episodes.container", "Pull=never\n", "Pull=never\nEntrypoint=/bin/bash\n", "overrides the image's entrypoint"),
    ("robot-zero-sim.container", "Pull=never\n", "Pull=never\nExec=-c 'anything'\n", "overrides the image's entrypoint"),
    ("robot-zero-frames.container", "Volume=/usr/local/lib/flywheel/robot-zero:/opt/robot-zero:ro,z\n", "", "must mount its code"),
    # S2: a value from one unit file is never a pattern for the other
    ("robot-zero-sim.container", "GZ_PARTITION=robot-zero", "GZ_PARTITION=.*", "a plain name"),
    ("robot-zero-sim.container", "GZ_PARTITION=robot-zero", "GZ_PARTITION=robot.zero", "a plain name"),
    ("robot-zero-sim.container", "Environment=GZ_PARTITION=robot-zero\n", "", "a plain name"),
    ("robot-zero-episodes.container", "Environment=GZ_PARTITION=robot-zero\n",
     "Environment=GZ_PARTITION=robot-zero\nEnvironment=GZ_PARTITION=other\n", "same Environment=GZ_PARTITION"),
    # M1: the flywheel's recorder and sim refuse MIG themselves
    ("act-coordinator.container", "ExecCondition=", "#ExecCondition=", "act-coordinator.container has no MIG start condition"),
    ("act-coordinator.container", '= Disabled ]', '= Enabled ]', "act-coordinator.container has no MIG start condition"),
    ("so-arm-sim.container", "ExecCondition=", "ExecStartPre=", "so-arm-sim.container has no MIG start condition"),
])
def test_a_spoiled_unit_is_refused_and_told_why(checkout, unit, old, new, says):
    """One wrong line is enough, and the refusal names it."""
    check, edit = checkout
    edit(unit, old, new)
    done = check()
    assert done.returncode == 1
    assert says in done.stderr, done.stderr
    assert "74-robot-zero-install.sh:" in done.stderr


# ---- the flywheel's own units under MIG (M1) ----

@pytest.mark.parametrize("unit, says", [("act-coordinator.container", "the collection loop belongs to flywheel mode"),
                                        ("so-arm-sim.container", "the sim needs graphics")])
def test_the_flywheels_units_only_start_with_mig_off(unit, says):
    """Robot zero puts a router, a policy and camera topics on this host under MIG: the recorder and the rendering sim must skip their own start there."""
    text = (FURY / "flywheel" / unit).read_text()
    (line,) = [ln for ln in text.splitlines() if ln.startswith("ExecCondition=")]
    assert "--query-gpu=mig.mode.current" in line and '" = Disabled ] ||' in line
    assert says in line and line.rstrip().endswith("exit 1; }'")
    # a condition, not a pre-start: a failed pre-start under Restart= is retried, and each retry reaches the policy
    assert not re.search(r"^ExecStartPre=.*mig\.mode", text, re.MULTILINE)


def test_the_recorders_condition_comes_before_it_creates_anything():
    """ExecCondition= is evaluated before ExecStartPre=, which makes the bag directory."""
    lines = (FURY / "flywheel" / "act-coordinator.container").read_text().splitlines()
    condition = next(i for i, ln in enumerate(lines) if ln.startswith("ExecCondition="))
    pre = next(i for i, ln in enumerate(lines) if ln.startswith("ExecStartPre="))
    assert condition < pre and "/data/flywheel/bags" in lines[pre]


def test_every_robot_zero_unit_only_starts_with_the_slice_there():
    """The mirror image: robot zero's units skip their start while MIG is off."""
    for unit in UNITS:
        (line,) = [ln for ln in (FURY / "flywheel" / unit).read_text().splitlines() if ln.startswith("ExecCondition=")]
        assert 'grep -qxF "nvidia.com/gpu=0:1"' in line


# ---- the policy's restart on a start of the world (S9) ----

@pytest.fixture
def start_post(tmp_path):
    """The shell of robot-zero-sim's ExecStartPost=, as systemd would run it, against stand-ins for ss, systemctl, sleep and /proc."""
    (line,) = [ln for ln in (FURY / "flywheel" / "robot-zero-sim.container").read_text().splitlines() if ln.startswith("ExecStartPost=")]
    found = re.fullmatch(r"ExecStartPost=-/bin/sh -c '(.*)'", line)
    assert found and "${" not in found.group(1), "systemd would expand ${...} before the shell sees it"
    proc, calls = tmp_path / "proc", tmp_path / "calls"
    script = found.group(1).replace("%n", "robot-zero-sim.service").replace("/proc/", f"{proc}/")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("systemctl", f'#!/bin/sh\necho "$*" >> {calls}\n'), ("sleep", "#!/bin/sh\nexit 0\n"),
                       ("ss", f"#!/bin/sh\ncat {tmp_path}/ss.out 2>/dev/null\n")):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)

    def run(listeners: dict[int, str]) -> tuple[subprocess.CompletedProcess, list[str]]:
        (tmp_path / "ss.out").write_text("".join(
            f'LISTEN 0 128 0.0.0.0:7447 0.0.0.0:* users:(("rmw_zenohd",pid={pid},fd=9))\n' for pid in listeners))
        for pid, cgroup in listeners.items():
            (proc / str(pid)).mkdir(parents=True, exist_ok=True)
            (proc / str(pid) / "cgroup").write_text(f"0::{cgroup}\n")
        done = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, timeout=30, check=False,
                              env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
        return done, calls.read_text().splitlines() if calls.exists() else []

    return run


def test_the_policy_is_restarted_once_this_units_router_listens(start_post):
    """A listener on 7447 that belongs to this unit's cgroup: one try-restart of the policy."""
    done, calls = start_post({4242: "/system.slice/robot-zero-sim.service/libpod-payload-abc"})
    assert done.returncode == 0, done.stderr
    assert calls == ["try-restart --no-block act-inference-*-act-inference.service"]


def test_a_world_that_never_listens_leaves_the_policy_alone(start_post):
    """A crash loop at start must not become a restart loop of a healthy policy."""
    done, calls = start_post({})
    assert done.returncode == 0 and calls == []
    assert "the policy is left alone" in done.stdout


def test_somebody_elses_listener_is_not_a_reason(start_post):
    """Port 7447 held by a process of another unit (a hand-started sim): this world is about to fail, the policy stays."""
    done, calls = start_post({777: "/user.slice/user-1000.slice/session-3.scope"})
    assert done.returncode == 0 and calls == []
