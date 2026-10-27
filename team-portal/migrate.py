#!/usr/bin/env python3
"""Back up and import legacy stores through domain owners; no external actions.

Use --database-dir with an empty local directory to rehearse before cutover.
The legacy inputs are read-only. Re-running verifies/imports idempotently.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from private_paths import private_root


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise ValueError(f"Migration parity failed: {label}")


def migrate(directory: Path, manifest: Path | None = None) -> dict:
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for variable, filename in (
        ("TASKS_DB", "tasks.db"), ("WORKSPACES_DB", "workspaces.db"),
        ("PRS_DB", "prs.db"), ("RAID_DB", "raid.db"),
        ("PLDM_DB", "pldm.db"), ("DELEGATION_DB", "delegation.db"),
    ):
        os.environ[variable] = str(directory / filename)
    skills = Path(__file__).resolve().parents[1] / "skills"
    for owner in ("task-manager", "workspace-manager", "pr-monitor", "team-planner",
                  "pldm-job-queue", "vm-delegate"):
        sys.path.insert(0, str(skills / owner))
    import task_store
    import wm
    import pm
    import rd
    import pjq
    import vd

    sources = [task_store.LEGACY, wm.workspace_store.LEGACY, rd.RAID,
               pjq.STORE, vd.JOBLOG, vd.JOBSTATE, vd.VMSTATE]
    memoir = pm.store_path()
    if memoir is not None:
        sources.extend((memoir, pm.CACHE))
    sources.extend(sorted((wm.workspace_store.LEGACY.parent / "workspace-state").glob("WS-*.json*")))
    before = {str(p): digest(p) for p in sources if p.is_file()}
    report = {"at": datetime.now(timezone.utc).isoformat(),
              "directory": str(directory), "source_hashes": before, "domains": {}}
    tasks = task_store.load()
    if task_store.LEGACY.exists():
        require_equal("all task fields", tasks, json.loads(task_store.LEGACY.read_text()))
    report["domains"]["tasks"] = len(tasks["tasks"])
    require_equal("repeat task import", task_store.load(), tasks)

    workspaces = wm.load()
    if wm.workspace_store.LEGACY.exists():
        legacy = json.loads(wm.workspace_store.LEGACY.read_text())
        for key, value in legacy.items():
            require_equal(f"workspace {key}", workspaces[key], value)
    report["domains"]["workspaces"] = len(workspaces.get("tracked", {}))
    task_ids = {task["id"] for task in tasks["tasks"]}
    orphans = [slot for slot, holder in wm.all_holders(workspaces).items()
               if holder.get("type") == "task" and holder.get("task_id") not in task_ids]
    if orphans:
        raise ValueError(f"Orphaned task workspace holds: {len(orphans)}")

    if rd.RAID.exists():
        rd.db.migrate(rd.RAID)
        require_equal("RAID original bytes", rd.db.snapshot()[0].encode(), rd.RAID.read_bytes())
        require_equal("repeat RAID import", rd.db.migrate(rd.RAID), False)
    report["domains"]["raid"] = len(rd.parse())
    if memoir is not None:
        pm.db.migrate(memoir, pm.CACHE)
        require_equal("PR memoir original bytes", pm.db.snapshot()[0].encode(), memoir.read_bytes())
        require_equal("repeat PR import", pm.db.migrate(memoir, pm.CACHE), False)
    features = pm.records()
    feature_ids = [f["id"] for f in features]
    if len(feature_ids) != len(set(feature_ids)):
        raise ValueError("Conflicting legacy feature IDs require repair before cutover")
    report["domains"]["pr_features"] = len(features)

    jobs = pjq.load()
    if pjq.STORE.exists():
        legacy = json.loads(pjq.STORE.read_text())
        legacy.setdefault("last_scan", None)
        require_equal("PLDM records", jobs, legacy)
    report["domains"]["pldm"] = len(jobs["jobs"])
    history = vd._read_log()
    state = vd._store().read("jobstate")
    machines = vd._store().read("vmstate")
    if vd.JOBSTATE.exists():
        require_equal("delegation observations", state, json.loads(vd.JOBSTATE.read_text()))
    if vd.VMSTATE.exists():
        require_equal("machine observations", machines, json.loads(vd.VMSTATE.read_text()))
    require_equal("repeat dispatch ingestion", vd._read_log(), history)
    report["domains"]["delegation"] = len(history)
    report["task_statuses"] = dict(Counter(t.get("status") for t in tasks["tasks"]))
    for db_path in sorted(directory.glob("*.db")):
        with sqlite3.connect(db_path) as conn:
            require_equal(f"{db_path.name} integrity",
                          conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            require_equal(f"{db_path.name} foreign keys",
                          conn.execute("PRAGMA foreign_key_check").fetchall(), [])
    after = {str(p): digest(p) for p in sources if p.is_file()}
    require_equal("legacy sources unchanged during migration", before, after)
    report["verified"] = True
    manifest = (manifest or private_root() / "team-portal" / "state" /
                "migration-manifest.json").expanduser().resolve()
    manifest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest.write_text(json.dumps(report, indent=2) + "\n")
    manifest.chmod(0o600)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-dir", type=Path, default=Path("~/tasks"))
    parser.add_argument("--manifest", type=Path,
                        help="report path (default private portal state directory)")
    args = parser.parse_args()
    report = migrate(args.database_dir, args.manifest)
    print(json.dumps({key: report[key] for key in
                      ("at", "directory", "domains", "task_statuses", "verified")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
