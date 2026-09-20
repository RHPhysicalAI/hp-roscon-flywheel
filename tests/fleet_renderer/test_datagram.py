# This project was developed with assistance from AI tools.
"""The state contract: what a datagram must be, and which reason an invalid one is counted under."""
import json

import pytest
import render_core as core


def doc(**changes):
    base = {"v": 1, "robot": "r07", "t": 12.5,
            "q": {name: 0.1 * i for i, name in enumerate(core.ARM_JOINTS)},
            "cubes": {name: [0.1 * i, 0.2, 0.41, 1.0, 0.0, 0.0, 0.0] for i, name in enumerate(core.CUBES)}}
    base.update(changes)
    return base


def raw(**changes) -> bytes:
    return json.dumps(doc(**changes)).encode()


def reason(data: bytes) -> str:
    with pytest.raises(core.DatagramError) as err:
        core.parse_datagram(data)
    return err.value.reason


def test_valid_datagram_is_parsed():
    """A conformant datagram yields the robot, its time, six joints and three cube poses."""
    state = core.parse_datagram(raw())
    assert (state.robot, state.t) == ("r07", 12.5)
    assert state.q["gripper_joint"] == pytest.approx(0.5)
    assert state.cubes["cube_large"] == pytest.approx((0.2, 0.2, 0.41, 1.0, 0.0, 0.0, 0.0))


def test_unknown_keys_are_ignored():
    """Extra keys at any level do not make a datagram invalid and are not kept."""
    data = doc(extra={"a": 1})
    data["q"]["mimic_joint"] = 3.0
    data["cubes"]["cube_huge"] = [0] * 7
    state = core.parse_datagram(json.dumps(data).encode())
    assert set(state.q) == set(core.ARM_JOINTS) and set(state.cubes) == set(core.CUBES)


def test_size_limit_is_1200_bytes():
    """1200 bytes pass, 1201 are dropped as oversize before they are parsed."""
    base = raw()
    assert core.parse_datagram(base[:-1] + b" " * (1200 - len(base)) + b"}").robot == "r07"
    assert reason(base[:-1] + b" " * (1201 - len(base)) + b"}") == "oversize"


@pytest.mark.parametrize("data", [b"", b"not json", b"\xff\xfe", b"[1, 2]", b'"r07"', b'{"v": 1, "t": NaN}',
                                  b"[" * 100000])
def test_what_does_not_parse_is_unparseable(data):
    """Bad UTF-8, bad JSON, a non-object and non-JSON constants are one counter."""
    assert reason(data[:1200]) == "unparseable"


@pytest.mark.parametrize("version", [0, 2, "1", None, True, 1.5])
def test_another_version_is_dropped(version):
    """Only v equal to the integer 1 is accepted."""
    assert reason(raw(v=version)) == "version"


@pytest.mark.parametrize("robot", ["r7", "r007", "R07", "x07", "r07 ", "r07\n", 7, None, "r०७"])
def test_robot_id_is_r_and_two_digits(robot):
    """The id is r plus exactly two ASCII digits."""
    assert reason(raw(robot=robot)) == "robot"


@pytest.mark.parametrize("t", ["1.0", None, True, [1]])
def test_time_must_be_a_number(t):
    """t is a number; a string, a bool, a list or nothing is not."""
    assert reason(raw(t=t)) == "t"


def test_a_literal_that_overflows_is_not_a_time():
    """1e999 is valid JSON and parses to infinity, which no clock shows."""
    assert reason(raw().replace(b'"t": 12.5', b'"t": 1e999')) == "t"


def test_missing_or_bad_joint_is_dropped():
    """All six arm joints must be there as finite numbers."""
    for bad in ({}, None, [0] * 6):
        assert reason(raw(q=bad)) == "q"
    q = doc()["q"]
    del q["wrist_roll_joint"]
    assert reason(raw(q=q)) == "q"
    assert reason(raw(q={**doc()["q"], "elbow_flex_joint": "0.3"})) == "q"


@pytest.mark.parametrize("pose", [[0, 0, 0, 1, 0, 0], [0, 0, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0, 0],
                                  [0, 0, 0, "1", 0, 0, 0], "pose", None])
def test_bad_cube_pose_is_dropped(pose):
    """A cube pose is seven finite numbers with a quaternion that has a length."""
    assert reason(raw(cubes={**doc()["cubes"], "cube_medium": pose})) == "cubes"


def test_quaternion_order_is_passed_through_and_normalised():
    """w x y z stays w x y z; only its length is made one."""
    cubes = {**doc()["cubes"], "cube_small": [1, 2, 3, 2.0, 0.0, 0.0, 2.0]}
    pose = core.parse_datagram(raw(cubes=cubes)).cubes["cube_small"]
    assert pose == pytest.approx((1, 2, 3, 0.70710678, 0, 0, 0.70710678))
