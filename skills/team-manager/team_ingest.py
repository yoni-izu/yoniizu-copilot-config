#!/usr/bin/env python3
"""HR leave ingestion and cross-domain evidence backfill for team-manager.

Both entry points are preview-by-default: they return a plan and write
nothing unless ``apply=True``. Source databases are opened read-only.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
import io
import json
import math
from pathlib import Path
import sqlite3
from typing import Any

from team_store import TeamStore, _hash_text, _id, now_iso
import domain_db

TASKS_DB = Path("~/tasks/tasks.db").expanduser()
BUGS_DB = Path("~/tasks/bugs.db").expanduser()

# ---------------------------------------------------------------- HR leave

LEAVE_COLUMNS: dict[str, tuple[str, ...]] = {
    "member": ("member", "member_key", "employee", "employee_name", "name",
               "jira_account_id", "account_id", "github_handle"),
    "leave_type": ("leave_type", "type", "absence_type", "category"),
    "start_date": ("start_date", "start", "from", "from_date"),
    "end_date": ("end_date", "end", "to", "to_date"),
    "status": ("status", "approval_status", "state"),
    "amount_days": ("amount_days", "days", "duration_days", "working_days"),
    "requested_at": ("requested_at", "requested", "request_date"),
    "approved_at": ("approved_at", "approved", "approval_date"),
    "external_ref": ("external_ref", "id", "request_id", "leave_id"),
    "notes": ("notes", "comment", "comments"),
}
REQUIRED_LEAVE = ("member", "start_date", "end_date")
STATUS_MAP = {
    "approved": "approved",
    "cancelled": "cancelled", "canceled": "cancelled",
    "pending": "pending", "requested": "pending", "submitted": "pending",
    "rejected": "rejected", "declined": "rejected", "denied": "rejected",
}
DATE_FORMATS = {"iso": "%Y-%m-%d", "dmy": "%d/%m/%Y", "mdy": "%m/%d/%Y", "dmy-dot": "%d.%m.%Y"}
LEAVE_FIELDS = ("member_key", "leave_type", "status", "external_ref", "start_date", "end_date",
                "requested_at", "approved_at", "cancelled_at", "amount_days", "amount_source",
                "source", "notes")


def _norm_header(text: str) -> str:
    return text.strip().lower().replace(" ", "_").replace("-", "_")


def _parse_date(raw: str, fmt: str | None) -> str:
    raw = raw.strip()
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        pass
    if not fmt:
        raise ValueError(f"non-ISO date '{raw}' needs --date-format ({'/'.join(DATE_FORMATS)})")
    return datetime.strptime(raw, DATE_FORMATS[fmt]).date().isoformat()


def _member_index(conn: sqlite3.Connection, extra: dict[str, str] | None) -> dict[str, str]:
    index: dict[str, str] = {}
    for row in conn.execute("SELECT member_key, display_name, jira_account_id, github_handle FROM members"):
        for value in row:
            if value:
                index[str(value).strip().lower()] = row["member_key"]
    for alias, key in (extra or {}).items():
        index[alias.strip().lower()] = key
    return index


def weekdays(start: date, end: date, base_country: str) -> int:
    """Working days inclusive: Israel rests Fri/Sat; other countries Sat/Sun."""
    if not base_country or not base_country.strip():
        raise ValueError("base_country is required to count working days")
    weekend = {4, 5} if base_country.strip().casefold() == "israel" else {5, 6}
    if end < start:
        return 0
    days = (end - start).days + 1
    full, rest = divmod(days, 7)
    count = full * 5
    for i in range(rest):
        if (start + timedelta(days=full * 7 + i)).weekday() not in weekend:
            count += 1
    return count


def import_leave_csv(
    store: TeamStore,
    csv_path: Path,
    *,
    apply: bool = False,
    source_label: str = "hr-csv",
    date_format: str | None = None,
    assume_status: str | None = None,
    member_map: dict[str, str] | None = None,
    allow_partial: bool = False,
) -> dict[str, Any]:
    csv_path = Path(csv_path).expanduser()
    text = csv_path.read_text(encoding="utf-8-sig")
    source_hash = _hash_text(text)
    reader = csv.DictReader(io.StringIO(text))
    headers = {_norm_header(h): h for h in (reader.fieldnames or [])}
    columns = {
        field: next((headers[a] for a in aliases if a in headers), None)
        for field, aliases in LEAVE_COLUMNS.items()
    }
    missing = [f for f in REQUIRED_LEAVE if not columns[f]]
    if not columns["status"] and not assume_status:
        missing.append("status (or pass --assume-status)")
    result: dict[str, Any] = {
        "source": str(csv_path), "source_hash": source_hash, "applied": False,
        "column_map": columns, "new": [], "updated": [], "unchanged": 0,
        "skipped_rejected": [], "errors": [], "warnings": [],
    }
    if missing:
        result["errors"].append({"row": 0, "error": f"missing required columns: {missing}"})
        return result

    with store.connect() as conn:
        prior = conn.execute(
            "SELECT 1 FROM imports WHERE source_kind='hr-leave' AND source_hash=? AND status='imported'",
            (source_hash,),
        ).fetchone()
        if prior:
            result["status"] = "skipped-already-imported"
            return result
        members = _member_index(conn, member_map)
        planned: list[dict[str, Any]] = []
        for line_no, raw in enumerate(reader, start=2):
            def col(field: str) -> str:
                name = columns[field]
                return (raw.get(name) or "").strip() if name else ""
            try:
                who = col("member")
                member_key = members.get(who.lower())
                if not member_key:
                    raise ValueError(f"unresolved member '{who}' (use --member-map)")
                start = _parse_date(col("start_date"), date_format)
                end = _parse_date(col("end_date"), date_format)
                if end < start:
                    raise ValueError(f"end {end} before start {start}")
                status_raw = (col("status") or assume_status or "").lower()
                status = STATUS_MAP.get(status_raw)
                if not status:
                    raise ValueError(f"unknown status '{status_raw}'")
                amount = col("amount_days")
                amount_days = float(amount) if amount else None
                if amount_days is not None and (not math.isfinite(amount_days) or amount_days < 0):
                    raise ValueError("amount_days must be finite and nonnegative")
            except ValueError as exc:
                result["errors"].append({"row": line_no, "error": str(exc)})
                continue
            if status == "rejected":
                result["skipped_rejected"].append(line_no)
                continue
            leave_type = (col("leave_type") or "vacation").lower()
            if leave_type in {"sick leave", "sick_leave", "sick-leave"}:
                leave_type = "sick"
            external_ref = col("external_ref") or None
            ident = _id("lv", source_label, external_ref) if external_ref else _id(
                "lv", source_label, member_key, leave_type, start, end
            )
            record = {
                "leave_id": ident, "member_key": member_key, "leave_type": leave_type,
                "status": status, "external_ref": external_ref, "start_date": start,
                "end_date": end,
                "requested_at": _parse_date(col("requested_at"), date_format) if col("requested_at") else None,
                "approved_at": _parse_date(col("approved_at"), date_format) if col("approved_at") else None,
                "cancelled_at": None, "amount_days": amount_days,
                "amount_source": "hr" if amount_days is not None else None,
                "source": source_label, "notes": col("notes") or None, "_row": line_no,
            }
            existing = conn.execute("SELECT * FROM leave_records WHERE leave_id=?", (ident,)).fetchone()
            if status == "cancelled":
                record["cancelled_at"] = (
                    existing["cancelled_at"] if existing and existing["cancelled_at"]
                    else date.today().isoformat()
                )
            if existing is None:
                record["_action"] = "new"
            elif any(existing[f] != record[f] for f in LEAVE_FIELDS):
                record["_action"] = "update"
            else:
                record["_action"] = "unchanged"
            planned.append(record)

        seen_ids: dict[str, int] = {}
        for rec in planned:
            if rec["leave_id"] in seen_ids:
                result["errors"].append({
                    "row": rec["_row"],
                    "error": f"duplicate of row {seen_ids[rec['leave_id']]} (same id)",
                })
            seen_ids[rec["leave_id"]] = rec["_row"]
        approved = [r for r in planned if r["status"] == "approved"]
        stored = conn.execute(
            "SELECT leave_id, member_key, start_date, end_date FROM leave_records WHERE status='approved'"
        ).fetchall()
        candidates = approved + [dict(r) for r in stored if r["leave_id"] not in seen_ids]
        for i, a in enumerate(approved):
            for b in candidates[i + 1:]:
                if (a["member_key"] == b["member_key"] and a["leave_id"] != b["leave_id"]
                        and a["start_date"] <= b["end_date"] and b["start_date"] <= a["end_date"]):
                    result["warnings"].append(
                        f"overlap {a['member_key']}: {a['start_date']}..{a['end_date']} vs "
                        f"{b['start_date']}..{b['end_date']}"
                    )

        for rec in planned:
            brief = {k: rec[k] for k in ("member_key", "leave_type", "status", "start_date", "end_date", "amount_days")}
            if rec["_action"] == "new":
                result["new"].append(brief)
            elif rec["_action"] == "update":
                result["updated"].append(brief)
            else:
                result["unchanged"] += 1

        if result["errors"] and not allow_partial:
            result["status"] = "rejected-validation-errors"
            return result
        if not apply:
            result["status"] = "preview"
            return result

        domain_db.backup_legacy(csv_path, store.db_path)
        stamp = now_iso()
        for rec in planned:
            if rec["_action"] == "unchanged":
                continue
            values = {f: rec[f] for f in LEAVE_FIELDS}
            conn.execute(
                f"""
                INSERT INTO leave_records(leave_id, {', '.join(LEAVE_FIELDS)}, created_at, updated_at)
                VALUES(?, {', '.join('?' for _ in LEAVE_FIELDS)}, ?, ?)
                ON CONFLICT(leave_id) DO UPDATE SET
                  {', '.join(f'{f}=excluded.{f}' for f in LEAVE_FIELDS)},
                  updated_at=excluded.updated_at
                """,
                (rec["leave_id"], *values.values(), stamp, stamp),
            )
            domain_db.audit(conn, "leave", rec["leave_id"], rec["_action"], values)
        conn.execute(
            """
            INSERT INTO imports(import_id, source_path, source_hash, source_kind, status, message,
                                entity_type, entity_id, imported_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (_id("imp", str(csv_path), source_hash, stamp), str(csv_path), source_hash, "hr-leave",
             "imported",
             f"new={len(result['new'])} updated={len(result['updated'])} errors={len(result['errors'])}",
             "leave", source_label, stamp),
        )
        result["applied"] = True
        result["status"] = "imported"
    return result


