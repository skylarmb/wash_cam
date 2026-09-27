"""Score vehicle-count models against verified image labels."""

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from wash_cam.config import (
    BAYS,
    DARK_PIXEL_LEVEL,
    DATABASE_PATH,
    IMAGES_DIR,
    NIGHT_DARK_FRACTION,
)
from wash_cam.detect import VehicleDetector
from wash_cam.session_eval import LABEL_SOURCES

__all__ = [
    "MODELS",
    "Predictor",
    "Score",
    "evaluate",
    "is_night",
    "load_labels",
    "run",
    "score",
]

# Vehicle counts for one image, keyed by bay ID. A labeled image may omit bays
# the labeler did not fill in; a prediction covers every bay.
Counts = dict[str, int]


class Predictor(Protocol):
    """Anything that counts vehicles in each bay of a cropped capture."""

    def predict(self, image: np.ndarray) -> Counts:
        """Return the vehicle count for every bay, keyed by bay ID."""
        ...


class EmptyPredictor:
    """Report every bay as empty; the floor any real model has to beat."""

    def predict(self, image: np.ndarray) -> Counts:
        """Return zero for every bay."""
        return dict.fromkeys(BAYS, 0)


# Models selectable from the CLI by name.
MODELS: dict[str, Callable[[], Predictor]] = {
    "empty": EmptyPredictor,
    "detector": VehicleDetector,
}


@dataclass(frozen=True)
class Score:
    """Agreement between predictions and labels over a set of images."""

    images: int
    # Labeled bays compared; unlabeled bays are skipped.
    bays: int
    # Share of labeled bays whose count was exactly right.
    bay_accuracy: float
    # Share of images whose labeled bays were all exactly right.
    image_accuracy: float
    # Mean of |predicted - labeled| over labeled bays.
    mean_absolute_error: float


def is_night(image: np.ndarray) -> bool:
    """Return True when the capture was taken under night lighting."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return float((gray < DARK_PIXEL_LEVEL).mean()) > NIGHT_DARK_FRACTION


def load_labels(database: Path, labels: str = "agent") -> dict[str, Counts]:
    """Return per-bay counts for every image labeled by the given source ("agent" or "human").

    Entries with an unknown bay ID or a missing count are left out rather
    than guessed, so they are not scored.
    """
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        rows = db.execute(
            f"SELECT image_path, metadata FROM image_labels AS l "
            f"WHERE {LABEL_SOURCES[labels]}"
        ).fetchall()

    labels: dict[str, Counts] = {}
    for image_path, metadata in rows:
        counts: Counts = {}
        for entry in json.loads(metadata).get("vehicles", {}).get("bays", []):
            bay_id = entry.get("bay_id")
            count = entry.get("vehicle_count")
            if bay_id in BAYS and isinstance(count, int):
                counts[bay_id] = count
        labels[image_path] = counts
    return labels


def score(labels: list[Counts], predictions: list[Counts]) -> Score:
    """Compare predictions with labels image by image, skipping unlabeled bays.

    A frame with no labeled bays (every bay omitted as unjudgeable) is left
    out entirely; otherwise it would count as a correct image.
    """
    images = bays = correct_bays = correct_images = total_error = 0
    for labeled, predicted in zip(labels, predictions, strict=True):
        if not labeled:
            continue
        images += 1
        pairs = [(want, predicted[bay_id]) for bay_id, want in labeled.items()]
        errors = [abs(want - got) for want, got in pairs]
        bays += len(pairs)
        correct_bays += errors.count(0)
        correct_images += all(error == 0 for error in errors)
        total_error += sum(errors)

    # An empty group scores zero rather than dividing by zero.
    return Score(
        images=images,
        bays=bays,
        bay_accuracy=correct_bays / bays if bays else 0.0,
        image_accuracy=correct_images / images if images else 0.0,
        mean_absolute_error=total_error / bays if bays else 0.0,
    )


def evaluate(
    predictor: Predictor, database: Path, images: Path, labels: str = "agent"
) -> dict[str, Score]:
    """Score a predictor on every labeled image, overall and split by day and night."""
    groups: dict[str, tuple[list[Counts], list[Counts]]] = {
        "all": ([], []),
        "day": ([], []),
        "night": ([], []),
    }
    for image_path, labeled in sorted(load_labels(database, labels).items()):
        # cv2.imread returns None for a missing or unreadable file.
        image = cv2.imread(str(images / image_path))
        if image is None:
            continue
        predicted = predictor.predict(image)
        for group in ("all", "night" if is_night(image) else "day"):
            groups[group][0].append(labeled)
            groups[group][1].append(predicted)
    return {name: score(*pair) for name, pair in groups.items()}


def run(model: str, labels: str = "agent") -> None:
    """Score the named model against the labels database and print a report."""
    scores = evaluate(MODELS[model](), DATABASE_PATH, IMAGES_DIR, labels)
    print(f"Model: {model}, scored against {labels} labels")
    print(
        f"{'group':<6} {'images':>6} {'bays':>5} {'bay acc':>8} {'image acc':>9} {'MAE':>6}"
    )
    for name, result in scores.items():
        print(
            f"{name:<6} {result.images:>6} {result.bays:>5} "
            f"{result.bay_accuracy:>8.1%} {result.image_accuracy:>9.1%} "
            f"{result.mean_absolute_error:>6.2f}"
        )
