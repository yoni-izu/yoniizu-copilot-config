#!/usr/bin/env python3
"""task-manager CLI — pure mechanics for the task-manager skill.

Subcommands that mutate / read ~/tasks/tasks.json without any LLM judgment.
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

# ----------------------------------------------------------------------------
# Constants & helpers
# ----------------------------------------------------------------------------

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
    if not STORE.exists():
        STORE.parent.mkdir(parents=True, exist_ok=True)
        return {"next_id": 1, "tasks": []}
    with STORE.open() as f:
        return json.load(f)


def save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("w") as f:
        json.dump(d, f, indent=2)


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
    d = load()
    tid = f"T-{d['next_id']:03d}"

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
    save(d)
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
    d = load()
    tid = f"T-{d['next_id']:03d}"
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
    save(d)

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
        print(f"[tm] {msg}. Use --force to displace.", file=sys.stderr)
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
                p["closed"] = True
                append_note(t, f"{p['repo']}#{p['pr_number']}: closed without merge")
                changed = True
                updates.append(f"{t['id']} {p['repo']}#{p['pr_number']}: closed")
        if changed:
            t["updated"] = TODAY
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
# Subcommand: dashboard / serve (HTML view; serve adds editing via a local API)
# ----------------------------------------------------------------------------

DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Task Dashboard</title>
<style>
  :root {
    --bg: #f6f7f9; --panel: #ffffff; --border: #e2e5ea; --text: #1c2230;
    --muted: #6b7280; --accent: #2563eb; --accent-soft: #eff4ff;
    --shadow: 0 1px 3px rgba(0,0,0,.08), 0 1px 2px rgba(0,0,0,.04);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#0f1218; --panel:#171b24; --border:#2a2f3a; --text:#e6e9ef;
      --muted:#9aa3b2; --accent:#60a5fa; --accent-soft:#1d2433;
      --shadow:0 1px 3px rgba(0,0,0,.5);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin:0; font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:var(--bg); color:var(--text);
  }
  header { padding:18px 24px 8px; }
  h1 { margin:0; font-size:20px; }
  .sub { color:var(--muted); font-size:12px; margin-top:2px; }
  .mode { display:inline-block; padding:1px 8px; border-radius:999px; font-size:11px; font-weight:600; margin-left:6px; }
  .mode.ro { background:#e5e7eb; color:#6b7280; }
  .mode.edit { background:#dcfce7; color:#15803d; }
  .wrap { padding:0 24px 40px; }
  .stats { display:flex; flex-wrap:wrap; gap:10px; margin:14px 0; }
  .stat {
    background:var(--panel); border:1px solid var(--border); border-radius:10px;
    padding:10px 14px; min-width:96px; box-shadow:var(--shadow);
  }
  .stat .n { font-size:20px; font-weight:700; }
  .stat .l { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .controls {
    background:var(--panel); border:1px solid var(--border); border-radius:12px;
    padding:14px 16px; box-shadow:var(--shadow); margin-bottom:16px;
  }
  .row { display:flex; flex-wrap:wrap; gap:18px; align-items:flex-start; }
  .grp { display:flex; flex-direction:column; gap:6px; }
  .grp > .lbl { font-size:11px; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .chips { display:flex; flex-wrap:wrap; gap:6px; }
  .chip {
    display:inline-flex; align-items:center; gap:5px; padding:4px 9px; border-radius:999px;
    border:1px solid var(--border); background:var(--bg); cursor:pointer; user-select:none;
    font-size:12px; transition:.12s;
  }
  .chip input { accent-color:var(--accent); margin:0; }
  .chip.on { background:var(--accent-soft); border-color:var(--accent); color:var(--accent); }
  .dd { position:relative; }
  .dd-btn {
    display:inline-flex; align-items:center; justify-content:space-between; gap:8px;
    min-width:160px; padding:7px 10px; border:1px solid var(--border); border-radius:8px;
    background:var(--bg); color:var(--text); cursor:pointer; font:inherit; user-select:none;
  }
  .dd-btn:hover { border-color:var(--accent); }
  .dd.open .dd-btn { border-color:var(--accent); }
  .dd-btn .dd-label { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .dd-btn.has-sel .dd-label { color:var(--accent); font-weight:600; }
  .dd-caret { font-size:10px; opacity:.7; }
  .dd-panel {
    display:none; position:absolute; z-index:20; top:calc(100% + 4px); left:0;
    min-width:100%; max-height:280px; overflow:auto; padding:6px;
    background:var(--panel); border:1px solid var(--border); border-radius:10px; box-shadow:var(--shadow);
  }
  .dd.open .dd-panel { display:block; }
  .dd-opt {
    display:flex; align-items:center; gap:8px; padding:6px 8px; border-radius:7px;
    cursor:pointer; font-size:13px; white-space:nowrap;
  }
  .dd-opt:hover { background:var(--accent-soft); }
  .dd-opt input { accent-color:var(--accent); margin:0; }
  .dd-clear {
    width:100%; margin-top:4px; padding:5px 8px; border:none; border-top:1px solid var(--border);
    background:none; color:var(--muted); cursor:pointer; font:inherit; font-size:12px; text-align:left;
  }
  .dd-clear:hover { color:var(--accent); }
  input[type=search], select, input[type=text], input[type=number], textarea {
    font:inherit; padding:7px 10px; border:1px solid var(--border); border-radius:8px;
    background:var(--bg); color:var(--text); min-width:160px;
  }
  input[type=search]{ min-width:260px; }
  .search-wrap { flex:1 1 260px; }
  .toolbar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:12px;
             padding-top:12px; border-top:1px dashed var(--border); }
  button.link {
    background:none; border:1px solid var(--border); color:var(--text); cursor:pointer;
    padding:6px 12px; border-radius:8px; font:inherit;
  }
  button.link:hover { border-color:var(--accent); color:var(--accent); }
  button.primary { background:var(--accent); border:1px solid var(--accent); color:#fff; cursor:pointer;
    padding:6px 12px; border-radius:8px; font:inherit; }
  button.primary:hover { filter:brightness(1.05); }
  .header-top { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; flex-wrap:wrap; }
  button.refresh {
    display:inline-flex; align-items:center; gap:6px; background:var(--panel); color:var(--text);
    border:1px solid var(--border); border-radius:8px; padding:7px 12px; cursor:pointer; font:inherit;
    box-shadow:var(--shadow);
  }
  button.refresh:hover { border-color:var(--accent); color:var(--accent); }
  button.refresh:disabled { opacity:.6; cursor:default; }
  button.refresh .ico { display:inline-block; font-size:14px; line-height:1; }
  button.refresh.spinning .ico { animation:spin .7s linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .refresh-note { font-size:11px; color:var(--muted); margin-top:4px; text-align:right; }
  table { width:100%; border-collapse:collapse; background:var(--panel);
          border:1px solid var(--border); border-radius:12px; overflow:hidden; box-shadow:var(--shadow); }
  th, td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--border); vertical-align:top; }
  th { font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted);
       cursor:pointer; white-space:nowrap; position:sticky; top:0; background:var(--panel); user-select:none; }
  th:hover { color:var(--accent); }
  th .arr { font-size:10px; opacity:.7; margin-left:3px; }
  tr.task { cursor:pointer; }
  tr.task:hover { background:var(--accent-soft); }
  td.id { font-family:ui-monospace,Menlo,Consolas,monospace; font-weight:600; white-space:nowrap; }
  .title-cell { max-width:420px; }
  .feature { color:var(--muted); font-size:12px; margin-top:2px; }
  .badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; white-space:nowrap; }
  .s-todo{background:#e0edff;color:#1d4ed8;} .s-in-progress{background:#fef3c7;color:#92400e;}
  .s-blocked{background:#fee2e2;color:#b91c1c;} .s-done{background:#dcfce7;color:#15803d;}
  .s-backlog{background:#ede9fe;color:#6d28d9;} .s-cancelled{background:#e5e7eb;color:#6b7280;}
  .p-critical{background:#fee2e2;color:#b91c1c;} .p-high{background:#ffedd5;color:#c2410c;}
  .p-medium{background:#e0edff;color:#1d4ed8;} .p-low{background:#e5e7eb;color:#6b7280;}
  @media (prefers-color-scheme: dark){
    .s-todo,.p-medium{background:#1e3a8a33;color:#93c5fd;} .s-in-progress,.p-high{background:#78350f44;color:#fcd34d;}
    .s-blocked,.p-critical{background:#7f1d1d44;color:#fca5a5;} .s-done{background:#14532d44;color:#86efac;}
    .s-backlog{background:#4c1d9544;color:#c4b5fd;} .s-cancelled,.p-low{background:#37415133;color:#9ca3af;}
  }
  .cat { font-size:11px; color:var(--muted); }
  .tabs { display:flex; gap:4px; flex-wrap:wrap; margin:16px 0 0; border-bottom:1px solid var(--border); }
  .tab { padding:8px 16px; border:1px solid transparent; border-bottom:none; border-radius:8px 8px 0 0;
         cursor:pointer; font:inherit; font-size:13px; font-weight:600; color:var(--muted); background:none; }
  .tab:hover { color:var(--accent); }
  .tab.on { color:var(--accent); background:var(--panel); border-color:var(--border); margin-bottom:-1px; }
  .tab .badge-count { font-size:11px; font-weight:500; color:var(--muted); margin-left:7px; }
  .tab.on .badge-count { color:var(--accent); }
  .rv-pending{background:#e5e7eb;color:#6b7280;} .rv-in-progress{background:#fef3c7;color:#92400e;}
  .rv-commented{background:#e0edff;color:#1d4ed8;} .rv-changes-requested{background:#fee2e2;color:#b91c1c;}
  .rv-waiting-for-author{background:#ffedd5;color:#c2410c;}
  .rv-waiting-for-re-review{background:#ede9fe;color:#6d28d9;} .rv-approved{background:#dcfce7;color:#15803d;}
  .rv-merged{background:#dcfce7;color:#15803d;}
  @media (prefers-color-scheme: dark){
    .rv-pending{background:#37415133;color:#9ca3af;} .rv-in-progress{background:#78350f44;color:#fcd34d;}
    .rv-commented{background:#1e3a8a33;color:#93c5fd;} .rv-changes-requested{background:#7f1d1d44;color:#fca5a5;}
    .rv-waiting-for-author{background:#7c2d1244;color:#fdba74;}
    .rv-waiting-for-re-review{background:#4c1d9544;color:#c4b5fd;} .rv-approved,.rv-merged{background:#14532d44;color:#86efac;}
  }
  .detail td { background:var(--bg); }
  .detail-box { display:grid; gap:10px; padding:6px 2px; }
  .detail-box h4 { margin:0 0 4px; font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .kv { display:flex; flex-wrap:wrap; gap:6px 18px; font-size:13px; }
  .kv span b { color:var(--muted); font-weight:600; }
  .notes { margin:0; padding-left:18px; font-size:13px; }
  .notes li { margin:2px 0; }
  a { color:var(--accent); }
  .pr-list { display:grid; gap:6px; }
  .pr { font-size:13px; }
  .empty { text-align:center; padding:40px; color:var(--muted); }
  code { font-family:ui-monospace,Menlo,Consolas,monospace; background:var(--bg); padding:1px 5px; border-radius:5px; }
  .count { color:var(--muted); font-size:12px; }
  .editor { display:grid; gap:10px; border:1px dashed var(--border); border-radius:10px; padding:10px 12px; }
  .editor .fields { display:flex; flex-wrap:wrap; gap:10px 16px; align-items:flex-end; }
  .editor label { display:flex; flex-direction:column; gap:4px; font-size:11px; color:var(--muted);
    text-transform:uppercase; letter-spacing:.04em; }
  .editor textarea { min-width:280px; min-height:42px; text-transform:none; }
  .editor .actions { display:flex; gap:8px; align-items:center; }
  .editor .msg { font-size:12px; color:var(--muted); }
  .editor .hint { font-size:11px; color:var(--muted); font-style:italic; align-self:center; text-transform:none; }
  .ro-note { color:var(--muted); font-size:12px; font-style:italic; }
  /* --- workspace state view --- */
  .ws-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-top:16px; }
  .ws-grid.cols-2 { grid-template-columns:repeat(2,1fr); }
  .ws-grid.cols-3 { grid-template-columns:repeat(3,1fr); }
  @media (max-width:900px){ .ws-grid, .ws-grid.cols-2, .ws-grid.cols-3 { grid-template-columns:1fr; } }
  .ws-toolbar { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; }
  .ws-layout { display:inline-flex; align-items:center; gap:6px; }
  .ws-layout .lbl { font-size:11px; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }
  .ws-seg { display:inline-flex; border:1px solid var(--border); border-radius:8px; overflow:hidden; }
  .ws-seg button { background:var(--bg); color:var(--text); border:none; padding:6px 12px; cursor:pointer; font:inherit; font-size:12px; }
  .ws-seg button + button { border-left:1px solid var(--border); }
  .ws-seg button.on { background:var(--accent); color:#fff; }
  .ws-card { background:var(--panel); border:1px solid var(--border); border-radius:12px;
             padding:14px 16px; box-shadow:var(--shadow); display:flex; flex-direction:column; gap:10px; }
  .ws-card.occupied { border-left:4px solid var(--accent); }
  .ws-card.free { border-left:4px solid #16a34a; }
  .ws-head { display:flex; align-items:center; justify-content:space-between; gap:8px; }
  .ws-name { font-size:15px; font-weight:700; display:flex; align-items:center; }
  .ws-name .num { display:inline-flex; align-items:center; justify-content:center; width:22px; height:22px;
                  border-radius:6px; background:var(--accent-soft); color:var(--accent); font-size:12px; margin-right:8px; }
  .ws-topic { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ws-free-name { color:var(--muted); font-weight:600; text-transform:capitalize; }
  .ws-badge-free{ background:#dcfce7; color:#15803d; }
  .ws-badge-occupied{ background:#fef3c7; color:#92400e; }
  @media (prefers-color-scheme: dark){
    .ws-badge-free{ background:#14532d44; color:#86efac; }
    .ws-badge-occupied{ background:#78350f44; color:#fcd34d; }
  }
  .ws-task { border:1px solid var(--border); border-radius:8px; padding:8px 10px; display:grid; gap:6px; background:var(--bg); }
  .ws-task .t-title { font-weight:600; }
  .ws-task .t-meta { font-size:12px; color:var(--muted); display:flex; flex-wrap:wrap; gap:4px 12px; align-items:center; }
  .ws-empty { color:var(--muted); font-size:13px; font-style:italic; padding:6px 2px; }
  .ws-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
  .ws-actions select { min-width:180px; }
  .ws-occupy-box { flex-direction:column; align-items:stretch; }
  .ws-occ-row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .ws-occ-row input[type=text] { flex:1; min-width:180px; }
  .ws-msg { font-size:12px; color:var(--muted); }
</style>
</head>
<body>
<header>
  <div class="header-top">
    <div>
      <h1>Task Dashboard <span id="modebadge" class="mode ro">read-only</span></h1>
      <div class="sub">Generated __GENERATED__ · <span id="total"></span></div>
    </div>
    <div>
      <button class="refresh" id="refresh" title="Reload entry values without losing filters, tab, or sort"><span class="ico">↻</span> Refresh</button>
      <div class="refresh-note" id="refresh-note"></div>
    </div>
  </div>
</header>
<div class="wrap">
  <nav class="tabs" id="tabs"></nav>
  <div class="stats" id="stats"></div>

  <div class="controls">
    <div class="row">
      <div class="grp search-wrap">
        <span class="lbl">Search</span>
        <input type="search" id="q" placeholder="Search title, description, jira, context, notes…">
      </div>
      <div class="grp">
        <span class="lbl">Status</span>
        <div class="dd" id="dd-status">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-status"></div>
        </div>
      </div>
      <div class="grp" id="grp-category">
        <span class="lbl">Category</span>
        <div class="dd" id="dd-category">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-category"></div>
        </div>
      </div>
      <div class="grp">
        <span class="lbl">Priority</span>
        <div class="dd" id="dd-priority">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-priority"></div>
        </div>
      </div>
    </div>
    <div class="row" style="margin-top:12px;">
      <div class="grp">
        <span class="lbl">Complexity</span>
        <div class="dd" id="dd-complexity">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-complexity"></div>
        </div>
      </div>
      <div class="grp">
        <span class="lbl">Workspace</span>
        <div class="dd" id="dd-workspace">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-workspace"></div>
        </div>
      </div>
      <div class="grp">
        <span class="lbl">Sort by</span>
        <select id="sort">
          <option value="priority">Priority</option>
          <option value="created">Created (newest)</option>
          <option value="worked">Last worked (recent)</option>
          <option value="age">Age (oldest)</option>
          <option value="cost">Est. cost</option>
          <option value="id">ID</option>
        </select>
      </div>
      <div class="grp">
        <span class="lbl">Options</span>
        <div class="chips">
          <label class="chip on"><input type="checkbox" id="o-hidedone" checked> Hide done/cancelled</label>
          <label class="chip"><input type="checkbox" id="o-jira"> Has Jira</label>
          <label class="chip"><input type="checkbox" id="o-ws"> Has workspace</label>
          <label class="chip"><input type="checkbox" id="o-review"> Code-review only</label>
        </div>
      </div>
    </div>
    <div class="row" id="row-review" style="margin-top:12px; display:none;">
      <div class="grp">
        <span class="lbl">Review status</span>
        <div class="dd" id="dd-reviewstatus">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-reviewstatus"></div>
        </div>
      </div>
      <div class="grp">
        <span class="lbl">Author</span>
        <div class="dd" id="dd-author">
          <button type="button" class="dd-btn"><span class="dd-label">All</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="f-author"></div>
        </div>
      </div>
    </div>
    <div class="toolbar">
      <div class="grp" style="flex-direction:row; align-items:center; gap:8px;">
        <span class="lbl" style="color:var(--muted);font-size:11px;">Columns</span>
        <div class="dd" id="dd-cols">
          <button type="button" class="dd-btn"><span class="dd-label">Columns</span><span class="dd-caret">▾</span></button>
          <div class="dd-panel" id="cols"></div>
        </div>
      </div>
      <button class="link" id="reset" style="margin-left:auto;">Reset filters</button>
    </div>
  </div>

  <div class="count" id="count"></div>
  <table id="tbl">
    <thead><tr id="head"></tr></thead>
    <tbody id="body"></tbody>
  </table>
  <div id="wsview" style="display:none;"></div>
</div>

<script>
let TASKS = __DATA__;
let WORKSPACES = __WSDATA__;
let SERVED = false;
const JIRA_BASE = "https://elementlabs.atlassian.net/browse/";
const STATUS_ORDER = ["in-progress","blocked","todo","backlog","done","cancelled"];
const PRI_ORDER = ["critical","high","medium","low"];
const CPX_ORDER = ["trivial","small","medium","large","epic"];
const CATEGORIES = ["development","code-review","planning","testing","track","misc"];
const RV_ORDER = ["pending","in-progress","commented","changes-requested","waiting-for-author","waiting-for-re-review","approved","merged"];
// Per-PR review statuses that can be *set* from the editor (excludes the
// consolidated-only "waiting-for-author").
const RV_SETTABLE = RV_ORDER.filter(s=>s!=="waiting-for-author");

// Numbered workspaces only: the Workspace State tab tracks slots whose name is a
// number-word, mapped to that number (one→1, two→2, … six→6). Other checkout dirs
// (e.g. main, elpci) are intentionally not shown here.
const WS_TAB = "__workspaces__";
const WS_NUMBER_NAMES = ["one","two","three","four","five","six"];
const WORKSPACE_SLOTS = WS_NUMBER_NAMES.map((slot,i)=>({slot, n:i+1}));
let wsGridCols = 3;   // Workspace State grid: 3 columns (3×2) or 2 columns (2×3)

const COLUMNS = [
  {key:"id",      label:"ID",        on:true},
  {key:"title",   label:"Title / Feature", on:true},
  {key:"category",label:"Category",  on:true},
  {key:"priority",label:"Priority",  on:true},
  {key:"status",  label:"Status",    on:true},
  {key:"reviewstatus",label:"Review", on:false},
  {key:"prs",     label:"PRs",       on:false},
  {key:"author",  label:"Author",    on:false},
  {key:"complexity",label:"Cx",      on:false},
  {key:"cost",    label:"Est",       on:true},
  {key:"jira",    label:"Jira",      on:true},
  {key:"workspace",label:"WS",       on:false},
  {key:"age",     label:"Age",       on:true},
  {key:"worked",  label:"Worked",    on:true},
];

// Per-tab column presets (smart category views). Order follows COLUMNS.
const TAB_DEFS = {
  "all":         {cols:["id","title","category","priority","status","cost","jira","age","worked"]},
  "development": {cols:["id","title","priority","status","complexity","cost","workspace","age","worked"]},
  "code-review": {cols:["id","title","priority","status","reviewstatus","prs","author","age"]},
  "planning":    {cols:["id","title","priority","status","jira","age","worked"]},
  "testing":     {cols:["id","title","priority","status","complexity","cost","age"]},
  "track":       {cols:["id","title","priority","status","jira","age","worked"]},
  "misc":        {cols:["id","title","priority","status","age","worked"]},
};
let activeTab = "all";

// Column key -> how to extract a comparable value
function sortVal(t,key){
  switch(key){
    case "id": return t.id||"";
    case "title": return (t.title||"").toLowerCase();
    case "category": return t.category||"";
    case "priority": return idx(PRI_ORDER,t.priority);
    case "status": return idx(STATUS_ORDER,t.status);
    case "complexity": return idx(CPX_ORDER,t.complexity);
    case "cost": return t.cost_hours||0;
    case "jira": return t.jira||"~";
    case "workspace": return t.workspace||"~";
    case "age": return t.created||"";        // ascending = oldest first
    case "created": return t.created||"";
    case "worked": return lastWorked(t)||"";
    case "reviewstatus": { const p=prs(t); return p.length?idx(RV_ORDER,consolidateReviewStatus(p)):99; }
    case "prs": { const p=prs(t); return p.length?(p[0].repo+"#"+p[0].pr_number):"~"; }
    case "author": { const p=prs(t); return p.length?(p[0].author||""):"~"; }
  }
  return "";
}
function idx(o,v){ const i=o.indexOf(v); return i<0?99:i; }

// Sort state: header clicks set this; dropdown also sets it.
const sortState = {key:"priority", dir:1};   // dir 1=asc, -1=desc

function daysSince(iso){ if(!iso) return null;
  const d=new Date(iso+"T00:00:00"); if(isNaN(d)) return null;
  return Math.floor((Date.now()-d)/(864e5)); }
function lastWorked(t){
  const cands=[]; if(t.updated) cands.push(t.updated);
  (t.notes||[]).forEach(n=>{ const m=/^(\d{4}-\d{2}-\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2})?)?:/.exec(n); if(m) cands.push(m[1]); });
  if(!cands.length) return null;
  return cands.sort().slice(-1)[0];
}
function workedLabel(t){ const iso=lastWorked(t); if(!iso) return "—";
  const n=daysSince(iso); return n===0?"today":n+"d ago"; }
function prs(t){ return t.reviews && t.reviews.length ? t.reviews : (t.review?[t.review]:[]); }
// Collapse per-PR review statuses into one overall task review status.
// Mirrors consolidate_review_status() in the Python CLI.
function consolidateReviewStatus(pl){
  const ss=(pl||[]).map(r=>r.review_status||"pending");
  if(!ss.length) return "pending";
  if(ss.every(s=>s==="merged")) return "merged";
  if(ss.every(s=>s==="approved"||s==="merged")) return "approved";
  if(ss.some(s=>s==="commented"||s==="changes-requested")) return "waiting-for-author";
  if(ss.some(s=>s==="waiting-for-re-review")) return "waiting-for-re-review";
  if(ss.some(s=>s==="in-progress")) return "in-progress";
  return "pending";
}
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function feature(t){
  let d=(t.description||"").trim();
  let f = d ? d.split(". ")[0] : (t.title||"").replace(/^Review:\s*/i,"");
  f=f.replace(/\s+/g," ").trim();
  return f.length>90 ? f.slice(0,89)+"…" : f;
}

// ---- build filter dropdowns ----
function uniq(arr){ return [...new Set(arr)]; }
const DD_GROUPS = ["status","category","priority","complexity","workspace","reviewstatus","author"];
function ddItem(container, value, label, group){
  const lab=document.createElement("label"); lab.className="dd-opt";
  lab.innerHTML=`<input type="checkbox" data-group="${group}" value="${esc(value)}"> ${esc(label)}`;
  const cb=lab.querySelector("input");
  cb.addEventListener("change",()=>{ updateDdLabel(group); render(); });
  container.appendChild(lab);
}
function updateDdLabel(group){
  const dd=document.getElementById("dd-"+group); if(!dd) return;
  const btn=dd.querySelector(".dd-btn"); const lbl=btn.querySelector(".dd-label");
  const vals=checkedVals(group);
  btn.classList.toggle("has-sel", vals.length>0);
  lbl.textContent = vals.length===0 ? "All" : (vals.length===1 ? vals[0] : vals.length+" selected");
}
function buildFilters(){
  DD_GROUPS.forEach(g=>{ const el=document.getElementById("f-"+g); if(el) el.innerHTML=""; });
  STATUS_ORDER.filter(s=>TASKS.some(t=>t.status===s))
    .forEach(s=>ddItem(document.getElementById("f-status"),s,s,"status"));
  uniq(TASKS.map(t=>t.category)).sort()
    .forEach(c=>ddItem(document.getElementById("f-category"),c,c,"category"));
  PRI_ORDER.filter(p=>TASKS.some(t=>t.priority===p))
    .forEach(p=>ddItem(document.getElementById("f-priority"),p,p,"priority"));
  CPX_ORDER.filter(c=>TASKS.some(t=>t.complexity===c))
    .forEach(c=>ddItem(document.getElementById("f-complexity"),c,c,"complexity"));
  uniq(TASKS.map(t=>t.workspace).filter(Boolean)).sort()
    .forEach(w=>ddItem(document.getElementById("f-workspace"),w,w,"workspace"));
  // code-review-only filters: consolidated review status + PR author
  const revPrs=TASKS.filter(t=>t.category==="code-review").flatMap(t=>prs(t));
  const revStatuses=TASKS.filter(t=>t.category==="code-review").map(t=>consolidateReviewStatus(prs(t)));
  RV_ORDER.filter(s=>revStatuses.includes(s))
    .forEach(s=>ddItem(document.getElementById("f-reviewstatus"),s,s,"reviewstatus"));
  uniq(revPrs.map(r=>r.author||"?")).sort()
    .forEach(a=>ddItem(document.getElementById("f-author"),a,a,"author"));
  DD_GROUPS.forEach(updateDdLabel);
}

function checkedVals(group){
  return [...document.querySelectorAll(`input[data-group="${group}"]:checked`)].map(c=>c.value);
}

// ---- dropdown open/close ----
document.querySelectorAll(".dd").forEach(dd=>{
  dd.querySelector(".dd-btn").addEventListener("click",e=>{
    e.stopPropagation();
    const wasOpen=dd.classList.contains("open");
    document.querySelectorAll(".dd.open").forEach(o=>o.classList.remove("open"));
    if(!wasOpen) dd.classList.add("open");
  });
  dd.querySelector(".dd-panel").addEventListener("click",e=>e.stopPropagation());
});
document.addEventListener("click",()=>document.querySelectorAll(".dd.open").forEach(o=>o.classList.remove("open")));

// ---- filtering ----
function applyFilters(){
  const q=document.getElementById("q").value.trim().toLowerCase();
  const st=checkedVals("status"), cat=checkedVals("category"), pri=checkedVals("priority");
  const cpx=checkedVals("complexity"), ws=checkedVals("workspace");
  const rvst=checkedVals("reviewstatus"), auth=checkedVals("author");
  const hideDone=document.getElementById("o-hidedone").checked;
  const needJira=document.getElementById("o-jira").checked;
  const needWs=document.getElementById("o-ws").checked;
  const reviewOnly=document.getElementById("o-review").checked;

  return TASKS.filter(t=>{
    if(activeTab!=="all" && t.category!==activeTab) return false;
    // Merged code-review tasks are "done" but stay visible in the review section
    // so the terminal `merged` status is trackable (until all PRs land they were open).
    const keepMerged = t.category==="code-review" && consolidateReviewStatus(prs(t))==="merged";
    if(hideDone && (t.status==="done"||t.status==="cancelled") && !keepMerged) return false;
    if(st.length && !st.includes(t.status)) return false;
    if(cat.length && !cat.includes(t.category)) return false;
    if(pri.length && !pri.includes(t.priority)) return false;
    if(cpx.length && !cpx.includes(t.complexity)) return false;
    if(ws.length && !ws.includes(t.workspace)) return false;
    if(rvst.length && !rvst.includes(consolidateReviewStatus(prs(t)))) return false;
    if(auth.length && !prs(t).some(r=>auth.includes(r.author||"?"))) return false;
    if(needJira && !t.jira) return false;
    if(needWs && !t.workspace) return false;
    if(reviewOnly && t.category!=="code-review") return false;
    if(q){
      const hay=[t.id,t.title,t.description,t.context,t.jira,t.workspace,
                 (t.notes||[]).join(" "), prs(t).map(p=>p.repo+" "+p.author).join(" ")]
                 .join(" ").toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
}

function sortTasks(arr){
  const {key,dir}=sortState;
  const cp=[...arr];
  cp.sort((a,b)=>{
    let va=sortVal(a,key), vb=sortVal(b,key);
    let c = (va<vb?-1:va>vb?1:0);
    if(c===0 && key!=="id") c = (a.id<b.id?-1:a.id>b.id?1:0);  // stable tiebreak
    return c*dir;
  });
  return cp;
}

// ---- rendering ----
function cellFor(t,key){
  switch(key){
    case "id": return `<td class="id">${esc(t.id)}</td>`;
    case "title": return `<td class="title-cell"><div>${esc(t.title)}</div>`+
                         `<div class="feature">${esc(feature(t))}</div></td>`;
    case "category": return `<td><span class="cat">${esc(t.category)}</span></td>`;
    case "priority": return `<td><span class="badge p-${esc(t.priority)}">${esc(t.priority)}</span></td>`;
    case "status": return `<td><span class="badge s-${esc(t.status)}">${esc(t.status)}</span></td>`;
    case "complexity": return `<td>${esc(t.complexity||"—")}</td>`;
    case "cost": return `<td>${t.cost_hours!=null?t.cost_hours+"h":"—"}</td>`;
    case "jira": return `<td>${t.jira?`<a href="${JIRA_BASE+encodeURIComponent(t.jira)}" target="_blank" onclick="event.stopPropagation()">${esc(t.jira)}</a>`:"—"}</td>`;
    case "workspace": return `<td>${esc(t.workspace||"—")}</td>`;
    case "age": { const n=daysSince(t.created); return `<td>${n==null?"—":n+"d"}</td>`; }
    case "worked": return `<td>${esc(workedLabel(t))}</td>`;
    case "reviewstatus": { const p=prs(t); if(!p.length) return "<td>\u2014</td>";
      const cs=consolidateReviewStatus(p);
      return `<td><span class="badge rv-${esc(cs)}">${esc(cs)}</span></td>`; }
    case "prs": { const p=prs(t); if(!p.length) return "<td>\u2014</td>";
      return `<td>${p.map(r=>`<a href="${esc(r.pr_url)}" target="_blank" onclick="event.stopPropagation()">${esc(r.repo)}#${esc(r.pr_number)}</a>${r.merged?" \u2713":""}`).join("<br>")}</td>`; }
    case "author": { const p=prs(t); if(!p.length) return "<td>\u2014</td>";
      return `<td>${esc([...new Set(p.map(r=>r.author||"?"))].join(", "))}</td>`; }
  }
  return "<td></td>";
}

function selOpts(values, current){
  return values.map(v=>`<option value="${esc(v)}" ${v===current?"selected":""}>${esc(v)}</option>`).join("");
}

function editorHtml(t){
  if(!SERVED) return `<div class="ro-note">Read-only. Run <code>tm.py serve</code> and open the served page to edit.</div>`;
  const p=prs(t);
  const reviewRow = (t.category==="code-review" && p.length) ? `<div class="fields">
      <label>Review status<select class="e-review-status"><option value="">— set on all PRs —</option>${selOpts(RV_SETTABLE,"")}</select></label>
      <span class="hint">Applies to all ${p.length} PR${p.length>1?"s":""}; task status is re-derived.</span>
    </div>` : "";
  return `<div class="editor" data-id="${esc(t.id)}">
    <div class="fields">
      <label>Status<select class="e-status">${selOpts(STATUS_ORDER,t.status)}</select></label>
      <label>Priority<select class="e-priority">${selOpts(PRI_ORDER,t.priority)}</select></label>
      <label>Category<select class="e-category">${selOpts(CATEGORIES,t.category)}</select></label>
      <label>Complexity<select class="e-complexity"><option value="">—</option>${selOpts(CPX_ORDER,t.complexity||"")}</select></label>
      <label>Est (h)<input type="number" step="0.5" class="e-cost" value="${t.cost_hours!=null?t.cost_hours:""}" style="min-width:90px"></label>
      <label>Workspace<input type="text" class="e-workspace" value="${esc(t.workspace||"")}" style="min-width:140px"></label>
    </div>
    ${reviewRow}
    <div class="fields">
      <label style="flex:1">Add note<textarea class="e-note" placeholder="Optional note to append…"></textarea></label>
    </div>
    <div class="actions">
      <button class="primary e-save">Save changes</button>
      <span class="msg"></span>
    </div>
  </div>`;
}

function detailRow(t,ncols){
  const p=prs(t);
  const prHtml = p.length ? `<div><h4>Pull Requests <span class="badge rv-${esc(consolidateReviewStatus(p))}">overall: ${esc(consolidateReviewStatus(p))}</span></h4><div class="pr-list">`+
    p.map(r=>`<div class="pr"><a href="${esc(r.pr_url)}" target="_blank">${esc(r.repo)}#${esc(r.pr_number)}</a> `+
      `by ${esc(r.author||"?")} · <code>${esc(r.branch||"?")}</code> · ${r.files_changed||0} files `+
      `+${r.additions||0}/-${r.deletions||0} · <b>${esc(r.review_status||"")}</b>${r.merged?" · merged":""}</div>`).join("")+
    `</div></div>` : "";
  const notesHtml=(t.notes||[]).length?`<div><h4>Notes</h4><ul class="notes">`+
    t.notes.map(n=>`<li>${esc(n)}</li>`).join("")+`</ul></div>`:"";
  return `<tr class="detail"><td colspan="${ncols}"><div class="detail-box">`+
    (t.description?`<div><h4>Description</h4>${esc(t.description)}</div>`:"")+
    `<div class="kv">`+
      (t.context?`<span><b>Context:</b> ${esc(t.context)}</span>`:"")+
      `<span><b>Created:</b> ${esc(t.created||"—")}</span>`+
      `<span><b>Updated:</b> ${esc(t.updated||"—")}</span>`+
      (t.due?`<span><b>Due:</b> ${esc(t.due)}</span>`:"")+
      (t.completed?`<span><b>Completed:</b> ${esc(t.completed)}</span>`:"")+
    `</div>`+ prHtml + notesHtml + editorHtml(t) +
    `</div></td></tr>`;
}

function wireEditor(detEl,t){
  const ed=detEl.querySelector(".editor"); if(!ed) return;
  const btn=ed.querySelector(".e-save"); const msg=ed.querySelector(".msg");
  btn.addEventListener("click", async (e)=>{
    e.stopPropagation();
    const payload={};
    const v=s=>ed.querySelector(s).value;
    if(v(".e-status")!==t.status) payload.status=v(".e-status");
    if(v(".e-priority")!==t.priority) payload.priority=v(".e-priority");
    if(v(".e-category")!==t.category) payload.category=v(".e-category");
    const cpx=v(".e-complexity"); if((cpx||null)!==(t.complexity||null)) payload.complexity=cpx;
    const cost=v(".e-cost"); if(cost!=="" && Number(cost)!==t.cost_hours) payload.cost_hours=Number(cost);
    const ws=v(".e-workspace"); if(ws!==(t.workspace||"")) payload.workspace=ws;
    const rvSel=ed.querySelector(".e-review-status");
    if(rvSel && rvSel.value) payload.review_status=rvSel.value;
    const note=v(".e-note").trim(); if(note) payload.add_note=note;
    if(Object.keys(payload).length===0){ msg.textContent="No changes."; return; }
    btn.disabled=true; msg.textContent="Saving…";
    try{
      const res=await fetch("/api/task/"+encodeURIComponent(t.id),{
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
      const data=await res.json();
      if(!res.ok){ msg.textContent="Error: "+(data.error||res.status); btn.disabled=false; return; }
      TASKS=data.tasks; buildFilters(); buildTabs(); render();
      msg.textContent="Saved.";
    }catch(err){ msg.textContent="Network error: "+err; btn.disabled=false; }
  });
  ed.querySelectorAll("select,input,textarea").forEach(el=>
    el.addEventListener("click",e=>e.stopPropagation()));
}

function render(){
  if(activeTab===WS_TAB){ renderWorkspaces(); return; }
  const cols=COLUMNS.filter(c=>c.on);
  document.getElementById("head").innerHTML=cols.map(c=>{
    const active=sortState.key===c.key;
    const arr=active?(sortState.dir>0?"▲":"▼"):"";
    return `<th data-key="${c.key}">${esc(c.label)}<span class="arr">${arr}</span></th>`;
  }).join("");
  document.querySelectorAll("#head th").forEach(th=>{
    th.addEventListener("click",()=>{
      const k=th.dataset.key;
      if(sortState.key===k){ sortState.dir*=-1; }
      else { sortState.key=k; sortState.dir = (k==="age")?1:( ["priority","status","complexity"].includes(k)?1:1 ); }
      // sync dropdown when the column maps to one of its options
      const dd=document.getElementById("sort");
      if([...dd.options].some(o=>o.value===k)) dd.value=k;
      render();
    });
  });
  const data=sortTasks(applyFilters());
  const body=document.getElementById("body");
  body.innerHTML="";
  if(!data.length){ body.innerHTML=`<tr><td class="empty" colspan="${cols.length}">No tasks match the current filters.</td></tr>`; }
  data.forEach(t=>{
    const tr=document.createElement("tr"); tr.className="task";
    tr.innerHTML=cols.map(c=>cellFor(t,c.key)).join("");
    const det=document.createElement("template");
    det.innerHTML=detailRow(t,cols.length);
    const detEl=det.content.firstChild; detEl.style.display="none";
    tr.addEventListener("click",()=>{
      const show=detEl.style.display==="none";
      detEl.style.display = show?"":"none";
      if(show) wireEditor(detEl,t);
    });
    body.appendChild(tr); body.appendChild(detEl);
  });
  document.getElementById("count").textContent=`Showing ${data.length} of ${TASKS.length} tasks`;
  renderStats();
}

// ---- tabs (category views) ----
function tabCount(cat){
  if(cat===WS_TAB){ return WORKSPACE_SLOTS.filter(s=>wsHolder(s.slot)).length; }
  return TASKS.filter(t=>(cat==="all"||t.category===cat) && !(["done","cancelled"].includes(t.status))).length;
}
function buildTabs(){
  const present=CATEGORIES.filter(c=>TASKS.some(t=>t.category===c));
  const tabs=[["all","All"], ...present.map(c=>[c,c]), [WS_TAB,"Workspace State"]];
  const box=document.getElementById("tabs");
  box.innerHTML=tabs.map(([k,l])=>
    `<button class="tab${k===activeTab?" on":""}" data-tab="${esc(k)}">${esc(l)}<span class="badge-count">${tabCount(k)}</span></button>`).join("");
  box.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>selectTab(b.dataset.tab)));
}
function applyTabPreset(tab){
  const preset=(TAB_DEFS[tab]||TAB_DEFS.all).cols;
  COLUMNS.forEach(c=>{ c.on=preset.includes(c.key); });
  buildColumns();
}
// Show/hide the standard filter+table chrome (hidden by the Workspace State tab).
function setChromeVisible(show){
  const disp=show?"":"none";
  const ctl=document.querySelector(".controls"); if(ctl) ctl.style.display=disp;
  ["tbl","count","stats"].forEach(id=>{ const el=document.getElementById(id); if(el) el.style.display=disp; });
  const wv=document.getElementById("wsview"); if(wv) wv.style.display=show?"none":"";
}
function selectTab(tab){
  activeTab=tab;
  document.querySelectorAll("#tabs .tab").forEach(b=>b.classList.toggle("on",b.dataset.tab===tab));
  if(tab===WS_TAB){
    setChromeVisible(false);
    renderWorkspaces();
    return;
  }
  setChromeVisible(true);
  // category filter is redundant inside a category tab: clear + hide it
  const gc=document.getElementById("grp-category");
  if(gc) gc.style.display = tab==="all" ? "" : "none";
  if(tab!=="all"){
    document.querySelectorAll('input[data-group="category"]').forEach(cb=>{ cb.checked=false; });
    updateDdLabel("category");
  }
  // review status / author filters only make sense inside the code-review tab
  const rvRow=document.getElementById("row-review");
  if(rvRow) rvRow.style.display = tab==="code-review" ? "" : "none";
  if(tab!=="code-review"){
    document.querySelectorAll('input[data-group="reviewstatus"],input[data-group="author"]')
      .forEach(cb=>{ cb.checked=false; });
    updateDdLabel("reviewstatus"); updateDdLabel("author");
  }
  applyTabPreset(tab);
  render();
}

// ---- workspace state view ----
// Occupancy lives in the workspace-manager store (WORKSPACES), not on tasks.
function wsHolder(slot){ const r=WORKSPACES.slots&&WORKSPACES.slots[slot]; return (r&&r.holder)||null; }
function wsTaskById(id){ return TASKS.find(t=>t.id===id)||null; }
function activeTaskList(){ return TASKS.filter(t=>!["done","cancelled"].includes(t.status)); }
function ago(iso){ if(!iso) return ""; const n=daysSince(iso); return n==null?"":(n===0?"today":n+"d ago"); }
const WS_ADHOC_TAGS=["monitor","debug","experiment","other"];

// POST a slot occupy/free to the workspace API, then refresh the whole view.
async function wsSlotPost(slot, payload, msgEl){
  if(!SERVED){ if(msgEl) msgEl.textContent="Read-only — run tm.py serve to edit."; return; }
  if(msgEl) msgEl.textContent="Saving…";
  try{
    const res=await fetch("/api/workspace/"+encodeURIComponent(slot),{
      method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
    const data=await res.json();
    if(!res.ok){ if(msgEl) msgEl.textContent="Error: "+(data.error||res.status); return; }
    TASKS=data.tasks; if(data.workspaces) WORKSPACES=data.workspaces;
    buildFilters(); buildTabs(); render();
  }catch(err){ if(msgEl) msgEl.textContent="Network error: "+err; }
}

function wsTaskCard(t, holder){
  const acq=holder&&holder.since; const rel=ago(acq);
  const detail=(holder&&holder.detail)||"";
  return `<div class="ws-task">
    <div class="t-title">${esc(t.id)} · ${esc(t.title)}</div>
    <div class="t-meta">
      <span class="badge s-${esc(t.status)}">${esc(t.status)}</span>
      ${t.priority?`<span class="badge p-${esc(t.priority)}">${esc(t.priority)}</span>`:""}
      ${detail&&detail!==t.workspace?`<span><b>path</b> <code>${esc(detail)}</code></span>`:""}
      <span><b>acquired</b> ${acq?esc(acq)+(rel?` (${esc(rel)})`:""):"—"}</span>
      ${t.jira?`<span><a href="${JIRA_BASE+encodeURIComponent(t.jira)}" target="_blank">${esc(t.jira)}</a></span>`:""}
    </div>
  </div>`;
}

function wsAdhocCard(holder){
  const acq=holder.since; const rel=ago(acq);
  return `<div class="ws-task">
    <div class="t-title">${esc(holder.label||"")}</div>
    <div class="t-meta">
      <span class="badge ws-badge-occupied">ad-hoc</span>
      <span class="badge">${esc(holder.tag||"other")}</span>
      <span><b>since</b> ${acq?esc(acq)+(rel?` (${esc(rel)})`:""):"—"}</span>
    </div>
  </div>`;
}

// Topic-based card title: what work is happening on this slot, not the slot name.
// Honors an explicit holder.header; otherwise derives from the task/ad-hoc hold.
function holderHeader(holder){
  if(!holder) return "";
  if(holder.header) return esc(holder.header);
  if(holder.type==="task"){
    const t=wsTaskById(holder.task_id);
    if(!t) return esc(holder.task_id);
    let s=(t.title||"").replace(/^review:\s*/i,"").replace(/^\[[^\]]*\]\s*/,"").trim();
    if(t.category==="code-review" && !/\breview\b|\bPR\b/i.test(s)) s+=" PR review";
    if(s.length>52) s=s.slice(0,51)+"…";
    return esc(s+" ("+t.id+")");
  }
  let s=holder.label||"";
  if(s.length>60) s=s.slice(0,59)+"…";
  return esc(s);
}

function wsCard(s){
  const holder=wsHolder(s.slot);
  const occ=!!holder;
  let bodyHtml;
  if(!holder){
    bodyHtml=`<div class="ws-empty">Free — no task or ad-hoc hold.</div>`;
  }else if(holder.type==="task"){
    const t=wsTaskById(holder.task_id);
    bodyHtml=t?wsTaskCard(t,holder):`<div class="ws-empty">Held by ${esc(holder.task_id)} (not in view)</div>`;
  }else{
    bodyHtml=wsAdhocCard(holder);
  }
  let ctlHtml="";
  if(SERVED){
    if(occ){
      ctlHtml=`<div class="ws-actions">
        <button class="link ws-free" data-slot="${esc(s.slot)}">Set free</button>
        <span class="ws-msg"></span></div>`;
    }else{
      const cand=activeTaskList().filter(t=>t.workspace!==s.slot);
      ctlHtml=`<div class="ws-actions ws-occupy-box" data-slot="${esc(s.slot)}">
        <div class="ws-occ-row">
          <select class="ws-pick"><option value="">— occupy with a task —</option>${
            cand.map(t=>`<option value="${esc(t.id)}">${esc(t.id)} · ${esc((t.title||"").slice(0,48))}${t.workspace?` (moves from ${esc(t.workspace)})`:""}</option>`).join("")}</select>
          <button class="primary ws-occupy-task">Occupy</button>
        </div>
        <div class="ws-occ-row">
          <input type="text" class="ws-adhoc-label" placeholder="…or an ad-hoc purpose (e.g. monitor PRs)">
          <select class="ws-adhoc-tag">${WS_ADHOC_TAGS.map(g=>`<option value="${g}">${g}</option>`).join("")}</select>
          <button class="ws-occupy-adhoc">Hold</button>
        </div>
        <span class="ws-msg"></span></div>`;
    }
  }else{
    ctlHtml=`<div class="ro-note">Read-only. Run <code>tm.py serve</code> and open the served page to change occupancy.</div>`;
  }
  const headTitle = occ
    ? `<span class="ws-topic">${holderHeader(holder)}</span>`
    : `<span class="ws-topic ws-free-name">${esc(s.slot)}</span>`;
  return `<div class="ws-card ${occ?"occupied":"free"}">
    <div class="ws-head">
      <div class="ws-name"><span class="num">${s.n}</span>${headTitle}</div>
      <span class="badge ws-badge-${occ?"occupied":"free"}">${occ?"occupied":"free"}</span>
    </div>
    <div class="ws-body">${bodyHtml}</div>
    ${ctlHtml}
  </div>`;
}

function renderWorkspaces(){
  const box=document.getElementById("wsview");
  const occupied=WORKSPACE_SLOTS.filter(s=>wsHolder(s.slot)).length;
  const free=WORKSPACE_SLOTS.length-occupied;
  const stats=`<div class="stats">
    <div class="stat"><div class="n">${WORKSPACE_SLOTS.length}</div><div class="l">Workspaces</div></div>
    <div class="stat"><div class="n">${occupied}</div><div class="l">Occupied</div></div>
    <div class="stat"><div class="n">${free}</div><div class="l">Free</div></div>
  </div>`;
  const layout=`<div class="ws-layout"><span class="lbl">Layout</span>
    <div class="ws-seg">
      <button data-cols="3" class="${wsGridCols===3?"on":""}">3×2</button>
      <button data-cols="2" class="${wsGridCols===2?"on":""}">2×3</button>
    </div></div>`;
  box.innerHTML = `<div class="ws-toolbar">${stats}${layout}</div>`
    + `<div class="ws-grid cols-${wsGridCols}">${WORKSPACE_SLOTS.map(wsCard).join("")}</div>`;
  // layout toggle
  box.querySelectorAll(".ws-seg button").forEach(b=>b.addEventListener("click",()=>{
    wsGridCols=Number(b.dataset.cols); renderWorkspaces();
  }));
  // wire controls
  box.querySelectorAll(".ws-free").forEach(b=>b.addEventListener("click",()=>{
    const msg=b.closest(".ws-actions").querySelector(".ws-msg");
    wsSlotPost(b.dataset.slot,{action:"free"},msg);
  }));
  box.querySelectorAll(".ws-occupy-task").forEach(b=>b.addEventListener("click",()=>{
    const wrap=b.closest(".ws-occupy-box");
    const sel=wrap.querySelector(".ws-pick"); const msg=wrap.querySelector(".ws-msg");
    if(!sel.value){ msg.textContent="Pick a task first."; return; }
    wsSlotPost(wrap.dataset.slot,{action:"occupy",type:"task",task_id:sel.value},msg);
  }));
  box.querySelectorAll(".ws-occupy-adhoc").forEach(b=>b.addEventListener("click",()=>{
    const wrap=b.closest(".ws-occupy-box");
    const lbl=wrap.querySelector(".ws-adhoc-label"); const tag=wrap.querySelector(".ws-adhoc-tag");
    const msg=wrap.querySelector(".ws-msg");
    if(!lbl.value.trim()){ msg.textContent="Describe the ad-hoc hold first."; return; }
    wsSlotPost(wrap.dataset.slot,{action:"occupy",type:"adhoc",label:lbl.value.trim(),tag:tag.value},msg);
  }));
}

// ---- column toggles ----
function buildColumns(){
  const colsBox=document.getElementById("cols");
  colsBox.innerHTML="";
  COLUMNS.forEach(c=>{
    const lab=document.createElement("label"); lab.className="dd-opt";
    lab.innerHTML=`<input type="checkbox" ${c.on?"checked":""}> ${esc(c.label)}`;
    const cb=lab.querySelector("input");
    cb.addEventListener("change",()=>{ c.on=cb.checked; render(); });
    colsBox.appendChild(lab);
  });
}

function renderStats(){
  const scope = activeTab==="all" ? TASKS : TASKS.filter(t=>t.category===activeTab);
  const c={}; scope.forEach(t=>c[t.status]=(c[t.status]||0)+1);
  const open=scope.filter(t=>!["done","cancelled"].includes(t.status));
  const est=open.reduce((s,t)=>s+(t.cost_hours||0),0);
  const cards=[
    ["Total",scope.length],["Open",open.length],
    ["In&nbsp;progress",c["in-progress"]||0],["Blocked",c["blocked"]||0],
    ["Todo",c["todo"]||0],["Done",c["done"]||0],
    ["Est&nbsp;remaining",est+"h"],
  ];
  if(activeTab==="development"){
    const ws=new Set(open.map(t=>t.workspace).filter(Boolean));
    cards.push(["Workspaces&nbsp;busy",ws.size]);
  } else if(activeTab==="code-review"){
    const revTasks=scope.filter(t=>t.category==="code-review" && !["done","cancelled"].includes(t.status));
    const allPrs=scope.flatMap(t=>prs(t));
    const by={}; revTasks.forEach(t=>{ const s=consolidateReviewStatus(prs(t)); by[s]=(by[s]||0)+1; });
    const merged=scope.filter(t=>t.category==="code-review" && consolidateReviewStatus(prs(t))==="merged").length;
    cards.push(["PRs",allPrs.length],
               ["Waiting&nbsp;author",by["waiting-for-author"]||0],
               ["Re-review",by["waiting-for-re-review"]||0],
               ["Approved",by["approved"]||0],
               ["Merged",merged]);
  } else if(activeTab==="planning"){
    cards.push(["With&nbsp;Jira",scope.filter(t=>t.jira).length]);
  }
  document.getElementById("stats").innerHTML=cards.map(([l,n])=>
    `<div class="stat"><div class="n">${n}</div><div class="l">${l}</div></div>`).join("");
}

// ---- events ----
document.getElementById("q").addEventListener("input",render);
document.getElementById("sort").addEventListener("change",e=>{
  sortState.key=e.target.value;
  sortState.dir = (e.target.value==="created"||e.target.value==="worked"||e.target.value==="cost")?-1:1;
  render();
});
["o-hidedone","o-jira","o-ws","o-review"]
  .forEach(id=>document.getElementById(id).addEventListener("change",render));
document.querySelectorAll("#o-jira,#o-ws,#o-review,#o-hidedone").forEach(cb=>
  cb.addEventListener("change",()=>cb.closest(".chip").classList.toggle("on",cb.checked)));
document.getElementById("reset").addEventListener("click",()=>{
  document.getElementById("q").value="";
  document.querySelectorAll('.dd-panel input[data-group]').forEach(cb=>{cb.checked=false;});
  DD_GROUPS.forEach(updateDdLabel);
  document.querySelectorAll(".dd.open").forEach(o=>o.classList.remove("open"));
  sortState.key="priority"; sortState.dir=1; document.getElementById("sort").value="priority";
  const hd=document.getElementById("o-hidedone"); hd.checked=true; hd.closest(".chip").classList.add("on");
  ["o-jira","o-ws","o-review"].forEach(id=>{const cb=document.getElementById(id); cb.checked=false; cb.closest(".chip").classList.remove("on");});
  render();
});

// ---- refresh: reload entry values in place, keeping filters/tab/sort/columns ----
function captureFilterState(){
  const s={}; DD_GROUPS.forEach(g=>{ s[g]=checkedVals(g); }); return s;
}
function restoreFilterState(s){
  DD_GROUPS.forEach(g=>{
    document.querySelectorAll(`input[data-group="${g}"]`).forEach(cb=>{
      cb.checked = !!(s[g] && s[g].includes(cb.value));
    });
    updateDdLabel(g);
  });
}
async function refreshData(){
  const btn=document.getElementById("refresh");
  const note=document.getElementById("refresh-note");
  if(btn){ btn.disabled=true; btn.classList.add("spinning"); }
  try{
    if(SERVED){
      const r=await fetch("/api/tasks",{cache:"no-store"});
      if(r.ok){ const j=await r.json(); TASKS=j.tasks||j; if(j.workspaces) WORKSPACES=j.workspaces; }
    }
    const fstate=captureFilterState();   // preserve active filter selections
    buildFilters();                       // rebuild options (may include new values)
    restoreFilterState(fstate);           // re-apply selections that still exist
    buildTabs();                          // refresh tab counts, keep activeTab
    document.getElementById("total").textContent=`${TASKS.length} tasks`;
    render();                             // sort/columns/options state untouched
    if(note){
      const now=new Date();
      const hh=String(now.getHours()).padStart(2,"0"), mm=String(now.getMinutes()).padStart(2,"0"), ss=String(now.getSeconds()).padStart(2,"0");
      note.textContent = (SERVED ? "Updated " : "Re-rendered ") + `${hh}:${mm}:${ss}`;
    }
  }catch(e){
    if(note){ note.textContent="Refresh failed"; }
  }finally{
    if(btn){ btn.disabled=false; btn.classList.remove("spinning"); }
  }
}
document.getElementById("refresh").addEventListener("click",refreshData);

// ---- served-mode detection: enables editing + live data ----
async function init(){
  try{
    const h=await fetch("/health",{cache:"no-store"});
    if(h.ok){
      SERVED=true;
      const badge=document.getElementById("modebadge");
      badge.textContent="editable"; badge.className="mode edit";
      const r=await fetch("/api/tasks",{cache:"no-store"});
      if(r.ok){ const j=await r.json(); TASKS=j.tasks||j; if(j.workspaces) WORKSPACES=j.workspaces; }
    }
  }catch(e){ /* file:// — stay read-only with embedded data */ }
  document.getElementById("total").textContent=`${TASKS.length} tasks`;
  buildFilters();
  buildTabs();
  applyTabPreset(activeTab);
  render();
}
init();
</script>
</body>
</html>
"""


