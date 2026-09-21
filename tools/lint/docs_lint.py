#!/usr/bin/env python3
# This project was developed with assistance from AI tools.
"""Deterministic documentation lint: standard library only, no model, no network. See tools/lint/README.md."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path
from urllib.parse import unquote

MARKER = "This project was developed with assistance from AI tools."
SALT = "docs-lint/v1:"
EXTRA_HASHES_ENV = "DOCS_LINT_EXTRA_HASHES"  # comma-separated extra digests: the hook the tests use
# Salted SHA-256 digests of lower-cased forbidden name tokens. Digests only: the scanner names nobody.
FORBIDDEN_HASHES = frozenset({
    "1b0705476fb9b172bd401d1a8861e967142544a5a4c4facc22577fb90bac8359",
    "1c7d7c5e07f29a7752844c86a01887abd718ca3e1de8da7d0a1c15d0916db6d8",
    "2e204da8bbc0010fcc1122b7f9e8476c1c2d2c74d5b1ff60d2f8cb2ce726cdac",
    "31ed82c48f345cfb06375badfbad16bd621e460a8f48e694fc491356d861f5b5",
    "35744a7a8fac1007f0c5f853a00be6de06522f3b57a711c8241c57e81cb33955",
    "4ce5ed9f38fe80d8cee5cea9bff0ac382f204c8d257671f35aa96d948c9e11d3",
    "7b6d51692eca994d3ea6a3a856bc5821222ecc1eaf69fe4313e0f86a90a518aa",
    "8db742dc2bbacd247b082d0d1474f4ca2233dbdf3e6b8353dab154bd72429424",
    "b5d20b5b63ef632aead8d6bf2d9bdc11ea8237a9c66d07a68536d92602f37c57",
    "bbfa0e62cc7abbd1d3af89d8cbd5b687fbc2587f5d06f6193114a12112abbd60",
    "bed285af74bfd73d901123f2aa30a6b87e09c5d48dc46330f945c54a52974ae3",
    "e9f8bf9221ab09fb4fdf2924d7ded9843f0f582d1fbf1c47f3fc0a13cafdfbb1",
    "f0257a8dcb8762d3589a665cc4b5fd7b941615d6cd56a65690b394cdd9cfcc31",
    "f4dd2addcb19ddf860eae091037d3060f79b253d8e98881bf83f77e78d995188",
    "fbee2daefe19a1ae9736ece2584d9a7a1f1d1a8f562cfaa31e40c082fcc38ed9",
})

# Exemptions, per rule: (path glob, line substring). An empty substring exempts the whole file.
OWN = [("tools/lint/**", ""), ("tests/docs/**", "")]  # the lint's own files must spell the patterns they look for
EXEMPT: dict[str, list[tuple[str, str]]] = {
    "names": [],  # an ordinary word that collides with a forbidden token: exempt it by path and phrase
    "username": OWN + [  # desktop-era scripts, not ported yet: deferred work
        ("device/enroll.sh", ""), ("device/vm/bags-share.sh", ""),
        ("device/vm/create-vm.sh", ""), ("device/vm/cloud-init/user-data", "")],
    "storage-product": [],
    "licence": OWN,  # a genuine operational caution that needs a pattern word goes here, by path and phrase
    "ai-note": [  # recorded artefacts and generated files; JSON and requirements*.txt are never checked
        ("docs/eval-records/**", ""), ("docs/demo-kit/**", ""), ("pipeline/act_flywheel_pipeline.yaml", "")],
    "links": [  # documents on the exclude list are skipped as a whole: their old paths never reach main
        ("**", "docs/user/"),  # paths inside the upstream Edge Manager source tree, not this repository
        ("**", "tests/eval_dashboard/test_demo_task.py"),  # lives on the coding-task demo branch only
        ("rhem/bootstrap/README.md", "rhem-config")],  # files the reader would create for a workaround
    "digests": [
        ("gitops/flywheel/manifest-consumer.yaml", ":latest"),  # deferred: the promotion machinery owns this file
        ("gitops/tekton/cosign-sign-task.yaml", "registry/repo@sha256:...")],  # a parameter description
    "main-only": [],
}
AUDIENCE = ("README.md", "AGENTS.md", "docs/*.md", "docs/internal/FURY-URLS.md", "docs/internal/README.md",
            "gitops/README.md", "tools/README.md", "src/**/*.html", "src/**/*.js")
DIGEST_SCOPE = ("gitops/**", "argocd/**", "rhem/**", "devfile.yaml", "tools/host/fury/flywheel/*.container")
NOTE_SUFFIXES = (".md", ".py", ".sh", ".html", ".container", ".volume", ".service")
PATH_SUFFIXES = (".md", ".sh", ".py", ".yaml", ".yml", ".json", ".container", ".html", ".txt")
TOP_DIRS = ("docs", "tools", "gitops", "argocd", "src", "tests", "rhem", "device", "pipeline", "docker")
SKIP_DIRS = ("__pycache__", "node_modules")

WORD = re.compile(r"[A-Za-z][A-Za-z'-]+")
CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+")
USER = re.compile(r"(?<![A-Za-z0-9])jary(?![A-Za-z0-9])", re.IGNORECASE)
USER_OK = re.compile(r"quay\\?\.io(?:\\?/repository)?\\?/jary\b|quay-jary", re.IGNORECASE)
PRODUCT = re.compile(r"minio", re.IGNORECASE)
URL = re.compile(r"https?://[^\s)>\]]+")  # a hostname is an identifier, not prose
LICENCE_NAME = re.compile(r"(LICEN[CS]E|COPYING|NOTICE)", re.IGNORECASE)
LICENCE_LINE = re.compile(r"\blicen[cs]e|\bSPDX-|\bcopyright\b|Apache-2|MIT License", re.IGNORECASE)
LICENCE_KEY = re.compile(r"\"licen[cs]es?\"\s*:", re.IGNORECASE)
LINK = re.compile(r"\]\(\s*<?([^)\s>]+)")
SPAN = re.compile(r"(`+)(.+?)\1")
PLACEHOLDER = re.compile(r"sha256:\s*(TODO|<[^>]*>|0{64})|PLACEHOLDER|^\s*-?\s*image\s*[:=].*<[^>]+>", re.IGNORECASE)
CITATION = re.compile(r"\bD[0-9]{3}\b|DECISIONS\.md|FURY-PLAN")


@dataclass(frozen=True)
class Finding:
    """One rule violation at one line of one file."""
    path: str
    line: int
    rule: str
    message: str


@cache
def glob_re(pattern: str) -> re.Pattern[str]:
    """Compile a path glob in which `*` stays inside one segment and `**` crosses segments."""
    table = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}
    return re.compile("".join(table.get(p, re.escape(p)) for p in re.split(r"(\*\*/|\*\*|\*|\?)", pattern)) + r"\Z")


def matches(path: str, patterns: Sequence[str]) -> bool:
    """True when the path, or the directory it names, matches any glob."""
    return any(glob_re(p).match(path) or glob_re(p).match(path.rstrip("/") + "/") for p in patterns)


@cache
def read_lines(path: Path) -> tuple[str, ...] | None:
    """The lines of a text file, or None when it is binary, missing or unreadable."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return None if b"\0" in data[:8192] else tuple(data.decode("utf-8", errors="replace").splitlines())


