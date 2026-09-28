"""Export capture batches for a labeling agent and import the labels it writes.

Per-frame vehicle counts are labeled by agents, so the labels must judge each
image independently: the export never includes detections. (Visit and washing
labels are separate; see wash_cam.visit_labels.)
Every imported row records who labeled it in `labeled_by`, which separates
agent labels from any human labels, and human rows are never overwritten.
See .agents/docs/labeling.md for the file formats.
"""

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2

from wash_cam.bays import draw_bays
from wash_cam.config import BAYS, CHECKOUT_ROOT, DATABASE_PATH, IMAGES_DIR, bay_kind

__all__ = ["ImportResult", "LabelingError", "export_batch", "import_labels"]

BATCHES_DIR = CHECKOUT_ROOT / "tmp" / "label_batches"
# Capture filenames are local time: see take_picture().
FILENAME_TIME_FORMAT = "%Y%m%d_%H%M%S_%f"
# Prefix every agent labeler identifier must carry, e.g. "agent:StormyCliff/gpt-6".
AGENT_PREFIX = "agent:"
# The rule the detector applies (wash_cam.bays.assign_to_bay), given to labelers.
COUNT_RULE = (
    "A vehicle counts for a bay when the point where its wheels meet the ground "
    "(the bottom-center of the vehicle) is inside the bay's quad. Cars queueing "
    "in front of a bay or parked behind it do not count."
)


class LabelingError(Exception):
    """Report an export that would clobber work, or a labels file that is invalid."""


class InvalidLabelError(Exception):
    """Describe why one labels.jsonl line cannot be imported."""


@dataclass(frozen=True)
class ImportResult:
    """What an import wrote."""

    imported: int
    # Rows left alone because a human labeled them.
    skipped_human: int


def capture_local_time(path: Path) -> datetime | None:
    """Return a capture's local time from its filename, or None for other names."""
    try:
        # Deliberately naive: the filename is local wall-clock time.
        return datetime.strptime(path.stem, FILENAME_TIME_FORMAT)  # noqa: DTZ007
    except ValueError:
        return None


