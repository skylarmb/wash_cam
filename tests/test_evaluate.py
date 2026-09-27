"""Check model scoring against verified labels without real captures."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import cv2
import numpy as np
import pytest

from wash_cam import evaluate
from wash_cam.config import BAYS

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


def insert_label(database, image_path, counts, labeled_by=None):
    """Store counts for the bays in a {bay_id: count} mapping, from a human by default."""
    entries = [
        {"bay_id": bay_id, "vehicle_count": count} for bay_id, count in counts.items()
    ]
    provenance = {"labeled_by": labeled_by} if labeled_by else {}
    metadata = json.dumps({"vehicles": {"bays": entries}, **provenance})
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "INSERT INTO image_labels (image_path, metadata) VALUES (?, ?)",
            (image_path, metadata),
        )


def all_bays(count):
    """Return the same count for every region."""
    return dict.fromkeys(BAYS, count)


class FixedPredictor:
    """Predict the same counts for every image."""

    def __init__(self, counts):
        self.counts = counts

    def predict(self, image):
        return dict(self.counts)


def test_is_night_separates_dark_and_bright_frames():
    """Mostly dark frames are night; mostly lit frames are day."""
    night = np.full((340, 1270, 3), 5, dtype=np.uint8)
    day = np.full((340, 1270, 3), 120, dtype=np.uint8)

    assert evaluate.is_night(night)
    assert not evaluate.is_night(day)


def test_load_labels_keeps_known_labeled_bays(database):
    """Counts are keyed by bay ID; unlabeled and unknown bays are left out."""
    insert_label(database, "a.jpg", {"bay_0": 0, "bay_1": 2, "vac_0": 1, "bay_9": 3})

    labels = evaluate.load_labels(database, "human")

    assert labels == {"a.jpg": {"bay_0": 0, "bay_1": 2, "vac_0": 1}}


def test_score_counts_exact_bays_images_and_error():
    """Scores skip unlabeled bays and compare the rest."""
    labels = [
        {"bay_0": 0, "bay_1": 1, "vac_0": 0},
        {"bay_0": 1, "bay_1": 0, "vac_0": 0},
    ]
    predictions = [
        {**all_bays(0), "bay_1": 1},
        {**all_bays(0), "vac_0": 2},
    ]

    score = evaluate.score(labels, predictions)

    assert score.images == 2
    assert score.bays == 6
    # 3 correct in the first image, 1 correct in the second.
    assert score.bay_accuracy == pytest.approx(4 / 6)
    assert score.image_accuracy == pytest.approx(1 / 2)
    # Errors of 1 and 2 across six labeled bays.
    assert score.mean_absolute_error == pytest.approx(3 / 6)


def test_evaluate_splits_day_and_night(database, tmp_path):
    """Each labeled image is scored overall and within its lighting group."""
    images = tmp_path / "images"
    images.mkdir()
    cv2.imwrite(str(images / "day.jpg"), np.full((340, 1270, 3), 120, dtype=np.uint8))
    cv2.imwrite(str(images / "night.jpg"), np.full((340, 1270, 3), 5, dtype=np.uint8))
    insert_label(database, "day.jpg", all_bays(0))
    insert_label(database, "night.jpg", {**all_bays(0), "bay_1": 1})

    scores = evaluate.evaluate(FixedPredictor(all_bays(0)), database, images, "human")

    assert scores["all"].images == 2
    assert scores["day"].image_accuracy == 1.0
    assert scores["night"].image_accuracy == 0.0


def test_evaluate_skips_labels_without_an_image(database, tmp_path):
    """A label whose image is missing on disk is not scored."""
    insert_label(database, "missing.jpg", all_bays(0))

    scores = evaluate.evaluate(FixedPredictor(all_bays(0)), database, tmp_path, "human")

    assert scores["all"].images == 0


def test_empty_model_predicts_no_vehicles():
    """The baseline model reports every bay as empty."""
    image = np.zeros((340, 1270, 3), dtype=np.uint8)

    assert evaluate.MODELS["empty"]().predict(image) == all_bays(0)


def test_load_labels_separates_agent_and_human_labels(database):
    """Agent labels carry labeled_by; human labels do not. Agent is the default."""
    insert_label(database, "human.jpg", {"bay_0": 1})
    insert_label(database, "agent.jpg", {"bay_0": 2}, labeled_by="agent:X/y")

    assert set(evaluate.load_labels(database)) == {"agent.jpg"}
    assert set(evaluate.load_labels(database, "agent")) == {"agent.jpg"}
    assert set(evaluate.load_labels(database, "human")) == {"human.jpg"}
