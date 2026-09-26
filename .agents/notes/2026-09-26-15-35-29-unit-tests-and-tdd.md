# Unit tests and TDD guidance

## Test setup

Added pytest to the uv development group and updated the lockfile. Configured discovery under `tests/` with importlib mode. Added six cases for dummy output, CLI dispatch, help, and invalid commands. Replaced the test placeholder and ignored the pytest cache. Application code did not change.

## Agent guidance and documentation

Added the required red/green workflow for new behavior and bug fixes to `AGENTS.md`. Documented exceptions and completion checks. Added README test commands and the testing briefing.

## Validation

The initial suite documents existing behavior, so no red phase was required. `uv sync --locked` passed. `uv run --locked pytest` passed all six cases on Python 3.13.12. `uv run --locked pytest tests/test_cli.py::test_dummy_command` passed one case. Ruff lint, Ruff format checks, `uv build`, and `git diff --check` passed.

The environment had an unrelated `VIRTUAL_ENV` and a restricted default uv cache. Validation unset `VIRTUAL_ENV` and used `UV_CACHE_DIR=/private/tmp/wash-cam-plan-uv-cache`. The initial dependency download required network access outside the sandbox.
