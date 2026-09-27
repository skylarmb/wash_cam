# Zero-indexed bays and label reset

Changed bay labels, input IDs, and saved metadata IDs to 0 through 4. The first bay is `bay_0`. The last bay is `bay_4`. Updated active documentation and keyboard test descriptions. Changed the session storage key so a refreshed labeling page ignores remembered counts from before the reset.

Deleted all rows from `db/labels.sqlite3` at the user's request. The database contained two records before deletion and zero afterward. Ran VACUUM. The schema and migration version remain unchanged. `PRAGMA user_version` returned 1. `PRAGMA integrity_check` returned ok. The user must refresh an already-open labeling page to load the new IDs and defaults.

Red: `uv run --locked pytest tests/test_dashboard.py -q --tb=short` reported 2 expected failures and 42 passes. Failures covered the first input ID and loading saved `bay_0` counts. Green: `uv run --locked pytest -q` passed all 108 tests. `ruff check .`, `ruff format --check .`, `uv build --quiet`, and `git diff --check` passed.
