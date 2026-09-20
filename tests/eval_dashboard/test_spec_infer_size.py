# This project was developed with assistance from AI tools.
"""Spec section 9: versions that are not in versions.yaml."""
import pytest

from eval_dashboard import main
from eval_dashboard.main import Store

UNLISTED = "upstream-act-teacher-ft160-20260920T0102"


def rec(episode_id, model_version):
    """A normalized record for the given version."""
    return {
        "episode_id": episode_id,
        "model_version": model_version,
        "has_failure": False,
        "task_success": True,
        "cubes_placed": 3,
        "avg_smoothness": 0.005,
        "scene": "place_cubes_on_tray",
        "timestamp": "2026-09-20T01:00:00Z",
        "curation_verdict": None,
        "rollout_steps": 100,
        "rollout_duration_s": 10.0,
        "seed": None,
        "origin": "live",
        "rollout_status": None,
    }


def test_infer_size_from_the_spec_example():
    """The spec's example name yields 160."""
    assert main.infer_size(UNLISTED) == 160


def test_infer_size_when_the_marker_ends_the_name():
    """A trailing -ft<digits> is matched too."""
    assert main.infer_size("act-v2-ft160") == 160


@pytest.mark.parametrize(
    "name, size",
    [("policy-ft5", 5), ("policy-ft20-run3", 20), ("policy-ft1280-20260920T0102", 1280)],
)
def test_infer_size_reads_the_whole_number(name, size):
    """Every digit after -ft is part of the size."""
    assert main.infer_size(name) == size


def test_infer_size_returns_an_int():
    """The size is an int, not the matched text."""
    size = main.infer_size(UNLISTED)
    assert isinstance(size, int) and not isinstance(size, bool)


@pytest.mark.parametrize("name", ["upstream-act-teacher", "pre-teacher", "ft160", "act-ftx", "act-ft", "soft160", ""])
def test_infer_size_is_none_without_the_marker(name):
    """A name with no -ft<digits> has no inferred size."""
    assert main.infer_size(name) is None


def test_unlisted_fine_tune_gets_its_size_in_the_snapshot():
    """A version missing from the versions file shows the inferred training-set size."""
    store = Store({})
    store.merge([rec("a", UNLISTED)])
    assert store.snapshot()["versions"][UNLISTED]["dataset_size"] == 160


def test_unlisted_version_without_the_marker_stays_unmapped():
    """A version missing from the file with nothing to infer keeps a null size."""
    store = Store({})
    store.merge([rec("a", "mystery-policy")])
    assert store.snapshot()["versions"]["mystery-policy"]["dataset_size"] is None


def test_the_files_entry_wins_over_the_inferred_size():
    """A size from the versions file is used even when the name says otherwise."""
    store = Store({UNLISTED: {"size": 20, "parent": "upstream-act-teacher"}})
    store.merge([rec("a", UNLISTED)])
    block = store.snapshot()["versions"][UNLISTED]
    assert block["dataset_size"] == 20
    assert block["parent"] == "upstream-act-teacher"


def test_the_files_zero_entry_wins_over_the_inferred_size():
    """A file entry of 0 marks a baseline and is not replaced by the inferred number."""
    store = Store({UNLISTED: {"size": 0, "parent": None}})
    store.merge([rec("a", UNLISTED)])
    assert store.snapshot()["versions"][UNLISTED]["dataset_size"] == 0


def test_the_files_entry_wins_when_loaded_from_yaml(tmp_path):
    """The same precedence holds for metadata loaded with load_version_meta."""
    path = tmp_path / "versions.yaml"
    path.write_text(f"{UNLISTED}: 40\n")
    store = Store(main.load_version_meta(str(path)))
    store.merge([rec("a", UNLISTED), rec("b", "other-policy-ft80")])
    versions = store.snapshot()["versions"]
    assert versions[UNLISTED]["dataset_size"] == 40
    assert versions["other-policy-ft80"]["dataset_size"] == 80