def _annotate_tasks(tasks: list[dict], wd: dict | None = None) -> list[dict]:
    """Return shallow task copies annotated with a transient ``workspace`` slot.

    The task store no longer holds a ``workspace`` field — occupancy lives in the
    workspace-manager store. The dashboard's task views still read ``t.workspace``
    for display, so we resolve each task's slot from the wm store at render time.
    """
    wd = wd if wd is not None else wm.load()
    tid_slot = {h["task_id"]: s for s, h in wm.all_holders(wd).items()
                if h.get("type") == "task"}
    out = []
    for t in tasks:
        c = dict(t)
        c["workspace"] = tid_slot.get(t["id"])
        out.append(c)
    return out


def render_dashboard_html(tasks: list[dict]) -> str:
    wd = wm.load()
    payload = json.dumps(_annotate_tasks(tasks, wd), ensure_ascii=False).replace("</", "<\\/")
    wspayload = json.dumps(wd, ensure_ascii=False).replace("</", "<\\/")
    return (DASHBOARD_TEMPLATE
            .replace("__DATA__", payload)
            .replace("__WSDATA__", wspayload)
            .replace("__GENERATED__", datetime.now().strftime("%Y-%m-%d %H:%M")))


def cmd_dashboard(args: argparse.Namespace) -> int:
    d = load()
    out = Path(os.path.expanduser(args.output)) if args.output else STORE.parent / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dashboard_html(d["tasks"]), encoding="utf-8")
    print(f"[tm] wrote dashboard with {len(d['tasks'])} tasks -> {out}")
    return 0


