"""Check that new captures are detected once and recorded in the database."""

import json
import sqlite3
from contextlib import closing

import cv2
import numpy as np
import pytest

from wash_cam import db, watch
from wash_cam.detect import Detection

# A car standing in bay 1.
IN_BAY_1 = (337.0, 64.0, 429.0, 167.0)


class FakeDetector:
    """Record which images were run and report one car in bay 1."""

    name = "fake"

    def __init__(self):
        self.calls = 0

    def detect(self, image):
        self.calls += 1
        return [Detection(IN_BAY_1, "car", 0.9), Detection(IN_BAY_1, "person", 0.9)]


@pytest.fixture
def setup(tmp_path):
    """Create a migrated database and an empty capture directory."""
    database = tmp_path / "labels.sqlite3"
    db.migrate(database)
    images = tmp_path / "images"
    images.mkdir()
    return images, database


def write_capture(images, name):
    """Save a blank capture."""
    cv2.imwrite(str(images / name), np.zeros((340, 1270, 3), dtype=np.uint8))


def detection_rows(database):
    """Return stored detections keyed by image path."""
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(
            "SELECT image_path, metadata, model FROM image_detections"
        ).fetchall()
    return {path: (json.loads(metadata), model) for path, metadata, model in rows}


def test_new_captures_are_detected_and_stored(setup):
    """Each capture gets per-bay counts and its counted vehicles."""
    images, database = setup
    write_capture(images, "a.jpg")

    processed = watch.process_new_images(FakeDetector(), images, database)

    assert processed == 1
    metadata, model = detection_rows(database)["a.jpg"]
    assert model == "fake"
    bays = {bay["bay_id"]: bay["vehicle_count"] for bay in metadata["vehicles"]["bays"]}
    assert bays == {
        "bay_0": 0,
        "bay_1": 1,
        "bay_2": 0,
        "bay_3": 0,
        "bay_4": 0,
        "vac_0": 0,
    }
    assert metadata["vehicles"]["detections"] == [
        {"bay_id": "bay_1", "label": "car", "score": 0.9, "box": list(IN_BAY_1)}
    ]


def test_recorded_captures_are_not_detected_again(setup):
    """A later pass only runs the detector on captures without a record."""
    images, database = setup
    detector = FakeDetector()
    write_capture(images, "a.jpg")
    watch.process_new_images(detector, images, database)
    write_capture(images, "b.jpg")

    processed = watch.process_new_images(detector, images, database)

    assert processed == 1
    assert detector.calls == 2
    assert set(detection_rows(database)) == {"a.jpg", "b.jpg"}


def test_partial_and_unreadable_files_are_skipped(setup):
    """In-progress writes and corrupt files produce no record."""
    images, database = setup
    write_capture(images, "a.jpg")
    (images / "b.jpg.part").write_bytes(b"half written")
    (images / "c.jpg").write_bytes(b"not a jpeg")

    watch.process_new_images(FakeDetector(), images, database)

    assert set(detection_rows(database)) == {"a.jpg"}
