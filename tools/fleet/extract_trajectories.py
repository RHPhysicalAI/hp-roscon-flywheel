#!/usr/bin/env python3
# This project was developed with assistance from AI tools.
"""Turn a LeRobot dataset's recorded actions into the compact trajectories.npz the fleet worlds' motion player replays."""

# One-time bring-up tool. Reads only meta/info.json and the parquet files under data/ (no videos), so it runs
# anywhere numpy and pyarrow are (tools/fleet/requirements.txt) - the runtime image has both:
#
#   tools/fleet/extract_trajectories.py /data/flywheel/datasets/flywheel-ladder-160 -o trajectories.npz
#
# What the dataset holds, per the recording contract (pai_data_collection/config/rosetta/so_arm101.yaml): `action`
# is the six targets published on /forward_position_controller/commands in the controller's order (shoulder_pan,
# shoulder_lift, elbow_flex, wrist_flex, wrist_roll, gripper - named `<joint>.pos`), clamped to +-pi and converted
# radians -> DEGREES, at the contract's `fps: 50`. The player publishes radians on that same topic, so degrees are
# converted back here. Nothing of that is assumed blindly: fps and the names come from meta/info.json, the unit is
# checked against the values, and --units / --fps override what is found.
#
# Output (np.load, no pickle): actions float32 [sum(T), 6] radians in the controller's order, episode_lengths
# int32 [N], joint_names [6], fps, source. Resampled to --fps (default 10: the player interpolates, and an arm
# moves smoothly enough for that) so ~30 episodes stay far below the 1 MiB a ConfigMap may hold.

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

JOINT_NAMES = ["shoulder_pan_joint", "shoulder_lift_joint", "elbow_flex_joint",
               "wrist_flex_joint", "wrist_roll_joint", "gripper_joint"]
# so_arm101_macro.xacro joint limits, radians, in JOINT_NAMES order
JOINT_LIMITS = np.array([[-1.91986, 1.91986], [-1.74533, 1.74533], [-1.69, 1.54],
                         [-1.6, 1.6], [-2.3, 2.3], [0.0, 1.70]])
CONFIGMAP_BUDGET = 700 * 1024   # base64 in a ConfigMap is 4/3 of this, under the 1 MiB an object may be


def action_names(info: dict) -> list[str]:
    """The names meta/info.json gives the action's six values, [] when it gives none."""
    names = info.get("features", {}).get("action", {}).get("names")
    if isinstance(names, dict):   # older datasets: {"motors": [...]}
        names = next(iter(names.values()), None)
    return [str(n) for n in names] if isinstance(names, list) else []


def check_order(names: list[str]) -> None:
    """ValueError unless the dataset's action names are the controller's joints in the controller's order."""
    if not names:
        return
    stems = [n.removesuffix(".pos").removesuffix("_joint") for n in names]
    expected = [j.removesuffix("_joint") for j in JOINT_NAMES]
    if stems != expected:
        raise ValueError(f"action names {names} are not the controller's order {expected}")


def decide_units(units: str, names: list[str], actions: np.ndarray) -> str:
    """'deg' or 'rad': as asked for, else from the values, cross-checked with the naming convention."""
    if units in ("deg", "rad"):
        return units
    # radians cannot exceed the contract's clamp at pi; a recorded reach in degrees always does
    by_values = "deg" if float(np.max(np.abs(actions))) > math.pi + 1e-3 else "rad"
    by_names = "deg" if names and all(n.endswith(".pos") for n in names) else None
    if by_names and by_names != by_values:
        raise ValueError(f"the names say {by_names}, the values (max |a| = {np.max(np.abs(actions)):.3f}) say "
                         f"{by_values}: pass --units deg or --units rad")
    return by_values


def read_episodes(dataset: Path) -> dict[int, np.ndarray]:
    """Every episode's actions [T, 6] in frame order, from whatever parquet files data/ holds."""
    import pyarrow.parquet as pq

    files = sorted((dataset / "data").rglob("*.parquet"))
    if not files:
        raise ValueError(f"no parquet files under {dataset / 'data'}")
    index, frame, action = [], [], []
    for file in files:
        table = pq.read_table(file, columns=["episode_index", "frame_index", "action"])
        index.append(table["episode_index"].to_numpy())
        frame.append(table["frame_index"].to_numpy())
        flat = table["action"].combine_chunks().flatten().to_numpy(zero_copy_only=False)
        if len(flat) != 6 * table.num_rows:
            raise ValueError(f"{file}: action is not six values a row")
        action.append(flat.reshape(-1, 6).astype(np.float64))
    index, frame, action = np.concatenate(index), np.concatenate(frame), np.concatenate(action)
    episodes = {}
    for episode in np.unique(index):
        rows = np.flatnonzero(index == episode)
        episodes[int(episode)] = action[rows[np.argsort(frame[rows], kind="stable")]]
    return episodes


