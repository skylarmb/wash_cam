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
    BAY_POLYGONS,
    CHECKOUT_ROOT,
    DARK_PIXEL_LEVEL,
    NIGHT_DARK_FRACTION,
)

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

IMAGES_DIR = CHECKOUT_ROOT / "images"
DATABASE_PATH = CHECKOUT_ROOT / "db" / "labels.sqlite3"
BAY_COUNT = len(BAY_POLYGONS)

# Per-bay counts for one image. None marks a bay the labeler did not fill in.
Counts = list[int | None]


class Predictor(Protocol):
    """Anything that counts vehicles in each bay of a cropped capture."""

    def predict(self, image: np.ndarray) -> list[int]:
        """Return one vehicle count per bay, indexed left to right."""
        ...


class EmptyPredictor:
    """Report every bay as empty; the floor any real model has to beat."""

    def predict(self, image: np.ndarray) -> list[int]:
        """Return zero for every bay."""
        return [0] * BAY_COUNT


# Models selectable from the CLI by name.
MODELS: dict[str, Callable[[], Predictor]] = {"empty": EmptyPredictor}


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


def load_labels(database: Path) -> dict[str, Counts]:
    """Return per-bay counts for every verified image, keyed by image path.

    Bays are placed by their `bay_<index>` ID. Entries with an unknown ID or a
    missing count are left as None rather than guessed.
    """
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT image_path, metadata FROM image_labels").fetchall()

    labels: dict[str, Counts] = {}
    for image_path, metadata in rows:
        counts: Counts = [None] * BAY_COUNT
        for entry in json.loads(metadata).get("vehicles", {}).get("bays", []):
            index = bay_index(entry.get("bay_id"))
            count = entry.get("vehicle_count")
            if index is not None and isinstance(count, int):
                counts[index] = count
        labels[image_path] = counts
    return labels


def bay_index(bay_id: object) -> int | None:
    """Return the index encoded in a `bay_<index>` ID, or None if it names no known bay."""
    if not isinstance(bay_id, str) or not bay_id.startswith("bay_"):
        return None
    suffix = bay_id.removeprefix("bay_")
    if not suffix.isdigit() or int(suffix) >= BAY_COUNT:
        return None
    return int(suffix)


def score(labels: list[Counts], predictions: list[list[int]]) -> Score:
    """Compare predictions with labels image by image, skipping unlabeled bays."""
    bays = correct_bays = correct_images = total_error = 0
    for labeled, predicted in zip(labels, predictions, strict=True):
        pairs = [
            (want, got) for want, got in zip(labeled, predicted) if want is not None
        ]
        errors = [abs(want - got) for want, got in pairs]
        bays += len(pairs)
        correct_bays += errors.count(0)
        correct_images += all(error == 0 for error in errors)
        total_error += sum(errors)

    # An empty group scores zero rather than dividing by zero.
    return Score(
        images=len(labels),
        bays=bays,
        bay_accuracy=correct_bays / bays if bays else 0.0,
        image_accuracy=correct_images / len(labels) if labels else 0.0,
        mean_absolute_error=total_error / bays if bays else 0.0,
    )


def evaluate(predictor: Predictor, database: Path, images: Path) -> dict[str, Score]:
    """Score a predictor on every labeled image, overall and split by day and night."""
    groups: dict[str, tuple[list[Counts], list[list[int]]]] = {
        "all": ([], []),
        "day": ([], []),
        "night": ([], []),
    }
    for image_path, labeled in sorted(load_labels(database).items()):
        # cv2.imread returns None for a missing or unreadable file.
        image = cv2.imread(str(images / image_path))
        if image is None:
            continue
        predicted = predictor.predict(image)
        for group in ("all", "night" if is_night(image) else "day"):
            groups[group][0].append(labeled)
            groups[group][1].append(predicted)
    return {name: score(*pair) for name, pair in groups.items()}


def run(model: str) -> None:
    """Score the named model against the labels database and print a report."""
    scores = evaluate(MODELS[model](), DATABASE_PATH, IMAGES_DIR)
    print(f"Model: {model}")
    print(
        f"{'group':<6} {'images':>6} {'bays':>5} {'bay acc':>8} {'image acc':>9} {'MAE':>6}"
    )
    for name, result in scores.items():
        print(
            f"{name:<6} {result.images:>6} {result.bays:>5} "
            f"{result.bay_accuracy:>8.1%} {result.image_accuracy:>9.1%} "
            f"{result.mean_absolute_error:>6.2f}"
        )
