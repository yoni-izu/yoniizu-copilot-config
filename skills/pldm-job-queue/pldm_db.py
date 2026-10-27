"""Local PLDM observations. External queue and Actions IDs remain opaque payloads.

Import happens once on first access. ``pjq.py migrate`` explicitly performs only
that local operation; ``pjq.py export`` emits a rollback-compatible JSON document.
Legacy files are never updated. Snapshot saves merge independent changes and
reject conflicting writers rather than silently losing an observation.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from domain_db import audit, backup_legacy, transaction


SCHEMA = """
CREATE TABLE IF NOT EXISTS pldm_jobs (
    id TEXT PRIMARY KEY, payload TEXT NOT NULL
);
"""
VERSION = 1


class Snapshot(dict):
    def __init__(self, value):
        super().__init__(value)
        self.original = deepcopy(value)


def validate(value):
    if not isinstance(value, dict) or not isinstance(value.get("jobs", []), list):
        raise ValueError("PLDM store must be an object with a jobs list")
    seen = set()
    for job in value.get("jobs", []):
        if (not isinstance(job, dict) or type(job.get("id")) is not int
                or job["id"] < 1 or job["id"] in seen):
            raise ValueError("PLDM jobs require unique positive integer IDs")
        seen.add(job["id"])
    return value


class Store:
    def __init__(self, path, legacy):
        self.path, self.legacy = Path(path), Path(legacy)

    def _prepare(self, c):
        row = c.execute("SELECT value FROM meta WHERE key='pldm_version'").fetchone()
        version = int(row[0]) if row else 0
        if version > VERSION:
            raise ValueError(f"unsupported PLDM database version: {version}")
        if version == 0:
            # The marker and every imported record commit together.
            value = {"jobs": [], "last_scan": None}
            if self.legacy.exists():
                backup_legacy(self.legacy, self.path)
                value = validate(json.loads(self.legacy.read_text(encoding="utf-8")))
            for job in value.get("jobs", []):
                self._put(c, job, "import")
            root = {k: v for k, v in value.items() if k != "jobs"}
            root.setdefault("last_scan", None)
            self._meta(c, "pldm_root", root)
            self._meta(c, "pldm_next_id",
                       max((j["id"] for j in value.get("jobs", [])), default=0) + 1)
            self._meta(c, "pldm_version", VERSION)
            audit(c, "migration", "pldm", "import", {"source": str(self.legacy)})

    @staticmethod
    def _meta(c, key, value):
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                  (key, json.dumps(value, ensure_ascii=False)))

    @staticmethod
    def _value(c, key):
        return json.loads(c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0])

    @staticmethod
    def _put(c, job, action="update"):
        c.execute("INSERT OR REPLACE INTO pldm_jobs(id,payload) VALUES(?,?)",
                  (str(job["id"]), json.dumps(job, ensure_ascii=False)))
        audit(c, "submission", str(job["id"]), action, job)

    def _load(self, c):
        root = self._value(c, "pldm_root")
        root["jobs"] = [json.loads(r[0]) for r in
                        c.execute("SELECT payload FROM pldm_jobs ORDER BY rowid")]
        return root

    def load(self):
        with transaction(self.path, SCHEMA) as c:
            self._prepare(c)
            return Snapshot(self._load(c))

    def save(self, value):
        validate(value)
        if not isinstance(value, Snapshot):
            raise ValueError("PLDM save requires the snapshot returned by load()")
        with transaction(self.path, SCHEMA) as c:
            self._prepare(c)
            current = self._load(c)
            baseline = value.original
            before = {j["id"]: j for j in baseline["jobs"]}
            after = {j["id"]: j for j in value["jobs"]}
            live = {j["id"]: j for j in current["jobs"]}
            for ident in before.keys() | after.keys():
                old, new = before.get(ident), after.get(ident)
                if old == new:
                    continue
                if live.get(ident) != old:
                    raise ValueError(f"concurrent PLDM change to job {ident}; reload and retry")
                if new is None:
                    c.execute("DELETE FROM pldm_jobs WHERE id=?", (str(ident),))
                    audit(c, "submission", str(ident), "delete")
                else:
                    self._put(c, new)
            root = {k: v for k, v in current.items() if k != "jobs"}
            for key in (baseline.keys() | value.keys()) - {"jobs"}:
                if baseline.get(key) == value.get(key):
                    continue
                if key == "last_scan" and root.get(key) and value.get(key):
                    root[key] = max(root[key], value[key])
                    continue
                if current.get(key) != baseline.get(key):
                    raise ValueError(f"concurrent PLDM metadata change: {key}")
                if key in value:
                    root[key] = value[key]
                else:
                    root.pop(key, None)
            self._meta(c, "pldm_root", root)
            self._meta(c, "pldm_next_id", max(
                self._value(c, "pldm_next_id"), max(after, default=0) + 1))
            audit(c, "tracker", "pldm", "save", root)

    def record(self, job):
        with transaction(self.path, SCHEMA) as c:
            self._prepare(c)
            # Discover may race another discover process. The durable external
            # identity is checked under the same writer lock as ID allocation.
            if job.get("octl_epoch"):
                for existing in self._load(c)["jobs"]:
                    if existing.get("octl_epoch") == job["octl_epoch"]:
                        return existing
            job = dict(job, id=self._value(c, "pldm_next_id"))
            self._meta(c, "pldm_next_id", job["id"] + 1)
            self._put(c, job, "record")
            return job

    def update(self, ident, changes):
        with transaction(self.path, SCHEMA) as c:
            self._prepare(c)
            row = c.execute("SELECT payload FROM pldm_jobs WHERE id=?", (str(ident),)).fetchone()
            if row is None:
                return None
            job = json.loads(row[0])
            job.update(changes)
            self._put(c, job)
            return job
