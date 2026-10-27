"""Outgoing-PR-owned SQLite store: lossless memoir and GitHub probe mirror."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.domain_db import audit, backup_legacy, transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS pr_document (
 id INTEGER PRIMARY KEY CHECK(id=1), text TEXT NOT NULL, revision INTEGER NOT NULL,
 cache TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pr_imports (
 digest TEXT PRIMARY KEY, source TEXT NOT NULL, original TEXT NOT NULL,
 cache_original TEXT NOT NULL);
"""
_active = ContextVar("pr_transaction", default=None)


class Conflict(ValueError):
    """A stale operation must be retried against current state."""


def path() -> Path:
    return Path(os.environ.get("PRS_DB", "~/tasks/prs.db")).expanduser()


@contextmanager
def edit():
    current = _active.get()
    if current is not None:
        yield current
        return
    with transaction(path(), SCHEMA) as conn:
        version = conn.execute("SELECT value FROM meta WHERE key='prs_schema'").fetchone()
        if version is not None and version[0] != "1":
            raise ValueError("unsupported PR schema version")
        if version is None:
            conn.execute("INSERT INTO meta VALUES('prs_schema','1')")
            audit(conn, "prs", "schema", "migrate", {"version": 1})
        conn.execute("INSERT OR IGNORE INTO pr_document VALUES(1,'',0,'{}')")
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


def snapshot() -> tuple[str, int, dict]:
    with edit() as conn:
        row = conn.execute("SELECT text,revision,cache FROM pr_document WHERE id=1").fetchone()
        return row["text"], row["revision"], json.loads(row["cache"])


def save(text: str, cache: dict | None = None, expected_revision: int | None = None,
         action="edit") -> int:
    with edit() as conn:
        _, revision, previous = snapshot()
        if expected_revision is not None and expected_revision != revision:
            raise Conflict(f"PR revision conflict: expected {expected_revision}, current {revision}")
        conn.execute("UPDATE pr_document SET text=?,revision=?,cache=? WHERE id=1",
                     (text, revision + 1, json.dumps(previous if cache is None else cache,
                                                   ensure_ascii=False)))
        audit(conn, "prs", "document", action, {"revision": revision + 1, "text": text,
                                              "cache": previous if cache is None else cache})
        return revision + 1


def migrate(source: Path, cache_path: Path | None = None) -> bool:
    source = Path(source)
    backup_legacy(source, path())
    with source.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    raw_cache = ""
    if cache_path is not None and Path(cache_path).exists():
        backup_legacy(Path(cache_path), path())
        with Path(cache_path).open(encoding="utf-8", newline="") as stream:
            raw_cache = stream.read()
    cache = json.loads(raw_cache) if raw_cache else {}
    digest = hashlib.sha256((text + "\0" + raw_cache).encode()).hexdigest()
    with edit() as conn:
        if conn.execute("SELECT 1 FROM pr_imports WHERE digest=?", (digest,)).fetchone():
            return False
        if snapshot()[0]:
            raise Conflict("PR store already initialized; export/reconcile before another import")
        save(text, cache, action="import")
        conn.execute("INSERT INTO pr_imports VALUES(?,?,?,?)",
                     (digest, str(source), text, raw_cache))
    return True


def export(destination: Path, cache_path: Path | None = None) -> None:
    text, _, cache = snapshot()
    targets = [Path(destination)] + ([Path(cache_path)] if cache_path is not None else [])
    if any(target.resolve() == path().resolve() for target in targets):
        raise ValueError("export cannot overwrite database")
    Path(destination).write_text(text, encoding="utf-8")
    if cache_path is not None:
        Path(cache_path).write_text(json.dumps(cache, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
