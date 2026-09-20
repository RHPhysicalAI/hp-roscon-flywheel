#!/bin/bash
# Stage the teacher -> v2 promotion so that the governed pipeline can run it in minutes: the runner skips
# training when the candidate's checkpoint is already there, and skips an evaluation whose record for the same
# seeds is already there. This puts both in place, from material that already exists:
#
#   - the project's paired evaluation of the two policies, 360 seeded episodes each (seeds 1000-1359), which was
#     run in eight chunks per policy: merged here into one record per policy, nothing recomputed but the totals,
#     and refused unless the totals and the paired counts come out exactly as the report next to the chunks says
#   - the teacher checkpoint (the incumbent): under /data/flywheel for the eval rig and as the tarball in MinIO
#     the runner unpacks
#   - the v2 checkpoint (the candidate): taken from the signed modelcar image already on this machine
#   - the 161-episode training dataset v2 was fine-tuned on: next to the rest, and in MinIO for the lineage entry
#
#   ./53-stage-promotion.sh [staging-dir]        default /data/models/import-dev, holding eval-360/ (the chunk files and
#                                                paired-report.md), teacher-ckpt/ and flywheel-ladder-160/
#   ./53-stage-promotion.sh [staging-dir] force  replace evaluation records that are already there
#                                                ("force" is recognised in any position, also on its own)
#
# Then, from a laptop:  tools/hub/start-promotion-run.sh act-v2-ft160 0.25 360   (incumbent upstream-act-teacher)
# Refuses to run while a training or an evaluation is in progress: a live run writes the same record names.
#
# This project was developed with assistance from AI tools.
set -euo pipefail
[[ $EUID -eq 0 ]] || exec sudo "$0" "$@"
here=$(dirname "$(readlink -f "$0")")
mkdir -p "$here/log"
exec > >(tee -a "$here/log/$(basename "$0" .sh).log") 2>&1

stage=/data/models/import-dev; force=no
for a in "$@"; do
    if [[ $a == force ]]; then force=yes; else stage=$a; fi
done
rt=quay.io/jary/soarm-flywheel@sha256:5eba6ca4ee8acf7be87ec8da852d314d6dd16d76cfbce09a1581dbf8c5c94837
car=quay.io/jary/soarm-act-modelcar@sha256:bdb513ca4db028fedfa8a30ffefbfafbfb5cd35fb0ce22e2226eb30781e15d6b
envf=/etc/flywheel-runner/env
die() { echo "${0##*/}: $*" >&2; exit 1; }

chunks=$stage/eval-360
teacher=$stage/teacher-ckpt
dataset=$stage/flywheel-ladder-160
[[ -f $chunks/paired-report.md ]]      || die "no evaluation chunks under $chunks"
[[ -f $teacher/model.safetensors ]]    || die "no teacher checkpoint under $teacher"
[[ -f $dataset/meta/info.json ]]       || die "no dataset under $dataset"
[[ -s $envf ]]                         || die "no $envf - ./50-runner-install.sh first"
podman image exists "$rt"  || die "the runtime image is not in root's storage"
podman image exists "$car" || die "the modelcar image is not in root's storage"
if pgrep -f 'lerobot-train|assemble_dataset' >/dev/null || podman pod exists eval-rig 2>/dev/null; then
    die "a training run or an evaluation is in progress - it writes the same record names. Run this when it has finished"
fi

read -r -d '' py <<'PYEOF' || true
import glob, io, json, os, re, shutil, sys, tarfile
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

FLY, STAGE, FORCE = "/flywheel", "/stage", os.environ["FORCE"] == "yes"
CHUNKS = f"{STAGE}/eval-360"
TEACHER, V2 = "upstream-act-teacher", "act-v2-ft160"
BUCKET = "episodes-data"


def merge(prefix, model_version):
    """One record from the chunk files of one policy: same episodes, same outcomes, totals recomputed."""
    files = sorted(glob.glob(f"{CHUNKS}/{prefix}*.json"), key=lambda f: json.load(open(f))["eval_config"]["seed_base"])
    parts = [json.load(open(f)) for f in files]
    eps = sorted((e for p in parts for e in p["episodes"]), key=lambda e: e["seed"])
    seeds = [e["seed"] for e in eps]
    assert seeds == list(range(seeds[0], seeds[0] + len(seeds))), f"{prefix}: seeds are not contiguous"
    scene = {k: v for k, v in parts[0]["eval_config"].items() if k not in ("episodes", "seed_base")}
    for p in parts:
        assert {k: v for k, v in p["eval_config"].items() if k not in ("episodes", "seed_base")} == scene, f"{prefix}: chunks differ in scene settings"
    ok = sum(1 for e in eps if e["task_success"])
    hist = {str(k): sum(1 for e in eps if e["cubes_placed"] == k) for k in range(4)}
    for i, e in enumerate(eps):
        e["index"] = i
    return {
        "model_version": model_version, "policy_path": parts[0].get("policy_path"),
        "served_model_version": parts[0].get("served_model_version"), "timestamp": parts[-1].get("timestamp"),
        "eval_config": {"episodes": len(eps), "seed_base": seeds[0], **scene},
        "aggregate": {"n": len(eps), "successes": ok, "success_rate": round(ok / len(eps), 4),
                      "mean_cubes": round(sum(e["cubes_placed"] for e in eps) / len(eps), 4), "cubes_hist": hist,
                      "mean_smoothness": round(sum(e.get("avg_smoothness") or 0 for e in eps) / len(eps), 6),
                      "goal_rejected": sum(p["aggregate"].get("goal_rejected", 0) for p in parts)},
        "episodes": eps,
        "merged_from": [os.path.basename(f) for f in files],
    }


