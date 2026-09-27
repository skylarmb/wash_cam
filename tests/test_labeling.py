"""Check exporting capture batches for a labeling agent and importing its labels."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime

import cv2
import numpy as np
import pytest

from wash_cam import db, labeling
from wash_cam.config import BAYS

NAMES = [
    "20260926_195900_000000.jpg",
    "20260926_200000_000000.jpg",
    "20260926_200015_000000.jpg",
    "20260926_200030_000000.jpg",
    "20260926_201500_000000.jpg",
]
LABELER = "agent:StormyCliff/gpt-6"


@pytest.fixture
def images(tmp_path):
    """A capture directory with a few frames around 20:00 local time."""
    path = tmp_path / "images"
    path.mkdir()
    for name in NAMES:
        cv2.imwrite(str(path / name), np.zeros((340, 1270, 3), dtype=np.uint8))
    return path


@pytest.fixture
def database(tmp_path):
    """A migrated database."""
    path = tmp_path / "labels.sqlite3"
    db.migrate(path)
    return path


def export(images, tmp_path, every=1):
    """Export the 20:00-20:15 window and return the batch directory."""
    return labeling.export_batch(
        datetime(2026, 9, 26, 20, 0),  # noqa: DTZ001 - capture times are local
        datetime(2026, 9, 26, 20, 15),  # noqa: DTZ001
        images,
        tmp_path / "batches",
        every=every,
    )


def test_export_writes_overlays_and_a_manifest_for_the_window(images, tmp_path):
    """Frames in [start, end) get an overlay; the manifest points at the originals."""
    batch = export(images, tmp_path)

    manifest = json.loads((batch / "manifest.json").read_text())
    names = [frame["image_path"] for frame in manifest["frames"]]
    assert names == NAMES[1:4]
    assert sorted(path.name for path in (batch / "overlay").iterdir()) == NAMES[1:4]
    first = manifest["frames"][0]
    assert first["raw"] == str(images / NAMES[1])
    assert first["overlay"] == f"overlay/{NAMES[1]}"
    assert first["local_time"] == "2026-09-26 20:00:00"
    assert set(manifest["bays"]) == set(BAYS)
    assert manifest["bays"]["vac_0"]["kind"] == "vacuum"
    assert manifest["bays"]["bay_0"]["quad"] == [list(point) for point in BAYS["bay_0"]]
    assert "bottom-center" in manifest["count_rule"]
    assert (batch / "labels.jsonl").read_text() == ""


def test_export_can_thin_frames(images, tmp_path):
    """--every keeps every Nth frame of the window."""
    batch = export(images, tmp_path, every=2)

    manifest = json.loads((batch / "manifest.json").read_text())
    assert [frame["image_path"] for frame in manifest["frames"]] == [NAMES[1], NAMES[3]]


def test_export_never_replaces_an_existing_batch(images, tmp_path):
    """Re-exporting the same window would wipe a labeler's work, so it is refused."""
    batch = export(images, tmp_path)
    (batch / "labels.jsonl").write_text("work in progress\n")

    with pytest.raises(labeling.LabelingError):
        export(images, tmp_path)
    assert (batch / "labels.jsonl").read_text() == "work in progress\n"


def label_line(name, counts, **extra):
    """One labels.jsonl line."""
    return json.dumps(
        {"image_path": name, "counts": counts, "labeled_by": LABELER, **extra}
    )


def stored(database):
    """Return stored label metadata keyed by image path."""
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute("SELECT image_path, metadata FROM image_labels")
        return {path: json.loads(metadata) for path, metadata in rows}


def write_labels(tmp_path, *lines):
    """Write a labels file inside a batch directory with a manifest."""
    batch = tmp_path / "batches" / "20260926-2000_20260926-2015"
    batch.mkdir(parents=True, exist_ok=True)
    (batch / "manifest.json").write_text(json.dumps({"batch_id": batch.name}))
    path = batch / "labels.jsonl"
    path.write_text("".join(f"{line}\n" for line in lines))
    return path


def test_import_stores_counts_with_provenance(images, database, tmp_path):
    """Labeled bays, the labeler, and its notes are stored; omitted bays stay unlabeled."""
    path = write_labels(
        tmp_path,
        label_line(
            NAMES[1],
            {"bay_0": 0, "bay_1": 1, "vac_0": 0},
            uncertain_bays=["bay_1"],
            notes="foam",
        ),
    )

    result = labeling.import_labels(path, images, database)

    assert (result.imported, result.skipped_human) == (1, 0)
    metadata = stored(database)[NAMES[1]]
    assert metadata["vehicles"]["bays"] == [
        {"bay_id": "bay_0", "vehicle_count": 0},
        {"bay_id": "bay_1", "vehicle_count": 1},
        {"bay_id": "vac_0", "vehicle_count": 0},
    ]
    assert metadata["labeled_by"] == LABELER
    assert metadata["batch_id"] == "20260926-2000_20260926-2015"
    assert metadata["uncertain_bays"] == ["bay_1"]
    assert metadata["notes"] == "foam"


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        json.dumps({"image_path": "missing.jpg", "counts": {}, "labeled_by": LABELER}),
        json.dumps(
            {"image_path": "../secret.jpg", "counts": {}, "labeled_by": LABELER}
        ),
        json.dumps(
            {"image_path": NAMES[2], "counts": {"bay_9": 1}, "labeled_by": LABELER}
        ),
        json.dumps(
            {"image_path": NAMES[2], "counts": {"bay_0": -1}, "labeled_by": LABELER}
        ),
        json.dumps(
            {"image_path": NAMES[2], "counts": {"bay_0": True}, "labeled_by": LABELER}
        ),
        json.dumps(
            {"image_path": NAMES[2], "counts": {"bay_0": 1}, "labeled_by": "human"}
        ),
        json.dumps({"image_path": NAMES[2], "counts": {"bay_0": 1}}),
    ],
)
def test_one_bad_line_rejects_the_whole_file(images, database, tmp_path, bad):
    """Validation is all-or-nothing, and the error names the bad line."""
    path = write_labels(tmp_path, label_line(NAMES[1], {"bay_0": 0}), bad)

    with pytest.raises(labeling.LabelingError, match="line 2"):
        labeling.import_labels(path, images, database)
    assert stored(database) == {}


def test_import_never_overwrites_human_labels(images, database, tmp_path):
    """Rows without labeled_by came from a person and are left alone."""
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO image_labels (image_path, metadata) VALUES (?, ?)",
            (NAMES[1], json.dumps({"vehicles": {"bays": []}})),
        )
    path = write_labels(tmp_path, label_line(NAMES[1], {"bay_0": 1}))

    result = labeling.import_labels(path, images, database)

    assert (result.imported, result.skipped_human) == (0, 1)
    assert "labeled_by" not in stored(database)[NAMES[1]]


def test_import_replaces_earlier_agent_labels(images, database, tmp_path):
    """Re-importing a batch updates the agent's own earlier labels."""
    labeling.import_labels(
        write_labels(tmp_path, label_line(NAMES[1], {"bay_0": 0})), images, database
    )

    labeling.import_labels(
        write_labels(tmp_path, label_line(NAMES[1], {"bay_0": 2})), images, database
    )

    bays = stored(database)[NAMES[1]]["vehicles"]["bays"]
    assert bays == [{"bay_id": "bay_0", "vehicle_count": 2}]
