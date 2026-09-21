<!-- This project was developed with assistance from AI tools. -->
# hp-roscon-flywheel — notes for an agent or a contributor

## What this is

One HP ZGX Fury workstation (NVIDIA GB300, aarch64, RHEL 10): its GPU split into four MIG slices
with one tenant each, a single-node OpenShift hub in a VM on the same machine, and Red Hat Edge
Manager managing the GPU host and twelve RHEL image mode micro-VM robots. Everything is delivered by
GitOps, and images are signed and verified on the devices. Read [`README.md`](README.md) first, then
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/DATA-FLOW.md`](docs/DATA-FLOW.md).
[`gitops/README.md`](gitops/README.md), [`argocd/README.md`](argocd/README.md) and
[`tools/README.md`](tools/README.md) index the manifests and the scripts.

## A running system follows this repository

The hub's Argo CD Applications and Edge Manager's ResourceSyncs track a branch of this repository
(`targetRevision` in `argocd/*.yaml` and `rhem/bootstrap/*.yaml`). A pushed change to a tracked path
is a change to the running system.

**Paths that must not move or be renamed** - the running system and its promotion machinery refer to
them by path:

- `gitops/*` - a directory there is the source of an Argo CD Application (`argocd/*.yaml`), by path
- `gitops/rhem/`, `gitops/rhem-catalog/` - rendered by Edge Manager, which takes **every** `.yaml`,
  `.yml` and `.json` file it finds there as a resource: add no file to them; the Fleet files' model
  digest and `MODEL_VERSION` are edited by promotion pull requests, not by hand
- `gitops/flywheel/manifest-consumer.yaml` - the training trigger; a promotion edits its lineage
  variables, and the promotion reset reverts that commit
- `devfile.yaml` - the coding workspace starts from it
- `tools/host/fury/**` - the GPU host runs copies of these scripts and units

**Promotions.** A promotion is a pull request from a branch named `promote/...`, merged by a human.

- Never start another branch's name with `promote/`: `tools/hub/reset-promotion.sh` finds the newest
  promotion by that prefix in the merge commit.
- Merge a promotion pull request with a **merge commit** - not squash, not rebase. The reset reverts
  that merge commit.

## Rules when editing

- **AI-assistance comment in every file**, near the top, in the file's comment syntax:
  `This project was developed with assistance from AI tools.` (`<!-- ... -->` in Markdown, `# ...` in
  YAML, shell and Python).
- **Nothing about the legal terms this repository is offered under**: no such file, header, metadata
  field or sentence. That decision belongs to people, never to a contribution - and the
  documentation lint (`tools/lint/`) refuses the usual word for it, which is why it is not used here.
- **Roles, not names.** Never name a person in code, comments or documents, and no usernames or
  personal handles: "the demo owner", "a presenter", "the operator". The registry identifier in
  image references is the one accepted exception; add no prose around it.
- **"Object storage"** in audience-facing text: documents, page labels, diagram labels. The storage
  product's name appears only where it is a real identifier - a Secret, a namespace, a variable, a
  hostname - in a code span or a manifest.
- **No secrets** in any file: no tokens, passwords or keys. Hand-created Secrets are listed by name
  and key in `argocd/README.md`.
- **No host addresses of the lab's uplink or tailnet, and no login names**, in anything new.
- **Say what runs, and only what was measured.** Do not claim vendor support for a configuration.
  The episodes an audience watches live are judged, not kept, and did not train the promoted model
  (`docs/DATA-FLOW.md`).
- Python: pin dependencies with upper bounds; short one-line docstrings; test servers bind to
  `127.0.0.1`.

## Tests

pytest suites by component, under `tests/`: `eval_dashboard`, `fleet_renderer`, `fleet_world`,
`host_runner`, `hub`, `pipeline`, `robot_zero`, `tenant_metrics`. Run a suite from the repository
root:

```sh
python -m pytest tests/eval_dashboard -q
```

Where a suite has a `conftest.py`, it puts the source folder (`src/<component>/`, `tools/fleet/`) on
the import path, so nothing is installed as a package. What a suite needs besides pytest: `eval_dashboard` -
`src/eval-dashboard/requirements-dev.txt`; `fleet_world` - `tools/fleet/requirements.txt`;
`fleet_renderer` and `robot_zero` - numpy; `hub` - PyYAML. `hub`, `robot_zero` and `tenant_metrics`
also read manifests, units, scripts and documents **by path**: run them after moving or rewording any
of those.

Run the suites that cover what you changed before proposing it, and say which you ran.
