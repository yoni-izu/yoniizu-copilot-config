#!/usr/bin/env python3
"""Team-manager owned storage for unified member/1:1/planning records."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import domain_db
from team_portal import TeamPortalMixin


DB_PATH = Path(os.environ.get("TEAM_DB", "~/tasks/team.db")).expanduser()
SCHEMA_VERSION = "8"
EMPLOYMENT_TYPES = ("full-time", "part-time", "student", "part-time-student", "contractor")
ROSTER_ROLES = ("member", "lead")
# Additive column upgrades keyed by the version that introduced them.
SCHEMA_UPGRADES: dict[str, list[tuple[str, str, str]]] = {
    "2": [
        ("leave_records", "status", "TEXT NOT NULL DEFAULT 'approved'"),
        ("leave_records", "external_ref", "TEXT"),
    ],
    "3": [
        ("members", "employment_type", "TEXT"),
        ("members", "role_title", "TEXT"),
    ],
    "4": [
        ("members", "base_country", "TEXT"),
        ("members", "roster_role", "TEXT NOT NULL DEFAULT 'member'"),
    ],
    "5": [
        ("members", "goals", "TEXT"),
        ("members", "profile_visibility", "TEXT NOT NULL DEFAULT 'private'"),
        ("members", "goals_visibility", "TEXT NOT NULL DEFAULT 'private'"),
        ("meetings", "visibility", "TEXT NOT NULL DEFAULT 'private'"),
        ("meetings", "title_visibility", "TEXT NOT NULL DEFAULT 'private'"),
        ("meeting_entries", "position", "INTEGER NOT NULL DEFAULT 0"),
        ("assignment_updates", "visibility", "TEXT NOT NULL DEFAULT 'private'"),
        ("sprints", "milestone_id", "TEXT REFERENCES milestones(milestone_id)"),
    ],
    "6": [
        ("members", "focus", "TEXT"),
        ("members", "focus_visibility", "TEXT NOT NULL DEFAULT 'shared'"),
    ],
    "7": [
        ("assignments", "category", "TEXT NOT NULL DEFAULT 'misc'"),
        ("assignments", "priority", "TEXT NOT NULL DEFAULT 'medium'"),
    ],
    "8": [
        ("meeting_entries", "fields_json", "TEXT"),
        ("meeting_entries", "record_type", "TEXT"),
        ("meeting_entries", "record_id", "TEXT"),
        ("roadblocks", "impact", "TEXT"),
        ("roadblocks", "unblocker", "TEXT"),
        ("roadblocks", "visibility", "TEXT NOT NULL DEFAULT 'private'"),
    ],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
  member_key TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  jira_account_id TEXT,
  github_handle TEXT,
  confluence_host_url TEXT,
  confluence_folder TEXT,
  onboarding_date TEXT,
  birthday_month INTEGER,
  birthday_day INTEGER,
  main_ip_ownership TEXT,
  profile_notes TEXT,
  employment_type TEXT,
  role_title TEXT,
  base_country TEXT,
  roster_role TEXT NOT NULL DEFAULT 'member',
  goals TEXT,
  profile_visibility TEXT NOT NULL DEFAULT 'private',
  goals_visibility TEXT NOT NULL DEFAULT 'private',
  focus TEXT,
  focus_visibility TEXT NOT NULL DEFAULT 'shared',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meetings (
  meeting_id TEXT PRIMARY KEY,
  member_key TEXT REFERENCES members(member_key),
  meeting_date TEXT,
  title TEXT NOT NULL,
  session_type TEXT NOT NULL,
  state TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  shared_summary TEXT,
  private_summary TEXT,
  full_markdown TEXT NOT NULL,
  finalized INTEGER NOT NULL DEFAULT 0,
  visibility TEXT NOT NULL DEFAULT 'private',
  title_visibility TEXT NOT NULL DEFAULT 'private',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_entries (
  entry_id TEXT PRIMARY KEY,
  meeting_id TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  body TEXT NOT NULL,
  visibility TEXT NOT NULL,
  actor TEXT,
  created_at TEXT NOT NULL,
  source_line TEXT
  ,position INTEGER NOT NULL DEFAULT 0
  ,fields_json TEXT
  ,record_type TEXT
  ,record_id TEXT
);

CREATE TABLE IF NOT EXISTS assignments (
  assignment_id TEXT PRIMARY KEY,
  member_key TEXT REFERENCES members(member_key),
  owner_label TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  kind TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'misc',
  priority TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL,
  outcome TEXT,
  target_date TEXT,
  jira_key TEXT,
  origin_meeting_id TEXT REFERENCES meetings(meeting_id),
  visibility TEXT NOT NULL DEFAULT 'private',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assignment_updates (
  update_id TEXT PRIMARY KEY,
  assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id) ON DELETE CASCADE,
  author TEXT NOT NULL,
  note TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'private',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback_items (
  feedback_id TEXT PRIMARY KEY,
  member_key TEXT REFERENCES members(member_key),
  kind TEXT NOT NULL,
  source_person TEXT,
  source_role TEXT,
  body TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'private',
  observed_at TEXT,
  received_at TEXT,
  delivered_at TEXT,
  meeting_id TEXT REFERENCES meetings(meeting_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roadblocks (
  roadblock_id TEXT PRIMARY KEY,
  member_key TEXT REFERENCES members(member_key),
  title TEXT NOT NULL,
  details TEXT,
  status TEXT NOT NULL,
  raised_at TEXT NOT NULL,
  resolved_at TEXT,
  meeting_id TEXT REFERENCES meetings(meeting_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  impact TEXT,
  unblocker TEXT,
  visibility TEXT NOT NULL DEFAULT 'private'
);

CREATE TABLE IF NOT EXISTS leave_records (
  leave_id TEXT PRIMARY KEY,
  member_key TEXT NOT NULL REFERENCES members(member_key),
  leave_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'approved',
  external_ref TEXT,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  requested_at TEXT,
  approved_at TEXT,
  cancelled_at TEXT,
  amount_days REAL,
  amount_source TEXT,
  source TEXT NOT NULL,
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_sets (
  feature_id TEXT PRIMARY KEY,
  member_key TEXT REFERENCES members(member_key),
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  delivered_at TEXT,
  grouping_basis TEXT NOT NULL,
  confirmation_state TEXT NOT NULL,
  acceptance_gate TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_prs (
  feature_pr_id TEXT PRIMARY KEY,
  feature_id TEXT NOT NULL REFERENCES feature_sets(feature_id) ON DELETE CASCADE,
  pr_ref TEXT NOT NULL,
  repo TEXT,
  pr_number INTEGER,
  pr_title TEXT,
  contributor TEXT,
  merged_at TEXT,
  jira_keys_json TEXT NOT NULL,
  role TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issue_completions (
  completion_id TEXT PRIMARY KEY,
  member_key TEXT REFERENCES members(member_key),
  issue_key TEXT NOT NULL,
  issue_type TEXT NOT NULL,
  resolution TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  assignee_at_completion TEXT,
  source TEXT NOT NULL,
  source_ref TEXT,
  created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS issue_unique_completion
  ON issue_completions(issue_key, completed_at, source);

CREATE TABLE IF NOT EXISTS milestones (
  milestone_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_revisions (
  meeting_id TEXT NOT NULL REFERENCES meetings(meeting_id),
  revision TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  PRIMARY KEY(meeting_id, revision)
);
CREATE TRIGGER IF NOT EXISTS meeting_revision_no_update
BEFORE UPDATE ON meeting_revisions BEGIN SELECT RAISE(ABORT, 'immutable meeting revision'); END;
CREATE TRIGGER IF NOT EXISTS meeting_revision_no_delete
BEFORE DELETE ON meeting_revisions BEGIN SELECT RAISE(ABORT, 'immutable meeting revision'); END;

CREATE TABLE IF NOT EXISTS commitment_baselines (
  baseline_id TEXT PRIMARY KEY,
  member_key TEXT NOT NULL REFERENCES members(member_key),
  scope_id TEXT NOT NULL,
  work_refs_json TEXT NOT NULL,
  visibility TEXT NOT NULL DEFAULT 'private',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sprints (
  sprint_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  state TEXT NOT NULL,
  source TEXT NOT NULL,
  updated_at TEXT NOT NULL
  ,milestone_id TEXT REFERENCES milestones(milestone_id)
);

CREATE TABLE IF NOT EXISTS typed_refs (
  ref_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  domain_name TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  provenance TEXT NOT NULL,
  check_status TEXT NOT NULL DEFAULT 'unchecked',
  last_checked_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  document_id TEXT PRIMARY KEY,
  member_key TEXT REFERENCES members(member_key),
  document_type TEXT NOT NULL,
  title TEXT NOT NULL,
  period_start TEXT,
  period_end TEXT,
  visibility TEXT NOT NULL DEFAULT 'private',
  body_markdown TEXT NOT NULL,
  source_path TEXT,
  source_hash TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exports (
  export_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_revision TEXT NOT NULL,
  audience TEXT NOT NULL,
  confluence_page_id TEXT,
  confluence_version TEXT,
  exported_at TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS imports (
  import_id TEXT PRIMARY KEY,
  source_path TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT,
  entity_type TEXT,
  entity_id TEXT,
  imported_at TEXT NOT NULL
);
"""

