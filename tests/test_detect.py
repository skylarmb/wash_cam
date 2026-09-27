"""Check vehicle counting and detection review without loading model weights."""

import cv2
import numpy as np

from wash_cam import detect
from wash_cam.config import BAYS
from wash_cam.detect import Detection

# Boxes whose bottom-center stands on bay floors, and one queueing in front.
IN_BAY_1 = (337.0, 64.0, 429.0, 167.0)
IN_BAY_2 = (541.0, 77.0, 624.0, 157.0)
QUEUEING = (670.0, 175.0, 943.0, 301.0)
# A car at the vacuum, clipped by the bottom of the frame.
AT_VACUUM = (20.0, 230.0, 170.0, 340.0)


def counts(**overrides):
    """Return zero for every region except the given ones."""
    return {**dict.fromkeys(BAYS, 0), **overrides}


def test_count_vehicles_places_confident_vehicles_in_bays():
    """Each confident vehicle counts toward the bay it stands in."""
    detections = [
        Detection(IN_BAY_1, "car", 0.95),
        Detection(IN_BAY_2, "truck", 0.9),
        Detection(QUEUEING, "car", 1.0),
        Detection(AT_VACUUM, "car", 0.8),
    ]

    assert detect.count_vehicles(detections) == counts(bay_1=1, bay_2=1, vac_0=1)


def test_count_vehicles_ignores_weak_and_non_vehicle_detections():
    """Low scores and other object classes do not count."""
    detections = [
        Detection(IN_BAY_1, "car", 0.2),
        Detection(IN_BAY_2, "person", 0.99),
    ]

    assert detect.count_vehicles(detections) == counts()


def test_count_vehicles_merges_one_vehicle_seen_as_two_classes():
    """Overlapping boxes of different vehicle classes are one vehicle."""
    detections = [
        Detection(IN_BAY_1, "car", 0.95),
        Detection((338.0, 65.0, 428.0, 167.0), "truck", 0.6),
    ]

    assert detect.count_vehicles(detections) == counts(bay_1=1)


def test_count_vehicles_keeps_two_vehicles_in_one_bay():
    """Separate vehicles sharing a bay are both counted."""
    detections = [
        Detection((300.0, 95.0, 380.0, 170.0), "car", 0.9),
        Detection((390.0, 95.0, 465.0, 170.0), "car", 0.9),
    ]

    assert detect.count_vehicles(detections) == counts(bay_1=2)


def test_count_vehicles_ignores_boxes_too_tall_to_be_a_vehicle():
    """A phantom box spanning the dark lot is not a second car at the vacuum."""
    detections = [
        # The real car at the vacuum at 21:20, about 120px tall.
        Detection((67.0, 152.0, 206.0, 272.0), "car", 0.94),
        # A 270px "truck" covering the left side of the same frame.
        Detection((3.0, 3.0, 316.0, 275.0), "truck", 0.77),
    ]

    assert detect.count_vehicles(detections) == counts(vac_0=1)


class FakeDetector:
    """Return fixed detections instead of running the model."""

    def detect(self, image):
        return [Detection(IN_BAY_1, "car", 0.95)]


def test_run_writes_a_review_image_per_capture(monkeypatch, tmp_path, capsys):
    """The detect command saves an overlay and prints each capture's counts."""
    monkeypatch.setattr(detect, "VehicleDetector", FakeDetector)
    image_path = tmp_path / "capture.jpg"
    cv2.imwrite(str(image_path), np.zeros((340, 1270, 3), dtype=np.uint8))
    output = tmp_path / "review"

    detect.run(images=[image_path], output=output)

    assert [path.name for path in output.iterdir()] == ["capture.jpg"]
    assert (
        "capture.jpg bay_0=0 bay_1=1 bay_2=0 bay_3=0 bay_4=0 vac_0=0"
        in capsys.readouterr().out
    )
