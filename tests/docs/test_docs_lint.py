# This project was developed with assistance from AI tools.
"""The documentation lint: each rule finds its violation in a small fixture tree and leaves the allowed forms alone."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "lint" / "docs_lint.py"
OWN_FILES = [TOOL, TOOL.with_name("README.md"), TOOL.with_name("excluded-from-main.txt"), Path(__file__)]
INVENTED = "zzyzx"  # a made-up token: the tests never spell a real name
HEX = "a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727"
EXCLUDED = "# fixture exclude list\ndocs/internal/DECISIONS.md\ndocs/records/**\n"

_spec = importlib.util.spec_from_file_location("docs_lint", TOOL)
docs_lint = importlib.util.module_from_spec(_spec)
sys.modules["docs_lint"] = docs_lint
_spec.loader.exec_module(docs_lint)
MARKER = docs_lint.MARKER
README_OK = f"<!-- {MARKER} -->\n# Title\n\nOne summary paragraph.\n\n> [!NOTE]\n> {MARKER}\n\n## Overview\n"


def tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """Write a fixture tree, with its exclude list beside it rather than inside it."""
    (tmp_path / "exclude.txt").write_text(EXCLUDED)
    for rel, text in files.items():
        path = tmp_path / "tree" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path / "tree"


def found(root: Path, rule: str, **options) -> list[tuple[str, int]]:
    """The (path, line) of every finding of one rule, without repeats."""
    findings = docs_lint.lint(root, only=rule, exclude_file=root.parent / "exclude.txt", **options)
    assert all(f.rule == rule for f in findings)
    return sorted({(f.path, f.line) for f in findings})


def run_tool(*args: str, **env: str) -> subprocess.CompletedProcess:
    """Run the tool as a command, the way an operator would."""
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, check=False,
                          cwd=ROOT, env={**os.environ, **env})


def test_every_rule_is_registered_with_an_exemption_list():
    """The registry holds the eight rules and each has a documented exemption list."""
    assert list(docs_lint.RULES) == ["names", "username", "storage-product", "licence", "ai-note", "links",
                                     "digests", "main-only"]
    assert set(docs_lint.EXEMPT) == set(docs_lint.RULES)


def test_names_finds_an_injected_token_in_every_spelling(tmp_path):
    """A forbidden token is found capitalised, possessive, in snake_case, in camelCase and in a path."""
    root = tree(tmp_path, {
        "docs/a.md": f"Ask {INVENTED.title()}.\n{INVENTED}'s page\n{INVENTED}_panel = 1\n"
                     f"class {INVENTED.title()}Panel:\n{INVENTED}ology is another word\n",
        f"docs/{INVENTED}-notes.md": "nothing here\n"})
    extra = [docs_lint.token_hash(INVENTED)]
    assert found(root, "names", extra_hashes=extra) == [("docs/a.md", 1), ("docs/a.md", 2), ("docs/a.md", 3),
                                                        ("docs/a.md", 4), (f"docs/{INVENTED}-notes.md", 1)]
    assert found(root, "names") == []


def test_names_findings_print_a_digest_never_the_word(tmp_path):
    """The report itself names nobody: the message carries a hash prefix."""
    root = tree(tmp_path, {"a.txt": f"{INVENTED}\n"})
    digest = docs_lint.token_hash(INVENTED)
    (finding,) = docs_lint.lint(root, only="names", extra_hashes=[digest])
    assert digest[:8] in finding.message and INVENTED not in finding.message.lower()


def test_names_hook_is_an_environment_variable(tmp_path):
    """Extra digests arrive through DOCS_LINT_EXTRA_HASHES, and the exit code follows the findings."""
    root = tree(tmp_path, {"docs/a.md": f"thanks to {INVENTED}\n"})
    clean = run_tool("--root", str(root), "--only", "names")
    dirty = run_tool("--root", str(root), "--only", "names", DOCS_LINT_EXTRA_HASHES=docs_lint.token_hash(INVENTED))
    assert (clean.returncode, clean.stdout) == (0, "")
    assert dirty.returncode == 1 and re.fullmatch(r"docs/a\.md:1: \[names\] .+\n", dirty.stdout)


def test_names_exemption_is_by_path_and_phrase(tmp_path, monkeypatch):
    """An ordinary word that collides is exempted where it stands, and nowhere else."""
    root = tree(tmp_path, {"docs/a.md": f"take {INVENTED.title()} Road north\n{INVENTED} wrote this\n"})
    monkeypatch.setitem(docs_lint.EXEMPT, "names", [("docs/a.md", "Road north")])
    assert found(root, "names", extra_hashes=[docs_lint.token_hash(INVENTED)]) == [("docs/a.md", 2)]


def test_the_scanner_names_nobody():
    """No token of the lint's own files hashes into its forbidden set."""
    for path in OWN_FILES:
        tokens = {piece for line in path.read_text().splitlines() for piece in docs_lint.pieces(line)}
        assert not {t for t in tokens if docs_lint.token_hash(t) in docs_lint.FORBIDDEN_HASHES}, path.name
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in docs_lint.FORBIDDEN_HASHES)


