"""Analytics tab data: one JSON payload built from captures and sessions.

Everything is measured against covered time, the time captures were actually
running, so camera outages never look like quiet hours. Revenue is an estimate
from the prices in config.py; no owner totals exist to check it against.
The page itself is static (static/analytics.js) and polls `build` via
`/api/analytics`.
"""

import sqlite3
import statistics
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from wash_cam.config import (
    BAYS,
    CAPTURE_INTERVAL,
    VACUUM_RATE_PER_MIN,
    WASH_MINIMUM,
    WASH_RATE_PER_MIN,
    bay_kind,
    bay_title,
)
from wash_cam.sessions import MAX_GAP_INTERVALS, Session, format_time

__all__ = ["MAX_DAYS", "build", "parse_range", "session_revenue"]

# Frames further apart than this are an outage; sessions use the same limit.
MAX_GAP = timedelta(seconds=CAPTURE_INTERVAL * MAX_GAP_INTERVALS)
# An hour with less covered time than this is too thin to report.
MIN_CELL_COVERAGE_S = 15 * 60
# The longest range the API serves, in local days.
MAX_DAYS = 90
# Default range when the request names none: the last week, today included.
DEFAULT_DAYS = 7
# The revenue pace chart compares a day against up to this many earlier days,
# sampled every PACE_STEP_MIN minutes, using only days at least
# PACE_MIN_COVERAGE covered so a half-captured day doesn't drag the typical
# curve down.
PACE_BASELINE_DAYS = 28
PACE_STEP_MIN = 15
PACE_MIN_COVERAGE = 0.5
# The daily calendar always shows this many days ending at the range's end,
# whatever range is selected, so it fills in over the capture retention.
CALENDAR_DAYS = 91

WASH_BAYS = [bay_id for bay_id in BAYS if bay_kind(bay_id) == "wash"]

# A span of time captures were running: (first frame, last frame), aware UTC.
Run = tuple[datetime, datetime]
# A local calendar day and hour of day, the key for hourly buckets.
HourKey = tuple[date, int]


@dataclass
class Cell:
    """Wash-bay occupied seconds and covered seconds in one local hour."""

    occupied_s: float = 0.0
    covered_s: float = 0.0


@dataclass
class DayTotal:
    """Visits and estimated revenue started on one local day, and its coverage."""

    covered_s: float
    visits: int = 0
    revenue: float = 0.0


def price(bay_id: str, seconds: float) -> float:
    """Estimated dollars for a visit of this length at the configured prices."""
    minutes = seconds / 60
    if bay_kind(bay_id) == "vacuum":
        return VACUUM_RATE_PER_MIN * minutes
    return max(WASH_MINIMUM, WASH_RATE_PER_MIN * minutes)


def session_revenue(session: Session) -> float:
    """Estimated dollars for one visit at the configured prices."""
    return price(session.bay_id, session.duration_s)


def millis(moment: datetime) -> int:
    """Epoch milliseconds, the time format the charts consume."""
    return int(moment.timestamp() * 1000)


def local_midnight(day: date) -> datetime:
    """The start of a local calendar day as an aware UTC time."""
    return datetime.combine(day, time()).astimezone().astimezone(timezone.utc)


def parse_range(
    start_text: str | None, end_text: str | None, today: date
) -> tuple[date, date]:
    """Parse inclusive local `YYYY-MM-DD` bounds, defaulting to the last week.

    The end is capped at today and the span at MAX_DAYS by moving the start.
    Raises ValueError for malformed dates or a start after the end.
    """
    end = date.fromisoformat(end_text) if end_text else today
    end = min(end, today)
    start = (
        date.fromisoformat(start_text)
        if start_text
        else end - timedelta(days=DEFAULT_DAYS - 1)
    )
    if start > end:
        raise ValueError("The range starts after it ends.")
    return max(start, end - timedelta(days=MAX_DAYS - 1)), end


def parse_time(stored: str) -> datetime:
    """Parse a stored UTC time (no offset) into an aware datetime."""
    return datetime.fromisoformat(stored).replace(tzinfo=timezone.utc)