t, v = merge("eval-teacher-shifted", f"eval-{TEACHER}"), merge("eval-v2-shifted", f"eval-{V2}")
A = {e["seed"]: e for e in t["episodes"]}
fixed = sum(1 for e in v["episodes"] if not A[e["seed"]]["task_success"] and e["task_success"])
broken = sum(1 for e in v["episodes"] if A[e["seed"]]["task_success"] and not e["task_success"])
report = open(f"{CHUNKS}/paired-report.md").read()
# the report's table has the teacher's row first, then v2's: "(successes/n)" twice, then "fixed=.. broken=.."
rates = re.findall(r"\((\d+)/(\d+)\)", report); want = re.search(r"fixed=(\d+) broken=(\d+)", report)
got = (t["aggregate"]["successes"], t["aggregate"]["n"], v["aggregate"]["successes"], v["aggregate"]["n"], fixed, broken)
exp = (int(rates[0][0]), int(rates[0][1]), int(rates[1][0]), int(rates[1][1]), int(want[1]), int(want[2]))
print(f"merged: teacher {got[0]}/{got[1]}, v2 {got[2]}/{got[3]}, fixed {got[4]}, broken {got[5]}   report: {exp}")
if got != exp:
    sys.exit("the merged records do not reproduce the report - nothing written")

os.makedirs(f"{FLY}/eval", exist_ok=True)
for rec in (t, v):
    out = f"{FLY}/eval/{rec['model_version']}.json"
    if os.path.exists(out) and not FORCE:
        have = json.load(open(out)).get("eval_config", {})
        if (have.get("episodes"), have.get("seed_base")) != (rec["eval_config"]["episodes"], rec["eval_config"]["seed_base"]):
            sys.exit(f"{out} exists with other seeds ({have.get('episodes')} from {have.get('seed_base')}) - 'force' to replace it")
        print(f"kept {out}"); continue
    json.dump(rec, open(out + ".tmp", "w"), indent=1); os.replace(out + ".tmp", out)
    print(f"wrote {out}: {rec['aggregate']['successes']}/{rec['aggregate']['n']}")


def place(src, dst):
    """Copy a checkpoint directory, readable by everyone (the rig's policy runs as another user)."""
    if os.path.isfile(f"{dst}/model.safetensors"):
        print(f"kept {dst}"); return
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    for root, dirs, names in os.walk(dst):
        os.chmod(root, 0o755)
        for n in names:
            os.chmod(os.path.join(root, n), 0o644)
    print(f"placed {dst}")


place(f"{STAGE}/teacher-ckpt", f"{FLY}/train/{TEACHER}/checkpoints/last/pretrained_model")
place("/modelcar/models/act", f"{FLY}/train/{V2}/checkpoints/last/pretrained_model")
ds = f"{FLY}/datasets/flywheel-ladder-160"
if not os.path.isfile(f"{ds}/meta/info.json"):
    shutil.copytree(f"{STAGE}/flywheel-ladder-160", ds); print(f"placed {ds}")

s3 = boto3.client("s3", endpoint_url=os.environ["ENDPOINT"], aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
                  aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
                  config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"))


def upload_dir(src, arcname, key):
    try:
        s3.head_object(Bucket=BUCKET, Key=key); print(f"kept s3://{BUCKET}/{key}"); return
    except ClientError as e:
        if e.response["Error"].get("Code") not in ("404", "NoSuchKey", "NotFound"):
            raise
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(src, arcname=arcname)
    size = buf.tell(); buf.seek(0)
    s3.upload_fileobj(buf, BUCKET, key); print(f"uploaded s3://{BUCKET}/{key} ({size} bytes)")


upload_dir(f"{FLY}/train/{TEACHER}/checkpoints/last/pretrained_model", "pretrained_model", f"checkpoints/{TEACHER}/pretrained_model.tar.gz")
upload_dir(ds, "flywheel-ladder-160", f"{TEACHER}/flywheel-ladder-160.tar.gz")
# what the run reports as its dataset when it reuses this checkpoint instead of assembling one
open(f"{FLY}/train/{V2}/dataset_uri.txt", "w").write(f"s3://{BUCKET}/{TEACHER}/flywheel-ladder-160.tar.gz\n")
info = json.load(open(f"{ds}/meta/info.json"))
print(f"dataset: {info['total_episodes']} episodes, {info['total_frames']} frames")
PYEOF

date -u
podman run --rm --pull=never --network host --env-file "$envf" -e ENDPOINT=http://10.20.0.10:30900 -e FORCE="$force" \
    -v "$stage:/stage:ro,z" -v /data/flywheel:/flywheel:z \
    --mount "type=image,source=$car,destination=/modelcar" \
    --entrypoint python3 "$rt" -c "$py"
echo
echo "staged. From a laptop, with the consumer's INCUMBENT set to upstream-act-teacher:"
echo "  tools/hub/start-promotion-run.sh act-v2-ft160 0.25 360"
