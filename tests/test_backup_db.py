"""Tests for scripts/backup_db.sh, the nightly database backup."""

import os
import sqlite3
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backup_db.sh"


def run_backup(db: Path, dest: Path, keep: int) -> None:
    """Run the backup script against a temporary database and directory."""
    env = {**os.environ, "DB": str(db), "DEST": str(dest), "KEEP": str(keep)}
    subprocess.run(["bash", str(SCRIPT)], env=env, check=True)


def test_backup_copies_database_and_keeps_newest(tmp_path):
    db = tmp_path / "labels.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (42)")
    dest = tmp_path / "backups"
    dest.mkdir()
    # Older backups sort before the new one by their timestamped names.
    for stamp in ("20260101-000000", "20260102-000000", "20260103-000000"):
        (dest / f"labels-{stamp}.sqlite3").write_text("old")
    (dest / "unrelated.txt").write_text("keep me")

    run_backup(db, dest, keep=2)

    backups = sorted(p.name for p in dest.glob("labels-*.sqlite3"))
    assert len(backups) == 2
    assert backups[0] == "labels-20260103-000000.sqlite3"
    with sqlite3.connect(dest / backups[1]) as conn:
        assert conn.execute("SELECT x FROM t").fetchall() == [(42,)]
    assert (dest / "unrelated.txt").exists()
