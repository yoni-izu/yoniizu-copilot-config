"""Workspace-owned SQLite roster, status cache and append-only controller log."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sys
import sqlite3
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import domain_db

DB_PATH = Path(os.environ.get("WORKSPACES_DB", "~/tasks/workspaces.db")).expanduser()
LEGACY = Path(os.environ.get("WORKSPACES_LEGACY_JSON", "~/tasks/workspaces.json")).expanduser()
SCHEMA = """
CREATE TABLE IF NOT EXISTS tracked (slot TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS slots (slot TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_groups (position INTEGER PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_status (workspace_id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, workspace_id TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS workspace_events_owner ON workspace_events(workspace_id,id);
"""
_state = threading.local()
_MISSING = object()


class ConflictError(ValueError):
    pass


class Snapshot(dict):
    def __init__(self, value):
        super().__init__(value)
        self.baseline = copy.deepcopy(value)


def _merge(base, ours, theirs, path="root"):
    if ours == base:
        return copy.deepcopy(theirs) if theirs is not _MISSING else _MISSING
    if ours is _MISSING or theirs is _MISSING:
        if theirs == base:
            return copy.deepcopy(ours) if ours is not _MISSING else _MISSING
        raise ConflictError(f"concurrent deletion/edit: {path}")
    if path.rsplit(".", 1)[-1] == "notes" and isinstance(base, list):
        if not (isinstance(ours, list) and isinstance(theirs, list)
                and ours[:len(base)] == base and theirs[:len(base)] == base):
            raise ConflictError(f"notes are append-only: {path}")
        return copy.deepcopy(theirs + ours[len(base):])
    if isinstance(base, dict) and isinstance(ours, dict) and isinstance(theirs, dict):
        out = {}
        for key in dict.fromkeys([*theirs, *ours, *base]):
            value = _merge(base.get(key, _MISSING), ours.get(key, _MISSING),
                           theirs.get(key, _MISSING), f"{path}.{key}")
            if value is not _MISSING:
                out[key] = value
        return out
    if theirs == base or ours == theirs:
        return copy.deepcopy(ours)
    raise ConflictError(f"concurrent change: {path}")


def _validate(data):
    if not isinstance(data, dict):
        raise ValueError("workspace store must be an object")
    for field in ("tracked", "slots"):
        if field in data and not isinstance(data[field], dict):
            raise ValueError(f"{field} must be an object")
        if any(not isinstance(v, dict) and not (field == "slots" and v is None)
               for v in data.get(field, {}).values()):
            raise ValueError(f"{field} entries must be objects")
    if "groups" in data and not isinstance(data["groups"], list):
        raise ValueError("groups must be an array")


def _read(c):
    data = json.loads(c.execute("SELECT value FROM meta WHERE key='workspace_root'").fetchone()[0])
    for field in ("tracked", "slots"):
        data[field] = {r[0]: json.loads(r[1]) for r in c.execute(f"SELECT slot,body FROM {field}")}
    data["groups"] = [json.loads(r[0]) for r in c.execute(
        "SELECT body FROM workspace_groups ORDER BY position")]
    return data


def _write(c, data):
    root = {k: v for k, v in data.items() if k not in ("tracked", "slots", "groups")}
    c.execute("INSERT OR REPLACE INTO meta VALUES('workspace_root',?)", (json.dumps(root),))
    for field in ("tracked", "slots"):
        existing = {r[0] for r in c.execute(f"SELECT slot FROM {field}")}
        for slot in existing - data.get(field, {}).keys():
            c.execute(f"DELETE FROM {field} WHERE slot=?", (slot,))
        c.executemany(f"INSERT INTO {field} VALUES(?,?) ON CONFLICT(slot) DO UPDATE SET body=excluded.body",
                      [(k, json.dumps(v)) for k, v in data.get(field, {}).items()])
    c.execute("DELETE FROM workspace_groups")
    c.executemany("INSERT INTO workspace_groups VALUES(?,?)",
                  [(i, json.dumps(v)) for i, v in enumerate(data.get("groups", []))])


def _initialize(c, seed):
    version = c.execute("SELECT value FROM meta WHERE key='workspace_schema_version'").fetchone()
    if version and version[0] != "1":
        raise ValueError(f"unsupported workspace schema version: {version[0]}")
    if c.execute("SELECT 1 FROM meta WHERE key='workspace_root'").fetchone():
        return
    data = seed() if seed else {"version": 1, "tracked": {}, "slots": {}, "groups": []}
    if Path(LEGACY).exists():
        data = json.loads(Path(LEGACY).read_text())
        _validate(data)
        backup = domain_db.backup_legacy(LEGACY, DB_PATH)
        domain_db.audit(c, "workspace-store", "", "legacy-import", str(backup))
        if "tracked" not in data and seed:
            data["tracked"] = seed()["tracked"]
    import uuid
    for rec in data.get("tracked", {}).values():
        if not rec.get("id"):
            rec["id"] = f"WS-{'R' if rec.get('target') else 'L'}-{uuid.uuid4().hex}"
    _validate(data)
    _write(c, data)
    directory = Path(LEGACY).parent / "workspace-state"
    # Controller caches and all historical events are imported once, never rewritten.
    for path in sorted(directory.glob("WS-*.json*")):
        if path.suffix not in (".json", ".jsonl"):
            continue
        content = path.read_text()
        if path.suffix == ".json":
            value = json.loads(content)
            if not isinstance(value, dict):
                raise ValueError(f"workspace status must be an object: {path}")
            c.execute("INSERT INTO workspace_status VALUES(?,?)", (path.stem, json.dumps(value)))
        else:
            events = [json.loads(line) for line in content.splitlines() if line.strip()]
            if any(not isinstance(event, dict) for event in events):
                raise ValueError(f"workspace events must be objects: {path}")
            c.executemany("INSERT INTO workspace_events(workspace_id,body) VALUES(?,?)",
                          [(path.stem, json.dumps(event)) for event in events])
        domain_db.backup_legacy(path, DB_PATH)
    c.execute("INSERT INTO meta VALUES('workspace_schema_version','1')")


@contextmanager
def connection(seed=None):
    active = getattr(_state, "connection", None)
    if active is not None:
        yield active
        return
    deadline = time.monotonic() + 30
    while True:
        try:
            c = domain_db.connect(DB_PATH, SCHEMA)
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
    try:
        c.execute("BEGIN IMMEDIATE")
        _initialize(c, seed)
        _state.connection = c
        try:
            yield c
        finally:
            _state.connection = None
        c.commit()
    except BaseException:
        c.rollback()
        raise
    finally:
        c.close()


def load(seed=None):
    with connection(seed) as c:
        return Snapshot(_read(c))


def save(data):
    _validate(data)
    import uuid
    for rec in data.get("tracked", {}).values():
        if not rec.get("id"):
            rec["id"] = f"WS-{'R' if rec.get('target') else 'L'}-{uuid.uuid4().hex}"
    with connection() as c:
        current = _read(c)
        base = getattr(data, "baseline", None)
        if base is None and any(current.get(k) for k in ("tracked", "slots", "groups")):
            raise ConflictError("save requires the snapshot returned by load()")
        result = _merge(base, data, current) if base is not None else copy.deepcopy(data)
        _write(c, result)
        domain_db.audit(c, "workspace-store", "", "save")
        if isinstance(data, Snapshot):
            data.clear()
            data.update(copy.deepcopy(result))
            data.baseline = copy.deepcopy(result)


@contextmanager
def transaction(seed=None):
    with connection(seed):
        data = load()
        yield data
        save(data)


def status_read(wid):
    with connection() as c:
        row = c.execute("SELECT body FROM workspace_status WHERE workspace_id=?", (wid,)).fetchone()
        return json.loads(row[0]) if row else {}


def status_write(wid, reading):
    with connection() as c:
        c.execute("INSERT INTO workspace_status VALUES(?,?) ON CONFLICT(workspace_id) "
                  "DO UPDATE SET body=excluded.body", (wid, json.dumps(reading)))
        domain_db.audit(c, "workspace", wid, "status")


def append_event(wid, event):
    with connection() as c:
        c.execute("INSERT INTO workspace_events(workspace_id,body) VALUES(?,?)",
                  (wid, json.dumps(event)))
        domain_db.audit(c, "workspace", wid, event.get("action", "event"), event)


def events(wid, limit=200):
    with connection() as c:
        rows = c.execute("SELECT body FROM workspace_events WHERE workspace_id=? "
                         "ORDER BY id DESC LIMIT ?", (wid, limit)).fetchall()
        return [json.loads(r[0]) for r in reversed(rows)]


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "import"):
        sub.add_parser(name).add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        if args.path.resolve() == DB_PATH.resolve():
            parser.error("export cannot overwrite the canonical database")
        with connection() as c:
            data = _read(c)
            data["_sqlite_status"] = {r[0]: json.loads(r[1]) for r in c.execute(
                "SELECT workspace_id,body FROM workspace_status")}
            data["_sqlite_events"] = [dict(workspace_id=r[0], event=json.loads(r[1]))
                                      for r in c.execute(
                                          "SELECT workspace_id,body FROM workspace_events ORDER BY id")]
        args.path.write_text(json.dumps(data, indent=2) + "\n")
    else:
        value = json.loads(args.path.read_text())
        _validate(value)
        with transaction() as data:
            statuses = value.pop("_sqlite_status", {})
            history = value.pop("_sqlite_events", [])
            data.clear()
            data.update(value)
            for wid, reading in statuses.items():
                status_write(wid, reading)
            for item in history:
                append_event(item["workspace_id"], item["event"])


if __name__ == "__main__":
    main()
