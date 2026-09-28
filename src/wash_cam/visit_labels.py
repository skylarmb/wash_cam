"""Visit labels: when cars arrive, wash, and leave, as ground truth per bay.

A labeler reviews one wash bay's captures across a time window (a
`visit_windows` row) and marks events on frames (`visit_events`): arrive,
wash_start, wash_stop, leave. Humans label on the dashboard's Visit labeling
tab; agents label exported batches and import them with provenance. The site
is coin-op with no sales records, so these labels are the only check on paid
time. See .agents/docs/visit-labels.md.
"""

import json
import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from wash_cam.config import (
    BAYS,
    CAMERA_CROP_BOX,
    CHECKOUT_ROOT,
    DATABASE_PATH,
    IMAGES_DIR,
    bay_kind,
    bay_title,
)
from wash_cam.sessions import format_time

__all__ = [
    "EVENTS",
    "LABEL_BAYS",
    "Comparison",
    "Visit",
    "VisitLabelError",
    "clear_frame",
    "compare_windows",
    "create_windows",
    "crop_box",
    "delete_window",
    "derive_visits",
    "export_batch",
    "frame_states",
    "import_events",
    "list_windows",
    "set_completed",
    "set_event",
    "window_crop",
    "window_detail",
]

# Events in the order they apply within one frame, so a frame can hold a
# whole short visit (arrive, wash_start, wash_stop, leave) unambiguously.
EVENTS = ("arrive", "wash_start", "wash_stop", "leave")
# Only wash bays are labeled for now; the vacuum has no washing to find.
LABEL_BAYS = [bay_id for bay_id in BAYS if bay_kind(bay_id) == "wash"]
# The labeler sees one bay: its quad's width plus a little either side, the
# car and back wall above the quad, and the apron in front of it.
CROP_MARGIN_X = 12
CROP_ABOVE = 115
CROP_BELOW = 90
FRAME_WIDTH = CAMERA_CROP_BOX[2] - CAMERA_CROP_BOX[0]
FRAME_HEIGHT = CAMERA_CROP_BOX[3] - CAMERA_CROP_BOX[1]

BATCHES_DIR = CHECKOUT_ROOT / "tmp" / "label_batches"
AGENT_PREFIX = "agent:"
# The rubric every labeler follows, human or agent.
RUBRIC = (
    "Label one bay at a time. arrive: the first frame a car is in the bay. "
    "leave: the last frame that car is in the bay. If one car leaves and "
    "another arrives between two frames, mark leave on the last frame of the "
    "first car and arrive on the first frame of the next. wash_start: the "
    "first frame someone is actively cleaning the car (wand, foam brush, or "
    "their own bucket and mitt all count). wash_stop: the last frame of "
    "cleaning. The bay meter runs continuously once started, so mark only the "
    "first start and last stop of a visit unless cleaning clearly stops and "
    "restarts much later. A car already in the bay at the first frame gets "
    "arrive on the first frame."
)


class VisitLabelError(Exception):
    """Report a request that would corrupt labels or reference unknown data."""


@dataclass
class Visit:
    """One car's stay in a bay, as frame indices into its window."""

    arrive: int
    leave: int | None = None
    # (first, last) frame indices of each stretch of cleaning.
    washes: list[tuple[int, int | None]] = field(default_factory=list)


def crop_box(bay_id: str) -> tuple[int, int, int, int]:
    """The (left, top, right, bottom) pixels a labeler sees for a bay."""
    xs = [x for x, _ in BAYS[bay_id]]
    ys = [y for _, y in BAYS[bay_id]]
    return (
        max(min(xs) - CROP_MARGIN_X, 0),
        max(min(ys) - CROP_ABOVE, 0),
        min(max(xs) + CROP_MARGIN_X, FRAME_WIDTH),
        min(max(ys) + CROP_BELOW, FRAME_HEIGHT),
    )


