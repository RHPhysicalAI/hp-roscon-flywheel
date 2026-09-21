# This project was developed with assistance from AI tools.
"""The pipeline's open_promotion_pr step against a fake GitHub, and a guard over the real Fleet files."""
import builtins
import json
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))

HOST = "gitops/rhem/fleet-act-inference.yaml"
ROBOTS = "gitops/rhem/fleet-robots.yaml"
CONSUMER = "gitops/flywheel/manifest-consumer.yaml"
CATALOG = "gitops/rhem-catalog/catalogitem-soarm-act.yaml"
OLD, NEW = "sha256:" + "a" * 64, "sha256:" + "b" * 64
IMAGE = "registry.example/team/soarm-act-modelcar"
REPORT = dict(incumbent="teacher", incumbent_success_rate=0.82, candidate_success_rate=0.925, incumbent_mean_cubes=1.5,
              candidate_mean_cubes=1.8, n_paired=360, fixed=57, broken=19, net=38, sign_test_p=0.0001,
              rule="net > 0 and p < 0.05", verdict="PASS")


def _fleet(name: str, digest: str = OLD, version: str = "teacher") -> str:
    return (f"kind: Fleet\nmetadata:\n  name: {name}\nspec:\n  envVars:\n    ROLE: policy\n    MODEL_VERSION: {version}\n"
            f"  content: |\n    # MODEL_VERSION: a comment is not a site\n    Image={IMAGE}@{digest}\n")


def _consumer() -> str:
    return "".join(f'  - {{name: {k}, value: "old"}}\n' for k in ("INCUMBENT", "COLLECTOR", "INCUMBENT_CHECKPOINT"))


class _GithubException(Exception):
    def __init__(self, status: int):
        super().__init__(status)
        self.status = status


class _Repo:
    """What open_promotion_pr uses of a PyGithub repository, recording every write."""

    def __init__(self, files: dict[str, str]):
        self.files, self.trees, self.commits, self.refs, self.pulls, self.refused = files, [], [], [], [], None
        self.owner = types.SimpleNamespace(login="org")

    def get_git_ref(self, ref):
        return types.SimpleNamespace(object=types.SimpleNamespace(sha="base"))

    def get_git_commit(self, sha):
        return types.SimpleNamespace(sha=sha, tree="base-tree")

    def get_contents(self, path, ref):
        if path not in self.files:
            raise _GithubException(404)
        return types.SimpleNamespace(decoded_content=self.files[path].encode())

    def create_git_tree(self, elems, base):
        self.trees.append({e.path: e.content for e in elems})
        return "tree"

    def create_git_commit(self, message, tree, parents):
        self.commits.append(message)
        return types.SimpleNamespace(sha="c0ffee")

    def create_git_ref(self, ref, sha):
        self.refs.append(ref)

    def get_pulls(self, **kw):
        return []

    def create_pull(self, **kw):
        self.pulls.append(kw)
        return types.SimpleNamespace(html_url="https://git.example/org/repo/pull/1")


@pytest.fixture
def promote(monkeypatch, tmp_path):
    """Returns run(files, **overrides) -> the fake repository after the step ran on those files."""
    pytest.importorskip("kfp")
    import act_flywheel_pipeline as pl

    token = tmp_path / "token"; token.write_text("not-a-token")
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **kw: real_open(token if f == "/etc/github/token" else f, *a, **kw))

    def run(files: dict[str, str], **overrides) -> _Repo:
        repo = _Repo(files)
        github = types.ModuleType("github")
        github.Github = lambda tok: types.SimpleNamespace(get_repo=lambda name: repo)
        github.GithubException = _GithubException
        github.InputGitTreeElement = lambda path, mode, kind, content: types.SimpleNamespace(path=path, content=content)
        monkeypatch.setitem(sys.modules, "github", github)
        args = dict(image_ref=f"{IMAGE}@{NEW}", candidate="cand", checkpoint_uri="s3://bucket/cand.tar.gz",
                    report_json=json.dumps(REPORT), github_repo="org/repo", gitops_branch="fury", fleet_file=HOST,
                    consumer_file=CONSUMER, fleet_ui_url="https://ui.example/fleets/act-inference",
                    run_id="9fb233e8-8402-47cf-9f38-038e9294dbe6")
        try:
            pl.open_promotion_pr.python_func(**{**args, **overrides})
        except SystemExit as e:
            repo.refused = e.code
        return repo

    return run


def _files(**over: str) -> dict[str, str]:
    return {HOST: _fleet("act-inference"), ROBOTS: _fleet("robots"), CONSUMER: _consumer(), **over}


