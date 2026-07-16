from __future__ import annotations

import sqlite3
from pathlib import Path


MIGRATIONS = Path(__file__).with_name("migrations")


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA synchronous = NORMAL")
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")
    migrate(db)
    return db


def migrate(db: sqlite3.Connection) -> None:
    has_meta = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    version = int(db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()[0]) if has_meta else 0

    for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        target = int(migration.name[:4])
        if target <= version:
            continue
        if target != version + 1:
            raise RuntimeError(f"missing migration after schema version {version}")
        db.executescript(migration.read_text(encoding="utf-8"))
        db.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(target),),
        )
        db.commit()
        version = target