def render_crop(image: np.ndarray, bay_id: str) -> np.ndarray:
    """A capture cropped to one bay with the bay's quad outlined.

    Humans (dashboard) and agents (exported batches) both see exactly this.
    """
    left, top, right, bottom = crop_box(bay_id)
    crop = image[top:bottom, left:right].copy()
    outline = np.array([[x - left, y - top] for x, y in BAYS[bay_id]], dtype=np.int32)
    cv2.polylines(crop, [outline], True, (0, 255, 255), 1)
    return crop


def frame_in_window(db: sqlite3.Connection, window: tuple, image_path: str) -> bool:
    """Whether a capture falls inside a window's time range."""
    return (
        db.execute(
            "SELECT 1 FROM image_detections WHERE image_path = ? "
            "AND captured_at BETWEEN ? AND ?",
            (image_path, window[2], window[3]),
        ).fetchone()
        is not None
    )


def window_crop(
    window_id: int,
    image_path: str,
    images: Path = IMAGES_DIR,
    database: Path = DATABASE_PATH,
) -> bytes:
    """JPEG bytes of one frame of a window, cropped to the window's bay."""
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        window = load_window(db, window_id)
        if not frame_in_window(db, window, image_path):
            raise VisitLabelError(f"{image_path} is not a frame of this window.")
    image = cv2.imread(str(images / image_path))
    if image is None:
        raise VisitLabelError(f"{image_path} is missing or unreadable.")
    ok, encoded = cv2.imencode(
        ".jpg", render_crop(image, window[1]), [cv2.IMWRITE_JPEG_QUALITY, 90]
    )
    if not ok:
        raise VisitLabelError(f"Could not encode {image_path}.")
    return encoded.tobytes()


def derive_visits(
    frames: Sequence[str], events: Mapping[str, set[str]]
) -> tuple[list[Visit], list[str]]:
    """Turn a window's events into visits, plus any inconsistencies found.

    Inconsistent events are still applied as sensibly as possible, e.g. a
    leave during washing ends the wash there too, so a half-finished label
    still yields visits; the problems list says what to fix.
    """
    visits: list[Visit] = []
    problems: list[str] = []
    current: Visit | None = None
    for index, image_path in enumerate(frames):
        marked = events.get(image_path, set())
        for event in EVENTS:
            if event not in marked:
                continue
            washing = (
                current is not None and current.washes and current.washes[-1][1] is None
            )
            if event == "arrive":
                if current is not None:
                    problems.append(
                        f"{image_path}: arrive while a car is already in the bay"
                    )
                    current.leave = index - 1 if index > current.arrive else index
                current = Visit(arrive=index)
                visits.append(current)
            elif event == "wash_start":
                if current is None:
                    problems.append(f"{image_path}: wash_start with no car in the bay")
                elif washing:
                    problems.append(f"{image_path}: wash_start while already washing")
                else:
                    current.washes.append((index, None))
            elif event == "wash_stop":
                if not washing:
                    problems.append(f"{image_path}: wash_stop while not washing")
                else:
                    current.washes[-1] = (current.washes[-1][0], index)
            elif event == "leave":
                if current is None:
                    problems.append(f"{image_path}: leave with no car in the bay")
                    continue
                if washing:
                    problems.append(f"{image_path}: leave while still washing")
                    current.washes[-1] = (current.washes[-1][0], index)
                current.leave = index
                current = None
    return visits, problems


def frame_states(frames: Sequence[str], events: Mapping[str, set[str]]) -> list[str]:
    """Each frame's labeled state: "empty", "car", or "washing"."""
    states = ["empty"] * len(frames)
    visits, _ = derive_visits(frames, events)
    last = len(frames) - 1
    for visit in visits:
        for index in range(
            visit.arrive, (last if visit.leave is None else visit.leave) + 1
        ):
            states[index] = "car"
        for start, stop in visit.washes:
            for index in range(start, (last if stop is None else stop) + 1):
                states[index] = "washing"
    return states


