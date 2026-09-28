"""Check visit labels: windows, events, derived visits, and agent batches."""

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
import pytest

from wash_cam import db, visit_labels
from wash_cam.sessions import format_time
from wash_cam.visit_labels import VisitLabelError

# Saturday 2026-09-26 20:00 Pacific daylight time, as UTC.
EVENING = datetime(2026, 9, 27, 3, 0, tzinfo=timezone.utc)
# The same instant as a naive local time, the way the dashboard sends it.
LOCAL_EVENING = datetime(2026, 9, 26, 20, 0)  # noqa: DTZ001
FRAMES = 12
AGENT = "agent:TestLabeler/vision-1"


@pytest.fixture(autouse=True)
def pacific(monkeypatch):
    """Use the car wash's local time, then restore the process timezone."""
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def setup(tmp_path):
    """A migrated database with FRAMES captures 15 s apart, and their images."""
    database = tmp_path / "labels.sqlite3"
    images = tmp_path / "images"
    images.mkdir()
    db.migrate(database)
    names = []
    with closing(sqlite3.connect(database)) as connection, connection:
        for index in range(FRAMES):
            name = f"frame{index:02d}.jpg"
            names.append(name)
            moment = EVENING + timedelta(seconds=15 * index)
            connection.execute(
                "INSERT INTO image_detections (image_path, metadata, model, captured_at) "
                "VALUES (?, '{}', 'test', ?)",
                (name, format_time(moment)),
            )
            frame = np.full((340, 1270, 3), index * 10, dtype=np.uint8)
            cv2.imwrite(str(images / name), frame)
    return database, images, names


def open_range(database, minutes=5):
    """Open human windows over the first `minutes` of captures."""
    return visit_labels.create_windows(
        LOCAL_EVENING, LOCAL_EVENING + timedelta(minutes=minutes), database
    )


# ---------- derived visits ----------


def events(**marks):
    """Events keyed by frame name, e.g. f00="arrive wash_start"."""
    return {f"{name}.jpg": set(value.split()) for name, value in marks.items()}


FRAME_NAMES = [f"f{index:02d}.jpg" for index in range(8)]


def test_a_visit_with_one_wash_derives_its_frames():
    """Arrive, wash, and leave frames are all inside the visit, inclusive."""
    marks = events(f01="arrive", f02="wash_start", f04="wash_stop", f05="leave")

    visits, problems = visit_labels.derive_visits(FRAME_NAMES, marks)

    assert problems == []
    assert len(visits) == 1
    assert (visits[0].arrive, visits[0].leave, visits[0].washes) == (1, 5, [(2, 4)])
    assert visit_labels.frame_states(FRAME_NAMES, marks) == [
        "empty",
        "car",
        "washing",
        "washing",
        "washing",
        "car",
        "empty",
        "empty",
    ]


def test_a_swap_is_leave_then_arrive_on_the_next_frame():
    """Two cars back to back are two visits, not one long one."""
    marks = events(f00="arrive", f03="leave", f04="arrive", f06="leave")

    visits, problems = visit_labels.derive_visits(FRAME_NAMES, marks)

    assert problems == []
    assert [(visit.arrive, visit.leave) for visit in visits] == [(0, 3), (4, 6)]


def test_one_frame_can_hold_a_whole_short_visit():
    """Events on one frame apply in arrive, wash_start, wash_stop, leave order."""
    marks = events(f02="leave wash_stop wash_start arrive")

    visits, problems = visit_labels.derive_visits(FRAME_NAMES, marks)

    assert problems == []
    assert (visits[0].arrive, visits[0].leave, visits[0].washes) == (2, 2, [(2, 2)])


def test_inconsistent_events_are_reported_but_still_applied():
    """A leave mid-wash ends the wash there; a stray wash_start is flagged."""
    marks = events(f00="wash_start", f01="arrive", f02="wash_start", f04="leave")

    visits, problems = visit_labels.derive_visits(FRAME_NAMES, marks)

    assert visits[0].washes == [(2, 4)]
    assert problems == [
        "f00.jpg: wash_start with no car in the bay",
        "f04.jpg: leave while still washing",
    ]


def test_a_visit_open_at_the_window_end_runs_to_the_last_frame():
    """A car still there at the end stays car through the last frame."""
    states = visit_labels.frame_states(FRAME_NAMES, events(f06="arrive"))

    assert states[-2:] == ["car", "car"]


# ---------- human windows ----------


