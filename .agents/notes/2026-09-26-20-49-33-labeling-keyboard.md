# Labeling keyboard workflow

Bay 1 receives focus on each labeling page load. Tab retains native focus behavior and preserves the count. Space increments the focused bay and advances focus. Space on Bay 5 focuses the save button. Repeated Space keydown events and modified shortcuts do not increment counts. Enter submits the labeling form, saves all five counts, and opens the next image. The save button has the same behavior. Next image wraps from the last image to the first. Previous image remains disabled at the first image. Failed saves keep the current image open.

Red: `uv run --locked pytest tests/test_dashboard.py -q --tb=short` reported 7 expected failures and 31 passes. Failures covered missing initial focus and missing last-to-first navigation. Extended the Node harness to check the 0, 0, 1, 1, 0 workflow, Space on Bay 5, submission destinations, validation failures, and save failures.

Green: `uv run --locked pytest -q` passed all 102 tests. `ruff check .`, `ruff format --check .`, `uv build --quiet`, and `git diff --check` passed. Updated README, database documentation, dashboard briefing, and inline keyboard help. Tests execute the rendered script with controlled DOM responses. No live browser test was run.
