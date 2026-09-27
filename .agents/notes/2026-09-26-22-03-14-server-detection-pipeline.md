# Server detection pipeline

Moved to the Mac mini (`skylar@10.0.4.25`, checkout `~/workspace/wash_cam`) as the only home for captures and the database. Merged the committed `images/` (44) with the local `tmp/images/` (237 total unique; 8 overlaps were byte-identical), rsynced to the mini with checksum verification, then removed `images/` from git and deleted the local copies. Stopped tracking `db/labels.sqlite3`. Committed the user's working calibration matrix into `config.example.yaml`.

Added migration `002_image_detections.sql`, `wash_cam.db.migrate` plus `wash-cam migrate`, and `wash_cam.watch` plus `wash-cam watch`. Centralized `IMAGES_DIR` and `DATABASE_PATH` in config. Capture now writes `.part` then renames. Added `scripts/install_services.sh` for launchd agents (dashboard, watch). Replaced the dashboard test that copied the module into a fake checkout with a check that the dashboard uses the checkout's `tmp/images` from config.

Red: `uv run --locked pytest -q tests/test_db.py tests/test_watch.py` with stub modules gave 5 behavioral failures (missing tables, user_version 1). Green: 131 passed. `uvx ruff check .`, `uvx ruff format --check .`, `uv build --quiet`, and `git diff --check` passed.
