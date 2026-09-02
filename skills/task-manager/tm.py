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

# bug-monitor owns the HSI bug store. The portal dashboard renders its Bugs
# section from it; tm.py never touches ~/tasks/bugs.json directly.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/bug-monitor"))
try:
    import bm  # noqa: E402
except ImportError:      # bug-monitor not installed -> portal hides the section
    bm = None

# pr-monitor owns the outgoing feature-PR memoir. Its refresh runs `gh` only,
# so the portal's scan button costs nothing.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/pr-monitor"))
try:
    import pm  # noqa: E402
except ImportError:
    pm = None

# team-planner owns ~/team-planner/raid.md; rd.py reads and surgically edits it.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/team-planner"))
try:
    import rd  # noqa: E402
except ImportError:
    rd = None

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
  /* --- portal section nav (My Work / Bugs / Team) --- */
  .sections { display:flex; gap:8px; flex-wrap:wrap; margin:4px 0 2px; }
  .section-btn { display:inline-flex; align-items:center; gap:8px; padding:9px 18px; border-radius:10px;
                 border:1px solid var(--border); background:var(--panel); color:var(--muted);
                 cursor:pointer; font:inherit; font-size:13px; font-weight:700; box-shadow:var(--shadow); }
  .section-btn:hover:not(:disabled) { color:var(--accent); border-color:var(--accent); }
  .section-btn.on { background:var(--accent); border-color:var(--accent); color:#fff; }
  .section-btn .badge-count { font-size:11px; font-weight:600; opacity:.85; }
  .section-btn:disabled { opacity:.45; cursor:not-allowed; }
  /* --- bug monitor view --- */
  .bug-controls { background:var(--panel); border:1px solid var(--border); border-radius:12px;
                  padding:14px 16px; box-shadow:var(--shadow); margin-bottom:16px; }
  .sg-moving{background:#dcfce7;color:#15803d;} .sg-slow{background:#fef3c7;color:#92400e;}
  .sg-stalled{background:#ffedd5;color:#c2410c;} .sg-dormant{background:#fee2e2;color:#b91c1c;}
  .sg-closed{background:#e5e7eb;color:#6b7280;}
  .tr-untriaged{background:#fee2e2;color:#b91c1c;} .tr-in-triage{background:#fef3c7;color:#92400e;}
  .tr-triaged{background:#dcfce7;color:#15803d;} .tr-deferred{background:#e5e7eb;color:#6b7280;}
  @media (prefers-color-scheme: dark){
    .sg-moving,.tr-triaged{background:#14532d44;color:#86efac;}
    .sg-slow,.tr-in-triage{background:#78350f44;color:#fcd34d;}
    .sg-stalled{background:#7c2d1244;color:#fdba74;}
    .sg-dormant,.tr-untriaged{background:#7f1d1d44;color:#fca5a5;}
    .sg-closed,.tr-deferred{background:#37415133;color:#9ca3af;}
  }
  .flag { display:inline-block; padding:1px 6px; margin:0 3px 3px 0; border-radius:5px; font-size:10px;
          background:var(--bg); border:1px solid var(--border); color:var(--muted); white-space:nowrap; }
  .flag.warn { border-color:#f59e0b; color:#b45309; }
  .fam-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(360px,1fr)); gap:14px; margin-top:16px; }
  .fam-card { background:var(--panel); border:1px solid var(--border); border-left:4px solid var(--accent);
              border-radius:12px; padding:14px 16px; box-shadow:var(--shadow); display:grid; gap:8px;
              min-width:0; overflow:hidden; }
  .fam-card.closed { border-left-color:#16a34a; opacity:.75; }
  .fam-title { font-size:15px; font-weight:700; }
  .fam-meta { font-size:12px; color:var(--muted); display:flex; flex-wrap:wrap; gap:4px 14px; }
  .fam-members { display:grid; gap:4px; font-size:13px; min-width:0; }
  .fam-members .m { display:flex; gap:8px; align-items:baseline; min-width:0; }
  .fam-members .m.primary { font-weight:700; }
  .stale-note { font-size:12px; color:#c2410c; }
  /* --- report canvas shared by the Overview and PRs sections --- */
  :root { --track:#e8ebf0; --grid:#eef0f4;
          --tile-sh:0 1px 2px rgba(16,24,40,.05), 0 6px 16px rgba(16,24,40,.07); }
  @media (prefers-color-scheme: dark) { :root { --track:#242a36; --grid:#232833;
          --tile-sh:0 1px 2px rgba(0,0,0,.4), 0 6px 18px rgba(0,0,0,.35); } }
  .rpt-head { display:flex; align-items:flex-end; justify-content:space-between; gap:16px;
              flex-wrap:wrap; margin:18px 0 14px; padding-bottom:12px; border-bottom:2px solid var(--border); }
  .rpt-head h2 { margin:0; font-size:20px; font-weight:800; letter-spacing:-.02em; }
  .rpt-head .sub { margin:3px 0 0; font-size:12px; color:var(--muted); }
  .rpt-head .sub b { color:var(--text); font-weight:700; }
  .slicer { display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
  .slicer .sl-l { font-size:9.5px; font-weight:800; letter-spacing:.12em; color:var(--muted);
                  text-transform:uppercase; margin-right:2px; }
  .sprint { border:1px solid var(--border); border-radius:5px; padding:5px 10px; font-size:11.5px;
            background:var(--panel); line-height:1.25; }
  .sprint.now { border-color:transparent; background:#118DFF; color:#fff; font-weight:700; box-shadow:0 2px 8px rgba(17,141,255,.35); }
  .sprint .w { display:block; color:var(--muted); font-size:10px; font-weight:400; }
  .sprint.now .w { color:rgba(255,255,255,.85); }

  .ov-canvas { display:grid; grid-template-columns:repeat(12,minmax(0,1fr)); gap:12px; align-items:start; }
  .t2{grid-column:span 2;} .t3{grid-column:span 3;} .t4{grid-column:span 4;}
  .t5{grid-column:span 5;} .t6{grid-column:span 6;} .t7{grid-column:span 7;}
  .t8{grid-column:span 8;} .t9{grid-column:span 9;} .t12{grid-column:span 12;}
  @media (max-width:1240px){
    .t2{grid-column:span 4;} .t3,.t4,.t5{grid-column:span 6;}
    .t7,.t8,.t9{grid-column:span 12;} .t6{grid-column:span 6;}
  }
  @media (max-width:780px){ .ov-canvas > * { grid-column:span 12; } }

  .tile { background:var(--panel); border-radius:8px; padding:14px 16px 16px; min-width:0;
          overflow:hidden; box-shadow:var(--tile-sh); border:1px solid transparent;
          transition:box-shadow .16s ease, transform .16s ease; }
  .tile:hover { transform:translateY(-1px); box-shadow:0 2px 6px rgba(16,24,40,.09), 0 14px 30px rgba(16,24,40,.12); }
  @media (prefers-color-scheme: dark) { .tile { border-color:var(--border); } }
  .tile > h3 { margin:0; font-size:13px; font-weight:700; letter-spacing:-.01em;
               display:flex; align-items:center; gap:8px; }
  .tile > h3::before { content:""; width:4px; height:15px; border-radius:2px; flex:none;
                       background:var(--tk,#118DFF); }
  .tile > .cap { margin:4px 0 12px 12px; font-size:11px; color:var(--muted); line-height:1.45; }
  .tile > .cap:last-child { margin-bottom:0; }

  /* KPI cards */
  .kpis { display:grid; grid-template-columns:repeat(12,minmax(0,1fr)); gap:12px; margin-bottom:12px; }
  .kpi { position:relative; grid-column:span 2; background:var(--panel); border-radius:8px;
         padding:13px 15px 12px 18px; box-shadow:var(--tile-sh); border:1px solid transparent;
         overflow:hidden; display:grid; gap:1px; align-content:start;
         transition:box-shadow .16s ease, transform .16s ease; }
  .kpi:hover { transform:translateY(-1px); box-shadow:0 2px 6px rgba(16,24,40,.09), 0 14px 30px rgba(16,24,40,.12); }
  @media (prefers-color-scheme: dark) { .kpi { border-color:var(--border); } }
  @media (max-width:1240px){ .kpi { grid-column:span 4; } }
  @media (max-width:780px){ .kpi { grid-column:span 6; } }
  .kpi::before { content:""; position:absolute; left:0; top:0; bottom:0; width:5px; background:var(--k,#118DFF); }
  .kpi .l { font-size:9.5px; color:var(--muted); text-transform:uppercase; letter-spacing:.11em; font-weight:800; }
  .kpi .n { font-size:34px; font-weight:800; line-height:1.05; color:var(--k,#118DFF);
            font-variant-numeric:tabular-nums; letter-spacing:-.03em; }
  .kpi .n small { font-size:12px; font-weight:700; color:var(--muted); margin-left:4px; letter-spacing:0; }
  .kpi .s { font-size:11px; color:var(--muted); line-height:1.4; }
  .kpi .bar { height:4px; border-radius:2px; background:var(--track); overflow:hidden; margin-top:7px; }
  .kpi .bar > i { display:block; height:100%; background:var(--k,#118DFF); border-radius:2px; }
  .kpi .l, .kpi .n, .kpi .s, .kpi .bar { position:relative; z-index:1; }
  .kpi .spark { position:absolute; right:-1px; bottom:-1px; width:58%; opacity:.3; pointer-events:none; z-index:0; }

  .chart-row { display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
  .donut .seg { transition:opacity .15s; }
  .donut:hover .seg { opacity:.45; } .donut .seg:hover { opacity:1; }
  .col { transition:opacity .15s; } svg:hover .col { opacity:.55; } .col:hover { opacity:1; }
  .legend { display:grid; gap:6px; font-size:12px; min-width:0; flex:1; }
  .legend .li { display:flex; align-items:center; gap:8px; white-space:nowrap; }
  .legend .sw { width:10px; height:10px; border-radius:2px; flex:none; }
  .legend .v { margin-left:auto; color:var(--muted); font-variant-numeric:tabular-nums; padding-left:10px;
               font-weight:700; }
  /* horizontal bar visual */
  .hbars { display:grid; gap:11px; }
  .hb-t { display:flex; justify-content:space-between; align-items:baseline; gap:10px; font-size:12px; margin-bottom:4px; }
  .hb-t b { font-size:13px; font-variant-numeric:tabular-nums; }
  .hb-r { height:8px; border-radius:4px; background:var(--track); overflow:hidden; }
  .hb-r > i { display:block; height:100%; border-radius:4px; }
  .hb-s { font-size:10px; color:var(--muted); margin-top:3px; }

  .sb { width:100%; border-collapse:collapse; font-size:13px; }
  .sb th { text-align:left; font-size:9.5px; text-transform:uppercase; letter-spacing:.1em; color:var(--muted);
           font-weight:800; padding:0 8px 7px 0; border-bottom:2px solid var(--border); }
  .sb td { padding:9px 8px 9px 0; border-bottom:1px solid var(--grid); vertical-align:middle; }
  .sb tbody tr:last-child td { border-bottom:none; }
  .sb tbody tr:hover { background:var(--accent-soft); }
  .sb .num { text-align:right; font-variant-numeric:tabular-nums; font-weight:700; }
  .sb .who { color:var(--muted); font-size:11px; font-weight:400; }
  .rag { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:7px; vertical-align:middle; }
  .rag.red{background:#D64550;} .rag.orange{background:#E66C37;}
  .rag.yellow{background:#D9B300;} .rag.green{background:#1AAB40;} .rag.gray{background:#9B9B9B;}
  .feed { display:grid; gap:3px; }
  .feed .it { display:flex; gap:10px; align-items:baseline; padding:8px 10px; font-size:13px;
              border-radius:6px; border-left:3px solid var(--fk,#9B9B9B); background:var(--bg); }
  .feed .it:hover { background:var(--accent-soft); }
  .feed .it .w { flex:1; min-width:0; }
  .feed .it .why { color:var(--muted); font-size:11px; margin-top:2px; }
  .feed .it .when { color:var(--muted); font-size:11px; white-space:nowrap; font-weight:600; }
  .pill { display:inline-block; padding:2px 8px; border-radius:4px; font-size:9.5px; font-weight:800;
          text-transform:uppercase; letter-spacing:.07em; color:#fff; background:var(--fk,#9B9B9B); }
  .slots { display:flex; gap:8px; flex-wrap:wrap; }
  .slot { border-radius:6px; padding:9px 12px; font-size:12px; min-width:104px;
          background:var(--bg); border:1px solid var(--border); border-top:3px solid var(--track); }
  .slot.busy { border-top-color:#118DFF; background:var(--accent-soft); border-color:transparent; }
  .slot .nm { font-weight:800; font-size:10px; text-transform:uppercase; letter-spacing:.09em; }
  .slot .oc { color:var(--muted); font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:160px; }

  /* --- PRs section --- */
  .prf { background:var(--panel); border-radius:8px; box-shadow:var(--tile-sh);
         border:1px solid transparent; border-left:5px solid var(--fk,#9B9B9B);
         padding:13px 16px 14px; margin-bottom:12px; }
  @media (prefers-color-scheme: dark) { .prf { border-color:var(--border); border-left-color:var(--fk,#9B9B9B); } }
  .prf > header { display:flex; align-items:baseline; gap:10px; flex-wrap:wrap; }
  .prf h3 { margin:0; font-size:14px; font-weight:700; flex:1; min-width:220px; }
  .prf .fid { font-size:10px; font-weight:800; color:var(--muted); letter-spacing:.08em; }
  .prf .desc { margin:6px 0 0; font-size:11.5px; color:var(--muted); line-height:1.5;
               display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
  .prf .prog { height:5px; border-radius:3px; background:var(--track); overflow:hidden; margin:10px 0 4px; }
  .prf .prog > i { display:block; height:100%; background:#1AAB40; border-radius:3px; }
  .prf .note { margin-top:9px; font-size:11.5px; color:var(--muted); line-height:1.5;
               border-left:2px solid var(--border); padding-left:9px;
               display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
  .pr-tbl { width:100%; border-collapse:collapse; font-size:12.5px; margin-top:9px; table-layout:fixed; }
  .pr-tbl th { text-align:left; font-size:9px; text-transform:uppercase; letter-spacing:.1em;
               color:var(--muted); font-weight:800; padding:0 8px 6px 0; border-bottom:2px solid var(--border); }
  .pr-tbl td { padding:6px 8px 6px 0; border-bottom:1px solid var(--grid); vertical-align:middle;
               overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .pr-tbl tr:last-child td { border-bottom:none; }
  .pr-tbl tr:hover td { background:var(--accent-soft); }
  .st { display:inline-block; padding:2px 8px; border-radius:4px; font-size:9.5px; font-weight:800;
        text-transform:uppercase; letter-spacing:.05em; color:#fff; background:var(--sk,#9B9B9B); }
  .chk { font-variant-numeric:tabular-nums; font-size:11px; color:var(--muted); }
  .chk b { color:#D64550; }
  .ci { display:inline-block; padding:2px 7px; border-radius:9px; font-size:10px; font-weight:700;
        text-transform:uppercase; letter-spacing:.04em; color:#fff; background:var(--ck); white-space:nowrap; }
  .ci.none { color:var(--muted); background:transparent; border:1px dashed var(--grid); font-weight:600; }
  .pldm { font-size:9.5px; font-weight:800; letter-spacing:.07em; text-transform:uppercase;
          padding:2px 8px; border-radius:4px; background:var(--track); color:var(--muted); }
  .pldm-btn { font-size:10px; font-weight:700; padding:3px 9px; border-radius:4px; cursor:pointer;
              border:1px solid var(--grid); background:var(--track); color:var(--fg); white-space:nowrap; }
  .pldm-btn:hover { border-color:#118DFF; color:#118DFF; }
  .pldm-box { margin-top:10px; padding:11px 13px; border:1px solid var(--grid);
              border-radius:6px; background:var(--track); }
  .pldm-form { display:grid; grid-template-columns:repeat(3,1fr); gap:9px 13px; }
  .pldm-form label { display:flex; flex-direction:column; gap:3px; font-size:10px;
                     font-weight:700; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); }
  .pldm-form input, .pldm-form select { font:inherit; font-size:12px; padding:4px 6px;
              border:1px solid var(--grid); border-radius:4px; background:var(--bg); color:var(--fg); }
  .pldm-warn { margin:10px 0 8px; font-size:11px; color:var(--muted); line-height:1.5; }
  .pldm-act { display:flex; align-items:center; gap:9px; }
  .pldm-act button { font:inherit; font-size:11px; font-weight:700; padding:4px 12px;
                     border-radius:4px; cursor:pointer; border:1px solid var(--grid); }
  .pldm-act .go { background:#118DFF; border-color:#118DFF; color:#fff; }
  .pldm-act .go:disabled { opacity:.55; cursor:default; }
  .pldm-act .cancel { background:transparent; color:var(--muted); }
  .pldm-note { font-size:11px; color:var(--muted); }
  .pldm-prog { font-size:11.5px; margin-bottom:7px; }
  .pldm-prog .ok { color:#1AAB40; } .pldm-prog .bad { color:#D64550; }
  .pl-row { font-size:11px; padding:2px 0; border-top:1px solid var(--grid); }
  .pl-row span { color:var(--muted); }
  .pl-row.error, .pl-row.not-found { color:#D64550; }
  .spin { display:inline-block; animation:sp 1s linear infinite; }
  @keyframes sp { to { transform:rotate(360deg); } }

  /* --- development hygiene tile --- */
  .hyg-wrap { display:flex; align-items:flex-start; gap:26px; flex-wrap:wrap; }
  .hyg-score { flex:0 0 220px; padding-top:6px; }
  .hyg-rules { flex:1 1 520px; display:grid; gap:14px 22px;
               grid-template-columns:repeat(auto-fit,minmax(215px,1fr)); }
  .hyg-h { display:flex; justify-content:space-between; align-items:baseline; gap:8px; }
  .hyg-l { font-size:11.5px; font-weight:700; }
  .hyg-n { font-size:11px; font-variant-numeric:tabular-nums; color:var(--muted); }
  .hyg-bar { height:6px; border-radius:3px; background:var(--track); margin:6px 0 4px; overflow:hidden; }
  .hyg-bar i { display:block; height:100%; border-radius:3px; background:var(--hc); }
  .hyg-s { font-size:10.5px; color:var(--muted); }
  .hyg-s b { color:var(--hc); }
  @media (max-width:780px) { .hyg-score { flex:1 1 100%; } }

  /* --- RAID section --- */
  #raid-tbl { table-layout:fixed; width:100%; margin-top:14px; }
  #raid-tbl th, #raid-body tr.raid-row td { overflow-wrap:break-word; }
  #raid-body tr.raid-row { cursor:pointer; }
  #raid-body tr.raid-row:hover td { background:var(--grid); }
  #raid-body .rid { font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
                    font-size:11.5px; font-weight:700; }
  #raid-body .ttl { font-weight:600; }
  #raid-body .stt, #raid-body td:nth-child(4) { color:var(--muted); font-size:12px; }
  #raid-body td.od { color:#D64550; font-weight:700; }
  .ragp { display:inline-block; padding:2px 9px; border-radius:999px; font-size:9.5px;
         font-weight:800; letter-spacing:.06em; text-transform:uppercase;
         color:#fff; background:var(--rk,#9B9B9B); }
  .raid-det { padding:14px 6px 18px; }
  .rf-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:12px 22px; }
  .rf span { display:block; font-size:9.5px; font-weight:800; letter-spacing:.07em;
             text-transform:uppercase; color:var(--muted); margin-bottom:3px; }
  .rf p { margin:0; font-size:12.5px; line-height:1.55; }
  .raid-edit, .raid-new { margin-top:16px; padding:14px 16px; border-radius:8px;
                          background:var(--grid); }
  .raid-new { margin-bottom:4px; }
  .re-row { display:flex; flex-wrap:wrap; gap:10px 14px; margin-bottom:10px; }
  .re-row label { display:flex; flex-direction:column; gap:4px; font-size:9.5px;
                  font-weight:800; letter-spacing:.07em; text-transform:uppercase;
                  color:var(--muted); }
  .re-row input, .re-row select { font:inherit; font-size:12.5px; font-weight:400;
      letter-spacing:0; text-transform:none; color:var(--text); padding:6px 8px;
      border:1px solid var(--border); border-radius:5px; background:var(--card); }
  .re-row label:first-child input { min-width:420px; }
  .re-act { display:flex; align-items:center; gap:10px; }
  /* Bug table uses a fixed layout so 11 columns never overflow the card.
     Percentages must sum to 100. Scoped to tr.task so the full-width
     colspan detail row keeps its own alignment. */
  #bug-tbl { table-layout:fixed; width:100%; }
  #bug-tbl th, #bug-body tr.task td { overflow-wrap:break-word; }
  #bug-head th:nth-child(1)  { width:3%;  }
  #bug-head th:nth-child(2)  { width:14%; }
  #bug-head th:nth-child(3)  { width:21%; }
  #bug-head th:nth-child(4)  { width:6%;  }
  #bug-head th:nth-child(5)  { width:9%;  }
  #bug-head th:nth-child(6)  { width:10%; }
  #bug-head th:nth-child(7)  { width:5%;  }
  #bug-head th:nth-child(8)  { width:5%;  }
  #bug-head th:nth-child(9)  { width:4%;  }
  #bug-head th:nth-child(10) { width:8%;  }
  #bug-head th:nth-child(11) { width:5%;  }
  #bug-head th:nth-child(12) { width:10%; }
  #bug-body tr.task td:nth-child(7), #bug-body tr.task td:nth-child(8),
  #bug-body tr.task td:nth-child(9), #bug-body tr.task td:nth-child(11) { white-space:nowrap; }
  #bug-body tr.task td:nth-child(2) a { overflow-wrap:anywhere; }
  #bug-head th:nth-child(9), #bug-body tr.task td:nth-child(9) { text-align:center; }
  #bug-head th:nth-child(1), #bug-body tr.task td:nth-child(1) { text-align:center; padding-left:8px; padding-right:0; }
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
<div id="align-banner" style="display:none;position:fixed;top:12px;right:12px;z-index:9999;
  max-width:420px;padding:12px 14px;border-radius:8px;background:#1e293b;color:#e2e8f0;
  box-shadow:0 6px 24px rgba(0,0,0,.35);font-size:13px;line-height:1.4;">
  <div style="display:flex;align-items:center;gap:8px;">
    <span id="align-spin" style="width:14px;height:14px;border:2px solid #64748b;
      border-top-color:#38bdf8;border-radius:50%;display:inline-block;
      animation:align-spin 0.8s linear infinite;"></span>
    <b id="align-title">Aligning workspace…</b>
    <button id="align-close" style="margin-left:auto;background:none;border:none;
      color:#94a3b8;cursor:pointer;font-size:16px;line-height:1;">×</button>
  </div>
  <div id="align-body" style="margin-top:8px;max-height:52vh;overflow:auto;"></div>
</div>
<style>@keyframes align-spin{to{transform:rotate(360deg)}}</style>
<header>
  <div class="header-top">
    <div>
      <h1>Team Portal <span id="modebadge" class="mode ro">read-only</span></h1>
      <div class="sub">Generated __GENERATED__ · <span id="total"></span></div>
    </div>
    <div>
      <button class="refresh" id="refresh" title="Reload entry values without losing filters, tab, or sort"><span class="ico">↻</span> Refresh</button>
      <div class="refresh-note" id="refresh-note"></div>
    </div>
  </div>
</header>
<div class="wrap">
  <nav class="sections" id="sections"></nav>

  <div id="sec-overview">
    <div class="rpt-head">
      <div>
        <h2>HSI Firmware — team report</h2>
        <p class="sub" id="ov-asof"></p>
      </div>
      <div class="slicer" id="ov-sprints"></div>
    </div>

    <div class="kpis" id="ov-kpis"></div>

    <div class="ov-canvas">
      <div class="tile t8" style="--tk:#118DFF;">
        <h3>Bug flow — open vs closed</h3>
        <p class="cap">Cumulative over 90 days, from each bug's created/resolved date in the local mirror.</p>
        <div id="ov-bugflow"></div>
      </div>
      <div class="tile t4" style="--tk:#1AAB40;">
        <h3>Bug backlog health</h3>
        <p class="cap">Share of open bugs that are triaged, and that carry a milestone and a sprint.</p>
        <div id="ov-gauges"></div>
      </div>

      <div class="tile t8" style="--tk:#E66C37;">
        <h3>Mean time to close — monthly</h3>
        <p class="cap" id="ov-ttc-cap">Days from created to resolved, bucketed by the month the bug closed.</p>
        <div id="ov-ttc"></div>
      </div>
      <div class="tile t4" style="--tk:#6B007B;">
        <h3>Open bugs by IP</h3>
        <p class="cap">Components mapped to an IP via the team-weekly scoreboard.</p>
        <div class="chart-row"><div id="ov-bugip"></div><div class="legend" id="ov-bugip-lg"></div></div>
      </div>

      <div class="tile t8" style="--tk:#12239E;">
        <h3>Per-IP scoreboard</h3>
        <p class="cap">Open bugs, attention load, review pressure and the 12-week load trend per IP.</p>
        <div id="ov-scoreboard"></div>
      </div>
      <div class="tile t4" style="--tk:#E044A7;">
        <h3>Open bugs by owner</h3>
        <p class="cap">Who is actually carrying the open bug load.</p>
        <div class="chart-row"><div id="ov-bugowner"></div><div class="legend" id="ov-bugowner-lg"></div></div>
      </div>

      <div class="tile t8" style="--tk:#D64550;">
        <h3>Needs you now</h3>
        <p class="cap">One ranked queue across bugs, RAID, reviews and blocked tasks — worst first.</p>
        <div id="ov-feed"></div>
      </div>
      <div class="tile t4" style="--tk:#D9B300;">
        <h3>Bug hygiene — missing planning fields</h3>
        <p class="cap">Open bugs carrying each flag. These are the gaps to close in Jira.</p>
        <div id="ov-flags"></div>
      </div>

      <div class="tile t12" style="--tk:#197278;">
        <h3>Development hygiene</h3>
        <p class="cap">How cleanly work is flowing through the pipe — outgoing PRs, my review turnaround, and work-in-progress discipline.</p>
        <div class="hyg-wrap">
          <div class="hyg-score" id="ov-hyg-score"></div>
          <div class="hyg-rules" id="ov-hygiene"></div>
        </div>
      </div>

      <div class="tile t5" style="--tk:#744EC2;">
        <h3>RAID — open rows</h3>
        <p class="cap">From <code>~/team-planner/raid.md</code>, worst RAG then worst score.</p>
        <div id="ov-raid"></div>
      </div>
      <div class="tile t3" style="--tk:#197278;">
        <h3>Review queue</h3>
        <p class="cap">PRs in my incoming review queue, by consolidated state.</p>
        <div id="ov-reviews"></div>
      </div>
      <div class="tile t4" style="--tk:#3599B8;">
        <h3>Workspace occupancy</h3>
        <p class="cap">Which physical checkouts are in use, and by what.</p>
        <div class="slots" id="ov-slots"></div>
      </div>
    </div>
  </div><!-- /sec-overview -->

  <div id="sec-work" style="display:none;">
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
  </div><!-- /sec-work -->

  <div id="sec-bugs" style="display:none;">
    <nav class="tabs" id="bug-tabs"></nav>
    <div class="stats" id="bug-stats"></div>

    <div class="bug-controls" id="bug-controls">
      <div class="row">
        <div class="grp search-wrap">
          <span class="lbl">Search</span>
          <input type="search" id="bq" placeholder="Search key, summary, description, root cause, notes…">
        </div>
        <div class="grp">
          <span class="lbl">Signal</span>
          <div class="chips" id="bug-signals"></div>
        </div>
      </div>
      <div class="row" style="margin-top:12px;">
        <div class="grp">
          <span class="lbl">Owner</span>
          <select id="b-owner"><option value="">All</option></select>
        </div>
        <div class="grp">
          <span class="lbl">Priority</span>
          <select id="b-priority"><option value="">All</option></select>
        </div>
        <div class="grp">
          <span class="lbl">Component</span>
          <select id="b-component"><option value="">All</option></select>
        </div>
        <div class="grp">
          <span class="lbl">Family</span>
          <select id="b-family"><option value="">All</option></select>
        </div>
        <div class="grp">
          <span class="lbl">Sort by</span>
          <select id="b-sort">
            <option value="signal">Signal (worst first)</option>
            <option value="idle">Idle (longest)</option>
            <option value="age">Age (oldest)</option>
            <option value="pings">Pings from Oded (most)</option>
            <option value="priority">Priority</option>
            <option value="key">Key</option>
          </select>
        </div>
        <div class="grp">
          <span class="lbl">Options</span>
          <div class="chips">
            <label class="chip on"><input type="checkbox" id="b-openonly" checked> Open only</label>
            <label class="chip"><input type="checkbox" id="b-untriaged"> Untriaged only</label>
            <label class="chip"><input type="checkbox" id="b-flagged"> Has hygiene flag</label>
            <label class="chip on"><input type="checkbox" id="b-hidemuted" checked> Hide muted</label>
          </div>
        </div>
      </div>
      <div class="toolbar">
        <button id="bug-scan" class="btn" title="Re-read every in-scope bug from Jira into the local mirror. No model, no tokens.">↻ Scan Jira</button>
        <span class="ro-note" id="bug-scanline"></span>
        <button class="link" id="b-reset" style="margin-left:auto;">Reset filters</button>
      </div>
    </div>

    <div class="count" id="bug-count"></div>
    <table id="bug-tbl">
      <thead><tr id="bug-head"></tr></thead>
      <tbody id="bug-body"></tbody>
    </table>
    <div id="famview" style="display:none;"></div>
  </div><!-- /sec-bugs -->

  <div id="sec-prs" style="display:none;">
    <div class="rpt-head">
      <div>
        <h2>My feature PRs</h2>
        <p class="sub" id="pr-asof"></p>
      </div>
      <div class="slicer">
        <button id="pr-scan" class="btn primary" title="Re-probe every tracked PR through the gh CLI, and retire features whose PRs are all merged. No model, no tokens.">↻ Scan PRs</button>
        <span id="pr-scan-note" class="ro-note"></span>
      </div>
    </div>
    <div class="kpis" id="pr-kpis"></div>
    <div id="pr-list"></div>
  </div><!-- /sec-prs -->

  <div id="sec-raid" style="display:none;">
    <div class="rpt-head">
      <div>
        <h2>RAID log</h2>
        <p class="sub" id="raid-asof"></p>
      </div>
      <div class="slicer">
        <label class="chip"><input type="checkbox" id="raid-showclosed"> Show closed</label>
        <button id="raid-new" class="btn primary">+ New row</button>
      </div>
    </div>
    <div class="kpis" id="raid-kpis"></div>
    <div id="raid-form" class="raid-new" style="display:none;"></div>
    <table id="raid-tbl">
      <thead><tr>
        <th style="width:8%;">ID</th><th style="width:34%;">Title</th>
        <th style="width:9%;">RAG</th><th style="width:7%;">Score</th>
        <th style="width:11%;">Owner</th><th style="width:12%;">Due</th>
        <th style="width:19%;">Status</th>
      </tr></thead>
      <tbody id="raid-body"></tbody>
    </table>
  </div><!-- /sec-raid -->

  <div id="sec-team" style="display:none;">
    <div class="empty">Team task monitoring lands here.</div>
  </div>
</div>

<script>
let TASKS = __DATA__;
let WORKSPACES = __WSDATA__;
let BUGS = __BUGDATA__;
let OVERVIEW = __OVDATA__;
let PRS = __PRDATA__;
let RAID = __RAIDDATA__;
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

// ---- workspace alignment (manual-review checkout) progress banner ----
const ALIGN_ICONS={"checked-out":"✓","skipped-dirty":"⚠","error":"✕","not-found":"✕","already":"✓"};
const TONE_COLOR={ok:"#4ade80",warn:"#fbbf24",bad:"#f87171"};
function alignRepoRow(r){
  const ic=r.icon||ALIGN_ICONS[r.action]||"·";
  const col=r.tone?(TONE_COLOR[r.tone]||"#94a3b8")
    :(r.action==="checked-out"?"#4ade80":(r.action==="skipped-dirty"?"#fbbf24":"#f87171"));
  const tag=r.role==="review"?" <b style='color:#38bdf8'>[PR]</b>":"";
  return `<div style="margin-top:3px"><span style="color:${col}">${ic}</span> `
    +`<code>${esc(r.name)}</code>${tag} — ${esc(r.detail||r.action)}</div>`;
}
function renderAlign(job){
  const banner=document.getElementById("align-banner");
  const spin=document.getElementById("align-spin");
  const title=document.getElementById("align-title");
  const body=document.getElementById("align-body");
  banner.style.display="block";
  const running=job.status==="running";
  const reset=job.kind==="reset";
  const prScan=job.kind==="pr-scan";
  spin.style.display=running?"inline-block":"none";
  if(running){
    const prog=job.total?` (${job.repos.length}/${job.total})`:"";
    if(prScan){
      title.textContent=`Scanning PRs${prog}`+(job.current?" · "+job.current:"");
    }else{
      const verb=reset?"Resetting":"Aligning";
      title.textContent=`${verb} ${esc(job.slot)}${prog}${job.current?" · "+esc(job.current):""}`;
    }
  }else if(job.status==="error"){
    const what=prScan?"PR scan":(reset?"Reset":"Alignment");
    title.innerHTML=`<span style="color:#f87171">✕ ${what} failed</span>`;
  }else{
    const done=prScan?`✓ Probed ${job.repos.length} PRs`
      :(reset?`✓ ${esc(job.slot)} reset to main`:`✓ Aligned ${esc(job.slot)}`);
    title.innerHTML=`<span style="color:#4ade80">${done}</span>`;
  }
  const rows=(job.repos||[]).map(alignRepoRow).join("");
  const msg=job.message?`<div style="margin-top:6px;color:#cbd5e1">${esc(job.message)}</div>`:"";
  body.innerHTML=rows+msg;
  if(running) body.scrollTop=body.scrollHeight;
}
async function pollAlign(jobId,label){
  const banner=document.getElementById("align-banner");
  banner.style.display="block";
  document.getElementById("align-spin").style.display="inline-block";
  document.getElementById("align-title").textContent=label||"Aligning workspace…";
  document.getElementById("align-body").innerHTML="";
  let done=false, last=null;
  while(!done){
    try{
      const r=await fetch("/api/align/"+encodeURIComponent(jobId),{cache:"no-store"});
      if(!r.ok){ document.getElementById("align-title").textContent="Alignment status unavailable"; return null; }
      last=await r.json();
      renderAlign(last);
      done=last.status!=="running";
    }catch(err){ document.getElementById("align-title").textContent="Network error during alignment"; return null; }
    if(!done) await new Promise(res=>setTimeout(res,900));
  }
  return last;
}
document.addEventListener("click",e=>{
  if(e.target && e.target.id==="align-close")
    document.getElementById("align-banner").style.display="none";
});

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
      if(data.align_job) pollAlign(data.align_job);
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
    if(data.align_job) pollAlign(data.align_job);
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

// ============================================================================
// Bugs section — HSI bug monitor (store: ~/tasks/bugs.json, owned by bm.py)
// ============================================================================
const SIGNALS = ["dormant","stalled","slow","moving","closed"];
const SIGNAL_ICON = {moving:"🟢", slow:"🟡", stalled:"🟠", dormant:"🔴", closed:"✅"};
const SIGNAL_COLOR = {moving:"#1AAB40", slow:"#D9B300", stalled:"#E66C37",
                      dormant:"#D64550", closed:"#9B9B9B"};
const TRIAGE_STATES = ["untriaged","in-triage","triaged","deferred"];
const TRIAGE_VERDICTS = ["new","reproduction","duplicate","known-error","not-a-bug","not-ours"];
const BUG_SEVERITIES = ["critical","high","medium","low"];
const WATCH_STATES = ["active","muted","resolved"];
const JIRA_PRI_ORDER = ["Blocker","Critical","High","Medium","Low"];
const FAM_TAB = "__families__";
// Bug tabs are saved filter presets, not categories.
const BUG_TABS = [
  ["open","Open"], ["new","New"], ["untriaged","Untriaged"],
  ["attention","Needs attention"], [FAM_TAB,"Families"], ["all","All"],
];
let bugTab = "open";
let bugSigFilter = new Set();
const bugSort = {key:"signal", dir:1};

function bugList(){ return (BUGS && BUGS.bugs) || []; }
function bugFamilies(){ return (BUGS && BUGS.families) || []; }
function famById(id){ return bugFamilies().find(f=>f.id===id)||null; }
function bugTri(b){ return b.triage||{}; }
function bugNeedsAttention(b){
  return b.open && (["stalled","dormant"].includes(b.signal)
                    || bugTri(b).state==="untriaged" || !b.assignee
                    || (b.flags||[]).includes("pinged"));
}
function bugMatchesTab(b, tab){
  switch(tab){
    case "open":      return b.open;
    case "new":       return b.open && b.is_new;
    case "untriaged": return b.open && (bugTri(b).state||"untriaged")==="untriaged";
    case "attention": return bugNeedsAttention(b);
    case "all":       return true;
  }
  return true;
}
function bugTabCount(tab){
  if(tab===FAM_TAB) return bugFamilies().filter(f=>f.status!=="closed").length;
  return bugList().filter(b=>bugMatchesTab(b,tab)).length;
}
function buildBugTabs(){
  const box=document.getElementById("bug-tabs");
  box.innerHTML=BUG_TABS.map(([k,l])=>
    `<button class="tab${k===bugTab?" on":""}" data-btab="${esc(k)}">${esc(l)}<span class="badge-count">${bugTabCount(k)}</span></button>`).join("");
  box.querySelectorAll(".tab").forEach(t=>t.addEventListener("click",()=>selectBugTab(t.dataset.btab)));
}
function selectBugTab(tab){
  bugTab=tab;
  document.querySelectorAll("#bug-tabs .tab").forEach(t=>t.classList.toggle("on",t.dataset.btab===tab));
  const fam = tab===FAM_TAB;
  document.getElementById("bug-controls").style.display = fam?"none":"";
  document.getElementById("bug-tbl").style.display     = fam?"none":"";
  document.getElementById("bug-count").style.display   = fam?"none":"";
  document.getElementById("bug-stats").style.display   = fam?"none":"";
  document.getElementById("famview").style.display     = fam?"":"none";
  applyBugTabPreset(tab);
  renderBugs();
}
// Tabs are presets: "All" opens the whole mirror (incl. closed + muted);
// every other tab is an open-bug lens.
function applyBugTabPreset(tab){
  if(tab===FAM_TAB) return;
  const set=(id,on)=>{ const cb=document.getElementById(id);
    cb.checked=on; cb.closest(".chip").classList.toggle("on",on); };
  set("b-openonly", tab!=="all");
  set("b-hidemuted", tab!=="all");
}

function buildBugFilters(){
  const bugs=bugList();
  const fill=(id, vals, labelFn)=>{
    const sel=document.getElementById(id); const keep=sel.value;
    sel.innerHTML='<option value="">All</option>'+vals.map(v=>
      `<option value="${esc(v)}">${esc(labelFn?labelFn(v):v)}</option>`).join("");
    if(vals.includes(keep)) sel.value=keep;
  };
  fill("b-owner", [...new Set(bugs.map(b=>b.assignee||"unassigned"))].sort());
  fill("b-priority", JIRA_PRI_ORDER.filter(p=>bugs.some(b=>b.priority===p)));
  fill("b-component", [...new Set(bugs.flatMap(b=>b.components||[]))].sort());
  fill("b-family", bugFamilies().map(f=>f.id), id=>{
    const f=famById(id); return f?`${id} · ${f.title.slice(0,40)}`:id; });
  const sig=document.getElementById("bug-signals");
  if(!sig.dataset.built){
    sig.innerHTML=SIGNALS.map(s=>
      `<label class="chip"><input type="checkbox" data-sig="${s}"> ${SIGNAL_ICON[s]} ${s}</label>`).join("");
    sig.querySelectorAll("input[data-sig]").forEach(cb=>cb.addEventListener("change",()=>{
      cb.closest(".chip").classList.toggle("on",cb.checked);
      if(cb.checked) bugSigFilter.add(cb.dataset.sig); else bugSigFilter.delete(cb.dataset.sig);
      renderBugs();
    }));
    sig.dataset.built="1";
  }
  const scan=document.getElementById("bug-scanline");
  const stale=daysSince((BUGS.last_scan||"").slice(0,10));
  const advice=SERVED ? "hit Scan Jira" : "run <code>bm.py scan</code>";
  scan.innerHTML = BUGS.last_scan
    ? `Jira mirror last scanned ${esc(BUGS.last_scan)}` +
      (stale!=null && stale>=1 ? ` <span class="stale-note">— ${stale}d old; ${advice}</span>` : "")
    : `<span class="stale-note">Never scanned — ${advice}</span>`;
  const btn=document.getElementById("bug-scan");
  btn.style.display = SERVED ? "" : "none";
}

// Server-side Jira pull via bm.do_scan — REST only, no model call.
async function scanBugs(){
  const btn=document.getElementById("bug-scan"), line=document.getElementById("bug-scanline");
  btn.disabled=true; btn.innerHTML='<span class="spin">↻</span> Scanning…';
  line.innerHTML="pulling from Jira…";
  try{
    const r=await fetch("/api/bug-scan",{method:"POST"});
    const j=await r.json();
    if(!r.ok||j.error) throw new Error(j.error||"scan failed");
    BUGS=j; renderBugs(); buildSections();
    const s=j.scan||{};
    const bits=[(s.fetched||0)+" mirrored"];
    if((s.new||[]).length) bits.push("<b>"+s.new.length+" new</b>: "+s.new.slice(0,4).map(esc).join(", "));
    if(s.changed) bits.push(s.changed+" changed");
    if((s.closed||[]).length) bits.push(s.closed.length+" closed");
    if(s.activity) bits.push(s.activity+" refreshed");
    if(s.pings) bits.push(s.pings+" 📣");
    if(s.ci_runs) bits.push(s.ci_runs+" CI links");
    line.innerHTML=bits.join(" · ");
  }catch(e){
    line.innerHTML='<span class="stale-note">Scan failed: '+esc(e.message)+'</span>';
  }finally{
    btn.disabled=false; btn.textContent="↻ Scan Jira";
  }
}
document.getElementById("bug-scan").addEventListener("click",scanBugs);

function bugFilters(){
  const q=(document.getElementById("bq").value||"").toLowerCase().trim();
  const owner=document.getElementById("b-owner").value;
  const pri=document.getElementById("b-priority").value;
  const comp=document.getElementById("b-component").value;
  const fam=document.getElementById("b-family").value;
  const openOnly=document.getElementById("b-openonly").checked;
  const untriOnly=document.getElementById("b-untriaged").checked;
  const flagged=document.getElementById("b-flagged").checked;
  const hideMuted=document.getElementById("b-hidemuted").checked;
  return bugList().filter(b=>{
    if(!bugMatchesTab(b,bugTab)) return false;
    if(openOnly && !b.open) return false;
    if(untriOnly && (bugTri(b).state||"untriaged")!=="untriaged") return false;
    if(flagged && !(b.flags||[]).length) return false;
    if(hideMuted && b.watch==="muted") return false;
    if(bugSigFilter.size && !bugSigFilter.has(b.signal)) return false;
    if(owner && (b.assignee||"unassigned")!==owner) return false;
    if(pri && b.priority!==pri) return false;
    if(comp && !(b.components||[]).includes(comp)) return false;
    if(fam && bugTri(b).family!==fam) return false;
    if(q){
      const hay=[b.key,b.summary,b.description,b.assignee,b.status,
                 bugTri(b).root_cause,bugTri(b).next_step,b.family_title,
                 (b.components||[]).join(" "),(b.labels||[]).join(" "),
                 (b.notes||[]).join(" ")].join(" ").toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
}
function bugSortVal(b,key){
  switch(key){
    case "signal": return SIGNALS.indexOf(b.signal);
    case "idle": return -(b.idle_days==null?-1:b.idle_days);
    case "age": return -(b.age_days||0);
    case "priority": { const i=JIRA_PRI_ORDER.indexOf(b.priority); return i<0?99:i; }
    case "key": return b.key||"";
    case "status": return b.status||"";
    case "owner": return b.assignee||"~";
    case "triage": return TRIAGE_STATES.indexOf(bugTri(b).state||"untriaged");
    case "family": return bugTri(b).family||"~";
    case "pings": return -(b.oded_pings||0);
    case "ci": return -((b.ci_runs||[]).length);
    case "summary": return (b.summary||"").toLowerCase();
  }
  return "";
}
const BUG_COLS=[
  {key:"signal", label:""}, {key:"key", label:"Bug"}, {key:"summary", label:"Summary"},
  {key:"priority", label:"Pri"}, {key:"status", label:"Status"}, {key:"owner", label:"Owner"},
  {key:"idle", label:"Idle"}, {key:"age", label:"Age"}, {key:"pings", label:"\uD83D\uDCE3"},
  {key:"ci", label:"CI"},
  {key:"triage", label:"Triage"}, {key:"family", label:"Fam"}, {key:"flags", label:"Flags"},
];
function bugCell(b,key){
  switch(key){
    case "signal": return `<td title="${esc(b.signal)}">${SIGNAL_ICON[b.signal]||""}</td>`;
    case "key": return `<td class="id"><a href="${esc(b.url)}" target="_blank" rel="noopener"
        onclick="event.stopPropagation()">${esc(b.key)}</a></td>`;
    case "summary": return `<td class="title-cell">${esc(b.summary)}${
        b.family_title?`<div class="feature">🧬 ${esc(b.family_title)}</div>`:""}</td>`;
    case "priority": return `<td><span class="badge p-${esc((b.priority||"").toLowerCase())}">${esc(b.priority||"—")}</span></td>`;
    case "status": return `<td><span class="badge sg-${esc(b.signal)}">${esc(b.status||"—")}</span></td>`;
    case "owner": return `<td>${esc(b.assignee||"unassigned")}</td>`;
    case "idle": return `<td>${b.idle_days==null?"—":b.idle_days+"d"}</td>`;
    case "age": return `<td>${b.age_days==null?"—":b.age_days+"d"}</td>`;
    case "pings": { const n=b.oded_pings||0;
      const man=b.manual_pings||0;
      const src=man?` (${b.jira_pings||0} in Jira + ${man} manual)`:"";
      return `<td title="Pings from Oded${src}${b.last_ping?` · last ${esc(b.last_ping)}`:""}"${
        n?' style="font-weight:700;color:#c2410c;"':''}>${n||"—"}</td>`; }
    case "ci": { const runs=b.ci_runs||[], r=runs[0];
      if(!r) return `<td>—</td>`;
      const more=runs.length>1?`<span class="feature">+${runs.length-1}</span>`:"";
      return `<td><a href="${esc(r.url)}" target="_blank" rel="noopener"`
        +` onclick="event.stopPropagation()"`
        +` title="${esc(r.at+(r.station?" · "+r.station:""))}">run</a>${more}</td>`; }
    case "triage": { const s=bugTri(b).state||"untriaged";
      const v=bugTri(b).verdict;
      return `<td><span class="badge tr-${esc(s)}">${esc(s)}</span>${v?`<div class="feature">${esc(v)}</div>`:""}</td>`; }
    case "family": return `<td>${esc(bugTri(b).family||"—")}</td>`;
    case "flags": return `<td>${(b.flags||[]).map(f=>
        `<span class="flag${["untriaged","unassigned","overdue","pinged"].includes(f)?" warn":""}">${esc(f)}</span>`).join("")||"—"}</td>`;
  }
  return "<td></td>";
}
function bugDetail(b,span){
  const t=bugTri(b), a=b.activity||{};
  const opts=(list,cur)=>list.map(v=>`<option value="${esc(v)}"${v===cur?" selected":""}>${esc(v)}</option>`).join("");
  const famOpts=bugFamilies().map(f=>
    `<option value="${esc(f.id)}"${f.id===t.family?" selected":""}>${esc(f.id+" · "+f.title.slice(0,44))}</option>`).join("");
  const editor=`
    <div class="editor" data-key="${esc(b.key)}">
      <h4>Triage &amp; monitoring — local only, never written to Jira</h4>
      <div class="fields">
        <label>Triage state<select class="b-state">${opts(TRIAGE_STATES,t.state||"untriaged")}</select></label>
        <label>Verdict<select class="b-verdict"><option value="">—</option>${opts(TRIAGE_VERDICTS,t.verdict)}</select></label>
        <label>Severity call<select class="b-severity"><option value="">—</option>${opts(BUG_SEVERITIES,t.severity_call)}</select></label>
        <label>Family<select class="b-family2"><option value="">—</option>${famOpts}</select></label>
        <label>Watch<select class="b-watch">${opts(WATCH_STATES,b.watch||"active")}</select></label>
        <label>Pings from Oded<input type="number" class="b-pings" min="0" style="width:64px;" value="${b.oded_pings||0}" title="Overrides the auto-derived count until the next scan"></label>
        <label>&nbsp;<button class="b-ping" title="Oded chased outside Jira — bump the manual counter and save">📣 +1 ping</button></label>
      </div>
      <div class="fields">
        <label style="flex:1;">Root cause<input type="text" class="b-root" style="min-width:320px;" value="${esc(t.root_cause||"")}"></label>
        <label style="flex:1;">Next step<input type="text" class="b-next" style="min-width:320px;" value="${esc(t.next_step||"")}"></label>
        <label>Owner (driver)<input type="text" class="b-owner2" value="${esc(t.owner||"")}"></label>
      </div>
      <div class="fields">
        <label style="flex:1;">Append note<textarea class="b-note" placeholder="What you observed / decided"></textarea></label>
      </div>
      <div class="actions">
        <button class="primary b-save">Save</button>
        <span class="msg"></span>
        ${SERVED?"":'<span class="hint">read-only — run tm.py serve to edit</span>'}
      </div>
    </div>`;
  return `<tr class="detail"><td colspan="${span}"><div class="detail-box">
    <div class="kv">
      <span><b>Jira</b> <a href="${esc(b.url)}" target="_blank" rel="noopener">${esc(b.key)}</a></span>
      <span><b>Project</b> ${esc(b.project||"—")}</span>
      <span><b>Reporter</b> ${esc(b.reporter||"—")}</span>
      <span><b>Components</b> ${esc((b.components||[]).join(", ")||"—")}</span>
      <span><b>Labels</b> ${esc((b.labels||[]).join(", ")||"—")}</span>
    </div>
    <div class="kv">
      <span><b>Milestone</b> ${esc(b.milestone||"—")}</span>
      <span><b>Sprint</b> ${esc((b.sprints||[]).join(", ")||"—")}</span>
      <span><b>Estimate</b> ${b.estimate_h?esc(b.estimate_h)+"h":"—"}</span>
      <span><b>Due</b> ${esc(b.duedate||"—")}</span>
      <span><b>Created</b> ${esc(b.created||"—")}</span>
      <span><b>First seen</b> ${esc(b.first_seen||"—")}</span>
    </div>
    <div class="kv">
      <span><b>Last activity</b> ${esc(b.last_activity||"—")} (${b.idle_days==null?"—":b.idle_days+"d idle"})</span>
      <span><b>Comments</b> ${esc(a.comment_count||0)}${a.last_comment?` (last ${esc(a.last_comment)} by ${esc(a.last_comment_by||"—")})`:""}</span>
      <span><b>Field changes</b> ${esc(a.change_count||0)}</span>
      <span><b>Last status change</b> ${esc(a.last_status_change||"—")}</span>
      <span><b>Pings from Oded</b> ${b.oded_pings||0}${
        b.manual_pings?` (${b.jira_pings||0} in Jira + ${b.manual_pings} manual)`:""}${
        b.last_ping?` (last ${esc(b.last_ping)}${b.days_since_ping==null?"":`, ${b.days_since_ping}d ago`})`:""}</span>
    </div>
    ${(b.ci_runs||[]).length?`<div><h4>CI runs <span class="feature">scraped from Jira comments</span></h4><div class="kv">${
      b.ci_runs.map(r=>`<span><a href="${esc(r.url)}" target="_blank" rel="noopener">run ${esc(r.run_id)}</a> ${esc(r.at)}${r.station?` · ${esc(r.station)}`:""}</span>`).join("")}</div></div>`:""}
    ${(b.links||[]).length?`<div><h4>Jira links</h4><div class="kv">${
      b.links.map(l=>`<span>${esc(l.type||"relates")} <a href="${JIRA_BASE}${esc(l.key)}" target="_blank" rel="noopener">${esc(l.key)}</a></span>`).join("")}</div></div>`:""}
    ${b.description?`<div><h4>Description</h4><pre style="white-space:pre-wrap;margin:0;font-size:12px;">${esc(b.description)}</pre></div>`:""}
    ${(b.changes||[]).length?`<div><h4>Observed changes</h4><ul class="notes">${
      b.changes.slice(-6).reverse().map(c=>`<li>${esc(c.at)}: ${esc(c.diff.join("; "))}</li>`).join("")}</ul></div>`:""}
    ${(b.notes||[]).length?`<div><h4>Notes</h4><ul class="notes">${
      b.notes.slice(-8).reverse().map(n=>`<li>${esc(n)}</li>`).join("")}</ul></div>`:""}
    ${editor}
  </div></td></tr>`;
}
function wireBugEditor(row,b){
  const ed=row.querySelector(".editor"); if(!ed) return;
  const btn=ed.querySelector(".b-save"), msg=ed.querySelector(".msg");
  const pingBtn=ed.querySelector(".b-ping");
  if(!SERVED){ btn.disabled=true; if(pingBtn) pingBtn.disabled=true; return; }
  async function post(payload,who){
    who.disabled=true; msg.textContent="Saving…";
    try{
      const res=await fetch("/api/bug/"+encodeURIComponent(b.key),{
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload)});
      const data=await res.json();
      if(!res.ok){ msg.textContent="Error: "+(data.error||res.status); who.disabled=false; return; }
      BUGS=data.bugs; buildBugFilters(); buildBugTabs(); buildSections(); renderBugs();
      msg.textContent="Saved.";
    }catch(err){ msg.textContent="Network error: "+err; who.disabled=false; }
  }
  btn.addEventListener("click",e=>{
    e.stopPropagation();
    post({
      triage_state: ed.querySelector(".b-state").value,
      verdict:      ed.querySelector(".b-verdict").value,
      severity:     ed.querySelector(".b-severity").value,
      family:       ed.querySelector(".b-family2").value,
      watch:        ed.querySelector(".b-watch").value,
      oded_pings:   ed.querySelector(".b-pings").value,
      root_cause:   ed.querySelector(".b-root").value,
      next_step:    ed.querySelector(".b-next").value,
      owner:        ed.querySelector(".b-owner2").value,
      note:         ed.querySelector(".b-note").value.trim(),
    }, btn);
  });
  // Bumping a ping is its own one-click action so it never drags along a
  // half-filled triage form.
  pingBtn.addEventListener("click",e=>{ e.stopPropagation(); post({ping:1}, pingBtn); });
  ed.querySelectorAll("select,input,textarea,button").forEach(el=>
    el.addEventListener("click",e=>e.stopPropagation()));
}
function renderBugStats(){
  const bugs=bugList(), open=bugs.filter(b=>b.open);
  const n=f=>open.filter(f).length;
  const cards=[
    ["Open", open.length],
    ["New", n(b=>b.is_new)],
    ["Untriaged", n(b=>(bugTri(b).state||"untriaged")==="untriaged")],
    ["Stalled", n(b=>["stalled","dormant"].includes(b.signal))],
    ["Unassigned", n(b=>!b.assignee)],
    ["Pinged", n(b=>b.oded_pings)],
    ["Families", bugFamilies().filter(f=>f.status!=="closed").length],
  ];
  document.getElementById("bug-stats").innerHTML=cards.map(([l,v])=>
    `<div class="stat"><div class="n">${v}</div><div class="l">${esc(l)}</div></div>`).join("");
}
function renderFamilies(){
  const box=document.getElementById("famview");
  const fams=bugFamilies();
  if(!fams.length){
    box.innerHTML=`<div class="empty">No duplicate families yet.<br>
      Run <code>bm.py dups</code> to get proposals, then <code>bm.py family new …</code> to confirm one.</div>`;
    return;
  }
  const byKey=Object.fromEntries(bugList().map(b=>[b.key,b]));
  box.innerHTML='<div class="fam-grid">'+fams.map(f=>{
    const members=(f.members||[]).map(k=>byKey[k]).filter(Boolean);
    const openN=members.filter(m=>m.open).length;
    return `<div class="fam-card${f.status==="closed"?" closed":""}">
      <div class="fam-title">${esc(f.id)} · ${esc(f.title)}</div>
      <div class="fam-meta">
        <span>${esc(f.status||"open")}</span>
        <span>${openN} open / ${(f.members||[]).length} members</span>
        <span>primary ${esc(f.primary||"—")}</span>
        ${f.created?`<span>since ${esc(f.created)}</span>`:""}
      </div>
      ${f.root_cause?`<div style="font-size:13px;"><b style="color:var(--muted);">Root cause</b> ${esc(f.root_cause)}</div>`:""}
      <div class="fam-members">${(f.members||[]).map(k=>{
        const m=byKey[k];
        if(!m) return `<div class="m"><span>${esc(k)}</span><span class="feature">not in store</span></div>`;
        return `<div class="m${k===f.primary?" primary":""}">
          <span>${SIGNAL_ICON[m.signal]||""}</span>
          <a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(k)}</a>
          <span class="badge sg-${esc(m.signal)}">${esc(m.status||"—")}</span>
          <span class="feature" style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(m.summary)}</span>
        </div>`;}).join("")}</div>
      ${(f.notes||[]).length?`<ul class="notes">${f.notes.slice(-3).map(n=>`<li>${esc(n)}</li>`).join("")}</ul>`:""}
    </div>`;}).join("")+'</div>';
}
function renderBugs(){
  if(bugTab===FAM_TAB){ renderFamilies(); return; }
  renderBugStats();
  document.getElementById("bug-head").innerHTML=BUG_COLS.map(c=>{
    const arr=bugSort.key===c.key?(bugSort.dir>0?"▲":"▼"):"";
    return `<th data-bkey="${c.key}">${esc(c.label)}<span class="arr">${arr}</span></th>`;
  }).join("");
  document.querySelectorAll("#bug-head th").forEach(th=>th.addEventListener("click",()=>{
    const k=th.dataset.bkey;
    if(bugSort.key===k) bugSort.dir*=-1; else { bugSort.key=k; bugSort.dir=1; }
    const dd=document.getElementById("b-sort");
    if([...dd.options].some(o=>o.value===k)) dd.value=k;
    renderBugs();
  }));
  const rows=bugFilters().slice().sort((x,y)=>{
    const a=bugSortVal(x,bugSort.key), b=bugSortVal(y,bugSort.key);
    return (a<b?-1:a>b?1:0)*bugSort.dir || (x.key<y.key?-1:1);
  });
  const body=document.getElementById("bug-body");
  body.innerHTML="";
  if(!rows.length){
    body.innerHTML=`<tr><td class="empty" colspan="${BUG_COLS.length}">No bugs match the current filters.</td></tr>`;
  }
  rows.forEach(b=>{
    const tr=document.createElement("tr"); tr.className="task";
    tr.innerHTML=BUG_COLS.map(c=>bugCell(b,c.key)).join("");
    const tpl=document.createElement("template");
    tpl.innerHTML=bugDetail(b,BUG_COLS.length);
    const det=tpl.content.firstChild; det.style.display="none";
    tr.addEventListener("click",()=>{
      const show=det.style.display==="none";
      det.style.display=show?"":"none";
      if(show) wireBugEditor(det,b);
    });
    body.appendChild(tr); body.appendChild(det);
  });
  document.getElementById("bug-count").textContent=
    `Showing ${rows.length} of ${bugList().length} bugs`;
}
["bq","b-owner","b-priority","b-component","b-family"].forEach(id=>{
  const el=document.getElementById(id);
  el.addEventListener(id==="bq"?"input":"change",renderBugs);
});
document.getElementById("b-sort").addEventListener("change",e=>{
  bugSort.key=e.target.value; bugSort.dir=1; renderBugs(); });
["b-openonly","b-untriaged","b-flagged","b-hidemuted"].forEach(id=>{
  const cb=document.getElementById(id);
  cb.addEventListener("change",()=>{ cb.closest(".chip").classList.toggle("on",cb.checked); renderBugs(); });
});
document.getElementById("b-reset").addEventListener("click",()=>{
  document.getElementById("bq").value="";
  ["b-owner","b-priority","b-component","b-family"].forEach(id=>{document.getElementById(id).value="";});
  document.querySelectorAll("#bug-signals input[data-sig]").forEach(cb=>{
    cb.checked=false; cb.closest(".chip").classList.remove("on"); });
  bugSigFilter.clear();
  [["b-untriaged",false],["b-flagged",false]].forEach(([id,on])=>{
    const cb=document.getElementById(id); cb.checked=on; cb.closest(".chip").classList.toggle("on",on); });
  applyBugTabPreset(bugTab);
  bugSort.key="signal"; bugSort.dir=1; document.getElementById("b-sort").value="signal";
  renderBugs();
});

// ============================================================================
// Overview storefront — team status at a glance. Everything is derived from
// data already on the page; no extra fetches and no Jira calls.
// ============================================================================
// Power BI default theme palette — categorical, high contrast, colour-safe order.
const PALETTE = ["#118DFF","#E66C37","#6B007B","#E044A7","#744EC2","#D9B300",
                 "#12239E","#1AAB40","#D64550","#197278","#3599B8","#9B9B9B"];
const PBI = {blue:"#118DFF", navy:"#12239E", orange:"#E66C37", purple:"#6B007B",
             pink:"#E044A7", violet:"#744EC2", gold:"#D9B300", green:"#1AAB40",
             red:"#D64550", teal:"#197278", grey:"#9B9B9B"};
const RAG_RANK = {red:0, orange:1, yellow:2, green:3};

function ovIps(){ return (OVERVIEW && OVERVIEW.ips) || []; }
function ipOf(b){
  const comps=(b.components||[]).map(c=>String(c).toLowerCase());
  for(const ip of ovIps())
    if(comps.some(c=>(ip.hints||[]).some(h=>c.includes(String(h).toLowerCase())))) return ip.name;
  return "Other";
}
function daysUntil(iso){
  if(!iso) return null;
  return Math.round((new Date(iso+"T00:00:00") - new Date(new Date().toDateString()))/86400000);
}
// Local-calendar YYYY-MM-DD. toISOString() would shift by the UTC offset and
// silently misplace "today" for anyone east of Greenwich.
function isoDay(d){
  return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")
         +"-"+String(d.getDate()).padStart(2,"0");
}

// ---- SVG primitives -------------------------------------------------------
// Charts render as ONE svg with an intrinsic viewBox and no explicit height, so
// they scale proportionally inside the tile. That keeps text crisp and removes
// the plot/label alignment drift a split-svg layout suffers from.
function svgDonut(segs, size, thick, capt){
  size=size||152; thick=thick||20;
  const total=segs.reduce((a,s)=>a+s.v,0);
  const cx=size/2, r=(size-thick)/2, circ=2*Math.PI*r;
  let off=0;
  const arcs=segs.filter(s=>s.v>0).map(s=>{
    const len=total?circ*s.v/total:0;
    const el='<circle class="seg" cx="'+cx+'" cy="'+cx+'" r="'+r+'" fill="none" stroke="'+s.c+'" stroke-width="'+thick+'"'
      +' stroke-dasharray="'+len.toFixed(2)+' '+(circ-len).toFixed(2)+'" stroke-dashoffset="'+(-off).toFixed(2)+'">'
      +'<title>'+esc(s.l)+': '+s.v+(total?' ('+Math.round(100*s.v/total)+'%)':'')+'</title></circle>';
    off+=len; return el;
  }).join("");
  return '<svg class="donut" width="'+size+'" height="'+size+'" viewBox="0 0 '+size+' '+size+'" style="flex:none;">'
    +'<g transform="rotate(-90 '+cx+' '+cx+')">'
    +'<circle cx="'+cx+'" cy="'+cx+'" r="'+r+'" fill="none" stroke="var(--track)" stroke-width="'+thick+'"/>'
    +arcs+'</g>'
    +'<text x="'+cx+'" y="'+(cx-2)+'" text-anchor="middle" dominant-baseline="middle"'
    +' style="font:800 30px system-ui;fill:var(--text);">'+total+'</text>'
    +'<text x="'+cx+'" y="'+(cx+20)+'" text-anchor="middle" dominant-baseline="middle"'
    +' style="font:700 9px system-ui;fill:var(--muted);letter-spacing:.12em;">'
    +esc(capt||"OPEN")+'</text></svg>';
}
// Semicircular gauge — the Power BI KPI staple. Value is a percentage.
function svgGauge(pct, label, color, sub){
  pct=Math.max(0,Math.min(100,pct||0));
  const w=200, h=124, cx=w/2, cy=104, r=76, t=18;
  const pt=(deg,rad)=>[cx+rad*Math.cos(Math.PI*deg/180), cy+rad*Math.sin(Math.PI*deg/180)];
  const arc=(from,to,rad,col,width)=>{
    const a=pt(from,rad), b=pt(to,rad);
    return '<path d="M'+a[0].toFixed(1)+' '+a[1].toFixed(1)+' A'+rad+' '+rad+' 0 '
      +((to-from)>180?1:0)+' 1 '+b[0].toFixed(1)+' '+b[1].toFixed(1)+'" fill="none" stroke="'+col
      +'" stroke-width="'+width+'" stroke-linecap="round"/>';
  };
  return '<svg viewBox="0 0 '+w+' '+h+'" width="100%" style="display:block;max-width:220px;margin:0 auto;">'
    +arc(180,359.9,r,"var(--track)",t)
    +(pct>0.6?arc(180,180+180*pct/100,r,color,t):"")
    +'<text x="'+cx+'" y="'+(cy-18)+'" text-anchor="middle" style="font:800 32px system-ui;fill:'+color+';">'
      +Math.round(pct)+'<tspan style="font:700 15px system-ui;">%</tspan></text>'
    +'<text x="'+cx+'" y="'+(cy-1)+'" text-anchor="middle" style="font:700 10px system-ui;fill:var(--muted);letter-spacing:.07em;">'
      +esc(label||"")+'</text>'
    +'<text x="'+(cx-r)+'" y="'+(cy+17)+'" text-anchor="middle" style="font:9px system-ui;fill:var(--muted);">0</text>'
    +'<text x="'+(cx+r)+'" y="'+(cy+17)+'" text-anchor="middle" style="font:9px system-ui;fill:var(--muted);">100</text>'
    +'<title>'+esc(sub||label||"")+'</title></svg>';
}
function svgLegend(segs, total){
  return segs.filter(s=>s.v>0).map(s=>'<div class="li">'
    +'<span class="sw" style="background:'+s.c+'"></span>'
    +'<span style="overflow:hidden;text-overflow:ellipsis;">'+esc(s.l)+'</span>'
    +'<span class="v">'+s.v+(total?' \u00b7 '+Math.round(100*s.v/total)+'%':'')+'</span></div>').join("");
}
// Stacked area over a daily x-axis. series = [{l,c,vals:[]}], stacked bottom-up.
function svgStackedArea(labels, series, w, h){
  w=w||780; h=h||250;
  const L=34, R=58, T=14, B=34, iw=w-L-R, ih=h-T-B, n=labels.length;
  if(!n) return '<div class="empty">No history yet.</div>';
  const tops=labels.map((_,i)=>series.reduce((a,s)=>a+(s.vals[i]||0),0));
  const max=Math.max(1,...tops);
  const X=i=>L+(n===1?iw/2:iw*i/(n-1));
  const Y=v=>T+ih-ih*v/max;
  const uid="g"+Math.random().toString(36).slice(2,8);
  const defs=[], bands=[], caps=[];
  let base=labels.map(()=>0);
  series.forEach((s,si)=>{
    const top=labels.map((_,i)=>base[i]+(s.vals[i]||0));
    const up=top.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)).join(" ");
    const down=base.map((_,i)=>X(n-1-i).toFixed(1)+','+Y(base[n-1-i]).toFixed(1)).join(" ");
    defs.push('<linearGradient id="'+uid+si+'" x1="0" y1="0" x2="0" y2="1">'
      +'<stop offset="0%" stop-color="'+s.c+'" stop-opacity=".95"/>'
      +'<stop offset="100%" stop-color="'+s.c+'" stop-opacity=".55"/></linearGradient>');
    bands.push('<polygon points="'+up+' '+down+'" fill="url(#'+uid+si+')"/>'
      +'<polyline points="'+up+'" fill="none" stroke="'+s.c+'" stroke-width="2" stroke-linejoin="round"/>');
    const ly=Math.max(T+12,(Y(top[n-1])+Y(base[n-1]))/2);
    caps.push('<circle cx="'+X(n-1).toFixed(1)+'" cy="'+Y(top[n-1]).toFixed(1)+'" r="3.6" fill="'+s.c
      +'" stroke="var(--panel)" stroke-width="1.6"/>'
      +'<text x="'+(w-R+9)+'" y="'+(ly+3).toFixed(1)+'" style="font:800 15px system-ui;fill:'+s.c+';">'
      +(s.vals[n-1]||0)+'</text>'
      +'<text x="'+(w-R+9)+'" y="'+(ly+15).toFixed(1)+'" style="font:700 8.5px system-ui;fill:var(--muted);letter-spacing:.1em;">'
      +esc(s.l.toUpperCase())+'</text>');
    base=top;
  });
  const ticks=[0,.5,1].map(f=>{
    const v=Math.round(max*f);
    return '<line x1="'+L+'" y1="'+Y(v)+'" x2="'+(w-R)+'" y2="'+Y(v)+'" stroke="var(--grid)"/>'
      +'<text x="'+(L-7)+'" y="'+(Y(v)+3.5)+'" text-anchor="end" style="font:10px system-ui;fill:var(--muted);">'+v+'</text>';
  }).join("");
  const step=Math.max(1,Math.floor(n/6));
  const xlab=labels.map((d,i)=>(i%step===0||i===n-1)
    ? '<text x="'+X(i)+'" y="'+(T+ih+17)+'" text-anchor="middle" style="font:10px system-ui;fill:var(--muted);">'
      +d.slice(5).replace("-","/")+'</text>' : "").join("");
  return '<svg viewBox="0 0 '+w+' '+h+'" width="100%" style="display:block;">'
    +'<defs>'+defs.join("")+'</defs>'+ticks+bands.join("")+caps.join("")
    +'<line x1="'+L+'" y1="'+(T+ih)+'" x2="'+(w-R)+'" y2="'+(T+ih)+'" stroke="var(--border)"/>'
    +xlab+'</svg>';
}
// Horizontal bar visual — category and value share a header line above the bar.
function barTable(rows, max){
  if(!rows.length) return '<div class="empty">Nothing to show.</div>';
  const m=Math.max(1,max);
  return '<div class="hbars">'+rows.map(r=>'<div class="hb">'
    +'<div class="hb-t"><span>'+esc(r.l)+'</span><b>'+r.v+'</b></div>'
    +'<div class="hb-r"><i style="width:'+(100*r.v/m).toFixed(1)+'%;background:'+(r.c||PBI.blue)+';"></i></div>'
    +(r.sub?'<div class="hb-s">'+esc(r.sub)+'</div>':'')
    +'</div>').join("")+'</div>';
}
// Axis-less inline trend line. Colour carries a second dimension (the signal).
function svgSparkline(vals, color, w, h){
  w=w||104; h=h||26;
  if(!vals || vals.length<2) return '<span style="color:var(--muted);">\u2014</span>';
  const n=vals.length, max=Math.max(1,...vals);
  const X=i=>1.5+(w-3)*i/(n-1), Y=v=>h-2-(h-6)*v/max;
  const pts=vals.map((v,i)=>X(i).toFixed(1)+','+Y(v).toFixed(1)).join(" ");
  const last=vals[n-1], first=vals[0];
  const delta=last-first;
  return '<svg width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'" style="vertical-align:middle;display:block;">'
    +'<polygon points="'+X(0).toFixed(1)+','+(h-1)+' '+pts+' '+X(n-1).toFixed(1)+','+(h-1)+'"'
      +' fill="'+color+'" fill-opacity=".15"/>'
    +'<polyline points="'+pts+'" fill="none" stroke="'+color+'" stroke-width="1.6"'
      +' stroke-linejoin="round" stroke-linecap="round"/>'
    +'<circle cx="'+X(n-1).toFixed(1)+'" cy="'+Y(last).toFixed(1)+'" r="2.4" fill="'+color+'"/>'
    +'<title>'+first+' \u2192 '+last+' open over '+n+' weeks (peak '+max+', '
      +(delta>0?"+"+delta:delta===0?"flat":delta)+')</title></svg>';
}
// Column visual with data labels and a dashed average line.
function svgBars(items, ref, unit, w, h){
  w=w||780; h=h||250;
  const L=34, R=12, T=24, B=44, iw=w-L-R, ih=h-T-B, n=items.length;
  if(!n) return '<div class="empty">Nothing to show.</div>';
  const max=Math.max(1, ...items.map(i=>i.v), ref||0)*1.14;
  const bw=Math.min(74, (iw/n)*0.56);
  const X=i=>L+iw*(i+0.5)/n, Y=v=>T+ih-ih*v/max;
  const ticks=[0,.5,1].map(f=>{
    const v=Math.round(max*f);
    return '<line x1="'+L+'" y1="'+Y(v)+'" x2="'+(w-R)+'" y2="'+Y(v)+'" stroke="var(--grid)"/>'
      +'<text x="'+(L-7)+'" y="'+(Y(v)+3.5)+'" text-anchor="end" style="font:10px system-ui;fill:var(--muted);">'+v+'</text>';
  }).join("");
  const bars=items.map((it,i)=>{
    if(!it.v) return '<text x="'+X(i)+'" y="'+(T+ih-7)+'" text-anchor="middle"'
      +' style="font:12px system-ui;fill:var(--muted);">\u2013</text>';
    return '<rect class="col" x="'+(X(i)-bw/2).toFixed(1)+'" y="'+Y(it.v).toFixed(1)+'" width="'+bw.toFixed(1)
      +'" height="'+(T+ih-Y(it.v)).toFixed(1)+'" rx="4" fill="'+(it.c||PBI.blue)+'">'
      +'<title>'+esc(it.label)+': '+it.v+unit+(it.sub?' ('+esc(it.sub)+')':'')+'</title></rect>'
      +'<text x="'+X(i)+'" y="'+(Y(it.v)-8).toFixed(1)+'" text-anchor="middle"'
      +' style="font:800 13px system-ui;fill:var(--text);">'+it.v+'</text>';
  }).join("");
  const refline=ref ? '<line x1="'+L+'" y1="'+Y(ref)+'" x2="'+(w-R)+'" y2="'+Y(ref)
      +'" stroke="'+PBI.red+'" stroke-width="1.4" stroke-dasharray="6 4"/>'
      +'<text x="'+(w-R)+'" y="'+(Y(ref)-6)+'" text-anchor="end"'
      +' style="font:800 9px system-ui;fill:'+PBI.red+';letter-spacing:.08em;">AVG '+ref+unit+'</text>' : "";
  const xlab=items.map((it,i)=>'<text x="'+X(i)+'" y="'+(T+ih+18)+'" text-anchor="middle"'
      +' style="font:700 11px system-ui;fill:var(--text);">'+esc(it.label)+'</text>'
    +'<text x="'+X(i)+'" y="'+(T+ih+31)+'" text-anchor="middle"'
      +' style="font:9.5px system-ui;fill:var(--muted);">'+esc(it.sub||"")+'</text>').join("");
  return '<svg viewBox="0 0 '+w+' '+h+'" width="100%" style="display:block;">'
    +ticks+bars+refline
    +'<line x1="'+L+'" y1="'+(T+ih)+'" x2="'+(w-R)+'" y2="'+(T+ih)+'" stroke="var(--border)"/>'
    +xlab+'</svg>';
}

// ---- derived series -------------------------------------------------------
function ovBugFlow(){
  const bugs=bugList();
  if(!bugs.length) return {labels:[], series:[]};
  const days=90, today=new Date(new Date().toDateString());
  // Closed bugs occasionally lack a resolution date; fall back to when they
  // last entered their current status so the burn-up still balances.
  const pts=bugs.map(b=>({
    c:(b.created||"").slice(0,10),
    r:(b.resolved || (b.open?null:(b.status_since||b.updated)) || "").slice(0,10),
  })).filter(p=>p.c);
  const labels=[], open=[], closed=[];
  for(let i=days-1;i>=0;i--){
    const d=new Date(today); d.setDate(d.getDate()-i);
    const iso=isoDay(d);
    let c=0, o=0;
    pts.forEach(p=>{
      if(p.c>iso) return;
      if(p.r && p.r<=iso) c++; else o++;
    });
    labels.push(iso); closed.push(c); open.push(o);
  }
  return {labels, series:[
    {l:"Closed", c:"#16a34a", vals:closed},
    {l:"Open",   c:"#2563eb", vals:open},
  ]};
}
const MONTH_ABBR=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
// Days from created \u2192 resolved, bucketed by the month the bug CLOSED. Bucketing
// by close date (not open date) keeps every bucket final \u2014 it can never move.
function ovCloseTime(months){
  months=months||6;
  const byMonth={};
  bugList().forEach(b=>{
    if(b.open) return;
    const c=(b.created||"").slice(0,10);
    const r=(b.resolved || b.status_since || b.updated || "").slice(0,10);
    if(!c || !r) return;
    const d=Math.max(0, Math.round((new Date(r+"T00:00:00")-new Date(c+"T00:00:00"))/86400000));
    (byMonth[r.slice(0,7)] = byMonth[r.slice(0,7)] || []).push(d);
  });
  const now=new Date(), out=[];
  for(let i=months-1;i>=0;i--){
    const d=new Date(now.getFullYear(), now.getMonth()-i, 1);
    const vals=(byMonth[d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")]||[])
      .slice().sort((a,b)=>a-b);
    const mean=vals.length ? vals.reduce((a,v)=>a+v,0)/vals.length : 0;
    out.push({label:MONTH_ABBR[d.getMonth()], v:Math.round(mean*10)/10, n:vals.length,
              sub:vals.length?vals.length+" closed":"", 
              median:vals.length?vals[Math.floor(vals.length/2)]:0});
  }
  return out;
}
// Weekly open-bug count per IP \u2014 the shape of each IP's load over time.
function ovIpTrend(weeks){
  weeks=weeks||12;
  const pts=bugList().map(b=>({ip:ipOf(b),
    c:(b.created||"").slice(0,10),
    r:(b.resolved || (b.open?null:(b.status_since||b.updated)) || "").slice(0,10)}))
    .filter(p=>p.c);
  const today=new Date(new Date().toDateString()), labels=[];
  for(let i=weeks-1;i>=0;i--){ const d=new Date(today); d.setDate(d.getDate()-i*7); labels.push(isoDay(d)); }
  const byIp={};
  pts.forEach(p=>{ byIp[p.ip]=byIp[p.ip]||labels.map(()=>0); });
  labels.forEach((iso,i)=>pts.forEach(p=>{
    if(p.c<=iso && !(p.r && p.r<=iso)) byIp[p.ip][i]++;
  }));
  return {labels, byIp};
}
function ovReviewTasks(){
  return TASKS.filter(t=>t.category==="code-review" && !["done","cancelled"].includes(t.status));
}function ovReviewRows(){
  const cnt={};
  ovReviewTasks().forEach(t=>{ const p=prs(t); if(!p.length) return;
    const s=consolidateReviewStatus(p); cnt[s]=(cnt[s]||0)+1; });
  const colors={pending:"#9B9B9B","in-progress":"#D9B300",commented:"#118DFF",
    "changes-requested":"#D64550","waiting-for-author":"#E66C37",
    "waiting-for-re-review":"#744EC2",approved:"#1AAB40",merged:"#197278"};
  return RV_ORDER.filter(s=>cnt[s]).map(s=>({l:s, v:cnt[s], c:colors[s]}));
}

// ---- development hygiene ---------------------------------------------------
// Each rule is "bad out of total"; a rule with no population is skipped rather
// than scored, so an empty PR queue can't flatter the score.
const HYG_STALE_PR_DAYS = 7;
const HYG_REVIEW_SLA_DAYS = 3;

function hygieneRules(){
  const feats=(PRS.features||[]).filter(f=>!f.complete);
  const prsAll=[].concat.apply([], feats.map(f=>f.prs||[]));
  const live=prsAll.filter(p=>!["merged","closed"].includes(p.status));
  const rvTasks=ovReviewTasks().filter(t=>prs(t).length);
  const rvWaiting=rvTasks.filter(t=>
    !["approved","merged","waiting-for-author"].includes(consolidateReviewStatus(prs(t))));
  const inProgress=TASKS.filter(t=>t.status==="in-progress");

  const rule=(label,bad,total,hint)=>({label, bad, total, hint});
  return [
    rule("CI green on open PRs",
         live.filter(p=>p.status==="ci-fail").length, live.length,
         "red checks block every reviewer behind you"),
    rule("Rebased on target",
         live.filter(p=>p.status==="needs-rebase").length, live.length,
         "behind or conflicting with the base branch"),
    rule("Approved PRs landed",
         live.filter(p=>p.status==="approved").length, live.length,
         "approved but still not merged"),
    rule("PRs moving",
         live.filter(p=>{const d=daysSince((p.updated||"").slice(0,10));
                         return d!=null && d>=HYG_STALE_PR_DAYS;}).length, live.length,
         "no activity for "+HYG_STALE_PR_DAYS+"+ days"),
    rule("Features PLDM-tested",
         feats.filter(f=>!f.pldm||f.pldm==="not-tested").length, feats.length,
         "never exercised on Palladium"),
    rule("Reviews answered in "+HYG_REVIEW_SLA_DAYS+"d",
         rvWaiting.filter(t=>{const d=daysSince(t.updated); return d!=null && d>HYG_REVIEW_SLA_DAYS;}).length,
         rvWaiting.length, "waiting on me past the turnaround target"),
    rule("WIP bound to a workspace",
         inProgress.filter(t=>!t.workspace).length, inProgress.length,
         "in-progress with no checkout assigned"),
  ].filter(r=>r.total>0);
}

function renderHygiene(){
  const rules=hygieneRules();
  const box=document.getElementById("ov-hygiene");
  if(!rules.length){
    document.getElementById("ov-hyg-score").innerHTML="";
    box.innerHTML='<div class="empty">Nothing in flight to measure.</div>';
    return;
  }
  const totBad=rules.reduce((a,r)=>a+r.bad,0), totAll=rules.reduce((a,r)=>a+r.total,0);
  const score=Math.round(100*(1-totBad/Math.max(1,totAll)));
  const sc=score>=85?PBI.green:score>=65?PBI.gold:PBI.red;
  document.getElementById("ov-hyg-score").innerHTML=
    svgGauge(score,"Pipeline clean",sc,totBad+" of "+totAll+" checks failing");

  box.innerHTML=rules.map(r=>{
    const pct=Math.round(100*r.bad/r.total);
    const c=r.bad===0?PBI.green:pct>=34?PBI.red:PBI.gold;
    return '<div class="hyg" style="--hc:'+c+'">'
      +'<div class="hyg-h"><span class="hyg-l">'+esc(r.label)+'</span>'
      +'<span class="hyg-n">'+(r.total-r.bad)+' / '+r.total+'</span></div>'
      +'<div class="hyg-bar"><i style="width:'+(100-pct)+'%"></i></div>'
      +'<div class="hyg-s">'+(r.bad?'<b>'+r.bad+'</b> '+esc(r.hint):"all clear")+'</div>'
      +'</div>';
  }).join("");
}

// ---- render ---------------------------------------------------------------
function renderOverview(){
  const bugs=bugList(), open=bugs.filter(b=>b.open);
  const attention=open.filter(bugNeedsAttention);
  const trend=ovIpTrend(12);
  const raid=((OVERVIEW&&OVERVIEW.raid)||[]).filter(r=>!r.closed);
  const raidHot=raid.filter(r=>["red","orange"].includes(r.rag));
  const rvRows=ovReviewRows();
  const rvTotal=rvRows.reduce((a,r)=>a+r.v,0);
  const rvOpen=rvRows.filter(r=>!["merged","approved"].includes(r.l)).reduce((a,r)=>a+r.v,0);
  const activeTasks=TASKS.filter(t=>!["done","cancelled"].includes(t.status));
  const prog=(OVERVIEW&&OVERVIEW.program)||{};
  const slots=(WORKSPACES&&WORKSPACES.slots)||{};
  const slotNames=Object.keys(slots);
  const busy=slotNames.filter(k=>slots[k]&&slots[k].holder).length;
  const today=isoDay(new Date());

  // --- KPI band ---
  const po=daysUntil(prog.power_on), a1=daysUntil(prog.a1_delivery);
  const kpi=o=>'<div class="kpi" style="--k:'+o.c+'">'
    +(o.spark?'<div class="spark">'+o.spark+'</div>':'')
    +'<div class="l">'+esc(o.label)+'</div>'
    +'<div class="n">'+o.n+(o.unit?'<small>'+esc(o.unit)+'</small>':'')+'</div>'
    +'<div class="s">'+(o.sub||"")+'</div>'
    +(o.pct==null?'':'<div class="bar"><i style="width:'+Math.max(0,Math.min(100,o.pct)).toFixed(0)+'%"></i></div>')
    +'</div>';
  // Program progress: how far into the window that ends at power-on, taking the
  // same-length run-up before it as the start of the window.
  const span=(po!=null)?100*(1-Math.max(0,po)/Math.max(1,po+60)):null;
  const openTrend=Object.values(trend.byIp).reduce((acc,v)=>
    v.map((x,i)=>x+(acc[i]||0)), []);
  document.getElementById("ov-kpis").innerHTML=[
    kpi({c:po!=null&&po<21?PBI.red:(po!=null&&po<45?PBI.orange:PBI.blue), label:"Power-on",
         n:po==null?"\u2014":po, unit:"days", pct:span,
         sub:esc(prog.power_on||"date unknown")+" \u00b7 A1 silicon"}),
    kpi({c:a1!=null&&a1<45?PBI.orange:PBI.navy, label:"A1 big delivery",
         n:a1==null?"\u2014":a1, unit:"days",
         sub:esc(prog.a1_delivery||"date unknown")+" \u00b7 end of MS8"}),
    kpi({c:attention.length?PBI.orange:PBI.green, label:"Open bugs", n:open.length,
         sub:attention.length+" need attention",
         pct:open.length?100*attention.length/open.length:0,
         spark:openTrend.length>1?svgSparkline(openTrend,
           attention.length?PBI.orange:PBI.green, 150, 44):""}),
    kpi({c:rvOpen>6?PBI.orange:PBI.teal, label:"Reviews on me", n:rvOpen,
         sub:rvTotal+" PR group"+(rvTotal===1?"":"s")+" in queue"}),
    kpi({c:raidHot.length?PBI.red:PBI.violet, label:"RAID open", n:raid.length,
         sub:raidHot.length+" red / orange",
         pct:raid.length?100*raidHot.length/raid.length:0}),
    kpi({c:PBI.purple, label:"Workspaces", n:busy+'<small>/'+slotNames.length+'</small>',
         sub:activeTasks.length+" active tasks",
         pct:slotNames.length?100*busy/slotNames.length:0}),
  ].join("");

  // --- report header + sprint slicer ---
  const curSprint=(prog.sprints||[]).find(s=>s.start&&s.end&&s.start<=today&&today<=s.end);
  document.getElementById("ov-asof").innerHTML=
    "As of <b>"+esc(today)+"</b> \u00b7 "+bugs.length+" bugs mirrored \u00b7 "
    +(curSprint?"sprint <b>"+esc(curSprint.name)+"</b>":"between sprints")
    +" \u00b7 "+(SERVED?"live":"static snapshot");
  document.getElementById("ov-sprints").innerHTML='<span class="sl-l">Sprint</span>'
    +(prog.sprints||[]).map(s=>{
      const now=s.start&&s.end&&s.start<=today&&today<=s.end;
      return '<div class="sprint'+(now?" now":"")+'">'+esc(s.name)
        +'<span class="w">'+esc(s.window)+'</span></div>';
    }).join("");

  // --- bug flow ---
  const flow=ovBugFlow();
  document.getElementById("ov-bugflow").innerHTML=svgStackedArea(flow.labels, flow.series);

  // --- backlog health gauges ---
  const gPct=(f)=>open.length?100*open.filter(f).length/open.length:0;
  const triaged=gPct(b=>(bugTri(b).state||"untriaged")!=="untriaged");
  const planned=gPct(b=>!(b.flags||[]).some(f=>f==="no-milestone"||f==="no-sprint"));
  const owned=gPct(b=>!!b.assignee);
  const gcol=p=>p>=80?PBI.green:p>=50?PBI.gold:PBI.red;
  document.getElementById("ov-gauges").innerHTML=open.length
    ? '<div style="display:grid;gap:10px;">'
      +svgGauge(triaged,"TRIAGED",gcol(triaged),
         Math.round(triaged*open.length/100)+" of "+open.length+" open bugs have left untriaged")
      +'<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">'
      +svgGauge(planned,"PLANNED",gcol(planned),"Has both a milestone and a sprint")
      +svgGauge(owned,"ASSIGNED",gcol(owned),"Has an assignee in Jira")
      +'</div></div>'
    : '<div class="empty">No open bugs.</div>';

  // --- mean time to close ---
  const ttc=ovCloseTime(6), ttcLive=ttc.filter(m=>m.n);
  const ttcAll=ttcLive.reduce((a,m)=>a+m.v*m.n,0)/Math.max(1,ttcLive.reduce((a,m)=>a+m.n,0));
  document.getElementById("ov-ttc").innerHTML=ttcLive.length
    ? svgBars(ttc.map(m=>({...m, c:m.v>ttcAll?"#f97316":"#2563eb"})), Math.round(ttcAll*10)/10, "d")
    : '<div class="empty">No bugs closed in the last 6 months.</div>';
  if(ttcLive.length){
    const last=ttcLive[ttcLive.length-1], prev=ttcLive[ttcLive.length-2];
    const dir=!prev ? "" : last.v<prev.v ? " \u2193 faster than "+prev.label
            : last.v>prev.v ? " \u2191 slower than "+prev.label : " \u2192 flat vs "+prev.label;
    document.getElementById("ov-ttc-cap").textContent=
      "Days from created to resolved, by close month. Dashed line = "
      +Math.round(ttcAll)+"d average." + dir + ". Bars above average are orange.";
  }

  // --- donuts ---
  const byIp={}; open.forEach(b=>{ const k=ipOf(b); byIp[k]=(byIp[k]||0)+1; });
  const ipSegs=Object.entries(byIp).sort((a,b)=>b[1]-a[1])
    .map((e,i)=>({l:e[0], v:e[1], c:PALETTE[i%PALETTE.length]}));
  document.getElementById("ov-bugip").innerHTML=svgDonut(ipSegs);
  document.getElementById("ov-bugip-lg").innerHTML=svgLegend(ipSegs, open.length);

  const byOwner={}; open.forEach(b=>{ const k=b.assignee||"Unassigned"; byOwner[k]=(byOwner[k]||0)+1; });
  const ownerSegs=Object.entries(byOwner).sort((a,b)=>b[1]-a[1])
    .map((e,i)=>({l:e[0], v:e[1], c:e[0]==="Unassigned"?"#9ca3af":PALETTE[i%PALETTE.length]}));
  document.getElementById("ov-bugowner").innerHTML=svgDonut(ownerSegs);
  document.getElementById("ov-bugowner-lg").innerHTML=svgLegend(ownerSegs, open.length);

  // --- per-IP scoreboard ---
  const rvByIp={};
  ovReviewTasks().forEach(t=>{
    const hay=((t.title||"")+" "+(t.description||"")+" "
      +prs(t).map(p=>p.repo||"").join(" ")).toLowerCase();
    const ip=ovIps().find(i=>(i.hints||[]).some(h=>hay.includes(String(h).toLowerCase())));
    const k=ip?ip.name:"Other"; rvByIp[k]=(rvByIp[k]||0)+1;
  });
  const rows=ovIps().map(i=>i.name).concat(["Other"]).map(name=>{
    const mine=open.filter(b=>ipOf(b)===name);
    const worst=mine.length?Math.min(...mine.map(b=>{
      const ix=SIGNALS.indexOf(b.signal); return ix<0?99:ix; })):99;
    return {name, owner:((ovIps().find(i=>i.name===name)||{}).owners)||"\u2014",
            n:mine.length, att:mine.filter(bugNeedsAttention).length,
            worst:worst<SIGNALS.length?SIGNALS[worst]:null,
            idle:mine.length?Math.max(...mine.map(b=>b.idle_days||0)):null,
            rv:rvByIp[name]||0};
  }).filter(r=>r.n||r.rv);
  document.getElementById("ov-scoreboard").innerHTML=rows.length
    ? '<table class="sb"><thead><tr><th>IP</th><th class="num">Bugs</th><th class="num">Attn</th>'
      +'<th class="num">Max idle</th><th class="num">Reviews</th><th>12-week trend</th></tr></thead><tbody>'
      +rows.map(r=>'<tr><td><b>'+esc(r.name)+'</b><div class="who">'+esc(r.owner)+'</div></td>'
        +'<td class="num">'+(r.n||"\u2014")+'</td>'
        +'<td class="num"'+(r.att?' style="color:'+PBI.orange+';"':'')+'>'+(r.att||"\u2014")+'</td>'
        +'<td class="num">'+(r.idle==null?"\u2014":r.idle+"d")+'</td>'
        +'<td class="num">'+(r.rv||"\u2014")+'</td>'
        +'<td style="width:130px;">'+svgSparkline(trend.byIp[r.name],
            SIGNAL_COLOR[r.worst]||"#9ca3af")+'</td></tr>').join("")
      +'</tbody></table>'
      +'<p class="cap" style="margin:8px 0 0;">Line = open bugs per week. '
      +'Colour = worst signal in that IP right now '
      +SIGNALS.filter(s=>s!=="closed").reverse().map(s=>SIGNAL_ICON[s]+" "+s).join(" \u00b7 ")+'.</p>'
    : '<div class="empty">No open bugs or reviews.</div>';

  // --- hygiene flags ---
  const fc={}; open.forEach(b=>(b.flags||[]).forEach(f=>{ fc[f]=(fc[f]||0)+1; }));
  const flagColor={untriaged:PBI.red, unassigned:PBI.red, overdue:PBI.red,
    pinged:PBI.orange,
    "no-milestone":PBI.gold, "no-sprint":PBI.gold, "no-estimate":PBI.gold,
    aging:PBI.purple, "no-discussion":PBI.grey};
  const frows=Object.entries(fc).sort((a,b)=>b[1]-a[1]).map(e=>({
    l:e[0], v:e[1], c:flagColor[e[0]]||"#6b7280",
    sub:Math.round(100*e[1]/Math.max(1,open.length))+"% of open bugs"}));
  document.getElementById("ov-flags").innerHTML=barTable(frows, open.length);

  // --- review queue ---
  document.getElementById("ov-reviews").innerHTML=
    barTable(rvRows, Math.max.apply(null, rvRows.map(r=>r.v).concat([1])));

  renderHygiene();

  // --- RAID ---
  const raidSorted=raid.slice().sort((a,b)=>
    ((RAG_RANK[a.rag]==null?9:RAG_RANK[a.rag])-(RAG_RANK[b.rag]==null?9:RAG_RANK[b.rag]))
    || (b.score-a.score));
  document.getElementById("ov-raid").innerHTML=raidSorted.length
    ? '<table class="sb"><thead><tr><th>Row</th><th>Owner</th><th class="num">Score</th>'
      +'<th class="num">Due</th></tr></thead><tbody>'
      +raidSorted.slice(0,8).map(r=>'<tr>'
        +'<td><span class="rag '+esc(r.rag||"gray")+'"></span><b>'+esc(r.id)+'</b>'
        +'<div class="who" style="white-space:normal;">'+esc(r.title)+'</div></td>'
        +'<td>'+esc(r.owner||"\u2014")+'</td>'
        +'<td class="num">'+(r.score||"\u2014")+'</td>'
        +'<td class="num"'+((r.due&&r.due<today)?' style="color:'+PBI.red+';"':'')+'>'
        +esc(r.due||"\u2014")+'</td></tr>').join("")
      +'</tbody></table>'
      +(raidSorted.length>8?'<div class="ro-note" style="margin-top:8px;">+'+(raidSorted.length-8)+' more open rows</div>':'')
    : '<div class="empty">No open RAID rows.</div>';

  // --- needs you now ---
  const feed=[];
  attention.forEach(b=>{
    const why=[], pings=b.oded_pings||0;
    if(pings) why.push("Oded pinged \u00d7"+pings+(b.last_ping?" (last "+b.last_ping+")":""));
    if(["stalled","dormant"].includes(b.signal)) why.push(b.signal+" \u2014 "+b.idle_days+"d idle");
    if((bugTri(b).state||"untriaged")==="untriaged") why.push("untriaged");
    if(!b.assignee) why.push("unassigned");
    feed.push({rank:((b.flags||[]).includes("pinged")||["dormant","stalled"].includes(b.signal))?0:1,
      kind:"bug", label:b.key,
      text:b.summary||"", why:why.join(" \u00b7 "),
      when:(b.idle_days==null?"":b.idle_days+"d idle"), href:b.url, sortv:b.idle_days||0});
  });
  // RAID is capped so a long-standing risk backlog can't crowd out live bugs.
  raidSorted.filter(r=>r.rag==="red"||(r.due&&r.due<today)).slice(0,3).forEach(r=>feed.push({
    rank:r.rag==="red"?0:2, kind:"raid", label:r.id, text:r.title||"",
    why:((r.due&&r.due<today)?"overdue since "+r.due:"red risk")+" \u00b7 "+(r.owner||"unowned"),
    when:r.score?"score "+r.score:"", sortv:r.score||0}));
  ovReviewTasks().forEach(t=>{
    const p=prs(t); if(!p.length) return;
    const s=consolidateReviewStatus(p);
    if(!["pending","in-progress","waiting-for-re-review"].includes(s)) return;
    const age=t.created?daysSince(t.created):0;
    feed.push({rank:s==="pending"?3:2, kind:"review", label:t.id,
      text:(t.title||"").slice(0,110), why:s+" \u00b7 "+p.length+" PR"+(p.length>1?"s":""),
      when:age?age+"d old":"", sortv:age});
  });
  TASKS.filter(t=>t.status==="blocked").forEach(t=>feed.push({
    rank:2, kind:"task", label:t.id, text:t.title||"", why:"blocked",
    when:"", sortv:0}));
  feed.sort((a,b)=>(a.rank-b.rank)||(b.sortv-a.sortv));
  const feedColor={bug:PBI.red, raid:PBI.orange, review:PBI.blue, task:PBI.purple};
  document.getElementById("ov-feed").innerHTML=feed.length
    ? '<div class="feed">'+feed.slice(0,12).map(f=>'<div class="it" style="--fk:'+feedColor[f.kind]+'">'
        +'<span class="pill">'+f.kind+'</span>'
        +'<span class="w"><b>'+(f.href?'<a href="'+esc(f.href)+'" target="_blank" rel="noopener">'+esc(f.label)+'</a>':esc(f.label))+'</b> '
        +esc((f.text||"").slice(0,110))
        +'<div class="why">'+esc(f.why)+'</div></span>'
        +'<span class="when">'+esc(f.when)+'</span></div>').join("")+'</div>'
      +(feed.length>12?'<div class="ro-note" style="margin-top:9px;">+'+(feed.length-12)+' more</div>':'')
    : '<div class="empty">Nothing is screaming for attention.</div>';

  // --- workspaces ---
  document.getElementById("ov-slots").innerHTML=slotNames.map(name=>{
    const h=slots[name]&&slots[name].holder;
    const who=h?(h.type==="task"?(h.task_id+" \u00b7 "+(h.detail||h.tag||"")):(h.label||h.tag||"ad-hoc")):"free";
    return '<div class="slot'+(h?" busy":"")+'"><div class="nm">'+esc(name)+'</div>'
      +'<div class="oc" title="'+esc(who)+'">'+esc(who)+'</div></div>';
  }).join("");
}

// ============================================================================
// Portal section nav — Overview / My Work / Bugs / PRs / Team
// ============================================================================
const SECTIONS=[
  {id:"overview", label:"Overview", ready:true},
  {id:"work",     label:"My Work",  ready:true},
  {id:"bugs",     label:"Bugs",     ready:true},
  {id:"prs",      label:"PRs",      ready:true},
  {id:"raid",     label:"RAID",     ready:true},
  {id:"team",     label:"Team",     ready:false},
];
let activeSection="overview";
function sectionCount(id){
  if(id==="overview"){
    const raid=((OVERVIEW&&OVERVIEW.raid)||[]).filter(r=>!r.closed&&["red","orange"].includes(r.rag));
    return bugList().filter(b=>bugNeedsAttention(b)).length + raid.length;
  }
  if(id==="work") return TASKS.filter(t=>!["done","cancelled"].includes(t.status)).length;
  if(id==="bugs") return bugList().filter(b=>bugNeedsAttention(b)).length;
  if(id==="prs") return (PRS.features||[]).filter(f=>(f.blocked||[]).length).length;
  if(id==="raid") return ((RAID.counts||{}).hot)||0;
  return 0;
}
function buildSections(){
  const box=document.getElementById("sections");
  box.innerHTML=SECTIONS.map(s=>{
    const n=sectionCount(s.id);
    const badge = s.ready ? `<span class="badge-count">${n}</span>`
                          : `<span class="badge-count">soon</span>`;
    const title = s.id==="bugs" ? "Bugs needing attention (stalled, untriaged, or unassigned)"
                : s.id==="work" ? "Active tasks"
                : s.id==="prs" ? "My outgoing feature PRs that are blocked (CI, review or rebase)"
                : s.id==="raid" ? "Open RAID rows at Red or Orange"
                : s.id==="overview" ? "Things demanding attention right now (bugs + hot RAID)"
                : "Team task monitoring — not built yet";
    return `<button class="section-btn${s.id===activeSection?" on":""}" data-section="${s.id}"
      title="${esc(title)}"${s.ready?"":" disabled"}>${esc(s.label)}${badge}</button>`;
  }).join("");
  box.querySelectorAll(".section-btn").forEach(b=>
    b.addEventListener("click",()=>selectSection(b.dataset.section)));
}
function selectSection(id){
  const sec=SECTIONS.find(s=>s.id===id);
  if(!sec || !sec.ready) return;
  activeSection=id;
  document.querySelectorAll(".section-btn").forEach(b=>b.classList.toggle("on",b.dataset.section===id));
  SECTIONS.forEach(s=>{
    const el=document.getElementById("sec-"+s.id);
    if(el) el.style.display = s.id===id ? "" : "none";
  });
  if(id==="bugs"){ buildBugFilters(); buildBugTabs(); selectBugTab(bugTab); }
  else if(id==="work"){ render(); }
  else if(id==="prs"){ renderPrs(); }
  else if(id==="raid"){ renderRaid(); }
  else if(id==="overview"){ renderOverview(); }
}

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
// ============================================================================
// PRs section — outgoing feature PRs mirrored from pr-monitor
// ============================================================================
const PR_STATUS_COLOR={merged:"#1AAB40", "merge-queue":"#118DFF", approved:"#197278", pending:"#9B9B9B",
  "needs-rebase":"#D9B300", "changes-requested":"#E66C37", "ci-fail":"#D64550",
  closed:"#6B7280"};
const PLDM_COLOR={passed:"#1AAB40", failed:"#D64550", running:"#118DFF",
  queued:"#D9B300", cancelled:"#9B9B9B"};

function renderPrs(){
  const feats=PRS.features||[], counts=PRS.counts||{};
  const n=s=>counts[s]||0;
  const totalPrs=feats.reduce((a,f)=>a+f.total,0);
  const blocked=feats.filter(f=>(f.blocked||[]).length);
  const done=feats.filter(f=>f.total&&f.merged===f.total);
  document.getElementById("pr-asof").innerHTML=
    feats.length+" active feature"+(feats.length===1?"":"s")+" \u00b7 "+totalPrs+" PRs \u00b7 "
    +(PRS.last_scan?"scanned <b>"+esc(PRS.last_scan)+"</b>":"<b>never scanned</b>")
    +(PRS.error?' \u00b7 <span style="color:'+PBI.red+'">'+esc(PRS.error)+'</span>':"");

  document.getElementById("pr-kpis").innerHTML=[    kpi({c:blocked.length?PBI.red:PBI.green, label:"Blocked features", n:blocked.length,
         sub:"of "+feats.length+" tracked"}),
    kpi({c:n("ci-fail")?PBI.red:PBI.grey, label:"CI failing", n:n("ci-fail"), sub:"PRs"}),
    kpi({c:n("needs-rebase")?PBI.gold:PBI.grey, label:"Needs rebase", n:n("needs-rebase"), sub:"PRs"}),
    kpi({c:n("changes-requested")?PBI.orange:PBI.grey, label:"Changes req.",
         n:n("changes-requested"), sub:"PRs"}),
    kpi({c:PBI.teal, label:"Approved", n:n("approved"), sub:"waiting to land"}),
    kpi({c:n("merge-queue")?PBI.blue:PBI.grey, label:"Merge queue", n:n("merge-queue"),
         sub:"landing now"}),
    kpi({c:PBI.green, label:"Merged", n:n("merged"),
         sub:done.length?done.length+" feature"+(done.length===1?"":"s")+" complete":"of "+totalPrs+" PRs"}),
  ].join("");

  document.getElementById("pr-list").innerHTML=feats.length
    ? feats.map(prFeature).join("")
    : '<div class="empty">Nothing tracked. Log a feature with the pr-monitor skill.</div>';
}

// CI never runs on its own here — it fires on a `/ci` comment — so "not run" is
// a real, actionable state rather than a gap in the data.
function ciCell(p){
  const f=p.checks_fail||0, r=p.checks_pending||0, s=p.checks_pass||0;
  let label,color,tip;
  if(f){ label="fail"; color=PBI.red;
         tip=f+" failing: "+((p.failed_checks||[]).join(", ")||"see PR"); }
  else if(r){ label="running"; color=PBI.gold; tip=r+" check"+(r===1?"":"s")+" still running"; }
  else if(s){ label="pass"; color=PBI.green; tip="all "+s+" checks green"; }
  else { return '<span class="ci none" title="No check runs on this commit '
           +'\u2014 comment /ci on the PR to trigger it">not run</span>'; }
  return '<span class="ci" style="--ck:'+color+'" title="'+esc(tip)+'">'+label+'</span>';
}

function prFeature(f){
  const c=PR_STATUS_COLOR[f.worst]||PBI.grey;
  const complete=f.total&&f.merged===f.total;
  const rows=f.prs.map(p=>{
    const sc=PR_STATUS_COLOR[p.status]||PBI.grey;
    // A failing check count is the actionable bit; everything else is noise.
    let detail="\u2014";
    if(p.checks_fail) detail='<b>'+p.checks_fail+'F</b>/'+(p.checks_pass||0)+'P';
    else if(p.checks_pass) detail=p.checks_pass+'P'+(p.checks_pending?' \u00b7 '+p.checks_pending+' run':'');
    const ci=ciCell(p);
    const gate=p.status==="merged"?"\u2014"
      :p.status==="merge-queue"?"in queue"
      :p.auto_merge?"auto-merge"
      :(p.merge_state==="BEHIND"||p.merge_state==="DIRTY")?(p.merge_state||"").toLowerCase()
      :(p.review||"").toLowerCase().replace(/_/g," ")||"\u2014";
    return '<tr><td><a href="'+esc(p.url||"#")+'" target="_blank" rel="noopener" title="'+esc(p.title||"")+'">'
      +esc(p.repo)+' #'+p.number+'</a></td>'
      +'<td title="'+esc(p.title||"")+'">'+esc(p.title||"")+'</td>'
      +'<td>'+ci+'</td>'
      +'<td class="chk">'+detail+'</td>'
      +'<td class="chk">'+esc(gate)+'</td>'
      +'<td><span class="st" style="--sk:'+sc+'">'+esc(p.status||"?")+'</span></td></tr>';
  }).join("");
  const pct=f.total?100*f.merged/f.total:0;
  const note=f.last_note
    ? '<div class="note"><b>'+esc(f.last_note.date)+'</b> \u2014 '+esc(f.last_note.text)+'</div>' : "";
  const pldm=f.pldm&&f.pldm!=="not-tested"
    ? '<span class="pldm" style="background:'+(PLDM_COLOR[f.pldm]||PBI.grey)+';color:#fff;">PLDM '+esc(f.pldm)+'</span>'
    : '<span class="pldm">PLDM untested</span>';
  const testable=SERVED&&f.prs.some(p=>p.status!=="closed");
  const btn=testable
    ? '<button class="pldm-btn" data-pldm="'+f.id+'" title="Grab a free workspace slot, '
      +'check it out to this feature\'s PR branches, and dispatch a remote PLDM build">'
      +'\u25b6 PLDM test</button>' : "";
  return '<div class="prf" style="--fk:'+c+'">'
    +'<header><span class="fid">['+f.id+']</span><h3>'+esc(f.name)+'</h3>'
    +btn+pldm+'<span class="st" style="--sk:'+c+'">'+esc(complete?"complete":f.worst)+'</span></header>'
    +(f.description?'<p class="desc">'+esc(f.description)+'</p>':"")
    +'<div class="prog"><i style="width:'+pct.toFixed(0)+'%"></i></div>'
    +'<div class="ro-note">'+f.merged+' of '+f.total+' merged'
    +(complete?' \u2014 retires on the next scan':'')+'</div>'
    +'<table class="pr-tbl"><thead><tr><th style="width:23%">Repo / PR</th><th style="width:31%">Title</th>'
    +'<th style="width:10%">CI</th><th style="width:10%">Checks</th>'
    +'<th style="width:12%">Gate</th><th style="width:14%">Status</th>'
    +'</tr></thead><tbody>'+rows+'</tbody></table>'
    +'<div class="pldm-box" id="pldm-box-'+f.id+'" hidden></div>'
    +note+'</div>';
}

// ---------------- PLDM submission ----------------
// Remote mode builds from a workspace manifest, so submitting is a checkout +
// preflight + dispatch chain rather than a single call — hence the progress log.
const PLDM_IMAGES=["1die_w_gmem","2die_w_gmem","1die_wo_gmem","4die_w_gmem"];

function allSlots(){
  // Only number-named dirs are real multi-repo checkouts; the server rejects
  // the rest, so offering them here would just be a trap.
  const canon=(WORKSPACES&&WORKSPACES.canonical)||[];
  return canon.length?[...canon].sort():WS_NUMBER_NAMES.slice();
}

function freeSlots(){
  const slots=(WORKSPACES&&WORKSPACES.slots)||{};
  return allSlots().filter(s=>!(slots[s]&&slots[s].holder));
}

function pldmForm(f){
  const free=freeSlots();
  const jira=(f.name.match(/[A-Z]+-\d+/)||[])[0];
  const dn=(jira||f.name).toLowerCase().replace(/[^a-z0-9]+/g,"-").slice(0,32);
  const slotOpts='<option value="">auto \u2014 first free slot</option>'
    +allSlots().map(s=>{const f=free.includes(s);
      return '<option value="'+esc(s)+'"'+(f?"":" disabled")+'>'+esc(s)
        +(f?" (free)":" (occupied)")+'</option>';}).join("");
  return '<div class="pldm-form">'
    +'<label>Display name<input id="pl-dn-'+f.id+'" value="'+esc(dn)+'"></label>'
    +'<label>Image type<select id="pl-img-'+f.id+'">'
      +PLDM_IMAGES.map(i=>'<option>'+i+'</option>').join("")+'</select></label>'
    +'<label>Group<input id="pl-grp-'+f.id+'" value="default" placeholder="empty = all groups"></label>'
    +'<label>Timeout (min)<input id="pl-to-'+f.id+'" type="number" min="1" max="120" value="20"></label>'
    +'<label>Args<input id="pl-args-'+f.id+'" placeholder="e.g. smoke"></label>'
    +'<label>Workspace<select id="pl-slot-'+f.id+'">'+slotOpts+'</select></label>'
    +'</div>'
    +'<div class="pldm-warn">Dispatches a real CI build and queues hardware. '
    +'The chosen slot is checked out to this feature\u2019s PR branches \u2014 all commits must be pushed.'
    +(free.length?"":' <b>No free slot right now.</b>')+'</div>'
    +'<div class="pldm-act"><button class="go" data-go="'+f.id+'">Submit to queue</button>'
    +'<button class="cancel" data-cancel="'+f.id+'">Cancel</button>'
    +'<span class="pldm-note" id="pl-note-'+f.id+'"></span></div>';
}

function togglePldm(id){
  const box=document.getElementById("pldm-box-"+id);
  if(!box) return;
  if(!box.hidden){ box.hidden=true; box.innerHTML=""; return; }
  const f=(PRS.features||[]).find(x=>x.id===id);
  if(!f) return;
  box.innerHTML=pldmForm(f); box.hidden=false;
}

async function submitPldm(id){
  const note=document.getElementById("pl-note-"+id);
  const go=document.querySelector('[data-go="'+id+'"]');
  const body={feature_id:id,
    display_name:document.getElementById("pl-dn-"+id).value.trim(),
    image_type:document.getElementById("pl-img-"+id).value,
    group:document.getElementById("pl-grp-"+id).value.trim(),
    timeout:parseInt(document.getElementById("pl-to-"+id).value,10)||20,
    args:document.getElementById("pl-args-"+id).value.trim(),
    slot:document.getElementById("pl-slot-"+id).value};
  go.disabled=true; note.textContent="dispatching\u2026";
  try{
    const r=await fetch("/api/pldm-test",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const j=await r.json();
    if(!r.ok||j.error) throw new Error(j.error||"submit failed");
    note.textContent=j.message||"";
    pollPldm(j.align_job,id);
  }catch(e){
    note.textContent="Failed: "+e.message; go.disabled=false;
  }
}

async function pollPldm(jobId,id){
  const box=document.getElementById("pldm-box-"+id);
  const PHASE={align:"checking out PR branches",preflight:"verifying pushed state",
               dispatch:"running octl \u2014 this takes a few minutes",done:"done"};
  for(;;){
    await new Promise(s=>setTimeout(s,2000));
    let j;
    try{ j=await (await fetch("/api/align/"+encodeURIComponent(jobId),{cache:"no-store"})).json(); }
    catch(e){ break; }
    if(!j||j.error) break;
    const log=(j.repos||[]).map(r=>'<div class="pl-row '+r.action+'">'+esc(r.name)
      +' <span>'+esc(r.detail||r.action)+'</span></div>').join("");
    const head=j.status==="running"
      ? '<b>'+esc(PHASE[j.phase]||j.phase||"working")+'</b>'
        +(j.current?' \u00b7 '+esc(j.current):"")
      : (j.status==="done"?'<b class="ok">Queued.</b> '+esc(j.message||"")
          +(j.run_url?' \u00b7 <a href="'+esc(j.run_url)+'" target="_blank" rel="noopener">Actions run</a>':"")
        : '<b class="bad">Failed.</b> '+esc(j.message||""));
    box.innerHTML='<div class="pldm-prog">'+head+'</div>'+log
      +(j.status==="running"?"":'<div class="pldm-act">'
        +'<button class="cancel" data-cancel="'+id+'">Close</button></div>');
    if(j.status!=="running"){
      if(j.status==="done") scanPrs();
      break;
    }
  }
}

document.addEventListener("click",e=>{
  const t=e.target.closest("[data-pldm],[data-go],[data-cancel]");
  if(!t) return;
  if(t.dataset.pldm) togglePldm(parseInt(t.dataset.pldm,10));
  else if(t.dataset.go) submitPldm(parseInt(t.dataset.go,10));
  else if(t.dataset.cancel) togglePldm(parseInt(t.dataset.cancel,10));
});

// The scan runs `gh` server-side — no model call, so it is free to hammer.
async function scanPrs(){
  const btn=document.getElementById("pr-scan"), note=document.getElementById("pr-scan-note");
  if(!SERVED){ note.textContent="Serve the portal to scan"; return; }
  btn.disabled=true; btn.innerHTML='<span class="spin">\u21bb</span> Scanning\u2026';
  note.textContent="probing GitHub via gh\u2026";
  try{
    const r=await fetch("/api/pr-scan",{method:"POST"});
    const j=await r.json();
    if(!r.ok||j.error) throw new Error(j.error||"scan failed");
    const job=await pollAlign(j.job,"Scanning PRs…");
    if(!job||job.status==="error") throw new Error((job&&job.message)||"scan failed");
    const res=job.result||{};
    PRS=res; renderPrs(); buildSections();
    const gone=res.purged||[];
    note.innerHTML="probed "+(res.scanned||0)+" PRs"
      +(gone.length?' \u00b7 <b>retired '+gone.length+'</b>: '
        +gone.map(f=>esc(f.name)+" ("+f.total+"/"+f.total+")").join(", "):"");
  }catch(e){
    note.textContent="Scan failed: "+e.message;
  }finally{
    btn.disabled=false; btn.textContent="\u21bb Scan PRs";
  }
}
document.getElementById("pr-scan").addEventListener("click",scanPrs);

// ---------------- RAID ----------------
const kpi=o=>'<div class="kpi" style="--k:'+o.c+'"><div class="l">'+esc(o.label)+'</div>'
  +'<div class="n">'+o.n+'</div><div class="s">'+(o.sub||"")+'</div></div>';
const RAG_COLOR={red:PBI.red, orange:PBI.orange, yellow:PBI.gold, green:PBI.green};
const RAID_TYPES=[["risk","Risk"],["assumption","Assumption"],["issue","Issue"],["dependency","Dependency"]];
let raidOpenRow=null;

function raidRows(){
  const showClosed=document.getElementById("raid-showclosed").checked;
  return (RAID.rows||[]).filter(r=>showClosed||!r.closed);
}

function renderRaid(){
  const c=RAID.counts||{}, rows=raidRows(), today=isoDay(new Date());
  document.getElementById("raid-asof").innerHTML=
    "<b>"+(c.open||0)+"</b> open \u00b7 <b>"+(c.hot||0)+"</b> red / orange \u00b7 <b>"
    +(c.overdue||0)+"</b> overdue \u00b7 "+(c.closed||0)+" closed"
    +(RAID.error?' \u00b7 <span class="stale-note">'+esc(RAID.error)+"</span>":"")
    +(SERVED?"":' \u00b7 <span class="ro-note">serve the portal to edit</span>');

  const open=(RAID.rows||[]).filter(r=>!r.closed);
  const byRag=g=>open.filter(r=>r.rag===g).length;
  document.getElementById("raid-kpis").innerHTML=[
    kpi({c:PBI.red,    label:"Red",      n:byRag("red"),      sub:"act now"}),
    kpi({c:PBI.orange, label:"Orange",   n:byRag("orange"),   sub:"needs a plan"}),
    kpi({c:PBI.gold,   label:"Yellow",   n:byRag("yellow"),   sub:"watch"}),
    kpi({c:PBI.green,  label:"Green",    n:byRag("green"),    sub:"under control"}),
    kpi({c:PBI.violet, label:"Overdue",  n:c.overdue||0,      sub:"trigger date passed"}),
    kpi({c:PBI.grey,   label:"Closed",   n:c.closed||0,       sub:"resolved to date"}),
  ].join("");

  document.getElementById("raid-body").innerHTML = rows.length
    ? rows.map(r=>{
        const overdue=!r.closed && r.due && r.due<today;
        return '<tr class="task raid-row" data-id="'+esc(r.id)+'">'
          +'<td><span class="rid">'+esc(r.id)+'</span></td>'
          +'<td class="ttl">'+esc(r.title||"\u2014")+'</td>'
          +'<td><span class="ragp" style="--rk:'+(RAG_COLOR[r.rag]||PBI.grey)+'">'+esc(r.rag||"\u2014")+'</span></td>'
          +'<td>'+r.score+'</td><td>'+esc(r.owner||"\u2014")+'</td>'
          +'<td'+(overdue?' class="od"':'')+'>'+esc(r.due||"\u2014")+'</td>'
          +'<td class="stt">'+esc(r.closed?"closed":(r.status||"open"))+'</td></tr>'
          +'<tr class="detail" data-for="'+esc(r.id)+'" style="display:none;"><td colspan="7"></td></tr>';
      }).join("")
    : '<tr><td colspan="7"><div class="empty">No RAID rows.</div></td></tr>';

  document.querySelectorAll("#raid-body tr.raid-row").forEach(tr=>
    tr.addEventListener("click",()=>toggleRaid(tr.dataset.id)));
  if(raidOpenRow && rows.some(r=>r.id===raidOpenRow)){ const id=raidOpenRow; raidOpenRow=null; toggleRaid(id); }
}

function toggleRaid(id){
  const det=document.querySelector('#raid-body tr.detail[data-for="'+id+'"]');
  if(!det) return;
  if(det.style.display!=="none"){ det.style.display="none"; raidOpenRow=null; return; }
  document.querySelectorAll("#raid-body tr.detail").forEach(d=>d.style.display="none");
  const r=(RAID.rows||[]).find(x=>x.id===id);
  det.firstElementChild.innerHTML=raidDetail(r);
  det.style.display="";
  raidOpenRow=id;
  wireRaidEditor(id);
}

function raidField(label,val){
  return val ? '<div class="rf"><span>'+esc(label)+'</span><p>'+esc(val)+'</p></div>' : "";
}

function raidDetail(r){
  const ro=!SERVED;
  // data-orig lets Save submit only what actually changed, so untouched fields
  // keep their original markdown in raid.md.
  const inp=(name,val,extra)=>'<input data-f="'+name+'" data-orig="'+esc(val||"")+'" value="'
    +esc(val||"")+'"'+(extra||"")+(ro?" disabled":"")+'>';
  const sel=(name,opts,cur)=>{
    const vals=opts.map(o=>Array.isArray(o)?o[0]:o);
    const hit=vals.find(v=>String(v).toLowerCase()===String(cur).toLowerCase());
    return '<select data-f="'+name+'" data-orig="'+esc(hit==null?"":hit)+'"'+(ro?" disabled":"")+'>'
      +opts.map(o=>{const[v,l]=Array.isArray(o)?o:[o,o];
        return '<option value="'+esc(v)+'"'+(String(cur).toLowerCase()===String(v).toLowerCase()?" selected":"")+'>'+esc(l)+'</option>';}).join("")+'</select>';
  };
  const num=n=>[1,2,3,4,5].map(v=>[v,String(v)]);
  return '<div class="raid-det">'
    +'<div class="rf-grid">'
      +raidField("Description",r.description)+raidField("Trigger",r.trigger)
      +raidField("Mitigation",r.mitigation)+raidField("Contingency",r.contingency)
      +raidField("External owner",r.external)+raidField("Link",r.link)
      +raidField("Status",r.status)
    +'</div>'
    +'<div class="raid-edit">'
      +'<div class="re-row">'
        +'<label>Title'+inp("title",r.title_raw!=null?r.title_raw:r.title)+'</label>'
      +'</div>'
      +'<div class="re-row">'
        +'<label>Type'+sel("type",RAID_TYPES,r.type)+'</label>'
        +'<label>Owner'+inp("fw_owner",r.owner)+'</label>'
        +'<label>P'+sel("p",num(),r.p)+'</label>'
        +'<label>I'+sel("i",num(),r.i)+'</label>'
        +'<label>RAG'+sel("rag",["green","yellow","orange","red"],r.rag)+'</label>'
        +'<label>Due'+inp("due",r.due_raw!=null?r.due_raw:r.due,' placeholder="YYYY-MM-DD"')+'</label>'
      +'</div>'
      +'<div class="re-row">'
        +'<label>Mitigation<input data-f="mitigation" placeholder="leave blank to keep" '+(ro?"disabled":"")+'></label>'
        +'<label>Status<input data-f="status" placeholder="leave blank to keep" '+(ro?"disabled":"")+'></label>'
      +'</div>'
      +'<div class="re-act">'
        +'<button class="btn primary" data-a="save"'+(ro?" disabled":"")+'>Save</button>'
        +(r.closed?"":'<button class="btn" data-a="close"'+(ro?" disabled":"")+'>Close row\u2026</button>')
        +'<span class="ro-note" data-a="msg"></span>'
      +'</div>'
    +'</div></div>';
}

function wireRaidEditor(id){
  const root=document.querySelector('#raid-body tr.detail[data-for="'+id+'"] .raid-edit');
  if(!root) return;
  const msg=root.querySelector('[data-a="msg"]');
  const post=async body=>{
    msg.textContent="saving\u2026";
    try{
      const res=await fetch("/api/raid/"+encodeURIComponent(id),
        {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      const j=await res.json();
      if(!res.ok||j.error) throw new Error(j.error||"save failed");
      RAID=j.payload; raidOpenRow=id; renderRaid(); buildSections(); renderOverview();
    }catch(e){ msg.textContent="Failed: "+e.message; }
  };
  root.querySelector('[data-a="save"]').addEventListener("click",()=>{
    const body={};
    root.querySelectorAll("[data-f]").forEach(el=>{
      const v=(el.value||"").trim();
      if(!v) return;
      const orig=el.dataset.orig;
      if(orig!==undefined && v===orig.trim()) return;   // untouched
      body[el.dataset.f]=v;
    });
    if(!Object.keys(body).length){ msg.textContent="Nothing changed"; return; }
    post(body);
  });
  const cb=root.querySelector('[data-a="close"]');
  if(cb) cb.addEventListener("click",()=>{
    const res=prompt("Resolution for "+id+":");
    if(res && res.trim()) post({close:res.trim()});
  });
}

function raidNewForm(){
  const box=document.getElementById("raid-form");
  if(box.style.display!=="none"){ box.style.display="none"; return; }
  box.innerHTML='<div class="re-row">'
    +'<label>Type<select id="nr-type">'+RAID_TYPES.map(([v,l])=>'<option value="'+v+'">'+l+'</option>').join("")+'</select></label>'
    +'<label style="flex:1 1 320px;">Title<input id="nr-title" placeholder="What is the risk / issue?"></label>'
    +'<label>Owner<input id="nr-owner" value="Yoni"></label>'
    +'<label>P<select id="nr-p">'+[1,2,3,4,5].map(v=>'<option'+(v===3?" selected":"")+'>'+v+'</option>').join("")+'</select></label>'
    +'<label>I<select id="nr-i">'+[1,2,3,4,5].map(v=>'<option'+(v===3?" selected":"")+'>'+v+'</option>').join("")+'</select></label>'
    +'<label>Due<input id="nr-due" placeholder="YYYY-MM-DD"></label>'
    +'</div>'
    +'<div class="re-row"><label style="flex:1 1 100%;">Description<input id="nr-desc" placeholder="Context, blast radius, who is affected"></label></div>'
    +'<div class="re-act"><button class="btn primary" id="nr-save">Create</button><span class="ro-note" id="nr-msg"></span></div>';
  box.style.display="";
  document.getElementById("nr-save").addEventListener("click",async()=>{
    const m=document.getElementById("nr-msg");
    const title=document.getElementById("nr-title").value.trim();
    if(!title){ m.textContent="Title is required"; return; }
    m.textContent="creating\u2026";
    try{
      const res=await fetch("/api/raid-new",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({type:document.getElementById("nr-type").value, title:title,
          fw_owner:document.getElementById("nr-owner").value.trim(),
          p:document.getElementById("nr-p").value, i:document.getElementById("nr-i").value,
          due:document.getElementById("nr-due").value.trim(),
          description:document.getElementById("nr-desc").value.trim()})});
      const j=await res.json();
      if(!res.ok||j.error) throw new Error(j.error||"create failed");
      RAID=j.payload; box.style.display="none"; raidOpenRow=j.id;
      renderRaid(); buildSections(); renderOverview();
    }catch(e){ m.textContent="Failed: "+e.message; }
  });
}
document.getElementById("raid-new").addEventListener("click",raidNewForm);
document.getElementById("raid-showclosed").addEventListener("change",renderRaid);

async function refreshData(){
  const btn=document.getElementById("refresh");
  const note=document.getElementById("refresh-note");
  if(btn){ btn.disabled=true; btn.classList.add("spinning"); }
  try{
    if(SERVED){
      const r=await fetch("/api/tasks",{cache:"no-store"});
      if(r.ok){ const j=await r.json(); TASKS=j.tasks||j; if(j.workspaces) WORKSPACES=j.workspaces; }
      const rb=await fetch("/api/bugs",{cache:"no-store"});
      if(rb.ok) BUGS=await rb.json();
      const ro=await fetch("/api/overview",{cache:"no-store"});
      if(ro.ok) OVERVIEW=await ro.json();
      const rp=await fetch("/api/prs",{cache:"no-store"});
      if(rp.ok) PRS=await rp.json();
      const rr=await fetch("/api/raid",{cache:"no-store"});
      if(rr.ok) RAID=await rr.json();
    }
    const fstate=captureFilterState();   // preserve active filter selections
    buildFilters();                       // rebuild options (may include new values)
    restoreFilterState(fstate);           // re-apply selections that still exist
    buildTabs();                          // refresh tab counts, keep activeTab
    buildBugFilters(); buildBugTabs(); buildSections();
    document.getElementById("total").textContent=
      `${TASKS.length} tasks · ${bugList().filter(b=>b.open).length} open bugs`;
    if(activeSection==="bugs") renderBugs();
    else if(activeSection==="overview") renderOverview();
    else if(activeSection==="prs") renderPrs();
    else if(activeSection==="raid") renderRaid();
    else render();
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
      const rb=await fetch("/api/bugs",{cache:"no-store"});
      if(rb.ok) BUGS=await rb.json();
      const ro=await fetch("/api/overview",{cache:"no-store"});
      if(ro.ok) OVERVIEW=await ro.json();
      const rp=await fetch("/api/prs",{cache:"no-store"});
      if(rp.ok) PRS=await rp.json();
      const rr=await fetch("/api/raid",{cache:"no-store"});
      if(rr.ok) RAID=await rr.json();
    }
  }catch(e){ /* file:// — stay read-only with embedded data */ }
  document.getElementById("total").textContent=
    `${TASKS.length} tasks · ${bugList().filter(b=>b.open).length} open bugs`;
  buildFilters();
  buildTabs();
  applyTabPreset(activeTab);
  render();
  buildBugFilters();
  buildBugTabs();
  buildSections();
  renderOverview();
  renderPrs();
  renderRaid();
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


TEAM_PLANNER = Path(os.path.expanduser("~/team-planner"))
IP_SCOREBOARD = Path(os.path.expanduser(
    "~/.copilot/skills/team-weekly/references/ip-scoreboard.md"))

# Fallback if the team-weekly reference table is missing or unparseable.
DEFAULT_IPS = [
    {"name": "PCIe EP", "owners": "Mateusz + Vasyl", "hints": ["pcie"]},
    {"name": "SE", "owners": "Akshay", "hints": ["ip-se-"]},
    {"name": "D2D", "owners": "Igal", "hints": ["d2d"]},
    {"name": "Infra/Mng", "owners": "Yoni", "hints": ["infra", "mng", "noc"]},
]

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def _plain(s: str) -> str:
    """Strip markdown links and bold markers from a field value."""
    return _MD_LINK.sub(r"\1", s or "").replace("**", "").strip()


def _parse_program_dates() -> dict:
    """Power-on / delivery gates and the sprint calendar from guidelines.md."""
    out = {"power_on": None, "a1_delivery": None, "sprints": []}
    f = TEAM_PLANNER / "guidelines.md"
    if not f.exists():
        return out
    txt = f.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\*\*Power-on:\*\*\s*\*\*(\d{4}-\d{2}-\d{2})\*\*", txt)
    if m:
        out["power_on"] = m.group(1)
    m = re.search(r"\*\*A1 big delivery:\*\*.*?\*\*(\d{4}-\d{2}-\d{2})\*\*", txt)
    if m:
        out["a1_delivery"] = m.group(1)

    year = int((out["power_on"] or out["a1_delivery"] or "2026")[:4])
    # "MS8.1=3749 (Aug 30–Sep 13)" — windows carry no year, so infer it and roll
    # over when the end month wraps past the start month.
    for name, sid, window in re.findall(r"(MS[\d.]+)=(\d+)\s*\(([^)]*)\)", txt):
        span = re.match(r"([A-Z][a-z]{2})\s+(\d{1,2})\s*[–-]\s*"
                        r"(?:([A-Z][a-z]{2})\s+)?(\d{1,2})", window.strip())
        start = end = None
        if span:
            sm, sd, em, ed = span.groups()
            em = em or sm
            try:
                start = date(year, _MONTHS[sm], int(sd))
                ey = year + 1 if _MONTHS[em] < _MONTHS[sm] else year
                end = date(ey, _MONTHS[em], int(ed))
            except (KeyError, ValueError):
                start = end = None
        out["sprints"].append({
            "name": name, "id": sid, "window": window.split(",")[0].strip(),
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        })
    return out


def _parse_raid() -> list[dict]:
    """RAID rows for the Overview. team-planner's rd.py owns the parsing."""
    if rd is None:
        return []
    return rd.payload()["rows"]


def _parse_ips() -> list[dict]:
    """IP → owner → component-hint mapping from the team-weekly reference."""
    if not IP_SCOREBOARD.exists():
        return DEFAULT_IPS
    ips = []
    txt = IP_SCOREBOARD.read_text(encoding="utf-8", errors="replace")
    for line in txt.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("IP", "---") or set(cells[0]) == {"-"}:
            continue
        hints = re.findall(r'"([^"]+)"', cells[2])
        if not hints:
            continue
        ips.append({"name": cells[0], "owners": cells[1], "hints": hints})
    return ips or DEFAULT_IPS


def overview_payload() -> dict:
    """Team-level context for the portal's Overview storefront. All of it comes
    from local files — the portal never calls Jira/GitHub."""
    try:
        raid = _parse_raid()
    except Exception:
        raid = []
    try:
        program = _parse_program_dates()
    except Exception:
        program = {"power_on": None, "a1_delivery": None, "sprints": []}
    try:
        ips = _parse_ips()
    except Exception:
        ips = DEFAULT_IPS
    return {"program": program, "raid": raid, "ips": ips,
            "today": date.today().isoformat()}


def bug_payload() -> dict:
    """Bugs section data. Empty shell when bug-monitor isn't installed."""
    if bm is None:
        return {"bugs": [], "families": [], "config": {}, "last_scan": None}
    try:
        return bm.dashboard_payload()
    except Exception as e:                       # never let a bug store break the portal
        return {"bugs": [], "families": [], "config": {}, "last_scan": None,
                "error": str(e)}


def pr_payload() -> dict:
    """Outgoing feature-PR data from pr-monitor. Read-only; the scan button is
    what refreshes it."""
    if pm is None:
        return {"features": [], "counts": {}, "last_scan": None}
    try:
        return pm.payload()
    except Exception as e:
        return {"features": [], "counts": {}, "last_scan": None, "error": str(e)}


def raid_payload() -> dict:
    """RAID rows from team-planner. Editable from the portal via rd.apply_edit."""
    if rd is None:
        return {"rows": [], "owners": [], "counts": {}, "path": ""}
    try:
        return rd.payload()
    except Exception as e:
        return {"rows": [], "owners": [], "counts": {}, "path": "", "error": str(e)}


WS_SLOT_NAMES = ["one", "two", "three", "four", "five", "six"]


def viable_slots() -> list[str]:
    """Slots that are real, buildable workspaces.

    ``wm.canonical_slots()`` lists every directory under the project root, but
    only the number-named ones are full multi-repo checkouts — the rest
    (``main``, ``rtk``, ``elpci``, …) are scratch dirs that can't be built.
    """
    return [s for s in wm.canonical_slots(DEFAULT_PROJECT) if s in WS_SLOT_NAMES]


def ws_ui_payload(wd: dict) -> dict:
    """Workspace state plus the roster of buildable slots.

    ``wm.load()`` only records slots that have *ever* been held, so a UI built
    from it alone can't tell a free slot from one that doesn't exist.
    """
    return dict(wd, canonical=viable_slots())


def render_dashboard_html(tasks: list[dict]) -> str:
    wd = wm.load()
    payload = json.dumps(_annotate_tasks(tasks, wd), ensure_ascii=False).replace("</", "<\\/")
    wspayload = json.dumps(ws_ui_payload(wd), ensure_ascii=False).replace("</", "<\\/")
    bugpayload = json.dumps(bug_payload(), ensure_ascii=False).replace("</", "<\\/")
    ovpayload = json.dumps(overview_payload(), ensure_ascii=False).replace("</", "<\\/")
    prpayload = json.dumps(pr_payload(), ensure_ascii=False).replace("</", "<\\/")
    raidpayload = json.dumps(raid_payload(), ensure_ascii=False).replace("</", "<\\/")
    return (DASHBOARD_TEMPLATE
            .replace("__DATA__", payload)
            .replace("__WSDATA__", wspayload)
            .replace("__BUGDATA__", bugpayload)
            .replace("__OVDATA__", ovpayload)
            .replace("__PRDATA__", prpayload)
            .replace("__RAIDDATA__", raidpayload)
            .replace("__GENERATED__", datetime.now().strftime("%Y-%m-%d %H:%M")))


def cmd_dashboard(args: argparse.Namespace) -> int:
    d = load()
    out = Path(os.path.expanduser(args.output)) if args.output else STORE.parent / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dashboard_html(d["tasks"]), encoding="utf-8")
    print(f"[tm] wrote portal with {len(d['tasks'])} tasks and "
          f"{len(bug_payload()['bugs'])} bugs -> {out}")
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


# ----------------------------------------------------------------------------
# Workspace alignment (dashboard "manual review" checkout)
# ----------------------------------------------------------------------------
# When a code-review task is assigned to a workspace slot from the dashboard,
# the server checks the slot's repos out to the PR branches (everything else to
# the default branch) so the user can review by hand — the no-AI/no-credit
# equivalent of review-mgr `start --manual`. This runs as a background job so
# the request returns immediately; the UI polls ``/api/align/<job>`` for a
# progress/done indicator. Non-destructive: dirty repos are skipped and
# reported, never reset.

import threading as _threading  # noqa: E402

ALIGN_JOBS: dict[str, dict] = {}
ALIGN_LOCK = _threading.Lock()
# Serialises the PR scan so a double-click can't launch two gh storms.
PR_SCAN_LOCK = _threading.Lock()

# Same for the Jira mirror refresh.
BUG_SCAN_LOCK = _threading.Lock()

# raid.md is a plain file with no locking of its own.
RAID_LOCK = _threading.Lock()


def _git(repo_dir: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


# owner/repo out of any GitHub remote (git@…:o/r.git, ssh://…/o/r.git, https://…/o/r).
_REMOTE_RE = re.compile(r"github\.com[:/]+([^/]+)/([^/\s]+?)(?:\.git)?/?$", re.I)


def _worktree_remote_key(repo_dir: Path) -> tuple[str, str] | None:
    """(owner, repo) lower-cased for a worktree's origin remote, or None."""
    r = _git(repo_dir, "remote", "get-url", "origin", timeout=15)
    if r.returncode != 0:
        return None
    m = _REMOTE_RE.search(r.stdout.strip())
    return (m.group(1).lower(), m.group(2).lower()) if m else None


def _discover_worktrees(ws_dir: Path) -> list[Path]:
    """All git worktrees under a (repo/west-style) workspace.

    Repos live at arbitrary nested paths whose dir name does not match the
    GitHub repo name, so we walk the tree, stop descending once a `.git` is
    found (repos aren't nested under each other here), and prune tool internals.
    """
    found: list[Path] = []
    skip = {".repo", ".west", ".cache", "node_modules", "build", ".venv"}
    for root, dirs, files in os.walk(ws_dir):
        dirs[:] = [d for d in dirs if d not in skip]
        if ".git" in dirs or ".git" in files:
            found.append(Path(root))
            dirs[:] = []  # don't descend into a repo's source tree
    return sorted(found)


def _repo_default_branch(repo_dir: Path) -> str:
    """Best-effort default branch for a repo (origin/HEAD, else main/master)."""
    r = _git(repo_dir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", timeout=15)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().split("/")[-1]
    for cand in ("main", "master"):
        if _git(repo_dir, "rev-parse", "--verify", cand, timeout=15).returncode == 0:
            return cand
    return "main"


def _is_dirty(repo_dir: Path) -> bool:
    r = _git(repo_dir, "status", "--porcelain", timeout=30)
    return bool(r.stdout.strip())


def _checkout_pr_branch(repo_dir: Path, branch: str, pr_number: int | None) -> tuple[bool, str]:
    """Fetch + checkout a PR branch; fall back to pull/<n>/head. Non-destructive."""
    _git(repo_dir, "fetch", "origin", branch, timeout=180)
    r = _git(repo_dir, "checkout", branch, timeout=60)
    if r.returncode == 0:
        return True, f"on {branch}"
    if pr_number:
        f = _git(repo_dir, "fetch", "origin",
                 f"pull/{pr_number}/head:{branch}", timeout=180)
        if f.returncode == 0 and _git(repo_dir, "checkout", branch, timeout=60).returncode == 0:
            return True, f"on {branch} (via pull/{pr_number}/head)"
    return False, (r.stderr.strip() or "checkout failed").splitlines()[-1]


def run_workspace_alignment(job: dict, store_lock=None) -> None:
    """Background worker: align a workspace slot for a code-review task.

    Updates ``job`` in place (guarded by ALIGN_LOCK) so the UI can poll progress.
    """
    def upd(**kw):
        with ALIGN_LOCK:
            job.update(kw)

    def add_repo(rec):
        with ALIGN_LOCK:
            job["repos"].append(rec)

    d = load()
    t = find_task(d, job["task_id"])
    if not t:
        upd(status="error", message="task not found", finished=NOW)
        return
    prs = [p for p in pr_entries(t) if not pr_resolved(p)]
    pr_by_repo = {(p.get("owner", "").lower(), p.get("repo", "").lower()): p
                  for p in prs if p.get("repo")}

    ws_dir = PROJECTS_ROOT / DEFAULT_PROJECT / job["slot"]
    if not ws_dir.is_dir():
        upd(status="error", message=f"workspace dir not found: {ws_dir}", finished=NOW)
        return

    # Match repos to PR entries by their origin remote (owner/repo), NOT by dir
    # name — this is a repo/west workspace where dir names differ from repo names.
    worktrees = _discover_worktrees(ws_dir)
    upd(total=len(worktrees))
    seen_review: set[tuple[str, str]] = set()
    left_alone = 0

    for repo_dir in worktrees:
        key = _worktree_remote_key(repo_dir)
        try:
            upd(current=str(repo_dir.relative_to(ws_dir)))
        except ValueError:
            upd(current=repo_dir.name)
        pr = pr_by_repo.get(key) if key else None
        label = pr.get("repo") if pr else (f"{key[0]}/{key[1]}" if key else repo_dir.name)
        if pr:
            seen_review.add(key)
            if _is_dirty(repo_dir):
                add_repo({"name": label, "role": "review", "action": "skipped-dirty",
                          "detail": "uncommitted changes — left as-is"})
                continue
            ok, detail = _checkout_pr_branch(
                repo_dir, pr.get("branch") or "", pr.get("pr_number"))
            add_repo({"name": label, "role": "review",
                      "action": "checked-out" if ok else "error", "detail": detail})
            continue
        # Non-PR repo → switch to default branch, but never disturb a
        # manifest-pinned (detached HEAD) checkout — leave it as the manifest set it.
        cur = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD", timeout=15).stdout.strip()
        if not key or cur == "HEAD":
            left_alone += 1
            continue
        default = _repo_default_branch(repo_dir)
        if cur == default:
            left_alone += 1
            continue
        if _is_dirty(repo_dir):
            add_repo({"name": label, "role": "default", "action": "skipped-dirty",
                      "detail": f"on {cur}, uncommitted changes — left as-is"})
            continue
        r = _git(repo_dir, "checkout", default, timeout=60)
        ok = r.returncode == 0
        add_repo({"name": label, "role": "default",
                  "action": "checked-out" if ok else "error",
                  "detail": (f"{cur} → {default}" if ok
                             else (r.stderr.strip() or "checkout failed").splitlines()[-1])})

    # Report review repos the task references but that aren't in the workspace.
    for key, pr in pr_by_repo.items():
        if key not in seen_review:
            add_repo({"name": pr.get("repo") or f"{key[0]}/{key[1]}",
                      "role": "review", "action": "not-found",
                      "detail": "repo not present in this workspace"})

    errors = sum(1 for r in job["repos"] if r["action"] in ("error", "not-found"))
    dirty = sum(1 for r in job["repos"] if r["action"] == "skipped-dirty")
    ok_n = sum(1 for r in job["repos"] if r["action"] == "checked-out")
    msg = f"{ok_n} aligned"
    if left_alone:
        msg += f", {left_alone} left as-is"
    if dirty:
        msg += f", {dirty} skipped (dirty)"
    if errors:
        msg += f", {errors} problem(s)"
    upd(status="done", current=None, finished=NOW, message=msg)

    # Record the outcome on the task (best-effort, guarded by the store lock).
    note = f"dashboard: workspace {job['slot']} aligned for manual review — {msg}"
    if store_lock is not None:
        store_lock.acquire()
    try:
        d2 = load()
        t2 = find_task(d2, job["task_id"])
        if t2:
            append_note(t2, note)
            t2["updated"] = TODAY
            save(d2)
    finally:
        if store_lock is not None:
            store_lock.release()


def run_workspace_reset(job: dict, store_lock=None) -> None:
    """Background worker: switch every repo in a workspace slot back to `main`.

    Fired when a slot is set free — returns the whole workspace to a clean
    default-branch state. Non-destructive: dirty repos are skipped and reported.
    """
    def upd(**kw):
        with ALIGN_LOCK:
            job.update(kw)

    def add_repo(rec):
        with ALIGN_LOCK:
            job["repos"].append(rec)

    ws_dir = PROJECTS_ROOT / DEFAULT_PROJECT / job["slot"]
    if not ws_dir.is_dir():
        upd(status="error", message=f"workspace dir not found: {ws_dir}", finished=NOW)
        return

    worktrees = _discover_worktrees(ws_dir)
    upd(total=len(worktrees))
    left_alone = 0
    for repo_dir in worktrees:
        try:
            upd(current=str(repo_dir.relative_to(ws_dir)))
        except ValueError:
            upd(current=repo_dir.name)
        key = _worktree_remote_key(repo_dir)
        label = f"{key[0]}/{key[1]}" if key else repo_dir.name
        default = _repo_default_branch(repo_dir)
        cur = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD", timeout=15).stdout.strip()
        if cur == default:
            left_alone += 1
            continue
        if _is_dirty(repo_dir):
            add_repo({"name": label, "role": "default", "action": "skipped-dirty",
                      "detail": f"on {cur}, uncommitted changes — left as-is"})
            continue
        r = _git(repo_dir, "checkout", default, timeout=60)
        ok = r.returncode == 0
        add_repo({"name": label, "role": "default",
                  "action": "checked-out" if ok else "error",
                  "detail": (f"{cur} → {default}" if ok
                             else (r.stderr.strip() or "checkout failed").splitlines()[-1])})

    dirty = sum(1 for r in job["repos"] if r["action"] == "skipped-dirty")
    errs = sum(1 for r in job["repos"] if r["action"] == "error")
    ok_n = sum(1 for r in job["repos"] if r["action"] == "checked-out")
    msg = f"{ok_n} → main"
    if left_alone:
        msg += f", {left_alone} already on main"
    if dirty:
        msg += f", {dirty} skipped (dirty)"
    if errs:
        msg += f", {errs} problem(s)"
    upd(status="done", current=None, finished=NOW, message=msg)


# ----------------------------------------------------------------------------
# PLDM (Palladium) test submission
# ----------------------------------------------------------------------------
# `octl ws build --remote --target pldm` builds from the *workspace manifest*,
# not from a PR reference, so a feature can only be submitted once a slot is
# physically checked out on every one of its PR branches, clean and pushed.
# The job therefore does the whole chain: grab a free slot, align it, preflight,
# write the tests-config, dispatch, then stamp the result back on the memoir.

PLDM_LOCK = _threading.Lock()
PLDM_IMAGE_TYPES = ["1die_w_gmem", "2die_w_gmem", "1die_wo_gmem", "4die_w_gmem"]
# Anything above this and the run is almost certainly stuck behind a busy EDK
# rather than actually building; octl itself caps a test at 120 minutes.
PLDM_DISPATCH_TIMEOUT = 1800

_RUN_URL_RE = re.compile(r"https://github\.com/\S+/actions/runs/\d+")


def _unpushed(repo_dir: Path) -> str | None:
    """Describe unpushed commits on the current branch, or None if in sync."""
    br = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD", timeout=15).stdout.strip()
    if not br or br == "HEAD":
        return None
    r = _git(repo_dir, "rev-list", "--count", f"origin/{br}..{br}", timeout=30)
    if r.returncode != 0:
        return f"{br} has no upstream on origin"
    n = r.stdout.strip()
    return f"{n} unpushed commit(s) on {br}" if n.isdigit() and int(n) else None


def run_pldm_submit(job: dict) -> None:
    """Background worker: align a slot to a feature's PRs and enqueue a PLDM job."""
    def upd(**kw):
        with ALIGN_LOCK:
            job.update(kw)

    def add_repo(rec):
        with ALIGN_LOCK:
            job["repos"].append(rec)

    def fail(msg):
        upd(status="error", current=None, finished=NOW, message=msg)
        if job.get("held"):
            wm.release(job["slot"])
            upd(held=False)

    prs = job["prs"]
    pr_by_repo = {(p["owner"].lower(), p["repo"].lower()): p for p in prs}
    ws_dir = PROJECTS_ROOT / DEFAULT_PROJECT / job["slot"]
    if not ws_dir.is_dir():
        return fail(f"workspace dir not found: {ws_dir}")

    upd(phase="align", current="scanning workspace")
    worktrees = _discover_worktrees(ws_dir)
    upd(total=len(worktrees))
    seen: set[tuple[str, str]] = set()
    for repo_dir in worktrees:
        key = _worktree_remote_key(repo_dir)
        upd(current=repo_dir.name)
        pr = pr_by_repo.get(key) if key else None
        if not pr:
            continue
        seen.add(key)
        label = f"{pr['repo']}#{pr['number']}"
        if _is_dirty(repo_dir):
            add_repo({"name": label, "action": "error",
                      "detail": "uncommitted changes — commit or stash first"})
            continue
        ok, detail = _checkout_pr_branch(repo_dir, pr.get("branch") or "", pr["number"])
        add_repo({"name": label, "action": "checked-out" if ok else "error",
                  "detail": detail})
    for key, pr in pr_by_repo.items():
        if key not in seen:
            add_repo({"name": f"{pr['repo']}#{pr['number']}", "action": "not-found",
                      "detail": "repo not present in this workspace"})

    bad = [r for r in job["repos"] if r["action"] != "checked-out"]
    if bad:
        return fail(f"{len(bad)} repo(s) not ready — nothing dispatched")

    # Remote mode rebuilds from origin, so an unpushed commit would silently be
    # left out of the very build meant to test it.
    upd(phase="preflight", current="checking for unpushed commits")
    stray = []
    for repo_dir in worktrees:
        key = _worktree_remote_key(repo_dir)
        if key and key in pr_by_repo:
            u = _unpushed(repo_dir)
            if u:
                stray.append(f"{pr_by_repo[key]['repo']}: {u}")
    if stray:
        return fail("push these first — " + "; ".join(stray))

    cfg_dir = Path(os.path.expanduser("~/tasks/pldm"))
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / f"{job['display_name']}.yaml"
    cfg.write_text(
        f'display_name: "{job["display_name"]}"\n'
        "tests:\n"
        f'  - image_type: "{job["image_type"]}"\n'
        f'    group: "{job["group"]}"\n'
        f'    timeout_minutes: {job["timeout"]}\n'
        f'    args: "{job["args"]}"\n'
        '    extra_flags: ""\n', encoding="utf-8")
    upd(phase="dispatch", current="octl ws build --remote --target pldm",
        config=str(cfg))

    try:
        r = subprocess.run(
            ["octl", "ws", "build", "--remote", "--target", "pldm",
             "--from-file", str(cfg)],
            cwd=str(ws_dir), capture_output=True, text=True,
            timeout=PLDM_DISPATCH_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return fail(f"octl dispatch failed: {e}")

    out = (r.stdout or "") + (r.stderr or "")
    upd(output=out[-4000:])
    if r.returncode != 0:
        tail = [ln for ln in out.splitlines() if ln.strip()][-1:] or ["see output"]
        return fail(f"octl exited {r.returncode}: {tail[0][:200]}")

    m = _RUN_URL_RE.search(out)
    url = m.group(0) if m else ""
    detail = f"{job['display_name']} · {url or 'run URL not printed'} · updated {TODAY}"
    ok, msg = pm.set_pldm(job["feature_id"], "queued", detail)

    upd(status="done", phase="done", current=None, finished=NOW, run_url=url,
        message=f"queued on {job['image_type']}" + ("" if ok else f" (memoir: {msg})"))
    if job.get("held"):
        wm.release(job["slot"])
        upd(held=False)


def start_pldm_job(feature: dict, opts: dict) -> tuple[str | None, str]:
    """Reserve a slot and launch a PLDM submission job. Returns (job_id, message).

    ``opts["dry"]`` resolves and validates everything, then reports the plan
    without reserving a slot, touching a checkout or dispatching a build.
    """
    prs = [p for p in feature.get("prs", []) if p.get("status") != "closed"]
    if not prs:
        return None, "feature has no open PRs to test"

    slot = (opts.get("slot") or "").strip()
    held = False
    known = viable_slots()
    if not known:
        return None, "no buildable workspace slots found"
    if slot:
        # Must name a real slot: normalize_slot() happily passes ".." through,
        # which would point the checkout at the whole projects root.
        slot = wm.normalize_slot(slot) or ""
        if slot not in known:
            return None, (f"{opts.get('slot')!r} is not a buildable workspace "
                          f"(use one of: {', '.join(known)})")
    else:
        free = [s for s in known if wm.is_free(s)]
        if not free:
            return None, "no free workspace slot — release one or pick a slot"
        slot = free[0]
    name = re.sub(r"[^a-z0-9]+", "-", feature["name"].lower()).strip("-")[:40]
    display = (opts.get("display_name") or f"{name}-{datetime.now():%m%d-%H%M}").strip()

    if not wm.is_free(slot) and not opts.get("force"):
        # Aligning would check PR branches out over someone else's work.
        return None, (f"slot '{slot}' is held by {wm.holder_summary(wm.get_holder(slot))}"
                      " \u2014 free it or choose another slot")
    if opts.get("dry"):
        return None, (f"dry run OK \u2014 would submit '{display}' from slot '{slot}' "
                      f"({len(prs)} PR(s), image {opts.get('image_type') or PLDM_IMAGE_TYPES[0]})")

    if wm.is_free(slot):
        ok, msg, _ = wm.occupy_adhoc(slot, f"PLDM test: {display}", tag="experiment")
        if not ok:
            return None, msg
        held = True

    jid = f"align-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "kind": "pldm", "slot": slot, "held": held,
        "feature_id": feature["id"], "feature": feature["name"],
        "display_name": display,
        "image_type": opts.get("image_type") or PLDM_IMAGE_TYPES[0],
        "group": opts.get("group", "default"),
        "timeout": max(1, min(120, int(opts.get("timeout") or 20))),
        "args": opts.get("args", ""),
        "prs": prs, "status": "running", "phase": "align",
        "started": NOW, "finished": None, "current": None, "total": 0,
        "message": "", "repos": [], "run_url": "",
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    _threading.Thread(target=run_pldm_submit, args=(job,), daemon=True).start()
    return jid, f"{display} → {slot}"


PR_SCAN_ICONS = {
    "merged": ("\u2713", "ok"), "approved": ("\u2713", "ok"),
    "merge-queue": ("\u21bb", "ok"),
    "pending": ("\u00b7", "warn"), "needs-rebase": ("\u26a0", "warn"),
    "ci-fail": ("\u2715", "bad"), "changes-requested": ("\u2715", "bad"),
    "closed": ("\u2715", "bad"),
}


def run_pr_scan(job: dict) -> None:
    """Re-probe every tracked PR, streaming one row per PR into the job.

    Runs on a worker thread and releases PR_SCAN_LOCK, which the request handler
    acquired, so a second scan can't start while this one is in flight.
    """
    try:
        _run_pr_scan(job)
    finally:
        PR_SCAN_LOCK.release()


def _run_pr_scan(job: dict) -> None:
    def progress(done, total, feat, pr):
        with ALIGN_LOCK:
            job["total"] = total
            if pr is None:          # opening callback: batch queue-state lookup
                job["current"] = "merge-queue states"
                return
            icon, tone = PR_SCAN_ICONS.get(pr.get("status") or "", ("\u00b7", "warn"))
            name = f"{pr['owner']}/{pr['repo']} #{pr['number']}"
            job["current"] = name
            job["repos"].append({
                "name": name, "action": pr.get("status") or "unknown",
                "icon": icon, "tone": tone,
                "detail": f"{pr.get('status') or 'unknown'} \u00b7 {feat['name']}",
            })
    try:
        result = pm.refresh(progress=progress)
    except Exception as e:
        with ALIGN_LOCK:
            job.update(status="error", finished=NOW, current=None, message=str(e))
        return
    with ALIGN_LOCK:
        job["current"] = None
        job["finished"] = NOW
        if result.get("error"):
            job.update(status="error", message=result["error"])
            return
        job["status"] = "done"
        job["result"] = result
        gone = result.get("purged") or []
        job["message"] = (
            f"retired {len(gone)}: " + ", ".join(f["name"] for f in gone)) if gone else ""


def start_pr_scan_job() -> str:
    """Register + launch a background PR scan; return its id."""
    jid = f"prscan-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "kind": "pr-scan", "slot": "PRs",
        "status": "running", "started": NOW, "finished": None,
        "current": None, "total": 0, "message": "", "repos": [],
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    _threading.Thread(target=run_pr_scan, args=(job,), daemon=True).start()
    return jid


def start_alignment_job(task_id: str, slot_root: str, store_lock=None) -> str:
    """Register + launch a background alignment job; return its id."""
    jid = f"align-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "task_id": task_id, "slot": slot_root, "kind": "align",
        "status": "running", "started": NOW, "finished": None,
        "current": None, "total": 0, "message": "", "repos": [],
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    th = _threading.Thread(
        target=run_workspace_alignment, args=(job, store_lock), daemon=True)
    th.start()
    return jid


def start_reset_job(slot_root: str, store_lock=None) -> str:
    """Register + launch a background 'reset to main' job for a slot; return id."""
    jid = f"align-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "task_id": None, "slot": slot_root, "kind": "reset",
        "status": "running", "started": NOW, "finished": None,
        "current": None, "total": 0, "message": "", "repos": [],
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    th = _threading.Thread(
        target=run_workspace_reset, args=(job, store_lock), daemon=True)
    th.start()
    return jid


def alignment_target_for_ws_payload(slot: str, payload: dict) -> tuple[str, str] | None:
    """Return (task_id, slot_root) if a workspace occupy assigns a code-review task."""
    if payload.get("action", "occupy") != "occupy":
        return None
    if payload.get("type", "task") != "task":
        return None
    tid = (payload.get("task_id") or "").strip()
    if not tid:
        return None
    t = find_task(load(), tid)
    if not t or t.get("category") != "code-review":
        return None
    root = wm.normalize_slot(slot)
    return (t["id"], root) if root else None


def alignment_target_for_task_payload(ident: str, payload: dict) -> tuple[str, str] | None:
    """Return (task_id, slot_root) if a task edit assigns a code-review task to a slot."""
    ws = (payload.get("workspace") or "").strip()
    if not ws:
        return None
    t = find_task(load(), ident)
    if not t or t.get("category") != "code-review":
        return None
    root = wm.normalize_slot(ws)
    return (t["id"], root) if root else None


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
                        {"tasks": _annotate_tasks(load()["tasks"], wd),
                         "workspaces": ws_ui_payload(wd)},
                        ensure_ascii=False))
            if self.path == "/api/bugs":
                with lock:
                    return self._send(200, json.dumps(bug_payload(), ensure_ascii=False))
            if self.path == "/api/overview":
                with lock:
                    return self._send(200, json.dumps(overview_payload(), ensure_ascii=False))
            if self.path == "/api/prs":
                with lock:
                    return self._send(200, json.dumps(pr_payload(), ensure_ascii=False))
            if self.path == "/api/raid":
                with RAID_LOCK:
                    return self._send(200, json.dumps(raid_payload(), ensure_ascii=False))
            if self.path.startswith("/api/align/"):
                jid = self.path[len("/api/align/"):]
                with ALIGN_LOCK:
                    job = ALIGN_JOBS.get(jid)
                    body = json.dumps(job, ensure_ascii=False) if job else None
                if body is None:
                    return self._send(404, json.dumps({"error": "unknown job"}))
                return self._send(200, body)
            return self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            from urllib.parse import unquote

            # The PR scan shells out to `gh`; it takes no body and can run long.
            if self.path == "/api/pr-scan":
                if pm is None:
                    return self._send(503, json.dumps({"error": "pr-monitor not installed"}))
                if not PR_SCAN_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a PR scan is already running"}))
                try:
                    jid = start_pr_scan_job()
                except Exception as e:
                    PR_SCAN_LOCK.release()
                    return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "job": jid}))

            # The bug scan hits the Jira REST API; also bodyless and slow.
            if self.path == "/api/bug-scan":
                if bm is None:
                    return self._send(503, json.dumps({"error": "bug-monitor not installed"}))
                with BUG_SCAN_LOCK:
                    try:
                        result = bm.portal_scan()
                    except Exception as e:
                        return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps(result, ensure_ascii=False))

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
                    align = alignment_target_for_ws_payload(slot, payload)
                resp = {"ok": True, "message": msg, "tasks": tasks,
                        "workspaces": ws_ui_payload(wd)}
                if align:
                    resp["align_job"] = start_alignment_job(*align, store_lock=lock)
                elif payload.get("action") == "free":
                    root = wm.normalize_slot(slot)
                    if root:
                        resp["align_job"] = start_reset_job(root, store_lock=lock)
                return self._send(200, json.dumps(resp, ensure_ascii=False))

            if self.path.startswith("/api/bug/"):
                if bm is None:
                    return self._send(400, json.dumps({"error": "bug-monitor not installed"}))
                key = unquote(self.path[len("/api/bug/"):])
                with lock:
                    ok, msg = bm.apply_bug_edit(key, payload)
                    if not ok:
                        return self._send(400, json.dumps({"error": msg}))
                    bugs = bug_payload()
                return self._send(200, json.dumps(
                    {"ok": True, "message": msg, "bugs": bugs}, ensure_ascii=False))

            if self.path == "/api/raid-new":
                if rd is None:
                    return self._send(400, json.dumps({"error": "team-planner not installed"}))
                with RAID_LOCK:
                    ok, msg = rd.new_row(payload)
                    if not ok:
                        return self._send(400, json.dumps({"error": msg}))
                    out = raid_payload()
                return self._send(200, json.dumps(
                    {"ok": True, "id": msg, "payload": out}, ensure_ascii=False))

            # Dispatches a real CI build onto shared emulation hardware, so it
            # is serialised and always reports which slot it took.
            if self.path == "/api/pldm-test":
                if pm is None:
                    return self._send(503, json.dumps({"error": "pr-monitor not installed"}))
                with PLDM_LOCK:
                    feats = [f for f in pm.parse(pm.store_path().read_text(encoding="utf-8"))
                             if f["id"] == payload.get("feature_id")]
                    if not feats:
                        return self._send(404, json.dumps(
                            {"error": f"feature [{payload.get('feature_id')}] not found"}))
                    jid, msg = start_pldm_job(feats[0], payload)
                if not jid:
                    if payload.get("dry") and msg.startswith("dry run OK"):
                        return self._send(200, json.dumps(
                            {"ok": True, "dry": True, "message": msg}, ensure_ascii=False))
                    return self._send(400, json.dumps({"error": msg}))
                return self._send(200, json.dumps(
                    {"ok": True, "message": msg, "align_job": jid}, ensure_ascii=False))

            if self.path.startswith("/api/raid/"):
                if rd is None:
                    return self._send(400, json.dumps({"error": "team-planner not installed"}))
                rid = unquote(self.path[len("/api/raid/"):])
                with RAID_LOCK:
                    if payload.get("close"):
                        ok, msg = rd.close_row(rid, str(payload["close"]))
                    else:
                        ok, msg = rd.apply_edit(rid, payload)
                    if not ok:
                        return self._send(400, json.dumps({"error": msg}))
                    out = raid_payload()
                return self._send(200, json.dumps(
                    {"ok": True, "message": msg, "payload": out}, ensure_ascii=False))

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
                align = alignment_target_for_task_payload(ident, payload)
            resp = {"ok": True, "message": msg, "tasks": tasks,
                    "workspaces": ws_ui_payload(wd)}
            if align:
                resp["align_job"] = start_alignment_job(*align, store_lock=lock)
            return self._send(200, json.dumps(resp, ensure_ascii=False))

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
