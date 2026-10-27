"""Shared SQLite mechanics; domain schemas and business rules stay with owners."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Iterator


COMMON_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, actor TEXT NOT NULL, entity TEXT NOT NULL,
    entity_id TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
);
"""


def revision(value: object) -> str:
    """Stable optimistic token for a domain record's JSON representation."""
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def connect(path: Path, schema: str = "") -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        path.chmod(0o600)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(COMMON_SCHEMA + schema)
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(path: Path, schema: str = "") -> Iterator[sqlite3.Connection]:
    conn = connect(path, schema)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def audit(conn: sqlite3.Connection, entity: str, entity_id: str, action: str,
          detail: object = "", actor: str = "cli") -> None:
    conn.execute(
        "INSERT INTO audit(at,actor,entity,entity_id,action,detail) VALUES(?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), actor, entity, entity_id, action,
         json.dumps(detail, ensure_ascii=False)),
    )


def backup_legacy(source: Path, db_path: Path) -> Path | None:
    """Keep a content-addressed original before importing; never overwrite it."""
    source = Path(source)
    if not source.exists():
        return None
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    directory = Path(db_path).parent / "migration-backups" / Path(db_path).stem
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / f"{source.name}.{digest}.bak"
    if not target.exists():
        with target.open("xb") as stream:
            stream.write(content)
        shutil.copystat(source, target)
        target.chmod(0o600)
    elif target.read_bytes() != content:
        raise ValueError(f"Migration backup does not match {source}")
    return target
