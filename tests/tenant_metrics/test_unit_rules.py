# This project was developed with assistance from AI tools.
"""The exporter's unit file against the installer's own rules: what ships passes, and every way to more than it needs is refused."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FURY = ROOT / "tools" / "host" / "fury"
UNIT = "tenant-metrics.container"

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")


@pytest.fixture
def checkout(tmp_path):
    """A copy of the installer and the unit files it reads, to be spoiled one line at a time."""
    (tmp_path / "flywheel").mkdir()
    shutil.copy(FURY / "75-tenant-metrics-install.sh", tmp_path)
    for name in (UNIT, "training-tenant.container"):
        shutil.copy(FURY / "flywheel" / name, tmp_path / "flywheel")

    def check() -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(tmp_path / "75-tenant-metrics-install.sh"), "check"],
                              capture_output=True, text=True, timeout=30, check=False)

    def edit(old: str, new: str, unit: str = UNIT) -> None:
        path = tmp_path / "flywheel" / unit
        text = path.read_text()
        assert old in text, f"{unit} no longer has the line this test spoils: {old!r}"
        path.write_text(text.replace(old, new, 1))

    return check, edit


def test_the_unit_in_the_repository_keeps_the_rules(checkout, tmp_path):
    """What is committed is what install accepts, and checking it needs no root and writes no log."""
    check, _ = checkout
    done = check()
    assert done.returncode == 0, done.stderr
    assert not (tmp_path / "log").exists()


@pytest.mark.parametrize("old, new, says", [
    # a GPU, credentials, a way around the hardening
    ("Pull=never\n", "Pull=never\nAddDevice=nvidia.com/gpu=0:2\n", "does not get"),
    ("Pull=never\n", "Pull=never\nEnvironmentFile=/etc/flywheel/runner.env\n", "does not get"),
    ("Pull=never\n", "Pull=never\nPodmanArgs=--privileged\n", "does not get"),
    ("Pull=never\n", "Pull=never\nAddCapability=CAP_DAC_READ_SEARCH\n", "does not get"),
    ("Pull=never\n", "Pull=never\nSecurityLabelDisable=true\n", "does not get"),
    ("Pull=never\n", "Pull=never\nPublishPort=9401:9401\n", "does not get"),
    ("Pull=never\n", "", "Pull=never"),
    ("ReadOnly=true\n", "", "ReadOnly=true"),
    ("ReadOnly=true\n", "ReadOnly=true\nReadOnly=false\n", "exactly once"),
    ("DropCapability=all\n", "DropCapability=CAP_NET_RAW\n", "DropCapability=all"),
    ("NoNewPrivileges=true\n", "NoNewPrivileges=false\n", "NoNewPrivileges=true"),
    ("User=65534\n", "User=0\n", "other than 0"),
    ("User=65534\n", "User=root\n", "other than 0"),
    ("User=65534\n", "", "User="),
    # the listener: one address, the hub-side one
    ("Environment=METRICS_ADDR=10.20.0.1:9401\n", "Environment=METRICS_ADDR=0.0.0.0:9401\n", "METRICS_ADDR=10.20.0.1:9401"),
    ("Environment=METRICS_ADDR=10.20.0.1:9401\n",
     "Environment=METRICS_ADDR=10.20.0.1:9401\nEnvironment=METRICS_ADDR=0.0.0.0:9401\n", "more than once"),
    ("Network=host\n", "Network=host\nNetwork=bridge\n", "more than once"),
    # the mounts: its code and the tenant's output, read-only, nothing else
    ("Volume=/data/flywheel/tenant-train:/tenant-train:ro\n", "Volume=/data/flywheel/tenant-train:/tenant-train:z\n", "exactly these two"),
    ("Volume=/data/flywheel/tenant-train:/tenant-train:ro\n", "Volume=/data/flywheel:/tenant-train:ro\n", "exactly these two"),
    ("Pull=never\n", "Pull=never\nVolume=/run/log/journal:/journal:ro\n", "exactly these two"),
    ("Pull=never\n", "Pull=never\nVolume=/run/podman/podman.sock:/podman.sock\n", "exactly these two"),
    ("Environment=TENANT_TRAIN_DIR=/tenant-train\n", "Environment=CURATOR_URL=http://10.20.0.10:30802/episode\n", "the hub's address"),
    # what runs, and the one connection it makes outwards
    ("Environment=RENDER_STATUS_URL=http://10.20.0.1:9702/status\n", "Environment=RENDER_STATUS_URL=http://198.51.100.7/status\n", "RENDER_STATUS_URL"),
    ("Environment=RENDER_STATUS_URL=http://10.20.0.1:9702/status\n",
     "Environment=RENDER_STATUS_URL=http://10.20.0.1:9702/status\nEnvironment=RENDER_STATUS_URL=http://198.51.100.7/\n", "exactly once"),
    ("Entrypoint=python3\n", "Entrypoint=/bin/sh\n", "Entrypoint=python3"),
    ("Exec=-u /opt/tenant-metrics/exporter.py\n", "Exec=-c 'import os; os.system(\"id\")'\n", "Exec=-u /opt/tenant-metrics/exporter.py"),
    ("Exec=-u /opt/tenant-metrics/exporter.py\n", "Exec=-u /opt/tenant-metrics/exporter.py\nExec=-m http.server\n", "exactly once"),
])
def test_a_unit_that_asks_for_more_is_refused(checkout, old, new, says):
    """Each spoiled line is refused, and the refusal says what to put right."""
    check, edit = checkout
    edit(old, new)
    done = check()
    assert done.returncode == 1 and says in done.stderr, done.stderr
    assert "tenant-metrics.container" in done.stderr


def test_another_image_than_the_training_tenants_is_refused(checkout):
    """The exporter runs in the image that is on the host already, by digest."""
    check, edit = checkout
    edit("Image=quay.io/jary/soarm-flywheel@sha256:", "Image=quay.io/jary/soarm-flywheel@sha256:0", unit="training-tenant.container")
    done = check()
    assert done.returncode == 1 and "different images" in done.stderr
    edit("Image=quay.io/jary/soarm-flywheel@sha256:", "Image=quay.io/jary/soarm-flywheel:latest #")
    assert "not pinned by digest" in check().stderr


@pytest.mark.parametrize("name", ["token", "stored_tokens"])
def test_a_token_in_the_tenants_cache_is_refused(checkout, tmp_path, name):
    """The mount would show a HuggingFace credential to a container with the host's network: refused, with the way out."""
    tree = tmp_path / "tenant-train"
    (tree / "cache" / "huggingface").mkdir(parents=True)
    script = ["bash", str(tmp_path / "75-tenant-metrics-install.sh"), "check"]

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(script, capture_output=True, text=True, timeout=30, check=False,
                              env={**os.environ, "TENANT_METRICS_CHECK_DATA": str(tree)})

    assert run().returncode == 0
    (tree / "cache" / "huggingface" / name).write_text("hf_not_a_real_token")
    done = run()
    assert done.returncode == 1 and "HuggingFace token" in done.stderr
    assert f"sudo shred -u {tree}/cache/huggingface/{name}" in done.stderr


