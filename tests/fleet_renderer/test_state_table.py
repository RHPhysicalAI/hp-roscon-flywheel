# This project was developed with assistance from AI tools.
"""The robot table: the ordering rule on t, a restarted world, ageing, capacity and the datagram rate."""
import pytest
import render_core as core


def state(robot="r01", t=0.0, pan=0.0):
    q = {name: 0.0 for name in core.ARM_JOINTS} | {"shoulder_pan_joint": pan}
    return core.RobotState(robot=robot, t=t, q=q, cubes={name: (0, 0, 0, 1, 0, 0, 0) for name in core.CUBES})


def test_newer_and_equal_t_are_accepted():
    """A state is the latest when its t is not older than the last accepted one."""
    table = core.StateTable(max_robots=4)
    assert table.accept(state(t=10.0, pan=0.1), now=0.0) is None
    assert table.accept(state(t=10.0, pan=0.2), now=0.1) is None
    assert table.accept(state(t=10.5, pan=0.3), now=0.2) is None
    assert table.views(0.2)[0].state.q["shoulder_pan_joint"] == 0.3


def test_older_t_is_dropped_and_changes_nothing():
    """A reordered datagram is counted as stale_t and neither replaces the state nor refreshes the age."""
    table = core.StateTable(max_robots=4)
    table.accept(state(t=10.0, pan=0.1), now=0.0)
    assert table.accept(state(t=9.9, pan=0.9), now=3.0) == "stale_t"
    assert table.accept(state(t=5.0, pan=0.9), now=3.0) == "stale_t"
    view = table.views(3.0)[0]
    assert (view.state.q["shoulder_pan_joint"], view.age_s, view.datagrams) == (0.1, 3.0, 1)


def test_restarted_world_is_accepted():
    """A t more than 5 s older than the last one is a world that started again from zero."""
    table = core.StateTable(max_robots=4)
    table.accept(state(t=1234.5), now=0.0)
    assert table.accept(state(t=0.03, pan=0.7), now=1.0) is None
    assert table.views(1.0)[0].state.t == 0.03
    assert table.accept(state(t=0.06), now=1.1) is None


def test_the_rule_is_per_robot():
    """One robot's clock says nothing about another's."""
    table = core.StateTable(max_robots=4)
    table.accept(state("r01", t=100.0), now=0.0)
    assert table.accept(state("r02", t=1.0), now=0.0) is None


def test_ageing_live_stale_gone():
    """Live under 5 s of silence, stale from 5 s, gone from the table at 60 s."""
    table = core.StateTable(max_robots=4)
    table.accept(state("r01"), now=100.0)
    assert table.views(104.9)[0].live
    assert not table.views(105.0)[0].live
    assert table.expire(159.9) == [] and len(table.views(159.9)) == 1
    assert table.expire(160.0) == ["r01"] and table.views(160.0) == []


def test_a_stale_robot_comes_back():
    """A datagram after a silence makes the robot live again."""
    table = core.StateTable(max_robots=4)
    table.accept(state(t=1.0), now=0.0)
    assert not table.views(30.0)[0].live
    table.accept(state(t=31.0), now=30.0)
    assert table.views(30.1)[0].live


def test_views_are_in_id_order():
    """The wall's order is the ids', not the order of arrival."""
    table = core.StateTable(max_robots=4)
    for robot in ("r10", "r02", "r07"):
        table.accept(state(robot), now=0.0)
    assert [v.robot for v in table.views(0.0)] == ["r02", "r07", "r10"]


def test_capacity_refuses_new_robots_until_one_is_gone():
    """A robot beyond max_robots is dropped as over_capacity; a place frees when a robot leaves."""
    table = core.StateTable(max_robots=2)
    table.accept(state("r01"), now=0.0)
    table.accept(state("r02"), now=50.0)
    assert table.accept(state("r03"), now=55.0) == "over_capacity"
    assert table.accept(state("r01", t=1.0), now=55.0) is None
    table.accept(state("r01", t=2.0), now=59.0)
    table.expire(110.0)
    assert table.accept(state("r03"), now=110.0) is None


def test_datagram_rate():
    """The rate is what arrived in the last full second, and zero once the robot is quiet."""
    table = core.StateTable(max_robots=4)
    for i in range(61):
        table.accept(state(t=i / 30), now=i / 30)
    assert table.views(2.0)[0].datagrams_per_s == pytest.approx(30.0, rel=0.05)
    assert table.views(4.1)[0].datagrams_per_s == 0.0
