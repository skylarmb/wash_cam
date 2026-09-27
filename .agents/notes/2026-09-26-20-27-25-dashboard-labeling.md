# Dashboard labeling

Added Live view and Data labeling tabs to the WSGI dashboard. Live view retains its 30-second refresh. The labeling page selects a capture and loads its saved counts into five inputs. Unknown counts remain blank. The page saves all five bays through a JSON API without refreshing. Previous image and Next image follow timestamp filename order. The dropdown remains available. Navigation shows the image position and disables links at either end.

Added GET and POST `/api/labels`. POST validates five nonnegative integers and an existing image. It replaces metadata with `vehicles.bays` entries and updates `verified_at` in one transaction. Connections close after use. Missing storage returns HTTP 503 without creating a database. No migration was required. Updated the README, database documentation, and agent briefings.

Red: `uv run --locked pytest tests/test_dashboard.py -q` produced 9 expected failures for missing routes and 10 passes. The first attempt could not access the uv cache. An elevated rerun reached the tests. Green: the focused command passed all 19 tests after implementation. Added coverage for separate image values, invalid payloads, and unavailable storage. Navigation red: `uv run --locked pytest tests/test_dashboard.py::test_labeling_previous_and_next_images -q` failed because navigation links were absent. The full suite passed after implementation.

Final checks: `uv run --locked pytest -q` passed all 85 tests. `ruff check .`, `ruff format --check .`, `uv build --quiet`, and `git diff --check` passed. The rendered save script passed `node --check`. No browser interaction test was run. Save tests used temporary databases. No sample labels were added to the tracked database.
