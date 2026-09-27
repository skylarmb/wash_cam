# Pretrained vehicle detection baseline

Added torch and torchvision, the `wash_cam.detect` module with `VehicleDetector` (COCO Faster R-CNN v2 on MPS), `count_vehicles` (vehicle classes, score >= 0.5, cross-class NMS via `cv2.dnn.NMSBoxes`, bay assignment), the `wash-cam detect` review command, and registered `detector` in `evaluate.MODELS`. No day/night split for the model, per the user; evaluate still reports day and night scores separately.

Red: `uv run --locked pytest tests/test_detect.py -q` with a stub module gave 4 behavioral failures and 1 pass (rejection, which the all-zero stub satisfies). Green: full suite passed, 124 tests. `uvx ruff check .`, `uvx ruff format --check .`, `uv build --quiet`, and `git diff --check` passed. Tests do not load model weights.

Ran on all 44 captures (no labels existed yet, labels were reset for zero-indexed bays). Visual review: day counts looked right, including a car on the bay_0 pad. Night false positives in bay_3 at 19:41, 19:42, 19:45 came from a red car parked behind the bay whose box bottom (~y 95-100) touched the quad's back edge (y 95); in-bay cars have box bottoms at y 150-170. At 19:49 a foam-covered, partly occluded car in bay_2 was missed.

Fix: moved every bay's back edge from y=95 to y=115 (corners slid along the side edges). Red: new `parked_at_back` case in `tests/test_bays.py::test_assign_to_bay_ignores_cars_outside_the_bays` failed. Green: full suite passed, 124 tests. The three bay_3 night false positives disappeared, and daytime counts were unchanged.
