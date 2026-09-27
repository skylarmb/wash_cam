"""Count vehicles in each wash and vacuum bay with a pretrained object detector."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from wash_cam.bays import Box, assign_to_bay, draw_bays
from wash_cam.config import BAYS, CHECKOUT_ROOT, IMAGES_DIR

__all__ = ["Detection", "VehicleDetector", "count_vehicles", "run"]

# Git-ignored, so review overlays never end up in version control.
REVIEW_DIR = CHECKOUT_ROOT / "tmp" / "detections"

# COCO classes that count as a vehicle in a bay. The model sometimes calls
# pickups and vans trucks or buses, so car alone would miss them.
VEHICLE_LABELS = frozenset({"car", "truck", "bus", "motorcycle"})
# Minimum detector confidence to count a vehicle. On the first collection run,
# real bay vehicles scored 0.95+ day and night; spurious second-class guesses
# on the same car scored 0.2-0.45.
MIN_VEHICLE_SCORE = 0.5
# Tallest plausible vehicle box, in cropped-capture pixels. Real cars in this
# view are at most ~130px tall; at night the model sometimes reports a 220-270px
# "truck" spanning the dark left side of the lot, which lands in the vacuum bay.
MAX_VEHICLE_HEIGHT = 180
# Vehicle boxes overlapping more than this (intersection over union) are one
# vehicle, whatever class each was given.
DUPLICATE_OVERLAP = 0.5

# Overlay colors (BGR): vehicles counted in a bay, and vehicles outside them.
COUNTED_COLOR = (0, 255, 0)
IGNORED_COLOR = (0, 0, 255)


@dataclass(frozen=True)
class Detection:
    """One object the detector found in a cropped capture."""

    box: Box
    # COCO category name, e.g. "car" or "person".
    label: str
    score: float


def vehicles(detections: Sequence[Detection]) -> list[Detection]:
    """Return confident, plausibly sized vehicle detections with cross-class duplicates removed."""
    candidates = [
        detection
        for detection in detections
        if detection.label in VEHICLE_LABELS
        and detection.score >= MIN_VEHICLE_SCORE
        and detection.box[3] - detection.box[1] <= MAX_VEHICLE_HEIGHT
    ]
    if not candidates:
        return []
    # NMSBoxes takes (x, y, width, height) and keeps the highest score in each
    # overlapping group. Running it over all vehicle classes together merges a
    # car that was also reported as a truck.
    rects = [
        (left, top, right - left, bottom - top)
        for left, top, right, bottom in (detection.box for detection in candidates)
    ]
    scores = [detection.score for detection in candidates]
    kept = cv2.dnn.NMSBoxes(rects, scores, MIN_VEHICLE_SCORE, DUPLICATE_OVERLAP)
    return [candidates[index] for index in np.array(kept).flatten()]


def count_vehicles(detections: Sequence[Detection]) -> dict[str, int]:
    """Return the number of vehicles standing in each bay, keyed by bay ID."""
    counts = dict.fromkeys(BAYS, 0)
    for detection in vehicles(detections):
        bay = assign_to_bay(detection.box)
        if bay is not None:
            counts[bay] += 1
    return counts


class VehicleDetector:
    """Pretrained COCO Faster R-CNN that counts vehicles per bay.

    Torch is imported here rather than at module level so CLI commands that
    never build a detector do not pay its import time. The first use downloads
    about 170 MB of weights into the torch cache.
    """

    # Recorded with stored detections to tell model versions apart.
    name = "fasterrcnn_resnet50_fpn_v2_coco"

    def __init__(self) -> None:
        import torch
        from torchvision.models.detection import (
            FasterRCNN_ResNet50_FPN_V2_Weights,
            fasterrcnn_resnet50_fpn_v2,
        )

        weights = FasterRCNN_ResNet50_FPN_V2_Weights.DEFAULT
        self._torch = torch
        self._categories: list[str] = weights.meta["categories"]
        # Apple Silicon GPU when available; CPU works but is several times slower.
        self._device = "mps" if torch.backends.mps.is_available() else "cpu"
        self._model = (
            fasterrcnn_resnet50_fpn_v2(weights=weights).eval().to(self._device)
        )

    def detect(self, image: np.ndarray) -> list[Detection]:
        """Return every object the model finds in a BGR capture."""
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        # The model expects a CHW float tensor scaled to 0-1.
        tensor = self._torch.from_numpy(rgb).permute(2, 0, 1).float().div(255)
        with self._torch.no_grad():
            output = self._model([tensor.to(self._device)])[0]
        return [
            Detection(tuple(box), self._categories[label], score)
            for box, label, score in zip(
                output["boxes"].cpu().tolist(),
                output["labels"].cpu().tolist(),
                output["scores"].cpu().tolist(),
            )
        ]

    def predict(self, image: np.ndarray) -> dict[str, int]:
        """Return the vehicle count for each bay of a BGR capture."""
        return count_vehicles(self.detect(image))


def draw_detections(image: np.ndarray, detections: Sequence[Detection]) -> np.ndarray:
    """Return a copy of a capture with bays and counted vehicles outlined."""
    overlay = draw_bays(image)
    for detection in vehicles(detections):
        counted = assign_to_bay(detection.box) is not None
        color = COUNTED_COLOR if counted else IGNORED_COLOR
        left, top, right, bottom = (int(value) for value in detection.box)
        cv2.rectangle(overlay, (left, top), (right, bottom), color, 2)
        cv2.putText(
            overlay,
            f"{detection.label} {detection.score:.2f}",
            (left, max(top - 5, 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
        )
    return overlay


def run(images: Sequence[Path] | None = None, output: Path = REVIEW_DIR) -> None:
    """Detect vehicles in each image, print its bay counts, and save a review overlay."""
    paths = list(images) if images else sorted(IMAGES_DIR.glob("*.jpg"))
    output.mkdir(parents=True, exist_ok=True)
    detector = VehicleDetector()
    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            raise SystemExit(f"Cannot read image {path}")
        detections = detector.detect(image)
        counts = count_vehicles(detections)
        print(path.name, " ".join(f"{bay}={count}" for bay, count in counts.items()))
        cv2.imwrite(str(output / path.name), draw_detections(image, detections))
    print(f"Wrote {len(paths)} overlays to {output}")
