"""Analytics tab: busy hours, bay utilization, and wash durations from sessions.

Everything is measured against covered time, the time captures were actually
running, so camera outages never look like quiet hours. Revenue is an estimate
from the prices in config.py; no owner totals exist to check it against.
"""

import sqlite3
import statistics
from collections import defaultdict
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

from wash_cam.config import (
    BAYS,
    CAPTURE_INTERVAL,
    VACUUM_RATE_PER_MIN,
    WASH_MINIMUM,
    WASH_RATE_PER_MIN,
)
from wash_cam.sessions import MAX_GAP_INTERVALS, Session, format_time

__all__ = [
    "busy_hours",
    "coverage_runs",
    "duration_histogram",
    "render",
    "revenue_totals",
    "session_revenue",
    "utilization",
]

# Frames further apart than this are an outage; sessions use the same limit.
MAX_GAP = timedelta(seconds=CAPTURE_INTERVAL * MAX_GAP_INTERVALS)
# A busy-hours cell with less covered time than this shows "no data".
MIN_CELL_COVERAGE_S = 15 * 60
# Duration histogram: 2-minute bins up to 30 minutes, then one "30+" bin.
BIN_MINUTES = 2
MAX_BIN_MINUTES = 30
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# Selectable ranges in days, and the upper bound for ?days=.
RANGE_CHOICES = (1, 7, 30, 90)
MAX_DAYS = 90
# Sequential blue ramp (light = few visits, dark = many) from the dataviz palette.
RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

# A span of time captures were running: (first frame, last frame), aware UTC.
Run = tuple[datetime, datetime]


@dataclass(frozen=True)
class Utilization:
    """Occupied and covered seconds for one bay."""

    occupied_s: float
    covered_s: float

    @property
    def share(self) -> float:
        """Fraction of covered time the bay was occupied."""
        return self.occupied_s / self.covered_s if self.covered_s else 0.0


@dataclass
class Cell:
    """Wash visits starting in one local weekday-hour, and its covered time."""

    starts: int = 0
    covered_s: float = 0.0

    @property
    def per_hour(self) -> float | None:
        """Visits per covered hour, or None when too little of the hour was covered."""
        if self.covered_s < MIN_CELL_COVERAGE_S:
            return None
        return self.starts / (self.covered_s / 3600)


@dataclass(frozen=True)
class Histogram:
    """Counts of complete visits by duration, with summary statistics."""

    # (label, count) per bin in minutes, e.g. ("4–6", 3).
    bins: list[tuple[str, int]]
    count: int
    median_s: float | None
    p90_s: float | None


def coverage_runs(times: Sequence[datetime], max_gap: timedelta = MAX_GAP) -> list[Run]:
    """Merge time-ordered capture times into runs that break at outages."""
    runs: list[Run] = []
    for moment in times:
        if runs and moment - runs[-1][1] <= max_gap:
            runs[-1] = (runs[-1][0], moment)
        else:
            runs.append((moment, moment))
    # A lone frame covers no measurable time.
    return [(start, end) for start, end in runs if end > start]


def overlap_s(start: datetime, end: datetime, runs: Sequence[Run]) -> float:
    """Return how many seconds of [start, end] fall inside the runs."""
    total = 0.0
    for run_start, run_end in runs:
        overlap = min(end, run_end) - max(start, run_start)
        total += max(overlap.total_seconds(), 0.0)
    return total


def utilization(
    found: Sequence[Session], runs: Sequence[Run]
) -> dict[str, Utilization]:
    """Return each bay's occupied share of covered time."""
    covered = sum((end - start).total_seconds() for start, end in runs)
    occupied: dict[str, float] = dict.fromkeys(BAYS, 0.0)
    for session in found:
        if session.bay_id in occupied:
            occupied[session.bay_id] += overlap_s(
                session.started_at, session.ended_at, runs
            )
    return {
        bay_id: Utilization(occupied_s=seconds, covered_s=covered)
        for bay_id, seconds in occupied.items()
    }


def local_hour(moment: datetime) -> tuple[int, int]:
    """Return the (weekday, hour) of a time in the server's local timezone."""
    local = moment.astimezone()
    return local.weekday(), local.hour


def busy_hours(
    found: Sequence[Session], runs: Sequence[Run]
) -> dict[tuple[int, int], Cell]:
    """Bucket wash-bay visit starts and covered time by local weekday and hour.

    The vacuum is left out: its visits run much longer and are not washes.
    """
    cells: dict[tuple[int, int], Cell] = defaultdict(Cell)
    for run_start, run_end in runs:
        cursor = run_start
        while cursor < run_end:
            local = cursor.astimezone()
            hour_start = local.replace(minute=0, second=0, microsecond=0)
            boundary = min(hour_start + timedelta(hours=1), run_end)
            cells[(local.weekday(), local.hour)].covered_s += (
                boundary - cursor
            ).total_seconds()
            cursor = boundary
    for session in found:
        if session.bay_id.startswith("bay_"):
            cells[local_hour(session.started_at)].starts += 1
    return dict(cells)