def leave_stats(store: TeamStore, member_key: str, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or date.today()
    with store.connect() as conn:
        member = conn.execute(
            "SELECT base_country FROM members WHERE member_key=?", (member_key,),
        ).fetchone()
        if member is None:
            raise ValueError(f"member not found: {member_key}")
        last_import = conn.execute(
            "SELECT MAX(imported_at) AS at FROM imports WHERE source_kind='hr-leave' AND status='imported'"
        ).fetchone()["at"]
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM leave_records WHERE member_key=? ORDER BY start_date",
            (member_key,),
        )]
    stats: dict[str, Any] = {
        "member_key": member_key, "as_of": as_of.isoformat(),
        "hr_last_import_at": last_import,
        "days_taken_this_year": None, "days_estimated": False,
        "sick_leave_days_this_year": None, "sick_leave_days_estimated": False,
        "base_country": member["base_country"],
        "coverage": "Recorded HR entries only; missing members, types and periods are unknown.",
        "current_leave": None, "next_approved_leave": None, "last_approved_leave": None,
    }
    if last_import is None or not rows:
        if not rows:
            stats["hr_last_import_at"] = None
        stats["note"] = "no HR leave records for this member; leave is unknown, not zero"
        return stats

    def brief(r: dict[str, Any]) -> dict[str, Any]:
        return {k: r[k] for k in ("leave_id", "leave_type", "start_date", "end_date", "amount_days")}

    year_start = date(as_of.year, 1, 1)
    totals = {kind: 0.0 for kind in ("vacation", "sick") if any(
        r["leave_type"] == kind for r in rows
    )}
    country = member["base_country"]
    for r in rows:
        kind = r["leave_type"]
        if r["status"] != "approved":
            continue
        start, end = date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"])
        lo, hi = max(start, year_start), min(end, as_of)
        if kind in totals and lo <= hi and country and country.strip():
            inside = weekdays(lo, hi, country)
            total = weekdays(start, end, country)
            amount = min(r["amount_days"], total) if r["amount_days"] is not None else total
            totals[kind] += amount * inside / total if total else 0
            estimated_field = "days_estimated" if kind == "vacation" else "sick_leave_days_estimated"
            if r["amount_days"] is None or (lo, hi) != (start, end) or amount != r["amount_days"]:
                stats[estimated_field] = True
        if start <= as_of <= end:
            stats["current_leave"] = brief(r)
        elif start > as_of and stats["next_approved_leave"] is None:
            stats["next_approved_leave"] = brief(r)
        elif end < as_of:
            stats["last_approved_leave"] = brief(r)
    if country and country.strip():
        if "vacation" in totals:
            stats["days_taken_this_year"] = round(totals["vacation"], 2)
        if "sick" in totals:
            stats["sick_leave_days_this_year"] = round(totals["sick"], 2)
    else:
        stats["note"] = "base_country is unknown; working-day totals are unknown, not zero"
    return stats


