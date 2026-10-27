"""Task-owned SQLite persistence, snapshot merges, and explicit JSON exchange."""
from __future__ import annotations

import argparse
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

DB_PATH = Path(os.environ.get("TASKS_DB", "~/tasks/tasks.db")).expanduser()
LEGACY = Path(os.environ.get("TASKS_LEGACY_JSON", "~/tasks/tasks.json")).expanduser()
SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, position INTEGER NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_notes (
 task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(task_id,position));
CREATE TABLE IF NOT EXISTS task_reviews (
 task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
 position INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(task_id,position));
"""
_state = threading.local()
_MISSING = object()


class ConflictError(ValueError):
    """A stale snapshot changed a field another writer also changed."""


class Snapshot(dict):
    def __init__(self, value):
        super().__init__(value)
        self.baseline = copy.deepcopy(value)


def merge(base, ours, theirs, path=""):
    """Three-way merge: disjoint fields commute; notes only append."""
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
    if (path.rsplit(".", 1)[-1] == "reviews"
            and all(isinstance(v, list) for v in (base, ours, theirs))
            and all(isinstance(r, dict) and r.get("pr_url")
                    for rows in (base, ours, theirs) for r in rows)
            and all(len({r["pr_url"] for r in rows}) == len(rows)
                    for rows in (base, ours, theirs))):
        return list(merge({r["pr_url"]: r for r in base},
                          {r["pr_url"]: r for r in ours},
                          {r["pr_url"]: r for r in theirs},
                          path + ".entries").values())
    if isinstance(base, dict) and isinstance(ours, dict) and isinstance(theirs, dict):
        out = {}
        for key in dict.fromkeys([*theirs, *ours, *base]):
            value = merge(base.get(key, _MISSING), ours.get(key, _MISSING),
                          theirs.get(key, _MISSING), f"{path}.{key}")
            if value is not _MISSING:
                out[key] = value
        return out
    if theirs == base or ours == theirs:
        return copy.deepcopy(ours)
    raise ConflictError(f"concurrent change: {path}")


def _validate(data):
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError("task store must be an object with a tasks array")
    ids = [task.get("id") if isinstance(task, dict) else None for task in data["tasks"]]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("task IDs must be unique nonempty strings")
    if "next_id" in data and (type(data["next_id"]) is not int or data["next_id"] < 1):
        raise ValueError("next_id must be a positive integer")
    for task in data["tasks"]:
        for field in ("notes", "reviews"):
            if field in task and not isinstance(task[field], list):
                raise ValueError(f"{task['id']}.{field} must be an array")


def _read(c):
    value = json.loads(c.execute("SELECT value FROM meta WHERE key='task_root'").fetchone()[0])
    value["tasks"] = []
    for row in c.execute("SELECT id,body FROM tasks ORDER BY position"):
        task = json.loads(row["body"])
        for field, table in (("notes", "task_notes"), ("reviews", "task_reviews")):
            if field in task:
                task[field] = [json.loads(r[0]) for r in c.execute(
                    f"SELECT body FROM {table} WHERE task_id=? ORDER BY position", (row["id"],))]
        value["tasks"].append(task)
    return value


def _write(c, data):
    root = {k: v for k, v in data.items() if k != "tasks"}
    c.execute("INSERT OR REPLACE INTO meta VALUES('task_root',?)", (json.dumps(root),))
    current = {r[0] for r in c.execute("SELECT id FROM tasks")}
    desired = {t["id"] for t in data["tasks"]}
    for ident in current - desired:
        c.execute("DELETE FROM tasks WHERE id=?", (ident,))
        domain_db.audit(c, "task", ident, "delete")
    for pos, task in enumerate(data["tasks"]):
        body = dict(task)
        for field in ("notes", "reviews"):
            if field in body:
                body[field] = []
        c.execute("INSERT INTO tasks VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET "
                  "position=excluded.position,body=excluded.body",
                  (task["id"], pos, json.dumps(body)))
        for field, table in (("notes", "task_notes"), ("reviews", "task_reviews")):
            c.execute(f"DELETE FROM {table} WHERE task_id=?", (task["id"],))
            c.executemany(f"INSERT INTO {table} VALUES(?,?,?)",
                          [(task["id"], i, json.dumps(v)) for i, v in enumerate(task.get(field, []))])


def _initialize(c):
    version = c.execute("SELECT value FROM meta WHERE key='task_schema_version'").fetchone()
    if version and version[0] != "1":
        raise ValueError(f"unsupported task schema version: {version[0]}")
    if c.execute("SELECT 1 FROM meta WHERE key='task_root'").fetchone():
        return
    data = {"next_id": 1, "tasks": []}
    if Path(LEGACY).exists():
        data = json.loads(Path(LEGACY).read_text())
        _validate(data)
        backup = domain_db.backup_legacy(LEGACY, DB_PATH)
        domain_db.audit(c, "task-store", "", "legacy-import", str(backup))
    _validate(data)
    _write(c, data)
    c.execute("INSERT INTO meta VALUES('task_schema_version','1')")


@contextmanager
def _connection():
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
        _initialize(c)
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


def load() -> dict:
    with _connection() as c:
        return Snapshot(_read(c))


def save(data: dict) -> None:
    _validate(data)
    with _connection() as c:
        current = _read(c)
        base = getattr(data, "baseline", None)
        if base is None:
            if current["tasks"]:
                raise ConflictError("save requires the snapshot returned by load()")
            result = copy.deepcopy(data)
        else:
            root_base = {k: v for k, v in base.items() if k not in ("tasks", "next_id")}
            result = merge(root_base, {k: v for k, v in data.items() if k not in ("tasks", "next_id")},
                           {k: v for k, v in current.items() if k not in ("tasks", "next_id")}, "root")
            rows = merge({t["id"]: t for t in base["tasks"]},
                         {t["id"]: t for t in data["tasks"]},
                         {t["id"]: t for t in current["tasks"]}, "tasks")
            result["tasks"] = list(rows.values())
            result["next_id"] = max(data.get("next_id", 1), current.get("next_id", 1))
        _validate(result)
        _write(c, result)
        domain_db.audit(c, "task-store", "", "save")
        if isinstance(data, Snapshot):
            data.clear()
            data.update(copy.deepcopy(result))
            data.baseline = copy.deepcopy(result)


@contextmanager
def transaction():
    """Serialize a short mutator, including ID allocation; rollback on failure."""
    with _connection():
        data = load()
        yield data
        save(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "import"):
        sub.add_parser(name).add_argument("path", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        if args.path.resolve() == DB_PATH.resolve():
            parser.error("export cannot overwrite the canonical database")
        args.path.write_text(json.dumps(load(), indent=2) + "\n")
    else:
        value = json.loads(args.path.read_text())
        _validate(value)
        with transaction() as data:
            data.clear()
            data.update(value)


if __name__ == "__main__":
    main()