def test_username_allows_only_the_registry_identifier(tmp_path):
    """The handle passes inside the image reference and its escaped forms, and is found anywhere else."""
    root = tree(tmp_path, {
        "gitops/a.yaml": f"image: quay.io/jary/soarm@sha256:{HEX}\nurl: https://quay.io/repository/jary/soarm\n"
                         "pattern: 'quay\\.io/jary/soarm'\nsed: 's/quay\\.io\\/jary\\/soarm//'\n"
                         "policy: /etc/containers/registries.d/quay-jary.yaml\n# january is only a month\n",
        "tools/b.sh": "scp bag jary@host:\nBAGS=/home/jary/bags\n",
        "device/enroll.sh": "ssh jary@host  # deferred desktop-era script\n"})
    assert found(root, "username") == [("tools/b.sh", 1), ("tools/b.sh", 2)]


def test_storage_product_reads_prose_in_the_audience_facing_set(tmp_path):
    """The product's name is a finding in prose, and allowed in code spans, fences, URLs and everything internal."""
    root = tree(tmp_path, {
        "README.md": "Episodes land in MinIO.\nThe Secret `minio-credentials` holds the key.\n"
                     "```\nmc alias set minio x\n```\n"
                     "Console: https://minio-console.example.test/ is the object storage console.\n",
        "docs/GUIDE.md": "open the minio console\n",
        "docs/internal/NOTES.md": "MinIO may be named in the internal record\n",
        "gitops/minio/minio.yaml": "name: minio\n",
        "src/app/web/static/index.html": "<p>Stored in MinIO</p>\n<code>minio-credentials</code>\n"})
    assert found(root, "storage-product") == [("README.md", 1), ("docs/GUIDE.md", 1),
                                              ("src/app/web/static/index.html", 1)]


def test_licence_finds_files_lines_and_metadata(tmp_path):
    """Licence-style file names, licensing wording and a package.json key are findings; a plain caution is not."""
    root = tree(tmp_path, {
        "LICENSE": "text\n", "docs/COPYING.txt": "text\n",
        "src/a.py": "# SPDX-License-Identifier: X\n# Copyright someone\nx = 1\n",
        "web/package.json": '{\n  "name": "x",\n  "license": "X"\n}\n',
        "tools/host/build.sh": "# Keep those images private - they are not redistributable.\n",
        "tools/lint/notes.md": "how the licence rule works\n",
        "tests/docs/fixture.txt": "MIT License\n"})
    assert found(root, "licence") == [("LICENSE", 1), ("docs/COPYING.txt", 1), ("src/a.py", 1), ("src/a.py", 2),
                                      ("web/package.json", 3)]


def test_licence_exemption_is_by_path_and_phrase(tmp_path, monkeypatch):
    """An operational caution is exempted by its path and phrase; the next line of the same file is not."""
    root = tree(tmp_path, {"tools/host/build.sh": "# the base images are licensed to this host only\n# license: X\n"})
    monkeypatch.setitem(docs_lint.EXEMPT, "licence", [("tools/host/build.sh", "licensed to this host")])
    assert found(root, "licence") == [("tools/host/build.sh", 2)]


def test_ai_note_accepts_marked_and_exempt_files(tmp_path):
    """Marked files, a long marked header, an empty package file, YAML, JSON and recorded artefacts pass."""
    header = "#!/bin/bash\n" + "# a long explanation\n" * 20 + f"# {MARKER}\nset -e\n"
    root = tree(tmp_path, {
        "README.md": README_OK, "src/a.py": f"#!/usr/bin/env python3\n# {MARKER}\n", "src/pkg/__init__.py": "",
        "tools/long.sh": header, "gitops/x.yaml": "kind: X\n", "data.json": "{}\n", "requirements.txt": "x\n",
        "docs/eval-records/run.md": "# recorded\n", "pipeline/act_flywheel_pipeline.yaml": "generated: true\n"})
    assert found(root, "ai-note") == []
    assert found(root, "ai-note", yaml_note=True) == [("gitops/x.yaml", 1)]