def connect(database: Path) -> sqlite3.Connection:
    """Open the database read-write without creating a missing file."""
    return sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True)


def window_frames(
    db: sqlite3.Connection, start_at: str, end_at: str
) -> list[tuple[str, str]]:
    """(image_path, captured_at) of every capture in a window, in time order."""
    return db.execute(
        "SELECT image_path, captured_at FROM image_detections "
        "WHERE captured_at BETWEEN ? AND ? ORDER BY captured_at",
        (start_at, end_at),
    ).fetchall()


def window_events(db: sqlite3.Connection, window_id: int) -> dict[str, set[str]]:
    """A window's events keyed by frame."""
    events: dict[str, set[str]] = defaultdict(set)
    for image_path, event in db.execute(
        "SELECT image_path, event FROM visit_events WHERE window_id = ?", (window_id,)
    ):
        events[image_path].add(event)
    return dict(events)


def utc_text(moment: datetime) -> str:
    """Format a local or aware time the way captured_at is stored."""
    aware = moment if moment.tzinfo else moment.astimezone()
    return format_time(aware)


def local_text(stored: str) -> str:
    """A stored UTC time as local 'YYYY-MM-DD HH:MM:SS'."""
    moment = datetime.fromisoformat(stored).replace(tzinfo=timezone.utc).astimezone()
    return f"{moment:%Y-%m-%d %H:%M:%S}"


def create_windows(
    start: datetime, end: datetime, database: Path = DATABASE_PATH
) -> list[int]:
    """Create one human window per wash bay for [start, end] and return their IDs.

    Naive times are local. An identical existing range is reused, so opening
    the same range twice continues the earlier work; an overlapping one is
    refused, because two human truths for the same frames would conflict.
    """
    start_at, end_at = utc_text(start), utc_text(end)
    if end_at <= start_at:
        raise VisitLabelError("The range must end after it starts.")
    with closing(connect(database)) as db, db:
        if not window_frames(db, start_at, end_at):
            raise VisitLabelError("No captures in that range.")
        ids = []
        for bay_id in LABEL_BAYS:
            same = db.execute(
                "SELECT id FROM visit_windows WHERE labeled_by IS NULL AND bay_id = ? "
                "AND start_at = ? AND end_at = ?",
                (bay_id, start_at, end_at),
            ).fetchone()
            if same:
                ids.append(same[0])
                continue
            overlap = db.execute(
                "SELECT id FROM visit_windows WHERE labeled_by IS NULL AND bay_id = ? "
                "AND start_at <= ? AND end_at >= ?",
                (bay_id, end_at, start_at),
            ).fetchone()
            if overlap:
                raise VisitLabelError(
                    f"{bay_title(bay_id)} already has a labeling window overlapping "
                    "that range. Pick a range that doesn't overlap it."
                )
            cursor = db.execute(
                "INSERT INTO visit_windows (bay_id, start_at, end_at) VALUES (?, ?, ?)",
                (bay_id, start_at, end_at),
            )
            ids.append(cursor.lastrowid)
    return ids


def window_summary(db: sqlite3.Connection, row: tuple) -> dict[str, object]:
    """One window's list entry."""
    window_id, bay_id, start_at, end_at, labeled_by, completed_at = row
    (events,) = db.execute(
        "SELECT count(*) FROM visit_events WHERE window_id = ?", (window_id,)
    ).fetchone()
    return {
        "id": window_id,
        "bay_id": bay_id,
        "bay": bay_title(bay_id),
        "start": local_text(start_at),
        "end": local_text(end_at),
        "labeled_by": labeled_by,
        "completed": completed_at is not None,
        "events": events,
    }


WINDOW_COLUMNS = "id, bay_id, start_at, end_at, labeled_by, completed_at"


