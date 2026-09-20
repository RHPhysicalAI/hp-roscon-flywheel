# This project was developed with assistance from AI tools.
"""Tagging rules of the pipeline's package_modelcar step, run against a fake crane, MinIO and download."""
import builtins
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import types
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipeline"))
kfp = pytest.importorskip("kfp")
import act_flywheel_pipeline as pl  # noqa: E402

REPO = "registry.example/team/modelcar"
CRANE_TGZ = b"not really crane"
DIGEST = {"amd64": "sha256:" + "a" * 64, "arm64": "sha256:" + "b" * 64, "index": "sha256:" + "c" * 64}


def _checkpoint_tgz() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = b"weights"
        info = tarfile.TarInfo("pretrained_model/model.safetensors"); info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def crane_calls(monkeypatch, tmp_path):
    """Run the component's Python with every outside effect faked; yields the crane command lines it issued."""
    calls = []

    def fake_run(cmd, **kw):
        out = ""
        if cmd[0] == "/tmp/crane":
            calls.append(cmd[1:])
            if cmd[1] == "append":
                tag = cmd[cmd.index("-t") + 1]
                out = f"{REPO}@{DIGEST['arm64' if tag.endswith('arm64') else 'amd64']}\n"
            elif cmd[1] == "index":
                out = f"{REPO}@{DIGEST['index']}\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    boto3 = types.ModuleType("boto3")
    boto3.client = lambda *a, **kw: types.SimpleNamespace(get_object=lambda **k: {"Body": io.BytesIO(_checkpoint_tgz())})
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(urllib.request, "urlretrieve", lambda url, dest: Path(dest).write_bytes(CRANE_TGZ))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x"); monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "y")
    quay = tmp_path / "dockerconfigjson"; quay.write_text(json.dumps({"auths": {}}))
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **kw: real_open(quay if f == "/etc/quay/.dockerconfigjson" else f, *a, **kw))
    return calls


def _package(run_id: str) -> str:
    sha = hashlib.sha256(CRANE_TGZ).hexdigest()
    return pl.package_modelcar.python_func(
        checkpoint_uri="s3://episodes-data/checkpoints/cand/pretrained_model.tar.gz", candidate="cand",
        registry_repo=REPO, platform=["linux/amd64", "linux/arm64"], s3_endpoint="http://minio.example:9000",
        crane_version="v0", modelcar_base="base.example/ubi-micro", crane_sha256_amd64=sha, crane_sha256_arm64=sha,
        run_id=run_id)


def _tags(calls, verb):
    return [c[c.index("-t") + 1] for c in calls if c[0] == verb]


def test_every_image_of_a_run_gets_a_tag_that_carries_the_run_id(crane_calls):
    """Per-arch images and the index are tagged <candidate>-<run id, 8 chars>, so a later run never untags them."""
    ref = _package("9fb233e8-8402-47cf-9f38-038e9294dbe6")
    assert _tags(crane_calls, "append") == [f"{REPO}:cand-9fb233e8-amd64", f"{REPO}:cand-9fb233e8-arm64"]
    assert _tags(crane_calls, "index") == [f"{REPO}:cand-9fb233e8"]
    assert ref == f"{REPO}@{DIGEST['index']}"


def test_the_bare_candidate_tag_is_moved_to_the_new_index_last(crane_calls):
    """The convenience tag follows the newest index, by digest, after the unique tag exists."""
    _package("9fb233e8-8402-47cf-9f38-038e9294dbe6")
    assert crane_calls[-1] == ["tag", f"{REPO}@{DIGEST['index']}", "cand"]
    assert [c[0] for c in crane_calls] == ["append", "append", "index", "tag"]


def test_without_a_run_id_the_old_single_tag_scheme_is_kept(crane_calls):
    """No run id: tags are the bare candidate name and nothing is re-tagged."""
    _package("")
    assert _tags(crane_calls, "index") == [f"{REPO}:cand"]
    assert not [c for c in crane_calls if c[0] == "tag"]
