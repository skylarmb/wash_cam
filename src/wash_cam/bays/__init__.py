"""Map detections to wash bays and render bay regions for review."""

from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np

from wash_cam.config import BAY_POLYGONS, CHECKOUT_ROOT

__all__ = ["assign_to_bay", "draw_bays", "run"]

IMAGES_DIR = CHECKOUT_ROOT / "images"
# Git-ignored, so review overlays never end up in version control.
REVIEW_DIR = CHECKOUT_ROOT / "tmp" / "bay_review"

# Outline color (BGR) and thickness for review overlays.
OUTLINE_COLOR = (0, 255, 255)
OUTLINE_THICKNESS = 2

# (left, top, right, bottom) in cropped-capture pixels.
Box = tuple[float, float, float, float]


def assign_to_bay(box: Box) -> int | None:
    """Return the index of the bay a vehicle box stands in, or None if it is outside every bay.

    Uses the box's bottom-center, where the vehicle meets the ground, so a car
    behind or in front of a bay does not count even when its box overlaps it.
    """
    left, _, right, bottom = box
    ground = ((left + right) / 2, bottom)
    for index, polygon in enumerate(BAY_POLYGONS):
        contour = np.array(polygon, dtype=np.float32)
        # pointPolygonTest is >= 0 inside or on the edge.
        if cv2.pointPolygonTest(contour, ground, False) >= 0:
            return index
    return None


def draw_bays(image: np.ndarray) -> np.ndarray:
    """Return a copy of a cropped capture with each bay outlined and numbered."""
    overlay = image.copy()
    for index, polygon in enumerate(BAY_POLYGONS):
        points = np.array(polygon, dtype=np.int32)
        cv2.polylines(overlay, [points], True, OUTLINE_COLOR, OUTLINE_THICKNESS)
        # Label just inside the top-left corner so it stays within the bay.
        x, y = polygon[0]
        cv2.putText(
            overlay,
            f"bay_{index}",
            (x + 5, y + 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            OUTLINE_COLOR,
            2,
        )
    return overlay


def run(images: Sequence[Path] | None = None, output: Path = REVIEW_DIR) -> None:
    """Write a bay overlay for each image, defaulting to every capture in images/."""
    paths = list(images) if images else sorted(IMAGES_DIR.glob("*.jpg"))
    output.mkdir(parents=True, exist_ok=True)
    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            raise SystemExit(f"Cannot read image {path}")
        cv2.imwrite(str(output / path.name), draw_bays(image))
    print(f"Wrote {len(paths)} overlays to {output}")
