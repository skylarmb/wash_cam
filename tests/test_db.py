"""Check that numbered migrations build and upgrade the database."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from wash_cam import db

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"


def tables(path):
    """Return the table names in a database."""
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {name for (name,) in rows}


def user_version(path):
    """Return the schema version recorded in a database."""
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute("PRAGMA user_version").fetchone()[0]


def test_migrate_creates_a_new_database(tmp_path):
    """A missing database is created with every migration applied."""
    path = tmp_path / "labels.sqlite3"

    db.migrate(path)

    assert {"image_labels", "image_detections", "sessions"} <= tables(path)
    assert user_version(path) == 4


def test_migrate_upgrades_without_touching_existing_labels(tmp_path):
    """A version 1 database gains later migrations and keeps its rows."""
    path = tmp_path / "labels.sqlite3"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript((MIGRATIONS / "001_image_labels.sql").read_text())
        connection.execute(
            "INSERT INTO image_labels (image_path, metadata) VALUES (?, ?)",
            ("a.jpg", json.dumps({"vehicles": {"bays": []}})),
        )

    db.migrate(path)
    db.migrate(path)

    assert user_version(path) == 4
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM image_labels").fetchone() == (
            1,
        )
