# Wash Cam

Read README.md for main project info.

## Testing

Use pytest for unit tests. Run `uv sync --locked` to install the development dependencies.
Write deterministic tests of observable behavior. Avoid external services, camera hardware, timing dependencies, and assertions against private implementation details.

Follow this workflow for new behavior and bug fixes:

1. **Red:** Write a focused test before changing behavior. Run it and confirm that it fails for the expected behavioral reason. Collection, import, and environment errors do not count as red.
2. **Green:** Make the smallest implementation change that passes the test. Do not weaken assertions to accept incorrect behavior.
3. **Refactor:** Simplify code only when needed. Keep tests green and avoid unrelated changes.
4. **Complete:** Run the full suite, build, and lint checks below. Record the red and green commands and outcomes briefly in the task notes.

```sh
uv run --locked pytest
ruff check .
ruff format --check .
uv build
git diff --check
```

Use `uv run --locked pytest tests/test_cli.py::test_dummy_command` as an example of a focused test command.

Exceptions:

- Tests that document existing behavior, including the initial suite, can start green. Do not break working code to manufacture a failure.
- Documentation and tooling changes do not require an artificial failing unit test. Run the checks appropriate to the change.
- Behavior-preserving refactors require passing relevant tests before and after the change.
- If a behavioral test cannot run, report the blocker before implementation. Do not claim an unobserved red or green result.

## Agents

### Notes

Agent notes are stored in `.agents/notes/` directory with the file name starting with the current date and time in format: `date +%Y-%m-%d-%H-%M-%S`

Once finished with a task, add notes about what was done in each project phase or feature you worked on to share context with future agents.

This is a shared scratchpad for all agents, anything can go here that you think is useful.

### Docs

For each feature completed, write a SHORT documentation file (no more than 3 paragraphs) in `.agents/docs/` directory with the file name corresponding to the feature name. This is the official briefing for other agents. Do not include implementation process or project phase details here, only document the current working version of the code.
