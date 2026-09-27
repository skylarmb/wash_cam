# Vehicle height limit and capture service

Night frames on the server produced 220-270px "truck" boxes over the dark left side of the lot whose bottom-center fell in `vac_0` (19:49, 21:20; at 21:20 a real car plus the phantom counted as 2). Added `MAX_VEHICLE_HEIGHT = 180` to `wash_cam.detect`; real cars here are at most ~130px tall. Added the capture loop as launchd agent `com.washcam.capture` in `scripts/install_services.sh`.

Red: `uv run --locked pytest -q tests/test_detect.py` failed `test_count_vehicles_ignores_boxes_too_tall_to_be_a_vehicle` (counted vac_0=2). Green: 132 passed; ruff check/format, build, and diff check passed.

Server verification before this change: the mini's own `config.yaml` calibration (set up 21:58-22:04) is within ~6px of the committed example, and a 22:09 night capture lined up with the bay quads.
