# This project was developed with assistance from AI tools.
"""From a robot's state to its row of qpos, through the scene map the image build wrote."""
import numpy as np
import pytest
import render_core as core

# the converted scene as measured on the GPU host: arm at 0-5, cubes' free joints at 6, 13, 20
SCENE_MAP = {"nq": 27,
             "joints": {name: {"qposadr": i, "offset": 0.0471 if name == "wrist_roll_joint" else 0.0}
                        for i, name in enumerate(core.ARM_JOINTS)},
             "cubes": {"cube_small": {"qposadr": 6, "body": "link_0"}, "cube_medium": {"qposadr": 13, "body": "link_1"},
                       "cube_large": {"qposadr": 20, "body": "link_2"}},
             "cameras": {"static": 0, "wrist": 1}, "fovy": 55.4114}


def state():
    q = dict(zip(core.ARM_JOINTS, (-0.5, -0.6, 1.2, 0.0, 0.8, 1.2)))
    cubes = {"cube_small": (0.2, 0.1, 0.4055, 0.921, 0.0, 0.0, 0.389),
             "cube_medium": (0.13, -0.12, 0.4086, 0.102, 0.7, 0.102, -0.7),
             "cube_large": (0.22, -0.16, 0.4108, 0.825, 0.0, 0.0, 0.565)}
    return core.RobotState(robot="r03", t=1.0, q=q, cubes=cubes)


def test_joints_land_at_their_addresses_with_the_wrist_roll_offset():
    """Gazebo's values go in unchanged, except wrist_roll_joint, which gets +0.0471 rad."""
    scene = core.SceneMap.from_json(SCENE_MAP)
    row = np.zeros(27, dtype=np.float32)
    core.assemble_qpos(row, state(), scene)
    assert row[:4] == pytest.approx([-0.5, -0.6, 1.2, 0.0])
    assert row[4] == pytest.approx(0.8 + 0.0471)
    assert row[5] == pytest.approx(1.2)


def test_cube_poses_are_passed_through_in_wxyz():
    """x y z qw qx qy qz is written as it came, at the cube's free joint."""
    scene = core.SceneMap.from_json(SCENE_MAP)
    row = np.full(27, 9.0, dtype=np.float32)
    core.assemble_qpos(row, state(), scene)
    assert row[6:13] == pytest.approx([0.2, 0.1, 0.4055, 0.921, 0.0, 0.0, 0.389])
    assert row[13:20] == pytest.approx([0.13, -0.12, 0.4086, 0.102, 0.7, 0.102, -0.7])
    assert row[20:27] == pytest.approx([0.22, -0.16, 0.4108, 0.825, 0.0, 0.0, 0.565])


def test_only_the_robots_own_row_is_written():
    """Assembling into one row of the batch leaves the other worlds alone."""
    scene = core.SceneMap.from_json(SCENE_MAP)
    qpos = np.zeros((3, 27), dtype=np.float32)
    core.assemble_qpos(qpos[1], state(), scene)
    assert not qpos[0].any() and not qpos[2].any() and qpos[1].any()


def test_scene_map_follows_the_file_not_a_fixed_layout():
    """Addresses come from scene_map.json, so a rebuilt scene with another order still works."""
    moved = {**SCENE_MAP, "nq": 34, "cubes": {**SCENE_MAP["cubes"], "cube_small": {"qposadr": 27}}}
    row = np.zeros(34, dtype=np.float32)
    core.assemble_qpos(row, state(), core.SceneMap.from_json(moved))
    assert row[27:34] == pytest.approx([0.2, 0.1, 0.4055, 0.921, 0.0, 0.0, 0.389])
    assert not row[6:13].any()


@pytest.mark.parametrize("broken", [
    {**SCENE_MAP, "joints": {k: v for k, v in SCENE_MAP["joints"].items() if k != "gripper_joint"}},
    {**SCENE_MAP, "cubes": {k: v for k, v in SCENE_MAP["cubes"].items() if k != "cube_large"}},
    {**SCENE_MAP, "cameras": {"static": 0}},
    {**SCENE_MAP, "nq": 26},
    {**SCENE_MAP, "joints": {**SCENE_MAP["joints"], "gripper_joint": {"qposadr": 27}}},
])
def test_incomplete_or_out_of_range_scene_map_is_refused(broken):
    """A map without a joint, cube or camera, or pointing outside qpos, never reaches the renderer."""
    with pytest.raises(ValueError):
        core.SceneMap.from_json(broken)