def token_hash(token: str) -> str:
    """The salted digest a lower-cased token is compared by."""
    return hashlib.sha256((SALT + token.lower()).encode()).hexdigest()


def pieces(line: str) -> list[str]:
    """Letter-only tokens of a line: each word split on non-letters, plus the parts of camelCase words."""
    out: list[str] = []
    for word in WORD.findall(line):
        for piece in re.split(r"[^A-Za-z]+", word):
            out += [piece] + CAMEL.findall(piece)
    return [p.lower() for p in out if len(p) > 1]


def prose(rel: str, lines: Sequence[str]) -> list[str]:
    """Lines with code blanked: fences and code spans in Markdown, <pre> and <code> in HTML."""
    if rel.endswith(".html"):
        text = re.sub(r"(?is)<(pre|code)\b.*?</\1>", lambda m: "\n" * m.group(0).count("\n"), "\n".join(lines))
        return text.split("\n")
    if not rel.endswith(".md"):
        return list(lines)
    out, fenced = [], False
    for line in lines:
        fence = re.match(r"\s*(```|~~~)", line) is not None
        out.append("" if fenced or fence else line)
        fenced = fenced != fence
    return out


def path_token(token: str, root: Path, rel: str, need_slash: bool = True) -> str | None:
    """The backticked token as a repo path candidate, or None when it does not look like one."""
    tok = re.sub(r":\d+(-\d+)?$", "", token.strip().rstrip(".,;:"))
    if not re.fullmatch(r"[A-Za-z0-9_.+/-]+", tok) or tok.startswith(("/", "./")) or "..." in tok:
        return None  # wildcards, <placeholders>, $VARS, ~, spaces and URLs fail the character set; ./x is a command
    if "/" not in tok:
        return None if need_slash or not tok.endswith(PATH_SUFFIXES) else tok
    first = tok.split("/")[0]
    if first in TOP_DIRS or (root / first).is_dir():
        return tok
    beside = (root / posixpath.dirname(rel) / first).exists()  # other first segments belong to other trees
    return tok if beside and tok.endswith(PATH_SUFFIXES) else None


