# Dashboard capture directory

The dashboard reads camera captures from the checkout's `tmp/images/` directory. Git already ignores `tmp/`. Updated the README and dashboard briefing. Replaced the committed-image test with an isolated temporary checkout fixture. Existing tracked images remain available to evaluation tools.

Red: `uv run --locked pytest tests/test_dashboard.py::test_serves_captures_from_any_working_directory --tb=short -q` failed with HTTP 404. Green: the same command passed after the path change. All 126 tests, Ruff lint, Ruff format, `uv build`, and `git diff --check` passed.
