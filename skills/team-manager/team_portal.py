"""Owner-side Team projections and validated portal mutations.

This module is a TeamStore mixin, not a web server. Callers must authorize
private access before selecting that audience or invoking a mutation.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
import re
from typing import Any
from uuid import uuid4

import domain_db

TASK_CATEGORIES = ("development", "debug", "research", "planning", "testing", "code-review", "track", "misc")
LOG_TYPES = {"one_on_one": "1:1", "conversation": "Conversation", "meeting": "Meeting", "note": "Note"}
ASSIGNMENT_STATUSES = (
    "assigned", "open", "todo", "backlog", "in-progress", "blocked", "done", "cancelled", "deferred",
)
# The lead's own log records 1:1s with their manager, so "member" reads as manager there.
MANAGER_NAME = os.environ.get("TEAM_MANAGER_NAME", "Manager")
PARTY = ["me", "member"]

# Typed log items. Served to the portal so the form is generated from one spec.
# Each field: key, label, input (text|textarea|date|select|checkbox|task), required, options.
LOG_ITEM_TYPES: dict[str, dict[str, Any]] = {
    "action": {"label": "Action item", "summary": "title", "fields": [
        {"key": "title", "label": "Action", "input": "text", "required": True},
        {"key": "owner", "label": "Owner", "input": "select", "options": PARTY},
        {"key": "target_date", "label": "Due", "input": "date"},
        {"key": "category", "label": "Category", "input": "select", "options": list(TASK_CATEGORIES)},
        {"key": "priority", "label": "Priority", "input": "select",
         "options": ["medium", "high", "critical", "low"]},
        {"key": "jira_key", "label": "Jira", "input": "text"},
        {"key": "description", "label": "Details", "input": "textarea"},
    ]},
    "decision": {"label": "Decision", "summary": "decision", "fields": [
        {"key": "decision", "label": "Decision", "input": "text", "required": True},
        {"key": "rationale", "label": "Rationale", "input": "textarea"},
        {"key": "decided_by", "label": "Decided by", "input": "select", "options": ["joint", "me", "member"]},
        {"key": "revisit_date", "label": "Revisit by", "input": "date"},
    ]},
    "discussion": {"label": "Discussion point", "summary": "topic", "fields": [
        {"key": "topic", "label": "Topic", "input": "text", "required": True},
        {"key": "raised_by", "label": "Raised by", "input": "select", "options": PARTY},
        {"key": "outcome", "label": "Outcome", "input": "textarea"},
        {"key": "follow_up", "label": "Follow-up needed", "input": "checkbox"},
    ]},
    "status": {"label": "Status update", "summary": "note", "fields": [
        {"key": "assignment_id", "label": "Task", "input": "task", "required": True},
        {"key": "status", "label": "New status", "input": "select", "options": [""] + list(ASSIGNMENT_STATUSES)},
        {"key": "note", "label": "Progress", "input": "textarea", "required": True},
    ]},
    "roadblock": {"label": "Roadblock", "summary": "blocker", "fields": [
        {"key": "blocker", "label": "Blocker", "input": "text", "required": True},
        {"key": "impact", "label": "Impact", "input": "textarea"},
        {"key": "unblocker", "label": "Who unblocks", "input": "select", "options": ["me", "member", "other"]},
        {"key": "unblocker_name", "label": "Name (if other)", "input": "text"},
        {"key": "status", "label": "Status", "input": "select", "options": ["open", "resolved"]},
    ]},
    "feedback_given": {"label": "Feedback given", "summary": "behavior", "default_visibility": "private", "fields": [
        {"key": "polarity", "label": "Kind", "input": "select", "options": ["positive", "constructive"]},
        {"key": "situation", "label": "Situation", "input": "text"},
        {"key": "behavior", "label": "Behavior", "input": "textarea", "required": True},
        {"key": "impact", "label": "Impact", "input": "textarea"},
    ]},
    "feedback_received": {"label": "Feedback received", "summary": "feedback", "default_visibility": "private",
                          "fields": [
        {"key": "feedback", "label": "Feedback", "input": "textarea", "required": True},
        {"key": "follow_up", "label": "My follow-up", "input": "textarea"},
    ]},
    "growth": {"label": "Growth / career", "summary": "goal", "default_visibility": "private", "fields": [
        {"key": "goal", "label": "Goal / aspiration", "input": "text", "required": True},
        {"key": "next_step", "label": "Next step", "input": "text"},
        {"key": "horizon", "label": "Horizon", "input": "text"},
    ]},
    "personal": {"label": "Personal", "summary": "text", "always_private": True, "fields": [
        {"key": "text", "label": "Note", "input": "textarea", "required": True},
    ]},
}


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _visibility(value: Any) -> str:
    if value not in {"private", "shared"}:
        raise ValueError("visibility must be private or shared")
    return value


def _text(value: Any, field: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{field} must be {'text' if empty else 'nonempty text'}")
    return value


def _day(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date")
    date.fromisoformat(value)
    if len(value) != 10:
        raise ValueError(f"{field} must be YYYY-MM-DD")
    return value


def _keys(data: dict, allowed: set[str], required: set[str] = frozenset()) -> None:
    unknown = set(data) - allowed
    missing = required - set(data)
    if unknown or missing:
        raise ValueError(f"invalid fields: unknown={sorted(unknown)}, missing={sorted(missing)}")


def _item_fields(tag: str, raw: Any) -> dict[str, Any]:
    """Validate one typed log item's fields against LOG_ITEM_TYPES; returns cleaned fields."""
    spec = LOG_ITEM_TYPES[tag]
    if not isinstance(raw, dict):
        raise ValueError(f"{tag} item fields must be an object")
    known = {f["key"]: f for f in spec["fields"]}
    unknown = set(raw) - set(known)
    if unknown:
        raise ValueError(f"unknown {tag} fields: {sorted(unknown)}")
    out: dict[str, Any] = {}
    for key, field in known.items():
        value = raw.get(key)
        if field["input"] == "checkbox":
            out[key] = bool(value)
            continue
        value = "" if value is None else value
        if not isinstance(value, str):
            raise ValueError(f"{tag}.{key} must be text")
        value = value.strip()
        if field.get("required") and not value:
            raise ValueError(f"{spec['label']}: {field['label']} is required")
        if value and field["input"] == "date":
            _day(value, f"{tag}.{key}")
        if value and field["input"] == "select" and value not in field["options"]:
            raise ValueError(f"invalid {tag}.{key}")
        if value:
            out[key] = value
    if tag == "action":
        if out.get("jira_key") and not re.fullmatch(r"[A-Z][A-Z0-9]{1,15}-\d+", out["jira_key"]):
            raise ValueError("invalid jira_key")
    return out


