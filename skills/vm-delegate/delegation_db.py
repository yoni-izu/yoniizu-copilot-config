"""Transactional local delegation history; never writes mctl's producer log.

``vd.py migrate`` imports local legacy observations and ingests dispatches only.
``vd.py export`` emits {joblog: [...], jobstate: {...}, vmstate: {...}} for an
explicit rollback. No export is mirrored automatically to legacy JSON files.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from domain_db import audit, backup_legacy, transaction


SCHEMA = """
CREATE TABLE IF NOT EXISTS delegation_dispatches (
    id TEXT PRIMARY KEY, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS delegation_documents (
    name TEXT PRIMARY KEY, payload TEXT NOT NULL
);
"""
VERSION = 1
EMPTY_STATE = {"jobs": {}, "last_refresh": "", "last_attempt": "",
               "errors": {}, "attempted": {}}
EMPTY_MACHINES = {"machines": [], "checked": "", "error": ""}
MISSING = object()


class Snapshot(dict):
    def __init__(self, value):
        super().__init__(value)
        self.original = deepcopy(value)


def validate_state(value):
    if not isinstance(value, dict):
        raise ValueError("delegation jobstate must be an object")
    for key in ("jobs", "errors", "attempted", "checked"):
        if key in value and not isinstance(value[key], dict):
            raise ValueError(f"delegation jobstate.{key} must be an object")
    if any(not isinstance(v, dict) for v in value.get("jobs", {}).values()):
        raise ValueError("delegation job observations must be objects")
    return value


def validate_machines(value):
    if not isinstance(value, dict) or not isinstance(value.get("machines", []), list):
        raise ValueError("delegation vmstate must be an object with a machines list")
    if any(not isinstance(v, dict) for v in value.get("machines", [])):
        raise ValueError("delegation machines must be objects")
    return value


def merge(old, new, current, path=""):
    """Three-way patch: never revert fields another writer changed."""
    if old == new or new == current:
        return current
    if current == old:
        return new
    if all(isinstance(v, dict) for v in (old, new, current)):
        out = deepcopy(current)
        for key in old.keys() | new.keys():
            value = merge(old.get(key, MISSING), new.get(key, MISSING),
                          current.get(key, MISSING), path + "." + key)
            if value is MISSING:
                out.pop(key, None)
            else:
                out[key] = value
        return out
    raise ValueError(f"concurrent delegation change at {path}; reload and retry")


class Store:
    def __init__(self, path, joblog, jobstate, vmstate):
        self.path = Path(path)
        self.joblog, self.jobstate, self.vmstate = map(Path, (joblog, jobstate, vmstate))

    @staticmethod
    def _document(c, name, default):
        row = c.execute("SELECT payload FROM delegation_documents WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else deepcopy(default)

    @staticmethod
    def _put(c, name, value, action="observe"):
        c.execute("INSERT OR REPLACE INTO delegation_documents(name,payload) VALUES(?,?)",
                  (name, json.dumps(value, ensure_ascii=False)))
        audit(c, "observation", name, action, value)

    def _prepare(self, c):
        row = c.execute("SELECT value FROM meta WHERE key='delegation_version'").fetchone()
        version = int(row[0]) if row else 0
        if version > VERSION:
            raise ValueError(f"unsupported delegation database version: {version}")
        if version == 0:
            for name, source, default, validate in (
                ("jobstate", self.jobstate, EMPTY_STATE, validate_state),
                ("vmstate", self.vmstate, EMPTY_MACHINES, validate_machines),
            ):
                value = deepcopy(default)
                if source.exists():
                    backup_legacy(source, self.path)
                    value = validate(json.loads(source.read_text(encoding="utf-8")))
                self._put(c, name, value, "import")
            c.execute("INSERT INTO meta(key,value) VALUES('delegation_version',?)", (str(VERSION),))
            audit(c, "migration", "delegation", "import")

    @staticmethod
    def _dispatch(c, rec):
        if not isinstance(rec, dict) or not isinstance(rec.get("id"), (str, int)) or not rec["id"]:
            raise ValueError("delegation dispatch requires an id")
        ident = str(rec["id"])
        row = c.execute("SELECT payload FROM delegation_dispatches WHERE id=?", (ident,)).fetchone()
        if row:
            if json.loads(row[0]) != rec:
                raise ValueError(f"conflicting delegation dispatch identity: {ident}")
            return
        c.execute("INSERT INTO delegation_dispatches(id,payload) VALUES(?,?)",
                  (ident, json.dumps(rec, ensure_ascii=False)))
        audit(c, "dispatch", ident, "ingest", rec)

    def _ingest(self, c):
        if not self.joblog.exists():
            return
        # Replaying by durable ID handles log rotation, appended lines and
        # reordered/repeated entries. A partial/malformed line fails explicitly;
        # no cursor advances and no partial batch is committed.
        for number, line in enumerate(self.joblog.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                self._dispatch(c, json.loads(line))
            except ValueError as exc:
                raise ValueError(f"{self.joblog}:{number}: {exc}") from exc

    def history(self):
        with transaction(self.path, SCHEMA) as c:
            self._prepare(c)
            self._ingest(c)
            return [json.loads(r[0]) for r in c.execute(
                "SELECT payload FROM delegation_dispatches ORDER BY rowid")]

    def adopt(self, rec):
        with transaction(self.path, SCHEMA) as c:
            self._prepare(c)
            self._dispatch(c, rec)

    def read(self, name):
        with transaction(self.path, SCHEMA) as c:
            self._prepare(c)
            return Snapshot(self._document(c, name, {}))

    def write(self, name, value, dispatches=()):
        (validate_state if name == "jobstate" else validate_machines)(value)
        if not isinstance(value, Snapshot):
            raise ValueError("delegation write requires the snapshot returned by read()")
        with transaction(self.path, SCHEMA) as c:
            self._prepare(c)
            for rec in dispatches:
                self._dispatch(c, rec)
            current = self._document(c, name, {})
            old = value.original
            proposed = deepcopy(dict(value))
            if name == "jobstate":
                # A delayed probe must not replace a more recent reading or
                # clear that probe's error. Group all per-target fields.
                stale = {t for t, stamp in proposed.get("attempted", {}).items()
                         if stamp < current.get("attempted", {}).get(t, "")}
                for key in ("attempted", "checked", "errors"):
                    for t in stale:
                        if t in current.get(key, {}):
                            proposed.setdefault(key, {})[t] = current[key][t]
                        else:
                            proposed.get(key, {}).pop(t, None)
                for ident, observation in list(proposed.get("jobs", {}).items()):
                    live = current.get("jobs", {}).get(ident, {})
                    rec = c.execute("SELECT payload FROM delegation_dispatches WHERE id=?",
                                    (str(ident),)).fetchone()
                    target = (json.loads(rec[0]).get("target") or "vm") if rec else None
                    if (target in stale or observation.get("checked", "") < live.get("checked", "")):
                        proposed["jobs"][ident] = live
                for key in ("last_refresh", "last_attempt"):
                    if proposed.get(key) and current.get(key):
                        proposed[key] = max(proposed[key], current[key])
            else:
                if proposed.get("last_attempt", proposed.get("checked", "")) < current.get(
                        "last_attempt", current.get("checked", "")):
                    return
            result = merge(old, proposed, current, name)
            self._put(c, name, result)

    def export(self):
        with transaction(self.path, SCHEMA) as c:
            self._prepare(c)
            self._ingest(c)
            return {"joblog": [json.loads(r[0]) for r in c.execute(
                        "SELECT payload FROM delegation_dispatches ORDER BY rowid")],
                    "jobstate": self._document(c, "jobstate", {}),
                    "vmstate": self._document(c, "vmstate", {})}