def refs(rel: str, lines: Sequence[str], root: Path, need_slash: bool = True) -> Iterator[tuple[int, str, str]]:
    """Yield (line, kind, target) for relative Markdown links and backticked repo paths outside fences."""
    for no, line in enumerate(prose(rel, lines), 1):
        for target in LINK.findall(SPAN.sub(" ", line)):
            if not re.match(r"[A-Za-z][A-Za-z0-9+.-]*:|#", target):
                yield no, "link", unquote(re.split(r"[#?]", target)[0])
        for _, token in SPAN.findall(line):
            candidate = path_token(token, root, rel, need_slash)
            if candidate:
                yield no, "path", candidate


def resolve(rel: str, kind: str, target: str) -> list[str]:
    """Repo-relative spellings of a reference: links resolve from the file, backticked paths from either."""
    here = posixpath.normpath(posixpath.join(posixpath.dirname(rel), target))
    if target.startswith("/"):
        return [target.lstrip("/")]
    return [here] if kind == "link" else [posixpath.normpath(target), here]


def rule_names(root: Path, files: list[str], opts: argparse.Namespace) -> Iterator[Finding]:
    """Rule 1: no individual's name, compared by salted hash so the scanner itself names nobody."""
    forbidden = FORBIDDEN_HASHES | set(opts.extra_hashes)
    seen: dict[str, str] = {}
    for rel in files:
        for no, line in enumerate((rel,) + (read_lines(root / rel) or ())):  # line 0 is the path itself
            for piece in sorted(set(pieces(line))):
                digest = seen.setdefault(piece, token_hash(piece))
                if digest in forbidden:
                    yield Finding(rel, max(no, 1), "names", f"forbidden name token (salted hash {digest[:8]})")


def rule_username(root: Path, files: list[str], opts: argparse.Namespace) -> Iterator[Finding]:
    """Rule 2: the personal handle appears only inside the registry identifier."""
    for rel in files:
        for no, line in enumerate(read_lines(root / rel) or (), 1):
            if USER.search(USER_OK.sub(" ", line)):
                yield Finding(rel, no, "username", "personal handle outside the registry identifier")


def rule_storage_product(root: Path, files: list[str], opts: argparse.Namespace) -> Iterator[Finding]:
    """Rule 3: audience-facing prose says "object storage", never the product's name."""
    for rel in (f for f in files if matches(f, AUDIENCE)):
        for no, line in enumerate(prose(rel, read_lines(root / rel) or ()), 1):
            if PRODUCT.search(URL.sub(" ", SPAN.sub(" ", line) if rel.endswith(".md") else line)):
                yield Finding(rel, no, "storage-product", 'storage product named in prose: say "object storage"')


