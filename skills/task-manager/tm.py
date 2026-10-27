#!/usr/bin/env python3
"""task-manager CLI — pure mechanics for the task-manager skill.

Subcommands that mutate / read ~/tasks/tasks.db without any LLM judgment.
See SKILL.md for which actions still require the model.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# workspace-manager owns all slot occupancy. tm.py delegates every workspace
# mutation/read to it (imported as a module; also a standalone CLI).
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/workspace-manager"))
import wm  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_store  # noqa: E402

# ----------------------------------------------------------------------------
# Constants & helpers
# ----------------------------------------------------------------------------

# Legacy tasks.json anchor; the canonical store is task_store.DB_PATH. Used for
# path-derived defaults (dashboard output, pid file) under ~/tasks.
STORE = Path(os.path.expanduser("~/tasks/tasks.json"))
PROJECTS_ROOT = Path(os.path.expanduser("~/projects"))
DEFAULT_PROJECT = "iridium"

CATEGORIES = {"development", "code-review", "planning", "testing", "track", "misc"}
PRIORITIES = ["critical", "high", "medium", "low"]
STATUSES = {"backlog", "todo", "in-progress", "blocked", "done", "cancelled"}
COMPLEXITIES = ["trivial", "small", "medium", "large", "epic"]
COMPLEXITY_DEFAULT_COST = {
    "trivial": 0.5,
    "small": 1.5,
    "medium": 4,
    "large": 10,
    "epic": 24,
}
REVIEW_STATUSES = {
    "pending",
    "in-progress",
    "commented",
    "changes-requested",
    "waiting-for-re-review",
    "approved",
    "merged",
}
TODAY = date.today().isoformat()
# Timestamp for note prefixes: date + time (notes are stored as
# ``YYYY-MM-DD HH:MM: msg``). Date-only fields (created/updated/completed)
# keep using TODAY.
NOW = datetime.now().strftime("%Y-%m-%d %H:%M")


def load() -> dict:
    """Task snapshot from the SQLite store (task_store owns persistence)."""
    return task_store.load()


def save(d: dict) -> None:
    """Three-way merged save; raises task_store.ConflictError on real conflicts."""
    task_store.save(d)


def task_revision(task: dict) -> str:
    return task_store.domain_db.revision(task)


def find_task(d: dict, ident: str) -> dict | None:
    """Find by exact T-NNN ID or by case-insensitive substring of title."""
    ident_l = ident.lower()
    # ID match
    for t in d["tasks"]:
        if t["id"].lower() == ident_l:
            return t
    # Title substring
    matches = [t for t in d["tasks"] if ident_l in t["title"].lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"[tm] ambiguous match for {ident!r}:", file=sys.stderr)
        for t in matches:
            print(f"  {t['id']}: {t['title']}", file=sys.stderr)
        return None
    return None


def days_since(iso: str) -> int:
    try:
        d0 = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return 0
    return (date.today() - d0).days


def last_worked(t: dict) -> str | None:
    """ISO date the task was most recently worked on.

    Uses the latest of the ``updated`` field and the most recent note's date
    prefix (notes are stored as ``YYYY-MM-DD HH:MM: msg``; legacy notes use
    ``YYYY-MM-DD: msg``). Returns None if unknown.
    """
    candidates: list[str] = []
    upd = t.get("updated")
    if upd:
        candidates.append(upd)
    for note in t.get("notes", []):
        m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2})?)?:", note)
        if m:
            candidates.append(m.group(1))
    valid = []
    for c in candidates:
        try:
            valid.append(datetime.strptime(c, "%Y-%m-%d").date())
        except ValueError:
            continue
    if not valid:
        return None
    return max(valid).isoformat()


def last_worked_label(t: dict) -> str:
    """Human-friendly 'days ago' label for the last time the task was worked."""
    iso = last_worked(t)
    if not iso:
        return "—"
    n = days_since(iso)
    return "today" if n == 0 else f"{n}d ago"


def detect_workspace(cwd: str | None = None) -> str | None:
    """Return workspace slot name if cwd is under ~/projects/<project>/<ws>/..."""
    return wm.slot_from_cwd(cwd)


def slot_holder_task(d: dict, slot: str) -> dict | None:
    """Resolve the task currently holding ``slot`` (if a task holds it)."""
    holder = wm.get_holder(slot)
    if holder and holder.get("type") == "task":
        return find_task(d, holder["task_id"])
    return None


def task_slot(task_id: str) -> str | None:
    """Slot a task currently holds, or ``None``."""
    return wm.slot_of_task(task_id)


def release_task_slot(t: dict) -> str | None:
    """Release whatever slot task ``t`` holds. Returns freed slot or ``None``."""
    return wm.release_task(t["id"])


def append_note(t: dict, msg: str) -> None:
    t.setdefault("notes", []).append(f"{NOW}: {msg}")


def pr_entries(t: dict) -> list[dict]:
    """Unified accessor — yields all PR entries whether legacy or list-based."""
    if t.get("reviews"):
        return t["reviews"]
    if t.get("review"):
        return [t["review"]]
    return []


def pr_resolved(p: dict) -> bool:
    """A PR no longer needs review once it is merged or closed-without-merge."""
    return bool(p.get("merged") or p.get("closed"))


def remove_pr_entry(t: dict, p: dict) -> None:
    """Drop a single PR entry from a review task (list- or legacy single-based)."""
    if t.get("reviews"):
        t["reviews"] = [x for x in t["reviews"] if x is not p]
    elif t.get("review") is p:
        t.pop("review", None)


# ----------------------------------------------------------------------------
# Review-status consolidation
# ----------------------------------------------------------------------------
# A code-review task can hold several PRs, each with its own per-PR
# ``review_status`` (merged / approved / changes-requested / commented / …).
# The *task* must expose exactly one overall status. ``consolidate_review_status``
# collapses the per-PR states into that single value using this precedence:
#
#   merged                — every PR merged (whole task landed)
#   approved              — every PR approved-or-merged, but not all merged
#                           (our review is done; some PRs still awaiting merge)
#   waiting-for-author    — at least one PR still carries our feedback
#                           (commented / changes-requested) and is not approved
#                           → the ball is with the author
#   waiting-for-re-review — author bounced it back; the ball is with the reviewer
#   in-progress           — a review is actively underway
#   pending               — nothing started yet
#
# ``waiting-for-author`` is a *consolidated-only* status: it never lives on an
# individual PR (those stay granular as commented / changes-requested).

CONSOLIDATED_REVIEW_STATUSES = [
    "pending",
    "in-progress",
    "waiting-for-re-review",
    "waiting-for-author",
    "approved",
    "merged",
]

# Consolidated review status → generic task status.
REVIEW_CONSOLIDATED_TO_TASK = {
    "pending": "todo",
    "in-progress": "in-progress",
    "waiting-for-author": "blocked",
    "waiting-for-re-review": "todo",
    "approved": "in-progress",   # reviewer done; task stays open until every PR merges
    "merged": "done",
}


def consolidate_review_status(prs: list[dict]) -> str:
    """Collapse per-PR review statuses into one overall task review status."""
    ss = [p.get("review_status") or "pending" for p in prs]
    if not ss:
        return "pending"
    if all(s == "merged" for s in ss):
        return "merged"
    if all(s in ("approved", "merged") for s in ss):
        return "approved"
    if any(s in ("commented", "changes-requested") for s in ss):
        return "waiting-for-author"
    if any(s == "waiting-for-re-review" for s in ss):
        return "waiting-for-re-review"
    if any(s == "in-progress" for s in ss):
        return "in-progress"
    return "pending"


# ----------------------------------------------------------------------------
# GitHub / git helpers (use gh CLI + git ls-remote, no API keys needed)
# ----------------------------------------------------------------------------

PR_URL_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)")


def parse_pr_url(url: str) -> tuple[str, str, int]:
    m = PR_URL_RE.search(url)
    if not m:
        raise ValueError(f"not a PR URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


def gh_pr_json(owner: str, repo: str, num: int, fields: str) -> dict:
    """Run `gh pr view` returning JSON or raise."""
    out = subprocess.run(
        ["gh", "pr", "view", str(num), "-R", f"{owner}/{repo}", "--json", fields],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh pr view failed for {owner}/{repo}#{num}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def resolve_branch(owner: str, repo: str, num: int, fallback_sha: str | None = None) -> str | None:
    """Resolve the PR head branch via gh, or fall back to git ls-remote sha lookup."""
    try:
        data = gh_pr_json(owner, repo, num, "headRefName")
        b = data.get("headRefName")
        if b:
            return b
    except Exception:
        pass
    # Fallback: ls-remote PR head, then match the sha against refs/heads/*
    try:
        ls = subprocess.run(
            ["git", "ls-remote", f"https://github.com/{owner}/{repo}",
             f"refs/pull/{num}/head"],
            capture_output=True, text=True, check=True,
        )
        sha = ls.stdout.split()[0] if ls.stdout.strip() else None
        if not sha:
            return None
        ls2 = subprocess.run(
            ["git", "ls-remote", f"https://github.com/{owner}/{repo}", "refs/heads/*"],
            capture_output=True, text=True, check=True,
        )
        for line in ls2.stdout.splitlines():
            if line.startswith(sha):
                ref = line.split()[1]
                return ref.removeprefix("refs/heads/")
    except subprocess.CalledProcessError:
        pass
    return None


def fetch_pr_details(url: str) -> dict:
    """Return dict with owner/repo/pr_number/author/branch/files_changed/additions/deletions/title/body."""
    owner, repo, num = parse_pr_url(url)
    fields = "title,body,author,headRefName,additions,deletions,changedFiles,state,mergedAt,closed"
    try:
        data = gh_pr_json(owner, repo, num, fields)
    except Exception as e:
        # Minimal fallback
        branch = resolve_branch(owner, repo, num)
        return {
            "owner": owner, "repo": repo, "pr_number": num,
            "author": None, "branch": branch,
            "files_changed": None, "additions": None, "deletions": None,
            "title": None, "body": None,
            "_warning": str(e),
        }
    author = (data.get("author") or {}).get("login")
    branch = data.get("headRefName") or resolve_branch(owner, repo, num)
    return {
        "owner": owner, "repo": repo, "pr_number": num,
        "author": author, "branch": branch,
        "files_changed": data.get("changedFiles"),
        "additions": data.get("additions"),
        "deletions": data.get("deletions"),
        "title": data.get("title"),
        "body": data.get("body") or "",
        "state": data.get("state"),
        "merged": bool(data.get("mergedAt")),
        "closed": bool(data.get("closed")),
    }


def estimate_review_complexity(files: int | None, lines: int | None) -> tuple[str, float]:
    f = files or 0
    l = lines or 0
    if f < 5 and l < 100:
        return ("trivial", 0.5)
    if f <= 15 and l <= 500:
        return ("small", 1.0)
    if f <= 30 and l <= 1000:
        return ("medium", 2.0)
    return ("large", 3.5)


# ----------------------------------------------------------------------------
# Subcommand: add
# ----------------------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> int:
    category = args.category or "misc"
    if category not in CATEGORIES:
        print(f"[tm] invalid category: {category}", file=sys.stderr)
        return 2
    if category == "code-review":
        print("[tm] use 'add-review <pr_url>' for code-review tasks", file=sys.stderr)
        return 2

    complexity = args.complexity or "medium"
    cost = args.cost if args.cost is not None else COMPLEXITY_DEFAULT_COST[complexity]

    # Workspace auto-assignment (delegated to workspace-manager)
    ws_name = args.workspace or (None if args.no_workspace else detect_workspace(args.cwd))
    status = "todo"
    notes = [f"{NOW}: initial submission"]
    assigned_ws = None
    skip_reason = None

    if args.backlog:
        status = "backlog"
        ws_name = None
    elif ws_name:
        root = wm.normalize_slot(ws_name)
        holder = wm.get_holder(root)
        if holder is None:
            assigned_ws = root
            notes.append(f"{NOW}: auto-assigned to workspace {root}")
        else:
            status = "backlog"
            skip_reason = f"workspace '{root}' occupied by {wm.holder_summary(holder)}"
            ws_name = None

    with task_store.transaction() as d:
        tid = f"T-{d['next_id']:03d}"
        task = {
            "id": tid,
            "title": args.title,
            "description": args.description or "",
            "category": category,
            "priority": args.priority or "medium",
            "status": status,
            "complexity": complexity,
            "cost_hours": cost,
            "context": args.context or "",
            "jira": args.jira,
            "created": TODAY,
            "updated": TODAY,
            "completed": None,
            "notes": notes,
        }
        d["tasks"].append(task)
        d["next_id"] += 1
    if assigned_ws:
        wm.occupy_task(assigned_ws, tid, detail=ws_name)

    summary = f"{tid} | {complexity} | {cost}h | status={status}"
    if assigned_ws:
        summary += f" | ws={assigned_ws}"
    elif skip_reason:
        summary += f" | {skip_reason}"
    print(summary)
    return 0


# ----------------------------------------------------------------------------
# Subcommand: add-review
# ----------------------------------------------------------------------------

def cmd_add_review(args: argparse.Namespace) -> int:
    if not args.urls:
        print("[tm] add-review needs at least one PR URL", file=sys.stderr)
        return 2
    today = TODAY

    reviews = []
    titles = []
    authors = []
    jira_keys = []
    total_files = 0
    total_lines = 0
    warnings = []

    for url in args.urls:
        info = fetch_pr_details(url)
        if "_warning" in info:
            warnings.append(f"{info['owner']}/{info['repo']}#{info['pr_number']}: {info['_warning']}")
        reviews.append({
            "pr_url": url,
            "owner": info["owner"],
            "repo": info["repo"],
            "pr_number": info["pr_number"],
            "author": info["author"],
            "branch": info["branch"],
            "files_changed": info["files_changed"],
            "additions": info["additions"],
            "deletions": info["deletions"],
            "review_status": "pending",
            "submitted_at": None,
            "merged": info.get("merged", False),
            "closed": info.get("closed", False),
        })
        if info.get("title"):
            titles.append(info["title"])
        if info.get("author"):
            authors.append(info["author"])
        if info.get("files_changed"):
            total_files += info["files_changed"]
        total_lines += (info.get("additions") or 0) + (info.get("deletions") or 0)
        # Pull jira from title like "[XXX-123] ..."
        if info.get("title"):
            m = re.match(r"\s*\[([A-Z][A-Z0-9]+-\d+)\]", info["title"])
            if m:
                jira_keys.append(m.group(1))

    # Title
    if args.title:
        title = args.title
    elif titles:
        first = titles[0]
        if len(first) > 50:
            first = first[:47] + "..."
        title = f"Review: {first}"
    else:
        title = f"Review: PR{'s' if len(reviews) > 1 else ''} " + ",".join(
            f"{r['repo']}#{r['pr_number']}" for r in reviews
        )

    description = args.description or (titles[0] if len(titles) == 1 else
                                       " | ".join(titles[:2]))
    context = args.context or (
        args.urls[0] if len(args.urls) == 1 else "multi-PR review"
    )
    jira = args.jira or (jira_keys[0] if jira_keys else None)

    if args.complexity:
        complexity = args.complexity
        cost = args.cost if args.cost is not None else COMPLEXITY_DEFAULT_COST[complexity]
    else:
        complexity, cost = estimate_review_complexity(total_files, total_lines)
        if args.cost is not None:
            cost = args.cost

    with task_store.transaction() as d:
        tid = f"T-{d['next_id']:03d}"
        task = {
            "id": tid,
            "title": title,
            "description": description,
            "category": "code-review",
            "priority": args.priority or "medium",
            "status": "todo",
            "complexity": complexity,
            "cost_hours": cost,
            "context": context,
            "jira": jira,
            "created": today,
            "updated": today,
            "completed": None,
            "notes": [f"{NOW}: initial submission" +
                      (f" ({len(reviews)} PRs)" if len(reviews) > 1 else "")],
            "reviews": reviews,
        }
        d["tasks"].append(task)
        d["next_id"] += 1

    print(f"{tid} | code-review | {complexity} | {cost}h | {len(reviews)} PR(s)")
    for r in reviews:
        b = r["branch"] or "BRANCH_UNRESOLVED"
        print(f"  {r['repo']}#{r['pr_number']} by {r['author']} | {b} "
              f"| {r['files_changed']} files +{r['additions']}/-{r['deletions']}")
    for w in warnings:
        print(f"  WARN: {w}")
    return 0


# ----------------------------------------------------------------------------
# Subcommand: list / list-reviews
# ----------------------------------------------------------------------------

def _pri_rank(p: str) -> int:
    try:
        return PRIORITIES.index(p)
    except ValueError:
        return 99


def cmd_list(args: argparse.Namespace) -> int:
    d = load()
    tasks = list(d["tasks"])
    if args.status:
        tasks = [t for t in tasks if t.get("status") == args.status]
    else:
        # Default: hide done & cancelled
        tasks = [t for t in tasks if t.get("status") not in ("done", "cancelled")]
    if args.category:
        tasks = [t for t in tasks if t.get("category") == args.category]
    if args.priority:
        tasks = [t for t in tasks if t.get("priority") == args.priority]

    tasks.sort(key=lambda t: (_pri_rank(t.get("priority", "low")),
                              t.get("category", ""), t.get("id", "")))

    if args.json:
        print(json.dumps(tasks, indent=2))
        return 0

    holders = wm.all_holders()
    tid_slot = {h["task_id"]: s for s, h in holders.items() if h.get("type") == "task"}

    rows = [("ID", "Title", "Feature", "Cat", "Pri", "Status", "Cx", "Est", "Age", "Worked", "WS")]
    for t in tasks:
        title = t["title"]
        if len(title) > 50:
            title = title[:47] + "..."
        rows.append((
            t["id"],
            title,
            feature_blurb(t),
            t["category"][:4],
            t["priority"][:4],
            t["status"][:11],
            (t.get("complexity") or "")[:6],
            f"{t.get('cost_hours', 0)}h",
            f"{days_since(t.get('created', TODAY))}d",
            last_worked_label(t),
            tid_slot.get(t["id"], "—"),
        ))
    _print_table(rows)

    active = sum(1 for t in tasks if t["status"] == "in-progress")
    blocked = sum(1 for t in tasks if t["status"] == "blocked")
    done = sum(1 for t in d["tasks"] if t["status"] == "done")
    total_est = sum(t.get("cost_hours", 0) for t in tasks)
    print(f"\n{active} active | {blocked} blocked | {done} done | total est: {total_est}h remaining")

    if holders:
        parts = []
        for s in sorted(holders):
            h = holders[s]
            parts.append(f"{s} → {h['task_id']}" if h.get("type") == "task"
                         else f"{s} → {h.get('label')} [{h.get('tag', 'other')}]")
        print("Workspaces: " + ", ".join(parts))
    return 0


def cmd_list_reviews(args: argparse.Namespace) -> int:
    d = load()
    rows = [("ID", "PRs", "Repos", "Authors", "Status", "Files", "+/-", "Age", "Worked", "Feature")]
    n = 0
    for t in d["tasks"]:
        if t.get("category") != "code-review":
            continue
        if t.get("status") in ("done", "cancelled"):
            continue
        prs = pr_entries(t)
        if all(pr_resolved(p) for p in prs):
            continue
        n += 1
        repos = ",".join(sorted({p["repo"] for p in prs}))
        authors = ",".join(sorted({p.get("author") or "?" for p in prs}))
        files = sum((p.get("files_changed") or 0) for p in prs)
        add = sum((p.get("additions") or 0) for p in prs)
        dele = sum((p.get("deletions") or 0) for p in prs)
        pr_nums = ",".join(f"#{p['pr_number']}" for p in prs)
        rows.append((
            t["id"], pr_nums, repos, authors, consolidate_review_status(prs),
            str(files), f"+{add}/-{dele}", f"{days_since(t.get('created', TODAY))}d",
            last_worked_label(t),
            feature_blurb(t),
        ))
    if n == 0:
        print("no active review tasks")
        return 0
    _print_table(rows)
    return 0


def _print_table(rows: list[tuple]) -> None:
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    for idx, r in enumerate(rows):
        line = "| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)) + " |"
        print(line)
        if idx == 0:
            print(sep)


def feature_blurb(t: dict, maxlen: int = 70) -> str:
    """Short human-readable feature label for a task.

    Prefers the description's first sentence; falls back to the title stripped
    of any leading "Review: " prefix. Returns "" if nothing useful is found.
    """
    desc = (t.get("description") or "").strip()
    if desc:
        # First sentence: split on ". " but keep terse
        first = desc.split(". ", 1)[0]
        # Collapse newlines / repeated whitespace
        first = re.sub(r"\s+", " ", first).strip()
    else:
        title = (t.get("title") or "").strip()
        first = re.sub(r"^Review:\s*", "", title, flags=re.IGNORECASE)
    if len(first) > maxlen:
        first = first[: maxlen - 1].rstrip() + "…"
    return first


# ----------------------------------------------------------------------------
# Subcommand: update / done / block / cancel
# ----------------------------------------------------------------------------

def _set_status(d: dict, ident: str, new_status: str, note: str | None) -> int:
    if new_status not in STATUSES:
        print(f"[tm] invalid status: {new_status}", file=sys.stderr)
        return 2
    t = find_task(d, ident)
    if not t:
        print(f"[tm] task not found: {ident}", file=sys.stderr)
        return 1
    old = t["status"]
    # Code-review tasks are only truly "done" once every PR has merged. Marking
    # one done early means the reviewer is finished → approve the open PRs and keep
    # the task in-progress until the merges actually land.
    if new_status == "done" and t.get("category") == "code-review":
        prs = pr_entries(t)
        if prs and not all(p.get("review_status") == "merged" for p in prs):
            for p in prs:
                if p.get("review_status") != "merged":
                    p["review_status"] = "approved"
                    p["submitted_at"] = TODAY
            consolidated = consolidate_review_status(prs)
            derived = REVIEW_CONSOLIDATED_TO_TASK[consolidated]
            t["status"] = derived
            t["updated"] = TODAY
            if derived in ("blocked", "todo"):
                release_task_slot(t)
            append_note(t, note or f"review approved; awaiting merge (overall: {consolidated})")
            save(d)
            print(f"{t['id']} {old} → {derived} (review approved; not merged yet)")
            return 0
    t["status"] = new_status
    t["updated"] = TODAY
    if new_status == "done":
        t["completed"] = TODAY
        release_task_slot(t)
    elif new_status == "cancelled":
        release_task_slot(t)
    if note:
        append_note(t, note)
    else:
        append_note(t, f"status: {old} → {new_status}")
    save(d)
    print(f"{t['id']} {old} → {new_status}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    d = load()
    return _set_status(d, args.id, args.status, args.note)


def cmd_done(args: argparse.Namespace) -> int:
    d = load()
    return _set_status(d, args.id, "done", args.note)


def cmd_block(args: argparse.Namespace) -> int:
    d = load()
    return _set_status(d, args.id, "blocked", args.note)


def cmd_cancel(args: argparse.Namespace) -> int:
    d = load()
    return _set_status(d, args.id, "cancelled", args.note)


# ----------------------------------------------------------------------------
# Subcommand: assign
# ----------------------------------------------------------------------------

def cmd_assign(args: argparse.Namespace) -> int:
    d = load()
    t = find_task(d, args.id)
    if not t:
        print(f"[tm] task not found: {args.id}", file=sys.stderr)
        return 1
    ws = args.workspace
    ok, msg, displaced = wm.occupy_task(ws, t["id"], detail=ws, force=args.force)
    if not ok:
        print(f"[tm] {msg}. Free the old hold and verify a clean baseline before retrying.", file=sys.stderr)
        return 1
    root = wm.normalize_slot(ws)
    if displaced and displaced.get("type") == "task":
        other = find_task(d, displaced["task_id"])
        if other:
            append_note(other, f"workspace {root} released to {t['id']}")
    elif displaced:
        print(f"[tm] displaced {wm.holder_summary(displaced)}")
    if t["status"] == "todo":
        t["status"] = "in-progress"
    t["updated"] = TODAY
    append_note(t, f"assigned to workspace {root}")
    save(d)
    print(f"{t['id']} → workspace {root} ({t['status']})")
    return 0


# ----------------------------------------------------------------------------
# Subcommand: release
# ----------------------------------------------------------------------------

def cmd_release(args: argparse.Namespace) -> int:
    """Free a task's workspace slot without forcing a status change.

    Used when you stop working in a workspace but the task itself is not done
    (e.g. review 'stop', switching contexts). Leaves ``status`` untouched.
    """
    d = load()
    t = find_task(d, args.id)
    if not t:
        print(f"[tm] task not found: {args.id}", file=sys.stderr)
        return 1
    prev = release_task_slot(t)
    if not prev:
        print(f"{t['id']} holds no workspace")
        return 0
    t["updated"] = TODAY
    append_note(t, args.note or f"released workspace {prev}")
    save(d)
    print(f"{t['id']} released workspace {prev} ({t['status']})")
    return 0


# ----------------------------------------------------------------------------
# Subcommand: workspaces
# ----------------------------------------------------------------------------

def cmd_workspaces(args: argparse.Namespace) -> int:
    d = load()
    project = args.project or DEFAULT_PROJECT
    wd = wm.load()
    slots = wm.canonical_slots(project)
    for s in wd["slots"]:
        if s not in slots and wm.get_holder(s, wd):
            slots.append(s)
    rows = [("Workspace", "Holder", "Kind", "Status", "Since")]
    occupied = 0
    for ws in slots:
        holder = wm.get_holder(ws, wd)
        if not holder:
            rows.append((ws, "—", "—", "—", "—"))
            continue
        occupied += 1
        since = holder.get("since") or "—"
        if holder.get("type") == "task":
            t = find_task(d, holder["task_id"])
            hid = holder["task_id"]
            title = (t["title"] if t else "?")
            if len(title) > 40:
                title = title[:37] + "..."
            rows.append((ws, f"{hid} · {title}", "task",
                         (t["status"] if t else "?"), since))
        else:
            label = holder.get("label", "")
            if len(label) > 44:
                label = label[:41] + "..."
            rows.append((ws, label, f"adhoc/{holder.get('tag', 'other')}", "—", since))
    _print_table(rows)
    print(f"\n{occupied} / {len(slots)} workspaces occupied")
    return 0


# ----------------------------------------------------------------------------
# Subcommand: remove
# ----------------------------------------------------------------------------

def edit_task_fields(d: dict, ident: str, *, title: str | None = None,
                     description: str | None = None) -> tuple[bool, str]:
    """Set title/description on a task in snapshot ``d``; other fields untouched."""
    t = find_task(d, ident)
    if not t:
        return False, f"task not found: {ident}"
    changes = []
    if title is not None:
        title = title.strip()
        if not title:
            return False, "title must not be empty"
        if title != t.get("title"):
            t["title"] = title
            changes.append("title")
    if description is not None and description != t.get("description", ""):
        t["description"] = description
        changes.append("description")
    if not changes:
        return False, "no changes"
    t["updated"] = TODAY
    append_note(t, "edit: " + ", ".join(changes))
    return True, f"{t['id']} updated: " + ", ".join(changes)


def edit_task(ident: str, *, title: str | None = None,
              description: str | None = None) -> tuple[bool, str]:
    """Owner API for title/description edits (CLI and portal route here)."""
    with task_store.transaction() as d:
        ok, msg = edit_task_fields(d, ident, title=title, description=description)
        if ok:
            task_store.save(d)
    return ok, msg


def cmd_edit(args: argparse.Namespace) -> int:
    if args.title is None and args.description is None:
        print("[tm] nothing to edit: pass --title and/or --description", file=sys.stderr)
        return 2
    ok, msg = edit_task(args.id, title=args.title, description=args.description)
    print(msg if ok else f"[tm] {msg}", file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def cmd_remove(args: argparse.Namespace) -> int:
    d = load()
    t = find_task(d, args.id)
    if not t:
        print(f"[tm] task not found: {args.id}", file=sys.stderr)
        return 1
    if not args.yes:
        print(f"[tm] refusing without --yes. Would remove {t['id']}: {t['title']}",
              file=sys.stderr)
        return 1
    d["tasks"] = [x for x in d["tasks"] if x["id"] != t["id"]]
    save(d)
    print(f"removed {t['id']}")
    return 0


# ----------------------------------------------------------------------------
# Subcommand: review-update (manual)
# ----------------------------------------------------------------------------


def cmd_review_update(args: argparse.Namespace) -> int:
    if args.status not in REVIEW_STATUSES:
        print(f"[tm] invalid review status: {args.status}", file=sys.stderr)
        return 2
    d = load()
    t = find_task(d, args.id)
    if not t:
        print(f"[tm] task not found: {args.id}", file=sys.stderr)
        return 1
    prs = pr_entries(t)
    if not prs:
        print(f"[tm] {t['id']} has no PR entries", file=sys.stderr)
        return 1
    # Pick target entry
    if args.pr:
        target = [p for p in prs if p.get("pr_number") == args.pr
                  or p.get("repo") == args.pr]
        if not target:
            print(f"[tm] no PR matching {args.pr} in {t['id']}", file=sys.stderr)
            return 1
        for p in target:
            p["review_status"] = args.status
            if args.status in ("approved", "merged"):
                if args.status == "merged":
                    p["merged"] = True
            if args.status in ("commented", "changes-requested", "approved"):
                p["submitted_at"] = TODAY
    else:
        # Apply to all entries
        for p in prs:
            p["review_status"] = args.status
            if args.status == "merged":
                p["merged"] = True
            if args.status in ("commented", "changes-requested", "approved"):
                p["submitted_at"] = TODAY

    # Derive task status from the consolidated review status of ALL PRs
    consolidated = consolidate_review_status(prs)
    new_status = REVIEW_CONSOLIDATED_TO_TASK[consolidated]
    if new_status == "done":
        t["status"] = "done"
        t["completed"] = TODAY
        release_task_slot(t)
    else:
        t["status"] = new_status
        if new_status in ("blocked", "todo"):
            release_task_slot(t)  # free slot when ball is with author / awaiting re-review
    t["updated"] = TODAY
    append_note(t, f"review-update: {args.status}" + (f" on #{args.pr}" if args.pr else " on all PRs")
                + f" (overall: {consolidated})")

    # A code-review task is only ever "done" once every PR has merged; until then
    # it stays tracked (no purge). The merged → done transition is handled above
    # via REVIEW_CONSOLIDATED_TO_TASK, so merged tasks persist as a done record.
    save(d)
    print(f"{t['id']} status={t['status']} overall-review={consolidated} (set {args.status})")
    return 0


# ----------------------------------------------------------------------------
# Subcommand: review-check (purge merged)
# ----------------------------------------------------------------------------

def cmd_review_check(args: argparse.Namespace) -> int:
    d = load()
    completed = []
    updates = []
    keep = []
    for t in d["tasks"]:
        if t.get("category") != "code-review":
            keep.append(t)
            continue
        if t.get("status") in ("done", "cancelled"):
            keep.append(t)
            continue
        prs = pr_entries(t)
        changed = False
        closed_prs = []
        for p in prs:
            if pr_resolved(p):
                continue
            try:
                info = gh_pr_json(p["owner"], p["repo"], p["pr_number"], "state,mergedAt,closed")
            except Exception as e:
                updates.append(f"{t['id']} {p['repo']}#{p['pr_number']}: fetch failed ({e})")
                continue
            merged = bool(info.get("mergedAt"))
            closed = bool(info.get("closed"))
            if merged:
                p["merged"] = True
                p["review_status"] = "merged"
                changed = True
                updates.append(f"{t['id']} {p['repo']}#{p['pr_number']}: merged")
            elif closed:
                # Nothing to review on a closed-without-merge PR — drop it.
                closed_prs.append(p)
                append_note(t, f"{p['repo']}#{p['pr_number']}: closed without merge — removed")
                changed = True
                updates.append(f"{t['id']} {p['repo']}#{p['pr_number']}: closed → removed")
        for p in closed_prs:
            remove_pr_entry(t, p)
        if changed:
            t["updated"] = TODAY
            prs = pr_entries(t)
            # Every PR was closed and removed — nothing left to review.
            if not prs:
                t["status"] = "cancelled"
                release_task_slot(t)
                append_note(t, "all PRs closed without merge — cancelled")
                keep.append(t)
                continue
            # Re-derive the task status from all PRs. The task only becomes done
            # once every PR has merged; otherwise it stays tracked (not purged).
            consolidated = consolidate_review_status(prs)
            new_status = REVIEW_CONSOLIDATED_TO_TASK[consolidated]
            t["status"] = new_status
            if new_status == "done":
                t["completed"] = TODAY
                release_task_slot(t)
                completed.append(t["id"])
            elif new_status in ("blocked", "todo"):
                release_task_slot(t)
        keep.append(t)
    d["tasks"] = keep
    save(d)
    for u in updates:
        print(u)
    if completed:
        print("merged → done: " + ", ".join(completed))
    if not updates and not completed:
        print("no changes")
    return 0


# ----------------------------------------------------------------------------
# Portal review scan (dashboard "Scan reviews" button)
# ----------------------------------------------------------------------------
# Now that the whole team reviews, a PR I own can move without me: someone else
# approves it, comments, requests changes, or the author re-pushes. This probes
# every open review PR through `gh` (pure REST, no model/tokens), maps the live
# GitHub state to the per-PR review_status, and re-derives each task's status.
# Uses the REST API (not GraphQL) — REST has a far larger, separate rate budget,
# so scanning dozens of PRs won't exhaust the tiny GraphQL points quota.

def _gh_api_raw(path: str) -> Any:
    """`gh api <path>` → parsed JSON, raising a concise error on failure."""
    out = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github+json", path],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise RuntimeError((out.stderr.strip() or "gh api failed").splitlines()[-1][:160])
    return json.loads(out.stdout) if out.stdout.strip() else None


def _fetch_pr_review_state(owner: str, repo: str, num: int) -> tuple[dict, list]:
    """Return (pr_object, reviews_list) for a PR via REST (2 cheap requests)."""
    pr = _gh_api_raw(f"/repos/{owner}/{repo}/pulls/{num}")
    if not isinstance(pr, dict):
        raise RuntimeError("unexpected PR payload")
    reviews = _gh_api_raw(f"/repos/{owner}/{repo}/pulls/{num}/reviews?per_page=100")
    return pr, (reviews if isinstance(reviews, list) else [])


def _derive_pr_review_status(p: dict, pr: dict, reviews: list) -> tuple[str, str]:
    """Map a PR's live GitHub state to a per-PR review_status + human detail.

    Returns (new_status, detail). ``new_status`` is one of merged / closed /
    approved / changes-requested / commented, or the PR's existing status when
    nothing observable changed. Decision is derived from the latest
    state-changing review per user (GitHub's own rule): a standing
    CHANGES_REQUESTED wins, else any APPROVED, else COMMENTED.
    """
    if pr.get("merged"):
        return "merged", "merged"
    if pr.get("state") == "closed":
        return "closed", "closed without merge"

    latest: dict[str, tuple[str, str]] = {}   # login -> (submitted_at, state)
    commenters: set[str] = set()
    for r in reviews:
        login = (r.get("user") or {}).get("login")
        state = r.get("state")
        if not login:
            continue
        if state == "COMMENTED":
            commenters.add(login)
            continue
        if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            sub = r.get("submitted_at") or ""
            if login not in latest or sub >= latest[login][0]:
                latest[login] = (sub, state)

    approvers = sorted(u for u, (_, st) in latest.items() if st == "APPROVED")
    changers = sorted(u for u, (_, st) in latest.items() if st == "CHANGES_REQUESTED")

    if changers:
        return "changes-requested", f"changes requested by {', '.join(changers)}"
    if approvers:
        return "approved", f"approved by {', '.join(approvers)}"
    if commenters:
        return "commented", f"commented by {', '.join(sorted(commenters))}"
    return p.get("review_status") or "pending", "no change"


def portal_review_scan(progress=None) -> dict:
    """Re-probe every open review PR and update task statuses from live GitHub.

    Returns a summary: scanned PR count, per-PR change lines, tasks that landed
    (merged→done), and any fetch errors. Mutates and saves the task store.

    ``progress`` (optional) is called as ``progress(i, total, task_id, label,
    status, detail)`` — once with ``status=None`` when a PR is about to be
    fetched, then once with the resolved status ("error"/"nochange"/the new
    review status) — so a caller can stream a live banner.
    """
    d = load()
    pairs = [(t, p) for t in d["tasks"]
             if t.get("category") == "code-review"
             and t.get("status") not in ("done", "cancelled")
             for p in pr_entries(t) if not pr_resolved(p)]
    total = len(pairs)
    scanned = 0
    changed: list[str] = []
    completed: list[str] = []
    cancelled: list[str] = []
    removed: list[str] = []
    errors: list[str] = []
    touched: set[str] = set()
    for t, p in pairs:
        scanned += 1
        label = f"{p['repo']}#{p['pr_number']}"
        if progress:
            progress(scanned, total, t["id"], label, None, "")
        try:
            pr, reviews = _fetch_pr_review_state(
                p["owner"], p["repo"], p["pr_number"])
        except Exception as e:
            msg = str(e).splitlines()[-1][:120]
            errors.append(f"{label}: {msg}")
            if progress:
                progress(scanned, total, t["id"], label, "error", msg)
            continue
        old = p.get("review_status")
        new, detail = _derive_pr_review_status(p, pr, reviews)
        if new == "merged":
            p["merged"] = True
            p["review_status"] = "merged"
        elif new == "closed":
            # A closed-without-merge PR has nothing left to review — drop it
            # from the task entirely rather than leaving a resolved stub.
            remove_pr_entry(t, p)
            removed.append(f"{t['id']} {label}: {detail}")
            append_note(t, f"review-scan: {label} closed without merge — removed")
            touched.add(t["id"])
            if progress:
                progress(scanned, total, t["id"], label, new, detail)
            continue
        elif new != old:
            p["review_status"] = new
        else:
            if progress:
                progress(scanned, total, t["id"], label, "nochange", "no change")
            continue  # nothing observable changed for this PR
        changed.append(f"{t['id']} {label}: {detail}")
        append_note(t, f"review-scan: {label} {detail}")
        touched.add(t["id"])
        if progress:
            progress(scanned, total, t["id"], label, new, detail)
    # Re-derive the task status for every task that had a PR change.
    for t in d["tasks"]:
        if t["id"] not in touched:
            continue
        prs = pr_entries(t)
        t["updated"] = TODAY
        # Every PR was closed and removed — nothing left to review, so the task
        # is cancelled rather than reset to an empty "todo".
        if not prs:
            if t.get("status") not in ("done", "cancelled"):
                t["status"] = "cancelled"
                release_task_slot(t)
                append_note(t, "review-scan: all PRs closed without merge — cancelled")
                cancelled.append(t["id"])
            continue
        consolidated = consolidate_review_status(prs)
        new_status = REVIEW_CONSOLIDATED_TO_TASK[consolidated]
        t["status"] = new_status
        if new_status == "done":
            t["completed"] = TODAY
            release_task_slot(t)
            completed.append(t["id"])
        elif new_status in ("blocked", "todo"):
            release_task_slot(t)
    save(d)
    return {"scanned": scanned, "changed": changed, "removed": removed,
            "completed": completed, "cancelled": cancelled, "errors": errors}


# ----------------------------------------------------------------------------
# Subcommand: review-msg (draft review-request message)
# ----------------------------------------------------------------------------

def cmd_review_msg(args: argparse.Namespace) -> int:
    d = load()
    t = find_task(d, args.id)
    if not t or t.get("category") != "code-review":
        print(f"[tm] no code-review task matching {args.id}", file=sys.stderr)
        return 1
    prs = pr_entries(t)
    title = t["title"].removeprefix("Review: ").strip()
    msg = [f"Review request: {title}"]
    if t.get("jira"):
        msg.append(f"Jira: {t['jira']}")
    msg.append("")
    msg.append("PRs:")
    for p in prs:
        msg.append(f"- {p['pr_url']}  ({p['repo']} | branch: {p.get('branch') or '?'} "
                   f"| {p.get('files_changed')} files +{p.get('additions')}/-{p.get('deletions')})")
    if t.get("description"):
        msg.append("")
        msg.append(t["description"])
    print("\n".join(msg))
    return 0


# ----------------------------------------------------------------------------
# Subcommand: scan-reviews (discover PRs that request my review)
# ----------------------------------------------------------------------------

# Repo families excluded from the review queue regardless of mode.
SCAN_EXCLUDE_REPOS = {"ip.infra.sw.manifests"}
# Jira-key prefixes (families) to drop from the scan, e.g. SDK noise.
SCAN_EXCLUDE_JIRA_PREFIXES = ("MODINFSDKSW",)
# Allowlist files (maintained by the review-mgr skill).
REVIEW_MGR_REFS = Path(os.path.expanduser("~/.copilot/skills/review-mgr/references"))
HSI_REPOS_FILE = REVIEW_MGR_REFS / "hsi-repos.txt"
FW_REPOS_FILE = REVIEW_MGR_REFS / "fw-repos.txt"


def jira_key_from_title(title: str | None) -> str | None:
    if not title:
        return None
    m = re.match(r"\s*\[([A-Z][A-Z0-9]+-\d+)\]", title)
    return m.group(1) if m else None


def load_repo_allowlist(path: Path) -> set[str]:
    """Read owner/repo entries from an allowlist file (empty set if missing)."""
    repos: set[str] = set()
    if not path.exists():
        return repos
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        repos.add(line)
    return repos


def gh_me() -> str | None:
    out = subprocess.run(["gh", "api", "user", "--jq", ".login"],
                         capture_output=True, text=True, check=False)
    return out.stdout.strip() or None if out.returncode == 0 else None


def tracked_pr_index(d: dict) -> dict[tuple[str, str, int], str]:
    """Map (owner, repo, pr_number) -> task id for every PR already in the store."""
    idx: dict[tuple[str, str, int], str] = {}
    for t in d["tasks"]:
        for p in pr_entries(t):
            key = (p.get("owner"), p.get("repo"), p.get("pr_number"))
            if all(k is not None for k in key):
                idx[key] = t["id"]
    return idx


def cmd_scan_reviews(args: argparse.Namespace) -> int:
    """Discover open PRs that request the user's review and emit a candidate list.

    Mechanical only: queries GitHub, filters noise, dedups against the store,
    and groups by Jira key. The review-mgr skill drives the checkbox selection
    and calls add-review on the chosen entries.
    """
    me = gh_me()
    cmd = ["gh", "search", "prs", "--review-requested=@me", "--state=open",
           "--json", "repository,number,title,author,url", "--limit", str(args.limit)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if out.returncode != 0:
        print(f"[tm] gh search failed: {out.stderr.strip()}", file=sys.stderr)
        return 1
    try:
        raw = json.loads(out.stdout or "[]")
    except json.JSONDecodeError as e:
        print(f"[tm] could not parse gh output: {e}", file=sys.stderr)
        return 1

    # Scope: hsi (default) < fw < all. --all wins, then --fw, else hsi.
    if args.all:
        scope, allow = "all", None
    elif args.fw:
        scope, allow = "fw", load_repo_allowlist(FW_REPOS_FILE)
    else:
        scope, allow = "hsi", load_repo_allowlist(HSI_REPOS_FILE)
    if allow is not None and not allow:
        afile = FW_REPOS_FILE if scope == "fw" else HSI_REPOS_FILE
        print(f"[tm] {scope} allowlist empty/missing; pass --all to scan every repo "
              f"or populate {afile}", file=sys.stderr)
        return 1

    d = load()
    tracked = tracked_pr_index(d)

    candidates = []
    for pr in raw:
        repo_full = (pr.get("repository") or {}).get("nameWithOwner") or ""
        repo_name = (pr.get("repository") or {}).get("name") or ""
        owner = repo_full.split("/")[0] if "/" in repo_full else None
        author = (pr.get("author") or {}).get("login")
        title = pr.get("title")
        num = pr.get("number")
        jira = jira_key_from_title(title)

        # Filters
        if me and author == me:
            continue
        if repo_name in SCAN_EXCLUDE_REPOS:
            continue
        if jira and jira.split("-")[0] in SCAN_EXCLUDE_JIRA_PREFIXES:
            continue
        if allow is not None and repo_full not in allow:
            continue

        candidates.append({
            "owner": owner,
            "repo": repo_name,
            "pr_number": num,
            "url": pr.get("url"),
            "title": title,
            "author": author,
            "jira": jira,
            "tracked": tracked.get((owner, repo_name, num)),
        })

    # Group by Jira key (None keys stay standalone)
    groups: dict[str, list[dict]] = {}
    for c in candidates:
        key = c["jira"] or f"__solo__{c['repo']}#{c['pr_number']}"
        groups.setdefault(key, []).append(c)
    for c in candidates:
        gkey = c["jira"] or ""
        c["group"] = gkey if (gkey and len(groups.get(gkey, [])) > 1) else None

    if args.json:
        print(json.dumps({"me": me, "mode": scope,
                          "candidates": candidates}, indent=2))
        return 0

    # Human table
    mode = {"all": "all repos", "fw": "fw repos", "hsi": "HSI repos"}[scope]
    new_n = sum(1 for c in candidates if not c["tracked"])
    print(f"# scan-reviews ({mode}) — {len(candidates)} requesting review, "
          f"{new_n} new")
    for c in candidates:
        mark = f"[tracked {c['tracked']}]" if c["tracked"] else "[new]"
        grp = f" {{{c['group']}}}" if c["group"] else ""
        print(f"  {mark} {c['repo']}#{c['pr_number']} by {c['author']}{grp}")
        print(f"        {c['title']}")
        print(f"        {c['url']}")
    return 0


# ----------------------------------------------------------------------------
# Subcommand: blocked-scan (check whether the ball has come back on my blocked reviews)
# ----------------------------------------------------------------------------

# Heuristic markers in author replies to a review comment.
_PUSHBACK_MARKERS = (
    "won't fix", "wont fix", "wontfix", "by design", "as-is", " as is",
    "intentional", "not applicable", "disagree", "next step", "future work",
    "out of scope",
)
_ADDRESSED_MARKERS = (
    "done", "fixed", "addressed", "applied", "updated", "resolved",
)


def _reply_pushback(body: str) -> bool:
    b = body.lower()
    return any(m in b for m in _PUSHBACK_MARKERS)


def _reply_addressed(body: str) -> bool:
    b = body.lower().strip()
    # Short "done"/"fixed" tokens are the strongest signal.
    if b in {"done", "done.", "fixed", "fixed."}:
        return True
    return any(m in b.split() for m in _ADDRESSED_MARKERS)


def _gh_api(path: str) -> Any:
    """gh api <path> --paginate → parsed JSON (list or dict). Returns [] on failure."""
    out = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github+json", "--paginate", path],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        return []
    text = out.stdout.strip()
    if not text:
        return []
    # --paginate concatenates JSON arrays as ][ — normalize.
    text = text.replace("][", ",")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return []


def _classify_pr(state: str, merged: bool, new_commits: int, threads_replied: int) -> str:
    if merged:
        return "merged"
    if state and state.upper() == "CLOSED":
        return "closed"
    if new_commits > 0:
        return "needs_re_review"
    if threads_replied > 0:
        return "replies_only"
    return "no_activity"


def _scan_one_pr(me: str, owner: str, repo: str, num: int, submitted_at: str) -> dict:
    """Return the compact per-PR summary used by blocked-scan."""
    result: dict[str, Any] = {
        "repo": repo, "pr": num,
        "url": f"https://github.com/{owner}/{repo}/pull/{num}",
        "submitted_at": submitted_at,
        "state": None, "mergeable": None, "head": None,
        "new_commits": 0, "recent_commits": [],
        "my_threads": 0, "threads_with_reply": 0, "replies": [],
        "classification": "unknown", "transitioned": None,
    }
    try:
        pr = gh_pr_json(owner, repo, num,
                        "state,mergeable,isDraft,updatedAt,headRefOid,mergedAt,closed,commits")
    except Exception as e:
        result["error"] = str(e)
        return result
    result["state"] = pr.get("state")
    result["mergeable"] = pr.get("mergeable")
    result["head"] = (pr.get("headRefOid") or "")[:8]
    merged = bool(pr.get("mergedAt"))
    commits = pr.get("commits") or []
    new = [c for c in commits if (c.get("committedDate") or "")[:10] > (submitted_at or "")]
    result["new_commits"] = len(new)
    result["recent_commits"] = [
        {"date": (c.get("committedDate") or "")[:10],
         "sha": (c.get("oid") or "")[:8],
         "msg": (c.get("messageHeadline") or "")[:80]}
        for c in commits[-3:]
    ]

    # Thread replies
    comments = _gh_api(f"/repos/{owner}/{repo}/pulls/{num}/comments?per_page=100")
    if isinstance(comments, list):
        threads: dict[int, list[dict]] = {}
        for c in comments:
            rid = c.get("in_reply_to_id") or c.get("id")
            threads.setdefault(rid, []).append(c)
        for _, msgs in threads.items():
            msgs.sort(key=lambda m: m.get("created_at", ""))
            head = msgs[0]
            if (head.get("user") or {}).get("login") != me:
                continue
            result["my_threads"] += 1
            later = [m for m in msgs[1:]
                     if (m.get("user") or {}).get("login") != me]
            if later:
                result["threads_with_reply"] += 1
                last = later[-1]
                body = (last.get("body") or "").replace("\r", " ").replace("\n", " ")
                head_body = (head.get("body") or "").replace("\r", " ").replace("\n", " ")
                result["replies"].append({
                    "path": head.get("path"),
                    "line": head.get("line") or head.get("original_line"),
                    "by": (last.get("user") or {}).get("login"),
                    "date": (last.get("created_at") or "")[:10],
                    "snippet": body[:140],
                    "my_snippet": head_body[:100],
                    "pushback": _reply_pushback(body),
                    "addressed": _reply_addressed(body),
                })

    result["classification"] = _classify_pr(
        result["state"] or "", merged, result["new_commits"], result["threads_with_reply"])
    return result


def cmd_blocked_scan(args: argparse.Namespace) -> int:
    """For every blocked code-review task, check GitHub for author activity.

    Compact output (~1–3 KB) that the review-mgr skill consumes instead of
    fetching PRs one-by-one. Optional --update auto-transitions PRs with new
    commits since submission to `waiting-for-re-review`.
    """
    d = load()
    me = gh_me()
    if not me:
        print("[tm] could not determine gh login (gh auth status?)", file=sys.stderr)
        return 1
    tasks = [t for t in d["tasks"]
             if t.get("category") == "code-review" and t.get("status") == "blocked"]
    report: list[dict] = []
    for t in tasks:
        entry = {
            "id": t["id"],
            "title": t.get("title", ""),
            "blocked_days": days_since(t.get("updated") or t.get("created") or TODAY),
            "prs": [],
        }
        for p in pr_entries(t):
            if pr_resolved(p):
                continue
            summary = _scan_one_pr(
                me, p.get("owner", ""), p.get("repo", ""),
                int(p.get("pr_number") or 0), p.get("submitted_at") or "",
            )
            entry["prs"].append(summary)
        report.append(entry)

    # Apply --update transitions (per-PR to avoid touching quiet PRs on multi-PR tasks)
    if args.update:
        d = load()  # reload for a clean write path
        transitioned: list[tuple[str, str, int]] = []
        for entry in report:
            t = find_task(d, entry["id"])
            if not t:
                continue
            for pr_sum in entry["prs"]:
                if pr_sum["classification"] != "needs_re_review":
                    continue
                for p in pr_entries(t):
                    if p.get("repo") == pr_sum["repo"] and p.get("pr_number") == pr_sum["pr"]:
                        p["review_status"] = "waiting-for-re-review"
                        pr_sum["transitioned"] = "waiting-for-re-review"
                        transitioned.append((t["id"], pr_sum["repo"], pr_sum["pr"]))
            # Derive task status: consolidate all PRs; a bounced-back PR
            # (waiting-for-re-review) moves the ball back to the reviewer.
            prs = pr_entries(t)
            if t.get("status") == "blocked":
                consolidated = consolidate_review_status(prs)
                target = REVIEW_CONSOLIDATED_TO_TASK[consolidated]
                if target != "blocked":
                    t["status"] = target
                    t["updated"] = TODAY
                    append_note(t, f"blocked-scan: bounced back → {consolidated}")
        save(d)

    if args.json:
        print(json.dumps({"generated_at": NOW, "me": me, "tasks": report}, indent=2))
        return 0

    # Markdown (compact)
    buckets = {"needs_re_review": [], "replies_only": [],
               "no_activity": [], "merged": [], "closed": []}
    for entry in report:
        for pr_sum in entry["prs"]:
            buckets.setdefault(pr_sum["classification"], []).append((entry, pr_sum))

    print(f"# blocked-scan — {TODAY} ({len(report)} blocked task(s), "
          f"{sum(len(e['prs']) for e in report)} PR(s))")

    def _emit(header: str, items: list[tuple[dict, dict]], icon: str,
              show_replies: bool = True) -> None:
        if not items:
            return
        print(f"\n## {icon} {header} ({len(items)})")
        for entry, pr_sum in items:
            tag = ""
            if pr_sum.get("transitioned"):
                tag = f"  → **{pr_sum['transitioned']}**"
            mergeable = pr_sum.get("mergeable")
            m = f" [{mergeable}]" if mergeable and mergeable != "MERGEABLE" else ""
            print(f"- **{entry['id']}** {pr_sum['repo']}#{pr_sum['pr']} "
                  f"(blocked {entry['blocked_days']}d, "
                  f"+{pr_sum['new_commits']} commits since {pr_sum['submitted_at']}, "
                  f"HEAD {pr_sum['head']}){m}{tag}")
            if show_replies and pr_sum["replies"]:
                addr = sum(1 for r in pr_sum["replies"] if r.get("addressed"))
                push = sum(1 for r in pr_sum["replies"] if r.get("pushback"))
                print(f"    threads: {pr_sum['threads_with_reply']}/{pr_sum['my_threads']} "
                      f"replied — {addr} addressed, {push} pushback")
                for r in pr_sum["replies"][:5]:
                    flag = " ⚠pushback" if r.get("pushback") else \
                           (" ✅addressed" if r.get("addressed") else "")
                    line = r.get("line") or "?"
                    print(f"    - `{r['path']}:{line}` by {r['by']} "
                          f"{r['date']}{flag}: {r['snippet']}")

    _emit("Auto-transition candidates (new commits)", buckets["needs_re_review"], "⚠️")
    _emit("Replies only — needs your call", buckets["replies_only"], "💬")
    _emit("No activity", buckets["no_activity"], "✅", show_replies=False)
    _emit("Merged", buckets["merged"], "🔀", show_replies=False)
    _emit("Closed", buckets["closed"], "❌", show_replies=False)
    return 0


# ----------------------------------------------------------------------------
# Subcommand: re-review
# ----------------------------------------------------------------------------

def cmd_re_review(args: argparse.Namespace) -> int:
    """Given one or more PR URLs:
    - Find existing review task(s) that own those PRs.
    - Add any untracked PRs to the matching task (fetching details from GitHub).
    - Transition each affected task to waiting-for-re-review.
    Reports an error for any URL whose task cannot be identified.
    """
    if not args.urls:
        print("[tm] re-review needs at least one PR URL", file=sys.stderr)
        return 2

    d = load()

    # Build lookup: (owner, repo, pr_number) → task
    pr_to_task: dict[tuple, dict] = {}
    for t in d["tasks"]:
        if t.get("category") != "code-review":
            continue
        for p in pr_entries(t):
            key = (p["owner"].lower(), p["repo"].lower(), int(p["pr_number"]))
            pr_to_task[key] = t

    # Classify each input URL
    task_to_new_urls: dict[str, list[str]] = {}   # task_id → new PR URLs to add
    task_objs: dict[str, dict] = {}
    unmatched: list[str] = []

    for url in args.urls:
        try:
            owner, repo, num = parse_pr_url(url)
        except ValueError as e:
            print(f"[tm] {e}", file=sys.stderr)
            return 2

        key = (owner.lower(), repo.lower(), num)
        task = pr_to_task.get(key)

        if task:
            # Already tracked — just flag the task
            task_objs[task["id"]] = task
            task_to_new_urls.setdefault(task["id"], [])
        else:
            # Not tracked — try to infer which task it belongs to by repo+owner
            candidates = [
                t for t in d["tasks"]
                if t.get("category") == "code-review"
                and t.get("status") not in ("done", "cancelled")
                and any(
                    p["owner"].lower() == owner.lower() or p["repo"].lower() == repo.lower()
                    for p in pr_entries(t)
                )
            ]
            # Prefer a task that shares the same author (fetch quick author info)
            if len(candidates) == 1:
                task = candidates[0]
                task_objs[task["id"]] = task
                task_to_new_urls.setdefault(task["id"], []).append(url)
            elif len(candidates) > 1:
                # Narrow by JIRA key in PR title if possible
                try:
                    info = fetch_pr_details(url)
                    jira_m = re.match(r"\s*\[([A-Z][A-Z0-9]+-\d+)\]", info.get("title") or "")
                    jira_key = jira_m.group(1) if jira_m else None
                except Exception:
                    jira_key = None
                matched = (
                    [c for c in candidates if c.get("jira") == jira_key]
                    if jira_key else []
                )
                if len(matched) == 1:
                    task = matched[0]
                    task_objs[task["id"]] = task
                    task_to_new_urls.setdefault(task["id"], []).append(url)
                else:
                    ids = ", ".join(c["id"] for c in candidates)
                    print(f"[tm] {owner}/{repo}#{num}: ambiguous — matches tasks {ids}; "
                          f"add manually with 'add-review' or specify task ID",
                          file=sys.stderr)
                    unmatched.append(url)
            else:
                print(f"[tm] {owner}/{repo}#{num}: not found in any review task — "
                      f"use 'add-review' to create a new task", file=sys.stderr)
                unmatched.append(url)

    if unmatched and not task_objs:
        return 1

    rc = 0
    for tid, task in task_objs.items():
        prs = pr_entries(task)

        # Add any new PRs
        new_urls = task_to_new_urls.get(tid, [])
        for url in new_urls:
            try:
                info = fetch_pr_details(url)
            except Exception as e:
                print(f"[tm] failed to fetch {url}: {e}", file=sys.stderr)
                rc = 1
                continue
            entry = {
                "pr_url": url,
                "owner": info["owner"],
                "repo": info["repo"],
                "pr_number": info["pr_number"],
                "author": info["author"],
                "branch": info["branch"],
                "files_changed": info["files_changed"],
                "additions": info["additions"],
                "deletions": info["deletions"],
                "review_status": "waiting-for-re-review",
                "submitted_at": None,
                "merged": info.get("merged", False),
            }
            if "reviews" not in task:
                task["reviews"] = prs if prs else []
            task["reviews"].append(entry)
            append_note(task, f"re-review: added {info['repo']}#{info['pr_number']}")
            print(f"  added {info['repo']}#{info['pr_number']} "
                  f"({info['files_changed']} files +{info['additions']}/-{info['deletions']})")

        # Transition all PR entries to waiting-for-re-review
        for p in pr_entries(task):
            p["review_status"] = "waiting-for-re-review"
        task["status"] = "todo"
        release_task_slot(task)
        task["updated"] = TODAY
        append_note(task, "re-review: waiting-for-re-review on all PRs")
        print(f"{tid} status=todo review_status=waiting-for-re-review "
              f"({len(pr_entries(task))} PR(s))")

    save(d)
    return rc

# ----------------------------------------------------------------------------
# Subcommand: dashboard / serve — the portal runtime lives in
# team-portal/portal.py; these are lazy delegates (legacy CLI).
# ----------------------------------------------------------------------------

def apply_task_edit(ident: str, payload: dict) -> tuple[bool, str]:
    """Apply a constrained set of edits to a task, enforcing store rules.

    Allowed keys: status, priority, category, complexity, cost_hours,
    workspace, review_status, add_note. ``review_status`` applies to every PR
    of a code-review task and re-derives the task status. Returns (ok, message).
    """
    d = load()
    t = find_task(d, ident)
    if not t:
        return False, f"task not found: {ident}"
    if (payload.get("expected_revision") is not None
            and payload["expected_revision"] != task_revision(t)):
        return False, "Task revision conflict; reload before saving"

    changes: list[str] = []

    if "status" in payload:
        new = payload["status"]
        if new not in STATUSES:
            return False, f"invalid status: {new}"
        if new != t["status"]:
            old = t["status"]
            t["status"] = new
            if new == "done":
                t["completed"] = TODAY
                release_task_slot(t)
            elif new == "cancelled":
                release_task_slot(t)
            changes.append(f"status {old} → {new}")

    if "priority" in payload:
        new = payload["priority"]
        if new not in PRIORITIES:
            return False, f"invalid priority: {new}"
        if new != t.get("priority"):
            changes.append(f"priority {t.get('priority')} → {new}")
            t["priority"] = new

    if "category" in payload:
        new = payload["category"]
        if new not in CATEGORIES:
            return False, f"invalid category: {new}"
        if new == "code-review" and t.get("category") != "code-review":
            return False, "cannot convert a task to code-review via the dashboard"
        if t.get("category") == "code-review" and new != "code-review":
            return False, "cannot drop code-review category (would orphan reviews[])"
        if new != t.get("category"):
            changes.append(f"category {t.get('category')} → {new}")
            t["category"] = new

    if "complexity" in payload:
        new = payload["complexity"] or None
        if new is not None and new not in COMPLEXITIES:
            return False, f"invalid complexity: {new}"
        if new != t.get("complexity"):
            changes.append(f"complexity {t.get('complexity')} → {new}")
            t["complexity"] = new

    if "cost_hours" in payload:
        try:
            new = float(payload["cost_hours"])
        except (TypeError, ValueError):
            return False, "cost_hours must be a number"
        if new != t.get("cost_hours"):
            changes.append(f"cost {t.get('cost_hours')} → {new}")
            t["cost_hours"] = new

    if "workspace" in payload:
        ws = (payload["workspace"] or "").strip()
        cur_slot = task_slot(t["id"])
        if ws == "":
            if cur_slot:
                changes.append(f"workspace {cur_slot} → (cleared)")
                release_task_slot(t)
        else:
            root = wm.normalize_slot(ws)
            if root != cur_slot:
                ok, msg, _ = wm.occupy_task(root, t["id"], detail=ws, force=False)
                if not ok:
                    return False, msg
                if t["status"] == "todo":
                    t["status"] = "in-progress"
                changes.append(f"workspace → {root}")

    if "review_status" in payload:
        new = payload["review_status"]
        if new not in REVIEW_STATUSES:
            return False, f"invalid review status: {new}"
        if t.get("category") != "code-review":
            return False, "review_status only applies to code-review tasks"
        prs = pr_entries(t)
        if not prs:
            return False, "task has no PR entries"
        # Apply to all PRs (mirrors `review-update` without --pr).
        for p in prs:
            p["review_status"] = new
            if new == "merged":
                p["merged"] = True
            if new in ("commented", "changes-requested", "approved"):
                p["submitted_at"] = TODAY
        # Derive the task status from the consolidated review status of all PRs.
        consolidated = consolidate_review_status(prs)
        new_task_status = REVIEW_CONSOLIDATED_TO_TASK[consolidated]
        if new_task_status != t.get("status"):
            changes.append(f"status {t.get('status')} → {new_task_status}")
        t["status"] = new_task_status
        if new_task_status == "done":
            t["completed"] = TODAY
            release_task_slot(t)
        elif new_task_status in ("blocked", "todo"):
            release_task_slot(t)  # ball with author / awaiting re-review
        changes.append(f"review-status → {new} (overall: {consolidated})")

    note = (payload.get("add_note") or "").strip()
    if note:
        append_note(t, note)

    if not changes and not note:
        return False, "no changes"

    t["updated"] = TODAY
    if changes:
        append_note(t, "dashboard: " + "; ".join(changes))
    save(d)
    return True, "; ".join(changes) if changes else "note added"



def _portal():
    if __name__ == "__main__":
        sys.modules.setdefault("tm", sys.modules["__main__"])
    pdir = str(Path(__file__).resolve().parents[2] / "team-portal")
    if pdir not in sys.path:
        sys.path.insert(0, pdir)
    import portal
    return portal


def cmd_dashboard(args) -> int:
    return _portal().cmd_dashboard(args)


def cmd_serve(args) -> int:
    return _portal().cmd_serve(args)



# ----------------------------------------------------------------------------
# Argparse wiring
# ----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tm", description="task-manager CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    # add
    a = sub.add_parser("add", help="add a non-review task")
    a.add_argument("title")
    a.add_argument("-d", "--description", default="")
    a.add_argument("-c", "--category", choices=sorted(CATEGORIES - {"code-review"}))
    a.add_argument("-p", "--priority", choices=PRIORITIES)
    a.add_argument("--complexity", choices=COMPLEXITIES)
    a.add_argument("--cost", type=float)
    a.add_argument("--context", default="")
    a.add_argument("--jira")
    a.add_argument("--workspace", help="explicit workspace name")
    a.add_argument("--no-workspace", action="store_true",
                   help="disable auto workspace detection")
    a.add_argument("--backlog", action="store_true",
                   help="force backlog status (someday/maybe)")
    a.add_argument("--cwd", help="override cwd for workspace detection")
    a.set_defaults(func=cmd_add)

    # add-review
    a = sub.add_parser("add-review", help="add a code-review task from one or more PR URLs")
    a.add_argument("urls", nargs="+")
    a.add_argument("-t", "--title")
    a.add_argument("-d", "--description")
    a.add_argument("-p", "--priority", choices=PRIORITIES)
    a.add_argument("--complexity", choices=COMPLEXITIES)
    a.add_argument("--cost", type=float)
    a.add_argument("--context")
    a.add_argument("--jira")
    a.set_defaults(func=cmd_add_review)

    # list
    a = sub.add_parser("list", help="list tasks")
    a.add_argument("--status", choices=sorted(STATUSES))
    a.add_argument("--category", choices=sorted(CATEGORIES))
    a.add_argument("--priority", choices=PRIORITIES)
    a.add_argument("--json", action="store_true")
    a.set_defaults(func=cmd_list)

    # list-reviews
    a = sub.add_parser("list-reviews", help="list active review tasks")
    a.set_defaults(func=cmd_list_reviews)

    # dashboard
    a = sub.add_parser("dashboard", help="generate a self-contained HTML dashboard of all tasks")
    a.add_argument("-o", "--output", help="output path (default ~/.copilot-private/team-portal/state/dashboard.html)")
    a.set_defaults(func=cmd_dashboard)

    # serve
    a = sub.add_parser("serve", help="serve an editable dashboard on 127.0.0.1")
    a.add_argument("--port", type=int, default=8787)
    a.add_argument("--stop", action="store_true", help="stop a running server and exit")
    a.add_argument("--status", action="store_true", help="report whether a server is running and exit")
    a.set_defaults(func=cmd_serve)

    # update / done / block / cancel
    a = sub.add_parser("update", help="set task status")
    a.add_argument("id")
    a.add_argument("status", choices=sorted(STATUSES))
    a.add_argument("-n", "--note")
    a.set_defaults(func=cmd_update)

    for name, st in [("done", "done"), ("block", "blocked"), ("cancel", "cancelled")]:
        a = sub.add_parser(name, help=f"mark task {st}")
        a.add_argument("id")
        a.add_argument("-n", "--note")
        if name == "done":
            a.set_defaults(func=cmd_done)
        elif name == "block":
            a.set_defaults(func=cmd_block)
        else:
            a.set_defaults(func=cmd_cancel)

    # assign
    a = sub.add_parser("assign", help="assign a task to a workspace")
    a.add_argument("id")
    a.add_argument("workspace")
    a.add_argument("--force", action="store_true",
                   help="legacy flag; held slots must still be freed first")
    a.set_defaults(func=cmd_assign)

    # release
    a = sub.add_parser("release", help="free a task's workspace without changing status")
    a.add_argument("id")
    a.add_argument("-n", "--note")
    a.set_defaults(func=cmd_release)

    # workspaces
    a = sub.add_parser("workspaces", help="show workspace occupancy")
    a.add_argument("--project", default=DEFAULT_PROJECT)
    a.set_defaults(func=cmd_workspaces)

    # edit
    a = sub.add_parser("edit", help="edit a task's title and/or description")
    a.add_argument("id")
    a.add_argument("--title")
    a.add_argument("-d", "--description")
    a.set_defaults(func=cmd_edit)

    # remove
    a = sub.add_parser("remove", help="permanently remove a task (prefer cancel)")
    a.add_argument("id")
    a.add_argument("--yes", action="store_true")
    a.set_defaults(func=cmd_remove)

    # review-update
    a = sub.add_parser("review-update", help="manual review status update")
    a.add_argument("id")
    a.add_argument("status", choices=sorted(REVIEW_STATUSES))
    a.add_argument("--pr", type=int, help="apply only to this PR number")
    a.set_defaults(func=cmd_review_update)

    # review-check
    a = sub.add_parser("review-check", help="poll GitHub for merges; mark fully-merged tasks done")
    a.set_defaults(func=cmd_review_check)

    # review-msg
    a = sub.add_parser("review-msg", help="print a review-request message for a task")
    a.add_argument("id")
    a.set_defaults(func=cmd_review_msg)

    # scan-reviews
    a = sub.add_parser(
        "scan-reviews",
        help="discover open PRs requesting your review (HSI repos by default)",
    )
    a.add_argument("--fw", action="store_true",
                   help="scan the broader FW team repo allowlist instead of HSI only")
    a.add_argument("--all", action="store_true",
                   help="scan every repo, not just an allowlist")
    a.add_argument("--limit", type=int, default=100,
                   help="max PRs to fetch from GitHub (default 100)")
    a.add_argument("--json", action="store_true", help="emit JSON for the skill to consume")
    a.set_defaults(func=cmd_scan_reviews)

    # re-review
    a = sub.add_parser(
        "re-review",
        help="mark PRs for re-review: find their task, add any new PRs, set waiting-for-re-review",
    )
    a.add_argument("urls", nargs="+", metavar="PR_URL")
    a.set_defaults(func=cmd_re_review)

    # blocked-scan
    a = sub.add_parser(
        "blocked-scan",
        help="check every blocked review task for author activity (new commits / thread replies)",
    )
    a.add_argument("--update", action="store_true",
                   help="auto-transition PRs with new commits to waiting-for-re-review")
    a.add_argument("--json", action="store_true",
                   help="emit the compact JSON report instead of markdown")
    a.set_defaults(func=cmd_blocked_scan)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
