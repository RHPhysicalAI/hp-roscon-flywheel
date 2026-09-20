# This project was developed with assistance from AI tools.
"""Demo task: how long was a policy stuck failing before it recovered?

`aggregate.longest_failure_streak(records)` is the longest run of consecutive failed episodes, taken in
timestamp order. Episodes that are not scored are no verdict on the policy: they neither extend a run nor
break one. `aggregate()` reports the same number per model version as `VersionStats.longest_failure_streak`.
"""
from eval_dashboard import aggregate


def ep(minute, task_success, model_version="v1", cubes_placed=None, rollout_status="ok"):
    """A normalized record stamped `minute` minutes into the collection run."""
    if cubes_placed is None:
        cubes_placed = 3 if task_success else 1
    return {
        "episode_id": f"{model_version}-{minute:02d}",
        "model_version": model_version,
        "has_failure": False,
        "task_success": task_success,
        "cubes_placed": cubes_placed,
        "avg_smoothness": 0.005,
        "scene": "place_cubes_on_tray",
        "timestamp": f"2026-09-20T10:{minute:02d}:00Z",
        "curation_verdict": None,
        "rollout_steps": 100,
        "rollout_status": rollout_status,
        "origin": "live",
    }


def sensor_fault(minute, model_version="v1"):
    """A failed episode whose cube count could not be read, so it is not scored."""
    record = ep(minute, False, model_version)
    record["cubes_placed"] = None
    return record


def run(outcomes, model_version="v1"):
    """One record per character, a minute apart: S is a success, F a failure."""
    return [ep(minute, outcome == "S", model_version) for minute, outcome in enumerate(outcomes)]


def test_no_episodes_means_no_streak():
    """An empty population has a streak of zero."""
    assert aggregate.longest_failure_streak([]) == 0


def test_all_successes_means_no_streak():
    """A policy that never failed has a streak of zero."""
    assert aggregate.longest_failure_streak(run("SSSS")) == 0


def test_longest_of_several_streaks_is_reported():
    """The longest run wins, not the first or the last one."""
    assert aggregate.longest_failure_streak(run("SFFSFFFSF")) == 3


def test_streak_still_open_at_the_end_counts():
    """A run of failures the policy has not recovered from yet is counted."""
    assert aggregate.longest_failure_streak(run("SSFF")) == 2


def test_episodes_are_ordered_by_timestamp_not_by_arrival():
    """Records arrive in any order; the streak is read off the timeline."""
    timeline = run("FSFFS")
    arrival = [timeline[0], timeline[2], timeline[3], timeline[1], timeline[4]]
    assert aggregate.longest_failure_streak(arrival) == 2


def test_not_scored_episodes_neither_extend_nor_break_a_streak():
    """A sensor fault or an unfinished rollout is skipped over."""
    records = [
        ep(0, True),
        ep(1, False),
        sensor_fault(2),
        ep(3, False),
        ep(4, False, cubes_placed=0, rollout_status="timeout"),
        ep(5, True),
    ]
    assert aggregate.longest_failure_streak(records) == 2


def test_aggregate_reports_the_streak_per_model_version():
    """Each version gets its own number; another version's episodes in between do not break a streak."""
    assert aggregate.VersionStats().longest_failure_streak == 0
    teacher = [ep(0, False, "teacher"), ep(2, False, "teacher"), ep(4, False, "teacher"), ep(6, True, "teacher")]
    student = [ep(1, True, "student-ft50"), ep(3, False, "student-ft50"), ep(5, True, "student-ft50")]
    stats = aggregate.aggregate(teacher + student)
    assert stats["teacher"].longest_failure_streak == 3
    assert stats["student-ft50"].longest_failure_streak == 1
