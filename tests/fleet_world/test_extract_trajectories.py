# This project was developed with assistance from AI tools.
"""The trajectory extractor against small made-up LeRobot datasets, and its output against the motion player."""

import json

import numpy as np
import pytest

pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

import extract_trajectories as ex  # noqa: E402
import motion_player as mp  # noqa: E402

NAMES = ["shoulder_pan.pos", "shoulder_lift.pos", "elbow_flex.pos", "wrist_flex.pos", "wrist_roll.pos", "gripper.pos"]


def _episode(seconds: float, fps: float, scale: float) -> np.ndarray:
    """A smooth six-joint motion in radians, inside the limits."""
    t = np.arange(int(seconds * fps)) / fps
    return np.stack([scale * 0.5 * np.sin(t * (0.3 + 0.1 * j)) + (0.6 if j == 5 else 0.0) for j in range(6)], axis=1)


def _dataset(root, episodes, fps=50.0, degrees=True, names=NAMES, per_file=None):
    """Write meta/info.json and data/ parquet files: one episode a file, or `per_file` episodes a file."""
    (root / "meta").mkdir(parents=True)
    (root / "data" / "chunk-000").mkdir(parents=True)
    features = {"action": {"dtype": "float32", "shape": [6], "names": names}}
    (root / "meta" / "info.json").write_text(json.dumps({"codebase_version": "v3.0", "fps": fps, "features": features}))
    groups = [list(range(len(episodes)))[i:i + (per_file or 1)] for i in range(0, len(episodes), per_file or 1)]
    for n, group in enumerate(groups):
        index, frame, action = [], [], []
        for e in group:
            values = np.rad2deg(episodes[e]) if degrees else episodes[e]
            index += [e] * len(values)
            frame += list(range(len(values)))
            action += values.astype(np.float32).tolist()
        table = pa.table({"episode_index": pa.array(index, pa.int64()), "frame_index": pa.array(frame, pa.int64()),
                          "action": pa.array(action, pa.list_(pa.float32()))})
        pq.write_table(table, root / "data" / "chunk-000" / f"file-{n:03d}.parquet")
    return root


@pytest.mark.parametrize("per_file", [1, 4])
def test_degrees_come_back_as_radians_in_both_layouts(tmp_path, per_file):
    """One episode a file or several: the recorded degrees are radians again, at the output rate."""
    episodes = [_episode(8.0 + i, 50.0, 1.0) for i in range(5)]
    arrays, notes = ex.extract(_dataset(tmp_path / "ds", episodes, per_file=per_file), 30, 10.0, "auto", None, 5.0)
    assert list(arrays["joint_names"]) == ex.JOINT_NAMES and float(arrays["fps"]) == 10.0
    assert len(arrays["episode_lengths"]) == 5 and int(arrays["episode_lengths"].sum()) == len(arrays["actions"])
    first = arrays["actions"][:arrays["episode_lengths"][0]]
    assert np.allclose(first[0], episodes[0][0], atol=1e-4) and np.allclose(first[-1], episodes[0][-1], atol=1e-4)
    assert np.allclose(first[10], episodes[0][50], atol=2e-3)
    assert any("deg" in line for line in notes)


def test_picks_a_spread_and_skips_short_episodes(tmp_path):
    """Thirty of many, evenly spread; an episode under the minimum length is left out."""
    episodes = [_episode(1.0 if i == 3 else 6.0, 50.0, 1.0) for i in range(12)]
    arrays, _ = ex.extract(_dataset(tmp_path / "ds", episodes, per_file=12), 4, 10.0, "auto", None, 5.0)
    assert len(arrays["episode_lengths"]) == 4
    assert all(n == 61 for n in arrays["episode_lengths"])


def test_radian_dataset_is_left_alone(tmp_path):
    """Values within pi and names without the .pos convention are taken as radians."""
    episodes = [_episode(6.0, 50.0, 1.0)]
    arrays, _ = ex.extract(_dataset(tmp_path / "ds", episodes, degrees=False, names=ex.JOINT_NAMES), 30, 50.0,
                           "auto", None, 5.0)
    assert np.allclose(arrays["actions"], episodes[0], atol=1e-5)


def test_names_and_values_disagreeing_is_an_error(tmp_path):
    """Degree-style names with radian-sized values are not guessed at."""
    with pytest.raises(ValueError):
        ex.extract(_dataset(tmp_path / "ds", [_episode(6.0, 50.0, 1.0)], degrees=False), 30, 10.0, "auto", None, 5.0)


def test_another_joint_order_is_an_error(tmp_path):
    """A dataset whose action is not in the controller's order is refused."""
    with pytest.raises(ValueError):
        ex.extract(_dataset(tmp_path / "ds", [_episode(6.0, 50.0, 1.0)], names=sorted(NAMES)), 30, 10.0, "auto",
                   None, 5.0)


def test_values_are_pulled_inside_the_joint_limits(tmp_path):
    """The contract clamps at pi, the joints stop earlier: the output respects the joints."""
    arrays, notes = ex.extract(_dataset(tmp_path / "ds", [_episode(6.0, 50.0, 6.0)]), 30, 10.0, "auto", None, 5.0)
    assert np.all(arrays["actions"] >= ex.JOINT_LIMITS[:, 0] - 1e-6)
    assert np.all(arrays["actions"] <= ex.JOINT_LIMITS[:, 1] + 1e-6)
    assert any("pulled inside" in line and " 0 values" not in line for line in notes)


def test_thirty_long_episodes_fit_a_configmap_and_the_player_loads_them(tmp_path):
    """Thirty 60-second episodes at the default rate are far under the budget, and the player accepts the file."""
    episodes = [_episode(60.0, 50.0, 0.5 + 0.02 * i) for i in range(30)]
    arrays, _ = ex.extract(_dataset(tmp_path / "ds", episodes, per_file=30), 30, 10.0, "auto", None, 5.0)
    path = tmp_path / "trajectories.npz"
    np.savez_compressed(path, **arrays)
    assert path.stat().st_size < ex.CONFIGMAP_BUDGET
    loaded, fps = mp.load_trajectories(str(path))
    assert len(loaded) == 30 and fps == 10.0 and loaded[0].shape == (601, 6)


def test_limits_match_the_player():
    """The extractor and the player agree on the joint order and limits."""
    assert ex.JOINT_NAMES == list(mp.ARM_JOINTS)
    assert np.array_equal(ex.JOINT_LIMITS, mp.JOINT_LIMITS)
