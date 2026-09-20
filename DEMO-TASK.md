<!-- This project was developed with assistance from AI tools. -->
# Task

The tests in `tests/eval_dashboard/test_demo_task.py` fail because a feature is missing. They ask the evaluation
dashboard for a number an operator of the collection loop cares about: how long was a policy stuck failing before
it recovered?

Build it:

1. In `src/eval-dashboard/eval_dashboard/aggregate.py`, add a function `longest_failure_streak(records)` that
   returns the longest run of consecutive failed episodes in timestamp order. Episodes that are not scored neither
   extend nor break a streak - the module already has a rule for which episodes those are; reuse it.
2. Add a `longest_failure_streak` field to `VersionStats`, and have `aggregate()` fill it in for each model version.

Rules:

- Read the test file and the existing code first, and match the code's style.
- Do not edit any test.
- Run `python -m pytest tests/eval_dashboard -q` and keep going until every test passes - the whole suite, not
  only the new file.
- Finish by showing `git diff --stat`.