def test_ai_note_finds_unmarked_files(tmp_path):
    """A missing marker, or one that only appears after the code has started, is a finding."""
    root = tree(tmp_path, {
        "README.md": README_OK, "src/b.py": "x = 1\n", "src/c.sh": "set -e\n" + "echo\n" * 20 + f"# {MARKER}\n",
        "src/Dockerfile.gpu": "FROM scratch\n", "tools/x.container": "[Container]\n", "docs/page.html": "<p>x</p>\n",
        "src/pkg/__init__.py": "import os\n"})
    assert found(root, "ai-note") == [("docs/page.html", 1), ("src/Dockerfile.gpu", 1), ("src/b.py", 1),
                                      ("src/c.sh", 1), ("src/pkg/__init__.py", 1), ("tools/x.container", 1)]


@pytest.mark.parametrize("body", [
    "# Title\n\nSummary.\n\n## Overview\n",
    f"# Title\n\n> [!NOTE]\n> {MARKER}\n\nSummary.\n",
    f"# Title\n\nSummary.\n\n## Overview\n\n> [!NOTE]\n> {MARKER}\n",
    "# Title\n\nSummary.\n\n> [!NOTE]\n> This project used AI tools.\n",
], ids=["absent", "before-summary", "after-first-section", "reworded"])
def test_ai_note_wants_the_readme_callout_after_the_summary(tmp_path, body):
    """The README callout must be the exact two lines, after the H1 and summary and before the first section."""
    root = tree(tmp_path, {"README.md": f"<!-- {MARKER} -->\n{body}"})
    assert found(root, "ai-note") == [("README.md", 1)]


def test_links_checks_relative_links_and_backticked_paths(tmp_path):
    """Dangling links and paths are findings; anchors, URLs, placeholders, fences and other trees are not."""
    root = tree(tmp_path, {
        "README.md": "# R\n", "docs/B.md": "# B\n", "tools/run.sh": "", "gitops/flywheel/app.yaml": "",
        "docs/A.md": "[ok](B.md#section) [ok](../README.md?plain=1) [web](https://example.test/x.md) "
                     "[mail](mailto:a@example.test) [top](#top) [dir](../tools/)\n"
                     "[gone](GONE.md#section)\n"
                     "See `docs/B.md`, `tools/run.sh:12`, `docs/` and `docs/GONE.md`.\n"
                     "`docs/*.md` `docs/<name>.md` `$HOME/docs/x.md` `~/docs/x.md` `/docs/x.md` `docs/.../x.md` "
                     "`python3 tools/gone.sh` `./gone.sh` `vllm/engine/arg_utils.py` `registries.d/policy.yaml`\n"
                     "```\n[fenced](NOPE.md) `docs/NOPE.md`\n```\n",
        "gitops/README.md": "`flywheel/app.yaml` is here, `flywheel/gone.yaml` is not\n"})
    assert found(root, "links") == [("docs/A.md", 2), ("docs/A.md", 3), ("gitops/README.md", 1)]


def test_links_skips_documents_on_the_exclude_list(tmp_path):
    """An excluded document keeps its historical paths, and on the working branch others may link to it."""
    root = tree(tmp_path, {"docs/internal/DECISIONS.md": "[old](REMOVED.md) and `docs/OLD.md`\n",
                           "docs/A.md": "[log](internal/DECISIONS.md)\n"})
    assert found(root, "links") == []


def test_digests_wants_a_full_digest_and_no_floating_tag(tmp_path):
    """A 64-hex digest passes; short digests, placeholders, all-zero digests and :latest are findings."""
    root = tree(tmp_path, {
        "gitops/app/deploy.yaml": f"image: quay.io/x/y@sha256:{HEX}  # was :latest\nimage: quay.io/x/y@sha256:abc123\n"
                                  f"image: quay.io/x/y@sha256:TODO\nimage: quay.io/x/y@sha256:{'0' * 64}\n"
                                  "image: quay.io/x/y@sha256:<digest>\nimage: WORKSPACE_IMAGE_DIGEST_PLACEHOLDER\n"
                                  "image: registry.example.test/ubi9/python:latest\n"
                                  "# a comment may say :latest and sha256:<hex>\n",
        "gitops/flywheel/manifest-consumer.yaml": "image: registry.example.test/ubi9/python:latest\n"
                                                  "image: x@sha256:abc\n",
        "gitops/app/README.md": "pin it as `image@sha256:<digest>`\n",
        "src/thing.yaml": "image: x/y:latest\n",
        "tools/host/fury/flywheel/sim.container": f"Image=quay.io/x/y@sha256:{HEX}\nImage=quay.io/x/y:latest\n",
        "devfile.yaml": "image: quay.io/x/y@sha256:short\n"})
    assert found(root, "digests") == [
        ("devfile.yaml", 1), *[("gitops/app/deploy.yaml", n) for n in range(2, 8)],
        ("gitops/flywheel/manifest-consumer.yaml", 2), ("tools/host/fury/flywheel/sim.container", 2)]


