"""Team-manager onboarding: preview, register and track a new team member.

Onboarding writes only owner records in team.db: the member profile, one
assignment per checklist step (tracked as items on the member page) and typed
references back to the member/buddy. External access (GitHub, Jira, VMs,
channels) is tracked as requests; nothing here grants permissions or calls
external systems.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from team_store import EMPLOYMENT_TYPES, TeamStore  # also puts ../../lib on sys.path
import domain_db
from team_portal import _day, _keys, _stamp, _visibility

ID_PREFIX = "asg-onb-"
KIND = "onboarding"

# Intake specification served to the portal; the form is generated from it.
# need: required (registration fails without it), readiness (registration
# succeeds but a named capability stays unknown) or optional.
INTAKE_FIELDS: list[dict[str, Any]] = [
    {"key": "member_key", "label": "Member key", "input": "text", "need": "required",
     "help": "Stable lowercase key, e.g. first name ([a-z0-9_-])."},
    {"key": "display_name", "label": "Full name", "input": "text", "need": "required"},
    {"key": "onboarding_date", "label": "Start date", "input": "date", "need": "required"},
    {"key": "role_title", "label": "Role", "input": "text", "need": "readiness",
     "impact": "Role is shown as unknown in team views."},
    {"key": "employment_type", "label": "Employment type", "input": "select",
     "options": list(EMPLOYMENT_TYPES), "need": "readiness",
     "impact": "Capacity assumptions for planning stay unknown."},
    {"key": "base_country", "label": "Base country", "input": "text", "need": "readiness",
     "impact": "Leave working-day totals stay unknown (country decides the weekend)."},
    {"key": "jira_account_id", "label": "Jira account ID", "input": "text", "need": "readiness",
     "impact": "Jira activity scans and bug/attribute/story metrics stay unknown."},
    {"key": "github_handle", "label": "GitHub handle", "input": "text", "need": "readiness",
     "impact": "GitHub activity scans and review/feature evidence stay unknown."},
    {"key": "main_ip_ownership", "label": "Main IP / domain", "input": "text", "need": "readiness",
     "impact": "IP ownership routing shows no owner for this member."},
    {"key": "buddy_key", "label": "Onboarding buddy", "input": "member", "need": "readiness",
     "impact": "No buddy is assigned; the checklist asks the lead to assign one."},
    {"key": "focus", "label": "Initial focus", "input": "textarea", "need": "optional"},
    {"key": "goals", "label": "Initial goals (private)", "input": "textarea", "need": "optional"},
    {"key": "confluence_host_url", "label": "Confluence personal space URL", "input": "text",
     "need": "optional"},
    {"key": "profile_notes", "label": "Profile notes (private)", "input": "textarea", "need": "optional"},
]

MEMBER_FIELDS = tuple(f["key"] for f in INTAKE_FIELDS if f["key"] not in {"member_key", "buddy_key"})

# owner: "lead" or "member". offset: calendar days from the start date.
CHECKLIST: list[dict[str, Any]] = [
    {"step": "access", "stage": "Before arrival", "offset": -5, "owner": "lead", "category": "planning",
     "priority": "high", "title": "Request accounts and access",
     "description": "Request GitHub org/team membership, Jira project access, Confluence space and "
                    "team chat channels. Record each request; mark done when every access works."},
    {"step": "machine", "stage": "Before arrival", "offset": -5, "owner": "lead", "category": "planning",
     "priority": "high", "title": "Request laptop, development VM and workspace slot",
     "description": "Arrange equipment, a development VM and a firmware workspace slot."},
    {"step": "buddy", "stage": "Before arrival", "offset": -3, "owner": "lead", "category": "planning",
     "priority": "medium", "title": "Confirm onboarding buddy",
     "description": "Agree the buddy and brief them on the first-month plan."},
    {"step": "welcome", "stage": "First day", "offset": 0, "owner": "lead", "category": "misc",
     "priority": "high", "title": "Day-one welcome and team introduction",
     "description": "Introduce the team, roles, HSI scope, working hours and communication channels."},
    {"step": "reading", "stage": "First day", "offset": 0, "owner": "lead", "category": "research",
     "priority": "medium", "title": "Share the onboarding reading list",
     "description": "Firmware architecture rules, project PRM, owned IP specification, coding "
                    "conventions, commit/PR rules and CI overview."},
    {"step": "environment", "stage": "First week", "offset": 7, "owner": "member", "category": "development",
     "priority": "high", "title": "Set up the firmware workspace and complete a first build and test",
     "description": "Check out the workspace, build on the VM and run the unit tests of the owned IP."},
    {"step": "identities", "stage": "First week", "offset": 7, "owner": "lead", "category": "track",
     "priority": "medium", "title": "Verify Jira and GitHub identities and run the first activity scan",
     "description": "Confirm the recorded Jira account ID and GitHub handle, then run the Team "
                    "activity scan. Unavailable sources stay unknown, not zero."},
    {"step": "first-1on1", "stage": "First week", "offset": 7, "owner": "lead", "category": "planning",
     "priority": "high", "title": "Hold the first 1:1",
     "description": "Expectations, working agreements, 1:1 cadence and the initial focus."},
    {"step": "first-contribution", "stage": "First two weeks", "offset": 14, "owner": "member",
     "category": "development", "priority": "medium", "title": "Land a first small reviewed contribution",
     "description": "A starter task or PR, reviewed by the buddy or lead."},
    {"step": "goals", "stage": "First month", "offset": 30, "owner": "lead", "category": "planning",
     "priority": "medium", "title": "Agree initial goals, IP ownership and milestone capacity",
     "description": "Record goals and ownership in the member profile; agree capacity for the next milestone."},
    {"step": "retrospective", "stage": "First month", "offset": 30, "owner": "lead", "category": "misc",
     "priority": "low", "title": "Onboarding retrospective",
     "description": "Ask what was missing or slow and improve this checklist."},
]

STAGES = list(dict.fromkeys(step["stage"] for step in CHECKLIST))


def spec() -> dict[str, Any]:
    return {"fields": INTAKE_FIELDS, "checklist": CHECKLIST, "stages": STAGES}


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    allowed = {f["key"] for f in INTAKE_FIELDS} | {"task_visibility", "plan_revision"}
    _keys(data, allowed, {"member_key", "display_name", "onboarding_date"})
    out = {}
    for key, value in data.items():
        if isinstance(value, str):
            value = value.strip()
        out[key] = value if value not in ("", None) else None
    return out


def _gaps(member: dict[str, Any], buddy_key: str | None) -> list[dict[str, str]]:
    values = {**member, "buddy_key": buddy_key}
    return [
        {"field": f["key"], "label": f["label"], "impact": f["impact"]}
        for f in INTAKE_FIELDS if f["need"] == "readiness" and not values.get(f["key"])
    ]


def _tasks(member_key: str, start: date, lead_key: str | None, buddy: dict | None,
           visibility: str) -> list[dict[str, Any]]:
    tasks = []
    for step in CHECKLIST:
        title = step["title"]
        if step["step"] == "buddy":
            if buddy:
                title = f"Confirm onboarding buddy: {buddy['display_name']}"
            else:
                title = "Assign an onboarding buddy"
        lead_owned = step["owner"] == "lead" and lead_key
        tasks.append({
            "assignment_id": f"{ID_PREFIX}{member_key}-{step['step']}",
            "step": step["step"], "stage": step["stage"], "title": title, "description": step["description"],
            "category": step["category"], "priority": step["priority"],
            "owner": step["owner"],
            # Lead-owned steps sit on the lead's task list and the new member's items,
            # matching how "owner: me" 1:1 actions are stored.
            "owner_key": lead_key if lead_owned else member_key,
            "target_date": (start + timedelta(days=step["offset"])).isoformat(),
            "visibility": visibility,
        })
    return tasks


def preview(store: TeamStore, data: dict[str, Any]) -> dict[str, Any]:
    """Validate an intake and return the exact plan apply() would write. Never writes."""
    if not isinstance(data, dict):
        raise ValueError("onboarding data must be an object")
    data = _clean(data)
    data.pop("plan_revision", None)
    member_key = data.pop("member_key")
    buddy_key = data.pop("buddy_key", None)
    visibility = _visibility(data.pop("task_visibility", None) or "private")
    start = date.fromisoformat(_day(data["onboarding_date"], "onboarding_date"))
    fields = {k: v for k, v in data.items() if v is not None}
    fields = store._prepare_new_member(member_key, fields)
    with store.connect() as conn:
        errors = store._member_conflicts(conn, member_key, fields)
        buddy = None
        if buddy_key:
            if buddy_key == member_key:
                errors.append("buddy must be an existing team member, not the new member")
            else:
                row = conn.execute(
                    "SELECT member_key,display_name FROM members WHERE member_key=?", (buddy_key,)
                ).fetchone()
                if row is None:
                    errors.append(f"buddy not found: {buddy_key}")
                else:
                    buddy = dict(row)
        lead_key = store._lead_key(conn)
    tasks = _tasks(member_key, start, lead_key, buddy, visibility)
    plan = {
        "member_key": member_key, "member": fields, "buddy": buddy,
        "task_visibility": visibility, "lead_key": lead_key, "tasks": tasks,
    }
    plan["plan_revision"] = domain_db.revision(plan)
    plan["errors"] = errors
    plan["gaps"] = _gaps(fields, buddy_key if buddy else None)
    plan["not_done"] = [
        "No accounts, channels, VMs or repository permissions are granted; checklist steps track the requests.",
        "Leave history and delivery metrics stay unknown until HR import or recorded evidence exists.",
        "Nothing is exported to Confluence or Jira.",
    ]
    if lead_key is None:
        plan["warnings"] = ["No roster lead is recorded; lead-owned steps are placed on the new member's list."]
    return plan


def apply(store: TeamStore, data: dict[str, Any]) -> dict[str, Any]:
    """Register the member and checklist in one transaction, only if the reviewed plan is unchanged."""
    if not isinstance(data, dict) or not data.get("plan_revision"):
        raise ValueError("plan_revision from a preview is required; preview before registering")
    reviewed = data["plan_revision"]
    plan = preview(store, data)
    if plan["errors"]:
        raise ValueError("; ".join(plan["errors"]))
    if plan["plan_revision"] != reviewed:
        raise ValueError("onboarding plan changed since preview; preview again before registering")
    member_key, stamp = plan["member_key"], _stamp()
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        store._insert_member(conn, member_key, plan["member"], stamp)
        for task in plan["tasks"]:
            ident = task["assignment_id"]
            conn.execute(
                """INSERT INTO assignments(assignment_id,member_key,owner_label,title,description,kind,
                status,target_date,visibility,created_at,updated_at,category,priority)
                VALUES(?,?,?,?,?,?,'todo',?,?,?,?,?,?)""",
                (ident, task["owner_key"], task["owner_key"], task["title"], task["description"], KIND,
                 task["target_date"], task["visibility"], stamp, stamp, task["category"], task["priority"]),
            )
            store._register_tracked_item(conn, {
                "member_key": member_key, "tag": "action", "body": task["title"],
                "visibility": task["visibility"], "priority": task["priority"],
                "record_type": "assignment", "record_id": ident, "created_at": stamp,
            }, None)
            refs = [("member", member_key, "onboarding-of")]
            if task["step"] == "buddy" and plan["buddy"]:
                refs.append(("member", plan["buddy"]["member_key"], "onboarding-buddy"))
            for target_type, target_id, relation in refs:
                conn.execute(
                    """INSERT INTO typed_refs(ref_id,source_type,source_id,domain_name,target_type,
                    target_id,relation,provenance,check_status,created_at)
                    VALUES(?,'assignment',?,'team',?,?,?,'onboarding','unchecked',?)""",
                    (f"ref-{ident}-{relation}", ident, target_type, target_id, relation, stamp),
                )
        domain_db.audit(conn, "member", member_key, "onboard", {
            "plan_revision": reviewed, "tasks": [t["assignment_id"] for t in plan["tasks"]],
            "buddy": plan["buddy"]["member_key"] if plan["buddy"] else None,
        })
    return {"member_key": member_key, "plan_revision": reviewed,
            "tasks": [t["assignment_id"] for t in plan["tasks"]], "gaps": plan["gaps"]}


def status(store: TeamStore, member_key: str, audience: str = "private",
           as_of: date | None = None) -> dict[str, Any] | None:
    """Checklist progress; readiness gaps are private. None if the member was not onboarded here."""
    _visibility(audience)
    today = as_of or date.today()
    with store.connect() as conn:
        member = conn.execute("SELECT * FROM members WHERE member_key=?", (member_key,)).fetchone()
        if member is None:
            raise ValueError(f"member not found: {member_key}")
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM assignments WHERE assignment_id LIKE ? AND kind=? ORDER BY target_date,assignment_id",
            (f"{ID_PREFIX}{member_key}-%", KIND),
        )]
        buddy = conn.execute(
            """SELECT target_id FROM typed_refs WHERE source_id=? AND relation='onboarding-buddy'""",
            (f"{ID_PREFIX}{member_key}-buddy",),
        ).fetchone()
    if not rows:
        return None
    order = {s["step"]: i for i, s in enumerate(CHECKLIST)}
    stage = {s["step"]: s["stage"] for s in CHECKLIST}
    steps = []
    for row in rows:
        if audience == "shared" and row["visibility"] != "shared":
            continue
        step = row["assignment_id"][len(f"{ID_PREFIX}{member_key}-"):]
        steps.append({
            "step": step, "stage": stage.get(step, "Other"), "assignment_id": row["assignment_id"],
            "title": row["title"], "status": row["status"], "target_date": row["target_date"],
            "owner_key": row["member_key"],
            "overdue": bool(row["target_date"] and row["status"] not in {"done", "cancelled"}
                            and row["target_date"] < today.isoformat()),
        })
    steps.sort(key=lambda s: (order.get(s["step"], len(order)), s["assignment_id"]))
    open_steps = [s for s in steps if s["status"] not in {"done", "cancelled"}]
    result = {
        "member_key": member_key, "total": len(steps),
        "done": sum(s["status"] == "done" for s in steps),
        "cancelled": sum(s["status"] == "cancelled" for s in steps),
        "open": len(open_steps), "overdue": sum(s["overdue"] for s in steps),
        "complete": bool(steps) and not open_steps,
        "next": min(open_steps, key=lambda s: s["target_date"] or "9999")["title"] if open_steps else None,
        "steps": steps,
    }
    if audience == "private":
        result["gaps"] = _gaps(dict(member), buddy[0] if buddy else None)
    return result
