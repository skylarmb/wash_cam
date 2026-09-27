"""Run vehicle detection on every new capture and store the results."""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from wash_cam.bays import assign_to_bay
from wash_cam.config import DATABASE_PATH, IMAGES_DIR
from wash_cam.db import migrate
from wash_cam.detect import Detection, VehicleDetector, count_vehicles, vehicles

__all__ = ["process_new_images", "run"]

# Seconds between scans of the capture directory. Captures arrive every 30s,
# so this keeps results close to real time without busy-polling.
POLL_INTERVAL = 5.0


class Detector(Protocol):
    """A named detector that finds objects in a BGR capture."""

    name: str

    def detect(self, image: np.ndarray) -> list[Detection]:
        """Return every object found in the capture."""
        ...


def process_new_images(
    detector: Detector,
    images: Path,
    database: Path,
    unreadable: set[str] | None = None,
) -> int:
    """Detect vehicles in captures without a stored result and return how many were stored.

    Only finished `.jpg` files are considered; the camera writes to a `.part`
    file and renames it, so a capture is never read half-written. Files that
    cannot be decoded are added to `unreadable` and skipped on later passes
    instead of being retried forever.
    """
    unreadable = set() if unreadable is None else unreadable
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True)) as db:
        done = {
            path for (path,) in db.execute("SELECT image_path FROM image_detections")
        }
        stored = 0
        for path in sorted(images.glob("*.jpg")):
            if path.name in done or path.name in unreadable:
                continue
            image = cv2.imread(str(path))
            if image is None:
                print(f"Skipping unreadable image {path.name}", flush=True)
                unreadable.add(path.name)
                continue
            detections = detector.detect(image)
            metadata = {
                "vehicles": {
                    "bays": [
                        {"bay_id": bay_id, "vehicle_count": count}
                        for bay_id, count in count_vehicles(detections).items()
                    ],
                    # Every vehicle that passed filtering, including ones outside
                    # the bays, so results can be audited against the image.
                    "detections": [
                        {
                            "bay_id": assign_to_bay(vehicle.box),
                            "label": vehicle.label,
                            "score": round(vehicle.score, 3),
                            "box": [round(value, 1) for value in vehicle.box],
                        }
                        for vehicle in vehicles(detections)
                    ],
                }
            }
            # Commit per image so progress survives a crash mid-backlog.
            with db:
                db.execute(
                    "INSERT OR IGNORE INTO image_detections (image_path, metadata, model) "
                    "VALUES (?, ?, ?)",
                    (path.name, json.dumps(metadata), detector.name),
                )
            stored += 1
    return stored


def run() -> None:
    """Watch the capture directory forever, storing detections for new captures."""
    migrate(DATABASE_PATH)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    detector = VehicleDetector()
    print(f"Watching {IMAGES_DIR} with {detector.name}", flush=True)
    unreadable: set[str] = set()
    while True:
        stored = process_new_images(detector, IMAGES_DIR, DATABASE_PATH, unreadable)
        if stored:
            print(f"Stored detections for {stored} captures", flush=True)
        time.sleep(POLL_INTERVAL)