def list_windows(database: Path = DATABASE_PATH) -> list[dict[str, object]]:
    """Every window, newest range first, bays in configured order."""
    order = {bay_id: index for index, bay_id in enumerate(BAYS)}
    with closing(connect(database)) as db:
        rows = db.execute(f"SELECT {WINDOW_COLUMNS} FROM visit_windows").fetchall()
        rows.sort(key=lambda row: (row[2], -order.get(row[1], 99)), reverse=True)
        return [window_summary(db, row) for row in rows]


def next_window(db: sqlite3.Connection, current: tuple) -> int | None:
    """The human window to label after this one: its range's next bay first."""
    order = {bay_id: index for index, bay_id in enumerate(BAYS)}
    rows = db.execute(
        f"SELECT {WINDOW_COLUMNS} FROM visit_windows "
        "WHERE labeled_by IS NULL AND completed_at IS NULL AND id != ?",
        (current[0],),
    ).fetchall()
    if not rows:
        return None
    same_range = [row for row in rows if (row[2], row[3]) == (current[2], current[3])]
    candidates = same_range or rows
    candidates.sort(key=lambda row: (row[2], order.get(row[1], 99)))
    # Prefer bays after this one in the same range, then wrap around.
    later = [
        row for row in candidates if order.get(row[1], 99) > order.get(current[1], 99)
    ]
    return (later or candidates)[0][0]


def load_window(db: sqlite3.Connection, window_id: int) -> tuple:
    """A window row, or VisitLabelError when it doesn't exist."""
    row = db.execute(
        f"SELECT {WINDOW_COLUMNS} FROM visit_windows WHERE id = ?", (window_id,)
    ).fetchone()
    if row is None:
        raise VisitLabelError(f"No labeling window {window_id}.")
    return row


def window_detail(window_id: int, database: Path = DATABASE_PATH) -> dict[str, object]:
    """Everything the labeling tab needs to label one window."""
    with closing(connect(database)) as db:
        row = load_window(db, window_id)
        frames = window_frames(db, row[2], row[3])
        events = window_events(db, window_id)
        summary = window_summary(db, row)
        following = next_window(db, row)
        position = [
            other[0]
            for other in db.execute(
                "SELECT id FROM visit_windows WHERE labeled_by IS NULL "
                "AND start_at = ? AND end_at = ? ORDER BY id",
                (row[2], row[3]),
            )
        ]
    left, top, _, _ = crop = crop_box(row[1])
    return {
        "window": summary,
        "task": {
            "index": position.index(window_id) + 1 if window_id in position else 1,
            "count": len(position) or 1,
        },
        "next_id": following,
        "crop": list(crop),
        # The quad relative to the crop, so the page can outline the bay.
        "quad": [[x - left, y - top] for x, y in BAYS[row[1]]],
        "frames": [
            {"image_path": image_path, "local": local_text(captured_at)[11:]}
            for image_path, captured_at in frames
        ],
        "events": {image_path: sorted(marked) for image_path, marked in events.items()},
        "rubric": RUBRIC,
    }


def editable_window(db: sqlite3.Connection, window_id: int) -> tuple:
    """A human window the dashboard may change; agent windows are read-only."""
    row = load_window(db, window_id)
    if row[4] is not None:
        raise VisitLabelError("Agent labels are read-only; re-import to change them.")
    return row


def set_event(
    window_id: int,
    image_path: str,
    event: str,
    present: bool,
    database: Path = DATABASE_PATH,
) -> None:
    """Add or remove one event on one frame of a human window."""
    if event not in EVENTS:
        raise VisitLabelError(f"Unknown event {event!r}.")
    with closing(connect(database)) as db, db:
        row = editable_window(db, window_id)
        if not frame_in_window(db, row, image_path):
            raise VisitLabelError(f"{image_path} is not a frame of this window.")
        if present:
            db.execute(
                "INSERT OR IGNORE INTO visit_events (window_id, image_path, event) "
                "VALUES (?, ?, ?)",
                (window_id, image_path, event),
            )
        else:
            db.execute(
                "DELETE FROM visit_events WHERE window_id = ? AND image_path = ? "
                "AND event = ?",
                (window_id, image_path, event),
            )