def resample(actions: np.ndarray, fps_in: float, fps_out: float) -> np.ndarray:
    """The same motion at another rate, linear between samples, first and last sample kept."""
    if fps_out >= fps_in or len(actions) < 3:
        return actions
    seconds = (len(actions) - 1) / fps_in
    t_out = np.linspace(0.0, seconds, max(2, int(round(seconds * fps_out)) + 1))
    t_in = np.arange(len(actions)) / fps_in
    return np.stack([np.interp(t_out, t_in, actions[:, j]) for j in range(actions.shape[1])], axis=1)


def pick(episodes: dict[int, np.ndarray], count: int, min_seconds: float, fps: float) -> list[int]:
    """Up to `count` episode indices spread evenly over the dataset, skipping ones too short to be a rollout."""
    usable = sorted(e for e, a in episodes.items() if (len(a) - 1) / fps >= min_seconds)
    if len(usable) <= count:
        return usable
    return [usable[int(round(i))] for i in np.linspace(0, len(usable) - 1, count)]


def extract(dataset: Path, count: int, fps_out: float, units: str, fps_in: float | None,
            min_seconds: float) -> tuple[dict, list[str]]:
    """The npz's arrays and the lines to report."""
    info = json.loads((dataset / "meta" / "info.json").read_text())
    names = action_names(info)
    check_order(names)
    fps = float(fps_in or info.get("fps") or 0)
    if fps <= 0:
        raise ValueError("meta/info.json has no fps: pass --dataset-fps")
    episodes = read_episodes(dataset)
    chosen = pick(episodes, count, min_seconds, fps)
    if not chosen:
        raise ValueError(f"no episode of at least {min_seconds} s among {len(episodes)}")
    unit = decide_units(units, names, np.concatenate([episodes[e] for e in chosen]))
    notes = [(f"dataset: codebase {info.get('codebase_version', '?')}, {len(episodes)} episodes, {fps:g} fps, "
              f"action names {names or 'not given'}"),
             f"units: {unit}" + (" -> radians" if unit == "deg" else "")]
    out, clipped = [], 0
    for e in chosen:
        a = np.deg2rad(episodes[e]) if unit == "deg" else episodes[e]
        if not np.all(np.isfinite(a)):
            raise ValueError(f"episode {e} has a non-finite action")
        a = resample(a, fps, fps_out)
        inside = np.clip(a, JOINT_LIMITS[:, 0], JOINT_LIMITS[:, 1])
        clipped += int(np.count_nonzero(np.abs(inside - a) > 1e-4))
        out.append(inside.astype(np.float32))
    fps_kept = min(fps, fps_out)
    total = sum(len(a) for a in out)
    notes.append(f"kept {len(out)} episodes {chosen}, {total} samples at {fps_kept:g} fps "
                 f"({total / fps_kept:.0f} s of motion), {clipped} values pulled inside the joint limits")
    span = np.ptp(np.concatenate(out), axis=0)
    notes.append("range moved per joint (rad): " + ", ".join(f"{n.removesuffix('_joint')} {s:.2f}"
                                                              for n, s in zip(JOINT_NAMES, span)))
    arrays = {"actions": np.concatenate(out), "episode_lengths": np.array([len(a) for a in out], dtype=np.int32),
              "joint_names": np.array(JOINT_NAMES), "fps": np.float32(fps_kept), "source": np.array(dataset.name)}
    return arrays, notes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dataset", type=Path, help="LeRobot dataset directory (holds meta/info.json and data/)")
    ap.add_argument("-o", "--output", type=Path, default=Path("trajectories.npz"))
    ap.add_argument("--episodes", type=int, default=30, help="how many episodes to keep (default 30)")
    ap.add_argument("--fps", type=float, default=10.0, help="output rate; the player interpolates (default 10)")
    ap.add_argument("--units", choices=["auto", "deg", "rad"], default="auto", help="unit of the dataset's actions")
    ap.add_argument("--dataset-fps", type=float, default=None, help="override meta/info.json's fps")
    ap.add_argument("--min-seconds", type=float, default=5.0, help="skip episodes shorter than this (default 5)")
    args = ap.parse_args()
    try:
        arrays, notes = extract(args.dataset, args.episodes, args.fps, args.units, args.dataset_fps, args.min_seconds)
    except (ValueError, OSError, KeyError) as err:
        sys.exit(f"extract_trajectories: {err}")
    np.savez_compressed(args.output, **arrays)
    size = args.output.stat().st_size
    for line in notes:
        print(line)
    print(f"wrote {args.output}: {size / 1024:.0f} KiB")
    if size > CONFIGMAP_BUDGET:
        sys.exit(f"extract_trajectories: {size / 1024:.0f} KiB is over the {CONFIGMAP_BUDGET // 1024} KiB that fits a "
                 f"ConfigMap: lower --fps or --episodes")


if __name__ == "__main__":
    main()