def load_runs(db: sqlite3.Connection, start: datetime, end: datetime) -> list[Run]:
    """Return the capture runs in [start, end), split at gaps over MAX_GAP.

    SQLite finds the frames that open or close a run, so a 90-day range reads a
    few hundred boundary rows rather than half a million capture times. A lone
    frame covers no measurable time and is dropped.
    """
    # julianday arithmetic carries float error well under a millisecond, so a
    # gap of exactly MAX_GAP still joins its neighbors, as sessions do.
    limit = MAX_GAP.total_seconds() + 0.001
    rows = db.execute(
        "SELECT captured_at, "
        "  prev IS NULL OR (julianday(captured_at) - julianday(prev)) * 86400 > :limit, "
        "  next IS NULL OR (julianday(next) - julianday(captured_at)) * 86400 > :limit "
        "FROM (SELECT captured_at, "
        "  lag(captured_at) OVER (ORDER BY captured_at) AS prev, "
        "  lead(captured_at) OVER (ORDER BY captured_at) AS next "
        "  FROM image_detections WHERE captured_at >= :start AND captured_at < :end) "
        "WHERE prev IS NULL OR next IS NULL "
        "  OR (julianday(captured_at) - julianday(prev)) * 86400 > :limit "
        "  OR (julianday(next) - julianday(captured_at)) * 86400 > :limit "
        "ORDER BY captured_at",
        {"start": format_time(start), "end": format_time(end), "limit": limit},
    ).fetchall()
    runs: list[Run] = []
    run_start: datetime | None = None
    for stored, opens, closes in rows:
        moment = parse_time(stored)
        if opens:
            run_start = moment
        if closes and run_start is not None:
            if moment > run_start:
                runs.append((run_start, moment))
            run_start = None
    return runs


def load_sessions(
    db: sqlite3.Connection, start: datetime, end: datetime
) -> list[Session]:
    """Return sessions that overlap [start, end) in any configured bay."""
    rows = db.execute(
        "SELECT bay_id, started_at, ended_at, first_frame_at, last_frame_at, "
        "duration_s, frames, max_count, end_reason FROM sessions "
        "WHERE ended_at >= ? AND started_at < ? ORDER BY started_at",
        (format_time(start), format_time(end)),
    ).fetchall()
    return [
        Session(
            bay_id=row[0],
            started_at=parse_time(row[1]),
            ended_at=parse_time(row[2]),
            first_frame_at=parse_time(row[3]),
            last_frame_at=parse_time(row[4]),
            duration_s=row[5],
            frames=row[6],
            max_count=row[7],
            end_reason=row[8],
        )
        for row in rows
        if row[0] in BAYS
    ]


def overlap_s(start: datetime, end: datetime, runs: Sequence[Run]) -> float:
    """Return how many seconds of [start, end] fall inside time-ordered runs."""
    total = 0.0
    # Skip runs that end before start: begin at the last run starting at or
    # before it, which is the only earlier run that can still reach it.
    index = max(bisect_right(runs, (start, start)) - 1, 0)
    for run_start, run_end in runs[index:]:
        if run_start >= end:
            break
        overlap = min(end, run_end) - max(start, run_start)
        total += max(overlap.total_seconds(), 0.0)
    return total


def clip_runs(runs: Sequence[Run], start: datetime, end: datetime) -> list[Run]:
    """Trim runs to [start, end], dropping those outside it."""
    return [
        (max(run_start, start), min(run_end, end))
        for run_start, run_end in runs
        if run_end > start and run_start < end
    ]


def hour_pieces(
    start: datetime, end: datetime
) -> Iterator[tuple[HourKey, datetime, datetime]]:
    """Split [start, end) at local hour boundaries, keyed by local day and hour."""
    cursor = start
    while cursor < end:
        local = cursor.astimezone()
        hour_start = local.replace(minute=0, second=0, microsecond=0)
        boundary = min(hour_start + timedelta(hours=1), end)
        yield (local.date(), local.hour), cursor, boundary
        cursor = boundary


def hour_cells(
    found: Sequence[Session], runs: Sequence[Run], start: datetime, end: datetime
) -> dict[HourKey, Cell]:
    """Bucket covered time and wash-bay occupied time by local day and hour.

    The vacuum is left out: its visits run much longer and are not washes.
    """
    cells: dict[HourKey, Cell] = defaultdict(Cell)
    for run_start, run_end in runs:
        for key, piece_start, piece_end in hour_pieces(run_start, run_end):
            cells[key].covered_s += (piece_end - piece_start).total_seconds()
    for session in found:
        if bay_kind(session.bay_id) != "wash":
            continue
        clipped_start = max(session.started_at, start)
        clipped_end = min(session.ended_at, end)
        for key, piece_start, piece_end in hour_pieces(clipped_start, clipped_end):
            cells[key].occupied_s += overlap_s(piece_start, piece_end, runs)
    return dict(cells)


