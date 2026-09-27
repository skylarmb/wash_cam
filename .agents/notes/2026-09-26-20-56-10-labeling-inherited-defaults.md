# Conservative inherited label defaults

Images without a saved database row inherit the last counts successfully saved in the current browser tab. Session storage holds those counts. A fresh session defaults to zero. Invalid or unavailable session storage leaves zero defaults. Failed saves do not update the remembered counts. Suggested counts do not enter the database until the user saves.

Any existing metadata object blocks inheritance, including empty objects, null nested fields, and partial labels. Existing valid counts load as before. Missing or invalid stored counts fall back to zero. The database schema prohibits SQL NULL and requires a JSON object. An absent row is the API's null state, so inheritance uses absence of a row. No schema migration was required. The tracked database already had user changes and was not modified by this task.

Red: `uv run --locked pytest tests/test_dashboard.py -q --tb=short` reported 5 expected failures and 39 passes. Failures covered remembering successful saves and inheriting defaults for an absent row. Extended the script tests to cover empty metadata, partial metadata, null counts, zero counts, malformed session data, and unavailable storage.

Green: `uv run --locked pytest -q` passed all 108 tests. `ruff check .`, `ruff format --check .`, `uv build --quiet`, and `git diff --check` passed. Updated README, database documentation, and dashboard briefing. No live browser test was run.
