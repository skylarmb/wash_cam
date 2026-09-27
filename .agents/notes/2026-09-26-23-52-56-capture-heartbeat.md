# Capture heartbeat

Owner ScarletEagle, reviewed by StormyCliff (QA) over Agent Mail. Added `wash_cam.heartbeat` and `wash-cam heartbeat`, and added `heartbeat` to the services in `scripts/install_services.sh`. Alerts append to the git-ignored `NOTIFICATIONS.md`, as Skylar chose.

Red: `uv run --locked pytest tests/test_heartbeat.py` against a stub `beat()` that returned its input: 4 failed on behavior (no stop entry, no resume entry, missing directory not stale, no appended entry), 1 passed (fresh captures write nothing). Green: the same command, 5 passed.

QA review (StormyCliff) found that the highest filename is not the newest capture at the fall DST change. Red: `test_newest_capture_is_chosen_by_mtime_not_name` failed because it chose the older 01:59 file. Green: the newest capture is now chosen by mtime. `test_a_capture_exactly_at_the_limit_is_not_stale` documents the 300 s boundary and passed before and after the change.
