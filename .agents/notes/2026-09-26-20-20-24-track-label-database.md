# Shared label database

The user requested a tracked SQLite database for sequential labeling by multiple developers.
Removed the database ignore rule and kept the temporary journal ignore rule.
Updated the database documentation and agent briefing with the shared labeling workflow.
The tracked database already contains migration 001.

`ruff check .`, `ruff format --check .`, `uv build`, and `git diff --check` passed.
The database integrity check returned `ok`.
This tracking and documentation change does not require a behavioral test.