# --- editing applied by the local server (reuses store rules) ----------------

PID_FILE = STORE.parent / ".dashboard.pid"


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


def apply_workspace_edit(slot: str, payload: dict) -> tuple[bool, str]:
    """Occupy or free a workspace slot from the dashboard.

    Payloads:
      * ``{"action": "free"}`` — release the slot's holder.
      * ``{"action": "occupy", "type": "task", "task_id": "T-NNN", "force": bool}``
      * ``{"action": "occupy", "type": "adhoc", "label": "...", "tag": "monitor", "force": bool}``

    Task holds are kept consistent with the task store (status promotion + notes);
    ad-hoc holds live only in the workspace-manager store.
    """
    root = wm.normalize_slot(slot)
    if not root:
        return False, f"invalid slot: {slot!r}"
    action = payload.get("action", "occupy")
    force = bool(payload.get("force"))

    if action == "free":
        ok, holder = wm.release(root)
        if not ok:
            return False, f"invalid slot: {slot!r}"
        if holder is None:
            return True, f"{root} already free"
        if holder.get("type") == "task":
            d = load()
            t = find_task(d, holder["task_id"])
            if t:
                t["updated"] = TODAY
                append_note(t, f"dashboard: released workspace {root}")
                save(d)
        return True, f"{root} freed ({wm.holder_summary(holder)})"

    htype = payload.get("type", "task")
    if htype == "task":
        tid = (payload.get("task_id") or "").strip()
        if not tid:
            return False, "task_id required"
        d = load()
        t = find_task(d, tid)
        if not t:
            return False, f"task not found: {tid}"
        ok, msg, displaced = wm.occupy_task(root, t["id"], detail=root, force=force)
        if not ok:
            return False, msg
        if displaced and displaced.get("type") == "task":
            other = find_task(d, displaced["task_id"])
            if other:
                append_note(other, f"dashboard: workspace {root} released to {t['id']}")
        if t["status"] == "todo":
            t["status"] = "in-progress"
        t["updated"] = TODAY
        append_note(t, f"dashboard: assigned to workspace {root}")
        save(d)
        return True, f"{t['id']} → {root}"

    if htype == "adhoc":
        label = (payload.get("label") or "").strip()
        tag = payload.get("tag") or "other"
        ok, msg, _ = wm.occupy_adhoc(root, label, tag=tag, force=force)
        if not ok:
            return False, msg
        return True, msg

    return False, f"invalid holder type: {htype}"


