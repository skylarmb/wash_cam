"""Check the analytics payload: coverage, live state, concurrency, and revenue."""

import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone

import pytest

from wash_cam import db
from wash_cam.dashboard import analytics
from wash_cam.sessions import format_time

# Saturday 2026-09-26 20:00 in Pacific daylight time.
EVENING = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
SATURDAY = date(2026, 9, 26)
WASH_BAYS = ["bay_0", "bay_1", "bay_2", "bay_3", "bay_4"]


@pytest.fixture(autouse=True)
def pacific(monkeypatch):
    """Bucket by the car wash's local time, then restore the process timezone."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def database(tmp_path):
    """A migrated, empty database."""
    path = tmp_path / "labels.sqlite3"
    db.migrate(path)
    return path


def minutes(value):
    """Offset EVENING by a number of minutes."""
    return EVENING + timedelta(minutes=value)


def add_frames(database, start_min, end_min, step_s=15):
    """Store capture times every step_s seconds from start_min to end_min."""
    count = int((end_min - start_min) * 60 / step_s) + 1
    moments = [minutes(start_min) + timedelta(seconds=step_s * i) for i in range(count)]
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executemany(
            # Adjacent ranges share their boundary frame.
            "INSERT OR IGNORE INTO image_detections "
            "(image_path, metadata, model, captured_at) "
            "VALUES (?, '{}', 'test', ?)",
            [(f"{moment.timestamp()}.jpg", format_time(moment)) for moment in moments],
        )


def add_visit(database, bay_id, start_min, end_min, end_reason="left", last_min=None):
    """Store a session in a bay between two minute offsets."""
    started, ended = minutes(start_min), minutes(end_min)
    last_seen = minutes(end_min if last_min is None else last_min)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO sessions (bay_id, started_at, ended_at, first_frame_at, "
            "last_frame_at, duration_s, frames, max_count, end_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?)",
            (
                bay_id,
                format_time(started),
                format_time(ended),
                format_time(started),
                format_time(last_seen),
                (ended - started).total_seconds(),
                end_reason,
            ),
        )


def build(database, now_min, start=SATURDAY, end=SATURDAY):
    """Build the payload as of a minute offset from EVENING."""
    return analytics.build(database, start, end, now=minutes(now_min))


def test_empty_database_reports_nothing_instead_of_zeros(database):
    """With no captures, ratios are None and the live strip is stale."""
    payload = build(database, 0)

    assert payload["meta"]["coverage_runs"] == []
    assert payload["meta"]["first_day"] is None
    assert payload["summary"]["covered_s"] == 0
    assert payload["summary"]["full_share"] is None
    assert payload["summary"]["median_wash_s"] is None
    assert payload["live"]["stale"] is True
    assert not any(bay["occupied"] for bay in payload["live"]["bays"])
    assert all(hour["mean"] is None for hour in payload["hourly"])


def test_bays_come_from_config_with_their_kind(database):
    """The page draws whatever bays config.BAYS defines."""
    bays = build(database, 0)["meta"]["bays"]

    assert [bay["id"] for bay in bays] == [*WASH_BAYS, "vac_0"]
    assert bays[-1] == {"id": "vac_0", "kind": "vacuum", "label": "Vacuum 0"}


def test_coverage_runs_break_at_capture_gaps(database):
    """A 45 s gap still joins frames; a longer outage starts a new run."""
    add_frames(database, 0, 10)
    add_frames(database, 10.75, 20)  # Exactly 45 s after the last frame.
    add_frames(database, 30, 40)
    add_frames(database, 50, 50)  # A lone frame covers no time.

    payload = build(database, 60)

    runs = payload["meta"]["coverage_runs"]
    assert runs == [
        [analytics.millis(minutes(0)), analytics.millis(minutes(20))],
        [analytics.millis(minutes(30)), analytics.millis(minutes(40))],
    ]
    assert payload["summary"]["covered_s"] == 30 * 60
    assert payload["summary"]["outages"] == 1


def test_live_shows_only_sessions_current_at_the_latest_frame(database):
    """An open session counts as live only if it saw the latest frames."""
    add_frames(database, 0, 30)
    add_visit(database, "bay_2", 25, 30, end_reason="open")
    # Open but last seen well before the latest frame, so not current.
    add_visit(database, "bay_1", 5, 10, end_reason="open")

    live = build(database, 30.25)["live"]

    bays = {bay["bay_id"]: bay for bay in live["bays"]}
    assert live["stale"] is False
    assert bays["bay_2"]["occupied"] is True
    assert bays["bay_2"]["elapsed_s"] == pytest.approx(5.25 * 60)
    # 5.25 minutes buys 6 whole tokens.
    assert bays["bay_2"]["revenue"] == pytest.approx(6.0)
    assert bays["bay_1"]["occupied"] is False


def test_live_is_stale_once_frames_stop(database):
    """A camera silent for longer than the gap limit shows nothing as occupied."""
    add_frames(database, 0, 30)
    add_visit(database, "bay_2", 25, 30, end_reason="open")

    live = build(database, 31)["live"]

    assert live["stale"] is True
    assert not any(bay["occupied"] for bay in live["bays"])


def test_full_share_is_time_with_every_wash_bay_busy(database):
    """All five wash bays overlap for 10 of 60 covered minutes; the vacuum doesn't count."""
    add_frames(database, 0, 60)
    for bay_id in WASH_BAYS:
        add_visit(database, bay_id, 10, 20)
    add_visit(database, "bay_0", 30, 40)
    add_visit(database, "vac_0", 0, 60)

    # Build just after the last frame, as the live server always does.
    payload = build(database, 60.1)

    levels = payload["concurrency"]["level_seconds"]
    assert levels[5] == pytest.approx(10 * 60)
    assert levels[1] == pytest.approx(10 * 60)
    assert levels[0] == pytest.approx(40 * 60)
    assert payload["summary"]["full_share"] == pytest.approx(1 / 6)
    steps = [level for _, level in payload["concurrency"]["steps"]]
    assert steps == [0, 5, 0, 1, 0, 0]


