"""Check that the heartbeat records capture outages in NOTIFICATIONS.md."""

import os

from wash_cam import heartbeat

# An arbitrary fixed clock, in epoch seconds.
NOW = 1_800_000_000.0


def capture(images, name, age):
    """Write a capture whose mtime is `age` seconds before NOW."""
    path = images / name
    path.write_bytes(b"jpg")
    os.utime(path, (NOW - age, NOW - age))
    return path


def entries(notifications):
    """Return the entry headings written so far."""
    if not notifications.exists():
        return []
    return [
        line
        for line in notifications.read_text().splitlines()
        if line.startswith("## ")
    ]


def test_fresh_captures_write_nothing(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    capture(images, "20270115_000000_000000.jpg", age=30)
    notifications = tmp_path / "NOTIFICATIONS.md"

    down_since = heartbeat.beat(images, notifications, NOW, down_since=None)

    assert down_since is None
    assert entries(notifications) == []


def test_newest_capture_is_chosen_by_mtime_not_name(tmp_path):
    # After the fall DST change, the repeated 01:00 hour sorts before 01:59.
    images = tmp_path / "images"
    images.mkdir()
    capture(images, "20271107_015900_000000.jpg", age=3600)
    fresh = capture(images, "20271107_010500_000000.jpg", age=30)
    notifications = tmp_path / "NOTIFICATIONS.md"

    assert heartbeat.newest_capture(images) == fresh
    assert heartbeat.beat(images, notifications, NOW, down_since=None) is None
    assert entries(notifications) == []


def test_a_capture_exactly_at_the_limit_is_not_stale(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    capture(images, "20270115_000000_000000.jpg", age=heartbeat.STALE_AFTER)
    notifications = tmp_path / "NOTIFICATIONS.md"

    assert heartbeat.beat(images, notifications, NOW, down_since=None) is None
    assert heartbeat.beat(images, notifications, NOW + 1, down_since=None) is not None


def test_one_entry_when_captures_stop_and_none_while_stopped(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    capture(images, "20270114_000000_000000.jpg", age=900)
    newest = capture(images, "20270115_000000_000000.jpg", age=600)
    notifications = tmp_path / "NOTIFICATIONS.md"

    down_since = heartbeat.beat(images, notifications, NOW, down_since=None)
    down_since = heartbeat.beat(images, notifications, NOW + 60, down_since)

    assert down_since == NOW - 600
    assert len(entries(notifications)) == 1
    assert "captures stopped" in entries(notifications)[0]
    text = notifications.read_text()
    assert newest.name in text
    assert "10 min" in text
    assert str(images) in text


def test_resumed_entry_reports_the_outage_length(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    capture(images, "20270115_000000_000000.jpg", age=10)
    notifications = tmp_path / "NOTIFICATIONS.md"

    # The outage started with a capture 40 minutes before the newest one.
    down_since = heartbeat.beat(images, notifications, NOW, down_since=NOW - 10 - 2400)

    assert down_since is None
    assert len(entries(notifications)) == 1
    assert "captures resumed" in entries(notifications)[0]
    assert "40 min" in notifications.read_text()


def test_missing_or_empty_directory_counts_as_stopped(tmp_path):
    notifications = tmp_path / "NOTIFICATIONS.md"
    missing = tmp_path / "missing"
    empty = tmp_path / "empty"
    empty.mkdir()

    assert heartbeat.beat(missing, notifications, NOW, down_since=None) == NOW
    assert heartbeat.beat(empty, notifications, NOW, down_since=None) == NOW
    assert len(entries(notifications)) == 2


def test_entries_append_to_existing_notes(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    notifications = tmp_path / "NOTIFICATIONS.md"
    notifications.write_text("## Earlier note\n\n- Keep me.\n\n")

    heartbeat.beat(images, notifications, NOW, down_since=None)

    assert notifications.read_text().startswith("## Earlier note\n\n- Keep me.\n\n")
    assert len(entries(notifications)) == 2
