# This project was developed with assistance from AI tools.
"""The state datagram of the fleet render contract: shape, size bound, quaternion order, robot id."""

import json
import sys

import pytest

import forwarder_core as core

JOINTS = {name: 0.1 * i for i, name in enumerate(core.ARM_JOINTS)}
CUBES = {name: [0.1, 0.2, 0.4, 1.0, 0.0, 0.0, 0.0] for name in core.CUBES}


def test_module_imports_no_ros_or_gazebo():
    """The pure helpers load without rclpy or the gz bindings."""
    assert not {"rclpy", "gz"} & {name.split(".")[0] for name in sys.modules if name.startswith(("rclpy", "gz."))}


@pytest.mark.parametrize("robot", ["r01", "r00", "r99"])
def test_valid_robot_ids(robot):
    """'r' and two digits is accepted unchanged."""
    assert core.valid_robot_id(robot) == robot


@pytest.mark.parametrize("robot", ["", "r1", "r001", "R01", "r0a", " r01", "r01\n", "x01", None, 7])
def test_invalid_robot_ids(robot):
    """Anything but 'r' and two digits is refused."""
    with pytest.raises(ValueError):
        core.valid_robot_id(robot)


def test_robot_number():
    """The number is the two digits."""
    assert core.robot_number("r07") == 7


def test_quaternion_reorder():
    """Gazebo's x, y, z, w becomes the contract's w, x, y, z."""
    assert core.xyzw_to_wxyz([0.1, 0.2, 0.3, 0.9]) == [0.9, 0.1, 0.2, 0.3]


def test_cube_pose_layout():
    """A cube is x, y, z, then the quaternion w first."""
    assert core.cube_pose((1.0, 2.0, 3.0), (0.1, 0.2, 0.3, 0.9)) == [1.0, 2.0, 3.0, 0.9, 0.1, 0.2, 0.3]


def test_datagram_matches_the_contract():
    """Version, robot, sim time, six joints by name, three cubes of seven numbers."""
    body = json.loads(core.build_datagram("r07", 1234.5678, JOINTS, CUBES))
    assert body["v"] == 1 and body["robot"] == "r07" and body["t"] == 1234.5678
    assert list(body["q"]) == list(core.ARM_JOINTS)
    assert body["q"]["gripper_joint"] == pytest.approx(0.5)
    assert set(body["cubes"]) == set(core.CUBES) and all(len(p) == 7 for p in body["cubes"].values())
    assert body["cubes"]["cube_small"][3] == 1.0


def test_datagram_is_compact_utf8_json_on_one_line():
    """No whitespace is spent, and the bytes decode as UTF-8."""
    text = core.build_datagram("r01", 0.0, JOINTS, CUBES).decode("utf-8")
    assert "\n" not in text and ", " not in text and ": " not in text


@pytest.mark.parametrize("value", [1.7976931348623157e308, -1.7976931348623157e308, 5e-324, -123456789.123456789])
def test_datagram_stays_within_1200_bytes_for_extreme_values(value):
    """The longest floats there are, in every slot, still fit the contract's bound."""
    joints = {name: value for name in core.ARM_JOINTS}
    cubes = {name: [value] * 7 for name in core.CUBES}
    data = core.build_datagram("r99", abs(value), joints, cubes)
    assert len(data) <= core.MAX_DATAGRAM_BYTES
    assert json.loads(data)["q"]["wrist_roll_joint"] == pytest.approx(value, rel=1e-6, abs=1e-6)


def test_typical_datagram_size():
    """An ordinary state is well under half the bound."""
    assert len(core.build_datagram("r07", 99.123, JOINTS, CUBES)) < 600


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_refused(bad):
    """NaN and infinity are not JSON; the datagram is not built."""
    with pytest.raises(ValueError):
        core.build_datagram("r01", 1.0, {**JOINTS, "gripper_joint": bad}, CUBES)
    with pytest.raises(ValueError):
        core.build_datagram("r01", 1.0, JOINTS, {**CUBES, "cube_large": [bad] * 7})