def test_occupied_time_during_an_outage_counts_toward_neither_side(database):
    """A bay busy through an outage is only busy for its covered part."""
    add_frames(database, 0, 10)
    add_frames(database, 20, 30)
    add_visit(database, "bay_1", 5, 25)

    balance = {row["bay_id"]: row for row in build(database, 30.1)["balance"]}

    assert balance["bay_1"]["occupied_s"] == pytest.approx(10 * 60)
    assert balance["bay_1"]["share"] == pytest.approx(0.5)


def test_hourly_profile_skips_thinly_covered_hours(database):
    """Hours with under 15 covered minutes report None rather than zero."""
    add_frames(database, 0, 60)  # 20:00-21:00, fully covered.
    add_frames(database, 60, 70)  # 21:00-21:10, too thin.
    add_visit(database, "bay_0", 0, 30)
    add_visit(database, "bay_1", 0, 30)

    hourly = {hour["hour"]: hour for hour in build(database, 70)["hourly"]}

    assert hourly[20]["mean"] == pytest.approx(1.0)
    assert hourly[20]["days"] == 1
    assert hourly[21]["mean"] is None


def test_revenue_counts_only_complete_visits(database):
    """Visits cut by a gap or still open have unknown length, so earn nothing yet."""
    add_frames(database, 0, 60)
    add_visit(database, "bay_0", 0, 5)  # $5.
    add_visit(database, "bay_1", 10, 12)  # $3 minimum.
    add_visit(database, "bay_2", 20, 30, end_reason="gap")
    add_visit(database, "vac_0", 0, 8)  # $2.

    payload = build(database, 60)

    assert payload["summary"]["revenue"] == pytest.approx(10.0)
    assert payload["summary"]["wash_visits"] == 3
    assert payload["summary"]["vacuum_visits"] == 1
    assert payload["summary"]["minimum_only_share"] == pytest.approx(0.5)
    saturday = next(day for day in payload["daily"] if day["date"] == "2026-09-26")
    assert saturday["revenue"] == pytest.approx(10.0)
    assert saturday["visits"] == 4
    assert len(payload["daily"]) == analytics.CALENDAR_DAYS


def test_pace_compares_against_earlier_well_covered_days(database):
    """The typical curve averages earlier days at least half captured."""
    # Friday: captured all day (00:00-24:00 local), one $10 wash at 20:00.
    friday_start = -20 * 60 - 24 * 60
    add_frames(database, friday_start, friday_start + 24 * 60 - 0.25)
    add_visit(database, "bay_0", -24 * 60, -24 * 60 + 10)
    # Saturday so far: one $4 wash at 20:00.
    add_frames(database, 0, 30)
    add_visit(database, "bay_0", 0, 4)

    pace = build(database, 30)["pace"]

    assert pace["baseline_days"] == ["2026-09-25"]
    assert pace["focus"][-1] == [20 * 60 + 30, 4.0]
    typical = dict(pace["typical"])
    assert typical[20 * 60] == 0.0
    assert typical[20 * 60 + 15] == 10.0


def test_pace_has_no_baseline_without_earlier_days(database):
    """A thinly captured earlier day isn't typical of anything."""
    add_frames(database, -24 * 60, -23 * 60)
    add_frames(database, 0, 30)

    assert build(database, 30)["pace"]["typical"] is None


@pytest.mark.parametrize(
    ("bay_id", "length_min", "dollars"),
    [
        ("bay_1", 2, 3.00),  # Inside the 3-token start.
        ("bay_1", 3, 3.00),  # Exactly the start.
        ("bay_1", 3.01, 4.00),  # Any time past a token buys the next one.
        ("bay_1", 4.5, 5.00),
        ("vac_0", 2, 0.50),  # Vacuum: per-minute placeholder, no tokens.
    ],
)
def test_price_buys_whole_tokens_with_a_start_minimum(bay_id, length_min, dollars):
    """A wash buys whole $1 one-minute tokens, 3 to start; the vacuum is $0.25/min."""
    assert analytics.price(bay_id, length_min * 60) == pytest.approx(dollars)


TODAY = date(2026, 9, 27)


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (None, None, (date(2026, 9, 21), TODAY)),  # Default: the last week.
        ("2026-09-01", "2026-09-05", (date(2026, 9, 1), date(2026, 9, 5))),
        ("2026-09-20", "2026-12-01", (date(2026, 9, 20), TODAY)),  # No future.
        ("2026-01-01", None, (TODAY - timedelta(days=89), TODAY)),  # Span cap.
    ],
)
def test_parse_range_defaults_and_clamps(start, end, expected):
    """Ranges default to a week, stop at today, and span at most MAX_DAYS."""
    assert analytics.parse_range(start, end, TODAY) == expected


@pytest.mark.parametrize(
    ("start", "end"), [("2026-09-05", "2026-09-01"), ("yesterday", None)]
)
def test_parse_range_rejects_bad_dates(start, end):
    """Malformed or backwards ranges are an error, not a silent default."""
    with pytest.raises(ValueError):
        analytics.parse_range(start, end, TODAY)
