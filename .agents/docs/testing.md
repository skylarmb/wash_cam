# Unit tests

Pytest is a development dependency managed by uv. Run `uv sync --locked` to install dependencies and `uv run --locked pytest` to run the suite. Tests reside in `tests/` and import the installed package with pytest's importlib mode. They cover dummy output, CLI dispatch, help, and invalid commands without camera hardware or external services.

For a focused check, run `uv run --locked pytest tests/test_cli.py::test_dummy_command`. Test functions use docstrings to describe their purpose. Built-in pytest fixtures supply command-line arguments and capture output.

The root `AGENTS.md` requires red/green development for new behavior and bug fixes. It defines exceptions for existing behavior tests, documentation, tooling, and behavior-preserving refactors. Completion requires the full test suite, Ruff lint and format checks, a package build, and a diff check.