def export_batch(
    start: datetime,
    end: datetime,
    images: Path = IMAGES_DIR,
    batches: Path = BATCHES_DIR,
    every: int = 1,
) -> Path:
    """Write a labeling batch for captures in [start, end) local time and return its directory.

    The batch holds bay overlays of each frame, a manifest pointing at the
    original captures, and an empty labels.jsonl for the labeler. An existing
    batch is never replaced, because it may hold labels in progress.
    """
    batch = batches / f"{start:%Y%m%d-%H%M}_{end:%Y%m%d-%H%M}"
    if batch.exists():
        raise LabelingError(f"Batch {batch} already exists; not replacing it.")
    selected = [
        path
        for path in sorted(images.glob("*.jpg"))
        if (moment := capture_local_time(path)) and start <= moment < end
    ][::every]

    overlays = batch / "overlay"
    overlays.mkdir(parents=True)
    frames = []
    for path in selected:
        image = cv2.imread(str(path))
        if image is None:
            continue
        cv2.imwrite(str(overlays / path.name), draw_bays(image))
        moment = capture_local_time(path)
        frames.append(
            {
                "image_path": path.name,
                "local_time": f"{moment:%Y-%m-%d %H:%M:%S}",
                "raw": str(path),
                "overlay": f"overlay/{path.name}",
            }
        )

    manifest = {
        "batch_id": batch.name,
        "created_at": f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}",
        "timezone": str(datetime.now().astimezone().tzinfo),
        "count_rule": COUNT_RULE,
        "bays": {
            bay_id: {
                "kind": bay_kind(bay_id),
                "quad": [list(point) for point in quad],
            }
            for bay_id, quad in BAYS.items()
        },
        "frames": frames,
    }
    (batch / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (batch / "labels.jsonl").write_text("")
    return batch


def parse_label(line: str, images: Path) -> dict:
    """Validate one labels.jsonl line and return it, or raise InvalidLabelError saying why."""
    try:
        label = json.loads(line)
    except json.JSONDecodeError as error:
        raise InvalidLabelError(f"not valid JSON ({error.msg})") from error
    if not isinstance(label, dict):
        raise InvalidLabelError("not a JSON object")

    name = label.get("image_path")
    # Plain filenames only, so a label cannot point outside the capture directory.
    if not isinstance(name, str) or Path(name).name != name or not name:
        raise InvalidLabelError("image_path must be a capture filename")
    if not (images / name).is_file():
        raise InvalidLabelError(f"image {name} does not exist")

    counts = label.get("counts")
    if not isinstance(counts, dict):
        raise InvalidLabelError("counts must be an object")
    for bay_id, count in counts.items():
        if bay_id not in BAYS:
            raise InvalidLabelError(f"unknown bay {bay_id}")
        if type(count) is not int or count < 0:
            raise InvalidLabelError(
                f"count for {bay_id} must be a non-negative integer"
            )

    labeled_by = label.get("labeled_by")
    if not isinstance(labeled_by, str) or not labeled_by.startswith(AGENT_PREFIX):
        raise InvalidLabelError(f"labeled_by must start with {AGENT_PREFIX!r}")
    uncertain = label.get("uncertain_bays", [])
    if not isinstance(uncertain, list) or any(bay not in BAYS for bay in uncertain):
        raise InvalidLabelError("uncertain_bays must list known bay IDs")
    if not isinstance(label.get("notes", ""), str):
        raise InvalidLabelError("notes must be text")
    return label


def import_labels(
    path: Path, images: Path = IMAGES_DIR, database: Path = DATABASE_PATH
) -> ImportResult:
    """Store every label in a labels.jsonl file, or nothing if any line is invalid.

    Rows a human labeled (no labeled_by) are skipped, never overwritten; an
    agent's earlier labels for the same capture are replaced.
    """
    labels, problems = [], []
    lines = path.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            labels.append(parse_label(line, images))
        except InvalidLabelError as error:
            problems.append(f"line {number}: {error}")
    if problems:
        raise LabelingError(
            "Rejected labels file, nothing imported:\n" + "\n".join(problems)
        )

    manifest = path.parent / "manifest.json"
    batch_id = (
        json.loads(manifest.read_text()).get("batch_id") if manifest.is_file() else None
    )
    imported = skipped_human = 0
    with (
        closing(sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True)) as db,
        db,
    ):
        for label in labels:
            existing = db.execute(
                "SELECT json_extract(metadata, '$.labeled_by') IS NULL "
                "FROM image_labels WHERE image_path = ?",
                (label["image_path"],),
            ).fetchone()
            if existing and existing[0]:
                skipped_human += 1
                continue
            metadata = {
                "vehicles": {
                    "bays": [
                        {"bay_id": bay_id, "vehicle_count": count}
                        for bay_id, count in label["counts"].items()
                    ]
                },
                "labeled_by": label["labeled_by"],
                "batch_id": batch_id,
                "uncertain_bays": label.get("uncertain_bays", []),
                "notes": label.get("notes", ""),
            }
            db.execute(
                "INSERT INTO image_labels (image_path, metadata) VALUES (?, ?) "
                "ON CONFLICT(image_path) DO UPDATE SET metadata = excluded.metadata, "
                "verified_at = CURRENT_TIMESTAMP",
                (label["image_path"], json.dumps(metadata)),
            )
            imported += 1
    return ImportResult(imported=imported, skipped_human=skipped_human)


def run_export(start: str, end: str, every: int = 1) -> None:
    """Export a batch for local times like '2026-09-27 12:00' and print where it went."""
    batch = export_batch(
        datetime.fromisoformat(start),  # Local wall-clock time, like filenames.
        datetime.fromisoformat(end),
        every=every,
    )
    frames = len(json.loads((batch / "manifest.json").read_text())["frames"])
    print(f"Exported {frames} frames to {batch}")


def run_import(path: Path) -> None:
    """Import a labels.jsonl file and print what was written."""
    try:
        result = import_labels(path)
    except LabelingError as error:
        raise SystemExit(str(error)) from error
    print(
        f"Imported {result.imported} labels; "
        f"skipped {result.skipped_human} human-labeled captures"
    )
