# Dashboard network access

Changed the dashboard binding to `0.0.0.0:8000`. Updated the startup message, README, and dashboard briefing.

Red: `uv run --locked pytest tests/test_dashboard.py::test_dashboard_serves_network_with_reload --tb=short -q` failed on the previous localhost binding. Green: the same command passed after the change. The full suite passed all 119 tests. Ruff lint, Ruff format, `uv build`, and `git diff --check` passed after formatting.

The Mac Mini checkout is `/Users/skylar/workspace/wash_cam` on `skylar@10.0.4.25`. Deployment uses `main`. Restart the dashboard after a binding change because the reloader can retain the original listening socket.

Rebased onto the newer `main` commits before deployment. The combined suite passed all 126 tests. Ruff lint, formatting, package build, and diff checks passed again.
