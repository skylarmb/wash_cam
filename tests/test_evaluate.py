"""Check model scoring against verified labels without real captures."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import cv2
import numpy as np
import pytest

from wash_cam import evaluate

MIGRATION = (
    Path(__file__).resolve().parents[1] / "db" / "migrations" / "001_image_labels.sql"
)


@pytest.fixture
def database(tmp_path):
    """Create an empty labels database from the tracked migration."""
    path = tmp_path / "labels.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(MIGRATION.read_text(encoding="utf-8"))
    return path


def insert_label(database, image_path, counts):
    """Store verified counts, using None for a bay that was not labeled."""
    entries = [
        {"bay_id": f"bay_{index}", "vehicle_count": count}
        for index, count in enumerate(counts)
        if count is not None
    ]
    metadata = json.dumps({"vehicles": {"bays": entries}})
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "INSERT INTO image_labels (image_path, metadata) VALUES (?, ?)",
            (image_path, metadata),
        )


class FixedPredictor:
    """Predict the same counts for every image."""

    def __init__(self, counts):
        self.counts = counts

    def predict(self, image):
        return list(self.counts)


def test_is_night_separates_dark_and_bright_frames():
    """Mostly dark frames are night; mostly lit frames are day."""
    night = np.full((340, 1270, 3), 5, dtype=np.uint8)
    day = np.full((340, 1270, 3), 120, dtype=np.uint8)

    assert evaluate.is_night(night)
    assert not evaluate.is_night(day)


def test_load_labels_maps_bay_ids_to_indexes(database):
    """Counts land at their bay index, and unlabeled bays are None."""
    insert_label(database, "a.jpg", [0, 2, None, 1, 0])

    labels = evaluate.load_labels(database)

    assert labels == {"a.jpg": [0, 2, None, 1, 0]}


def test_score_counts_exact_bays_images_and_error():
    """Scores skip unlabeled bays and compare the rest."""
    labels = [[0, 1, 0, 0, 0], [1, 0, None, 0, 0]]
    predictions = [[0, 1, 0, 0, 0], [0, 0, 1, 0, 2]]

    score = evaluate.score(labels, predictions)

    assert score.images == 2
    assert score.bays == 9
    # 5 correct in the first image, 2 correct in the second.
    assert score.bay_accuracy == pytest.approx(7 / 9)
    assert score.image_accuracy == pytest.approx(1 / 2)
    # Errors of 1 and 2 across nine labeled bays.
    assert score.mean_absolute_error == pytest.approx(3 / 9)


def test_evaluate_splits_day_and_night(database, tmp_path):
    """Each labeled image is scored overall and within its lighting group."""
    images = tmp_path / "images"
    images.mkdir()
    cv2.imwrite(str(images / "day.jpg"), np.full((340, 1270, 3), 120, dtype=np.uint8))
    cv2.imwrite(str(images / "night.jpg"), np.full((340, 1270, 3), 5, dtype=np.uint8))
    insert_label(database, "day.jpg", [0, 0, 0, 0, 0])
    insert_label(database, "night.jpg", [0, 1, 0, 0, 0])

    scores = evaluate.evaluate(FixedPredictor([0] * 5), database, images)

    assert scores["all"].images == 2
    assert scores["day"].image_accuracy == 1.0
    assert scores["night"].image_accuracy == 0.0


def test_evaluate_skips_labels_without_an_image(database, tmp_path):
    """A label whose image is missing on disk is not scored."""
    insert_label(database, "missing.jpg", [0, 0, 0, 0, 0])

    scores = evaluate.evaluate(FixedPredictor([0] * 5), database, tmp_path)

    assert scores["all"].images == 0


def test_empty_model_predicts_no_vehicles():
    """The baseline model reports every bay as empty."""
    image = np.zeros((340, 1270, 3), dtype=np.uint8)

    assert evaluate.MODELS["empty"]().predict(image) == [0, 0, 0, 0, 0]
