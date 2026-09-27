"""Create and upgrade the SQLite database from numbered migrations."""

import sqlite3
from contextlib import closing
from pathlib import Path

from wash_cam.config import CHECKOUT_ROOT

__all__ = ["MIGRATIONS_DIR", "migrate"]

MIGRATIONS_DIR = CHECKOUT_ROOT / "db" / "migrations"


def migrate(path: Path) -> None:
    """Apply every migration newer than the database's recorded version.

    Creates the database when it is missing. Each migration file is named
    `NNN_description.sql` and sets `PRAGMA user_version` to NNN inside its own
    transaction, so a failed migration leaves the previous version intact and
    running this again is a no-op once the database is current.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if int(migration.name.split("_", 1)[0]) > version:
                connection.executescript(migration.read_text(encoding="utf-8"))