def quartiles(values: Sequence[float]) -> tuple[float, float]:
    """The 25th and 75th percentiles, or the lone value twice."""
    if len(values) < 2:
        return values[0], values[0]
    # Inclusive quantiles stay within the observed values.
    cuts = statistics.quantiles(values, n=4, method="inclusive")
    return cuts[0], cuts[2]


def hourly_profile(cells: dict[HourKey, Cell]) -> list[dict[str, float | int | None]]:
    """Mean wash bays occupied in each local hour of day, across days.

    Each day's hour is one sample; thinly covered hours are skipped, and an
    hour with no samples reports None rather than a misleading zero.
    """
    samples: dict[int, list[float]] = defaultdict(list)
    for (_, hour), cell in cells.items():
        if cell.covered_s >= MIN_CELL_COVERAGE_S:
            samples[hour].append(cell.occupied_s / cell.covered_s)
    profile = []
    for hour in range(24):
        values = samples.get(hour, [])
        low, high = quartiles(values) if values else (None, None)
        profile.append(
            {
                "hour": hour,
                "mean": statistics.fmean(values) if values else None,
                "p25": low,
                "p75": high,
                "days": len(values),
            }
        )
    return profile


def weekday_hours(cells: dict[HourKey, Cell]) -> list[list[float | int]]:
    """Mean wash bays occupied per [weekday, hour], pooled over covered time.

    Weekdays are 0 = Monday. Cells under MIN_CELL_COVERAGE_S are omitted.
    """
    pooled: dict[tuple[int, int], Cell] = defaultdict(Cell)
    for (day, hour), cell in cells.items():
        target = pooled[(day.weekday(), hour)]
        target.occupied_s += cell.occupied_s
        target.covered_s += cell.covered_s
    return [
        [weekday, hour, cell.occupied_s / cell.covered_s]
        for (weekday, hour), cell in sorted(pooled.items())
        if cell.covered_s >= MIN_CELL_COVERAGE_S
    ]


def occupancy_steps(
    found: Sequence[Session], start: datetime, end: datetime
) -> list[tuple[datetime, int]]:
    """Change points of how many wash bays were occupied across [start, end]."""
    events: dict[datetime, int] = defaultdict(int)
    for session in found:
        if bay_kind(session.bay_id) != "wash":
            continue
        clipped_start = max(session.started_at, start)
        clipped_end = min(session.ended_at, end)
        if clipped_end > clipped_start:
            events[clipped_start] += 1
            events[clipped_end] -= 1
    steps = [(start, 0)]
    level = 0
    for moment in sorted(events):
        level += events[moment]
        if moment == start:
            steps[0] = (start, level)
        elif level != steps[-1][1]:
            steps.append((moment, level))
    return steps


def level_seconds(
    steps: Sequence[tuple[datetime, int]], end: datetime, runs: Sequence[Run]
) -> list[float]:
    """Covered seconds spent at each count of occupied wash bays, 0..all."""
    seconds = [0.0] * (len(WASH_BAYS) + 1)
    bounds = [moment for moment, _ in steps[1:]] + [end]
    for (moment, level), until in zip(steps, bounds, strict=True):
        # Two cars can share one bay, but "all bays busy" is the cap that matters.
        seconds[min(level, len(WASH_BAYS))] += overlap_s(moment, until, runs)
    return seconds


def cumulative_revenue(
    found: Sequence[Session], day: date, until_min: int
) -> list[list[float]]:
    """Cumulative revenue of complete visits started on a local day, per step."""
    starts = sorted(
        (session.started_at.astimezone(), session_revenue(session))
        for session in found
        if session.end_reason == "left"
        and session.started_at.astimezone().date() == day
    )
    until_min = min(until_min, 24 * 60)
    minutes = list(range(0, until_min + 1, PACE_STEP_MIN))
    # End exactly at "now" when it falls between steps.
    if minutes[-1] != until_min:
        minutes.append(until_min)
    points: list[list[float]] = []
    total, index = 0.0, 0
    for minute in minutes:
        while index < len(starts) and (
            starts[index][0].hour * 60 + starts[index][0].minute < minute
        ):
            total += starts[index][1]
            index += 1
        points.append([minute, round(total, 2)])
    return points


