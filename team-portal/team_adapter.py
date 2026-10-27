"""Read-only adapter for Team portal saved evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "task-manager"))

PR_URL_RE = re.compile(r"https://github\.com/\S+/pull/\d+")
REVIEW_KEYWORDS = ("review", "approve", "request changes", "re-review")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open_team_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(Path.home() / "tasks" / "team.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _member_activity(member_key: str) -> list[dict]:
    activity: list[dict] = []
    with _open_team_db() as conn:
        for row in conn.execute(
            """
            SELECT pr_ref, repo, pr_number, pr_title, merged_at
            FROM feature_prs
            JOIN feature_sets USING(feature_id)
            WHERE feature_sets.member_key=? AND merged_at IS NOT NULL
            ORDER BY merged_at DESC
            LIMIT 15
            """,
            (member_key,),
        ):
            label = row["pr_title"] or row["pr_ref"] or f"PR #{row['pr_number']}"
            activity.append(
                {
                    "at": row["merged_at"],
                    "kind": "pr",
                    "label": label,
                    "url": row["pr_ref"],
                    "source": row["repo"] or "github",
                }
            )
        for row in conn.execute(
            """
            SELECT issue_key, issue_type, completed_at, source
            FROM issue_completions
            WHERE member_key=?
            ORDER BY completed_at DESC
            LIMIT 15
            """,
            (member_key,),
        ):
            activity.append(
                {
                    "at": row["completed_at"],
                    "kind": "issue",
                    "label": row["issue_key"],
                    "url": None,
                    "source": row["source"] or row["issue_type"],
                }
            )
    activity.sort(key=lambda item: item.get("at") or "", reverse=True)
    return activity[:20]


def _review_tasks_for_member(member: dict) -> list[dict]:
    import tm

    handle = (member.get("github_handle") or "").lower().strip()
    name_tokens = [tok for tok in (member.get("display_name") or "").lower().split() if len(tok) >= 3]
    reviews: list[dict] = []
    for task in tm.load()["tasks"]:
        status = (task.get("status") or "").lower()
        if status in {"done", "closed", "completed", "cancelled"}:
            continue
        text_blob = " ".join(
            str(task.get(field) or "")
            for field in ("title", "description", "context", "notes", "jira")
        ).lower()
        if not any(word in text_blob for word in REVIEW_KEYWORDS):
            continue
        if handle and handle not in text_blob and not any(tok in text_blob for tok in name_tokens):
            continue
        pr_urls: list[str] = []
        for field in ("description", "context", "notes"):
            field_text = str(task.get(field) or "")
            pr_urls.extend(PR_URL_RE.findall(field_text))
        reviews.append(
            {
                "task_id": task.get("id"),
                "title": task.get("title"),
                "status": task.get("status"),
                "jira": task.get("jira"),
                "pr_urls": sorted(set(pr_urls)),
            }
        )
    return reviews[:20]


def saved_evidence(members: list[dict], audience: str = "shared") -> dict:
    if audience not in {"shared", "private"}:
        raise ValueError("audience must be shared or private")
    sources = ["team-db"]
    try:
        import tm  # noqa: F401
        sources.append("task-manager")
    except ModuleNotFoundError:
        pass
    per_member = {}
    for member in members:
        key = member.get("member_key")
        if not key:
            continue
        activity = _member_activity(key)
        reviews = _review_tasks_for_member(member) if "task-manager" in sources else []
        times = [item.get("at") for item in activity if item.get("at")]
        per_member[key] = {
            "activity": activity,
            "reviews": reviews,
            "last_activity_at": max(times) if times else None,
        }
    return {
        "generated_at": _iso_now(),
        "coverage": {
            "note": "Saved evidence is built from owner stores only.",
            "sources": sources,
        },
        "per_member": per_member,
        "planning_items": [],
    }