def rule_licence(root: Path, files: list[str], opts: argparse.Namespace) -> Iterator[Finding]:
    """Rule 4: no licence file, header, metadata field or sentence."""
    for rel in files:
        base = posixpath.basename(rel)
        if LICENCE_NAME.match(base):
            yield Finding(rel, 1, "licence", "licence-style file name")
        for no, line in enumerate(read_lines(root / rel) or (), 1):
            if base == "package.json" and LICENCE_KEY.search(line):
                yield Finding(rel, no, "licence", 'package.json carries a "license" key')
            elif LICENCE_LINE.search(line):
                yield Finding(rel, no, "licence", "licensing wording")


def rule_ai_note(root: Path, files: list[str], opts: argparse.Namespace) -> Iterator[Finding]:
    """Rule 5: the README callout, and the marker sentence near the top of every authored file."""
    suffixes = NOTE_SUFFIXES + ((".yaml", ".yml") if opts.yaml_note else ())
    for rel in files:
        lines = read_lines(root / rel)
        if lines is None or not (rel.endswith(suffixes) or posixpath.basename(rel).startswith("Dockerfile")):
            continue
        if posixpath.basename(rel) == "__init__.py" and not "".join(lines).strip():
            continue
        header = next((i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("#")), 0)
        if MARKER not in "\n".join(lines[:max(15, header)]):
            yield Finding(rel, 1, "ai-note", "marker sentence missing from the first 15 lines and the header comment")
        if rel == "README.md":
            text = [line.rstrip() for line in lines]
            h1 = next((i for i, line in enumerate(text) if line.startswith("# ")), len(text))
            h2 = next((i for i, line in enumerate(text) if line.startswith("## ")), len(text))
            note = next((i for i in range(len(text) - 1) if text[i:i + 2] == ["> [!NOTE]", "> " + MARKER]), -1)
            summary = any(line and line[0] not in "#><" for line in text[h1 + 1:max(note, 0)])
            if not (h1 < note < h2 and summary):
                yield Finding(rel, 1, "ai-note", "the [!NOTE] callout must follow the H1 and the summary paragraph")


def rule_links(root: Path, files: list[str], opts: argparse.Namespace) -> Iterator[Finding]:
    """Rule 6: relative Markdown links and backticked repo paths must exist."""
    for rel in (f for f in files if f.endswith(".md") and not matches(f, opts.exclude)):
        for no, kind, target in refs(rel, read_lines(root / rel) or (), root):
            spellings = resolve(rel, kind, target)
            if opts.main and any(matches(s, opts.exclude) for s in spellings):
                continue  # reported by main-only instead
            if not any((root / s).exists() for s in spellings):
                yield Finding(rel, no, "links", f"dangling reference: {spellings[0]}")


def rule_digests(root: Path, files: list[str], opts: argparse.Namespace) -> Iterator[Finding]:
    """Rule 7: deployable image references carry a real 64-hex digest: no placeholder, no floating tag."""
    for rel in (f for f in files if matches(f, DIGEST_SCOPE) and not f.endswith(".md")):
        for no, line in enumerate(read_lines(root / rel) or (), 1):
            code = re.sub(r"(^|\s)#.*$", "", line).rstrip()
            digests = re.findall(r"@sha256:([0-9A-Za-z]*)", code)
            if any(not re.fullmatch(r"[0-9a-f]{64}", d) for d in digests):
                yield Finding(rel, no, "digests", "@sha256: is not followed by exactly 64 hex characters")
            if PLACEHOLDER.search(code):
                yield Finding(rel, no, "digests", "placeholder where an image digest belongs")
            if re.search(r":latest[\"']?$", code):
                yield Finding(rel, no, "digests", "image reference floats on :latest")


