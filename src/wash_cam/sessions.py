"""Turn per-frame bay counts into debounced vehicle visit sessions."""

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wash_cam.config import BAYS, CAPTURE_INTERVAL, DATABASE_PATH

__all__ = [
    "Session",
    "build_sessions",
    "format_time",
    "rebuild_sessions",
    "run",
    "update_sessions",
]

# Consecutive occupied frames needed to open a session. Two means a single
# phantom frame, such as a car parked behind a bay, never becomes a visit.
OPEN_FRAMES = 2
# Consecutive empty frames needed to close a session. Two means a single
# missed frame, such as a foam-covered car at night, does not split a visit.
CLOSE_FRAMES = 2
# A pause longer than this many capture intervals is an outage, not a quiet
# bay: an open visit ends there instead of being stretched across it.
MAX_GAP_INTERVALS = 3

# One frame: its UTC capture time and the bay's vehicle count.
Frame = tuple[datetime, int]


@dataclass(frozen=True)
class Session:
    """One vehicle visit to one bay. Times are timezone-aware UTC."""

    bay_id: str
    # Estimated arrival and departure, halfway to the neighboring empty frames.
    started_at: datetime
    ended_at: datetime
    # First and last frames that saw the bay occupied.
    first_frame_at: datetime
    last_frame_at: datetime
    duration_s: float
    frames: int
    max_count: int
    # "left", "gap", or "open"; see db/migrations/004_sessions.sql.
    end_reason: str


def build_sessions(
    bay_id: str,
    frames: Sequence[Frame],
    interval: float,
    open_frames: int = OPEN_FRAMES,
    close_frames: int = CLOSE_FRAMES,
) -> list[Session]:
    """Return the sessions in one bay's time-ordered frames.

    A session opens after open_frames occupied frames in a row and closes after
    close_frames empty frames in a row: OPEN_FRAMES and CLOSE_FRAMES by default,
    while session_eval uses 1 and 1 for human labels, which need no debouncing.
    A pause longer than MAX_GAP_INTERVALS intervals ends an open session with
    reason "gap". A session still occupied at the last frame is returned with
    reason "open".
    """
    max_gap = timedelta(seconds=interval * MAX_GAP_INTERVALS)
    found: list[Session] = []
    # Occupied frames seen while no session is open, and the frame before them.
    pending: list[Frame] = []
    pending_before: datetime | None = None
    # The open session's occupied frames and the frame before its first one.
    visit: list[Frame] = []
    visit_before: datetime | None = None
    # Empty frames since the open session's last occupied frame.
    empty_since: datetime | None = None
    empty_frames = 0
    previous: datetime | None = None

    def close(reason: str, after: datetime | None) -> None:
        """Record the open session; after is the first empty frame, if known."""
        first, last = visit[0][0], visit[-1][0]
        started = first - (first - visit_before) / 2 if visit_before else first
        ended = last + (after - last) / 2 if after else last
        found.append(
            Session(
                bay_id=bay_id,
                started_at=started,
                ended_at=ended,
                first_frame_at=first,
                last_frame_at=last,
                duration_s=(ended - started).total_seconds(),
                frames=len(visit),
                max_count=max(count for _, count in visit),
                end_reason=reason,
            )
        )

    for moment, count in frames:
        if previous is not None and moment - previous > max_gap:
            if visit:
                close("gap", None)
                visit = []
            pending, empty_frames = [], 0
            # Nothing is known about the bay just before this frame.
            previous = None

        if visit:
            if count > 0:
                visit.append((moment, count))
                empty_frames = 0
            else:
                if empty_frames == 0:
                    empty_since = moment
                empty_frames += 1
                if empty_frames >= close_frames:
                    close("left", empty_since)
                    visit, empty_frames = [], 0
        elif count > 0:
            if not pending:
                pending_before = previous
            pending.append((moment, count))
            if len(pending) >= open_frames:
                visit, visit_before, pending = pending, pending_before, []
        else:
            pending = []
        previous = moment

    if visit:
        close("open", None)
    return found


def format_time(moment: datetime) -> str:
    """Format a time as UTC the way the database stores it, to milliseconds."""
    utc = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return utc.isoformat(sep=" ", timespec="milliseconds")


def bay_frames(db: sqlite3.Connection, bay_id: str, after: str | None) -> list[Frame]:
    """Return a bay's frames in capture order, only those after a time if given."""
    rows = db.execute(
        "SELECT d.captured_at, json_extract(bay.value, '$.vehicle_count') "
        "FROM image_detections AS d, json_each(d.metadata, '$.vehicles.bays') AS bay "
        "WHERE d.captured_at IS NOT NULL AND d.captured_at > coalesce(?, '') "
        "AND json_extract(bay.value, '$.bay_id') = ? "
        "ORDER BY d.captured_at",
        (after, bay_id),
    )
    # Stored times are UTC without an offset.
    return [
        (datetime.fromisoformat(moment).replace(tzinfo=timezone.utc), count)
        for moment, count in rows
    ]


def update_sessions(database: Path, interval: float = CAPTURE_INTERVAL) -> int:
    """Bring stored sessions up to date with new detections; return sessions written.

    Closed sessions are final, so each bay resumes after its last closed
    session's last occupied frame. By then the bay was empty or capture had
    stopped, so resuming there gives the same result as a full rebuild. Open
    sessions are recomputed because later frames can extend or close them.
    Detections added earlier than a closed session need rebuild_sessions.
    """
    written = 0
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True)) as db:
        for bay_id in BAYS:
            (resume_after,) = db.execute(
                "SELECT max(last_frame_at) FROM sessions "
                "WHERE bay_id = ? AND end_reason != 'open'",
                (bay_id,),
            ).fetchone()
            found = build_sessions(
                bay_id, bay_frames(db, bay_id, resume_after), interval
            )
            with db:
                db.execute(
                    "DELETE FROM sessions WHERE bay_id = ? AND end_reason = 'open'",
                    (bay_id,),
                )
                db.executemany(
                    "INSERT INTO sessions (bay_id, started_at, ended_at, "
                    "first_frame_at, last_frame_at, duration_s, frames, max_count, "
                    "end_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            session.bay_id,
                            format_time(session.started_at),
                            format_time(session.ended_at),
                            format_time(session.first_frame_at),
                            format_time(session.last_frame_at),
                            session.duration_s,
                            session.frames,
                            session.max_count,
                            session.end_reason,
                        )
                        for session in found
                    ],
                )
            written += len(found)
    return written


def rebuild_sessions(database: Path, interval: float = CAPTURE_INTERVAL) -> int:
    """Recompute every session from scratch; return how many were written."""
    with (
        closing(sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True)) as db,
        db,
    ):
        db.execute("DELETE FROM sessions")
    return update_sessions(database, interval)


def run(rebuild: bool = False) -> None:
    """Update or rebuild sessions in the server database and report the count."""
    if rebuild:
        written = rebuild_sessions(DATABASE_PATH)
        print(f"Rebuilt {written} sessions")
    else:
        written = update_sessions(DATABASE_PATH)
        print(f"Updated {written} sessions")
