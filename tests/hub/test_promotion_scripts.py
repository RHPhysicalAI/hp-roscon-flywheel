# This project was developed with assistance from AI tools.
"""reset-promotion.sh and reopen-promotion.sh on a throwaway repository, with gh, flightctl, oc and sleep stubbed."""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HUB = Path(__file__).resolve().parents[2] / "tools" / "hub"
HOST = "gitops/rhem/fleet-act-inference.yaml"
ROBOTS = "gitops/rhem/fleet-robots.yaml"
CONSUMER = "gitops/flywheel/manifest-consumer.yaml"
TEACHER, PROMOTED = ("teacher", "sha256:" + "a" * 64), ("cand", "sha256:" + "b" * 64)
HUB_TOKEN = "stub-token-not-a-real-one"
ORIGINAL_PR = dict(number=7, title="Promote cand (82% -> 92%)", headRefName="promote/cand-9fb233e8",
                   createdAt="2026-09-20T11:29:02Z", body="## Promotion: `cand` replaces `teacher`\n\ngate: PASS")
STUB = """#!{python}
import json, os, sys
d, a = os.environ["STUB_DIR"], sys.argv[1:]
open(d + "/calls.jsonl", "a").write(json.dumps([os.path.basename(sys.argv[0])] + a) + "\\n")
name = os.path.basename(sys.argv[0])
if name == "gh" and a[:2] == ["pr", "view"]: print(open(f"{{d}}/pr-{{a[2]}}.json").read())
elif name == "gh" and a[:2] == ["pr", "create"]:
    open(d + "/gh-create-token.txt", "w").write(os.environ.get("GH_TOKEN", ""))
    print("https://git.example/org/repo/pull/8")
elif name == "oc" and a[2:4] == ["get", "secret"] and os.path.exists(d + "/secret-token.b64"):
    print(open(d + "/secret-token.b64").read(), end="")
elif name == "flightctl" and a[:2] == ["get", "devices"]: print(open(d + "/devices.json").read())
elif name == "flightctl" and a[:2] == ["get", "fleet"]: print(open(d + "/fleet.json").read())
elif name != "sleep": sys.exit(1)
"""

pytestmark = pytest.mark.skipif(not all(shutil.which(t) for t in ("bash", "git", "jq")), reason="needs bash, git and jq")


def _fleet(name: str, pin: tuple[str, str]) -> str:
    return (f"kind: Fleet\nmetadata:\n  name: {name}\nspec:\n  envVars:\n    MODEL_VERSION: {pin[0]}\n"
            f"  content: |\n    Image=registry.example/team/soarm-act-modelcar@{pin[1]}\n")


def _devices(robot_status: str = "UpToDate") -> str:
    def dev(owner, alias, status):
        return {"metadata": {"name": alias + "-0000000000", "owner": owner, "labels": {"alias": alias}},
                "status": {"updated": {"status": status}, "applicationsSummary": {"status": "Healthy"}}}
    return json.dumps({"items": [dev("Fleet/act-inference", "fury", "UpToDate"), dev("Fleet/robots", "fleet-vm-01", "UpToDate"),
                                 dev("Fleet/robots", "fleet-vm-03", robot_status)]})


