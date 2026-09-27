"""Check the analytics tab's coverage, utilization, busy hours, and durations."""

import time
from datetime import datetime, timedelta, timezone

import pytest

from wash_cam import db, sessions
from wash_cam.dashboard import analytics

# Saturday 2026-09-26 20:00 in Pacific daylight time.
EVENING = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
MAX_GAP = timedelta(seconds=45)


@pytest.fixture
def pacific(monkeypatch):
    """Bucket by the car wash's local time, then restore the process timezone."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def minutes(value):
    """Offset EVENING by a number of minutes."""
    return EVENING + timedelta(minutes=value)


def frames_every_15s(start_min, end_min):
    """Capture times every 15 s from start_min up to end_min."""
    count = int((end_min - start_min) * 4) + 1
    return [
        minutes(start_min) + timedelta(seconds=15 * index) for index in range(count)
    ]


def visit(bay_id, start_min, end_min, end_reason="left"):
    """A session in a bay between two minute offsets."""
    started, ended = minutes(start_min), minutes(end_min)
    return sessions.Session(
        bay_id=bay_id,
        started_at=started,
        ended_at=ended,
        first_frame_at=started,
        last_frame_at=ended,
        duration_s=(ended - started).total_seconds(),
        frames=1,
        max_count=1,
        end_reason=end_reason,
    )


def test_coverage_runs_break_at_capture_gaps():
    """Frames within the gap limit form one run; an outage starts a new one."""
    times = frames_every_15s(0, 10) + frames_every_15s(30, 40)

    runs = analytics.coverage_runs(times, MAX_GAP)

    assert runs == [(minutes(0), minutes(10)), (minutes(30), minutes(40))]


def test_utilization_counts_only_covered_time():
    """Occupied time during an outage counts toward neither side of the ratio."""
    runs = [(minutes(0), minutes(60)), (minutes(120), minutes(180))]
    # 30 min inside the first run, then 60 min spanning the outage.
    found = [visit("bay_1", 0, 30), visit("bay_1", 90, 150)]

    usage = analytics.utilization(found, runs)

    assert usage["bay_1"].covered_s == 120 * 60
    assert usage["bay_1"].occupied_s == 60 * 60
    assert usage["bay_1"].share == pytest.approx(0.5)
    assert usage["bay_0"].occupied_s == 0


def test_busy_hours_are_starts_per_covered_local_hour(pacific):
    """Wash visits are bucketed by local weekday and hour of their start."""
    runs = [(minutes(0), minutes(90))]
    found = [
        visit("bay_1", 10, 20),
        visit("bay_2", 40, 50),
        visit("bay_3", 70, 80),
        # The vacuum is not a wash visit.
        visit("vac_0", 15, 60),
    ]

    cells = analytics.busy_hours(found, runs)

    saturday = 5
    assert cells[(saturday, 20)].starts == 2
    assert cells[(saturday, 20)].covered_s == 3600
    assert cells[(saturday, 20)].per_hour == pytest.approx(2.0)
    assert cells[(saturday, 21)].starts == 1
    assert cells[(saturday, 21)].covered_s == 1800


def test_busy_hours_mark_thinly_covered_cells_as_no_data(pacific):
    """A cell with under 15 covered minutes has no rate rather than a misleading one."""
    cells = analytics.busy_hours([visit("bay_1", 1, 5)], [(minutes(0), minutes(10))])

    assert cells[(5, 20)].per_hour is None


def test_duration_histogram_uses_only_complete_visits():
    """Visits cut by a capture gap or still open are excluded."""
    found = [
        visit("bay_1", 0, 3),
        visit("bay_1", 10, 15),
        visit("bay_2", 0, 35),
        visit("bay_2", 40, 41, end_reason="gap"),
        visit("bay_3", 50, 52, end_reason="open"),
    ]

    histogram = analytics.duration_histogram(found)

    counts = dict(histogram.bins)
    assert counts["2–4"] == 1
    assert counts["4–6"] == 1
    assert counts["30+"] == 1
    assert sum(counts.values()) == 3
    assert histogram.median_s == 5 * 60


@pytest.fixture
def database(tmp_path):
    """A migrated, empty database."""
    path = tmp_path / "labels.sqlite3"
    db.migrate(path)
    return path


def test_render_explains_an_empty_range(database):
    """With no captures in range, the tab says so instead of drawing empty charts."""
    html = analytics.render(database, days=7, now=EVENING)

    assert "No captures in this range" in html
