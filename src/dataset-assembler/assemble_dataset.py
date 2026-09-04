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
import tempfile
from pathlib import Path


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
    ap.add_argument("--curated-dir", type=Path, required=True,
                    help="Directory of curated episode JSONs (the curator's pass set)")
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
    ap.add_argument("--vcodec", type=str, default="libx264",
                    help="Video codec passed to port_bags (default libx264 for "
                         "fast, universally-decodable video)")
    ap.add_argument("--dry-run", action="store_true",
                    help="List the selected episodes and exit without porting")
    args = ap.parse_args()

    selected = select_episodes(args.curated_dir, args.bags_root, args.min_cubes,
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
    print(f"[assembler] done -> LeRobot dataset at {Path(root) / args.repo_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
