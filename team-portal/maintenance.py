#!/usr/bin/env python3
"""Append-only maintenance journal for the Team Portal."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from domain_db import audit, transaction
from private_paths import private_root


DB_PATH = Path(os.path.expanduser(os.environ.get(
    "PORTAL_MAINTENANCE_DB",
    str(private_root() / "team-portal" / "state" / "portal-maintenance.db"))))
SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent INTEGER REFERENCES actions(id),
    status TEXT NOT NULL CHECK(status IN ('started','completed','failed','blocked')),
    summary TEXT NOT NULL CHECK(length(trim(summary)) > 0),
    component TEXT NOT NULL,
    validation TEXT NOT NULL,
    deployment TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TRIGGER IF NOT EXISTS actions_no_update BEFORE UPDATE ON actions
BEGIN SELECT RAISE(ABORT, 'Maintenance actions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS actions_no_delete BEFORE DELETE ON actions
BEGIN SELECT RAISE(ABORT, 'Maintenance actions are append-only'); END;
"""


def record(summary: str, status: str, component: str = "portal",
           validation: str = "", deployment: str = "",
           parent: int | None = None) -> int:
    if not summary.strip():
        raise ValueError("Action summary must not be empty")
    with transaction(DB_PATH, SCHEMA) as conn:
        conn.execute("INSERT OR IGNORE INTO meta VALUES('schema_version','1')")
        if parent is not None:
            previous = conn.execute(
                "SELECT status FROM actions WHERE id=?", (parent,)).fetchone()
            if previous is None or previous["status"] != "started":
                raise ValueError("Parent must reference an existing started action")
        cursor = conn.execute(
            "INSERT INTO actions(parent,status,summary,component,validation,deployment) "
            "VALUES(?,?,?,?,?,?)",
            (parent, status, summary.strip(), component, validation, deployment),
        )
        ident = cursor.lastrowid
        audit(conn, "maintenance", str(ident), status,
              {"summary": summary, "parent": parent}, actor="portal-maintainer")
        return ident


def history(limit: int = 20) -> list[dict]:
    if limit < 1:
        raise ValueError("Limit must be positive")
    with transaction(DB_PATH, SCHEMA) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("list")
    show.add_argument("--limit", type=int, default=20)
    add = commands.add_parser("record")
    add.add_argument("--summary", required=True)
    add.add_argument("--status", required=True,
                     choices=("started", "completed", "failed", "blocked"))
    add.add_argument("--parent", type=int)
    add.add_argument("--component", default="portal")
    add.add_argument("--validation", default="")
    add.add_argument("--deployment", default="")
    args = parser.parse_args()
    try:
        result = (history(args.limit) if args.command == "list" else
                  {"id": record(args.summary, args.status, args.component,
                                args.validation, args.deployment, args.parent)})
    except (ValueError, OSError) as error:
        parser.exit(1, f"maintenance: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
