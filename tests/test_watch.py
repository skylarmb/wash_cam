"""Check that new captures are detected once and recorded in the database."""

import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone

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


@pytest.fixture
def pacific(monkeypatch):
    """Run in the car wash's timezone, then restore the process timezone."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


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


def captured_at(database, image_path):
    """Return the stored UTC capture time of one image."""
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(
            "SELECT captured_at FROM image_detections WHERE image_path = ?",
            (image_path,),
        ).fetchone()[0]


def set_mtime(path, utc_text):
    """Set a file's modification time to a UTC timestamp."""
    stamp = datetime.fromisoformat(f"{utc_text}+00:00").timestamp()
    os.utime(path, (stamp, stamp))


def test_capture_time_converts_local_filenames_to_utc(pacific, tmp_path):
    """Capture filenames are local time; stored times are UTC."""
    path = tmp_path / "20260926_164649_764289.jpg"
    path.write_bytes(b"")

    assert watch.capture_time(path) == datetime(
        2026, 9, 26, 23, 46, 49, 764289, tzinfo=timezone.utc
    )


def test_capture_time_uses_the_file_to_resolve_the_repeated_hour(pacific, tmp_path):
    """When daylight saving ends, 01:30 happens twice; the file time decides which."""
    path = tmp_path / "20261101_013000_000000.jpg"
    path.write_bytes(b"")

    set_mtime(path, "2026-11-01 08:30:01")
    assert watch.capture_time(path) == datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)
    set_mtime(path, "2026-11-01 09:30:01")
    assert watch.capture_time(path) == datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)


def test_detections_record_the_capture_time(pacific, setup):
    """Stored detections carry the capture's UTC time."""
    images, database = setup
    write_capture(images, "20260926_164649_764289.jpg")

    watch.process_new_images(FakeDetector(), images, database)

    assert (
        captured_at(database, "20260926_164649_764289.jpg") == "2026-09-26 23:46:49.764"
    )


def test_backfill_fills_missing_capture_times(pacific, setup):
    """Detections stored before capture times existed get them filled in."""
    images, database = setup
    write_capture(images, "20260926_164649_764289.jpg")
    watch.process_new_images(FakeDetector(), images, database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE image_detections SET captured_at = NULL")

    filled = watch.backfill_capture_times(images, database)

    assert filled == 1
    assert (
        captured_at(database, "20260926_164649_764289.jpg") == "2026-09-26 23:46:49.764"
    )


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


def brightness_of(database, image_path):
    """Return the stored brightness of one capture."""
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(
            "SELECT brightness FROM image_detections WHERE image_path = ?",
            (image_path,),
        ).fetchone()[0]


def test_detections_record_capture_brightness(setup):
    """Each stored detection carries its capture's mean brightness."""
    images, database = setup
    cv2.imwrite(str(images / "lit.jpg"), np.full((34, 127, 3), 30, dtype=np.uint8))

    watch.process_new_images(FakeDetector(), images, database)

    assert brightness_of(database, "lit.jpg") == pytest.approx(30, abs=1)


def test_backfill_measures_brightness_for_older_detections(setup):
    """Detections stored before brightness existed get it measured."""
    images, database = setup
    write_capture(images, "old.jpg")
    watch.process_new_images(FakeDetector(), images, database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE image_detections SET brightness = NULL")
        connection.execute(
            "INSERT INTO image_detections (image_path, metadata, model) "
            "VALUES ('gone.jpg', '{}', 'fake')"
        )

    measured = watch.backfill_brightness(images, database)

    # The deleted capture stays unmeasured.
    assert measured == 1
    assert brightness_of(database, "old.jpg") == 0.0
    assert brightness_of(database, "gone.jpg") is None