def test_the_test_only_tree_is_read_by_check_alone():
    """install and status look under /data/flywheel/tenant-train whatever the environment says."""
    text = (FURY / "75-tenant-metrics-install.sh").read_text()
    assert text.count("TENANT_METRICS_CHECK_DATA") == 1
    assert "[[ $1 != check ]] || scan=${TENANT_METRICS_CHECK_DATA:-$data}" in text


TABLE = """\
| MIG devices:                                                                            |
+------------------+----------------------------------+-----------+-----------------------+
| GPU  GI  CI  MIG |              Shared Memory-Usage |        Vol|        Shared         |
|      ID  ID  Dev |                Shared BAR1-Usage | SM     Unc| CE ENC  DEC  OFA  JPG |
|==================+==================================+===========+=======================|
{rows}
+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  0    1   0   9  |  this row is a process, not a slice                                  |
"""
ROW = "|  0  {gi:>3}   0  {dev:>2}  |             951MiB / 31744MiB    | 18      0 |  2   0    1    0    1 |\n|                  |               0MiB /   728MiB    |           |                       |"


def fake_nvidia_smi(tmp_path: Path, slices: list[tuple[int, int, str]], mode: str = "Enabled") -> dict[str, str]:
    """An environment whose nvidia-smi answers like the host's: (GI id, MIG device, profile) per slice."""
    table = TABLE.format(rows="\n".join(ROW.format(gi=gi, dev=dev) for gi, dev, _ in slices))
    listing = "GPU 0: NVIDIA GB300 (UUID: GPU-0)\n" + "".join(
        f"  MIG {profile:<11} Device  {dev}: (UUID: MIG-{dev})\n" for _, dev, profile in slices)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "table.txt").write_text(table)
    (bin_dir / "list.txt").write_text(listing)
    fake = bin_dir / "nvidia-smi"
    fake.write_text(f"""#!/bin/bash
case "$*" in
*mig.mode.current*) echo {mode} ;;
-L) cat "{bin_dir}/list.txt" ;;
"") cat "{bin_dir}/table.txt" ;;
*) exit 9 ;;
esac
""")
    fake.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def slices(env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run the installer's slice table, which needs no root."""
    return subprocess.run(["bash", str(FURY / "75-tenant-metrics-install.sh"), "slices"], capture_output=True,
                          text=True, timeout=30, check=False, env=env)


def test_slices_as_the_host_has_them(tmp_path):
    """The layout the dashboard was written for reads ok on every row, and the table never counts a process row."""
    done = slices(fake_nvidia_smi(tmp_path, [(1, 0, "3g.126gb"), (11, 1, "1g.31gb"), (12, 2, "1g.31gb"), (13, 3, "1g.31gb")]))
    assert done.returncode == 0, done.stdout + done.stderr
    rows = [line.split() for line in done.stdout.splitlines() if line.strip().endswith("ok")]
    assert [(r[0], r[1]) for r in rows] == [("1", "0"), ("11", "1"), ("12", "2"), ("13", "3")]
    assert "training (1g)" in done.stdout and "WRONG" not in done.stdout


def test_shifted_ids_are_flagged_before_they_reach_a_slide(tmp_path):
    """A known id on another tenant's slice is a wrong name, not a guess; the repair is named."""
    done = slices(fake_nvidia_smi(tmp_path, [(1, 0, "3g.126gb"), (12, 1, "1g.31gb"), (11, 2, "1g.31gb"), (13, 3, "1g.31gb")]))
    assert done.returncode == 1
    assert done.stdout.count("WRONG NAME") == 2 and done.stdout.count(" ok") == 2
    assert "gpu-tenants-dashboard.yaml" in done.stdout and "fury-mode tenants" in done.stdout


def test_another_layout_is_flagged_row_by_row(tmp_path):
    """Unknown ids have no name, the expected ids are reported missing, and a 1g slice where the assistant runs is said."""
    done = slices(fake_nvidia_smi(tmp_path, [(7, 0, "1g.31gb"), (8, 1, "1g.31gb"), (9, 2, "1g.31gb"), (2, 3, "3g.126gb")]))
    assert done.returncode == 1
    assert done.stdout.count("WRONG NAME") == 4 and done.stdout.count("NOT ON THIS GPU") == 4
    done = slices(fake_nvidia_smi(tmp_path / "again", [(1, 0, "1g.31gb"), (11, 1, "1g.31gb"), (12, 2, "1g.31gb"), (13, 3, "1g.31gb")]))
    assert done.returncode == 1 and "WRONG SIZE: a 3g slice was expected for assistant" in done.stdout


def test_slices_with_mig_off(tmp_path):
    """In flywheel mode there is nothing to name, and that is not a failure."""
    done = slices(fake_nvidia_smi(tmp_path, [], mode="Disabled"))
    assert done.returncode == 0 and "MIG is off" in done.stdout


def test_the_installer_and_the_dashboard_carry_the_same_map():
    """The hand-written id map exists twice; the two copies may not drift apart."""
    installer = (FURY / "75-tenant-metrics-install.sh").read_text()
    dashboard = (ROOT / "gitops" / "observability" / "gpu-tenants-dashboard.yaml").read_text()
    pairs = re.findall(r'name\[(\d+)\] = "([^"]+)"', installer)
    assert sorted(pairs) == [("1", "assistant (3g)"), ("11", "robot zero (1g)"), ("12", "training (1g)"), ("13", "rendering (1g)")]
    for gi, name in pairs:
        assert dashboard.count(f'"slice", "{name}", "GPU_I_ID", "{gi}")') == 6, (gi, name)
    assert sorted(set(re.findall(r'GPU_I_ID="(\d+)"', dashboard))) == sorted(gi for gi, _ in pairs)


def test_the_unit_is_not_a_gpu_mode_unit():
    """The unit holds no GPU and fury-mode does not name it: it runs in both modes."""
    text = (FURY / "flywheel" / UNIT).read_text()
    assert "AddDevice" not in text.split("[Unit]", 1)[1] and "WantedBy=multi-user.target" in text
    assert "tenant-metrics" not in (FURY / "fury-mode.sh").read_text()


def test_the_port_is_the_same_everywhere():
    """The unit, the installer, the hub's scrape and the operator page agree on 10.20.0.1:9401."""
    for path in (FURY / "flywheel" / UNIT, FURY / "75-tenant-metrics-install.sh", FURY / "75-tenant-metrics.md",
                 ROOT / "gitops" / "observability" / "fury-tenants-scrape.yaml"):
        assert "10.20.0.1:9401" in path.read_text(), path
