# Revenue estimate (steps 4 and 6)

Owner SilentIsland. Pricing from Skylar via ScarletEagle: wash max($3, $1/min), vacuum $0.25/min placeholder, constants in `config.py`. Added `session_revenue`, `revenue_totals`, and an "Estimated revenue" section (by day and by hour) to the Analytics tab; only `left` sessions count. Also fixed the duration p90 (inclusive quantiles) per StormyCliff's follow-up.

Red: `uv run --locked pytest -q tests/test_analytics.py` gave AttributeErrors for the new functions and `assert 1608.0 <= 1020` for p90. Green: 191 passed; ruff check/format, uv build, git diff --check passed. Rendered on the post-import snapshot: $49.85 estimated for 2026-09-26.
Follow-up: day rows keyed by date and sorted chronologically. Red: `assert 377 < 331` (Mon sorted before Sat). Green: 194 passed.
