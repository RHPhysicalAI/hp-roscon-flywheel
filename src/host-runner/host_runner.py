#!/usr/bin/env python3
# This project was developed with assistance from AI tools.
"""Host runner — [desktop shim] the pipeline's train + eval stages, executed on the host GPU.

On the target (GB10/GB300) the GPU is in the cluster and the KFP pipeline runs training and the
eval harness in pods. On the desktop (D013) the GPU is outside the cluster, so the pipeline's
train/eval components instead PUBLISH a trigger and WAIT for results; this runner consumes the
trigger, does the work with the scripts that already exist, and puts the results where the
pipeline looks. Same contract both ways, so the pipeline definition is identical.

Contract (Kafka, PLAINTEXT NodePort 30903; MinIO S3 NodePort 30900):
  topic training-triggers  <- pipeline:  {run_id, candidate, incumbent, collector,
                                          incumbent_checkpoint: "hf"|"s3://...", steps_per_frame,
                                          eval_n, eval_seed_base}
  topic training-results   -> runner:    {run_id, status: ok|error, candidate, incumbent,
                                          checkpoint_uri, dataset_uri, eval_report_uri, message}
  s3  checkpoints/<candidate>/pretrained_model.tar.gz         the trained checkpoint dir
  s3  eval/<run_id>/eval_report.json                          paired candidate-vs-incumbent report
  s3  eval/<run_id>/eval-<candidate>.json, eval-<incumbent>.json   raw harness records

Steps per run:
  1. assemble  — assemble_dataset.py --from-minio --model-version <collector> --push-dataset
  2. train     — lerobot-train --policy.path=<incumbent> on that dataset, steps = k * frames (D021)
  3. eval      — park the collection loop; D020 harness on candidate AND incumbent, same seeds,
                 eval_n episodes (reuses an incumbent record if one exists for these seeds); restart loop
  4. report    — paired fixed/broken/net + exact sign test (ladder_report logic) -> eval_report.json
  5. publish   — upload artifacts, emit training-results

Runs detached on the host:  nohup python3 host_runner.py >> ~/host-runner.log 2>&1 &
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from math import comb
from pathlib import Path

import boto3
from kafka import KafkaConsumer, KafkaProducer

HOME = Path.home()
FLY = HOME / "flywheel-data"
MINIO = os.environ.get("MINIO_ENDPOINT", "http://10.0.0.49:30900")
KAFKA = os.environ.get("KAFKA_BOOTSTRAP", "10.0.0.49:30903")
S3KEY, S3SEC = os.environ.get("MINIO_ACCESS_KEY"), os.environ.get("MINIO_SECRET_KEY")
if not (S3KEY and S3SEC):
    sys.exit("host_runner: MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be set (source ~/.minio-env); refusing to run")
BUCKET = os.environ.get("ARTIFACT_BUCKET", "episodes-data")
IMAGE = os.environ.get("ACT_IMAGE", "act-inference:latest")
TEACHER_HF = ("/root/.cache/huggingface/hub/models--francocipollone--"
              "rospai_act_sim_arm101_place_cubes_on_tray/snapshots/4c2bdba206dccc382dbf80d48e15b3d754102df6")
CONTRACT = "$(ros2 pkg prefix pai_data_collection)/share/pai_data_collection/config/rosetta/so_arm101.yaml"


def s3():
    # Path-style, accelerate off: the runner runs as the host user and would otherwise inherit any
    # ~/.aws config (boto3 >= 1.36 raises "custom endpoint cannot be combined with S3 Accelerate").
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=MINIO, aws_access_key_id=S3KEY, aws_secret_access_key=S3SEC,
                        config=Config(s3={"addressing_style": "path", "use_accelerate_endpoint": False},
                                      signature_version="s3v4"))


def log(msg):
    print(time.strftime("%m-%d %H:%M ") + "[runner] " + msg, flush=True)


def sh(cmd, **kw):
    log("$ " + (cmd if isinstance(cmd, str) else " ".join(cmd)))
    return subprocess.run(cmd, shell=isinstance(cmd, str), check=True, **kw)


def in_image(script, gpus=False, extra=()):
    """Run a bash snippet inside the act-inference image with the standard mounts."""
    cmd = ["docker", "run", "--rm", "--network", "host", "--entrypoint", "bash", "--shm-size=2g",
           "-v", f"{HOME}/.cache/huggingface:/root/.cache/huggingface",
           "-v", f"{FLY}:/flywheel", "-e", "MINIO_ACCESS_KEY", "-e", "MINIO_SECRET_KEY", *extra]
    if gpus:
        cmd += ["--gpus", "all"]
    cmd += [IMAGE, "-lc", "source /opt/ros/$ROS_DISTRO/setup.bash; source /ws_pai/install/setup.bash; " + script]
    return sh(cmd)


# ---------------------------------------------------------------- stages

def assemble(collector: str, repo_id: str) -> str:
    in_image(f"python3 /ws_pai/assemble_dataset.py --from-minio --model-version {collector} "
             f"--bags-root /flywheel/bags --contract \"{CONTRACT}\" --root /flywheel/datasets "
             f"--repo-id {repo_id} --vcodec h264 --push-dataset")
    info = json.load(open(FLY / "datasets" / repo_id / "meta" / "info.json"))
    log(f"assembled {repo_id}: {info['total_episodes']} episodes, {info['total_frames']} frames")
    return f"s3://{BUCKET}/{collector}/{repo_id}.tar.gz"


def resolve_incumbent(spec: str, name: str) -> str:
    """Return a host path to the incumbent checkpoint dir ('hf' = the upstream teacher)."""
    if spec == "hf":
        return "HF"
    if spec.startswith("s3://"):
        bucket, key = spec[5:].split("/", 1)
        dest = FLY / "train" / name / "checkpoints" / "last"
        dest.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO(); s3().download_fileobj(bucket, key, buf); buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r:gz") as t:
            t.extractall(dest)
        return str(dest / "pretrained_model")
    return spec  # a host path


def train(candidate: str, repo_id: str, incumbent_path: str, k: float) -> Path:
    info = json.load(open(FLY / "datasets" / repo_id / "meta" / "info.json"))
    steps = round(k * info["total_frames"])
    pol = TEACHER_HF if incumbent_path == "HF" else "/incumbent"
    extra = () if incumbent_path == "HF" else ("-v", f"{incumbent_path}:/incumbent:ro")
    in_image(f"lerobot-train --policy.path={pol} --dataset.repo_id={repo_id} "
             f"--dataset.root=/flywheel/datasets/{repo_id} --dataset.video_backend=pyav "
             f"--policy.device=cuda --policy.push_to_hub=false --output_dir=/flywheel/train/{candidate} "
             f"--steps={steps} --save_freq={steps} --log_freq=1000 2>&1 | tr '\\r' '\\n' | grep -E 'loss:|End of training|rror'",
             gpus=True, extra=extra)
    # lerobot writes the checkpoint as root with mode 0600; make it readable to the host user so
    # it can be tarred/uploaded and packaged (the eval mounts it into a root container regardless).
    sh(["docker", "run", "--rm", "-v", f"{FLY}:/flywheel", "--entrypoint", "sh", IMAGE, "-c",
        f"chmod -R a+rX /flywheel/train/{candidate}"])
    ck = FLY / "train" / candidate / "checkpoints" / "last" / "pretrained_model"
    assert (ck / "model.safetensors").exists(), "no checkpoint produced"
    log(f"trained {candidate}: {steps} steps (k={k})")
    return ck


def evaluate(mv: str, ckpt: str, seed_base: int, n: int) -> dict:
    """Run the D020 harness (eval_policy.sh) unless a record for these seeds already exists."""
    out = FLY / "eval" / f"{mv}.json"
    if out.exists():
        d = json.load(open(out))
        c = d.get("eval_config", {})
        if c.get("seed_base") == seed_base and c.get("episodes") == n:
            log(f"eval {mv}: reusing existing record"); return d
    sh(["bash", str(HOME / "eval_policy.sh"), mv, ckpt, str(seed_base), str(n)])
    return json.load(open(out))


def paired_report(run_id, cand_mv, inc_mv, cand, inc) -> dict:
    A = {e["seed"]: e for e in inc["episodes"]}; B = [e for e in cand["episodes"] if e["seed"] in A]
    fix = sum(1 for y in B if not A[y["seed"]]["task_success"] and y["task_success"])
    brk = sum(1 for y in B if A[y["seed"]]["task_success"] and not y["task_success"])
    m, kk = fix + brk, min(fix, brk)
    p = min(1.0, 2 * sum(comb(m, i) for i in range(kk + 1)) / 2 ** m) if m else 1.0
    net = fix - brk
    verdict = "PASS" if (net > 0 and p < 0.05) else "FAIL"
    return {
        "run_id": run_id, "candidate": cand_mv, "incumbent": inc_mv,
        "n_paired": len(B), "candidate_success_rate": cand["aggregate"]["success_rate"],
        "incumbent_success_rate": inc["aggregate"]["success_rate"],
        "delta": round(cand["aggregate"]["success_rate"] - inc["aggregate"]["success_rate"], 4),
        "fixed": fix, "broken": brk, "net": net, "sign_test_p": round(p, 4),
        "candidate_mean_cubes": cand["aggregate"]["mean_cubes"], "incumbent_mean_cubes": inc["aggregate"]["mean_cubes"],
        "rule": "promote iff net > 0 and p < 0.05 (D022)", "verdict": verdict,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def upload_dir_tgz(dirpath: Path, key: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        p = tmp.name
    with tarfile.open(p, "w:gz") as t:
        t.add(dirpath, arcname="pretrained_model")
    s3().upload_file(p, BUCKET, key); os.unlink(p)
    return f"s3://{BUCKET}/{key}"


def upload_json(obj: dict, key: str) -> str:
    s3().put_object(Bucket=BUCKET, Key=key, Body=json.dumps(obj, indent=1).encode(), ContentType="application/json")
    return f"s3://{BUCKET}/{key}"


_LOOP_WAS_RUNNING = False


def loop_park():
    """Stop the collection loop for the eval (it and the eval both drive /run_policy). Remembers
    whether it was running so restore doesn't start a loop the operator had parked (e.g. for disk)."""
    global _LOOP_WAS_RUNNING
    r = subprocess.run(["docker", "ps", "-q", "-f", "name=^act-inference$"], capture_output=True, text=True)
    _LOOP_WAS_RUNNING = bool(r.stdout.strip())
    if _LOOP_WAS_RUNNING:
        subprocess.run(["docker", "stop", "act-inference"], capture_output=True)