# --------------------------------------------------------- evidence backfill

def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path).expanduser()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _upsert_ref(conn: sqlite3.Connection, source_type: str, source_id: str, domain_name: str,
                target_type: str, target_id: str, provenance: str) -> None:
    stamp = now_iso()
    ref_id = _id("ref", source_type, source_id, domain_name, target_type, target_id, "evidence-of")
    conn.execute(
        """
        INSERT INTO typed_refs(ref_id, source_type, source_id, domain_name, target_type, target_id,
                               relation, provenance, check_status, last_checked_at, created_at)
        VALUES(?,?,?,?,?,?,'evidence-of',?,'resolved',?,?)
        ON CONFLICT(ref_id) DO UPDATE SET check_status='resolved', last_checked_at=excluded.last_checked_at
        """,
        (ref_id, source_type, source_id, domain_name, target_type, target_id, provenance, stamp, stamp),
    )


def _plan_bug_completions(bugs_db: Path, by_jira: dict[str, str]) -> list[dict[str, Any]]:
    plan = []
    with _ro(bugs_db) as src:
        for (blob,) in src.execute("SELECT data FROM bugs WHERE is_open=0"):
            bug = json.loads(blob)
            member = by_jira.get(bug.get("assignee_id") or "")
            if not member or not bug.get("resolved"):
                continue
            completed_at = str(bug["resolved"])[:10]
            plan.append({
                "completion_id": _id("ic", bug["key"], completed_at, "bugs-db"),
                "member_key": member, "issue_key": bug["key"], "issue_type": "Bug",
                "resolution": bug.get("resolution") or "Unresolved",
                "completed_at": completed_at, "assignee_at_completion": bug.get("assignee"),
                "source": "bugs-db", "source_ref": bug["key"],
            })
    return plan