def _server_running() -> int | None:
    """Return the PID of a running server (per PID file), or None."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    try:
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def cmd_serve(args: argparse.Namespace) -> int:
    import signal
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if args.status:
        pid = _server_running()
        print(f"running (pid {pid})" if pid else "not running")
        return 0

    if args.stop:
        pid = _server_running()
        if not pid:
            print("[tm] no server running")
            return 0
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        print(f"[tm] stopped server (pid {pid})")
        return 0

    pid = _server_running()
    if pid:
        print(f"[tm] server already running (pid {pid}) on http://127.0.0.1:{args.port}")
        return 0

    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, "ok", "text/plain")
            if self.path in ("/", "/index.html"):
                with lock:
                    html = render_dashboard_html(load()["tasks"])
                return self._send(200, html, "text/html; charset=utf-8")
            if self.path == "/api/tasks":
                with lock:
                    wd = wm.load()
                    return self._send(200, json.dumps(
                        {"tasks": _annotate_tasks(load()["tasks"], wd), "workspaces": wd},
                        ensure_ascii=False))
            return self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            from urllib.parse import unquote
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, json.dumps({"error": "invalid JSON"}))

            if self.path.startswith("/api/workspace/"):
                slot = unquote(self.path[len("/api/workspace/"):])
                with lock:
                    ok, msg = apply_workspace_edit(slot, payload)
                    if not ok:
                        return self._send(400, json.dumps({"error": msg}))
                    wd = wm.load()
                    tasks = _annotate_tasks(load()["tasks"], wd)
                return self._send(200, json.dumps(
                    {"ok": True, "message": msg, "tasks": tasks, "workspaces": wd},
                    ensure_ascii=False))

            if not self.path.startswith("/api/task/"):
                return self._send(404, json.dumps({"error": "not found"}))
            ident = self.path[len("/api/task/"):]
            ident = unquote(ident)
            with lock:
                ok, msg = apply_task_edit(ident, payload)
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                wd = wm.load()
                tasks = _annotate_tasks(load()["tasks"], wd)
            return self._send(200, json.dumps(
                {"ok": True, "message": msg, "tasks": tasks, "workspaces": wd},
                ensure_ascii=False))

    class Server(ThreadingHTTPServer):
        # Avoid "Address already in use" on quick restarts (TIME_WAIT sockets).
        allow_reuse_address = True
        daemon_threads = True

    httpd = Server(("127.0.0.1", args.port), Handler)
    PID_FILE.write_text(str(os.getpid()))

    # Run the request loop in a background thread so the main thread can call
    # httpd.shutdown() safely (shutdown() deadlocks if invoked from the same
    # thread as serve_forever()).
    stop = threading.Event()
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()

    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    print(f"[tm] serving editable dashboard at http://127.0.0.1:{args.port} (Ctrl-C to stop)")
    try:
        stop.wait()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
        PID_FILE.unlink(missing_ok=True)
    return 0


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
    a.add_argument("-o", "--output", help="output path (default ~/tasks/dashboard.html)")
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
                   help="displace the current holder of that workspace")
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
