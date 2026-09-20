<!-- This project was developed with assistance from AI tools. -->
# Demo runbook: a coding agent closes a failing test

This branch is `fury` plus one failing test file, `tests/eval_dashboard/test_demo_task.py`. The tests ask the
evaluation dashboard for a number an operator of the collection loop cares about: **how long was a policy stuck
failing before it recovered?** The feature is missing, so 7 tests fail and the other 342 pass. The agent in the
workspace terminal writes the feature; the model it talks to runs on the same box as the cluster.

## Before the audience arrives

1. Open the workspace and wait for the editor. Open `tests/eval_dashboard/test_demo_task.py` in one pane and a
   terminal in the other.
2. Show the starting point:

       python -m pytest tests/eval_dashboard -q

   Expected last line: `7 failed, 342 passed, 5 skipped`. The 5 skips are tests for fixture files that are not
   part of a clone.
3. Start the agent: type `opencode` in a terminal at the project root.

## The layout

The editor keeps panel positions and sizes in the browser, per workspace, and cannot export them. What this branch
can set up front is in `.vscode/settings.json`: the terminal panel docks on the right of the code, larger fonts,
less chrome. Arrange the rest once - it stays as you left it for as long as you use the same browser and the same
workspace (it never idles out).

Right-click inside the agent's full-screen view belongs to the agent, and a paste by mouse makes the browser show a
one-item menu of its own first. Paste with the keyboard.

## The prompt

Type this, and nothing else:

> Read DEMO-TASK.md and do what it says.

`DEMO-TASK.md` is the task as the agent gets it: what to build, where, and the command that says when it is done.
Keeping it in a file means the prompt on stage is one short line, and the task can be shown to the audience first.

## What to point at while it works

- It reads before it writes: the test file first, then `aggregate.py`. Both show up as tool calls.
- The dashboard already has a rule for episodes that are no verdict on the policy (`not_scored_reason`). Watch
  whether the agent finds it and reuses it instead of writing the rule a second time.
- It runs the test command itself, reads the failures and corrects its own work. Nobody pastes an error back.
- It is finished when the whole suite is green, `349 passed, 5 skipped`, and not only the new file. The existing
  342 tests are what shows it broke nothing.
- It can edit files and run the tests, and nothing else. Any other shell command is refused by the workspace's
  agent configuration, and web access is off. If a refusal appears on screen, that is the guard rail working.
- Nothing leaves the box: the workspace, the agent and the model all run on the hub.

Afterwards, `git diff` in the terminal shows the whole change: one function, one field, a few lines in
`aggregate()`.

## How long it takes

About three minutes. A comparable multi-file task took under three minutes in rehearsal against the same model.
Time this one on the hub during rehearsal and keep talking points for five.

## Reset

The `reset-demo` command, or by hand from the project root:

    git checkout -- src/eval-dashboard tests/eval_dashboard && git clean -fd -- src/eval-dashboard tests/eval_dashboard

That puts back the failing state and removes any file the agent added beside the code or the tests. Leave the
agent with `/exit` and start it again, or type `/new`, so the next run begins with an empty conversation.

## If it goes wrong

- The agent stalls or loops: Ctrl+C, reset, start again. A fresh conversation usually takes a different path.
- The model does not answer: `curl -s "$ASSISTANT_BASE_URL/models"` from the workspace terminal shows whether the
  endpoint is reachable.
- No reference implementation is kept on this branch, on purpose: the agent searches the checkout.