def _plan_review_features(tasks_db: Path, by_github: dict[str, str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    unmapped: dict[str, int] = {}
    with _ro(tasks_db) as src:
        tasks = {r["id"]: json.loads(r["body"]) for r in src.execute("SELECT id, body FROM tasks")}
        for row in src.execute("SELECT task_id, body FROM task_reviews ORDER BY task_id, position"):
            review = json.loads(row["body"])
            author = (review.get("author") or "").lower()
            member = by_github.get(author)
            if not member:
                if author:
                    unmapped[author] = unmapped.get(author, 0) + 1
                continue
            if review.get("closed") and not review.get("merged"):
                continue
            task = tasks.get(row["task_id"], {})
            jira = task.get("jira")
            key = (member, f"jira:{jira}" if jira else f"task:{row['task_id']}")
            group = groups.setdefault(key, {
                "member_key": member, "jira": jira, "tasks": {}, "prs": {},
            })
            group["tasks"][row["task_id"]] = task
            group["prs"].setdefault(review["pr_url"], review)
    plan = []
    for (member, group_key), g in sorted(groups.items()):
        prs = list(g["prs"].values())
        all_merged = all(p.get("merged") for p in prs)
        done_dates = [t.get("completed") for t in g["tasks"].values() if t.get("completed")]
        first_task = g["tasks"][sorted(g["tasks"])[0]]
        title = (first_task.get("title") or group_key).removeprefix("Review: ").strip()
        plan.append({
            "feature_id": _id("feat", "review-backfill", member, group_key),
            "member_key": member,
            "title": title,
            "status": "delivered" if all_merged else "in-progress",
            "delivered_at": (max(done_dates) if done_dates else max(
                (p.get("submitted_at") or "" for p in prs), default="") or None) if all_merged else None,
            "grouping_basis": "review-task+jira-key" if g["jira"] else "review-task",
            "acceptance_gate": "all-prs-merged",
            "task_ids": sorted(g["tasks"]),
            "prs": [{
                "pr_ref": p["pr_url"], "repo": p.get("repo"), "pr_number": p.get("pr_number"),
                "contributor": p.get("author"), "merged": bool(p.get("merged")),
                "jira_keys": [g["jira"]] if g["jira"] else [],
            } for p in prs],
        })
    return plan, dict(sorted(unmapped.items(), key=lambda kv: -kv[1]))


def backfill_evidence(
    store: TeamStore,
    *,
    tasks_db: Path = TASKS_DB,
    bugs_db: Path = BUGS_DB,
    apply: bool = False,
    confirm_merged: bool = False,
) -> dict[str, Any]:
    with store.connect() as conn:
        members = conn.execute(
            "SELECT member_key, jira_account_id, github_handle FROM members"
        ).fetchall()
        by_jira = {m["jira_account_id"]: m["member_key"] for m in members if m["jira_account_id"]}
        by_github = {m["github_handle"].lower(): m["member_key"] for m in members if m["github_handle"]}
        bugs = _plan_bug_completions(bugs_db, by_jira) if Path(bugs_db).expanduser().exists() else []
        features, unmapped = (
            _plan_review_features(tasks_db, by_github) if Path(tasks_db).expanduser().exists() else ([], {})
        )
        stale_bugs = [r["completion_id"] for r in conn.execute(
            "SELECT completion_id FROM issue_completions WHERE source='bugs-db'"
        ) if r["completion_id"] not in {b["completion_id"] for b in bugs}]
        stale_features = [r["feature_id"] for r in conn.execute(
            """SELECT feature_id FROM feature_sets
               WHERE grouping_basis LIKE 'review-task%' AND confirmation_state='candidate'"""
        ) if r["feature_id"] not in {f["feature_id"] for f in features}]
        summary: dict[str, Any] = {
            "applied": False,
            "members_without_github_handle": [m["member_key"] for m in members if not m["github_handle"]],
            "unmapped_pr_authors": unmapped,
            "bug_completions": len(bugs),
            "bug_completions_done": sum(1 for b in bugs if b["resolution"] == "Done"),
            "features": len(features),
            "features_delivered": sum(1 for f in features if f["status"] == "delivered"),
            "retract_bug_completions": len(stale_bugs),
            "retract_candidate_features": len(stale_features),
            "per_member": {},
        }
        for b in bugs:
            pm = summary["per_member"].setdefault(b["member_key"], {"bugs": 0, "bugs_done": 0, "features": 0})
            pm["bugs"] += 1
            pm["bugs_done"] += b["resolution"] == "Done"
        for f in features:
            pm = summary["per_member"].setdefault(f["member_key"], {"bugs": 0, "bugs_done": 0, "features": 0})
            pm["features"] += 1
        if not apply:
            return summary

        stamp = now_iso()
        for cid in stale_bugs:
            conn.execute("DELETE FROM typed_refs WHERE source_type='issue-completion' AND source_id=?", (cid,))
            conn.execute("DELETE FROM issue_completions WHERE completion_id=?", (cid,))
            domain_db.audit(conn, "issue-completion", cid, "retract", {"source": "bugs-db"})
        for fid in stale_features:
            conn.execute("DELETE FROM typed_refs WHERE source_type='feature' AND source_id=?", (fid,))
            conn.execute("DELETE FROM feature_sets WHERE feature_id=?", (fid,))
            domain_db.audit(conn, "feature", fid, "retract", {"source": "tasks-db"})
        for b in bugs:
            conn.execute(
                """
                INSERT INTO issue_completions(completion_id, member_key, issue_key, issue_type, resolution,
                  completed_at, assignee_at_completion, source, source_ref, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(completion_id) DO UPDATE SET member_key=excluded.member_key,
                  resolution=excluded.resolution, assignee_at_completion=excluded.assignee_at_completion
                """,
                (b["completion_id"], b["member_key"], b["issue_key"], b["issue_type"], b["resolution"],
                 b["completed_at"], b["assignee_at_completion"], b["source"], b["source_ref"], stamp),
            )
            _upsert_ref(conn, "issue-completion", b["completion_id"], "bugs", "bug", b["issue_key"],
                        "backfill:bugs-db")
        for f in features:
            existing = conn.execute(
                "SELECT confirmation_state FROM feature_sets WHERE feature_id=?", (f["feature_id"],)
            ).fetchone()
            state = existing["confirmation_state"] if existing else "candidate"
            if state == "candidate" and confirm_merged and f["status"] == "delivered":
                state = "confirmed"
            conn.execute(
                """
                INSERT INTO feature_sets(feature_id, member_key, title, status, delivered_at, grouping_basis,
                  confirmation_state, acceptance_gate, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(feature_id) DO UPDATE SET title=excluded.title, status=excluded.status,
                  delivered_at=excluded.delivered_at, grouping_basis=excluded.grouping_basis,
                  confirmation_state=excluded.confirmation_state, updated_at=excluded.updated_at
                """,
                (f["feature_id"], f["member_key"], f["title"], f["status"], f["delivered_at"],
                 f["grouping_basis"], state, f["acceptance_gate"], stamp, stamp),
            )
            conn.execute("DELETE FROM feature_prs WHERE feature_id=?", (f["feature_id"],))
            for pr in f["prs"]:
                conn.execute(
                    """
                    INSERT INTO feature_prs(feature_pr_id, feature_id, pr_ref, repo, pr_number, pr_title,
                      contributor, merged_at, jira_keys_json, role, created_at)
                    VALUES(?,?,?,?,?,NULL,?,NULL,?,?,?)
                    """,
                    (_id("fpr", f["feature_id"], pr["pr_ref"]), f["feature_id"], pr["pr_ref"], pr["repo"],
                     pr["pr_number"], pr["contributor"], json.dumps(pr["jira_keys"]),
                     "author" if pr["merged"] else "author-unmerged", stamp),
                )
            for task_id in f["task_ids"]:
                _upsert_ref(conn, "feature", f["feature_id"], "tasks", "task", task_id, "backfill:tasks-db")
        domain_db.audit(conn, "team-store", "", "backfill", {
            k: v for k, v in summary.items() if k != "per_member"
        })
        summary["applied"] = True
    return summary
