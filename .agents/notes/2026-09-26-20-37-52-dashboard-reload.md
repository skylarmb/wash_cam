# Default dashboard reload

Replaced the standard-library WSGI server with Werkzeug's development server. `wash-cam dashboard` enables the source reloader by default. The host and port remain 127.0.0.1:8000. The interactive debugger remains disabled. Browser refresh is separate, so server reload does not discard labeling inputs. Added Werkzeug to the dependencies and updated the lockfile, README, and dashboard briefing.

Red: `uv run --locked pytest tests/test_dashboard.py::test_dashboard_reloads_by_default -q --tb=short` failed because the CLI did not start the reloading server. Green: the same test passed after the server change. Updated the existing dashboard fixture to intercept Werkzeug without sockets or child processes. Removed the temporary old-server stub after the red check.

Final checks: `uv run --locked pytest -q` passed all 96 tests. `ruff check .`, `ruff format --check .`, `uv build --quiet`, and `git diff --check` passed. The deterministic test checks CLI server settings. No live process restart test was run.