def _item_summary(tag: str, fields: dict[str, Any]) -> str:
    text = str(fields.get(LOG_ITEM_TYPES[tag]["summary"], ""))
    return text.splitlines()[0][:240] if text else ""


def _entry_fields(entry: dict) -> dict[str, Any]:
    if entry.get("fields_json"):
        return json.loads(entry["fields_json"])
    if entry["tag"] == "decision":
        return {"decision": entry["body"]}
    return {}


class TeamPortalMixin:
    def _meeting_snapshot(self, conn, ident: str) -> dict[str, Any]:
        row = self._entity_row(conn, "meeting", ident)
        if row is None:
            raise ValueError(f"meeting not found: {ident}")
        row["entries"] = [dict(r) for r in conn.execute(
            "SELECT * FROM meeting_entries WHERE meeting_id=? ORDER BY position,created_at,entry_id",
            (ident,),
        )]
        return row

    def _remember_meeting(self, conn, snapshot: dict) -> str:
        revision = domain_db.revision(snapshot)
        conn.execute(
            "INSERT OR IGNORE INTO meeting_revisions VALUES(?,?,?,?)",
            (snapshot["meeting_id"], revision, json.dumps(snapshot, ensure_ascii=False), _stamp()),
        )
        return revision

    def _meeting_projection(self, conn, ident: str, audience: str) -> dict | None:
        row = self._meeting_snapshot(conn, ident)
        revision = domain_db.revision(row)
        for e in row["entries"]:
            e["fields"] = _entry_fields(e)
            if e.get("record_type") == "roadblock" and e.get("record_id"):
                ref = conn.execute(
                    """SELECT target_id FROM typed_refs WHERE source_type='roadblock' AND source_id=?
                    AND domain_name='raid' ORDER BY created_at DESC LIMIT 1""", (e["record_id"],),
                ).fetchone()
                if ref:
                    e["raid_id"] = ref[0]
        if audience == "private":
            row["revision"] = revision
            row["history"] = [
                {"revision": r["revision"], "recorded_at": r["recorded_at"],
                 "snapshot": json.loads(r["snapshot_json"])}
                for r in conn.execute(
                    "SELECT * FROM meeting_revisions WHERE meeting_id=? ORDER BY recorded_at,revision",
                    (ident,),
                )
            ]
            return row
        entries = [
            {k: e.get(k) for k in ("entry_id", "tag", "body", "visibility", "position", "fields", "raid_id")}
            for e in row["entries"] if e["visibility"] == "shared"
        ]
        if not entries:
            return None
        return {
            "meeting_id": ident, "member_key": row["member_key"],
            "session_type": row["session_type"],
            "meeting_date": row["meeting_date"], "state": row["state"],
            "finalized": bool(row["finalized"]), "entries": entries,
            "title": row["title"] if entries and row["title_visibility"] == "shared" else "1:1 session",
        }

    def _assignment_projection(self, conn, ident: str, audience: str) -> dict | None:
        row = self._entity_row(conn, "assignment", ident)
        if row is None:
            raise ValueError(f"assignment not found: {ident}")
        if audience == "shared" and row["visibility"] != "shared":
            return None
        updates = [dict(r) for r in conn.execute(
            "SELECT * FROM assignment_updates WHERE assignment_id=? ORDER BY created_at,update_id",
            (ident,),
        )]
        if audience == "private":
            row["revision"] = domain_db.revision(row)
            row["updates"] = updates
        else:
            row = {k: row[k] for k in (
                "assignment_id", "member_key", "owner_label", "title", "description",
                "kind", "status", "outcome", "target_date", "jira_key", "visibility",
                "category", "priority", "created_at",
            )}
            row["updates"] = [
                {k: r[k] for k in ("update_id", "note", "created_at", "visibility")}
                for r in updates if r["visibility"] == "shared"
            ]
        row["refs"] = [dict(r) for r in conn.execute(
            """SELECT domain_name,target_type,target_id,relation FROM typed_refs
            WHERE source_type='assignment' AND source_id=? ORDER BY created_at,ref_id""", (ident,)
        )]
        return row

    def calendar(self, audience: str = "private") -> dict:
        _visibility(audience)
        with self.connect() as conn:
            milestones = [dict(r) for r in conn.execute(
                "SELECT milestone_id,name,started_at,ended_at FROM milestones ORDER BY started_at,milestone_id"
            )]
            sprints = [dict(r) for r in conn.execute(
                "SELECT sprint_id,milestone_id,name,started_at,ended_at FROM sprints ORDER BY started_at,sprint_id"
            )]
            result = {"milestones": milestones, "sprints": sprints}
            if audience == "private":
                result["revision"] = domain_db.revision(result)
            baselines = []
            for r in conn.execute("SELECT * FROM commitment_baselines ORDER BY baseline_id"):
                b = dict(r)
                if audience == "shared" and b["visibility"] != "shared":
                    continue
                if audience == "private":
                    b["revision"] = domain_db.revision(b)
                b["work_refs"] = json.loads(b.pop("work_refs_json"))
                baselines.append(b)
            result["baselines"] = baselines
            # Overlaps are intentional (MS9 and Q1), never collapsed or inferred.
            result["overlaps"] = [
                {"milestone_ids": [a["milestone_id"], b["milestone_id"]],
                 "started_at": max(a["started_at"], b["started_at"]),
                 "ended_at": min(a["ended_at"], b["ended_at"])}
                for i, a in enumerate(milestones) for b in milestones[i + 1:]
                if max(a["started_at"], b["started_at"]) < min(a["ended_at"], b["ended_at"])
            ]
            return result

    def portal_payload(self, audience: str = "shared", as_of: date | str | None = None) -> dict:
        _visibility(audience)
        when = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of or date.today()
        if not isinstance(when, date):
            raise ValueError("as_of must be a date or YYYY-MM-DD")
        from team_ingest import leave_stats
        calendar = self.calendar(audience)
        result = {
            "audience": audience, "as_of": when.isoformat(), "generated_at": _stamp(),
            "members": [], "meetings": [], "assignments": [], "feedback": [],
            "calendar": calendar, "metrics": {},
            "task_categories": list(TASK_CATEGORIES), "log_types": LOG_TYPES,
            "log_item_types": LOG_ITEM_TYPES, "manager_name": MANAGER_NAME,
        }
        with self.connect() as conn:
            for row in conn.execute("SELECT * FROM members ORDER BY display_name,member_key"):
                member = dict(row)
                key = member["member_key"]
                if audience == "private":
                    member["revision"] = domain_db.revision(member)
                else:
                    safe = (
                        "member_key", "display_name", "jira_account_id", "github_handle",
                        "main_ip_ownership", "roster_role", "role_title", "employment_type",
                        "base_country", "onboarding_date",
                    )
                    member = {k: member[k] for k in safe}
                    if row["profile_visibility"] == "shared":
                        member["profile_notes"] = row["profile_notes"]
                    if row["goals_visibility"] == "shared":
                        member["goals"] = row["goals"]
                    if row["focus_visibility"] == "shared":
                        member["focus"] = row["focus"]
                leave = leave_stats(self, key, when)
                if audience == "private":
                    member["leave"] = leave
                else:
                    member["leave"] = {
                        "known": leave["hr_last_import_at"] is not None,
                        **{field: (
                            {k: leave[field][k] for k in ("start_date", "end_date")}
                            if leave[field] else None
                        ) for field in ("current_leave", "next_approved_leave", "last_approved_leave")},
                    }
                result["members"].append(member)
                scopes = ["total", "quarter", "last-sprint", "current-sprint"]
                scopes += [m["milestone_id"] for m in calendar["milestones"]]
                result["metrics"][key] = {scope: self.metrics(key, scope, when) for scope in scopes}
            for row in conn.execute("SELECT meeting_id FROM meetings ORDER BY meeting_date DESC,meeting_id"):
                projected = self._meeting_projection(conn, row[0], audience)
                if projected is not None:
                    result["meetings"].append(projected)
            for row in conn.execute("SELECT assignment_id FROM assignments ORDER BY updated_at DESC,assignment_id"):
                projected = self._assignment_projection(conn, row[0], audience)
                if projected is not None:
                    result["assignments"].append(projected)
            if audience == "private":
                result["feedback"] = self.list_entities("feedback", None)
        return result

    def _save_meeting(self, data: dict, *, create: bool) -> dict:
        allowed = {
            "id", "revision", "member_key", "meeting_date", "title", "state",
            "entries", "visibility", "title_visibility", "action_items", "session_type",
        }
        required = {"member_key", "entries"} if create else {
            "id", "revision", "meeting_date", "title", "entries",
        }
        _keys(data, allowed, required)
        data = dict(data)
        data["meeting_date"] = data.get("meeting_date") or date.today().isoformat()
        _day(data["meeting_date"], "meeting_date")
        if data.get("title"):
            _text(data["title"], "title")
        if "session_type" in data and data["session_type"] not in LOG_TYPES:
            raise ValueError("invalid log type")
        actions = data.get("action_items", [])
        if not isinstance(actions, list):
            raise ValueError("action_items must be a list")
        for item in actions:
            if not isinstance(item, dict):
                raise ValueError("each action item must be an object")
            _keys(item, {"title", "target_date", "visibility", "description", "jira_key",
                         "category", "priority", "refs"}, {"title"})
            self._validate_assignment(item)
            if "description" in item:
                _text(item["description"], "description", empty=True)
        state = data.get("state", "working")
        if state not in {"prep", "working", "live", "finalized", "amended"}:
            raise ValueError("invalid meeting state")
        for field in ("visibility", "title_visibility"):
            if field in data:
                _visibility(data[field])
        if not isinstance(data["entries"], list):
            raise ValueError("entries must be a list")
        entries, ids = [], set()
        for pos, raw in enumerate(data["entries"]):
            if not isinstance(raw, dict):
                raise ValueError("each entry must be an object")
            _keys(raw, {"entry_id", "id", "tag", "body", "visibility", "fields"}, {"tag"})
            if "id" in raw and "entry_id" in raw and raw["id"] != raw["entry_id"]:
                raise ValueError("entry id aliases disagree")
            ident = raw.get("entry_id") or raw.get("id") or f"me-{uuid4().hex}"
            _text(ident, "entry_id")
            if ident in ids:
                raise ValueError("duplicate entry_id")
            ids.add(ident)
            tag = _text(raw["tag"], "tag")
            visibility = _visibility(raw.get("visibility", "private"))
            if tag in LOG_ITEM_TYPES and "fields" in raw:
                fields = _item_fields(tag, raw["fields"])
                body = _item_summary(tag, fields)
                if LOG_ITEM_TYPES[tag].get("always_private"):
                    visibility = "private"
            else:
                if "body" not in raw:
                    raise ValueError("missing required fields: ['body']")
                fields, body = None, _text(raw["body"], "body", empty=True)
            entries.append({
                "entry_id": ident, "tag": tag, "body": body, "fields": fields,
                "visibility": visibility, "position": pos,
            })
        ident = data.get("id") if not create else f"mtg-{uuid4().hex}"
        stamp = _stamp()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if create:
                if "revision" in data or "id" in data:
                    raise ValueError("new meetings do not accept id or revision")
                member = conn.execute("SELECT * FROM members WHERE member_key=?", (data["member_key"],)).fetchone()
                if not member:
                    raise ValueError("member not found")
                session_type = data.get("session_type", "one_on_one")
                counterpart = member["display_name"]
                if session_type == "one_on_one" and member["roster_role"] == "lead":
                    counterpart = MANAGER_NAME
                data["title"] = (data.get("title") or "").strip() or (
                    f"{LOG_TYPES[session_type]} with {counterpart} - {data['meeting_date']}"
                )
                conn.execute(
                    """INSERT INTO meetings(
                      meeting_id,member_key,meeting_date,title,session_type,state,source_kind,
                      source_path,source_hash,full_markdown,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,'local','','','',?,?)""",
                    (ident, data["member_key"], data["meeting_date"], data["title"],
                     session_type, state, stamp, stamp),
                )
            else:
                before = self._meeting_snapshot(conn, ident)
                if domain_db.revision(before) != data["revision"]:
                    raise ValueError("stale revision; re-read meeting before saving")
                if data.get("member_key", before["member_key"]) != before["member_key"]:
                    raise ValueError("meeting member cannot change")
                session_type = data.get("session_type", before["session_type"])
                data["title"] = (data.get("title") or "").strip() or before["title"]
                if "state" not in data:
                    state = before["state"]
                if before["finalized"] and state not in {"finalized", "amended"}:
                    raise ValueError("finalized sessions must be amended, not reopened")
                self._remember_meeting(conn, before)
                if before["finalized"]:
                    state = "amended"
            for e in entries:
                other = conn.execute(
                    "SELECT meeting_id FROM meeting_entries WHERE entry_id=?", (e["entry_id"],)
                ).fetchone()
                if other and other[0] != ident:
                    raise ValueError("entry belongs to another meeting")
            updates = {
                "meeting_date": data["meeting_date"], "title": data["title"], "state": state,
                "session_type": session_type,
                "finalized": (
                    before["finalized"] if not create and "state" not in data
                    else int(state in {"finalized", "amended"})
                ), "updated_at": stamp,
            }
            for field in ("visibility", "title_visibility"):
                if field in data:
                    updates[field] = data[field]
            conn.execute(
                f"UPDATE meetings SET {','.join(k+'=?' for k in updates)} WHERE meeting_id=?",
                (*updates.values(), ident),
            )
            previous = {r["entry_id"]: dict(r) for r in conn.execute(
                "SELECT * FROM meeting_entries WHERE meeting_id=?", (ident,))}
            meeting = dict(conn.execute("SELECT * FROM meetings WHERE meeting_id=?", (ident,)).fetchone())
            for e in entries:
                if e["fields"] is not None:
                    self._sync_item(conn, meeting, e, previous.get(e["entry_id"]), stamp)
                elif previous.get(e["entry_id"]):
                    old_entry = previous[e["entry_id"]]
                    e["record_type"], e["record_id"] = old_entry["record_type"], old_entry["record_id"]
            kept = {e["entry_id"] for e in entries}
            for old_entry in previous.values():
                if old_entry["entry_id"] not in kept:
                    self._drop_item_record(conn, old_entry)
            conn.execute("DELETE FROM meeting_entries WHERE meeting_id=?", (ident,))
            for e in entries:
                conn.execute(
                    """INSERT INTO meeting_entries(
                      entry_id,meeting_id,tag,body,visibility,actor,created_at,position,
                      fields_json,record_type,record_id
                    ) VALUES(?,?,?,?,?,'team-manager',?,?,?,?,?)""",
                    (e["entry_id"], ident, e["tag"], e["body"], e["visibility"], stamp, e["position"],
                     json.dumps(e["fields"], ensure_ascii=False) if e["fields"] is not None else None,
                     e.get("record_type"), e.get("record_id")),
                )
            snapshot = self._meeting_snapshot(conn, ident)
            for item in actions:
                assignment_id = f"as-{uuid4().hex}"
                conn.execute(
                    """INSERT INTO assignments(
                    assignment_id,member_key,owner_label,title,description,kind,status,
                    target_date,jira_key,origin_meeting_id,visibility,created_at,updated_at,category,priority)
                    VALUES(?,?,?,?,?,'action-item','todo',?,?,?,?,?,?,?,?)""",
                    (assignment_id, snapshot["member_key"], snapshot["member_key"], item["title"],
                     item.get("description"), item.get("target_date") or None, item.get("jira_key"),
                     ident, item.get("visibility", "private"), stamp, stamp,
                     item.get("category", "misc"), item.get("priority", "medium")),
                )
                self._save_task_refs(conn, assignment_id, item.get("refs", []))
                domain_db.audit(conn, "assignment", assignment_id, "create",
                                {"origin_meeting_id": ident})
            self._remember_meeting(conn, snapshot)
            domain_db.audit(conn, "meeting", ident, "create" if create else "save",
                            {"state": state, "entries": len(entries)})
            return self._meeting_projection(conn, ident, "private")

    def _lead_key(self, conn) -> str | None:
        row = conn.execute(
            "SELECT member_key FROM members WHERE roster_role='lead' ORDER BY member_key LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def _sync_item(self, conn, meeting: dict, e: dict, prev: dict | None, stamp: str) -> None:
        """Create or update the record a typed log item owns; links it back onto the entry."""
        tag, f = e["tag"], e["fields"]
        if prev and prev.get("record_id") and prev.get("tag") == tag:
            e["record_type"], e["record_id"] = prev["record_type"], prev["record_id"]
        member, day = meeting["member_key"], meeting["meeting_date"]
        lead = self._lead_key(conn)
        if tag == "action":
            if e.get("record_id"):
                # Task is edited in Tasks after creation; the log keeps what was agreed.
                if prev and prev.get("fields_json"):
                    e["fields"] = json.loads(prev["fields_json"])
                    e["body"] = _item_summary(tag, e["fields"])
                return
            owner = f.get("owner", "member")
            owner_key, owner_label = member, member
            if owner == "me" and lead:
                owner_key = owner_label = lead
            elif member == lead:
                owner_label = "manager"
            asg = f"as-{uuid4().hex}"
            conn.execute(
                """INSERT INTO assignments(
                assignment_id,member_key,owner_label,title,description,kind,status,
                target_date,jira_key,origin_meeting_id,visibility,created_at,updated_at,category,priority)
                VALUES(?,?,?,?,?,'action-item','todo',?,?,?,?,?,?,?,?)""",
                (asg, owner_key, owner_label, f["title"], f.get("description"), f.get("target_date"),
                 f.get("jira_key"), meeting["meeting_id"], e["visibility"], stamp, stamp,
                 f.get("category", "misc"), f.get("priority", "medium")),
            )
            domain_db.audit(conn, "assignment", asg, "create", {"origin_meeting_id": meeting["meeting_id"]})
            e["record_type"], e["record_id"] = "assignment", asg
        elif tag == "status":
            task = conn.execute(
                "SELECT assignment_id,title,status FROM assignments WHERE assignment_id=?", (f["assignment_id"],)
            ).fetchone()
            if task is None:
                raise ValueError("status update: task not found")
            if e.get("record_id") and prev and json.loads(prev.get("fields_json") or "{}").get(
                    "assignment_id") == f["assignment_id"]:
                conn.execute("UPDATE assignment_updates SET note=?,visibility=? WHERE update_id=?",
                             (f["note"], e["visibility"], e["record_id"]))
            else:
                if e.get("record_id"):
                    conn.execute("DELETE FROM assignment_updates WHERE update_id=?", (e["record_id"],))
                upd = f"au-{uuid4().hex}"
                conn.execute(
                    """INSERT INTO assignment_updates(update_id,assignment_id,author,note,visibility,created_at)
                    VALUES(?,?,'team-manager',?,?,?)""",
                    (upd, f["assignment_id"], f["note"], e["visibility"], stamp),
                )
                e["record_type"], e["record_id"] = "assignment_update", upd
            if f.get("status") and f["status"] != task["status"]:
                conn.execute("UPDATE assignments SET status=?,updated_at=? WHERE assignment_id=?",
                             (f["status"], stamp, f["assignment_id"]))
                domain_db.audit(conn, "assignment", f["assignment_id"], "status",
                                {"status": f["status"], "meeting_id": meeting["meeting_id"]})
            e["body"] = f"{task['title']}: {_item_summary(tag, f)}"
        elif tag == "roadblock":
            unblocker = f.get("unblocker", "")
            if unblocker == "other":
                unblocker = f.get("unblocker_name") or "other"
            status = f.get("status", "open")
            values = (f["blocker"], f.get("impact"), unblocker or None, status,
                      day if status == "resolved" else None, e["visibility"], stamp)
            if e.get("record_id"):
                conn.execute(
                    """UPDATE roadblocks SET title=?,impact=?,unblocker=?,status=?,
                    resolved_at=COALESCE(resolved_at,?),visibility=?,updated_at=? WHERE roadblock_id=?""",
                    (*values, e["record_id"]))
                if status != "resolved":
                    conn.execute("UPDATE roadblocks SET resolved_at=NULL WHERE roadblock_id=?", (e["record_id"],))
            else:
                rb = f"rb-{uuid4().hex}"
                conn.execute(
                    """INSERT INTO roadblocks(roadblock_id,member_key,title,impact,unblocker,status,resolved_at,
                    visibility,updated_at,raised_at,meeting_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rb, member, *values, day, meeting["meeting_id"], stamp))
                domain_db.audit(conn, "roadblock", rb, "create", {"meeting_id": meeting["meeting_id"]})
                e["record_type"], e["record_id"] = "roadblock", rb
        elif tag in {"feedback_given", "feedback_received"}:
            given = tag == "feedback_given"
            if given:
                kind = f"given-{f.get('polarity', 'positive')}"
                body = "\n".join(f"{k.capitalize()}: {f[k]}" for k in ("situation", "behavior", "impact") if f.get(k))
                source = lead
            else:
                kind = "received"
                body = f["feedback"] + (f"\nFollow-up: {f['follow_up']}" if f.get("follow_up") else "")
                source = member
            if e.get("record_id"):
                conn.execute(
                    "UPDATE feedback_items SET kind=?,body=?,visibility=?,updated_at=? WHERE feedback_id=?",
                    (kind, body, e["visibility"], stamp, e["record_id"]))
            else:
                fb = f"fb-{uuid4().hex}"
                conn.execute(
                    """INSERT INTO feedback_items(feedback_id,member_key,kind,source_person,source_role,body,
                    visibility,delivered_at,received_at,meeting_id,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (fb, member, kind, source, "lead" if given else "member", body, e["visibility"],
                     day if given else None, None if given else day, meeting["meeting_id"], stamp, stamp))
                domain_db.audit(conn, "feedback", fb, "create", {"meeting_id": meeting["meeting_id"]})
                e["record_type"], e["record_id"] = "feedback", fb

    def _drop_item_record(self, conn, entry: dict) -> None:
        """A removed log item takes its owned record along; tasks survive in Tasks."""
        kind, rid = entry.get("record_type"), entry.get("record_id")
        if not rid or kind == "assignment":
            return
        if kind == "assignment_update":
            conn.execute("DELETE FROM assignment_updates WHERE update_id=?", (rid,))
        elif kind == "roadblock":
            conn.execute("DELETE FROM typed_refs WHERE source_type='roadblock' AND source_id=?", (rid,))
            conn.execute("DELETE FROM roadblocks WHERE roadblock_id=?", (rid,))
        elif kind == "feedback":
            conn.execute("DELETE FROM feedback_items WHERE feedback_id=?", (rid,))
        domain_db.audit(conn, kind, rid, "delete", {"entry_id": entry["entry_id"]})

    def _link_roadblock_raid(self, data: dict) -> dict:
        _keys(data, {"roadblock_id", "raid_id"}, {"roadblock_id", "raid_id"})
        _text(data["raid_id"], "raid_id")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM roadblocks WHERE roadblock_id=?", (data["roadblock_id"],)).fetchone():
                raise ValueError("roadblock not found")
            conn.execute(
                """INSERT INTO typed_refs(ref_id,source_type,source_id,domain_name,target_type,
                target_id,relation,provenance,check_status,created_at)
                VALUES(?,'roadblock',?,'raid','raid-item',?,'escalated-to','portal','unchecked',?)""",
                (f"ref-{uuid4().hex}", data["roadblock_id"], data["raid_id"], _stamp()))
            domain_db.audit(conn, "roadblock", data["roadblock_id"], "raid-link", {"raid_id": data["raid_id"]})
        return {"roadblock_id": data["roadblock_id"], "raid_id": data["raid_id"]}

    def _save_calendar(self, data: dict, *, seed: bool = False) -> dict:
        _keys(data, {"revision", "milestones", "sprints"}, {"milestones", "sprints"})
        if not seed and not isinstance(data.get("revision"), str):
            raise ValueError("calendar revision required")
        for field in ("milestones", "sprints"):
            if not isinstance(data[field], list):
                raise ValueError(f"{field} must be a list")
        mids, sids = set(), set()
        for field, key, ids in (("milestones", "milestone_id", mids), ("sprints", "sprint_id", sids)):
            for row in data[field]:
                if not isinstance(row, dict):
                    raise ValueError("calendar rows must be objects")
                fields = {key, "name", "started_at", "ended_at"}
                if field == "sprints":
                    fields.add("milestone_id")
                _keys(row, fields, fields)
                for text in (key, "name"):
                    _text(row[text], text)
                if row[key] in ids:
                    raise ValueError(f"duplicate {key}")
                ids.add(row[key])
                if _day(row["started_at"], "started_at") >= _day(row["ended_at"], "ended_at"):
                    raise ValueError("calendar windows must be nonempty and end-exclusive")
        by_mid = {m["milestone_id"]: m for m in data["milestones"]}
        for s in data["sprints"]:
            m = by_mid.get(s["milestone_id"])
            if not m or not (m["started_at"] <= s["started_at"] < s["ended_at"] <= m["ended_at"]):
                raise ValueError("sprint must be within its associated milestone")
        for i, a in enumerate(data["sprints"]):
            for b in data["sprints"][i + 1:]:
                if max(a["started_at"], b["started_at"]) < min(a["ended_at"], b["ended_at"]):
                    raise ValueError("sprint windows must not overlap")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self.calendar("private")
            if not seed and current["revision"] != data["revision"]:
                raise ValueError("stale calendar revision")
            if seed:
                for field, key in (("milestones", "milestone_id"), ("sprints", "sprint_id")):
                    existing = {r[key]: r for r in current[field]}
                    for row in data[field]:
                        if row[key] in existing and row != existing[row[key]]:
                            raise ValueError(f"approved calendar conflicts with existing {row[key]}")
            else:
                retained = mids | sids
                for (scope,) in conn.execute("SELECT scope_id FROM commitment_baselines"):
                    if scope not in retained and not re.fullmatch(r"\d{4}-Q[1-4]", scope):
                        raise ValueError("calendar removal would orphan a baseline")
                conn.execute("DELETE FROM sprints")
                conn.execute("DELETE FROM milestones")
            stamp = _stamp()
            for m in data["milestones"]:
                conn.execute(
                    "INSERT OR IGNORE INTO milestones VALUES(?,?,?,?,?)",
                    (m["milestone_id"], m["name"], m["started_at"], m["ended_at"], stamp),
                )
            for s in data["sprints"]:
                conn.execute(
                    """INSERT OR IGNORE INTO sprints(
                       sprint_id,milestone_id,name,started_at,ended_at,state,source,updated_at
                       ) VALUES(?,?,?,?,?,'scheduled','team-manager',?)""",
                    (s["sprint_id"], s["milestone_id"], s["name"], s["started_at"], s["ended_at"], stamp),
                )
            domain_db.audit(conn, "calendar", "", "seed" if seed else "save",
                            {"milestones": len(mids), "sprints": len(sids)})
        return self.calendar("private")

    def seed_approved_calendar(self) -> dict:
        """Explicit, idempotent approved seed. Never called by reads or initialization."""
        dates = ["2026-10-19", "2026-11-02", "2026-11-16", "2026-11-30", "2026-12-14", "2027-01-04"]
        return self._save_calendar({
            "milestones": [
                {"milestone_id": "MS9", "name": "MS9", "started_at": dates[0], "ended_at": dates[-1]},
                {"milestone_id": "2027-Q1", "name": "Q1 2027 (name TBD)",
                 "started_at": "2027-01-01", "ended_at": "2027-04-01"},
            ],
            "sprints": [
                {"sprint_id": f"MS9.{i+1}", "milestone_id": "MS9",
                 "name": f"FW HSI MS9.{i+1}", "started_at": start, "ended_at": end}
                for i, (start, end) in enumerate(zip(dates, dates[1:]))
            ],
        }, seed=True)

    def _save_baseline(self, data: dict) -> dict:
        _keys(data, {"id", "revision", "member_key", "scope_id", "work_refs", "visibility"},
              {"member_key", "scope_id", "work_refs"})
        if not isinstance(data["work_refs"], list) or not all(
            isinstance(ref, str) and re.fullmatch(
                r"(?:[A-Z][A-Z0-9]{1,15}-[0-9]+|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+|"
                r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[0-9]+)", ref
            ) for ref in data["work_refs"]
        ):
            raise ValueError("work_refs must contain public Jira keys or GitHub PR references only")
        visibility = _visibility(data.get("visibility", "private"))
        ident, stamp = data.get("id") or f"baseline-{uuid4().hex}", _stamp()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("SELECT 1 FROM members WHERE member_key=?", (data["member_key"],)).fetchone():
                raise ValueError("member not found")
            if not re.fullmatch(r"\d{4}-Q[1-4]", data["scope_id"]) and not conn.execute(
                "SELECT 1 FROM milestones WHERE milestone_id=? UNION ALL SELECT 1 FROM sprints WHERE sprint_id=?",
                (data["scope_id"], data["scope_id"]),
            ).fetchone():
                raise ValueError("unknown baseline scope")
            before = conn.execute("SELECT * FROM commitment_baselines WHERE baseline_id=?", (ident,)).fetchone()
            if before:
                if domain_db.revision(dict(before)) != data.get("revision"):
                    raise ValueError("stale baseline revision")
            elif "id" in data or "revision" in data:
                raise ValueError("baseline not found")
            conn.execute(
                """INSERT INTO commitment_baselines VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(baseline_id) DO UPDATE SET member_key=excluded.member_key,
                scope_id=excluded.scope_id,work_refs_json=excluded.work_refs_json,
                visibility=excluded.visibility,updated_at=excluded.updated_at""",
                (ident, data["member_key"], data["scope_id"],
                 json.dumps(sorted(set(data["work_refs"]))), visibility,
                 before["created_at"] if before else stamp, stamp),
            )
            domain_db.audit(conn, "baseline", ident, "save", {"scope_id": data["scope_id"]})
        return next(b for b in self.calendar()["baselines"] if b["baseline_id"] == ident)

    def portal_mutate(self, action: str, data: dict) -> dict:
        """Private-only owner mutation facade. Fields are flat, not nested patches."""
        if not isinstance(data, dict):
            raise ValueError("mutation data must be an object")
        data = dict(data)
        if action == "bulk-set":
            return self._bulk_set(data)
        if action == "member-add":
            _keys(data, set(self.MEMBER_EDITABLE) | {"member_key"}, {"member_key", "display_name"})
            ident = data.pop("member_key")
            self.add_member(ident, data)
            entity = self.get_member(ident)
        elif action == "member-set":
            _keys(data, set(self.MEMBER_EDITABLE) | {"id", "revision"}, {"id", "revision"})
            ident, revision = data.pop("id"), data.pop("revision")
            self.update_member(ident, revision, data)
            entity = self.get_member(ident)
        elif action in {"meeting-create", "meeting-save"}:
            entity = self._save_meeting(data, create=action == "meeting-create")
            ident = entity["meeting_id"]
        elif action in {"assignment-create", "assignment-set", "assignment-note", "note"}:
            entity = self._mutate_assignment(action, data)
            ident = entity["assignment_id"]
        elif action == "roadblock-raid-link":
            entity = self._link_roadblock_raid(data)
            ident = entity["roadblock_id"]
        elif action == "calendar-save":
            entity = self._save_calendar(data)
            ident = "calendar"
        elif action == "baseline-save":
            entity = self._save_baseline(data)
            ident = entity["baseline_id"]
        else:
            raise ValueError(f"unsupported team mutation: {action}")
        return {"ok": True, "action": action, "id": ident,
                "revision": entity.get("revision"), "entity": entity}

    def _bulk_set(self, data: dict) -> dict:
        _keys(data, {"entity_type", "member_key", "items", "field", "value"},
              {"entity_type", "member_key", "items", "field", "value"})
        entity_type, field, value = data["entity_type"], data["field"], data["value"]
        allowed = {
            "assignment": {"category", "priority", "status", "target_date"},
            "meeting": {"session_type", "meeting_date", "title_visibility"},
        }
        if entity_type not in allowed or field not in allowed[entity_type]:
            raise ValueError("unsupported bulk field")
        _text(data["member_key"], "member_key")
        items = data["items"]
        if not isinstance(items, list) or not 1 <= len(items) <= 500:
            raise ValueError("bulk edit requires 1 to 500 items")
        ids = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("bulk items must be objects")
            _keys(item, {"id", "revision"}, {"id", "revision"})
            _text(item["id"], "id")
            _text(item["revision"], "revision")
            if item["id"] in ids:
                raise ValueError("duplicate bulk item")
            ids.add(item["id"])
        if entity_type == "assignment":
            self._validate_assignment({field: value})
            if field == "target_date" and value is not None:
                _day(value, field)
        elif field == "session_type":
            if value not in LOG_TYPES:
                raise ValueError("invalid log type")
        elif field == "meeting_date":
            _day(value, field)
        else:
            _visibility(value)
        table, key = ("assignments", "assignment_id") if entity_type == "assignment" else ("meetings", "meeting_id")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            snapshots = []
            for item in items:
                row = (self._meeting_snapshot(conn, item["id"]) if entity_type == "meeting"
                       else self._entity_row(conn, "assignment", item["id"]))
                if row is None or row["member_key"] != data["member_key"]:
                    raise ValueError(f"missing {entity_type} for member: {item['id']}")
                if domain_db.revision(row) != item["revision"]:
                    raise ValueError(f"stale revision for {item['id']}; reload before saving; no rows changed")
                snapshots.append(row)
            for row in snapshots:
                ident = row[key]
                if entity_type == "meeting":
                    self._remember_meeting(conn, row)
                updates = {field: value, "updated_at": _stamp()}
                if entity_type == "meeting" and row["finalized"]:
                    updates["state"] = "amended"
                conn.execute(f"UPDATE {table} SET {','.join(k+'=?' for k in updates)} WHERE {key}=?",
                             (*updates.values(), ident))
                if entity_type == "meeting":
                    self._remember_meeting(conn, self._meeting_snapshot(conn, ident))
                domain_db.audit(conn, entity_type, ident, "bulk-update", {field: value})
        return {"ok": True, "action": "bulk-set", "count": len(items)}

    def _mutate_assignment(self, action: str, data: dict) -> dict:
        fields = {
            "member_key", "owner_label", "title", "description", "kind", "status",
            "outcome", "target_date", "jira_key", "origin_meeting_id", "visibility",
            "category", "priority", "refs",
        }
        if action in {"assignment-note", "note"}:
            _keys(data, {"id", "revision", "note", "visibility"}, {"id", "revision", "note"})
            _text(data["note"], "note")
            _visibility(data.get("visibility", "private"))
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = self._entity_row(conn, "assignment", data["id"])
                if row is None or domain_db.revision(row) != data["revision"]:
                    raise ValueError("missing assignment or stale revision")
                conn.execute(
                    """INSERT INTO assignment_updates(
                    update_id,assignment_id,author,note,visibility,created_at) VALUES(?,?,?,?,?,?)""",
                    (f"au-{uuid4().hex}", data["id"], "team-manager", data["note"],
                     data.get("visibility", "private"), _stamp()),
                )
                conn.execute("UPDATE assignments SET updated_at=? WHERE assignment_id=?", (_stamp(), data["id"]))
                domain_db.audit(conn, "assignment", data["id"], "note", {"visibility": data.get("visibility", "private")})
                return self._assignment_projection(conn, data["id"], "private")
        creating = action == "assignment-create"
        _keys(data, fields | {"id", "revision"}, {"member_key", "title"} if creating else {"id", "revision"})
        if creating and ("id" in data or "revision" in data):
            raise ValueError("new assignments do not accept id or revision")
        self._validate_assignment(data)
        refs = data.pop("refs", None)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            stamp = _stamp()
            if creating:
                ident = f"asg-{uuid4().hex}"
                data.setdefault("owner_label", data["member_key"])
                data.setdefault("kind", "task")
                data.setdefault("status", "todo")
                data.update(assignment_id=ident, created_at=stamp, updated_at=stamp)
                conn.execute(
                    f"INSERT INTO assignments({','.join(data)}) VALUES({','.join('?' for _ in data)})",
                    tuple(data.values()),
                )
            else:
                ident, revision = data.pop("id"), data.pop("revision")
                row = self._entity_row(conn, "assignment", ident)
                if row is None or domain_db.revision(row) != revision:
                    raise ValueError("missing assignment or stale revision")
                data["updated_at"] = stamp
                conn.execute(f"UPDATE assignments SET {','.join(k+'=?' for k in data)} WHERE assignment_id=?",
                             (*data.values(), ident))
            if refs is not None:
                self._save_task_refs(conn, ident, refs)
            domain_db.audit(conn, "assignment", ident, "create" if creating else "update", data)
            return self._assignment_projection(conn, ident, "private")

    def _save_task_refs(self, conn, ident: str, refs: list[dict]) -> None:
        conn.execute(
            """DELETE FROM typed_refs WHERE source_type='assignment' AND source_id=?
            AND provenance='task-form'""", (ident,),
        )
        for ref in refs:
            if conn.execute(
                """SELECT 1 FROM typed_refs WHERE source_type='assignment' AND source_id=?
                AND domain_name=? AND target_type=? AND target_id=? AND relation=?""",
                (ident, ref["domain_name"], ref["target_type"], ref["target_id"],
                 ref.get("relation", "related-to")),
            ).fetchone():
                continue
            conn.execute(
                """INSERT INTO typed_refs(ref_id,source_type,source_id,domain_name,target_type,
                target_id,relation,provenance,check_status,created_at)
                VALUES(?,'assignment',?,?,?,?,?,'task-form','unchecked',?)""",
                (f"ref-{uuid4().hex}", ident, ref["domain_name"], ref["target_type"],
                 ref["target_id"], ref.get("relation", "related-to"), _stamp()),
            )

    def _validate_assignment(self, data: dict) -> None:
        if "category" in data and data["category"] not in TASK_CATEGORIES:
            raise ValueError("invalid task category")
        if "priority" in data and data["priority"] not in {"critical", "high", "medium", "low"}:
            raise ValueError("invalid task priority")
        for field in ("description", "outcome"):
            if field in data and data[field] is not None:
                _text(data[field], field, empty=True)
        if "refs" in data:
            if not isinstance(data["refs"], list):
                raise ValueError("refs must be a list")
            for ref in data["refs"]:
                if not isinstance(ref, dict):
                    raise ValueError("each reference must be an object")
                _keys(ref, {"domain_name", "target_type", "target_id", "relation"},
                      {"domain_name", "target_type", "target_id"})
                for field in ref:
                    _text(ref[field], field)
                if ref["domain_name"] == "github" and not re.fullmatch(
                    r"https://github\.com/[\w.-]+/[\w.-]+/pull/[1-9]\d*", ref["target_id"]
                ):
                    raise ValueError("GitHub references must be PR URLs")
                if ref["domain_name"] == "jira" and not re.fullmatch(
                    r"[A-Z][A-Z0-9]{1,15}-\d+", ref["target_id"]
                ):
                    raise ValueError("invalid Jira reference")
        if "visibility" in data:
            _visibility(data["visibility"])
        if "title" in data:
            _text(data["title"], "title")
        if "owner_label" in data:
            _text(data["owner_label"], "owner_label")
        if data.get("target_date"):
            _day(data["target_date"], "target_date")
        if data.get("jira_key") and not re.fullmatch(r"[A-Z][A-Z0-9]{1,15}-\d+", data["jira_key"]):
            raise ValueError("invalid jira_key")
        if "status" in data and data["status"] not in {
            "assigned", "open", "todo", "backlog", "in-progress", "blocked", "done", "cancelled", "deferred",
        }:
            raise ValueError("invalid assignment status")