def clear_frame(
    window_id: int, image_path: str, database: Path = DATABASE_PATH
) -> None:
    """Remove every event on one frame of a human window."""
    with closing(connect(database)) as db, db:
        editable_window(db, window_id)
        db.execute(
            "DELETE FROM visit_events WHERE window_id = ? AND image_path = ?",
            (window_id, image_path),
        )


def set_completed(
    window_id: int, completed: bool, database: Path = DATABASE_PATH
) -> None:
    """Mark a human window finished, or reopen it."""
    with closing(connect(database)) as db, db:
        editable_window(db, window_id)
        db.execute(
            "UPDATE visit_windows SET completed_at = "
            "CASE WHEN ? THEN CURRENT_TIMESTAMP END WHERE id = ?",
            (completed, window_id),
        )


def delete_window(window_id: int, database: Path = DATABASE_PATH) -> None:
    """Delete a human window and its events, e.g. a range opened by mistake."""
    with closing(connect(database)) as db, db:
        editable_window(db, window_id)
        db.execute("DELETE FROM visit_events WHERE window_id = ?", (window_id,))
        db.execute("DELETE FROM visit_windows WHERE id = ?", (window_id,))


# ---------- agent batches ----------


def export_batch(
    start: datetime,
    end: datetime,
    images: Path = IMAGES_DIR,
    batches: Path = BATCHES_DIR,
    database: Path = DATABASE_PATH,
) -> Path:
    """Write a visit-labeling batch for local times [start, end] and return it.

    The batch holds each wash bay's crop of every frame (the same view the
    dashboard shows a human, with the bay outlined), a manifest, and an empty
    events.jsonl. Detections are never included, so agent labels stay
    independent of the detector. An existing batch is never replaced.
    """
    batch = batches / f"visits_{start:%Y%m%d-%H%M}_{end:%Y%m%d-%H%M}"
    if batch.exists():
        raise VisitLabelError(f"Batch {batch} already exists; not replacing it.")
    start_at, end_at = utc_text(start), utc_text(end)
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        frames = window_frames(db, start_at, end_at)
    if not frames:
        raise VisitLabelError("No captures in that range.")
    for bay_id in LABEL_BAYS:
        (batch / "crops" / bay_id).mkdir(parents=True)
    exported = []
    for image_path, captured_at in frames:
        image = cv2.imread(str(images / image_path))
        if image is None:
            continue
        for bay_id in LABEL_BAYS:
            cv2.imwrite(
                str(batch / "crops" / bay_id / image_path), render_crop(image, bay_id)
            )
        exported.append(
            {"image_path": image_path, "local_time": local_text(captured_at)}
        )
    manifest = {
        "batch_id": batch.name,
        "kind": "visits",
        "start_at": start_at,
        "end_at": end_at,
        "timezone": str(datetime.now().astimezone().tzinfo),
        "rubric": RUBRIC,
        "events": list(EVENTS),
        "bays": {
            bay_id: {"label": bay_title(bay_id), "crops": f"crops/{bay_id}/"}
            for bay_id in LABEL_BAYS
        },
        "frames": exported,
    }
    (batch / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (batch / "events.jsonl").write_text("")
    return batch


@dataclass(frozen=True)
class ImportResult:
    """What an events import wrote."""

    windows: int
    events: int


def parse_line(
    line: str, manifest: Mapping[str, object], frame_names: set[str]
) -> dict[str, object]:
    """Validate one events.jsonl line and return it, or raise VisitLabelError."""
    try:
        entry = json.loads(line)
    except json.JSONDecodeError as error:
        raise VisitLabelError(f"not valid JSON ({error.msg})") from error
    if not isinstance(entry, dict):
        raise VisitLabelError("not a JSON object")
    labeled_by = entry.get("labeled_by")
    if not isinstance(labeled_by, str) or not labeled_by.startswith(AGENT_PREFIX):
        raise VisitLabelError(f"labeled_by must start with {AGENT_PREFIX!r}")
    if entry.get("bay_id") not in manifest["bays"]:
        raise VisitLabelError(f"unknown bay {entry.get('bay_id')!r} for this batch")
    if entry.get("done") is True:
        return entry
    if entry.get("image_path") not in frame_names:
        raise VisitLabelError(
            f"{entry.get('image_path')!r} is not a frame of this batch"
        )
    if entry.get("event") not in EVENTS:
        raise VisitLabelError(f"event must be one of {', '.join(EVENTS)}")
    return entry


def import_events(path: Path, database: Path = DATABASE_PATH) -> ImportResult:
    """Store an agent's events.jsonl, or nothing if any line is invalid.

    Each bay with events or a `done` line becomes an agent window over the
    batch's range; `done` marks it complete. The same agent's earlier window
    for that bay and range is replaced. Human windows are never touched.
    """
    manifest = json.loads((path.parent / "manifest.json").read_text())
    if manifest.get("kind") != "visits":
        raise VisitLabelError("This batch's manifest is not a visits batch.")
    frame_names = {frame["image_path"] for frame in manifest["frames"]}
    entries, problems = [], []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entries.append(parse_line(line, manifest, frame_names))
        except VisitLabelError as error:
            problems.append(f"line {number}: {error}")
    labelers = {entry["labeled_by"] for entry in entries}
    if len(labelers) > 1:
        problems.append(f"one labeler per file, found {', '.join(sorted(labelers))}")
    if problems:
        raise VisitLabelError(
            "Rejected events file, nothing imported:\n" + "\n".join(problems)
        )
    if not entries:
        return ImportResult(windows=0, events=0)

    labeled_by = entries[0]["labeled_by"]
    by_bay: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in entries:
        by_bay[str(entry["bay_id"])].append(entry)
    windows = events = 0
    with closing(connect(database)) as db, db:
        for bay_id, bay_entries in by_bay.items():
            for (old,) in db.execute(
                "SELECT id FROM visit_windows WHERE labeled_by = ? AND bay_id = ? "
                "AND start_at = ? AND end_at = ?",
                (labeled_by, bay_id, manifest["start_at"], manifest["end_at"]),
            ).fetchall():
                db.execute("DELETE FROM visit_events WHERE window_id = ?", (old,))
                db.execute("DELETE FROM visit_windows WHERE id = ?", (old,))
            done = any(entry.get("done") is True for entry in bay_entries)
            cursor = db.execute(
                "INSERT INTO visit_windows (bay_id, start_at, end_at, labeled_by, "
                "completed_at) VALUES (?, ?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP END)",
                (bay_id, manifest["start_at"], manifest["end_at"], labeled_by, done),
            )
            windows += 1
            for entry in bay_entries:
                if entry.get("done") is True:
                    continue
                inserted = db.execute(
                    "INSERT OR IGNORE INTO visit_events (window_id, image_path, event) "
                    "VALUES (?, ?, ?)",
                    (cursor.lastrowid, entry["image_path"], entry["event"]),
                )
                events += inserted.rowcount
    return ImportResult(windows=windows, events=events)


# ---------- human vs agent ----------


@dataclass(frozen=True)
class Comparison:
    """How closely an agent's window agrees with the human's, frame by frame."""

    bay_id: str
    labeled_by: str
    frames: int
    # Share of shared frames where both agree on car present, and on washing.
    occupied_agreement: float
    washing_agreement: float
    human_visits: int
    agent_visits: int
    # Frames each labeler called washing, the quantity revenue depends on.
    human_washing_frames: int
    agent_washing_frames: int
    # Inconsistent events (e.g. wash_start with no car), a sign of sloppy labels.
    human_problems: int
    agent_problems: int


def compare_windows(database: Path = DATABASE_PATH) -> list[Comparison]:
    """Compare every agent window with the completed human window it overlaps."""
    results = []
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as db:
        humans = db.execute(
            f"SELECT {WINDOW_COLUMNS} FROM visit_windows "
            "WHERE labeled_by IS NULL AND completed_at IS NOT NULL"
        ).fetchall()
        for human in humans:
            agents = db.execute(
                f"SELECT {WINDOW_COLUMNS} FROM visit_windows WHERE labeled_by IS NOT NULL "
                "AND bay_id = ? AND start_at <= ? AND end_at >= ?",
                (human[1], human[3], human[2]),
            ).fetchall()
            human_frames = [name for name, _ in window_frames(db, human[2], human[3])]
            human_states = dict(
                zip(
                    human_frames,
                    frame_states(human_frames, window_events(db, human[0])),
                    strict=True,
                )
            )
            for agent in agents:
                agent_frames = [
                    name for name, _ in window_frames(db, agent[2], agent[3])
                ]
                agent_events = window_events(db, agent[0])
                agent_states = dict(
                    zip(
                        agent_frames,
                        frame_states(agent_frames, agent_events),
                        strict=True,
                    )
                )
                shared = [name for name in human_frames if name in agent_states]
                if not shared:
                    continue
                pairs = [(human_states[name], agent_states[name]) for name in shared]
                human_visits, human_problems = derive_visits(
                    shared, window_events(db, human[0])
                )
                agent_visits, agent_problems = derive_visits(shared, agent_events)
                results.append(
                    Comparison(
                        bay_id=human[1],
                        labeled_by=agent[4],
                        frames=len(shared),
                        occupied_agreement=sum(
                            (h != "empty") == (a != "empty") for h, a in pairs
                        )
                        / len(pairs),
                        washing_agreement=sum(
                            (h == "washing") == (a == "washing") for h, a in pairs
                        )
                        / len(pairs),
                        human_visits=len(human_visits),
                        agent_visits=len(agent_visits),
                        human_washing_frames=sum(h == "washing" for h, _ in pairs),
                        agent_washing_frames=sum(a == "washing" for _, a in pairs),
                        human_problems=len(human_problems),
                        agent_problems=len(agent_problems),
                    )
                )
    return results


# ---------- CLI ----------


def run_export(start: str, end: str) -> None:
    """Export a visits batch for local times like '2026-09-27 12:00'."""
    try:
        batch = export_batch(datetime.fromisoformat(start), datetime.fromisoformat(end))
    except VisitLabelError as error:
        raise SystemExit(str(error)) from error
    frames = len(json.loads((batch / "manifest.json").read_text())["frames"])
    print(f"Exported {frames} frames for {len(LABEL_BAYS)} bays to {batch}")


def run_import(path: Path) -> None:
    """Import an agent's events.jsonl and print what was written."""
    try:
        result = import_events(path)
    except VisitLabelError as error:
        raise SystemExit(str(error)) from error
    print(f"Imported {result.events} events in {result.windows} windows")


def run_compare() -> None:
    """Print how each agent window agrees with the human labels it overlaps."""
    results = compare_windows()
    if not results:
        print("No agent window overlaps a completed human window yet.")
        return
    for result in results:
        print(
            f"{bay_title(result.bay_id)} vs {result.labeled_by}: {result.frames} frames, "
            f"occupied {result.occupied_agreement:.0%}, washing {result.washing_agreement:.0%} "
            f"agree; visits {result.human_visits} human / {result.agent_visits} agent; "
            f"washing frames {result.human_washing_frames} human / "
            f"{result.agent_washing_frames} agent; problems {result.human_problems} "
            f"human / {result.agent_problems} agent"
        )