def test_opening_a_range_makes_one_task_per_wash_bay(setup):
    """Each wash bay gets a window; reopening the same range reuses them."""
    database, _, _ = setup

    first = open_range(database)
    again = open_range(database)

    assert len(first) == len(visit_labels.LABEL_BAYS) == 5
    assert again == first
    windows = visit_labels.list_windows(database)
    assert [window["bay_id"] for window in windows] == visit_labels.LABEL_BAYS
    assert windows[0]["start"] == "2026-09-26 20:00:00"


def test_overlapping_and_empty_ranges_are_refused(setup):
    """Two human truths for the same frames would conflict."""
    database, _, _ = setup
    open_range(database, minutes=2)

    with pytest.raises(VisitLabelError, match="overlapping"):
        open_range(database, minutes=3)
    with pytest.raises(VisitLabelError, match="No captures"):
        visit_labels.create_windows(
            LOCAL_EVENING + timedelta(hours=5),
            LOCAL_EVENING + timedelta(hours=6),
            database,
        )
    with pytest.raises(VisitLabelError, match="end after"):
        visit_labels.create_windows(LOCAL_EVENING, LOCAL_EVENING, database)


def test_events_toggle_and_clear_per_frame(setup):
    """Marking twice is idempotent; removing and clearing take effect."""
    database, _, names = setup
    window = open_range(database)[0]

    visit_labels.set_event(window, names[1], "arrive", True, database)
    visit_labels.set_event(window, names[1], "arrive", True, database)
    visit_labels.set_event(window, names[1], "wash_start", True, database)
    visit_labels.set_event(window, names[1], "wash_start", False, database)
    visit_labels.set_event(window, names[3], "leave", True, database)
    visit_labels.clear_frame(window, names[3], database)

    detail = visit_labels.window_detail(window, database)
    assert detail["events"] == {names[1]: ["arrive"]}


def test_events_must_name_a_frame_of_the_window_and_a_known_event(setup):
    """A frame outside the window's range or an unknown event is refused."""
    database, _, names = setup
    window = open_range(database, minutes=1)[0]  # Frames 0-4 only.

    with pytest.raises(VisitLabelError, match="not a frame"):
        visit_labels.set_event(window, names[10], "arrive", True, database)
    with pytest.raises(VisitLabelError, match="Unknown event"):
        visit_labels.set_event(window, names[0], "park", True, database)


def test_window_detail_crops_to_the_bay_and_advances_to_the_next_bay(setup):
    """The page gets the bay's crop and the next bay's task in the same range."""
    database, _, names = setup
    ids = open_range(database, minutes=1)

    detail = visit_labels.window_detail(ids[0], database)

    assert [frame["image_path"] for frame in detail["frames"]] == names[:5]
    assert detail["frames"][0]["local"] == "20:00:00"
    assert detail["crop"] == list(visit_labels.crop_box("bay_0"))
    assert detail["task"] == {"index": 1, "count": 5}
    assert detail["next_id"] == ids[1]
    # Finishing the last bay wraps to the first unfinished one.
    for window in ids[1:4]:
        visit_labels.set_completed(window, True, database)
    assert visit_labels.window_detail(ids[4], database)["next_id"] == ids[0]


def test_window_crop_is_the_bays_region_as_jpeg(setup):
    """Crops match crop_box, so humans see exactly the region agents get."""
    database, images, names = setup
    window = open_range(database)[2]

    data = visit_labels.window_crop(window, names[0], images, database)

    crop = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    left, top, right, bottom = visit_labels.crop_box("bay_2")
    assert crop.shape[:2] == (bottom - top, right - left)


# ---------- agent batches ----------


def export(setup, tmp_path):
    """Export the first minute of captures as a visits batch."""
    database, images, _ = setup
    return visit_labels.export_batch(
        LOCAL_EVENING,
        LOCAL_EVENING + timedelta(minutes=1),
        images=images,
        batches=tmp_path / "batches",
        database=database,
    )


def write_events(batch, lines):
    """Write events.jsonl lines as JSON."""
    path = batch / "events.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


def test_export_writes_bay_crops_and_a_manifest(setup, tmp_path):
    """Each wash bay gets its crop of every frame; nothing from the detector."""
    batch = export(setup, tmp_path)

    manifest = json.loads((batch / "manifest.json").read_text())
    assert manifest["kind"] == "visits"
    assert len(manifest["frames"]) == 5
    assert set(manifest["bays"]) == set(visit_labels.LABEL_BAYS)
    assert len(list((batch / "crops" / "bay_3").glob("*.jpg"))) == 5
    assert (batch / "events.jsonl").read_text() == ""
    with pytest.raises(VisitLabelError, match="already exists"):
        export(setup, tmp_path)