MEMBER_FILE_RE = re.compile(r"^(?P<member>[a-z0-9_-]+)\.md$")
ARCHIVE_FILE_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})\.md$")
HISTORY_HEADING_RE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})\s*$")
ACTION_ROW_RE = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$"
)
JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,15}-\d+)\b")
PR_JIRA_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,15}-\d+)\b")
SECTIONS_RE = re.compile(r"^##\s+(.+?)\s*$")
FIELDS_RE = re.compile(r"^\s*-\s+\*\*([^:]+):\*\*\s*(.*)$")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "item"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _id(prefix: str, *parts: str) -> str:
    material = "|".join(parts)
    return f"{prefix}-{hashlib.sha1(material.encode('utf-8')).hexdigest()[:12]}"


def _parse_markdown_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = "__root__"
    sections[current] = []
    for line in text.splitlines():
        m = SECTIONS_RE.match(line)
        if m:
            current = m.group(1).strip()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


@dataclass
class Revisioned:
    row: dict[str, Any]
    revision: str


class TeamStore(TeamPortalMixin):
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        conn = domain_db.connect(self.db_path, SCHEMA)
        current = conn.execute(
            "SELECT value FROM meta WHERE key='team_schema_version'"
        ).fetchone()
        if current is None:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('team_schema_version',?)",
                (SCHEMA_VERSION,),
            )
        elif current[0] != SCHEMA_VERSION:
            if not current[0].isdigit() or int(current[0]) > int(SCHEMA_VERSION):
                raise ValueError(
                    f"unsupported team schema version: {current[0]} (expected {SCHEMA_VERSION})"
                )
            for version in range(int(current[0]) + 1, int(SCHEMA_VERSION) + 1):
                for table, column, ddl in SCHEMA_UPGRADES.get(str(version), []):
                    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                    if column not in cols:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            conn.execute(
                "UPDATE meta SET value=? WHERE key='team_schema_version'", (SCHEMA_VERSION,)
            )
            conn.commit()
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            domain_db.audit(conn, "team-store", "", "init", {"path": str(self.db_path)})

    def _upsert_member(self, conn: sqlite3.Connection, payload: dict[str, Any]) -> str:
        stamp = now_iso()
        conn.execute(
            """
            INSERT INTO members(
              member_key, display_name, jira_account_id, github_handle,
              confluence_host_url, confluence_folder, onboarding_date,
              birthday_month, birthday_day, main_ip_ownership, profile_notes,
              created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(member_key) DO UPDATE SET
              display_name=excluded.display_name,
              jira_account_id=excluded.jira_account_id,
              github_handle=excluded.github_handle,
              confluence_host_url=excluded.confluence_host_url,
              confluence_folder=excluded.confluence_folder,
              onboarding_date=excluded.onboarding_date,
              birthday_month=excluded.birthday_month,
              birthday_day=excluded.birthday_day,
              main_ip_ownership=excluded.main_ip_ownership,
              profile_notes=excluded.profile_notes,
              updated_at=excluded.updated_at
            """,
            (
                payload["member_key"],
                payload["display_name"],
                payload.get("jira_account_id"),
                payload.get("github_handle"),
                payload.get("confluence_host_url"),
                payload.get("confluence_folder"),
                payload.get("onboarding_date"),
                payload.get("birthday_month"),
                payload.get("birthday_day"),
                payload.get("main_ip_ownership"),
                payload.get("profile_notes", ""),
                stamp,
                stamp,
            ),
        )
        domain_db.audit(conn, "member", payload["member_key"], "upsert", payload)
        return payload["member_key"]

    def list_members(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT member_key, display_name, jira_account_id, github_handle,
                       confluence_host_url, confluence_folder, onboarding_date,
                       birthday_month, birthday_day, main_ip_ownership,
                       employment_type, role_title, base_country, roster_role
                FROM members ORDER BY display_name
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def get_member(self, member_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM members WHERE member_key=?", (member_key,)
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["revision"] = domain_db.revision(dict(row))
            return data

    MEMBER_EDITABLE = (
        "display_name", "jira_account_id", "github_handle", "confluence_host_url",
        "confluence_folder", "onboarding_date", "birthday_month", "birthday_day",
        "main_ip_ownership", "profile_notes", "employment_type", "role_title",
        "base_country", "roster_role",
        "goals", "profile_visibility", "goals_visibility", "focus", "focus_visibility",
    )

    def _validate_member_fields(self, patch: dict[str, Any]) -> None:
        unknown = set(patch) - set(self.MEMBER_EDITABLE)
        if unknown:
            raise ValueError(f"non-editable member fields: {sorted(unknown)}")
        for key in ("profile_visibility", "goals_visibility", "focus_visibility"):
            if key in patch and patch[key] not in {"private", "shared"}:
                raise ValueError(f"{key} must be private or shared")
        if "display_name" in patch and (
            not isinstance(patch["display_name"], str) or not patch["display_name"].strip()
        ):
            raise ValueError("display_name must be nonempty")
        for key in ("goals", "profile_notes", "focus"):
            if key in patch and patch[key] is not None and not isinstance(patch[key], str):
                raise ValueError(f"{key} must be text or null")
        if "github_handle" in patch and patch["github_handle"]:
            patch["github_handle"] = str(patch["github_handle"]).strip().lower()
        if "onboarding_date" in patch and patch["onboarding_date"]:
            date.fromisoformat(patch["onboarding_date"])
        if patch.get("employment_type") and patch["employment_type"] not in EMPLOYMENT_TYPES:
            raise ValueError(f"employment_type must be one of {EMPLOYMENT_TYPES}")
        if patch.get("roster_role") is not None and patch["roster_role"] not in ROSTER_ROLES:
            raise ValueError(f"roster_role must be one of {ROSTER_ROLES}")
        if patch.get("base_country"):
            patch["base_country"] = str(patch["base_country"]).strip()
        for field, upper in (("birthday_month", 12), ("birthday_day", 31)):
            if patch.get(field) is not None and not 1 <= int(patch[field]) <= upper:
                raise ValueError(f"{field} out of range")

    def add_member(self, member_key: str, fields: dict[str, Any]) -> str:
        if not re.fullmatch(r"[a-z0-9_-]+", member_key):
            raise ValueError("member_key must match [a-z0-9_-]+")
        fields = dict(fields)
        self._validate_member_fields(fields)
        if not fields.get("display_name"):
            raise ValueError("display_name is required")
        stamp = now_iso()
        with self.connect() as conn:
            if conn.execute("SELECT 1 FROM members WHERE member_key=?", (member_key,)).fetchone():
                raise ValueError(f"member already exists: {member_key}")
            for column in ("jira_account_id", "github_handle"):
                if fields.get(column):
                    clash = conn.execute(
                        f"SELECT member_key FROM members WHERE {column}=?", (fields[column],)
                    ).fetchone()
                    if clash:
                        raise ValueError(f"{column} already used by {clash[0]}")
            columns = ["member_key", *fields, "created_at", "updated_at"]
            conn.execute(
                f"INSERT INTO members({', '.join(columns)}) VALUES({', '.join('?' for _ in columns)})",
                (member_key, *fields.values(), stamp, stamp),
            )
            domain_db.audit(conn, "member", member_key, "create", fields)
        return member_key

    def update_member(self, member_key: str, revision: str, patch: dict[str, Any]) -> None:
        self._validate_member_fields(patch)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM members WHERE member_key=?", (member_key,)).fetchone()
            if row is None:
                raise ValueError(f"member not found: {member_key}")
            if domain_db.revision(dict(row)) != revision:
                raise ValueError("stale revision; re-read the member before updating")
            if not patch:
                return
            for column in ("jira_account_id", "github_handle"):
                if patch.get(column) and conn.execute(
                    f"SELECT 1 FROM members WHERE {column}=? AND member_key<>?",
                    (patch[column], member_key),
                ).fetchone():
                    raise ValueError(f"{column} already used")
            assignments = ", ".join(f"{k}=?" for k in patch)
            conn.execute(
                f"UPDATE members SET {assignments}, updated_at=? WHERE member_key=?",
                (*patch.values(), now_iso(), member_key),
            )
            domain_db.audit(conn, "member", member_key, "update", patch)

    def _entity_row(
        self, conn: sqlite3.Connection, entity_type: str, entity_id: str
    ) -> dict[str, Any] | None:
        table, key = {
            "assignment": ("assignments", "assignment_id"),
            "meeting": ("meetings", "meeting_id"),
            "feedback": ("feedback_items", "feedback_id"),
            "roadblock": ("roadblocks", "roadblock_id"),
            "document": ("documents", "document_id"),
            "feature": ("feature_sets", "feature_id"),
            "issue-completion": ("issue_completions", "completion_id"),
        }[entity_type]
        row = conn.execute(
            f"SELECT * FROM {table} WHERE {key}=?",
            (entity_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_entities(self, entity_type: str, member: str | None) -> list[dict[str, Any]]:
        table, member_column = {
            "assignment": ("assignments", "member_key"),
            "meeting": ("meetings", "member_key"),
            "feedback": ("feedback_items", "member_key"),
            "roadblock": ("roadblocks", "member_key"),
            "document": ("documents", "member_key"),
            "feature": ("feature_sets", "member_key"),
            "issue-completion": ("issue_completions", "member_key"),
        }[entity_type]
        with self.connect() as conn:
            if member:
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE {member_column}=? ORDER BY created_at DESC",
                    (member,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM {table} ORDER BY created_at DESC"
                ).fetchall()
            result = []
            for row in rows:
                data = dict(row)
                data["revision"] = domain_db.revision(data)
                result.append(data)
            return result

    def create_entity(self, entity_type: str, member: str | None, payload: dict[str, Any]) -> str:
        if entity_type == "assignment":
            self._validate_assignment(payload)
        if "visibility" in payload and payload["visibility"] not in {"private", "shared"}:
            raise ValueError("visibility must be private or shared")
        stamp = now_iso()
        with self.connect() as conn:
            if entity_type == "assignment":
                ident = payload.get("assignment_id") or _id(
                    "asg", member or payload.get("owner_label", "team"), payload["title"], stamp
                )
                conn.execute(
                    """
                    INSERT INTO assignments(
                      assignment_id, member_key, owner_label, title, description, kind,
                      status, outcome, target_date, jira_key, origin_meeting_id, visibility,
                      created_at, updated_at, category, priority
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ident,
                        member,
                        payload.get("owner_label", "team-lead"),
                        payload["title"],
                        payload.get("description"),
                        payload.get("kind", "direction"),
                        payload.get("status", "assigned"),
                        payload.get("outcome"),
                        payload.get("target_date"),
                        payload.get("jira_key"),
                        payload.get("origin_meeting_id"),
                        payload.get("visibility", "private"),
                        stamp,
                        stamp,
                        payload.get("category", "misc"),
                        payload.get("priority", "medium"),
                    ),
                )
                if "refs" in payload:
                    self._save_task_refs(conn, ident, payload["refs"])
                domain_db.audit(conn, "assignment", ident, "create", payload)
                return ident
            if entity_type == "feedback":
                ident = payload.get("feedback_id") or _id(
                    "fb", member or "team", payload["kind"], payload["body"], stamp
                )
                conn.execute(
                    """
                    INSERT INTO feedback_items(
                      feedback_id, member_key, kind, source_person, source_role, body, visibility,
                      observed_at, received_at, delivered_at, meeting_id, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ident,
                        member,
                        payload["kind"],
                        payload.get("source_person"),
                        payload.get("source_role"),
                        payload["body"],
                        payload.get("visibility", "private"),
                        payload.get("observed_at"),
                        payload.get("received_at"),
                        payload.get("delivered_at"),
                        payload.get("meeting_id"),
                        stamp,
                        stamp,
                    ),
                )
                domain_db.audit(conn, "feedback", ident, "create", payload)
                return ident
            if entity_type == "roadblock":
                ident = payload.get("roadblock_id") or _id(
                    "rb", member or "team", payload["title"], stamp
                )
                conn.execute(
                    """
                    INSERT INTO roadblocks(
                      roadblock_id, member_key, title, details, status, raised_at, resolved_at,
                      meeting_id, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ident,
                        member,
                        payload["title"],
                        payload.get("details"),
                        payload.get("status", "open"),
                        payload.get("raised_at", stamp[:10]),
                        payload.get("resolved_at"),
                        payload.get("meeting_id"),
                        stamp,
                        stamp,
                    ),
                )
                domain_db.audit(conn, "roadblock", ident, "create", payload)
                return ident
            if entity_type == "document":
                ident = payload.get("document_id") or _id(
                    "doc", member or "team", payload["document_type"], payload["title"], stamp
                )
                conn.execute(
                    """
                    INSERT INTO documents(
                      document_id, member_key, document_type, title, period_start, period_end,
                      visibility, body_markdown, source_path, source_hash, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ident,
                        member,
                        payload["document_type"],
                        payload["title"],
                        payload.get("period_start"),
                        payload.get("period_end"),
                        payload.get("visibility", "private"),
                        payload.get("body_markdown", ""),
                        payload.get("source_path"),
                        payload.get("source_hash"),
                        stamp,
                        stamp,
                    ),
                )
                domain_db.audit(conn, "document", ident, "create", payload)
                return ident
            if entity_type == "feature":
                ident = payload.get("feature_id") or _id(
                    "feat", member or "team", payload["title"], stamp
                )
                conn.execute(
                    """
                    INSERT INTO feature_sets(
                      feature_id, member_key, title, status, delivered_at, grouping_basis,
                      confirmation_state, acceptance_gate, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ident,
                        member,
                        payload["title"],
                        payload.get("status", "candidate"),
                        payload.get("delivered_at"),
                        payload.get("grouping_basis", "jira-title"),
                        payload.get("confirmation_state", "candidate"),
                        payload.get("acceptance_gate"),
                        stamp,
                        stamp,
                    ),
                )
                for pr in payload.get("prs", []):
                    pr_id = _id("fpr", ident, pr["pr_ref"])
                    conn.execute(
                        """
                        INSERT INTO feature_prs(
                          feature_pr_id, feature_id, pr_ref, repo, pr_number, pr_title,
                          contributor, merged_at, jira_keys_json, role, created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            pr_id,
                            ident,
                            pr["pr_ref"],
                            pr.get("repo"),
                            pr.get("pr_number"),
                            pr.get("pr_title"),
                            pr.get("contributor"),
                            pr.get("merged_at"),
                            json.dumps(pr.get("jira_keys", []), ensure_ascii=False),
                            pr.get("role"),
                            stamp,
                        ),
                    )
                domain_db.audit(conn, "feature", ident, "create", payload)
                return ident
            if entity_type == "issue-completion":
                ident = payload.get("completion_id") or _id(
                    "ic", payload["issue_key"], payload["completed_at"], payload.get("source", "manual")
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO issue_completions(
                      completion_id, member_key, issue_key, issue_type, resolution,
                      completed_at, assignee_at_completion, source, source_ref, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        ident,
                        member,
                        payload["issue_key"],
                        payload["issue_type"],
                        payload["resolution"],
                        payload["completed_at"],
                        payload.get("assignee_at_completion"),
                        payload.get("source", "manual"),
                        payload.get("source_ref"),
                        stamp,
                    ),
                )
                domain_db.audit(conn, "issue-completion", ident, "create", payload)
                return ident
            raise ValueError(f"unsupported entity type: {entity_type}")

    def update_entity(self, entity_type: str, entity_id: str, revision: str, patch: dict[str, Any]) -> None:
        if entity_type == "assignment":
            allowed = {
                "member_key", "owner_label", "title", "description", "kind", "status",
                "outcome", "target_date", "jira_key", "origin_meeting_id", "visibility",
                "category", "priority",
                "refs",
            }
            if set(patch) - allowed:
                raise ValueError("non-editable assignment fields")
            self._validate_assignment(patch)
        if "visibility" in patch and patch["visibility"] not in {"private", "shared"}:
            raise ValueError("visibility must be private or shared")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._entity_row(conn, entity_type, entity_id)
            if row is None:
                raise ValueError(f"{entity_type} not found: {entity_id}")
            current_revision = domain_db.revision(row)
            if current_revision != revision:
                raise ValueError(
                    f"stale revision for {entity_type} {entity_id}: expected {current_revision}"
                )
            updated = dict(row)
            updated.update(patch)
            updated["updated_at"] = now_iso()
            if entity_type == "assignment":
                conn.execute(
                    """
                    UPDATE assignments SET
                      member_key=?, owner_label=?, title=?, description=?, kind=?, status=?,
                      outcome=?, target_date=?, jira_key=?, origin_meeting_id=?, visibility=?,
                      updated_at=?, category=?, priority=?
                    WHERE assignment_id=?
                    """,
                    (
                        updated.get("member_key"),
                        updated["owner_label"],
                        updated["title"],
                        updated.get("description"),
                        updated["kind"],
                        updated["status"],
                        updated.get("outcome"),
                        updated.get("target_date"),
                        updated.get("jira_key"),
                        updated.get("origin_meeting_id"),
                        updated.get("visibility", "private"),
                        updated["updated_at"],
                        updated["category"],
                        updated["priority"],
                        entity_id,
                    ),
                )
                if "refs" in patch:
                    self._save_task_refs(conn, entity_id, patch["refs"])
            elif entity_type == "feedback":
                conn.execute(
                    """
                    UPDATE feedback_items SET
                      member_key=?, kind=?, source_person=?, source_role=?, body=?, visibility=?,
                      observed_at=?, received_at=?, delivered_at=?, meeting_id=?, updated_at=?
                    WHERE feedback_id=?
                    """,
                    (
                        updated.get("member_key"),
                        updated["kind"],
                        updated.get("source_person"),
                        updated.get("source_role"),
                        updated["body"],
                        updated.get("visibility", "private"),
                        updated.get("observed_at"),
                        updated.get("received_at"),
                        updated.get("delivered_at"),
                        updated.get("meeting_id"),
                        updated["updated_at"],
                        entity_id,
                    ),
                )
            elif entity_type == "roadblock":
                conn.execute(
                    """
                    UPDATE roadblocks SET
                      member_key=?, title=?, details=?, status=?, raised_at=?, resolved_at=?,
                      meeting_id=?, updated_at=?
                    WHERE roadblock_id=?
                    """,
                    (
                        updated.get("member_key"),
                        updated["title"],
                        updated.get("details"),
                        updated["status"],
                        updated["raised_at"],
                        updated.get("resolved_at"),
                        updated.get("meeting_id"),
                        updated["updated_at"],
                        entity_id,
                    ),
                )
            elif entity_type == "document":
                conn.execute(
                    """
                    UPDATE documents SET
                      member_key=?, document_type=?, title=?, period_start=?, period_end=?,
                      visibility=?, body_markdown=?, source_path=?, source_hash=?, updated_at=?
                    WHERE document_id=?
                    """,
                    (
                        updated.get("member_key"),
                        updated["document_type"],
                        updated["title"],
                        updated.get("period_start"),
                        updated.get("period_end"),
                        updated.get("visibility", "private"),
                        updated["body_markdown"],
                        updated.get("source_path"),
                        updated.get("source_hash"),
                        updated["updated_at"],
                        entity_id,
                    ),
                )
            elif entity_type == "feature":
                conn.execute(
                    """
                    UPDATE feature_sets SET
                      member_key=?, title=?, status=?, delivered_at=?, grouping_basis=?,
                      confirmation_state=?, acceptance_gate=?, updated_at=?
                    WHERE feature_id=?
                    """,
                    (
                        updated.get("member_key"),
                        updated["title"],
                        updated["status"],
                        updated.get("delivered_at"),
                        updated["grouping_basis"],
                        updated["confirmation_state"],
                        updated.get("acceptance_gate"),
                        updated["updated_at"],
                        entity_id,
                    ),
                )
            else:
                raise ValueError(f"update not supported for {entity_type}")
            domain_db.audit(conn, entity_type, entity_id, "update", patch)

    def append_note(self, entity_type: str, entity_id: str, text: str) -> str:
        if entity_type == "meeting":
            with self.connect() as conn:
                before = self._meeting_snapshot(conn, entity_id)
            entries = [
                {key: entry[key] for key in ("entry_id", "tag", "body", "visibility")}
                for entry in before["entries"]
            ]
            entry_id = _id("me", entity_id, now_iso(), text)
            entries.append({"entry_id": entry_id, "tag": "note", "body": text, "visibility": "private"})
            self._save_meeting({
                "id": entity_id, "revision": domain_db.revision(before),
                "meeting_date": before["meeting_date"] or date.today().isoformat(),
                "title": before["title"], "state": before["state"], "entries": entries,
            }, create=False)
            return entry_id
        stamp = now_iso()
        with self.connect() as conn:
            if entity_type == "assignment":
                if self._entity_row(conn, "assignment", entity_id) is None:
                    raise ValueError(f"assignment not found: {entity_id}")
                note_id = _id("au", entity_id, stamp, text)
                conn.execute(
                    """
                    INSERT INTO assignment_updates(update_id, assignment_id, author, note, created_at)
                    VALUES(?,?,?,?,?)
                    """,
                    (note_id, entity_id, "team-manager", text, stamp),
                )
                domain_db.audit(conn, "assignment", entity_id, "note", {"note": text})
                return note_id
            raise ValueError(f"note unsupported for entity type: {entity_type}")

    def link_ref(
        self,
        source_type: str,
        source_id: str,
        domain_name: str,
        target_type: str,
        target_id: str,
        relation: str,
    ) -> str:
        stamp = now_iso()
        with self.connect() as conn:
            if self._entity_row(conn, source_type, source_id) is None:
                raise ValueError(f"{source_type} not found: {source_id}")
            ref_id = _id("ref", source_type, source_id, domain_name, target_type, target_id, relation)
            conn.execute(
                """
                INSERT OR REPLACE INTO typed_refs(
                  ref_id, source_type, source_id, domain_name, target_type, target_id,
                  relation, provenance, check_status, last_checked_at, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ref_id,
                    source_type,
                    source_id,
                    domain_name,
                    target_type,
                    target_id,
                    relation,
                    "manual",
                    "unchecked",
                    None,
                    stamp,
                ),
            )
            domain_db.audit(conn, source_type, source_id, "link", {
                "domain": domain_name,
                "target_type": target_type,
                "target_id": target_id,
                "relation": relation,
            })
            return ref_id

    def _scope_bounds(
        self, scope: str, as_of: date, quarter: str | None
    ) -> tuple[str | None, str | None, str]:
        if scope == "total":
            return None, None, "all-recorded"
        if scope == "quarter":
            if quarter:
                m = re.match(r"^(\d{4})-Q([1-4])$", quarter)
                if not m:
                    raise ValueError("quarter must be YYYY-QN")
                year = int(m.group(1))
                q = int(m.group(2))
            else:
                year = as_of.year
                q = ((as_of.month - 1) // 3) + 1
            start_month = (q - 1) * 3 + 1
            start = date(year, start_month, 1)
            end_month = start_month + 2
            if end_month == 12:
                end = date(year + 1, 1, 1)
            else:
                end = date(year, end_month + 1, 1)
            return start.isoformat(), end.isoformat(), f"{year}-Q{q}"
        if scope in {"last-sprint", "current-sprint"}:
            with self.connect() as conn:
                if scope == "current-sprint":
                    row = conn.execute(
                        """SELECT sprint_id, started_at, ended_at FROM sprints
                        WHERE substr(started_at,1,10)<=? AND substr(ended_at,1,10)>?
                        ORDER BY started_at DESC,sprint_id LIMIT 1""",
                        (as_of.isoformat(), as_of.isoformat()),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """SELECT sprint_id, started_at, ended_at FROM sprints
                        WHERE substr(ended_at,1,10)<=? AND started_at IS NOT NULL
                        ORDER BY ended_at DESC,sprint_id LIMIT 1""",
                        (as_of.isoformat(),),
                    ).fetchone()
            if row is None or not row["started_at"] or not row["ended_at"]:
                return None, None, "unknown-sprint-window"
            return row["started_at"][:10], row["ended_at"][:10], row["sprint_id"]
        with self.connect() as conn:
            row = conn.execute(
                """SELECT milestone_id AS id,started_at,ended_at FROM milestones WHERE milestone_id=?
                UNION ALL SELECT sprint_id,started_at,ended_at FROM sprints WHERE sprint_id=?""",
                (scope, scope),
            ).fetchone()
        if row:
            if not row["started_at"] or not row["ended_at"]:
                return None, None, "unknown-sprint-window"
            return row["started_at"][:10], row["ended_at"][:10], row["id"]
        raise ValueError(f"unsupported scope: {scope}")

    def metrics(
        self,
        member_key: str,
        scope: str,
        as_of: date,
        quarter: str | None = None,
    ) -> dict[str, Any]:
        start, end, label = self._scope_bounds(scope, as_of, quarter)
        cutoff = (as_of + timedelta(days=1)).isoformat()
        effective_end = min(end, cutoff) if end else cutoff
        filters = ["member_key=?"]
        args: list[Any] = [member_key]
        feature_filters = ["member_key=?"]
        feature_args: list[Any] = [member_key]
        if start:
            filters.append("completed_at >= ?")
            args.append(start)
            feature_filters.append("delivered_at >= ?")
            feature_args.append(start)
        if effective_end:
            filters.append("completed_at < ?")
            args.append(effective_end)
            feature_filters.append("delivered_at < ?")
            feature_args.append(effective_end)
        where = " AND ".join(filters)
        f_where = " AND ".join(feature_filters)
        with self.connect() as conn:
            features = conn.execute(
                f"""
                SELECT COUNT(*) AS c FROM feature_sets
                WHERE {f_where}
                  AND status='delivered'
                  AND delivered_at IS NOT NULL
                  AND confirmation_state IN ('confirmed', 'accepted')
                """,
                feature_args,
            ).fetchone()["c"]
            bug_count = conn.execute(
                f"""
                SELECT COUNT(DISTINCT issue_key) AS c
                FROM issue_completions
                WHERE {where} AND issue_type='Bug' AND resolution='Done'
                """,
                args,
            ).fetchone()["c"]
            attr_count = conn.execute(
                f"""
                SELECT COUNT(DISTINCT issue_key) AS c
                FROM issue_completions
                WHERE {where} AND issue_type='Attribute' AND resolution='Done'
                """,
                args,
            ).fetchone()["c"]
            story_count = conn.execute(
                f"""
                SELECT COUNT(DISTINCT issue_key) AS c
                FROM issue_completions
                WHERE {where} AND issue_type='Story' AND resolution='Done'
                """,
                args,
            ).fetchone()["c"]
            coverage_rows = conn.execute(
                """
                SELECT COUNT(*) AS imports_count,
                       SUM(CASE WHEN status='imported' THEN 1 ELSE 0 END) AS imported_count
                FROM imports
                """
            ).fetchone()
        coverage = {
            "label": label,
            "window_start": start,
            "window_end": end,
            "end_exclusive": True,
            "as_of": as_of.isoformat(),
            "effective_end": effective_end,
            "window_known": label != "unknown-sprint-window",
            "freshness_at": now_iso(),
            "imports_seen": int(coverage_rows["imports_count"] or 0),
            "imports_imported": int(coverage_rows["imported_count"] or 0),
            "note": "metrics reflect loaded evidence only; unknown coverage is not coerced to zero",
        }
        return {
            "member_key": member_key,
            "scope": scope,
            "coverage": coverage,
            "metrics": {
                "features_delivered": None if label == "unknown-sprint-window" else int(features or 0),
                "bugs_closed": None if label == "unknown-sprint-window" else int(bug_count or 0),
                "attributes_completed": None if label == "unknown-sprint-window" else int(attr_count or 0),
                "stories_completed": None if label == "unknown-sprint-window" else int(story_count or 0),
            },
        }

    def group_prs_by_jira_title(self, prs: list[dict[str, Any]]) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = {}
        ambiguous: list[dict[str, Any]] = []
        without_jira: list[dict[str, Any]] = []
        for pr in prs:
            title = pr.get("pr_title") or pr.get("title") or ""
            keys = sorted(set(PR_JIRA_RE.findall(title)))
            if len(keys) == 1:
                groups.setdefault(keys[0], []).append({**pr, "jira_keys": keys})
            elif len(keys) > 1:
                ambiguous.append({**pr, "jira_keys": keys})
            else:
                without_jira.append({**pr, "jira_keys": []})
        candidates = []
        for jira_key, rows in sorted(groups.items()):
            contributors = sorted({r.get("contributor") for r in rows if r.get("contributor")})
            merged_times = [r.get("merged_at") for r in rows if r.get("merged_at")]
            delivered_at = max(merged_times) if merged_times else None
            candidates.append(
                {
                    "jira_key": jira_key,
                    "pr_count": len(rows),
                    "contributors": contributors,
                    "confirmation_state": "candidate",
                    "grouping_basis": "single-jira-key-in-pr-title",
                    "delivered_at_candidate": delivered_at,
                    "prs": rows,
                }
            )
        return {
            "candidates": candidates,
            "ambiguous_multi_key": ambiguous,
            "without_jira_key": without_jira,
        }

    def payload(self, member_key: str | None, audience: str) -> dict[str, Any]:
        """Legacy nested projection, sharing the portal's privacy policy."""
        projected = self.portal_payload(audience)
        members = []
        for member in projected["members"]:
            if member_key and member["member_key"] != member_key:
                continue
            member = dict(member)
            key = member["member_key"]
            member["meetings"] = [r for r in projected["meetings"] if r["member_key"] == key]
            member["assignments"] = [r for r in projected["assignments"] if r["member_key"] == key]
            if audience == "private":
                member["feedback"] = [r for r in projected["feedback"] if r["member_key"] == key]
            else:
                # Legacy callers did not receive availability; retain that shape.
                member.pop("leave", None)
            members.append(member)
        return {"audience": audience, "members": members, "generated_at": projected["generated_at"]}

    def export_entity(self, entity_type: str, entity_id: str, audience: str) -> str:
        if audience not in {"private", "shared"}:
            raise ValueError("audience must be private or shared")
        with self.connect() as conn:
            row = self._entity_row(conn, entity_type, entity_id)
            if row is None:
                raise ValueError(f"{entity_type} not found: {entity_id}")
            if entity_type == "meeting":
                if audience == "private":
                    return json.dumps(self._meeting_projection(conn, entity_id, audience),
                                      indent=2, ensure_ascii=False) + "\n"
                row = self._meeting_projection(conn, entity_id, audience)
                if row is None:
                    raise ValueError("meeting is private")
                lines = [
                    f"# {row['title']}",
                    "",
                    f"- Date: {row.get('meeting_date') or 'unknown'}",
                ]
                for entry in row["entries"]:
                    lines += ["", f"## {entry['tag']}", entry["body"]]
                return "\n".join(lines).strip() + "\n"
            if audience == "shared":
                if entity_type in {"feedback", "roadblock", "feature", "issue-completion"}:
                    raise ValueError(f"{entity_type} export is private")
                if row.get("visibility") != "shared":
                    raise ValueError(f"{entity_type} is private")
                if entity_type == "assignment":
                    row = self._assignment_projection(conn, entity_id, audience)
                elif entity_type == "document":
                    row = {k: row[k] for k in (
                        "document_id", "member_key", "document_type", "title",
                        "period_start", "period_end", "body_markdown", "visibility",
                    )}
            return json.dumps(row, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    def _record_import(
        self,
        conn: sqlite3.Connection,
        source_path: Path,
        source_hash: str,
        kind: str,
        status: str,
        message: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO imports(import_id, source_path, source_hash, source_kind, status, message, entity_type, entity_id, imported_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                _id("imp", str(source_path), source_hash, status, now_iso()),
                str(source_path),
                source_hash,
                kind,
                status,
                message,
                entity_type,
                entity_id,
                now_iso(),
            ),
        )

    def _find_import(self, conn: sqlite3.Connection, source_path: Path) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT source_hash, status, message
            FROM imports WHERE source_path=?
            ORDER BY imported_at DESC LIMIT 1
            """,
            (str(source_path),),
        ).fetchone()

    def _parse_profile(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        sections = _parse_markdown_sections(text)
        root = sections.get("__root__", [])
        name = None
        for line in root:
            if line.startswith("# "):
                name = line[2:].strip()
                break
        if not name:
            raise ValueError(f"missing profile heading in {path}")
        member_key = path.stem
        fields: dict[str, str] = {}
        for line in root:
            m = FIELDS_RE.match(line)
            if m:
                key = m.group(1).strip().lower()
                value = m.group(2).strip()
                fields[key] = value
        confluence_raw = fields.get("confluence personal space (1:1 host)", "")
        confluence_url = None
        confluence_folder = None
        if "—" in confluence_raw:
            confluence_url, confluence_folder = [p.strip() for p in confluence_raw.split("—", 1)]
        else:
            confluence_url = confluence_raw or None
        notes_text = "\n".join(sections.get("Notes", [])).strip()
        return {
            "member_key": member_key,
            "display_name": name,
            "jira_account_id": fields.get("jira account id", "").strip("`") or None,
            "github_handle": fields.get("github", "").strip("`") or None,
            "confluence_host_url": confluence_url,
            "confluence_folder": confluence_folder,
            "profile_notes": notes_text,
            "main_ip_ownership": None,
            "onboarding_date": None,
            "birthday_month": None,
            "birthday_day": None,
            "raw_markdown": text,
        }

    def _history_dates_from_profile(self, text: str) -> set[str]:
        sections = _parse_markdown_sections(text)
        history_lines = sections.get("1:1 History", [])
        dates: set[str] = set()
        for line in history_lines:
            m = HISTORY_HEADING_RE.match(line)
            if m:
                dates.add(m.group(1))
        return dates

    def _parse_actions(self, markdown: str) -> list[dict[str, Any]]:
        in_actions = False
        actions: list[dict[str, Any]] = []
        for raw in markdown.splitlines():
            line = raw.strip()
            if line.startswith("## Action items"):
                in_actions = True
                continue
            if in_actions and line.startswith("## "):
                break
            if in_actions:
                m = ACTION_ROW_RE.match(line)
                if not m:
                    continue
                if m.group(1) == "---":
                    continue
                if m.group(2).lower() == "action":
                    continue
                actions.append(
                    {
                        "action": m.group(2),
                        "owner": m.group(3),
                        "due": None if m.group(4) in {"—", "-", ""} else m.group(4),
                        "status": m.group(5),
                        "jira": None if m.group(6) in {"—", "-", ""} else m.group(6),
                    }
                )
        return actions

    def _parse_summary(self, markdown: str) -> tuple[str | None, str | None]:
        shared = None
        private = None
        sections = _parse_markdown_sections(markdown)
        summary = "\n".join(sections.get("Summary", [])).strip()
        if summary:
            shared = summary
        private_lines = []
        for heading, lines in sections.items():
            normalized = heading.lower()
            if not normalized.startswith(("feedback (private", "private notes (private")):
                continue
            if "only)" not in normalized:
                continue
            private_lines.extend(lines)
        private = "\n".join(private_lines).strip() or None
        return shared, private

    def _meeting_date_from_path(self, path: Path) -> str | None:
        m = ARCHIVE_FILE_RE.match(path.name)
        return m.group("date") if m else None

    def _record_meeting(
        self,
        conn: sqlite3.Connection,
        member_key: str,
        path: Path,
        kind: str,
        text: str,
        source_hash: str,
    ) -> tuple[str, int, int]:
        meeting_date = self._meeting_date_from_path(path)
        meeting_id = _id("mtg", member_key, path.name, source_hash)
        shared, private = self._parse_summary(text)
        conn.execute(
            """
            INSERT OR REPLACE INTO meetings(
              meeting_id, member_key, meeting_date, title, session_type, state,
              source_kind, source_path, source_hash, shared_summary, private_summary,
              full_markdown, finalized, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                meeting_id,
                member_key,
                meeting_date,
                f"1:1 with {member_key} — {meeting_date or path.name}",
                "one_on_one",
                "finalized" if kind == "archive" else "working",
                kind,
                str(path),
                source_hash,
                shared,
                private,
                text,
                1 if kind == "archive" else 0,
                now_iso(),
                now_iso(),
            ),
        )
        conn.execute("DELETE FROM meeting_entries WHERE meeting_id=?", (meeting_id,))
        decisions = 0
        actions_created = 0
        sections = _parse_markdown_sections(text)
        for line in sections.get("Decisions", []):
            clean = line.strip()
            if clean.startswith("- "):
                decisions += 1
                entry_id = _id("me", meeting_id, "decision", clean)
                conn.execute(
                    """
                    INSERT INTO meeting_entries(entry_id, meeting_id, tag, body, visibility, actor, created_at, source_line)
                    VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (entry_id, meeting_id, "decision", clean[2:].strip(), "private", None, now_iso(), None),
                )
        for action in self._parse_actions(text):
            assignment_id = _id("asg", member_key, action["owner"], action["action"])
            conn.execute(
                """
                INSERT OR IGNORE INTO assignments(
                  assignment_id, member_key, owner_label, title, description, kind,
                  status, outcome, target_date, jira_key, origin_meeting_id, visibility,
                  created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    assignment_id,
                    member_key,
                    action["owner"],
                    action["action"][:180],
                    action["action"],
                    "simple-task",
                    _normalize_assignment_status(action["status"]),
                    None,
                    action["due"],
                    _extract_first_jira(action["jira"]),
                    meeting_id,
                    "private",
                    now_iso(),
                    now_iso(),
                ),
            )
            actions_created += 1
        return meeting_id, decisions, actions_created

    def migrate(self, source_root: Path, dry_run: bool = False) -> dict[str, Any]:
        source_root = Path(source_root)
        if not source_root.exists():
            raise ValueError(f"source not found: {source_root}")
        summary = {
            "members_imported": 0,
            "meetings_imported": 0,
            "working_docs_imported": 0,
            "assignments_detected": 0,
            "decisions_detected": 0,
            "leave_imported": 0,
            "archives_found": 0,
            "history_index_mismatches": [],
            "conflicts": [],
            "skipped_unchanged": 0,
        }
        with self.connect() as conn:
            member_files = sorted(
                p for p in source_root.glob("*.md") if MEMBER_FILE_RE.match(p.name)
            )
            for member_file in member_files:
                profile = self._parse_profile(member_file)
                text = profile.pop("raw_markdown")
                source_hash = _hash_text(text)
                existing = self._find_import(conn, member_file)
                if existing and existing["source_hash"] == source_hash and existing["status"] == "imported":
                    summary["skipped_unchanged"] += 1
                    continue
                if existing and existing["source_hash"] != source_hash and existing["status"] == "imported":
                    summary["conflicts"].append(str(member_file))
                    self._record_import(
                        conn,
                        member_file,
                        source_hash,
                        "member-profile",
                        "conflict",
                        "source changed after prior import",
                    )
                    continue
                if not dry_run:
                    domain_db.backup_legacy(member_file, self.db_path)
                    self._upsert_member(conn, profile)
                summary["members_imported"] += 1
                self._record_import(
                    conn,
                    member_file,
                    source_hash,
                    "member-profile",
                    "imported" if not dry_run else "dry-run",
                    "profile imported",
                    "member",
                    profile["member_key"],
                )
                profile_dates = self._history_dates_from_profile(text)
                member_dir = source_root / profile["member_key"]
                archive_dates: set[str] = set()
                if member_dir.exists():
                    archive_files = sorted((member_dir / "1on1").glob("*.md")) if (member_dir / "1on1").exists() else []
                    summary["archives_found"] += len(archive_files)
                    for archive in archive_files:
                        archive_text = archive.read_text(encoding="utf-8")
                        archive_hash = _hash_text(archive_text)
                        existing_archive = self._find_import(conn, archive)
                        if existing_archive and existing_archive["source_hash"] == archive_hash and existing_archive["status"] == "imported":
                            summary["skipped_unchanged"] += 1
                            archive_dates.add(self._meeting_date_from_path(archive) or archive.name)
                            continue
                        if existing_archive and existing_archive["source_hash"] != archive_hash and existing_archive["status"] == "imported":
                            summary["conflicts"].append(str(archive))
                            self._record_import(
                                conn,
                                archive,
                                archive_hash,
                                "meeting-archive",
                                "conflict",
                                "archive changed after prior import",
                            )
                            continue
                        if not dry_run:
                            domain_db.backup_legacy(archive, self.db_path)
                            _meeting_id, decisions, actions = self._record_meeting(
                                conn, profile["member_key"], archive, "archive", archive_text, archive_hash
                            )
                            summary["decisions_detected"] += decisions
                            summary["assignments_detected"] += actions
                        summary["meetings_imported"] += 1
                        archive_dates.add(self._meeting_date_from_path(archive) or archive.name)
                        self._record_import(
                            conn,
                            archive,
                            archive_hash,
                            "meeting-archive",
                            "imported" if not dry_run else "dry-run",
                            "archive imported",
                            "meeting",
                            _id("mtg", profile["member_key"], archive.name, archive_hash),
                        )
                    for working_name in ("_next.md", "_last.md", "running.md"):
                        working_path = member_dir / working_name
                        if not working_path.exists():
                            continue
                        working_text = working_path.read_text(encoding="utf-8")
                        working_hash = _hash_text(working_text)
                        existing_working = self._find_import(conn, working_path)
                        if existing_working and existing_working["source_hash"] == working_hash and existing_working["status"] == "imported":
                            summary["skipped_unchanged"] += 1
                            continue
                        if existing_working and existing_working["source_hash"] != working_hash and existing_working["status"] == "imported":
                            summary["conflicts"].append(str(working_path))
                            self._record_import(
                                conn,
                                working_path,
                                working_hash,
                                "meeting-working",
                                "conflict",
                                "working file changed after prior import",
                            )
                            continue
                        if not dry_run:
                            domain_db.backup_legacy(working_path, self.db_path)
                            self._record_meeting(
                                conn, profile["member_key"], working_path, "working", working_text, working_hash
                            )
                        summary["working_docs_imported"] += 1
                        self._record_import(
                            conn,
                            working_path,
                            working_hash,
                            "meeting-working",
                            "imported" if not dry_run else "dry-run",
                            "working doc imported",
                            "meeting",
                            _id("mtg", profile["member_key"], working_path.name, working_hash),
                        )
                mismatch = sorted(profile_dates.symmetric_difference(archive_dates))
                if mismatch:
                    summary["history_index_mismatches"].append(
                        {
                            "member": profile["member_key"],
                            "profile_dates": sorted(profile_dates),
                            "archive_dates": sorted(archive_dates),
                            "difference": mismatch,
                        }
                    )
            domain_db.audit(conn, "team-store", "", "migrate", summary)
        return summary


def _extract_first_jira(text: str | None) -> str | None:
    if not text:
        return None
    m = JIRA_KEY_RE.search(text)
    return m.group(1) if m else None


def _normalize_assignment_status(raw: str) -> str:
    value = raw.lower().strip()
    mapping = {
        "open": "assigned",
        "in progress": "in-progress",
        "done": "done",
        "closed": "done",
        "cancelled": "cancelled",
        "blocked": "blocked",
    }
    return mapping.get(value, "assigned")
