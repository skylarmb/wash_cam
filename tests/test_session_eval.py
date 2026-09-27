"""Check that predicted sessions are scored against sessions from human labels."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

from wash_cam import db, session_eval, sessions

START = datetime(2026, 9, 27, 18, 0, 0, tzinfo=timezone.utc)
INTERVAL = 15.0


def at(index):
    """Return the time of the frame at an index."""
    return START + timedelta(seconds=index * INTERVAL)


def visit(first, last, bay_id="bay_1"):
    """A session occupying frames first..last, halfway to each empty neighbor."""
    started = at(first) - timedelta(seconds=INTERVAL / 2)
    ended = at(last) + timedelta(seconds=INTERVAL / 2)
    return sessions.Session(
        bay_id=bay_id,
        started_at=started,
        ended_at=ended,
        first_frame_at=at(first),
        last_frame_at=at(last),
        duration_s=(ended - started).total_seconds(),
        frames=last - first + 1,
        max_count=1,
        end_reason="left",
    )


def test_matched_sessions_report_timing_errors():
    """Overlapping sessions match; start, end, and duration errors are measured."""
    truth = [visit(2, 9)]
    # The detector saw the car one frame late and lost it two frames early.
    predicted = [visit(3, 7)]

    score = session_eval.score_sessions(truth, predicted)

    assert (score.true_sessions, score.predicted_sessions, score.matched) == (1, 1, 1)
    assert (score.missed, score.phantom) == (0, 0)
    assert score.mean_start_error_s == INTERVAL
    assert score.mean_end_error_s == 2 * INTERVAL
    assert score.mean_duration_error_s == 3 * INTERVAL
    assert score.true_minutes == pytest.approx(8 * INTERVAL / 60)
    assert score.predicted_minutes == pytest.approx(5 * INTERVAL / 60)


def test_unmatched_sessions_are_missed_or_phantom():
    """A true session with no overlap is missed; a predicted one is a phantom."""
    truth = [visit(2, 5), visit(20, 25)]
    predicted = [visit(3, 5), visit(40, 42)]

    score = session_eval.score_sessions(truth, predicted)

    assert (score.matched, score.missed, score.phantom) == (1, 1, 1)


def test_each_predicted_session_matches_at_most_once():
    """Two true visits covered by one merged prediction count one match and one miss."""
    truth = [visit(2, 5), visit(7, 10)]
    predicted = [visit(2, 10)]

    score = session_eval.score_sessions(truth, predicted)

    assert (score.matched, score.missed, score.phantom) == (1, 1, 0)


@pytest.fixture
def database(tmp_path):
    """Create a migrated database."""
    path = tmp_path / "labels.sqlite3"
    db.migrate(path)
    return path


def bays_metadata(counts):
    """Metadata with the given bay_1 count and an empty bay_0."""
    return json.dumps(
        {
            "vehicles": {
                "bays": [
                    {"bay_id": "bay_0", "vehicle_count": 0},
                    {"bay_id": "bay_1", "vehicle_count": counts},
                ]
            }
        }
    )


def insert(database, labeled, detected):
    """Store frames with labels (None for unlabeled) and detections for bay_1."""
    with closing(sqlite3.connect(database)) as connection, connection:
        for index, (label, count) in enumerate(zip(labeled, detected, strict=True)):
            name = f"{index}.jpg"
            connection.execute(
                "INSERT INTO image_detections "
                "(image_path, metadata, model, captured_at) VALUES (?, ?, ?, ?)",
                (name, bays_metadata(count), "fake", sessions.format_time(at(index))),
            )
            if label is not None:
                connection.execute(
                    "INSERT INTO image_labels (image_path, metadata) VALUES (?, ?)",
                    (name, bays_metadata(label)),
                )


def test_evaluate_compares_labels_and_detections_on_the_same_frames(database):
    """Debouncing bridges the detector's one-frame miss, so the visit matches exactly."""
    labeled = [0, 1, 1, 1, 1, 1, 0, 0]
    detected = [0, 1, 1, 0, 1, 1, 0, 0]
    insert(database, labeled, detected)

    scores = session_eval.evaluate_sessions(database, INTERVAL)

    assert scores["bay_1"].matched == 1
    assert scores["bay_1"].phantom == 0
    assert scores["bay_1"].mean_duration_error_s == 0
    assert scores["all"].true_sessions == 1


def test_evaluate_uses_the_given_thresholds(database):
    """Without debouncing, the one-frame miss splits the visit into two."""
    insert(database, [0, 1, 1, 1, 1, 1, 0, 0], [0, 1, 1, 0, 1, 1, 0, 0])

    scores = session_eval.evaluate_sessions(
        database, INTERVAL, open_frames=1, close_frames=1
    )

    assert scores["bay_1"].predicted_sessions == 2
    assert (scores["bay_1"].matched, scores["bay_1"].phantom) == (1, 1)


def test_evaluate_ignores_unlabeled_frames(database):
    """Detections outside labeled frames are not scored."""
    # Only the first four frames are labeled; the car detected later is not scored.
    labeled = [0, 0, 0, 0, None, None, None, None]
    detected = [0, 0, 0, 0, 1, 1, 1, 0]
    insert(database, labeled, detected)

    scores = session_eval.evaluate_sessions(database, INTERVAL)

    assert scores["all"].predicted_sessions == 0