def test_import_creates_agent_windows_and_replaces_its_own(setup, tmp_path):
    """Importing again replaces that agent's window; done marks it complete."""
    database, _, names = setup
    batch = export(setup, tmp_path)
    lines = [
        {
            "bay_id": "bay_1",
            "image_path": names[0],
            "event": "arrive",
            "labeled_by": AGENT,
        },
        {
            "bay_id": "bay_1",
            "image_path": names[3],
            "event": "leave",
            "labeled_by": AGENT,
        },
        {"bay_id": "bay_2", "done": True, "labeled_by": AGENT},
    ]

    first = visit_labels.import_events(write_events(batch, lines), database)
    second = visit_labels.import_events(write_events(batch, lines[:1]), database)

    assert (first.windows, first.events) == (2, 2)
    assert (second.windows, second.events) == (1, 1)
    windows = {
        window["bay_id"]: window
        for window in visit_labels.list_windows(database)
        if window["labeled_by"] == AGENT
    }
    assert windows["bay_1"]["events"] == 1
    # bay_2 came from the first import, so it survives; it was marked done.
    assert windows["bay_2"]["completed"] is True


def test_invalid_lines_reject_the_whole_file(setup, tmp_path):
    """Any bad line imports nothing and names every problem."""
    database, _, names = setup
    batch = export(setup, tmp_path)
    lines = [
        {
            "bay_id": "bay_1",
            "image_path": names[0],
            "event": "arrive",
            "labeled_by": AGENT,
        },
        {
            "bay_id": "bay_1",
            "image_path": names[9],
            "event": "leave",
            "labeled_by": AGENT,
        },
        {
            "bay_id": "vac_0",
            "image_path": names[0],
            "event": "arrive",
            "labeled_by": AGENT,
        },
        {
            "bay_id": "bay_1",
            "image_path": names[1],
            "event": "park",
            "labeled_by": AGENT,
        },
        {
            "bay_id": "bay_1",
            "image_path": names[1],
            "event": "leave",
            "labeled_by": "me",
        },
    ]

    with pytest.raises(VisitLabelError) as problem:
        visit_labels.import_events(write_events(batch, lines), database)

    message = str(problem.value)
    for number in (2, 3, 4, 5):
        assert f"line {number}:" in message
    assert visit_labels.list_windows(database) == []


def test_agent_windows_are_read_only_on_the_dashboard(setup, tmp_path):
    """Only a re-import changes agent labels."""
    database, _, names = setup
    batch = export(setup, tmp_path)
    visit_labels.import_events(
        write_events(batch, [{"bay_id": "bay_1", "done": True, "labeled_by": AGENT}]),
        database,
    )
    agent_window = visit_labels.list_windows(database)[0]["id"]

    with pytest.raises(VisitLabelError, match="read-only"):
        visit_labels.set_event(agent_window, names[0], "arrive", True, database)


def test_compare_scores_agent_frames_against_the_human(setup, tmp_path):
    """Frame agreement and washing totals over the frames both labeled."""
    database, _, names = setup
    human = open_range(database, minutes=1)[1]  # bay_1, frames 0-4.
    for frame, event in [
        (0, "arrive"),
        (1, "wash_start"),
        (3, "wash_stop"),
        (4, "leave"),
    ]:
        visit_labels.set_event(human, names[frame], event, True, database)
    visit_labels.set_completed(human, True, database)
    batch = export(setup, tmp_path)
    # The agent sees the car arrive but starts washing one frame late.
    agent = [(0, "arrive"), (2, "wash_start"), (3, "wash_stop")]
    lines = [
        {
            "bay_id": "bay_1",
            "image_path": names[frame],
            "event": event,
            "labeled_by": AGENT,
        }
        for frame, event in agent
    ]
    visit_labels.import_events(write_events(batch, lines), database)

    (result,) = visit_labels.compare_windows(database)

    assert result.frames == 5
    assert result.occupied_agreement == 1.0
    # Frame 1: human washing, agent not; the rest agree.
    assert result.washing_agreement == pytest.approx(4 / 5)
    assert (result.human_washing_frames, result.agent_washing_frames) == (3, 2)
    # The agent never marks the car leaving, which is allowed, not a problem.
    assert (result.human_problems, result.agent_problems) == (0, 0)