def test_main_only_runs_only_when_asked(tmp_path):
    """Without --main the excluded paths may exist, be linked and be cited."""
    root = tree(tmp_path, {"README.md": README_OK + "See [the log](docs/internal/DECISIONS.md), D123.\n",
                           "docs/internal/DECISIONS.md": f"<!-- {MARKER} -->\n# Log\n"})
    assert docs_lint.lint(root, exclude_file=tmp_path / "exclude.txt") == []
    assert found(root, "main-only", main=True) == [("README.md", 10), ("docs/internal/DECISIONS.md", 1)]


def test_main_only_finds_excluded_paths_references_and_citations(tmp_path):
    """With --main: one finding per pattern present, per reference to it, and per citation in audience-facing text."""
    root = tree(tmp_path, {
        "README.md": "[log](docs/internal/DECISIONS.md)\nsee `DECISIONS.md`\nruns are under `docs/records/`\n"
                     "as decided in D123\nfollow FURY-PLAN step 4\n[setup](docs/SETUP.md) and `docs/SETUP.md`\n",
        "docs/SETUP.md": "[run](records/a/run.md)\n", "tools/notes.md": "D123 is not audience-facing here\n",
        "src/a.py": "# D123\n", "docs/internal/DECISIONS.md": "D001\n",
        "docs/records/a/run.md": "x\n", "docs/records/b.md": "x\n"})
    findings = docs_lint.lint(root, main=True, only="main-only", exclude_file=tmp_path / "exclude.txt")
    assert sorted({(f.path, f.line) for f in findings}) == [
        *[("README.md", n) for n in range(1, 6)], ("docs/SETUP.md", 1), ("docs/internal/DECISIONS.md", 1),
        ("docs/records/a/run.md", 1)]
    assert any("docs/records/** (2 files)" in f.message for f in findings)


def test_main_only_passes_a_tree_that_stands_on_its_own(tmp_path):
    """With --main a tree without the excluded paths, and without a word about them, is clean."""
    root = tree(tmp_path, {"README.md": "[setup](docs/SETUP.md)\n", "docs/SETUP.md": "`README.md` is the front page\n"})
    assert found(root, "main-only", main=True) == []


def test_main_reports_an_excluded_target_once(tmp_path):
    """In --main mode a link to an excluded path is main-only's finding, not also a dangling link."""
    root = tree(tmp_path, {"tools/A.md": f"<!-- {MARKER} -->\n[log](../docs/internal/DECISIONS.md) [gone](GONE.md)\n"})
    assert found(root, "links") == [("tools/A.md", 2)]
    both = docs_lint.lint(root, main=True, exclude_file=tmp_path / "exclude.txt")
    assert [(f.rule, f.message.split(": ")[-1]) for f in both] == [
        ("links", "tools/GONE.md"), ("main-only", "docs/internal/DECISIONS.md")]


def test_the_shipped_exclude_list_is_well_formed():
    """Every entry is a repository path or a dir/** pattern, and the decision log is on the list."""
    entries = [s.strip() for s in OWN_FILES[2].read_text().splitlines() if s.strip() and not s.startswith("#")]
    assert "docs/internal/DECISIONS.md" in entries and len(entries) == len(set(entries))
    assert all(re.fullmatch(r"[A-Za-z0-9_./-]+?(/\*\*)?", e) and not e.startswith("/") for e in entries)


def test_command_line_output_and_exit_codes(tmp_path):
    """Text output is path:line: [rule] message, --json is a list of the same findings, exit 1 means findings."""
    root = tree(tmp_path, {"README.md": README_OK, "tools/b.sh": f"# {MARKER}\nscp bag jary@host:\n"})
    text = run_tool("--root", str(root))
    data = run_tool("--root", str(root), "--json")
    assert text.returncode == data.returncode == 1
    assert text.stdout == "tools/b.sh:2: [username] personal handle outside the registry identifier\n"
    assert json.loads(data.stdout) == [{"path": "tools/b.sh", "line": 2, "rule": "username",
                                        "message": "personal handle outside the registry identifier"}]
    (root / "tools/b.sh").write_text(f"# {MARKER}\n")
    assert run_tool("--root", str(root)).returncode == 0


@pytest.mark.skipif(os.environ.get("DOCS_LINT_ENFORCE") != "1", reason="set DOCS_LINT_ENFORCE=1 once the tree is clean")
def test_the_repository_is_clean():
    """The real tree passes the lint; the owner switches this on when the documents have settled."""
    result = run_tool()
    assert result.returncode == 0, result.stdout
