<!-- This project was developed with assistance from AI tools. -->
# Documentation lint

`tools/lint/docs_lint.py` checks the repository's content rules with plain pattern matching: no model, no
network, standard library only. A rule that matters gets a scanner, so that it stays true after the day it
was written down.

## Run it

From the repository root:

```sh
python3 tools/lint/docs_lint.py                 # every rule except main-only, over the tracked files
python3 tools/lint/docs_lint.py --main          # plus the rules for a tree that is bound for main
python3 tools/lint/docs_lint.py --only links    # one rule
python3 tools/lint/docs_lint.py --json          # findings as a JSON list, for other tools
python3 tools/lint/docs_lint.py --root PATH     # lint another tree by walking it (no git)
```

Exit code 0 means clean, 1 means findings. Each finding prints as `path:line: [rule] message`. The file set is
`git ls-files`; without git, or with `--root`, the tree is walked instead.

There is deliberately no CI workflow. The lint's tests run in the existing pytest run
(`tests/docs/test_docs_lint.py`), and the tool is run by hand before a merge to `main`. The test that lints the
real repository is skipped until `DOCS_LINT_ENFORCE=1` is set: switch it on once the tree is clean.

## Rules

| Rule | Enforces | Why |
|---|---|---|
| `names` | No individual's name in any tracked text file or path | Documents speak of roles, never of people |
| `username` | The personal handle appears only inside the registry identifier (`quay.io/jary`, `quay-jary`) | No usernames or handles; the image reference is the one accepted identifier |
| `storage-product` | Audience-facing prose never matches `minio`; code spans, fenced blocks and URLs may | On stage and on the page it is "object storage"; the product's name is only an identifier |
| `licence` | No licence-style file name, no licensing wording in any line, no `"license"` key in a `package.json` | Licensing is a human decision that this repository does not make |
| `ai-note` | The README callout after its H1 and summary; the marker sentence at the top of authored files | AI assistance is disclosed in every file |
| `links` | Relative Markdown links and backticked repository paths exist | A document must not point at something that is not there |
| `digests` | Deployable image references carry a full 64-hex digest: no placeholder, no `:latest` | What runs is pinned |
| `main-only` | With `--main`: excluded paths are absent, unreferenced, and the decision log is not cited | `main` has to stand on its own |

Details that matter when a finding surprises you:

- **names** compares salted SHA-256 digests of lower-cased word tokens (words are also split on non-letters and
  at camelCase boundaries), so the scanner itself names nobody, and a finding prints a digest prefix rather
  than the word. To forbid another token, add its digest to `FORBIDDEN_HASHES`: compute it with `token_hash()`
  in a throwaway Python session and paste only the hex. `DOCS_LINT_EXTRA_HASHES` (comma-separated digests) adds
  tokens for one run; the tests use it with an invented word. Two forbidden tokens are also ordinary English
  words (digest prefixes `4ce5ed9f` and `1c7d7c5e`), and a third (`35744a7a`) is short enough to collide with a
  fragment of a hyphenated word. None occurs in the tree today. If one ever appears legitimately, exempt that
  path and phrase under `names` in `EXEMPT`; do not remove the digest.
- **storage-product** reads the audience-facing set only: the root README, the agent guide, `docs/*.md`, the
  two front pages under `docs/internal/`, the top-level READMEs of the GitOps and tools trees, and the `*.html`
  and `*.js` under `src/`. Manifests, code, identifiers and the internal record may use the name.
- **ai-note** wants the sentence within the first 15 lines, or anywhere in a longer leading `#` comment header.
  It checks `.md .py .sh .html .container .volume .service` and every `Dockerfile*`; JSON and requirements
  files are never checked. YAML is opt-in with `--yaml-note`, off by default, because adding comments to the
  GitOps manifests is deferred work: the running system follows those files.
- **links** skips fenced code blocks, and skips backticked tokens with wildcards, `<placeholders>`, `$VARS`,
  `...`, a leading `~`, `/` or `./` (a command in the reader's working directory), or a space. A token counts
  as a repository path when its first segment is a top-level directory, or when it ends in a known extension
  and its first segment exists beside the document; anything else is taken to belong to another source tree.
  Documents on the exclude list are skipped as a whole, because their historical paths never reach `main`.
- **digests** reads the GitOps, Argo CD and Edge Manager trees, the devfile and the host's flywheel container
  units, ignoring comments and Markdown.

## Exemptions

Every exemption is an explicit entry in `EXEMPT` at the top of the tool: `rule -> [(path glob, line substring)]`.
An empty substring exempts the whole file; otherwise only lines containing the substring are exempt. In a glob,
`*` stays inside one path segment and `**` crosses segments. To add one, add the narrowest entry that works and
a comment saying why. Today's entries:

- `username`: four desktop-era device scripts that are not ported yet (deferred work).
- `licence`: only the lint's own files, which must spell the pattern words. The tree's operational cautions
  about redistribution do not match the pattern; one that ever needs a pattern word gets a path and phrase here.
- `ai-note`: recorded artefacts and the generated pipeline definition.
- `links`: paths that live in the upstream Edge Manager source tree, one test file that lives on the
  coding-task demo branch, and two files a reader would create for a workaround in the Edge Manager bootstrap
  notes.
- `digests`: `gitops/flywheel/manifest-consumer.yaml` keeps one `:latest` image for now, because the promotion
  machinery depends on that file and it must not be edited (deferred); and a Tekton parameter description that
  spells a digest with an ellipsis.

The lint's own files (`tools/lint/**`, `tests/docs/**`) are exempt from `username` and `licence` for the same
reason.

## What `--main` is for

`main` is the working branch minus the paths in `tools/lint/excluded-from-main.txt` (one path or `dir/**`
pattern per line, `#` for comments). `--main` answers "would this tree stand on its own there?":

1. none of the listed paths exists in the tree (one finding per pattern);
2. no remaining Markdown file links to a listed path or names one in backticks;
3. in the audience-facing set, nothing cites the decision log: the pattern `\bD[0-9]{3}\b`, or the file names
   of the decision log and the plan (`CITATION` in the tool).

On the working branch the first check always reports, since the excluded files live there; the second and
third show what still has to change before the merge. Without `--main` none of this runs.