def rule_main_only(root: Path, files: list[str], opts: argparse.Namespace) -> Iterator[Finding]:
    """Rule 8 (--main): excluded paths are absent, unreferenced, and their decision log is not cited."""
    bare = {posixpath.basename(p) for p in opts.exclude if "*" not in p}
    for pattern in opts.exclude:
        present = [f for f in files if matches(f, [pattern])]
        if present:
            message = f"excluded from main but present: {pattern} ({len(present)} files)"
            yield Finding(present[0], 1, "main-only", message)
    for rel in (f for f in files if not matches(f, opts.exclude)):
        lines = read_lines(root / rel) or ()
        for no, kind, target in refs(rel, lines, root, need_slash=False) if rel.endswith(".md") else ():
            named = [s for s in resolve(rel, kind, target) if matches(s, opts.exclude)]
            if named or target in bare:
                yield Finding(rel, no, "main-only", f"names a path excluded from main: {(named or [target])[0]}")
        for no, line in enumerate(lines if matches(rel, AUDIENCE) else (), 1):
            if CITATION.search(line):
                yield Finding(rel, no, "main-only", "cites the decision log or plan, which main does not carry")


Rule = Callable[[Path, list[str], argparse.Namespace], Iterator[Finding]]
RULES: dict[str, Rule] = {
    "names": rule_names, "username": rule_username, "storage-product": rule_storage_product,
    "licence": rule_licence, "ai-note": rule_ai_note, "links": rule_links, "digests": rule_digests,
    "main-only": rule_main_only,
}


def list_files(root: Path, use_git: bool) -> list[str]:
    """Tracked files from git, or every file under the root when git is not used or not available."""
    if use_git:
        try:
            out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
            return sorted(p for p in out.decode().split("\0") if p and (root / p).is_file())
        except (OSError, subprocess.CalledProcessError):
            pass
    found: list[str] = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        found += [(Path(base) / n).relative_to(root).as_posix() for n in names]
    return sorted(found)


def exempt(root: Path, finding: Finding) -> bool:
    """True when the finding's path and line text match an entry of its rule's exemption list."""
    lines = read_lines(root / finding.path) or ()
    text = lines[finding.line - 1] if 0 < finding.line <= len(lines) else ""
    return any(glob_re(g).match(finding.path) and phrase in text for g, phrase in EXEMPT.get(finding.rule, []))


def lint(root: Path, main: bool = False, only: str | None = None, yaml_note: bool = False,
         extra_hashes: Sequence[str] = (), exclude_file: Path | None = None, use_git: bool = False) -> list[Finding]:
    """Run the selected rules over the tree and return the findings that no exemption covers."""
    read_lines.cache_clear()
    listed = (exclude_file or Path(__file__).with_name("excluded-from-main.txt")).read_text().splitlines()
    env = [h for h in os.environ.get(EXTRA_HASHES_ENV, "").split(",") if h]
    opts = argparse.Namespace(main=main, yaml_note=yaml_note, extra_hashes=list(extra_hashes) + env,
                              exclude=[s.strip() for s in listed if s.strip() and not s.strip().startswith("#")])
    files = list_files(root, use_git)
    names = [n for n in RULES if (n == only if only else main or n != "main-only")]
    found = {f for n in names for f in RULES[n](root, files, opts) if not exempt(root, f)}
    return sorted(found, key=lambda f: (f.path, f.line, f.rule, f.message))


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry: print the findings, exit 1 when there are any."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="lint this tree by walking it instead of asking git")
    parser.add_argument("--main", action="store_true", help="also run the rules for a tree bound for main")
    parser.add_argument("--only", choices=sorted(RULES), help="run a single rule")
    parser.add_argument("--yaml-note", action="store_true", help="also require the marker sentence in YAML")
    parser.add_argument("--exclude-file", type=Path, help="exclude list to use instead of the one beside this tool")
    parser.add_argument("--json", action="store_true", help="print the findings as a JSON list")
    args = parser.parse_args(argv)
    findings = lint((args.root or Path.cwd()).resolve(), main=args.main or args.only == "main-only", only=args.only,
                    yaml_note=args.yaml_note, exclude_file=args.exclude_file, use_git=args.root is None)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        for f in findings:
            print(f"{f.path}:{f.line}: [{f.rule}] {f.message}")
        print(f"docs-lint: {len(findings)} finding(s)", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
