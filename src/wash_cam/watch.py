"""Run vehicle detection on every new capture and store the results."""

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from wash_cam.bays import assign_to_bay
from wash_cam.config import DATABASE_PATH, IMAGES_DIR
from wash_cam.db import migrate
from wash_cam.detect import Detection, VehicleDetector, count_vehicles, vehicles
from wash_cam.sessions import format_time, update_sessions

__all__ = ["backfill_capture_times", "capture_time", "process_new_images", "run"]

# Capture filenames are local time: see take_picture().
FILENAME_TIME_FORMAT = "%Y%m%d_%H%M%S_%f"

# Seconds between scans of the capture directory. Captures arrive every 30s,
# so this keeps results close to real time without busy-polling.
POLL_INTERVAL = 5.0


class Detector(Protocol):
    """A named detector that finds objects in a BGR capture."""

    name: str

    def detect(self, image: np.ndarray) -> list[Detection]:
        """Return every object found in the capture."""
        ...


def capture_time(path: Path) -> datetime | None:
    """Return a capture's UTC time from its local-time filename.

    When daylight saving ends, one local hour happens twice and its filenames
    are ambiguous; the file's modification time, written at capture, picks the
    matching occurrence. Returns None for names that are not capture times.
    """
    try:
        # Deliberately naive: the filename is local wall-clock time.
        local = datetime.strptime(path.stem, FILENAME_TIME_FORMAT)  # noqa: DTZ007
    except ValueError:
        return None
    # Naive times are interpreted in the process's local timezone; fold picks
    # the first (0) or second (1) occurrence of a repeated hour.
    candidates = {local.replace(fold=fold).astimezone(timezone.utc) for fold in (0, 1)}
    written = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return min(candidates, key=lambda moment: abs(moment - written))


def stored_time(path: Path) -> str | None:
    """Return a capture's UTC time formatted for the database, if it has one."""
    moment = capture_time(path)
    return format_time(moment) if moment else None


def backfill_capture_times(images: Path, database: Path) -> int:
    """Fill in capture times for detections stored before they were recorded."""
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True)) as db:
        missing = [
            path
            for (path,) in db.execute(
                "SELECT image_path FROM image_detections WHERE captured_at IS NULL"
            )
        ]
        # Captures deleted from disk, or with non-timestamp names, stay null.
        filled = [
            (moment, name)
            for name in missing
            if (images / name).is_file() and (moment := stored_time(images / name))
        ]
        with db:
            db.executemany(
                "UPDATE image_detections SET captured_at = ? WHERE image_path = ?",
                filled,
            )
    return len(filled)


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
                    "INSERT OR IGNORE INTO image_detections "
                    "(image_path, metadata, model, captured_at) VALUES (?, ?, ?, ?)",
                    (path.name, json.dumps(metadata), detector.name, stored_time(path)),
                )
            stored += 1
    return stored


def run() -> None:
    """Watch the capture directory forever, storing detections for new captures."""
    migrate(DATABASE_PATH)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    detector = VehicleDetector()
    print(f"Watching {IMAGES_DIR} with {detector.name}", flush=True)
    filled = backfill_capture_times(IMAGES_DIR, DATABASE_PATH)
    if filled:
        print(f"Filled capture times for {filled} detections", flush=True)
    unreadable: set[str] = set()
    # Update sessions on the first pass too, so a restart after a migration or
    # backfill brings them up to date before any new capture arrives.
    first_pass = True
    while True:
        stored = process_new_images(detector, IMAGES_DIR, DATABASE_PATH, unreadable)
        if stored:
            print(f"Stored detections for {stored} captures", flush=True)
        if stored or first_pass:
            update_sessions(DATABASE_PATH)
            first_pass = False
        time.sleep(POLL_INTERVAL)
