"""Dataset assembler — build a LeRobot dataset from curated flywheel episodes.

Phase 2.5 step 4 (D018). This is the seam between the data plane and training:
it turns the loop's *curated* episodes into a LeRobot v2 dataset that
`lerobot-train` consumes directly, so "we retrain on the curated episodes the
loop recorded" is literally true.

Pipeline:
  1. Read curated episode JSONs (the curator's pass set) from --curated-dir.
  2. Select episodes that passed curation, succeeded (3/3), carry a
     dataset_path, and whose recorded MCAP bag exists under --bags-root.
  3. Stage the selected bags into a raw dir (real dir per bag: the small
     metadata.yaml is copied, the large .mcap is symlinked) so
     rosetta.port_bags discovers exactly the curated set and nothing else.
  4. Run `rosetta.port_bags` to port the staged bags to a LeRobot dataset,
     using the SAME contract decoders as live inference (schema-consistent).

Runs on the host inside the act-inference image (has lerobot + rosetta). The
recorded bags stay on the host (~2 GB each, raw images); port_bags encodes the
frames to video, so the resulting LeRobot dataset is far smaller.

Example (throwaway container):
  docker run --rm -v ~/flywheel-data:/flywheel act-inference:latest \
    python3 /ws_pai/assemble_dataset.py \
      --curated-dir /flywheel/curated \
      --bags-root   /flywheel/bags \
      --contract    "$(ros2 pkg prefix pai_data_collection)/share/pai_data_collection/config/rosetta/so_arm101.yaml" \
      --root        /flywheel/datasets \
      --repo-id     flywheel-curated \
      --vcodec      libx264
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# MinIO / S3 config (env-overridable). Used only when --from-minio / --push-dataset.
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://10.0.0.49:30900")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
# Kafka (external NodePort listener, PLAINTEXT). Used only for the dataset manifest.
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "10.0.0.49:30903")


def _s3():
    """Boto3 S3 client for MinIO. Imported lazily so the local-only path has no
    boto3 dependency."""
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
    )


def pull_curated_from_minio(bucket: str, model_version: str | None, dest: Path) -> Path:
    """Download curated episode JSONs from MinIO into dest, so the curated
    *selection* comes from the hub. Returns dest."""
    s3 = _s3()
    prefix = f"{model_version}/" if model_version else ""
    dest.mkdir(parents=True, exist_ok=True)
    paginator = s3.get_paginator("list_objects_v2")
    n = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            out = dest / Path(key).name
            s3.download_file(bucket, key, str(out))
            n += 1
    print(f"[assembler] pulled {n} curated JSON(s) from s3://{bucket}/{prefix}")
    return dest


def push_dataset_to_minio(dataset_dir: Path, bucket: str, model_version: str,
                          repo_id: str) -> str:
    """Tar the ported LeRobot dataset and upload it to MinIO. Returns s3 URI.

    The dataset is small (video-encoded), so a single tarball object is the
    canonical trainable artifact in the hub — pull it and train. Raw bags stay
    on the host by design (Fury-prep decides whether the hub archives them too)."""
    s3 = _s3()
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        s3.create_bucket(Bucket=bucket)
        print(f"[assembler] created bucket {bucket}")
    key = f"{model_version}/{repo_id}.tar.gz"
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tar_path = tmp.name
    try:
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(dataset_dir, arcname=repo_id)
        size_bytes = os.path.getsize(tar_path)
        s3.upload_file(tar_path, bucket, key)
    finally:
        os.unlink(tar_path)
    uri = f"s3://{bucket}/{key}"
    print(f"[assembler] pushed dataset ({size_bytes / 1e6:.1f} MB) -> {uri}")
    return uri, size_bytes


def publish_dataset_manifest(bootstrap: str, topic: str, manifest: dict) -> None:
    """Announce the pushed dataset on Kafka, mirroring the sync-agent's
    per-episode manifest. Consumers (e.g. a training trigger, the eval
    dashboard) can discover a new trainable corpus without scanning MinIO."""
    from kafka import KafkaProducer
    p = KafkaProducer(
        bootstrap_servers=bootstrap,
        value_serializer=lambda v: json.dumps(v).encode(),
    )
    p.send(topic, value=manifest)
    p.flush()
    p.close()
    print(f"[assembler] published dataset manifest -> {topic} @ {bootstrap}")


def select_episodes(curated_dir: Path, bags_root: Path, min_cubes: int,
                    model_version: str | None, limit: int) -> list[tuple[str, Path]]:
    """Return [(episode_id, bag_dir)] for curated episodes with a present bag."""
    selected: list[tuple[str, Path]] = []
    for p in sorted(curated_dir.glob("*.json")):
        try:
            ep = json.loads(p.read_text())
        except Exception as e:
            print(f"[assembler] skip {p.name}: unreadable ({e})")
            continue
        # The curator only writes passes to CURATED_DIR, but be explicit.
        if ep.get("curation_verdict", "pass") != "pass":
            continue
        if not ep.get("task_success", False):
            continue
        if ep.get("cubes_placed", 0) < min_cubes:
            continue
        if model_version and ep.get("model_version") != model_version:
            continue
        dp = ep.get("dataset_path")
        eid = ep.get("episode_id", p.stem)
        if not dp:
            print(f"[assembler] skip {eid}: no dataset_path (unrecorded)")
            continue
        name = os.path.basename(str(dp).rstrip("/"))
        bag_dir = bags_root / name
        if not (bag_dir / "metadata.yaml").exists():
            print(f"[assembler] skip {eid}: bag missing at {bag_dir}")
            continue
        selected.append((eid, bag_dir))
    if limit:
        selected = selected[:limit]
    return selected


def stage_bags(selected: list[tuple[str, Path]]) -> Path:
    """Stage selected bags into a raw dir port_bags can discover.

    Each staged bag is a real directory (so rglob('metadata.yaml') finds it
    without depending on symlink-following): metadata.yaml is copied, the
    large .mcap files are symlinked to avoid duplicating gigabytes.
    """
    stage = Path(tempfile.mkdtemp(prefix="assemble_"))
    for _eid, bag_dir in selected:
        dest = stage / bag_dir.name
        dest.mkdir()
        for f in bag_dir.iterdir():
            if f.name == "metadata.yaml":
                shutil.copy2(f, dest / f.name)
            else:
                (dest / f.name).symlink_to(f.resolve())
    return stage


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--curated-dir", type=Path, default=None,
                    help="Directory of curated episode JSONs (the curator's pass set). "
                         "Omit with --from-minio to pull the selection from the hub.")
    ap.add_argument("--bags-root", type=Path, required=True,
                    help="Directory holding the recorded bag subdirectories")
    ap.add_argument("--contract", type=Path, required=True,
                    help="Rosetta contract YAML (so_arm101.yaml)")
    ap.add_argument("--repo-id", type=str, default="flywheel-curated",
                    help="LeRobot dataset repo-id / local name")
    ap.add_argument("--root", type=Path, default=None,
                    help="Parent dir for datasets; saved to root/repo-id "
                         "(default: ~/.cache/huggingface/lerobot)")
    ap.add_argument("--model-version", type=str, default=None,
                    help="Only include episodes stamped with this model_version")
    ap.add_argument("--min-cubes", type=int, default=3,
                    help="Minimum cubes_placed to include (default 3 = full success)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Cap the number of episodes (0 = all)")
    ap.add_argument("--vcodec", type=str, default="h264",
                    help="Video codec passed to port_bags. lerobot 0.5.1 accepts "
                         "h264 (CPU, default — universally pyav-decodable), "
                         "h264_nvenc (GPU), hevc, or libsvtav1. NOT libx264.")
    ap.add_argument("--dry-run", action="store_true",
                    help="List the selected episodes and exit without porting")
    # MinIO integration (the hub as the source/sink of curated data)
    ap.add_argument("--from-minio", action="store_true",
                    help="Pull the curated selection (JSONs) from MinIO instead of --curated-dir")
    ap.add_argument("--push-dataset", action="store_true",
                    help="Upload the ported LeRobot dataset (as a tarball) to MinIO")
    ap.add_argument("--curated-bucket", default="episodes-curated",
                    help="MinIO bucket holding curated episode JSONs (--from-minio)")
    ap.add_argument("--data-bucket", default="episodes-data",
                    help="MinIO bucket for the ported dataset tarball (--push-dataset)")
    ap.add_argument("--dataset-topic", default="dataset-manifests",
                    help="Kafka topic for the dataset manifest (empty string to skip)")
    args = ap.parse_args()

    if args.from_minio:
        if args.model_version is None:
            print("[assembler] --from-minio needs --model-version to scope the pull")
            return 2
        curated_dir = pull_curated_from_minio(
            args.curated_bucket, args.model_version,
            Path(tempfile.mkdtemp(prefix="curated_")))
    elif args.curated_dir:
        curated_dir = args.curated_dir
    else:
        print("[assembler] need --curated-dir or --from-minio")
        return 2

    selected = select_episodes(curated_dir, args.bags_root, args.min_cubes,
                               args.model_version, args.limit)
    print(f"[assembler] {len(selected)} curated episode(s) with a present bag:")
    for eid, bag_dir in selected:
        print(f"  {eid}  <-  {bag_dir.name}")
    if not selected:
        print("[assembler] nothing to assemble")
        return 1
    if args.dry_run:
        return 0

    stage = stage_bags(selected)
    print(f"[assembler] staged {len(selected)} bag(s) at {stage}")

    cmd = [
        sys.executable, "-m", "rosetta.port_bags",
        "--raw-dir", str(stage),
        "--repo-id", args.repo_id,
        "--contract", str(args.contract),
        "--vcodec", args.vcodec,
    ]
    if args.root:
        cmd += ["--root", str(args.root)]
    print("[assembler] running:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    root = args.root or Path.home() / ".cache/huggingface/lerobot"
    dataset_dir = Path(root) / args.repo_id
    print(f"[assembler] done -> LeRobot dataset at {dataset_dir}")

    if args.push_dataset:
        mv = args.model_version or "unversioned"
        uri, size_bytes = push_dataset_to_minio(dataset_dir, args.data_bucket, mv, args.repo_id)

        if args.dataset_topic and KAFKA_BOOTSTRAP:
            import time as _time
            info = {}
            info_path = dataset_dir / "meta" / "info.json"
            if info_path.exists():
                info = json.loads(info_path.read_text())
            manifest = {
                "dataset_id": args.repo_id,
                "s3_uri": uri,
                "model_version": mv,
                "num_episodes": info.get("total_episodes", len(selected)),
                "num_frames": info.get("total_frames"),
                "fps": info.get("fps"),
                "robot_type": info.get("robot_type"),
                "size_bytes": size_bytes,
                "vcodec": args.vcodec,
                "episode_ids": [eid for eid, _ in selected],
                "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            }
            try:
                publish_dataset_manifest(KAFKA_BOOTSTRAP, args.dataset_topic, manifest)
            except Exception as e:
                print(f"[assembler] dataset manifest publish failed: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
