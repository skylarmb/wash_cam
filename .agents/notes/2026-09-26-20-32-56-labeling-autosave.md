# Labeling defaults and navigation saves

Unsaved bay counts now default to zero. Saved values retain priority. Previous image and Next image validate and save all five counts before navigation. Invalid inputs or save failures keep the current image open. The explicit save button remains available. The dropdown behavior is unchanged.

Red: `uv run --locked pytest tests/test_dashboard.py -q --tb=short` reported 7 expected failures and 24 passes. The failures covered zero defaults and missing navigation save handlers. Added a Node script harness that executes the rendered page script with controlled DOM and fetch responses. It verifies both directions, save ordering, invalid input, and server errors. Pytest skips these script tests if Node is unavailable.

Green: `uv run --locked pytest -q` passed all 91 tests, including all six script cases. `ruff check .`, `ruff format --check .`, `uv build --quiet`, and `git diff --check` passed. Updated the user documentation and dashboard briefing. The tracked database was not modified.
