"""RAID-owned SQLite persistence and lossless legacy interchange.

The document is stored intact, not reconstructed from display-only projections.
All edits are serialized and revision checked; original imports remain evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.domain_db import audit, backup_legacy, transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS raid_document (
 id INTEGER PRIMARY KEY CHECK(id=1), text TEXT NOT NULL, revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS raid_imports (
 digest TEXT PRIMARY KEY, source TEXT NOT NULL, original TEXT NOT NULL);
"""
_active = ContextVar("raid_transaction", default=None)


class Conflict(ValueError):
    """A caller edited an obsolete revision."""


def path() -> Path:
    return Path(os.environ.get("RAID_DB", "~/tasks/raid.db")).expanduser()


@contextmanager
def edit():
    current = _active.get()
    if current is not None:
        yield current
        return
    with transaction(path(), SCHEMA) as conn:
        version = conn.execute("SELECT value FROM meta WHERE key='raid_schema'").fetchone()
        if version is not None and version[0] != "1":
            raise ValueError("unsupported RAID schema version")
        if version is None:
            conn.execute("INSERT INTO meta VALUES('raid_schema','1')")
            audit(conn, "raid", "schema", "migrate", {"version": 1})
        conn.execute("INSERT OR IGNORE INTO raid_document VALUES(1,'',0)")
        token = _active.set(conn)
        try:
            yield conn
        finally:
            _active.reset(token)


def atomic(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with edit():
            return fn(*args, **kwargs)
    return wrapped


def snapshot() -> tuple[str, int]:
    with edit() as conn:
        row = conn.execute("SELECT text,revision FROM raid_document WHERE id=1").fetchone()
        return row["text"], row["revision"]


def save(text: str, expected_revision: int | None = None, action="edit") -> int:
    with edit() as conn:
        _, revision = snapshot()
        if expected_revision is not None and expected_revision != revision:
            raise Conflict(f"RAID revision conflict: expected {expected_revision}, current {revision}")
        conn.execute("UPDATE raid_document SET text=?,revision=? WHERE id=1",
                     (text, revision + 1))
        audit(conn, "raid", "document", action, {"revision": revision + 1, "text": text})
        return revision + 1


def migrate(source: Path) -> bool:
    source = Path(source)
    backup_legacy(source, path())
    with source.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    digest = hashlib.sha256(text.encode()).hexdigest()
    with edit() as conn:
        if conn.execute("SELECT 1 FROM raid_imports WHERE digest=?", (digest,)).fetchone():
            return False
        if snapshot()[0]:
            raise Conflict("RAID already initialized; export/reconcile before importing another document")
        save(text, action="import")
        conn.execute("INSERT INTO raid_imports VALUES(?,?,?)", (digest, str(source), text))
    return True


def export(destination: Path) -> None:
    destination = Path(destination)
    if destination.resolve() == path().resolve():
        raise ValueError("export cannot overwrite database")
    destination.write_text(snapshot()[0], encoding="utf-8")