def revenue_pace(
    found: Sequence[Session],
    day_coverage: dict[date, float],
    focus: date,
    now: datetime,
) -> dict[str, object]:
    """Compare the focus day's revenue curve against typical earlier days."""
    local_now = now.astimezone()
    until = (
        local_now.hour * 60 + local_now.minute if focus == local_now.date() else 24 * 60
    )
    baseline_days = [
        focus - timedelta(days=offset)
        for offset in range(1, PACE_BASELINE_DAYS + 1)
        if day_coverage.get(focus - timedelta(days=offset), 0.0)
        >= PACE_MIN_COVERAGE * 86400
    ]
    typical = None
    if baseline_days:
        curves = [cumulative_revenue(found, day, 24 * 60) for day in baseline_days]
        typical = [
            [points[0][0], round(statistics.fmean(point[1] for point in points), 2)]
            for points in zip(*curves, strict=True)
        ]
    return {
        "day": focus.isoformat(),
        "focus": cumulative_revenue(found, focus, until),
        "typical": typical,
        "baseline_days": [day.isoformat() for day in sorted(baseline_days)],
    }


def daily_coverage(runs: Sequence[Run]) -> dict[date, float]:
    """Covered seconds per local calendar day."""
    covered: dict[date, float] = defaultdict(float)
    for run_start, run_end in runs:
        for (day, _), piece_start, piece_end in hour_pieces(run_start, run_end):
            covered[day] += (piece_end - piece_start).total_seconds()
    return dict(covered)


def live_state(db: sqlite3.Connection, now: datetime) -> dict[str, object]:
    """What each bay looks like right now, from the latest open sessions."""
    (latest,) = db.execute("SELECT max(captured_at) FROM image_detections").fetchone()
    last_frame = parse_time(latest) if latest else None
    stale = last_frame is None or now - last_frame > MAX_GAP
    opened: dict[str, datetime] = {}
    if last_frame is not None and not stale:
        for bay_id, started, last_seen in db.execute(
            "SELECT bay_id, started_at, last_frame_at FROM sessions "
            "WHERE end_reason = 'open'"
        ):
            # An open session is only current if it saw the latest frames.
            if bay_id in BAYS and last_frame - parse_time(last_seen) <= MAX_GAP:
                opened[bay_id] = parse_time(started)
    bays = []
    for bay_id in BAYS:
        since = opened.get(bay_id)
        elapsed = (now - since).total_seconds() if since else None
        bays.append(
            {
                "bay_id": bay_id,
                "occupied": since is not None,
                "since": millis(since) if since else None,
                "elapsed_s": elapsed,
                "revenue": price(bay_id, elapsed) if elapsed is not None else None,
            }
        )
    return {
        "last_frame": millis(last_frame) if last_frame else None,
        "stale": stale,
        "bays": bays,
    }


