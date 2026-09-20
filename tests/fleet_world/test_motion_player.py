# This project was developed with assistance from AI tools.
"""The pure parts of the motion player: where a robot starts, synthetic motion, sampling, trajectory files."""

import sys

import numpy as np
import pytest

import motion_player as mp
from forwarder_core import ARM_JOINTS

LOW, HIGH = mp.JOINT_LIMITS[:, 0], mp.JOINT_LIMITS[:, 1]


def test_module_imports_no_ros():
    """Importing the player for its pure parts does not pull in rclpy."""
    assert "rclpy" not in sys.modules


def test_synthetic_motion_stays_inside_joint_limits():
    """Every sample of every built-in episode is inside the limits, margin included."""
    for episode in mp.synthetic_episodes(seed=3):
        assert episode.shape[1] == 6 and episode.dtype == np.float32
        assert np.all(episode >= LOW + mp.LIMIT_MARGIN - 1e-6) and np.all(episode <= HIGH - mp.LIMIT_MARGIN + 1e-6)


def test_synthetic_episode_is_a_reach_and_return():
    """It starts and ends at home, reaches forward in between, and works the gripper."""
    episode = mp.synthetic_episode(11)
    assert np.allclose(episode[0], mp.clip_to_limits(mp.HOME)) and np.allclose(episode[-1], mp.clip_to_limits(mp.HOME))
    assert episode[:, 3].max() > 1.0
    assert episode[:, 5].max() == pytest.approx(mp.GRIP_OPEN) and mp.GRIP_CLOSED in np.round(episode[:, 5], 6)
    assert 8.0 < mp.duration(episode, mp.SYNTHETIC_FPS) < 90.0


def test_synthetic_motion_is_smooth():
    """No joint is asked to move faster than the arm plausibly does between two samples."""
    for episode in mp.synthetic_episodes(seed=1, count=8):
        step = np.abs(np.diff(episode, axis=0)).max()
        assert step * mp.SYNTHETIC_FPS < 2.0


def test_synthetic_episodes_are_repeatable_and_differ():
    """The same seed gives the same set in every pod; episodes within a set differ."""
    a, b = mp.synthetic_episodes(seed=5, count=4), mp.synthetic_episodes(seed=5, count=4)
    assert all(np.array_equal(x, y) for x, y in zip(a, b))
    assert len({e.tobytes() for e in a}) == 4
    assert not np.array_equal(a[0], mp.synthetic_episodes(seed=6, count=1)[0])


def test_start_position_spreads_robots():
    """Twenty robots over thirty episodes start in twenty different episodes at different fractions."""
    lengths = [400] * 30
    starts = [mp.start_position(n, lengths) for n in range(1, 21)]
    assert len({episode for episode, _ in starts}) == 20
    assert len({offset for _, offset in starts}) >= 18
    assert all(0 <= offset < 0.8 * 400 for _, offset in starts)


def test_start_position_with_few_episodes():
    """More robots than episodes: the episode wraps, the offset still tells neighbours apart."""
    lengths = [300, 500, 200]
    starts = [mp.start_position(n, lengths) for n in range(1, 13)]
    assert all(0 <= episode < 3 and 0 <= offset < lengths[episode] - 1 for episode, offset in starts)
    assert len(set(starts)) == 12
    assert mp.start_position(4, lengths)[0] == mp.start_position(1, lengths)[0]


def test_start_position_is_deterministic():
    """A restarted pod enters at the same place."""
    assert mp.start_position(7, [250] * 9) == mp.start_position(7, [250] * 9)


def test_sample_interpolates_and_holds_the_ends():
    """Linear between samples, clamped before the first and after the last."""
    episode = np.array([[0.0] * 6, [1.0] * 6, [3.0] * 6])
    assert np.allclose(mp.sample(episode, 10.0, 0.05), 0.5)
    assert np.allclose(mp.sample(episode, 10.0, 0.15), 2.0)
    assert np.allclose(mp.sample(episode, 10.0, -1.0), 0.0)
    assert np.allclose(mp.sample(episode, 10.0, 99.0), 3.0)
    assert mp.duration(episode, 10.0) == pytest.approx(0.2)


def test_blend_eases_from_the_current_pose():
    """At first the command is the start pose, after the blend time the target, and in between neither."""
    start, target = np.zeros(6), np.ones(6)
    assert np.allclose(mp.blend(start, target, 0.0), start)
    assert np.allclose(mp.blend(start, target, mp.BLEND_S), target)
    middle = mp.blend(start, target, mp.BLEND_S / 2)
    assert np.allclose(middle, 0.5)


def test_smoothstep_is_monotonic_and_clamped():
    """0 at and before the start, 1 at and after the end, never decreasing."""
    values = [mp.smoothstep(u) for u in np.linspace(-0.5, 1.5, 41)]
    assert values[0] == 0.0 and values[-1] == 1.0
    assert all(b >= a for a, b in zip(values, values[1:]))


def test_cube_rng_differs_per_robot_and_episode():
    """Two robots, or two episodes of one robot, do not get the same cube placement."""
    draws = {(robot, played): mp.cube_rng(robot, played).random() for robot in (1, 2, 3) for played in (0, 1, 2)}
    assert len(set(draws.values())) == 9
    assert mp.cube_rng(2, 5).random() == mp.cube_rng(2, 5).random()


def _write_npz(path, actions, lengths, names=ARM_JOINTS, fps=10.0):
    np.savez_compressed(path, actions=np.asarray(actions, dtype=np.float32),
                        episode_lengths=np.asarray(lengths, dtype=np.int32),
                        joint_names=np.array(names), fps=np.float32(fps))


def test_load_trajectories_splits_and_clips(tmp_path):
    """Episodes come back by their lengths, as radians inside the limits."""
    actions = np.zeros((7, 6))
    actions[3, 5] = 9.0
    _write_npz(tmp_path / "t.npz", actions, [3, 4], fps=25.0)
    episodes, fps = mp.load_trajectories(str(tmp_path / "t.npz"))
    assert [len(e) for e in episodes] == [3, 4] and fps == 25.0
    assert episodes[1][0, 5] == pytest.approx(HIGH[5] - mp.LIMIT_MARGIN)


def test_load_trajectories_refuses_another_joint_order(tmp_path):
    """A file whose joints are not in the controller's order is not played."""
    _write_npz(tmp_path / "t.npz", np.zeros((4, 6)), [4], names=sorted(ARM_JOINTS))
    with pytest.raises(ValueError):
        mp.load_trajectories(str(tmp_path / "t.npz"))


@pytest.mark.parametrize("actions,lengths", [(np.zeros((4, 6)), [5]), (np.zeros((4, 5)), [4]),
                                             (np.full((4, 6), np.nan), [4]), (np.zeros((4, 6)), [1, 3])])
def test_load_trajectories_refuses_broken_files(tmp_path, actions, lengths):
    """Lengths that do not add up, five joints, NaN, or a one-sample episode."""
    _write_npz(tmp_path / "t.npz", actions, lengths)
    with pytest.raises(ValueError):
        mp.load_trajectories(str(tmp_path / "t.npz"))
