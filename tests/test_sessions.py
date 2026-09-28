"""Check that per-frame bay counts become debounced visit sessions."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from wash_cam import db, sessions

START = datetime(2026, 9, 27, 18, 0, 0, tzinfo=timezone.utc)
INTERVAL = 15.0


def frames(counts, start=START, interval=INTERVAL):
    """Build (time, count) frames spaced one interval apart."""
    return [
        (start + timedelta(seconds=index * interval), count)
        for index, count in enumerate(counts)
    ]


def at(index):
    """Return the time of the frame at an index."""
    return START + timedelta(seconds=index * INTERVAL)


def build(counts):
    """Sessions for one bay from a list of counts."""
    return sessions.build_sessions("bay_1", frames(counts), INTERVAL)


def test_a_clean_visit_becomes_one_session():
    """Arrival and departure are estimated halfway between frames."""
    [visit] = build([0, 0, 1, 1, 1, 1, 0, 0, 0])

    assert visit.bay_id == "bay_1"
    assert visit.first_frame_at == at(2)
    assert visit.last_frame_at == at(5)
    # The car arrived after frame 1 and left before frame 6.
    assert visit.started_at == at(2) - timedelta(seconds=INTERVAL / 2)
    assert visit.ended_at == at(5) + timedelta(seconds=INTERVAL / 2)
    assert visit.duration_s == 4 * INTERVAL
    assert visit.frames == 4
    assert visit.end_reason == "left"


def test_a_one_frame_miss_does_not_split_a_visit():
    """A single empty frame mid-visit, such as a missed foam-covered car, is bridged."""
    [visit] = build([0, 1, 1, 0, 1, 1, 0, 0])

    assert visit.first_frame_at == at(1)
    assert visit.last_frame_at == at(5)
    assert visit.frames == 4


def test_a_one_frame_phantom_is_ignored():
    """A single occupied frame is not a visit."""
    assert build([0, 0, 1, 0, 0, 0]) == []


def test_back_to_back_cars_one_frame_apart_merge():
    """Two cars separated by one empty frame look like one visit; two frames split them."""
    assert len(build([1, 1, 0, 1, 1, 0, 0])) == 1
    assert len(build([1, 1, 0, 0, 1, 1, 0, 0])) == 2


def test_a_capture_gap_ends_the_visit():
    """A visit is never stretched across a capture outage."""
    before = frames([0, 1, 1, 1])
    # Capture stops for ten minutes while the bay is still occupied.
    after = frames([1, 1, 0, 0], start=before[-1][0] + timedelta(minutes=10))

    first, second = sessions.build_sessions("bay_1", before + after, INTERVAL)

    assert first.end_reason == "gap"
    assert first.ended_at == first.last_frame_at
    assert second.started_at == second.first_frame_at
    assert second.end_reason == "left"


def test_a_visit_in_progress_is_open():
    """A visit still occupied at the latest frame has no departure yet."""
    [visit] = build([0, 1, 1, 1])

    assert visit.end_reason == "open"
    assert visit.ended_at == at(3)


def test_the_largest_count_is_kept():
    """A bay that briefly held two vehicles records the peak."""
    [visit] = build([1, 2, 1, 0, 0])

    assert visit.max_count == 2


@pytest.fixture
def database(tmp_path):
    """Create a migrated database."""
    path = tmp_path / "labels.sqlite3"
    db.migrate(path)
    return path


def insert_frames(database, counts, offset=0):
    """Store detections whose bay_1 count follows counts; other bays stay empty."""
    with closing(sqlite3.connect(database)) as connection, connection:
        for index, count in enumerate(counts, start=offset):
            metadata = {
                "vehicles": {
                    "bays": [
                        {"bay_id": "bay_0", "vehicle_count": 0},
                        {"bay_id": "bay_1", "vehicle_count": count},
                    ]
                }
            }
            connection.execute(
                "INSERT INTO image_detections "
                "(image_path, metadata, model, captured_at) VALUES (?, ?, ?, ?)",
                (
                    f"{index}.jpg",
                    json.dumps(metadata),
                    "fake",
                    sessions.format_time(at(index)),
                ),
            )


def stored_sessions(database):
    """Return stored sessions as comparable tuples."""
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(
            "SELECT bay_id, started_at, ended_at, duration_s, frames, max_count, "
            "end_reason FROM sessions ORDER BY bay_id, started_at"
        ).fetchall()


def test_update_extends_stored_sessions_like_a_full_rebuild(database):
    """Updating as frames arrive matches rebuilding from scratch."""
    counts = [0, 1, 1, 1, 0, 0, 1, 0, 1, 1, 1, 1, 0, 0, 1, 1]
    # Frames arrive in batches, splitting visits across updates.
    for start, end in [(0, 3), (3, 7), (7, 10), (10, len(counts))]:
        insert_frames(database, counts[start:end], offset=start)
        sessions.update_sessions(database, INTERVAL)
    incremental = stored_sessions(database)

    sessions.rebuild_sessions(database, INTERVAL)

    assert incremental == stored_sessions(database)
    assert [row[-1] for row in incremental] == ["left", "left", "open"]


def test_frames_too_dark_to_see_are_an_outage_not_an_empty_bay(database):
    """A car still there when frames go dark ends as a gap, not a departure."""
    insert_frames(database, [0, 1, 1, 1, 0, 0, 0, 0, 0, 0])
    # Frames 4-7 are near-black: the detector saw nothing, but nothing was seen.
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE image_detections SET brightness = CASE "
            "WHEN image_path IN ('4.jpg', '5.jpg', '6.jpg', '7.jpg') THEN 1.4 "
            "ELSE 30 END"
        )

    sessions.rebuild_sessions(database, INTERVAL)

    assert [row[-1] for row in stored_sessions(database)] == ["gap"]