class Hub:
    """A bare origin, a checkout of it holding one promotion merge that knew only the host's Fleet, and the stubs."""

    def __init__(self, tmp: Path):
        self.origin, self.work, self.stubs, self.scratch = tmp / "origin.git", tmp / "work", tmp / "stubs", tmp / "scratch"
        for d in (self.stubs, self.scratch):
            d.mkdir()
        (tmp / "gitconfig").write_text("[user]\n  name = Test Operator\n  email = operator@example.invalid\n")
        self.env = {"PATH": f"{self.stubs}:{os.environ['PATH']}", "HOME": str(tmp), "TMPDIR": str(self.scratch),
                    "STUB_DIR": str(self.stubs), "GIT_CONFIG_GLOBAL": str(tmp / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
        for name in ("gh", "flightctl", "oc", "sleep"):
            (self.stubs / name).write_text(STUB.format(python=sys.executable)); (self.stubs / name).chmod(0o755)
        (self.stubs / "pr-7.json").write_text(json.dumps(ORIGINAL_PR))
        (self.stubs / "secret-token.b64").write_text(base64.b64encode(HUB_TOKEN.encode()).decode())
        self.env["KUBECONFIG"] = str(tmp / "kubeconfig")
        (self.stubs / "devices.json").write_text(_devices())
        (self.stubs / "fleet.json").write_text(json.dumps({"spec": {"template": {"spec": {"applications": [{"envVars": {"MODEL_VERSION": TEACHER[0]}}]}}}}))
        self.git("init", "-q", "--bare", "-b", "fury", str(self.origin), cwd=tmp)
        self.git("clone", "-q", str(self.origin), str(self.work), cwd=tmp)
        self.commit({HOST: _fleet("act-inference", TEACHER), CONSUMER: "incumbent: teacher\n"}, "the teacher serves")
        self.git("switch", "-q", "-c", "promote/cand-9fb233e8")
        self.commit({HOST: _fleet("act-inference", PROMOTED), CONSUMER: "incumbent: cand\n"}, "Promote cand")
        self.git("switch", "-q", "fury")
        self.merge("promote/cand-9fb233e8", 7, ORIGINAL_PR["title"])
        self.commit({ROBOTS: _fleet("robots", PROMOTED)}, "the robots' Fleet goes live")
        self.git("push", "-q", "origin", "fury")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(["git", *args], cwd=cwd or self.work, env=self.env, check=True, capture_output=True, text=True).stdout

    def commit(self, files: dict[str, str], message: str) -> None:
        for path, text in files.items():
            (self.work / path).parent.mkdir(parents=True, exist_ok=True); (self.work / path).write_text(text)
        self.git("add", "-A"); self.git("commit", "-q", "-m", message)

    def merge(self, branch: str, number: int, title: str) -> None:
        """What merging a pull request with a merge commit leaves on the branch."""
        self.git("merge", "-q", "--no-ff", branch, "-m", f"Merge pull request #{number} from org/{branch.removeprefix('origin/')}", "-m", title)

    def run(self, script: str, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(HUB / script), *args], cwd=self.work, env=self.env, input=stdin, capture_output=True, text=True)

    def refs(self) -> str:
        return self.git("for-each-ref", "--format=%(refname) %(objectname)", cwd=self.origin)

    def pins(self, ref: str = "fury") -> set[tuple[str, str]]:
        """The (MODEL_VERSION, digest) pairs the Fleet files hold on the origin's branch."""
        out = set()
        for f in (HOST, ROBOTS):
            text = self.git("show", f"{ref}:{f}", cwd=self.origin)
            out.add((re.search(r"MODEL_VERSION: (\S+)", text).group(1), re.search(r"@(sha256:\w+)", text).group(1)))
        return out

    def calls(self, tool: str) -> list[list[str]]:
        log = self.stubs / "calls.jsonl"
        return [c[1:] for c in map(json.loads, log.read_text().splitlines() if log.exists() else []) if c[0] == tool]

    def left_clean(self) -> bool:
        """No scratch worktree left behind, and nothing touched in the operator's checkout."""
        return (not list(self.scratch.iterdir()) and len(self.git("worktree", "list").splitlines()) == 1
                and self.git("status", "--porcelain") == "")


@pytest.fixture
def hub(tmp_path) -> Hub:
    return Hub(tmp_path)


def _new_commits(hub: Hub, before: str) -> list[str]:
    old = dict(line.split() for line in before.splitlines())["refs/heads/fury"]
    return hub.git("log", "--format=%s", f"{old}..fury", cwd=hub.origin).splitlines()


def test_reset_leaves_every_fleet_file_on_the_previous_model(hub):
    """A merge that knew only the host's Fleet: the revert plus one commit that brings the robots' file along, in one push."""
    before = hub.refs()
    r = hub.run("reset-promotion.sh", "--yes")
    assert r.returncode == 0, r.stderr
    assert hub.pins() == {TEACHER}
    assert hub.git("show", f"fury:{CONSUMER}", cwd=hub.origin) == "incumbent: teacher\n"
    new = _new_commits(hub, before)
    assert len(new) == 2 and new[1].startswith('Revert "Merge pull request #7') and new[0].startswith(f"Reset: {ROBOTS} follows")
    assert f"did not know {ROBOTS}" in r.stdout and "robots 2/2 UpToDate" in r.stdout
    assert hub.left_clean() and not hub.calls("oc")


def test_reset_pushes_nothing_without_a_yes(hub):
    """Answering no leaves the origin and the checkout as they were."""
    before = hub.refs()
    r = hub.run("reset-promotion.sh", stdin="n\n")
    assert r.returncode == 1 and "nothing was pushed" in r.stderr
    assert hub.refs() == before and hub.left_clean()


def test_both_scripts_refuse_while_a_rollout_is_in_progress(hub):
    """An Updating robot stops the reset and the real reopen, names the device and what to wait for; the dry run only says so."""
    (hub.stubs / "devices.json").write_text(_devices("Updating"))
    before = hub.refs()
    for script, args in (("reset-promotion.sh", ["--yes"]), ("reopen-promotion.sh", ["--open"])):
        r = hub.run(script, *args)
        assert r.returncode == 1 and "fleet-vm-03=Updating" in r.stderr and "Wait until every device" in r.stderr
        assert "fleet-status.sh" in r.stderr
    assert hub.refs() == before and hub.left_clean()
    (hub.stubs / "devices.json").write_text(_devices())
    assert hub.run("reset-promotion.sh", "--yes").returncode == 0
    (hub.stubs / "devices.json").write_text(_devices("OutOfDate"))
    r = hub.run("reopen-promotion.sh")
    assert r.returncode == 0 and "fleet-vm-03=OutOfDate" in r.stderr and "--open refuses" in r.stdout


def test_reopen_refuses_a_promotion_that_is_not_reverted(hub):
    """With the promotion in effect the script stops and names the reset as the step that comes first."""
    before = hub.refs()
    r = hub.run("reopen-promotion.sh", "--open")
    assert r.returncode == 1 and "not reverted" in r.stderr and "tools/hub/reset-promotion.sh" in r.stderr
    assert hub.refs() == before and not [c for c in hub.calls("gh") if c[:2] == ["pr", "create"]] and hub.left_clean()


def test_a_showing_can_be_reset_and_reopened_again_and_says_what_it_is(hub):
    """reset, dry run, reopen, merge, reset, reopen: the branch name, the PR text, what each step changes, and the original PR named throughout."""
    assert hub.run("reset-promotion.sh", "--yes").returncode == 0

    before = hub.refs()
    dry = hub.run("reopen-promotion.sh")
    assert dry.returncode == 0 and "dry run: nothing was pushed" in dry.stdout
    assert hub.refs() == before and hub.left_clean() and not [c for c in hub.calls("gh") if c[:2] == ["pr", "create"]]

    r = hub.run("reopen-promotion.sh", "--open")
    assert r.returncode == 0, r.stderr
    create = [c for c in hub.calls("gh") if c[:2] == ["pr", "create"]]
    assert len(create) == 1
    opts = dict(zip(create[0][2::2], create[0][3::2]))
    branch = opts["--head"]
    assert re.fullmatch(r"promote/cand-reshow-\d{8}T\d{6}Z", branch) and opts["--base"] == "fury"
    assert opts["--title"] == "Promote cand (82% -> 92%) - re-opened for a showing"
    assert opts["--body"].startswith("Re-proposes PR #7 (pipeline run 9fb233e8, 2026-09-20): same signed image and transparency-log entry, "
                                     "same gate record. The pipeline did not run again; nothing was re-measured.\n")
    assert "> ## Promotion: `cand` replaces `teacher`\n> \n> gate: PASS" in opts["--body"]
    assert hub.pins(branch) == {PROMOTED} and hub.pins() == {TEACHER}
    assert hub.git("log", "--format=%s", f"fury..{branch}", cwd=hub.origin).splitlines() == [
        f"Promote cand again for a showing: cherry-pick of {hub.git('log', '--merges', '-1', '--format=%h', 'origin/fury').strip()} (PR #7)"]
    assert hub.left_clean()

    hub.git("fetch", "-q"); hub.git("merge", "-q", "--ff-only", "origin/fury")
    hub.merge(f"origin/{branch}", 8, opts["--title"]); hub.git("push", "-q", "origin", "fury")
    assert hub.pins() == {PROMOTED}
    refused = hub.run("reopen-promotion.sh")
    assert refused.returncode == 1 and "the reset comes first" in refused.stderr

    before = hub.refs()
    assert hub.run("reset-promotion.sh", "--yes").returncode == 0
    assert hub.pins() == {TEACHER}
    assert [s[:31] for s in _new_commits(hub, before)] == ['Revert "Merge pull request #8 f']

    again = hub.run("reopen-promotion.sh")
    assert again.returncode == 0, again.stderr
    assert "| Re-proposes PR #7 (pipeline run 9fb233e8, 2026-09-20)" in again.stdout
    assert "(PR #8), itself a re-opening of PR #7" in again.stdout
    assert ["pr", "view", "8"] not in [c[:3] for c in hub.calls("gh")]


def test_the_pull_request_is_opened_with_the_hubs_token_which_is_never_shown(hub):
    """--open reads the token from the hub's Secret, hands it to the one gh call, and prints it nowhere."""
    assert hub.run("reset-promotion.sh", "--yes").returncode == 0
    r = hub.run("reopen-promotion.sh", "--open")
    assert r.returncode == 0, r.stderr
    assert (hub.stubs / "gh-create-token.txt").read_text() == HUB_TOKEN
    assert [c for c in hub.calls("oc") if c[:5] == ["-n", "flywheel", "get", "secret", "github-token"]]
    assert HUB_TOKEN not in r.stdout + r.stderr and hub.left_clean()


def test_without_the_hubs_token_nothing_is_pushed(hub):
    """A Secret that cannot be read stops --open before the branch is pushed, and names the check."""
    assert hub.run("reset-promotion.sh", "--yes").returncode == 0
    (hub.stubs / "secret-token.b64").unlink()
    before = hub.refs()
    r = hub.run("reopen-promotion.sh", "--open")
    assert r.returncode == 1 and "nothing was pushed" in r.stderr and "oc -n flywheel get secret github-token" in r.stderr
    assert hub.refs() == before and not [c for c in hub.calls("gh") if c[:2] == ["pr", "create"]] and hub.left_clean()