def test_both_fleet_files_are_edited_in_one_tree_and_one_commit(promote):
    """The host's Fleet, the robots' Fleet and the consumer change together, and the commit and the PR name both Fleets."""
    repo = promote(_files())
    assert len(repo.trees) == len(repo.commits) == len(repo.pulls) == 1
    tree = repo.trees[0]
    assert set(tree) == {HOST, ROBOTS, CONSUMER}
    for f, name in ((HOST, "act-inference"), (ROBOTS, "robots")):
        assert tree[f] == _fleet(name, NEW, "cand")
    assert "Fleet act-inference + robots" in repo.commits[0]
    body = repo.pulls[0]["body"]
    assert f"Fleet `act-inference` (`{HOST}`) and Fleet `robots` (`{ROBOTS}`)" in body
    assert "https://ui.example/fleets/act-inference , https://ui.example/fleets/robots" in body
    assert "no re-pull on a device that has served it before" in body and "does not re-pull" not in body
    assert repo.refs == ["refs/heads/promote/cand-9fb233e8"]


@pytest.mark.parametrize("robots", [_fleet("robots", digest="sha256:" + "c" * 64), _fleet("robots", version="other")])
def test_it_refuses_when_the_fleet_files_disagree_beforehand(promote, capsys, robots):
    """A different digest or MODEL_VERSION in one file stops the promotion before anything is written."""
    repo = promote(_files(**{ROBOTS: robots}))
    assert repo.refused == 1 and not (repo.trees or repo.commits or repo.refs or repo.pulls)
    out = capsys.readouterr().out
    assert "do not pin the same model" in out and HOST in out and ROBOTS in out


@pytest.mark.parametrize("which", [HOST, ROBOTS])
@pytest.mark.parametrize("broken", [
    _fleet("x").replace("@" + OLD, ":a-tag"),
    _fleet("x") + f"    Image={IMAGE}@{OLD}\n",
    _fleet("x").replace("    MODEL_VERSION:", "    MODEL_VERSIONS:"),
    _fleet("x") + "    MODEL_VERSION: teacher\n",
], ids=["no-image", "two-images", "no-model-version", "two-model-versions"])
def test_it_refuses_unless_each_edit_matches_exactly_once(promote, capsys, which, broken):
    """Zero or several sites for the digest or for MODEL_VERSION, in either file, stop the promotion."""
    repo = promote(_files(**{which: broken}))
    assert repo.refused == 1 and not (repo.trees or repo.commits or repo.refs or repo.pulls)
    assert f"{which}: the promotion edit must match exactly once each" in capsys.readouterr().out


def test_an_empty_also_fleet_files_edits_the_one_fleet_as_before(promote):
    """With no further Fleet files the step touches the host's Fleet and the consumer only, whatever the robots' file holds."""
    repo = promote(_files(**{ROBOTS: _fleet("robots", version="other")}), also_fleet_files="")
    assert set(repo.trees[0]) == {HOST, CONSUMER}
    assert repo.trees[0][HOST] == _fleet("act-inference", NEW, "cand")
    assert "Fleet act-inference <-" in repo.commits[0] and "robots" not in repo.pulls[0]["body"]


def test_the_step_accepts_the_real_files_as_they_are(promote):
    """The repository's own Fleet files, consumer and catalog item pass every check of the step."""
    repo = promote({f: (ROOT / f).read_text() for f in (HOST, ROBOTS, CONSUMER, CATALOG)},
                   image_ref=f"quay.io/jary/soarm-act-modelcar@{NEW}")
    assert repo.refused is None and set(repo.trees[0]) == {HOST, ROBOTS, CONSUMER, CATALOG}
    assert all(NEW in repo.trees[0][f] for f in (HOST, ROBOTS))


def test_the_real_fleet_files_pin_the_same_model_and_runtime_image():
    """gitops/rhem: both Fleets agree on the modelcar digest, MODEL_VERSION and the runtime image digest."""
    pins = {}
    for f in (HOST, ROBOTS):
        text = (ROOT / f).read_text()
        modelcar = re.findall(r"soarm-act-modelcar@(sha256:[0-9a-f]{64})", text)
        version = re.findall(r"^\s+MODEL_VERSION:\s*(\S+)", text, re.M)
        runtime = set(re.findall(r"soarm-flywheel@(sha256:[0-9a-f]{64})", text))
        assert (len(modelcar), len(version), len(runtime)) == (1, 1, 1), f
        pins[f] = (modelcar[0], version[0], runtime.pop())
    assert pins[HOST] == pins[ROBOTS]