def test_negative_zero_is_not_sent():
    """A rounded -0.0 goes out as 0.0."""
    cubes = {**CUBES, "cube_small": [0.1, 0.2, 0.4, 1.0, -1e-9, 0.0, -0.0]}
    assert b"-0.0" not in core.build_datagram("r01", 1.0, JOINTS, cubes)


def test_missing_cube_or_joint_is_refused():
    """All three cubes and all six joints, or nothing."""
    with pytest.raises(ValueError):
        core.build_datagram("r01", 1.0, JOINTS, {k: v for k, v in CUBES.items() if k != "cube_medium"})
    with pytest.raises(ValueError):
        core.build_datagram("r01", 1.0, {k: v for k, v in JOINTS.items() if k != "gripper_joint"}, CUBES)
    with pytest.raises(ValueError):
        core.build_datagram("r01", 1.0, JOINTS, {**CUBES, "cube_small": [0.0] * 6})


def test_bad_robot_id_is_refused_by_the_datagram():
    """The datagram validates the id itself."""
    with pytest.raises(ValueError):
        core.build_datagram("robot7", 1.0, JOINTS, CUBES)


def test_select_joints_maps_by_name_from_alphabetical_order():
    """/joint_states is alphabetical; the result is keyed by name in controller order."""
    names = sorted(core.ARM_JOINTS)
    positions = [float(i) for i in range(6)]
    picked = core.select_joints(names, positions)
    assert list(picked) == list(core.ARM_JOINTS)
    assert picked["gripper_joint"] == positions[names.index("gripper_joint")]
    assert picked["wrist_roll_joint"] == positions[names.index("wrist_roll_joint")]


def test_select_joints_refuses_incomplete_samples():
    """A missing joint or a ragged message gives None."""
    names = sorted(core.ARM_JOINTS)
    assert core.select_joints(names[:-1], [0.0] * 5) is None
    assert core.select_joints(names, [0.0] * 5) is None


def test_first_hit_per_cube_wins():
    """The model's pose precedes its link's; later entries of the same name are ignored."""
    triples = [("cube_small", (1, 1, 1), (0, 0, 0, 1)), ("link", (9, 9, 9), (0, 0, 0, 1)),
               ("cube_small", (2, 2, 2), (0, 0, 0, 1)), ("so_arm", (3, 3, 3), (0, 0, 0, 1))]
    assert core.first_cube_poses(triples) == {"cube_small": [1, 1, 1, 1, 0, 0, 0]}


def test_cubes_from_cli_json_with_omitted_zero_fields():
    """Protobuf JSON drops zero fields and writes int64 as strings; both are read as numbers."""
    message = {"header": {"stamp": {"sec": "12", "nsec": 500000000}},
               "pose": [{"name": "cube_small", "position": {"x": 0.16, "z": 0.4}, "orientation": {"w": 1}},
                        {"name": "cube_medium", "position": {"x": 0.17, "y": 0.05, "z": 0.4},
                         "orientation": {"z": 0.7071, "w": 0.7071}},
                        {"name": "cube_large", "position": {}, "orientation": {}}]}
    t, cubes = core.cubes_from_json(message)
    assert t == pytest.approx(12.5)
    assert cubes["cube_small"] == [0.16, 0.0, 0.4, 1.0, 0.0, 0.0, 0.0]
    assert cubes["cube_medium"][3:] == [0.7071, 0.0, 0.0, 0.7071]
    assert cubes["cube_large"] == [0.0] * 7


@pytest.mark.parametrize("addr,expected", [("10.20.0.1:9701", ("10.20.0.1", 9701)), ("renderer:1", ("renderer", 1))])
def test_parse_addr(addr, expected):
    """host:port splits at the last colon."""
    assert core.parse_addr(addr) == expected


@pytest.mark.parametrize("addr", ["", "10.20.0.1", ":9701", "host:", "host:0", "host:70000", "host:port"])
def test_parse_addr_refuses(addr):
    """Anything that is not host:port with a real port is refused."""
    with pytest.raises(ValueError):
        core.parse_addr(addr)