def duration_histogram(found: Sequence[Session]) -> Histogram:
    """Histogram of complete visit durations; visits cut by a gap or still open are left out."""
    durations = [
        session.duration_s for session in found if session.end_reason == "left"
    ]
    last = MAX_BIN_MINUTES // BIN_MINUTES
    counts = [0] * (last + 1)
    for seconds in durations:
        counts[min(int(seconds / 60 // BIN_MINUTES), last)] += 1
    labels = [
        f"{index * BIN_MINUTES}–{(index + 1) * BIN_MINUTES}" for index in range(last)
    ] + [f"{MAX_BIN_MINUTES}+"]
    if len(durations) >= 2:
        # Inclusive quantiles stay within the observed durations.
        p90 = statistics.quantiles(durations, n=10, method="inclusive")[-1]
    else:
        p90 = durations[0] if durations else None
    return Histogram(
        bins=list(zip(labels, counts, strict=True)),
        count=len(durations),
        median_s=statistics.median(durations) if durations else None,
        p90_s=p90,
    )


def session_revenue(session: Session) -> float:
    """Estimated dollars for one visit at the configured prices."""
    minutes = session.duration_s / 60
    if session.bay_id.startswith("vac_"):
        return VACUUM_RATE_PER_MIN * minutes
    return max(WASH_MINIMUM, WASH_RATE_PER_MIN * minutes)


def revenue_totals(
    found: Sequence[Session],
) -> tuple[dict[date, float], dict[int, float]]:
    """Sum complete visits' revenue by local start day and by local start hour.

    Visits cut off by a gap or still open have unknown length, so they are left out.
    """
    by_day: dict[date, float] = defaultdict(float)
    by_hour: dict[int, float] = defaultdict(float)
    for session in found:
        if session.end_reason != "left":
            continue
        local = session.started_at.astimezone()
        by_day[local.date()] += session_revenue(session)
        by_hour[local.hour] += session_revenue(session)
    return dict(by_day), dict(by_hour)


def render_revenue(found: Sequence[Session]) -> str:
    """Estimated revenue tables by day and by hour of day."""
    by_day, by_hour = revenue_totals(found)
    note = (
        '<p class="note">Estimates only, from complete visits: wash bays at '
        f"${WASH_RATE_PER_MIN:.2f}/min with a ${WASH_MINIMUM:.2f} minimum, the "
        f"vacuum at ${VACUUM_RATE_PER_MIN:.2f}/min (a placeholder price). No owner "
        "totals exist to check these against.</p>"
    )
    if not by_day:
        return f"<h2>Estimated revenue</h2>{note}<p>No complete visits yet.</p>"

    def table(caption: str, rows: dict) -> str:
        """One two-column table of estimated dollars."""
        body = "".join(
            f"<tr><th>{escape(str(key))}</th><td>${value:.2f}</td></tr>"
            for key, value in rows.items()
        )
        return f'<table class="revenue"><caption>{caption}</caption>{body}</table>'

    # Sort by date first, then label, so weekday names never decide the order.
    days = {day.strftime("%a %Y-%m-%d"): by_day[day] for day in sorted(by_day)}
    hours = {f"{hour:02d}:00": by_hour[hour] for hour in sorted(by_hour)}
    return (
        f"<h2>Estimated revenue</h2>{note}"
        f"<p><strong>${sum(by_day.values()):.2f}</strong> estimated in this range</p>"
        f"{table('By day', days)}"
        f"{table('By hour of day, all days', hours)}"
    )


def parse_time(stored: str) -> datetime:
    """Parse a stored UTC time (no offset) into an aware datetime."""
    return datetime.fromisoformat(stored).replace(tzinfo=timezone.utc)


def load(database: Path, since: datetime) -> tuple[list[datetime], list[Session]]:
    """Return capture times and sessions touching the range, read-only."""
    since_text = format_time(since)
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        times = [
            parse_time(moment)
            for (moment,) in db.execute(
                "SELECT captured_at FROM image_detections "
                "WHERE captured_at >= ? ORDER BY captured_at",
                (since_text,),
            )
        ]
        rows = db.execute(
            "SELECT bay_id, started_at, ended_at, first_frame_at, last_frame_at, "
            "duration_s, frames, max_count, end_reason FROM sessions "
            "WHERE ended_at >= ?",
            (since_text,),
        ).fetchall()
    found = [
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
    ]
    return times, found


def minutes_text(seconds: float | None) -> str:
    """Format seconds as minutes for display."""
    return "–" if seconds is None else f"{seconds / 60:.1f} min"


def render_range_links(days: int) -> str:
    """Links to switch the time range, with the current one marked."""
    links = []
    for choice in RANGE_CHOICES:
        label = "1 day" if choice == 1 else f"{choice} days"
        current = ' aria-current="true"' if choice == days else ""
        links.append(f'<a href="/analytics?days={choice}"{current}>{label}</a>')
    return f'<nav class="ranges" aria-label="Time range">{"".join(links)}</nav>'


def render_utilization(usage: dict[str, Utilization]) -> str:
    """Horizontal bars of each bay's occupied share of covered time."""
    rows = []
    for bay_id, value in usage.items():
        percent = value.share * 100
        tooltip = (
            f"{bay_id}: occupied {value.occupied_s / 3600:.1f} h "
            f"of {value.covered_s / 3600:.1f} covered h ({percent:.1f}%)"
        )
        rows.append(
            f'<div class="bar-row" title="{escape(tooltip, quote=True)}">'
            f'<span class="bar-label">{escape(bay_id)}</span>'
            '<span class="bar-track">'
            f'<span class="bar" style="width:{percent:.2f}%"></span></span>'
            f'<span class="bar-value">{percent:.1f}%</span></div>'
        )
    return "".join(rows)


def ramp_step(rate: float, peak: float) -> int:
    """Pick a ramp step for a rate; any visits at all skip the lightest step."""
    if peak == 0 or rate == 0:
        return 0
    return 1 + round(rate / peak * (len(RAMP) - 2))


def render_heatmap(cells: dict[tuple[int, int], Cell]) -> str:
    """A weekday-by-hour table shaded by wash visits per covered hour."""
    rates = [cell.per_hour for cell in cells.values() if cell.per_hour is not None]
    peak = max(rates, default=0.0)
    header = "".join(f'<th scope="col">{hour}</th>' for hour in range(24))
    body = []
    for weekday, name in enumerate(WEEKDAYS):
        row = [f'<th scope="row">{name}</th>']
        for hour in range(24):
            cell = cells.get((weekday, hour), Cell())
            rate = cell.per_hour
            covered = f"{cell.covered_s / 3600:.2f} covered h"
            if rate is None:
                tip = f"{name} {hour}:00 · no data ({covered})"
                row.append(
                    f'<td class="nodata" title="{escape(tip, quote=True)}"></td>'
                )
                continue
            tip = (
                f"{name} {hour}:00 · {rate:.2f} visits/h "
                f"({cell.starts} visits in {covered})"
            )
            color = RAMP[ramp_step(rate, peak)]
            row.append(
                f'<td style="background:{color}" title="{escape(tip, quote=True)}"></td>'
            )
        body.append(f"<tr>{''.join(row)}</tr>")
    legend = "".join(f'<span style="background:{color}"></span>' for color in RAMP)
    return (
        '<table class="heat"><caption>Local time. Hover a cell for its numbers.'
        f"</caption><thead><tr><th></th>{header}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
        f'<p class="legend"><span class="ramp">{legend}</span> 0 to {peak:.1f} '
        'visits per covered hour <span class="nodata-key"></span> no data</p>'
    )


def render_histogram(title: str, histogram: Histogram) -> str:
    """Vertical bars of complete visits per duration bin, with median and p90."""
    if not histogram.count:
        return f"<h3>{escape(title)}</h3><p>No complete visits yet.</p>"
    peak = max(count for _, count in histogram.bins)
    bars = "".join(
        f'<div class="col" title="{escape(f"{label} min: {count} visits", quote=True)}">'
        f'<span class="col-bar" style="height:{count / peak * 100:.1f}%"></span>'
        f'<span class="col-label">{escape(label)}</span></div>'
        for label, count in histogram.bins
    )
    return (
        f"<h3>{escape(title)}</h3>"
        f'<p class="summary">{histogram.count} complete visits · median '
        f"{minutes_text(histogram.median_s)} · 90th percentile "
        f"{minutes_text(histogram.p90_s)}</p>"
        f'<div class="hist" aria-label="Visits per duration bin, minutes">{bars}</div>'
    )


STYLE = """<style>
.viz-root { --series: #2a78d6; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --nodata: #f0efec; color: var(--ink); }
.viz-root h2 { margin-top: 2rem; }
.viz-root .ranges { display: flex; gap: .75rem; margin: .5rem 0 1rem; }
.viz-root .ranges a { color: #234; }
.viz-root .ranges a[aria-current] { font-weight: bold; text-decoration: none; }
.viz-root .tiles { display: flex; flex-wrap: wrap; gap: 1rem; }
.viz-root .tile { border: 1px solid var(--grid); border-radius: .4rem; padding: .75rem 1rem; }
.viz-root .tile strong { display: block; font-size: 1.6rem; }
.viz-root .tile span, .viz-root .note, .viz-root .summary, .viz-root caption,
.viz-root .legend { color: var(--ink-2); font-size: .9rem; }
.viz-root .bar-row { display: grid; grid-template-columns: 4rem 1fr 4rem; gap: .5rem;
  align-items: center; margin: .35rem 0; }
.viz-root .bar-track { background: var(--grid); height: .7rem; border-radius: 4px; }
.viz-root .bar { display: block; height: 100%; background: var(--series);
  border-radius: 4px; }
.viz-root .bar-value { text-align: right; font-variant-numeric: tabular-nums; }
.viz-root .heat-wrap { overflow-x: auto; }
.viz-root .revenue { border-collapse: collapse; margin: .5rem 2rem 1rem 0;
  display: inline-table; vertical-align: top; font-variant-numeric: tabular-nums; }
.viz-root .revenue caption { text-align: left; }
.viz-root .revenue th, .viz-root .revenue td { padding: .2rem .75rem .2rem 0; text-align: left; }
.viz-root .revenue td { text-align: right; }
.viz-root .heat { border-collapse: separate; border-spacing: 2px; font-size: .75rem; }
.viz-root .heat caption { text-align: left; }
.viz-root .heat th { color: var(--muted); font-weight: normal; }
.viz-root .heat td { width: 1.6rem; height: 1.4rem; border-radius: 3px; }
.viz-root .nodata, .viz-root .nodata-key { background: repeating-linear-gradient(
  45deg, var(--nodata), var(--nodata) 3px, #fcfcfb 3px, #fcfcfb 6px); }
.viz-root .legend { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; }
.viz-root .ramp { display: inline-flex; gap: 2px; }
.viz-root .ramp span, .viz-root .nodata-key { display: inline-block; width: 1rem;
  height: .8rem; border-radius: 2px; }
.viz-root .hist { display: flex; align-items: flex-end; gap: 2px; height: 9rem;
  border-bottom: 1px solid var(--grid); }
.viz-root .col { flex: 1; display: flex; flex-direction: column;
  justify-content: flex-end; height: 100%; position: relative; }
.viz-root .col-bar { display: block; background: var(--series);
  border-radius: 4px 4px 0 0; min-height: 1px; }
.viz-root .col-label { position: absolute; bottom: -1.3rem; font-size: .65rem;
  color: var(--muted); white-space: nowrap; }
.viz-root .hist-wrap { margin-bottom: 2.5rem; }
</style>"""


def render(database: Path, days: int = 7, now: datetime | None = None) -> str:
    """Return the analytics tab's HTML for the last `days` days."""
    days = min(max(days, 1), MAX_DAYS)
    now = now or datetime.now(timezone.utc)
    times, found = load(database, now - timedelta(days=days))
    runs = coverage_runs(times)
    header = f"<h2>Analytics</h2>{render_range_links(days)}"
    if not runs:
        return (
            f'{STYLE}<div class="viz-root">{header}'
            "<p>No captures in this range, so there is nothing to measure yet.</p>"
            "</div>"
        )

    covered_h = sum((end - start).total_seconds() for start, end in runs) / 3600
    wash = [session for session in found if session.bay_id.startswith("bay_")]
    vacuum = [session for session in found if session.bay_id.startswith("vac_")]
    span = f"{days} day{'s' if days > 1 else ''}"
    tiles = (
        '<div class="tiles">'
        f'<div class="tile"><strong>{covered_h:.1f} h</strong>'
        f"<span>covered by captures in the last {span}</span></div>"
        f'<div class="tile"><strong>{len(wash)}</strong><span>wash visits</span></div>'
        f'<div class="tile"><strong>{len(vacuum)}</strong>'
        "<span>vacuum visits</span></div></div>"
    )
    return (
        f'{STYLE}<div class="viz-root">{header}{tiles}'
        '<p class="note">All numbers count only time when captures were running. '
        "Occupied bay time is an upper bound on paid time.</p>"
        "<h2>Busy hours</h2>"
        '<p class="note">Wash-bay visits per covered hour, by the hour each visit '
        "started.</p>"
        f'<div class="heat-wrap">{render_heatmap(busy_hours(found, runs))}</div>'
        "<h2>Bay utilization</h2>"
        '<p class="note">Share of covered time each bay was occupied.</p>'
        f"{render_utilization(utilization(found, runs))}"
        "<h2>Visit durations</h2>"
        '<p class="note">Complete visits only; visits cut off by a capture gap or '
        "still in progress are left out.</p>"
        '<div class="hist-wrap">'
        f"{render_histogram('Wash bays', duration_histogram(wash))}</div>"
        '<div class="hist-wrap">'
        f"{render_histogram('Vacuum', duration_histogram(vacuum))}</div>"
        f"{render_revenue(found)}"
        "</div>"
    )
