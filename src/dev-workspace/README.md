# Developer workspace image

The container an OpenShift Dev Spaces workspace runs for the coding-agent part of the demo: the UBI Python image
plus a terminal coding agent ([opencode](https://opencode.ai)) wired to the model on the hub, and the evaluation
dashboard's Python requirements, so `python -m pytest tests/eval_dashboard` works the moment the workspace is up.
Nothing is downloaded when a workspace starts or while the agent runs. The workspace itself is `devfile.yaml` at
the repository root.

> [!NOTE]
> This project was developed with assistance from AI tools.

## What is in it

| Piece | Where | Why |
|---|---|---|
| Base | `registry.access.redhat.com/ubi9/python-312` | Pulls without a registry login; already has git, bash, `nohup`, tar and openssl, which is all the Dev Spaces editor asks of an image. The editor brings its own Node.js and libraries. |
| opencode 1.18.23 | `/usr/local/bin/opencode` | The release binary for arm64, checked against a pinned sha256 at build time. |
| ripgrep 15.1.0 | `/usr/local/bin/rg` | opencode downloads exactly this release on first use when `rg` is not on the `PATH`. Baked in, it never has to. Also sha256-checked. |
| Dashboard requirements | the image's Python (`/opt/app-root`) | Installed from `src/eval-dashboard/requirements-dev.txt` itself, which is why the build context is `src/`. |
| Agent configuration | `/opt/opencode/opencode.jsonc`, selected by `OPENCODE_CONFIG` | One provider, the hub's model; no other provider is offered. A project-level `opencode.json` can still override it. |

## Settings baked in as environment

| Variable | Value | Meaning |
|---|---|---|
| `ASSISTANT_BASE_URL` | `http://assistant.flywheel.svc:8000/v1` | The OpenAI-compatible endpoint the agent talks to. The agent configuration reads it, so the devfile can point a workspace elsewhere. |
| `OPENCODE_CONFIG` | `/opt/opencode/opencode.jsonc` | The agent configuration. |
| `OPENCODE_DISABLE_AUTOUPDATE`, `OPENCODE_DISABLE_MODELS_FETCH`, `OPENCODE_DISABLE_LSP_DOWNLOAD`, `OPENCODE_DISABLE_DEFAULT_PLUGINS` | `1` | No self-update, no model catalogue fetch, no language-server or plugin installs. |
| `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` | under `/tmp/xdg` | Where opencode keeps its state. Writable whatever the UID; gone when the workspace restarts, so every start is a clean one. |
| `HOME` | `/home/user` | Group-writable for GID 0, because a workspace runs as an arbitrary UID in the root group. |
| `SHELL` | `/bin/bash` | The shell the editor's terminal opens. |

The agent may edit files in the project and run the tests. Every other shell command is refused, apart from a few
read-only ones (`ls`, `cat`, `head`, `tail`, `wc`, `grep`, `git status`, `git diff`, `git log`), and web access
is off. The rules are the `permission` block of `opencode.jsonc`.

## Build

The hub builds, pushes, signs and verifies it with the same pipeline as the other images:

    tools/hub/build-dev-workspace.sh

It prints the digest to pin in `devfile.yaml`. arm64 only: the pinned checksums are for the arm64 assets, and the
build stops on any other architecture. The build needs internet access (the release binaries and the Python
packages); a running workspace does not.

To try it on an arm64 machine, from the repository root:

    podman build -t dev-workspace -f src/dev-workspace/Dockerfile src
    podman run --rm -it --user 12345:0 --network none --read-only \
        -v "$PWD:/projects/hp-roscon-flywheel" -w /projects/hp-roscon-flywheel dev-workspace bash

`opencode models` lists the one model, `python -m pytest tests/eval_dashboard -q` runs, and neither needs the
network. Mount a clone rather than a git worktree: a worktree's `.git` points at a path outside the mount.

## Updating opencode

Change `OPENCODE_VERSION` and `OPENCODE_SHA256` together. The sha256 is that of `opencode-linux-arm64.tar.gz` in
the release; compute it from the downloaded file and compare it with the digest the release page shows.
opencode uses whichever `rg` is on the `PATH`, so `RIPGREP_VERSION` only moves when there is a reason to. Rehearse
the demo task before pinning the new image: a newer agent is not automatically a better one against this model.
