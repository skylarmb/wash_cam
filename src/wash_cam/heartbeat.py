"""Record capture outages in NOTIFICATIONS.md for a human to review."""

import time
from datetime import datetime, timezone
from pathlib import Path

from wash_cam.config import CHECKOUT_ROOT, IMAGES_DIR

__all__ = ["beat", "newest_capture", "run"]

# Captures older than this mean capture has stopped (plan step 7: 5 minutes).
STALE_AFTER = 300.0

# Seconds between checks.
CHECK_INTERVAL = 60.0

# Git ignores this file. Skylar reads it each morning.
NOTIFICATIONS_PATH = CHECKOUT_ROOT / "NOTIFICATIONS.md"


def newest_capture(images_dir: Path) -> Path | None:
    """Return the capture with the newest mtime, or None if there is none.

    Filenames are local times, which repeat at the fall DST change, so the
    highest filename is not always the newest capture.
    """
    if not images_dir.is_dir():
        return None
    return max(
        images_dir.glob("*.jpg"), key=lambda path: path.stat().st_mtime, default=None
    )


def minutes(seconds: float) -> str:
    """Format a duration in whole minutes, for a human reader."""
    return f"{seconds / 60:.0f} min"


def append_entry(notifications: Path, now: float, title: str, detail: str) -> None:
    """Append one self-contained entry with the local and UTC time."""
    local = datetime.fromtimestamp(now).astimezone()
    utc = datetime.fromtimestamp(now, timezone.utc)
    heading = f"## {local:%Y-%m-%d %H:%M %Z} ({utc:%H:%M} UTC): heartbeat, {title}"
    with notifications.open("a") as file:
        file.write(f"{heading}\n\n- {detail}\n\n")


def beat(
    images_dir: Path, notifications: Path, now: float, down_since: float | None
) -> float | None:
    """Check capture freshness once. Write an entry when capture stops or resumes.

    `down_since` is None while capture runs. During an outage, it is the time of
    the last capture before the outage, or the time the outage was noticed if
    no capture exists. Return the new value.
    """
    newest = newest_capture(images_dir)
    newest_time = newest.stat().st_mtime if newest else None
    stale = newest_time is None or now - newest_time > STALE_AFTER
    if stale and down_since is None:
        if newest is None:
            detail = f"No captures in `{images_dir}`."
        else:
            detail = (
                f"No new capture for {minutes(now - newest_time)}. "
                f"Newest: `{newest.name}` in `{images_dir}`."
            )
        append_entry(notifications, now, "captures stopped", detail)
        return newest_time if newest_time is not None else now
    if not stale and down_since is not None:
        detail = (
            f"Newest: `{newest.name}` in `{images_dir}`. "
            f"No captures arrived for about {minutes(newest_time - down_since)}."
        )
        append_entry(notifications, now, "captures resumed", detail)
        return None
    return down_since


def run() -> None:
    """Check capture freshness forever. Outage state lives only in memory."""
    print(f"Checking {IMAGES_DIR} every {CHECK_INTERVAL:.0f}s", flush=True)
    down_since = None
    while True:
        down_since = beat(IMAGES_DIR, NOTIFICATIONS_PATH, time.time(), down_since)
        time.sleep(CHECK_INTERVAL)
