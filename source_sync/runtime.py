from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path


class Runtime:
    def __init__(self, root: str | Path = "~/.info-collector/source-sync"):
        self.root = Path(root).expanduser()
        self.logs = self.root / "logs"
        self.locks = self.root / "locks"
        self.status_file = self.root / "status.json"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.locks.mkdir(parents=True, exist_ok=True)
        if not self.status_file.exists():
            self._write_json(self.status_file, {})

    @contextmanager
    def lock(self, name: str):
        path = self.locks / f"{name}.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError(f"{name} is already running") from error
            yield
        finally:
            os.close(fd)

    def status(self) -> dict:
        try:
            return json.loads(self.status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def update_operation(self, group: str, key: str, **patch) -> None:
        lock_path = self.locks / "status.lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            state = self.status()
            state.setdefault(group, {}).setdefault(key, {}).update(patch)
            self._write_json(self.status_file, state)
        finally:
            os.close(fd)

    def log(self, message: str) -> None:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with (self.logs / "source-sync.log").open("a", encoding="utf-8") as file:
            file.write(f"[{stamp}] {message}\n")

    def maintenance(self, database: sqlite3.Connection, database_path: str | Path) -> Path:
        result = database.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {result}")
        backups = self.root / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        destination = backups / f"source-sync-{date.today().isoformat()}.db"
        fd, temp = tempfile.mkstemp(dir=backups)
        os.close(fd)
        try:
            target = sqlite3.connect(temp)
            try:
                database.backup(target)
            finally:
                target.close()
            os.replace(temp, destination)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        self.update_operation(
            "maintenance", "daily", outcome="ok", finishedAt=utc_iso(),
            backup=str(destination), database=str(Path(database_path).expanduser()),
        )
        return destination

    @staticmethod
    def _write_json(path: Path, value: dict) -> None:
        fd, temp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(value, file, ensure_ascii=False, indent=2)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
