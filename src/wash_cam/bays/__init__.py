"""Map detections to wash and vacuum bays and render the regions for review."""

from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np

from wash_cam.config import BAYS, CHECKOUT_ROOT

__all__ = ["assign_to_bay", "draw_bays", "run"]

IMAGES_DIR = CHECKOUT_ROOT / "images"
# Git-ignored, so review overlays never end up in version control.
REVIEW_DIR = CHECKOUT_ROOT / "tmp" / "bay_review"

# Outline colors (BGR) for review overlays: yellow wash bays, magenta vacuum.
WASH_COLOR = (0, 255, 255)
VACUUM_COLOR = (255, 0, 255)
OUTLINE_THICKNESS = 2

# (left, top, right, bottom) in cropped-capture pixels.
Box = tuple[float, float, float, float]


def assign_to_bay(box: Box) -> str | None:
    """Return the ID of the bay a vehicle box stands in, or None if it is outside every bay.

    Uses the box's bottom-center, where the vehicle meets the ground, so a car
    behind or in front of a bay does not count even when its box overlaps it.
    """
    left, _, right, bottom = box
    ground = ((left + right) / 2, bottom)
    for bay_id, polygon in BAYS.items():
        contour = np.array(polygon, dtype=np.float32)
        # pointPolygonTest is >= 0 inside or on the edge.
        if cv2.pointPolygonTest(contour, ground, False) >= 0:
            return bay_id
    return None


def draw_bays(image: np.ndarray) -> np.ndarray:
    """Return a copy of a cropped capture with each bay outlined and labeled by ID."""
    overlay = image.copy()
    for bay_id, polygon in BAYS.items():
        color = VACUUM_COLOR if bay_id.startswith("vac_") else WASH_COLOR
        points = np.array(polygon, dtype=np.int32)
        cv2.polylines(overlay, [points], True, color, OUTLINE_THICKNESS)
        # Label just inside the top-left corner so it stays within the bay.
        x, y = polygon[0]
        cv2.putText(
            overlay,
            bay_id,
            (x + 5, y + 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
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
