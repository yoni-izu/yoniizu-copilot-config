"""Owner-side Team projections and validated portal mutations.

This module is a TeamStore mixin, not a web server. Callers must authorize
private access before selecting that audience or invoking private mutations.
The shared-log edit mutation is restricted to shared-visible fields and rows.
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
LOG_ITEM_PRIORITIES = ("critical", "high", "medium", "low")
ITEM_STATUSES = ("unspecified", "open", "in-progress", "blocked", "done", "cancelled", "deferred")
# The lead's own log records 1:1s with their manager, so "member" reads as manager there.
MANAGER_NAME = os.environ.get("TEAM_MANAGER_NAME", "Manager")
PARTY = ["me", "member"]

# Typed log items. Served to the portal so the form is generated from one spec.
# Each field: key, label, input (text|textarea|date|select|checkbox|task), required, options.
LOG_ITEM_TYPES: dict[str, dict[str, Any]] = {
    "action": {"label": "Action item", "summary": "title", "default_visibility": "shared", "fields": [
        {"key": "title", "label": "Action", "input": "text", "required": True},
        {"key": "owner", "label": "Owner", "input": "select", "options": PARTY},
        {"key": "target_date", "label": "Due", "input": "date"},
        {"key": "category", "label": "Task category", "input": "select", "options": list(TASK_CATEGORIES)},
        {"key": "priority", "label": "Priority", "input": "select",
         "options": ["medium", "high", "critical", "low"]},
        {"key": "jira_key", "label": "Jira", "input": "text"},
        {"key": "description", "label": "Details", "input": "textarea"},
    ]},
    "decision": {"label": "Decision", "summary": "decision", "default_visibility": "shared", "fields": [
        {"key": "decision", "label": "Decision", "input": "text", "required": True},
        {"key": "rationale", "label": "Rationale", "input": "textarea"},
        {"key": "decided_by", "label": "Decided by", "input": "select", "options": ["joint", "me", "member"]},
        {"key": "revisit_date", "label": "Revisit by", "input": "date"},
    ]},
    "discussion": {"label": "Discussion point", "summary": "topic", "default_visibility": "shared", "fields": [
        {"key": "topic", "label": "Topic", "input": "text", "required": True},
        {"key": "raised_by", "label": "Raised by", "input": "select", "options": PARTY},
        {"key": "outcome", "label": "Outcome", "input": "textarea"},
        {"key": "follow_up", "label": "Follow-up needed", "input": "checkbox"},
    ]},
    "status": {"label": "Status update", "summary": "note", "default_visibility": "shared", "fields": [
        {"key": "assignment_id", "label": "Task (optional)", "input": "task"},
        {"key": "activity", "label": "Activity", "input": "text"},
        {"key": "status", "label": "New status", "input": "select", "options": [""] + list(ASSIGNMENT_STATUSES)},
        {"key": "note", "label": "Progress", "input": "textarea", "required": True},
    ]},
    "roadblock": {"label": "Roadblock", "summary": "blocker", "default_visibility": "shared", "fields": [
        {"key": "blocker", "label": "Blocker", "input": "text", "required": True},
        {"key": "impact", "label": "Impact", "input": "textarea"},
        {"key": "unblocker", "label": "Who unblocks", "input": "select", "options": ["me", "member", "other"]},
        {"key": "unblocker_name", "label": "Name (if other)", "input": "text"},
        {"key": "status", "label": "Status", "input": "select", "options": ["open", "resolved"]},
    ]},
    "feedback_given": {"label": "Feedback given", "summary": "behavior", "default_visibility": "shared", "fields": [
        {"key": "polarity", "label": "Kind", "input": "select", "options": ["positive", "constructive"]},
        {"key": "situation", "label": "Situation", "input": "text"},
        {"key": "behavior", "label": "Behavior", "input": "textarea", "required": True},
        {"key": "impact", "label": "Impact", "input": "textarea"},
    ]},
    "feedback_received": {"label": "Feedback received", "summary": "feedback", "default_visibility": "shared",
                          "fields": [
        {"key": "feedback", "label": "Feedback", "input": "textarea", "required": True},
        {"key": "follow_up", "label": "My follow-up", "input": "textarea"},
    ]},
    "growth": {"label": "Growth / career", "summary": "goal", "default_visibility": "shared", "fields": [
        {"key": "goal", "label": "Goal / aspiration", "input": "text", "required": True},
        {"key": "next_step", "label": "Next step", "input": "text"},
        {"key": "horizon", "label": "Horizon", "input": "text"},
    ]},
    "personal": {"label": "Personal", "summary": "text", "default_visibility": "shared", "fields": [
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
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{tag}.{key} must be a boolean")
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
    if tag == "status":
        if out.get("assignment_id"):
            if out.get("activity"):
                raise ValueError("status.activity is only available without a task")
        elif out.get("status"):
            raise ValueError("status: New status requires a task")
    return out


def _item_summary(tag: str, fields: dict[str, Any]) -> str:
    if tag == "status" and not fields.get("assignment_id"):
        activity = fields.get("activity")
        text = f"{activity}: {fields['note']}" if activity else fields["note"]
        return text.splitlines()[0][:240]
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
        if row.get("deleted_at") is not None:
            return None
        revision = domain_db.revision(row)
        for e in row["entries"]:
            e["fields"] = _entry_fields(e)
            if e["tag"] == "action" and e.get("record_id"):
                task = conn.execute(
                    "SELECT priority FROM assignments WHERE assignment_id=?", (e["record_id"],)
                ).fetchone()
                if task:
                    e["priority"] = task["priority"]
                    e["fields"]["priority"] = task["priority"]
            if e.get("record_type") == "roadblock" and e.get("record_id"):
                ref = conn.execute(
                    """SELECT target_id FROM typed_refs WHERE source_type='roadblock' AND source_id=?
                    AND domain_name='raid' ORDER BY created_at DESC LIMIT 1""", (e["record_id"],),
                ).fetchone()
                if ref:
                    e["raid_id"] = ref[0]
            if e.get("item_id"):
                item = self._item_projection(conn, e["item_id"], audience)
                if item:
                    e["tracking"] = {k: item.get(k) for k in (
                        "status", "owner", "target_date", "follow_up",
                    )}
                    e["item_revision" if audience == "private" else "item_edit_token"] = item.get(
                        "revision" if audience == "private" else "edit_token"
                    )
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
            {k: e.get(k) for k in (
                "entry_id", "tag", "body", "visibility", "priority", "position", "fields", "raid_id",
                "item_id", "tracking", "item_edit_token", "follow_up_from",
            )}
            | ({"locked": bool(e.get("record_id"))} if e["tag"] == "action" else {})
            for e in row["entries"] if self._entry_shared(conn, e)
        ]
        if not entries:
            return None
        return {
            "meeting_id": ident, "member_key": row["member_key"],
            "session_type": row["session_type"],
            "meeting_date": row["meeting_date"], "state": row["state"],
            "finalized": bool(row["finalized"]), "entries": entries,
            "title_visibility": row["title_visibility"], "edit_token": revision,
            "title": row["title"] if entries and row["title_visibility"] == "shared" else "1:1 session",
        }

    def _register_tracked_item(self, conn, entry: dict, meeting: dict | None, *, historical=False) -> str:
        record_type, record_id = entry.get("record_type"), entry.get("record_id")
        if historical and entry.get("entry_id"):
            found = conn.execute(
                "SELECT item_id FROM tracked_items WHERE origin_entry_id=? AND item_type=?",
                (entry["entry_id"], entry["tag"]),
            ).fetchone()
            if found:
                return found[0]
        if record_type == "assignment" and record_id:
            found = conn.execute(
                "SELECT item_id FROM tracked_items WHERE record_type='assignment' AND record_id=?",
                (record_id,),
            ).fetchone()
            if found:
                return found[0]
        fields = entry.get("fields")
        if fields is None and entry.get("fields_json"):
            fields = json.loads(entry["fields_json"])
        status = "unspecified" if historical else "open"
        if entry["tag"] == "roadblock" and fields and fields.get("status"):
            status = "done" if fields["status"] == "resolved" else "open"
        follow_up = bool(fields and entry["tag"] == "discussion" and fields.get("follow_up"))
        ident, stamp = f"item-{uuid4().hex}", entry.get("created_at") or _stamp()
        conn.execute(
            """INSERT INTO tracked_items(item_id,member_key,item_type,body,fields_json,status,
            priority,follow_up,visibility,record_type,record_id,origin_meeting_id,origin_entry_id,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ident, meeting["member_key"] if meeting else entry["member_key"], entry["tag"],
             entry["body"], json.dumps(fields, ensure_ascii=False) if fields is not None else None,
             status, entry.get("priority", "medium"), int(follow_up), entry["visibility"],
             record_type, record_id, meeting["meeting_id"] if meeting else entry.get("origin_meeting_id"),
             entry.get("entry_id"), stamp, stamp),
        )
        return ident

    def _backfill_tracked_items(self, conn) -> bool:
        """Register historical/imported rows without inventing their lifecycle state."""
        changed = False
        for raw in conn.execute(
            """SELECT a.* FROM assignments a WHERE NOT EXISTS(
            SELECT 1 FROM tracked_items i WHERE i.record_type='assignment'
            AND i.record_id=a.assignment_id)"""
        ).fetchall():
            task = dict(raw)
            meeting = conn.execute("SELECT * FROM meetings WHERE meeting_id=?",
                                   (task["origin_meeting_id"],)).fetchone()
            self._register_tracked_item(conn, {
                **task, "tag": "action", "body": task["title"],
                "record_type": "assignment", "record_id": task["assignment_id"],
            }, dict(meeting) if meeting else None, historical=True)
            changed = True
        for raw in conn.execute(
            """SELECT e.* FROM meeting_entries e JOIN meetings m USING(meeting_id)
            WHERE e.item_id IS NULL AND m.deleted_at IS NULL
            ORDER BY m.meeting_date,m.created_at,e.position,e.entry_id"""
        ).fetchall():
            entry = dict(raw)
            meeting = dict(conn.execute("SELECT * FROM meetings WHERE meeting_id=?",
                                        (entry["meeting_id"],)).fetchone())
            ident = self._register_tracked_item(conn, entry, meeting, historical=True)
            conn.execute("UPDATE meeting_entries SET item_id=? WHERE entry_id=?", (ident, entry["entry_id"]))
            changed = True
        return changed

    def _item_snapshot(self, conn, ident: str) -> dict:
        raw = conn.execute("SELECT * FROM tracked_items WHERE item_id=?", (ident,)).fetchone()
        if raw is None:
            raise ValueError("tracked item not found")
        row = dict(raw)
        task = None
        if row["record_type"] == "assignment" and row["record_id"]:
            task = self._entity_row(conn, "assignment", row["record_id"])
            if task is None:
                raise ValueError("tracked task is missing its assignment")
        linked = None
        tables = {
            "roadblock": ("roadblocks", "roadblock_id"),
            "feedback": ("feedback_items", "feedback_id"),
            "assignment_update": ("assignment_updates", "update_id"),
        }
        if row["record_type"] in tables and row["record_id"]:
            table, key = tables[row["record_type"]]
            linked = conn.execute(f"SELECT * FROM {table} WHERE {key}=?", (row["record_id"],)).fetchone()
            if linked is None:
                raise ValueError("tracked item is missing its linked record")
            linked = dict(linked)
        return {"item": row, "assignment": task, "linked": linked}

    def _item_projection(self, conn, ident: str, audience: str) -> dict | None:
        snapshot = self._item_snapshot(conn, ident)
        row, task = snapshot["item"], snapshot["assignment"]
        linked = snapshot["linked"]
        if audience == "shared" and (
            (task["visibility"] if task else row["visibility"]) != "shared"
            or linked and linked.get("visibility", "shared") != "shared"
        ):
            return None
        result = {k: row[k] for k in (
            "item_id", "member_key", "item_type", "body", "status", "priority", "owner",
            "target_date", "visibility", "created_at", "updated_at",
        )}
        result["follow_up"] = bool(row["follow_up"])
        result["fields"] = json.loads(row["fields_json"]) if row["fields_json"] else None
        if task:
            result.update(
                body=task["title"], status=task["status"], priority=task["priority"],
                owner_key=task["member_key"], owner_label=task["owner_label"],
                target_date=task["target_date"], category=task["category"],
                assignment_id=task["assignment_id"],
                visibility=task["visibility"],
            )
            lead = self._lead_key(conn)
            result["owner"] = "member" if task["owner_label"] == "manager" else (
                "me" if task["member_key"] == lead else "member"
            )
            result["fields"] = {
                "title": task["title"], "owner": result["owner"], "priority": task["priority"],
                "category": task["category"],
                **{k: task[k] for k in ("description", "target_date", "jira_key") if task[k]},
            }
            assignment = self._assignment_projection(conn, task["assignment_id"], audience)
            result["updates"], result["refs"] = assignment["updates"], assignment["refs"]
            if audience == "private":
                result["assignment_revision"] = assignment["revision"]
        else:
            if row["record_type"] == "roadblock" and row["record_id"]:
                roadblock = conn.execute("SELECT status FROM roadblocks WHERE roadblock_id=?",
                                         (row["record_id"],)).fetchone()
                if roadblock and roadblock["status"] == "resolved" and result["status"] != "cancelled":
                    result["status"] = "done"
                if result["fields"] is not None and roadblock:
                    result["fields"]["status"] = roadblock["status"]
            result["updates"] = [
                dict(r) for r in conn.execute(
                    """SELECT update_id,note,visibility,created_at FROM item_updates
                    WHERE item_id=? ORDER BY created_at,update_id""", (ident,)
                ) if audience == "private" or r["visibility"] == "shared"
            ]
        result["occurrences"] = []
        for raw in conn.execute(
            """SELECT e.entry_id,e.body,e.visibility,e.follow_up_from,m.meeting_id,m.meeting_date,
            m.title,m.title_visibility FROM meeting_entries e JOIN meetings m USING(meeting_id)
            WHERE e.item_id=? AND m.deleted_at IS NULL ORDER BY m.meeting_date,m.created_at,e.position""",
            (ident,),
        ):
            if audience == "shared" and raw["visibility"] != "shared":
                continue
            occurrence = {k: raw[k] for k in (
                "entry_id", "body", "follow_up_from", "meeting_id", "meeting_date",
            )}
            occurrence["title"] = raw["title"] if audience == "private" or raw["title_visibility"] == "shared" else "1:1 session"
            result["occurrences"].append(occurrence)
        revision = domain_db.revision(snapshot)
        result["revision" if audience == "private" else "edit_token"] = revision
        return result

    def _entry_shared(self, conn, entry: dict) -> bool:
        return entry["visibility"] == "shared" and (
            not entry.get("item_id") or self._item_projection(conn, entry["item_id"], "shared") is not None
        )

    def _prepare_meeting(self, data: dict) -> dict:
        _keys(data, {"member_key", "meeting_date", "session_type"}, {"member_key"})
        day = data.get("meeting_date") or date.today().isoformat()
        _day(day, "meeting_date")
        session_type = data.get("session_type", "one_on_one")
        if session_type not in LOG_TYPES:
            raise ValueError("invalid log type")
        with self.connect() as conn:
            if not conn.execute("SELECT 1 FROM members WHERE member_key=?", (data["member_key"],)).fetchone():
                raise ValueError("member not found")
            entries = self._follow_up_entries(conn, data["member_key"], day) if session_type == "one_on_one" else []
        return {"meeting_date": day, "session_type": session_type, "entries": entries}

    def _follow_up_entries(self, conn, member: str, day: str) -> list[dict]:
        entries = []
        for raw in conn.execute(
            "SELECT item_id FROM tracked_items WHERE follow_up=1 ORDER BY created_at,item_id"
        ).fetchall():
            item = self._item_projection(conn, raw[0], "private")
            if item["status"] in {"done", "cancelled"}:
                continue
            source = conn.execute(
                """SELECT m.meeting_date FROM meeting_entries e JOIN meetings m USING(meeting_id)
                WHERE e.item_id=? AND m.member_key=? AND m.session_type='one_on_one'
                AND m.deleted_at IS NULL
                ORDER BY m.meeting_date DESC,m.created_at DESC LIMIT 1""", (raw[0], member)
            ).fetchone()
            if not source:
                has_occurrence = conn.execute(
                    "SELECT 1 FROM meeting_entries WHERE item_id=? LIMIT 1", (raw[0],)
                ).fetchone()
                if has_occurrence or not item.get("assignment_id") or item["owner_key"] != member:
                    continue
                source_day = item["created_at"][:10]
            else:
                source_day = source[0]
            if source_day > day:
                continue
            entry = {
                "entry_id": f"me-{uuid4().hex}",
                "tag": item["item_type"], "body": item["body"], "priority": item["priority"],
                "visibility": item["visibility"], "item_id": item["item_id"],
                "item_revision": item["revision"], "tracking_status": item["status"],
                "tracking_owner": item["owner"], "tracking_due": item["target_date"],
                "follow_up": True, "follow_up_from": source_day,
            }
            if item["fields"] is not None:
                entry["fields"] = item["fields"]
            if item.get("assignment_id"):
                entry["record_id"], entry["locked"] = item["assignment_id"], True
            entries.append(entry)
        return entries

    def _bind_item(self, conn, meeting: dict, entry: dict, previous: dict | None) -> dict | None:
        ident = entry.get("item_id") or (
            previous.get("item_id") if previous and previous["tag"] == entry["tag"] else None
        )
        if not ident:
            return None
        snapshot = self._item_snapshot(conn, ident)
        item = snapshot["item"]
        if item["item_type"] != entry["tag"]:
            raise ValueError("tracked item type mismatch")
        if previous and previous.get("item_id") != ident:
            raise ValueError("a saved row cannot change its tracked item identity")
        if not previous:
            eligible = {e["item_id"]: e for e in self._follow_up_entries(
                conn, meeting["member_key"], meeting["meeting_date"]
            )} if meeting["session_type"] == "one_on_one" else {}
            if ident not in eligible:
                raise ValueError("item is not an eligible follow-up for this member and date")
            entry["follow_up_from"] = eligible[ident]["follow_up_from"]
        if entry.get("item_revision") is not None:
            if domain_db.revision(snapshot) != entry["item_revision"]:
                raise ValueError("stale item revision; reload before saving")
        elif not previous or any(k in entry for k in (
            "follow_up", "tracking_status", "tracking_owner", "tracking_due"
        )):
            raise ValueError("tracked item revision is required")
        entry["item_id"] = ident
        entry["record_type"], entry["record_id"] = item["record_type"], item["record_id"]
        return item

    def _store_item(self, conn, meeting: dict, entry: dict, item: dict | None, stamp: str) -> None:
        if item is None:
            entry["item_id"] = self._register_tracked_item(conn, entry, meeting)
            item = self._item_snapshot(conn, entry["item_id"])["item"]
        updates = {"updated_at": stamp}
        newest = conn.execute(
            """SELECT m.meeting_id,m.meeting_date,m.created_at FROM meeting_entries e JOIN meetings m USING(meeting_id)
            WHERE e.item_id=? AND m.deleted_at IS NULL ORDER BY m.meeting_date DESC,m.created_at DESC LIMIT 1""",
            (entry["item_id"],),
        ).fetchone()
        if not newest or newest[0] == meeting["meeting_id"] or (
            meeting["meeting_date"], meeting["created_at"]
        ) >= (newest["meeting_date"], newest["created_at"]):
            updates.update(body=entry["body"], fields_json=(
                json.dumps(entry["fields"], ensure_ascii=False) if entry["fields"] is not None else None
            ), priority=entry["priority"], visibility=entry["visibility"],
                           record_type=entry.get("record_type"), record_id=entry.get("record_id"))
        for key, column in (
            ("follow_up", "follow_up"), ("tracking_status", "status"),
            ("tracking_owner", "owner"), ("tracking_due", "target_date"),
        ):
            if key in entry:
                updates[column] = int(entry[key]) if key == "follow_up" else entry[key]
        if "follow_up" not in entry and entry["tag"] == "discussion" and entry["fields"] is not None:
            updates["follow_up"] = int(entry["fields"].get("follow_up", False))
        if entry.get("record_type") == "assignment":
            task_updates = {k: updates.pop(k) for k in ("status", "owner", "target_date") if k in updates}
            if task_updates:
                self._set_item_task_fields(conn, entry["record_id"], meeting["member_key"], task_updates, stamp)
        elif entry.get("record_type") == "roadblock" and "status" in updates:
            self._set_item_roadblock_status(conn, entry["record_id"], updates["status"], stamp)
        conn.execute(f"UPDATE tracked_items SET {','.join(k+'=?' for k in updates)} WHERE item_id=?",
                     (*updates.values(), entry["item_id"]))
        domain_db.audit(conn, "tracked-item", entry["item_id"], "save", {"meeting_id": meeting["meeting_id"]})

    def _set_item_task_fields(self, conn, ident: str, member: str, updates: dict, stamp: str) -> None:
        if "status" in updates and updates["status"] not in ASSIGNMENT_STATUSES:
            raise ValueError("invalid task status")
        if "owner" in updates:
            owner = updates.pop("owner")
            lead = self._lead_key(conn)
            if owner not in {"me", "member"} or owner == "me" and not lead:
                raise ValueError("task owner must be a member or configured lead")
            updates["member_key"] = lead if owner == "me" else member
            updates["owner_label"] = "manager" if owner == "member" and member == lead else updates["member_key"]
        updates["updated_at"] = stamp
        self._validate_assignment(updates)
        conn.execute(f"UPDATE assignments SET {','.join(k+'=?' for k in updates)} WHERE assignment_id=?",
                     (*updates.values(), ident))
        domain_db.audit(conn, "assignment", ident, "item-update", updates)

    def _set_item_roadblock_status(self, conn, ident: str, status: str, stamp: str) -> None:
        resolved = status in {"done", "cancelled"}
        conn.execute(
            """UPDATE roadblocks SET status=?,resolved_at=?,updated_at=? WHERE roadblock_id=?""",
            ("resolved" if resolved else "open", stamp if resolved else None, stamp, ident),
        )

    def _apply_item_patch(self, conn, snapshot: dict, values: dict, stamp: str) -> None:
        item, task = snapshot["item"], snapshot["assignment"]
        updates = dict(values)
        self._validate_tracking(updates, task=bool(task))
        if "follow_up" in updates:
            updates["follow_up"] = int(updates["follow_up"])
        if task:
            task_updates = {k: updates.pop(k) for k in (
                "status", "priority", "owner", "target_date", "visibility"
            ) if k in updates}
            if task_updates:
                self._set_item_task_fields(conn, task["assignment_id"], item["member_key"], task_updates, stamp)
                if "visibility" in task_updates:
                    updates["visibility"] = task_updates["visibility"]
        elif item["record_type"] == "roadblock" and "status" in updates:
            self._set_item_roadblock_status(conn, item["record_id"], updates["status"], stamp)
        if not task and "visibility" in updates and item["record_type"] in {"roadblock", "feedback"}:
            table, key = ("roadblocks", "roadblock_id") if item["record_type"] == "roadblock" else (
                "feedback_items", "feedback_id"
            )
            conn.execute(f"UPDATE {table} SET visibility=?,updated_at=? WHERE {key}=?",
                         (updates["visibility"], stamp, item["record_id"]))
        updates["updated_at"] = stamp
        conn.execute(f"UPDATE tracked_items SET {','.join(k+'=?' for k in updates)} WHERE item_id=?",
                     (*updates.values(), item["item_id"]))
        domain_db.audit(conn, "tracked-item", item["item_id"], "update", updates)

    def _mutate_item(self, action: str, data: dict) -> dict:
        allowed = {"id", "revision", "status", "priority", "owner", "target_date", "follow_up", "visibility"}
        if action == "item-note":
            allowed = {"id", "revision", "note", "visibility"}
        _keys(data, allowed, {"id", "revision"} | ({"note"} if action == "item-note" else set()))
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            snapshot = self._item_snapshot(conn, data["id"])
            if domain_db.revision(snapshot) != data["revision"]:
                raise ValueError("stale item revision; reload before saving")
            item, task, stamp = snapshot["item"], snapshot["assignment"], _stamp()
            if action == "item-note":
                _text(data["note"], "note")
                visibility = _visibility(data.get("visibility", "private"))
                table, key = ("assignment_updates", "assignment_id") if task else ("item_updates", "item_id")
                columns = f"update_id,{key},note,visibility,created_at" + (",author" if task else "")
                values = (f"iu-{uuid4().hex}", task["assignment_id"] if task else item["item_id"],
                          data["note"], visibility, stamp) + (("team-manager",) if task else ())
                conn.execute(f"INSERT INTO {table}({columns}) VALUES({','.join('?' for _ in values)})", values)
                if task:
                    conn.execute("UPDATE assignments SET updated_at=? WHERE assignment_id=?",
                                 (stamp, task["assignment_id"]))
                updates = {"updated_at": stamp}
            else:
                self._apply_item_patch(conn, snapshot, {
                    k: v for k, v in data.items() if k not in {"id", "revision"}
                }, stamp)
                return self._item_projection(conn, item["item_id"], "private")
            conn.execute(f"UPDATE tracked_items SET {','.join(k+'=?' for k in updates)} WHERE item_id=?",
                         (*updates.values(), item["item_id"]))
            domain_db.audit(conn, "tracked-item", item["item_id"], action, updates)
            return self._item_projection(conn, item["item_id"], "private")

    def _validate_tracking(self, values: dict, *, task=False) -> None:
        if "status" in values and values["status"] not in (ASSIGNMENT_STATUSES if task else ITEM_STATUSES):
            raise ValueError("invalid item status")
        if "priority" in values and values["priority"] not in LOG_ITEM_PRIORITIES:
            raise ValueError("invalid item priority")
        if "owner" in values and values["owner"] not in {None, "me", "member"}:
            raise ValueError("invalid item owner")
        if "target_date" in values and values["target_date"] is not None:
            _day(values["target_date"], "target_date")
        if "follow_up" in values and not isinstance(values["follow_up"], bool):
            raise ValueError("follow_up must be a boolean")
        if "visibility" in values:
            _visibility(values["visibility"])

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
            "members": [], "meetings": [], "assignments": [], "items": [], "feedback": [],
            "calendar": calendar, "metrics": {},
            "task_categories": list(TASK_CATEGORIES), "log_types": LOG_TYPES,
            "log_item_types": LOG_ITEM_TYPES, "log_item_priorities": list(LOG_ITEM_PRIORITIES),
            "item_statuses": list(ITEM_STATUSES),
            "manager_name": MANAGER_NAME,
        }
        with self.connect() as conn:
            for row in conn.execute("SELECT item_id FROM tracked_items ORDER BY updated_at DESC,item_id"):
                item = self._item_projection(conn, row[0], audience)
                if item is not None:
                    result["items"].append(item)
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

    def _save_meeting(self, data: dict, *, create: bool, shared_only: bool = False) -> dict:
        allowed = {
            "id", "revision", "member_key", "meeting_date", "title", "state",
            "entries", "visibility", "title_visibility", "action_items", "session_type",
        }
        required = {"member_key", "entries"} if create else {
            "id", "revision", "meeting_date", "title", "entries",
        }
        if shared_only:
            allowed = {"id", "revision", "meeting_date", "title", "title_visibility",
                       "entries", "session_type"}
            required = {"id", "revision", "meeting_date", "entries"}
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
            _keys(raw, {"entry_id", "id", "tag", "body", "visibility", "priority", "fields",
                        "item_id", "item_revision", "follow_up", "follow_up_from",
                        "tracking_status", "tracking_owner", "tracking_due"}, {"tag"})
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
            else:
                if "body" not in raw:
                    raise ValueError("missing required fields: ['body']")
                fields, body = None, _text(raw["body"], "body", empty=True)
            priority = raw.get(
                "priority", fields.get("priority", "medium") if tag == "action" and fields else "medium"
            )
            if priority not in LOG_ITEM_PRIORITIES:
                raise ValueError(f"invalid log item priority: {priority}")
            if fields is not None and tag == "action":
                fields["priority"] = priority
            if shared_only and visibility != "shared":
                raise ValueError("Presentation edits can only save shared log items")
            tracking = {column: raw[key] for key, column in (
                ("tracking_status", "status"), ("tracking_owner", "owner"),
                ("tracking_due", "target_date"), ("follow_up", "follow_up"),
            ) if key in raw}
            self._validate_tracking(tracking, task=tag == "action" and fields is not None)
            for key in ("item_id", "item_revision"):
                if key in raw:
                    _text(raw[key], key)
            entries.append({
                "entry_id": ident, "tag": tag, "body": body, "fields": fields,
                "visibility": visibility, "priority": priority, "position": pos,
                **{k: raw[k] for k in (
                    "item_id", "item_revision", "follow_up", "tracking_status", "tracking_owner",
                    "tracking_due",
                ) if k in raw},
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
                if before.get("deleted_at") is not None:
                    raise ValueError("meeting was deleted")
                if data.get("member_key", before["member_key"]) != before["member_key"]:
                    raise ValueError("meeting member cannot change")
                if (shared_only and before["title_visibility"] != "shared"
                        and ("title" in data or "title_visibility" in data)):
                    raise ValueError("hidden topic cannot be edited in Presentation mode")
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
            previous_rows = [dict(r) for r in conn.execute(
                """SELECT * FROM meeting_entries WHERE meeting_id=?
                ORDER BY position,created_at,entry_id""", (ident,))]
            previous = {r["entry_id"]: r for r in previous_rows}
            if shared_only:
                submitted = {e["entry_id"]: e for e in entries}
                merged = []
                for old in previous_rows:
                    replacement = submitted.pop(old["entry_id"], None)
                    if not self._entry_shared(conn, old):
                        if replacement:
                            raise ValueError("private log items cannot be edited in Presentation mode")
                        merged.append({
                            "entry_id": old["entry_id"], "tag": old["tag"], "body": old["body"],
                            "fields": _entry_fields(old), "visibility": old["visibility"],
                            "priority": old["priority"], "position": old["position"],
                            "record_type": old["record_type"], "record_id": old["record_id"],
                            "item_id": old["item_id"], "follow_up_from": old["follow_up_from"],
                            "preserve_tracking": True,
                        })
                    elif replacement:
                        merged.append(replacement)
                entries = merged + list(submitted.values())
            meeting = dict(conn.execute("SELECT * FROM meetings WHERE meeting_id=?", (ident,)).fetchone())
            item_ids = set()
            bindings = {}
            for e in entries:
                if e.get("preserve_tracking"):
                    continue
                old = previous.get(e["entry_id"])
                item = self._bind_item(conn, meeting, e, old)
                if shared_only and item and self._item_projection(conn, item["item_id"], "shared") is None:
                    raise ValueError("private tracked items cannot be edited in Presentation mode")
                if e.get("item_id") in item_ids:
                    raise ValueError("duplicate tracked item in meeting")
                if e.get("item_id"):
                    item_ids.add(e["item_id"])
                bindings[e["entry_id"]] = item
            for e in entries:
                if e.pop("preserve_tracking", False):
                    continue
                old = previous.get(e["entry_id"])
                item = bindings[e["entry_id"]]
                e["follow_up_from"] = old.get("follow_up_from") if old else e.get("follow_up_from")
                latest = conn.execute(
                    """SELECT m.meeting_id,m.meeting_date,m.created_at FROM meeting_entries e
                    JOIN meetings m USING(meeting_id) WHERE e.item_id=? AND m.deleted_at IS NULL
                    ORDER BY m.meeting_date DESC,m.created_at DESC LIMIT 1""", (e.get("item_id"),)
                ).fetchone()
                historical_edit = old and latest and latest["meeting_id"] != ident and (
                    latest["meeting_date"], latest["created_at"]
                ) > (meeting["meeting_date"], meeting["created_at"])
                unchanged_carry = item and not old and e["fields"] == (
                    json.loads(item["fields_json"]) if item["fields_json"] else None
                )
                if e["fields"] is not None:
                    if old and old["tag"] != e["tag"] and not old.get("record_id"):
                        self._drop_item_record(conn, old)
                    if not unchanged_carry and not historical_edit:
                        if item and not old and e["tag"] == "status":
                            e["record_type"], e["record_id"] = None, None
                        self._sync_item(conn, meeting, e, old, stamp)
                elif old:
                    old_entry = old
                    if old_entry["tag"] == e["tag"]:
                        e["record_type"], e["record_id"] = old_entry["record_type"], old_entry["record_id"]
                    else:
                        self._drop_item_record(conn, old_entry)
                self._store_item(conn, meeting, e, item, stamp)
            kept = {e["entry_id"] for e in entries}
            for old_entry in previous.values():
                if old_entry["entry_id"] not in kept:
                    self._drop_item_record(conn, old_entry)
            conn.execute("DELETE FROM meeting_entries WHERE meeting_id=?", (ident,))
            for e in entries:
                conn.execute(
                    """INSERT INTO meeting_entries(
                      entry_id,meeting_id,tag,body,visibility,priority,actor,created_at,position,
                      fields_json,record_type,record_id,item_id,follow_up_from
                    ) VALUES(?,?,?,?,?,?,'team-manager',?,?,?,?,?,?,?)""",
                    (e["entry_id"], ident, e["tag"], e["body"], e["visibility"], e["priority"],
                     stamp, e["position"],
                     json.dumps(e["fields"], ensure_ascii=False) if e["fields"] is not None else None,
                     e.get("record_type"), e.get("record_id"), e.get("item_id"), e.get("follow_up_from")),
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
        if prev and prev.get("record_id"):
            if prev.get("tag") == tag:
                e["record_type"], e["record_id"] = prev["record_type"], prev["record_id"]
            else:
                self._drop_item_record(conn, prev)
        member, day = meeting["member_key"], meeting["meeting_date"]
        lead = self._lead_key(conn)
        if tag == "action":
            if e.get("record_id"):
                # Task is edited in Tasks after creation; the log keeps what was agreed.
                if prev and prev.get("fields_json"):
                    e["fields"] = json.loads(prev["fields_json"])
                    e["body"] = _item_summary(tag, e["fields"])
                task = conn.execute(
                    "SELECT priority FROM assignments WHERE assignment_id=?", (e["record_id"],)
                ).fetchone()
                if task:
                    e["priority"] = task["priority"]
                    e["fields"]["priority"] = task["priority"]
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
            if not f.get("assignment_id"):
                if e.get("record_id"):
                    conn.execute("DELETE FROM assignment_updates WHERE update_id=?", (e["record_id"],))
                    e["record_type"], e["record_id"] = None, None
                e["body"] = _item_summary(tag, f)
                return
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
        if kind == "assignment":
            return
        if entry.get("item_id"):
            other = conn.execute(
                """SELECT 1 FROM meeting_entries e JOIN meetings m USING(meeting_id)
                WHERE e.item_id=? AND e.entry_id<>? AND m.deleted_at IS NULL LIMIT 1""",
                (entry["item_id"], entry["entry_id"]),
            ).fetchone()
            if other:
                return
            conn.execute(
                """UPDATE tracked_items SET status='cancelled',follow_up=0,record_type=NULL,
                record_id=NULL,updated_at=? WHERE item_id=?""", (_stamp(), entry["item_id"])
            )
        if not rid:
            return
        if kind == "assignment_update":
            conn.execute("DELETE FROM assignment_updates WHERE update_id=?", (rid,))
        elif kind == "roadblock":
            conn.execute("DELETE FROM typed_refs WHERE source_type='roadblock' AND source_id=?", (rid,))
            conn.execute("DELETE FROM roadblocks WHERE roadblock_id=?", (rid,))
        elif kind == "feedback":
            conn.execute("DELETE FROM feedback_items WHERE feedback_id=?", (rid,))
        domain_db.audit(conn, kind, rid, "delete", {"entry_id": entry["entry_id"]})

    def _delete_meeting(self, data: dict) -> dict:
        _keys(data, {"id", "revision"}, {"id", "revision"})
        _text(data["id"], "id")
        _text(data["revision"], "revision")
        ident = data["id"]
        stamp = _stamp()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            before = self._meeting_snapshot(conn, ident)
            if domain_db.revision(before) != data["revision"]:
                raise ValueError("stale revision; re-read meeting before deleting")
            if before.get("deleted_at") is not None:
                raise ValueError("meeting was deleted")
            self._remember_meeting(conn, before)
            for entry in before["entries"]:
                self._drop_item_record(conn, entry)
            conn.execute(
                "UPDATE meetings SET deleted_at=?,updated_at=? WHERE meeting_id=?",
                (stamp, stamp, ident),
            )
            after = self._meeting_snapshot(conn, ident)
            revision = self._remember_meeting(conn, after)
            domain_db.audit(conn, "meeting", ident, "delete", {
                "member_key": before["member_key"], "entries": len(before["entries"]),
            })
        return {"meeting_id": ident, "deleted_at": stamp, "revision": revision}

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
        """Validate owner mutations; shared-log edits are limited to shared-visible data."""
        if not isinstance(data, dict):
            raise ValueError("mutation data must be an object")
        data = dict(data)
        if action == "bulk-set":
            return self._bulk_set(data)
        if action == "meeting-prepare":
            return {"ok": True, "action": action, "entity": self._prepare_meeting(data)}
        if action in {"item-set", "item-note"}:
            entity = self._mutate_item(action, data)
            ident = entity["item_id"]
        elif action == "member-add":
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
        elif action == "meeting-shared-save":
            saved = self._save_meeting(data, create=False, shared_only=True)
            ident = saved["meeting_id"]
            with self.connect() as conn:
                entity = self._meeting_projection(conn, ident, "shared")
            if entity is None:
                entity = {"meeting_id": ident}
        elif action == "meeting-delete":
            entity = self._delete_meeting(data)
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
        if entity_type == "item":
            return self._bulk_set_items(data)
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
                if (row is None or row["member_key"] != data["member_key"]
                        or entity_type == "meeting" and row.get("deleted_at") is not None):
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

    def _bulk_set_items(self, data: dict) -> dict:
        field = data["field"]
        if field not in {"status", "priority", "target_date", "follow_up"}:
            raise ValueError("unsupported bulk item field")
        rows = data["items"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
            raise ValueError("bulk edit requires 1 to 500 items")
        ids = set()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            snapshots = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("bulk items must be objects")
                _keys(row, {"id", "revision"}, {"id", "revision"})
                if row["id"] in ids:
                    raise ValueError("duplicate bulk item")
                ids.add(row["id"])
                snapshot = self._item_snapshot(conn, row["id"])
                item, task = snapshot["item"], snapshot["assignment"]
                if data["member_key"] not in {item["member_key"], task["member_key"] if task else None}:
                    raise ValueError("item belongs to another member")
                if domain_db.revision(snapshot) != row["revision"]:
                    raise ValueError("stale item revision; no rows changed")
                self._validate_tracking({field: data["value"]}, task=bool(task))
                snapshots.append(snapshot)
            for snapshot in snapshots:
                self._apply_item_patch(conn, snapshot, {field: data["value"]}, _stamp())
        return {"ok": True, "action": "bulk-set", "count": len(rows)}

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