def build(
    database: Path, start: date, end: date, now: datetime | None = None
) -> dict[str, object]:
    """Build the Analytics tab payload for inclusive local days [start, end].

    `now` is an aware time; it defaults to the current time and caps a range
    that ends today.
    """
    now = now or datetime.now(timezone.utc)
    range_start = local_midnight(start)
    range_end = min(local_midnight(end + timedelta(days=1)), now)
    # The calendar and the pace baseline read earlier days, so load back far
    # enough for both.
    focus = min(end, now.astimezone().date())
    calendar_start = end - timedelta(days=CALENDAR_DAYS - 1)
    load_start = min(range_start, local_midnight(calendar_start))
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        all_runs = load_runs(db, load_start, range_end)
        all_found = load_sessions(db, load_start, range_end)
        live = live_state(db, now)
        (first,) = db.execute(
            "SELECT min(captured_at) FROM image_detections"
        ).fetchone()

    runs = clip_runs(all_runs, range_start, range_end)
    covered = sum((run_end - run_start).total_seconds() for run_start, run_end in runs)
    found = [
        session
        for session in all_found
        if session.ended_at >= range_start and session.started_at < range_end
    ]
    # Counts and revenue attribute each visit to the range it started in.
    started = [session for session in found if session.started_at >= range_start]
    complete = [session for session in started if session.end_reason == "left"]

    steps = occupancy_steps(found, range_start, range_end)
    levels = level_seconds(steps, range_end, runs)
    cells = hour_cells(found, runs, range_start, range_end)
    coverage_by_day = daily_coverage(all_runs)

    daily = {
        calendar_start + timedelta(days=offset): DayTotal(
            covered_s=coverage_by_day.get(calendar_start + timedelta(days=offset), 0.0)
        )
        for offset in range(CALENDAR_DAYS)
    }
    for session in all_found:
        entry = daily.get(session.started_at.astimezone().date())
        if entry is not None:
            entry.visits += 1
            if session.end_reason == "left":
                entry.revenue += session_revenue(session)

    balance = []
    for bay_id in BAYS:
        occupied = sum(
            overlap_s(
                max(session.started_at, range_start),
                min(session.ended_at, range_end),
                runs,
            )
            for session in found
            if session.bay_id == bay_id
        )
        balance.append(
            {
                "bay_id": bay_id,
                "occupied_s": occupied,
                "share": occupied / covered if covered else 0.0,
                "visits": sum(1 for session in started if session.bay_id == bay_id),
                "revenue": sum(
                    session_revenue(session)
                    for session in complete
                    if session.bay_id == bay_id
                ),
            }
        )

    wash_durations = [
        session.duration_s for session in complete if bay_kind(session.bay_id) == "wash"
    ]
    # Wash visits no longer than this pay only the minimum charge.
    minimum_s = WASH_MINIMUM / WASH_RATE_PER_MIN * 60
    return {
        "meta": {
            "now": millis(now),
            "range": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "start_ms": millis(range_start),
                "end_ms": millis(range_end),
            },
            "tz": str(now.astimezone().tzinfo),
            "first_day": parse_time(first).astimezone().date().isoformat()
            if first
            else None,
            "max_days": MAX_DAYS,
            "bays": [
                {"id": bay_id, "kind": bay_kind(bay_id), "label": bay_title(bay_id)}
                for bay_id in BAYS
            ],
            "pricing": {
                "wash_rate_per_min": WASH_RATE_PER_MIN,
                "wash_minimum": WASH_MINIMUM,
                "vacuum_rate_per_min": VACUUM_RATE_PER_MIN,
                "minimum_break_even_s": minimum_s,
            },
            "capture_interval_s": CAPTURE_INTERVAL,
            "coverage_runs": [
                [millis(run_start), millis(run_end)] for run_start, run_end in runs
            ],
        },
        "live": live,
        "summary": {
            "covered_s": covered,
            "span_s": (range_end - range_start).total_seconds(),
            "outages": max(len(runs) - 1, 0),
            "revenue": sum(session_revenue(session) for session in complete),
            "wash_visits": sum(1 for s in started if bay_kind(s.bay_id) == "wash"),
            "vacuum_visits": sum(1 for s in started if bay_kind(s.bay_id) == "vacuum"),
            "median_wash_s": statistics.median(wash_durations)
            if wash_durations
            else None,
            "minimum_only_share": (
                sum(1 for seconds in wash_durations if seconds <= minimum_s)
                / len(wash_durations)
                if wash_durations
                else None
            ),
            "full_share": levels[-1] / covered if covered else None,
        },
        "sessions": [
            {
                "bay_id": session.bay_id,
                "start": millis(session.started_at),
                "end": millis(session.ended_at),
                "duration_s": session.duration_s,
                "end_reason": session.end_reason,
                "revenue": session_revenue(session)
                if session.end_reason == "left"
                else None,
            }
            for session in found
        ],
        "concurrency": {
            "steps": [[millis(moment), level] for moment, level in steps]
            + [[millis(range_end), steps[-1][1]]],
            "level_seconds": levels,
            "wash_bays": len(WASH_BAYS),
        },
        "hourly": hourly_profile(cells),
        "weekday_hours": weekday_hours(cells),
        "daily": [
            {
                "date": day.isoformat(),
                "revenue": total.revenue,
                "visits": total.visits,
                "covered_s": total.covered_s,
            }
            for day, total in daily.items()
        ],
        "pace": revenue_pace(all_found, coverage_by_day, focus, now),
        "balance": balance,
    }
