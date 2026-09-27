# Bay regions and scoring harness

Added `BAY_POLYGONS`, `DARK_PIXEL_LEVEL`, and `NIGHT_DARK_FRACTION` to config, the `wash_cam.bays` module and `wash-cam bays` overlay command, and the `wash_cam.evaluate` module and `wash-cam evaluate` command with an `empty` baseline model. Quads were drawn on daytime frames and checked visually on dusk and night frames; they agreed with the (since deleted) test labels. Day/night split uses dark-pixel share because mean brightness overlapped between the two.

Red: `uv run --locked pytest tests/test_bays.py tests/test_evaluate.py -q` with stub modules gave 9 behavioral failures and 2 passes (config ordering, and outside-bay rejection which the stub's None satisfies). Green: full suite passed, 119 tests. `uvx ruff check .`, `uvx ruff format --check .`, `uv build --quiet`, and `git diff --check` passed. `ruff` is not on PATH here; use `uvx ruff`.

Next: pretrained detector baseline (torchvision Faster R-CNN on MPS, avoiding AGPL Ultralytics) wired through `assign_to_bay`, then a night fine-tune, likely a per-bay crop classifier since labels are counts, not boxes.