def loop_restore():
    if _LOOP_WAS_RUNNING:
        subprocess.run(["docker", "start", "act-inference"], capture_output=True)
    else:
        log("loop was parked before the eval — leaving it parked")


# ---------------------------------------------------------------- run

# training-triggers arrives over an unauthenticated PLAINTEXT Kafka NodePort (D116 — reachable
# off-box by design, D013's host-GPU-outside-the-cluster split). run_id/candidate/incumbent/
# collector all end up in shell commands (in_image's docker run ... bash -lc) and filesystem/S3
# paths, so they're validated here before anything touches them — a malicious value is rejected,
# not executed. Every legitimate value seen in this project (upstream-act-teacher, act-v2-ft160,
# act-v2-ft160-rhem, <collector>-ft<n>-<YYYYMMDDHHMM>, KFP's run_id) fits this charset.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _require_safe(name: str, value) -> str:
    value = str(value)
    if not _SAFE_TOKEN.match(value):
        raise ValueError(f"{name}={value!r} rejected: must match {_SAFE_TOKEN.pattern}")
    return value


def handle(t: dict, producer: KafkaProducer):
    run_id = t.get("run_id", ""); cand = t.get("candidate", ""); inc = t.get("incumbent", "")
    coll = t.get("collector", inc)
    result = {"run_id": run_id, "candidate": cand, "incumbent": inc, "status": "error", "message": ""}
    try:
        run_id = _require_safe("run_id", run_id)
        cand = _require_safe("candidate", cand)
        inc = _require_safe("incumbent", inc)
        coll = _require_safe("collector", coll)
        k = float(t.get("steps_per_frame", 0.25)); n = int(t.get("eval_n", 100)); sb = int(t.get("eval_seed_base", 1000))
        inc_path = resolve_incumbent(t.get("incumbent_checkpoint", "hf"), inc)
        ck = FLY / "train" / cand / "checkpoints" / "last" / "pretrained_model"
        if (ck / "model.safetensors").exists():
            # Idempotent: a checkpoint already trained under this candidate name (e.g. the D022
            # bootstrap that promotes the already-evaluated v2) is reused; assemble+train skipped.
            log(f"checkpoint for {cand} exists — skipping assemble/train"); result["dataset_uri"] = "reused"
        else:
            repo_id = f"{cand}-train"
            result["dataset_uri"] = assemble(coll, repo_id)
            ck = train(cand, repo_id, inc_path, k)
        result["checkpoint_uri"] = upload_dir_tgz(ck, f"checkpoints/{cand}/pretrained_model.tar.gz")
        loop_park()
        try:
            cand_rec = evaluate(f"eval-{cand}", str(ck), sb, n)
            inc_rec = evaluate(f"eval-{inc}", "HF" if inc_path == "HF" else inc_path, sb, n)
        finally:
            loop_restore()
        rep = paired_report(run_id, cand, inc, cand_rec, inc_rec)
        upload_json(cand_rec, f"eval/{run_id}/eval-{cand}.json")
        upload_json(inc_rec, f"eval/{run_id}/eval-{inc}.json")
        result["eval_report_uri"] = upload_json(rep, f"eval/{run_id}/eval_report.json")
        result["verdict"] = rep["verdict"]; result["status"] = "ok"
        log(f"run {run_id}: {rep['verdict']} — {inc} {rep['incumbent_success_rate']:.2f} -> {cand} "
            f"{rep['candidate_success_rate']:.2f}, fixed {rep['fixed']} broken {rep['broken']} p={rep['sign_test_p']}")
    except Exception as e:  # noqa: BLE001 — report every failure to the pipeline
        result["message"] = f"{type(e).__name__}: {e}"; log(f"run {run_id} FAILED: {result['message']}")
    producer.send("training-results", value=result); producer.flush()


def main():
    log(f"listening on {KAFKA} training-triggers")
    consumer = KafkaConsumer("training-triggers", bootstrap_servers=KAFKA, group_id="host-runner",
                             auto_offset_reset="latest", enable_auto_commit=True,
                             value_deserializer=lambda v: json.loads(v.decode()))
    producer = KafkaProducer(bootstrap_servers=KAFKA, value_serializer=lambda v: json.dumps(v).encode())
    for msg in consumer:
        log(f"trigger: {msg.value}")
        handle(msg.value, producer)


if __name__ == "__main__":
    main()
