# Dashboard image directory

- Changed the dashboard source to the checkout's `images/` directory for shared test data. Camera capture still writes to `tmp/images/`.
- Updated dashboard tests and documentation. Added a regression test that requests the committed image from another working directory.
- Red: `uv run --locked pytest tests/test_dashboard.py::test_serves_committed_images_from_any_working_directory --tb=short -q` failed because the response contained a different image.
- Green: `uv run --locked pytest tests/test_dashboard.py --tb=short -q` passed all seven dashboard tests.
- The full suite passed all 65 tests. Ruff lint, formatting, package build, and `git diff --check` passed.
- uv commands used `UV_CACHE_DIR=/tmp/wash-cam-uv-cache`. The build required network access for setuptools.
