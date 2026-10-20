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
import shutil
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

# pjq.py (pldm-job-queue skill) tracks PLDM hardware jobs; zero-token gh polling.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/pldm-job-queue"))
try:
    import pjq  # noqa: E402
except ImportError:
    pjq = None

# vd.py (vm-delegate skill) keeps the history of every job mctl delegated to a
# VM / lab machine; refreshing it is one ssh round trip per target, no tokens.
sys.path.insert(0, os.path.expanduser("~/.copilot/skills/vm-delegate"))
try:
    import vd  # noqa: E402
except ImportError:
    vd = None

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
# Subcommand: dashboard / serve (HTML view; serve adds editing via a local API)
# ----------------------------------------------------------------------------

DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Task Dashboard</title>
__STYLES__
</head>
<body>
<div id="align-banner">
  <div class="ab-head">
    <span id="align-spin" class="ab-spin"></span>
    <b id="align-title">Aligning workspace…</b>
    <button id="align-close" class="ab-close" aria-label="Dismiss">&times;</button>
  </div>
  <div id="align-body"></div>
</div>
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
      <button id="review-scan" class="btn" style="display:none;" title="Re-probe every open review PR through the gh CLI — approvals, comments, change-requests, re-pushes, merges — and update each task's status. No model, no tokens.">↻ Scan reviews</button>
      <span class="ro-note" id="review-scan-note" style="display:none;"></span>
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
  </div><!-- /sec-work -->

  <div id="sec-ws" style="display:none;">
    <div id="wsview"></div>
  </div>

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
        <button id="pr-collapse" class="btn" title="Collapse or expand the PR rows of every feature">⇱ Collapse all</button>
        <button id="pr-track" class="btn" title="Track a new feature by hand: a name, an optional description, and its PRs">＋ Track feature</button>
        <button id="pr-scan" class="btn primary" title="Re-probe every tracked PR through the gh CLI, and retire features whose PRs are all merged. No model, no tokens.">↻ Scan PRs</button>
        <span id="pr-scan-note" class="ro-note"></span>
      </div>
    </div>
    <div class="kpis" id="pr-kpis"></div>
    <div id="pr-list"></div>
  </div><!-- /sec-prs -->

  <div id="sec-pldmq" style="display:none;">
    <div class="rpt-head">
      <div>
        <h2>PLDM job queue</h2>
        <p class="sub" id="pq-asof"></p>
      </div>
      <div class="slicer">
        <a class="chip" id="pq-portal" target="_blank" rel="noopener">Queue portal &#8599;</a>
        <button id="pq-scan" class="btn primary" title="Follow every tracked job through the gh CLI: build run, queue row, execution run, and the failure evidence. No model, no tokens.">&#8635; Scan queue</button>
        <span id="pq-scan-note" class="ro-note"></span>
      </div>
    </div>
    <div class="kpis" id="pq-kpis"></div>
    <table id="pq-tbl">
      <thead><tr>
        <th style="width:20%;">Job</th><th style="width:14%;">Feature</th>
        <th style="width:12%;">Image</th><th style="width:16%;">Stage</th>
        <th style="width:10%;">Status</th><th style="width:12%;">EDK</th>
        <th style="width:8%;">Age</th><th style="width:8%;">Links</th>
      </tr></thead>
      <tbody id="pq-body"></tbody>
    </table>
  </div><!-- /sec-pldmq -->

  <div id="sec-vm" style="display:none;">
    <div class="rpt-head">
      <div>
        <h2>VM delegator log</h2>
        <p class="sub" id="vm-asof"></p>
      </div>
      <div class="slicer">
        <select id="vm-target"><option value="">All targets</option></select>
        <label class="chip"><input type="checkbox" id="vm-livefirst" checked> Live on top</label>
        <button id="vm-vms" class="btn" title="Query `octl vm list` for the power state, lease and IP of every EC2 instance. Does not touch the targets over ssh.">&#9741; Check VM status</button>
        <button id="vm-sync" class="btn" title="Mirror every target's job logs into ~/.mctl/jobs. The target keeps only the newest log per workspace and overwrites it on the next run, so this is what makes an old log openable later.">&#8595; Sync logs</button>
        <button id="vm-refresh" class="btn primary" title="Ask every target for the state of its job dirs, mirror its logs, and re-read the EC2 power state. One ssh round trip per machine plus an octl call. No model, no tokens.">&#8635; Refresh status</button>
        <span id="vm-refresh-note" class="ro-note"></span>
      </div>
    </div>
    <div id="vm-machines"></div>
    <div id="vm-now"></div>
    <div class="kpis" id="vm-kpis"></div>
    <table id="vm-tbl">
      <thead><tr>
        <th style="width:8%;">Target</th><th style="width:6%;">Kind</th>
        <th style="width:19%;">Workspace</th><th style="width:17%;">Command</th>
        <th style="width:12%;">Started</th><th style="width:7%;">Duration</th>
        <th style="width:19%;">Status</th><th style="width:12%;">Log</th>
      </tr></thead>
      <tbody id="vm-body"></tbody>
    </table>

    <!-- Log viewer. Doubles as a live tail: for a running job the panel
         re-polls `mctl jobs-tail` on a timer, which is a plain ssh round trip,
         so no websocket and no extra dependency. -->
    <div id="vm-logpane" class="logpane" style="display:none;">
      <div class="logpane-head">
        <div class="logpane-t">
          <b id="vm-log-title">log</b>
          <span id="vm-log-sub" class="muted"></span>
        </div>
        <div class="logpane-act">
          <label class="chip" id="vm-log-followwrap"><input type="checkbox" id="vm-log-follow"> Follow</label>
          <button id="vm-log-reload" class="ci-btn" title="Re-read this log now">&#8635;</button>
          <button id="vm-log-close" class="ci-btn" title="Close">&#10005;</button>
        </div>
      </div>
      <pre id="vm-log-body" class="logpane-body"></pre>
    </div>
  </div><!-- /sec-vm -->

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

<script>window.__PORTAL__ = __BOOTDATA__;</script>
__SCRIPTS__
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


def pr_track_edit(path: str, payload: dict) -> tuple[bool, str]:
    """Apply one tracking edit to the pr-monitor memoir.

    A multi-PR add reports per-reference failures rather than aborting: one
    mistyped line should not throw away the references that did resolve.
    """
    fid = payload.get("feature_id")
    if path == "/api/feature-track":
        ok, msg, _ = pm.add_feature(payload.get("name", ""),
                                    payload.get("description", ""),
                                    payload.get("refs") or [])
        return ok, msg
    if not isinstance(fid, int):
        return False, "a feature id is required"
    if path == "/api/feature-untrack":
        return pm.drop_feature(fid)
    if path == "/api/pr-untrack":
        num = payload.get("number")
        if not isinstance(num, int):
            return False, "a PR number is required"
        return pm.remove_pr(fid, num, payload.get("repo") or "")

    refs = payload.get("refs") or []
    if not refs:
        return False, "no PR reference given"
    added, bad = 0, []
    for ref in refs:
        ok, msg = pm.add_pr(fid, str(ref))
        added += ok
        if not ok:
            bad.append(msg)
    note = f"[{fid}] +{added} PR(s)"
    return (added > 0, note + " \u00b7 " + "; ".join(bad)) if bad else (True, note)


def raid_payload() -> dict:
    """RAID rows from team-planner. Editable from the portal via rd.apply_edit."""
    if rd is None:
        return {"rows": [], "owners": [], "counts": {}, "path": ""}
    try:
        return rd.payload()
    except Exception as e:
        return {"rows": [], "owners": [], "counts": {}, "path": "", "error": str(e)}


def pldmq_payload() -> dict:
    """Tracked PLDM jobs from pjq.py. Read-only; scanning is a separate POST."""
    if pjq is None:
        return {"jobs": [], "counts": {}, "portal": "", "error": "pjq unavailable"}
    try:
        return pjq.payload()
    except Exception as e:
        return {"jobs": [], "counts": {}, "portal": "", "error": str(e)}


def vmlog_payload() -> dict:
    """Delegated VM/lab jobs from vd.py. Read-only; the probe is a separate POST."""
    if vd is None:
        return {"jobs": [], "running": [], "counts": {}, "targets": [], "machines": [],
                "machines_checked": "", "last_refresh": "", "log": "",
                "error": "vm-delegate unavailable"}
    try:
        return vd.payload()
    except Exception as e:
        return {"jobs": [], "running": [], "counts": {}, "targets": [], "machines": [],
                "machines_checked": "", "last_refresh": "", "log": "", "error": str(e)}


def is_pinned_slot(slot: str) -> bool:
    """True for slots pinned to a non-main revision via their own manifests.

    Freeing such a slot must NOT reset its repos to main — that would clobber
    the pinned checkout — so it is refreshed in place with ``octl ws sync``.
    """
    rec = next((r for r in wm.roster() if r["slot"] == slot), None)
    return bool(rec and rec["rev"] != "main")


def viable_slots() -> list[str]:
    """Tracked slots that can take a fresh multi-repo checkout.

    Pinned-revision slots are excluded: a PLDM build would have to re-point
    them at PR branches, clobbering the revision they exist to hold.
    """
    return [r["slot"] for r in wm.roster() if r["rev"] == "main" and r["exists"]]


def ws_ui_payload(wd: dict) -> dict:
    """Workspace state plus the tracked roster and its groups.

    ``wm.load()`` only records slots that have *ever* been held, so a UI built
    from it alone can't tell a free slot from one that doesn't exist.
    """
    return dict(wd, canonical=viable_slots(),
                roster=wm.roster(), groups=wm.groups())


ASSETS = Path(__file__).resolve().parent / "assets"
ASSET_TYPES = {".css": "text/css; charset=utf-8",
               ".js": "application/javascript; charset=utf-8"}


def asset_text(name: str) -> str:
    """Read an asset off disk. Never cached, so an edit shows up on reload."""
    return (ASSETS / name).read_text(encoding="utf-8")


def asset_stamp() -> str:
    """Cache-buster derived from the assets' mtimes."""
    return str(int(max((ASSETS / n).stat().st_mtime for n in ("portal.css", "portal.js"))))


def boot_payload(tasks: list[dict], wd: dict) -> str:
    """The one bootstrap object the page hands to portal.js.

    ``</`` is escaped because this lands inside a <script> element, where a
    literal ``</script>`` in any string would close it early.
    """
    return json.dumps({
        "tasks": _annotate_tasks(tasks, wd),
        "workspaces": ws_ui_payload(wd),
        "bugs": bug_payload(),
        "overview": overview_payload(),
        "prs": pr_payload(),
        "raid": raid_payload(),
        "pldmq": pldmq_payload(),
        "vmlog": vmlog_payload(),
    }, ensure_ascii=False).replace("</", "<\\/")


def render_dashboard_html(tasks: list[dict], inline: bool = False) -> str:
    """Render the portal. ``inline`` embeds the assets for the static export,
    which is opened over ``file://`` and so cannot fetch ``/assets/…``."""
    wd = wm.load()
    if inline:
        styles = "<style>\n" + asset_text("portal.css") + "\n</style>"
        scripts = "<script>\n" + asset_text("portal.js") + "\n</script>"
    else:
        v = asset_stamp()
        styles = f'<link rel="stylesheet" href="/assets/portal.css?v={v}">'
        scripts = f'<script src="/assets/portal.js?v={v}"></script>'
    return (DASHBOARD_TEMPLATE
            .replace("__STYLES__", styles)
            .replace("__BOOTDATA__", boot_payload(tasks, wd))
            .replace("__SCRIPTS__", scripts)
            .replace("__GENERATED__", datetime.now().strftime("%Y-%m-%d %H:%M")))


def cmd_dashboard(args: argparse.Namespace) -> int:
    d = load()
    out = Path(os.path.expanduser(args.output)) if args.output else STORE.parent / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dashboard_html(d["tasks"], inline=True), encoding="utf-8")
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

# Serialises `/ci` comment posting so a double-click can't double-fire CI.
PR_CI_LOCK = _threading.Lock()

# Same for the Jira mirror refresh.
BUG_SCAN_LOCK = _threading.Lock()

# Serialises the review scan (gh probe of every open review PR).
REVIEW_SCAN_LOCK = _threading.Lock()

# raid.md is a plain file with no locking of its own.
RAID_LOCK = _threading.Lock()

# Serialises the PLDM queue scan (gh probe of every active tracked job).
PLDMQ_SCAN_LOCK = _threading.Lock()

# Serialises the VM delegation probe (one ssh round trip per target).
VM_REFRESH_LOCK = _threading.Lock()


def _git(repo_dir: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _wip_snapshot(ws_dir: Path, name: str) -> bool:
    """Best-effort: scan the aligned workspace and save a named `wip` snapshot.

    Lets a later re-align restore the exact composite state with a single
    `wip load <name> && wip all checkout` instead of re-querying gh / the
    manifest. Silently no-ops if the `wip` CLI isn't installed.
    """
    wip = shutil.which("wip")
    if not wip:
        return False
    try:
        subprocess.run([wip, "scan"], cwd=str(ws_dir),
                       capture_output=True, text=True, timeout=120, check=False)
        r = subprocess.run([wip, "save", name], cwd=str(ws_dir),
                           capture_output=True, text=True, timeout=60, check=False)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


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


def _checkout_default_record(repo_dir: Path) -> dict | None:
    """Checkout a repo to its default branch; return a job record, or None if
    it's already there (nothing to report).
    """
    key = _worktree_remote_key(repo_dir)
    label = f"{key[0]}/{key[1]}" if key else repo_dir.name
    default = _repo_default_branch(repo_dir)
    cur = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD", timeout=15).stdout.strip()
    if cur == default:
        return None
    if _is_dirty(repo_dir):
        return {"name": label, "role": "default", "action": "skipped-dirty",
                "detail": f"on {cur}, uncommitted changes — left as-is"}
    r = _git(repo_dir, "checkout", default, timeout=60)
    ok = r.returncode == 0
    return {"name": label, "role": "default",
            "action": "checked-out" if ok else "error",
            "detail": (f"{cur} → {default}" if ok
                       else (r.stderr.strip() or "checkout failed").splitlines()[-1])}


def _try_snapshot_restore(ws_dir: Path, worktrees: list, pr_by_repo: dict,
                          name: str, upd, add_repo):
    """Re-align a slot from a saved `wip` snapshot instead of re-querying gh.

    Mirrors the review-mgr `start` snapshot fast path so the dashboard uses the
    same `wip` tool as the skills: if a `review-<task-id>` snapshot exists (saved
    by a prior alignment) and the slot is clean, restore the whole composite
    state with `wip load <name> && wip all checkout`. Returns the set of matched
    review-repo keys when it handled the alignment, or ``None`` to fall back to
    the per-PR checkout loop (no `wip`, no snapshot, or any dirty repo).
    """
    wip = shutil.which("wip")
    snap = Path(os.path.expanduser(f"~/.wip/saved/{name}.wip"))
    if not wip or not snap.is_file():
        return None
    if any(_is_dirty(w) for w in worktrees):
        return None  # let the careful loop report skipped-dirty repos
    upd(current=f"restoring snapshot {name}")
    try:
        lr = subprocess.run([wip, "load", name], cwd=str(ws_dir),
                            capture_output=True, text=True, timeout=60, check=False)
        if lr.returncode != 0:
            return None
        subprocess.run([wip, "all", "checkout"], cwd=str(ws_dir),
                       capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError):
        return None

    seen = set()
    for repo_dir in worktrees:
        key = _worktree_remote_key(repo_dir)
        cur = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD",
                   timeout=15).stdout.strip()
        pr = pr_by_repo.get(key) if key else None
        if pr:
            seen.add(key)
            exp = pr.get("branch") or ""
            ok = bool(exp) and cur == exp
            add_repo({"name": pr.get("repo") or f"{key[0]}/{key[1]}",
                      "role": "review",
                      "action": "checked-out" if ok else "error",
                      "detail": (f"on {cur} (snapshot)" if ok
                                 else f"expected {exp}, on {cur}")})
        else:
            if not key or cur == "HEAD":
                continue
            default = _repo_default_branch(repo_dir)
            if cur == default:
                continue
            add_repo({"name": f"{key[0]}/{key[1]}", "role": "default",
                      "action": "checked-out", "detail": f"on {cur} (snapshot)"})
    return seen


def _octl_bin() -> str:
    """Resolve the `octl` executable. systemd user services don't inherit the
    interactive shell's PATH, so `~/.local/bin` (its usual home) is checked
    explicitly rather than relying on a bare `"octl"` lookup.
    """
    found = shutil.which("octl")
    if found:
        return found
    local = os.path.expanduser("~/.local/bin/octl")
    return local if os.path.isfile(local) else "octl"


def _octl_ws_roots(ws_dir: Path) -> list[Path]:
    """All repo-tool workspace roots directly under a project slot.

    A slot (e.g. ``~/projects/iridium/two``) can hold *several* independent
    octl/west checkouts side by side (``two/fw``, ``two/kmd``, ``two/elpci``,
    ...), each with its own `.repo` — every one of them needs its own
    `octl ws sync`, not just the first one found.
    """
    if (ws_dir / ".repo").is_dir():
        return [ws_dir]
    roots = []
    if ws_dir.is_dir():
        for child in sorted(ws_dir.iterdir()):
            if child.is_dir() and (child / ".repo").is_dir():
                roots.append(child)
    return roots or [ws_dir]


def _octl_ws_root(ws_dir: Path) -> Path:
    """The primary repo-tool workspace root under a slot (prefers `fw`).

    Used for actions scoped to one checkout (e.g. the PLDM build, which
    targets the firmware tree specifically) rather than every root.
    """
    roots = _octl_ws_roots(ws_dir)
    for r in roots:
        if r.name == "fw":
            return r
    return roots[0]


def _find_ssh_auth_sock() -> str | None:
    """Locate an ssh-agent socket that actually has an identity loaded.

    The dashboard runs as a systemd user service, whose `SSH_AUTH_SOCK` may
    point at an empty gpg-agent emulation while the user's real, key-bearing
    agent lives at a per-login `/tmp/ssh-*/agent.*` path. Probe every candidate
    with `ssh-add -l` and return the first one that reports a key, so git auth
    works without the user pre-loading anything into a specific agent.
    """
    import glob
    xdg = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    candidates = []
    if os.environ.get("SSH_AUTH_SOCK"):
        candidates.append(os.environ["SSH_AUTH_SOCK"])
    candidates += sorted(glob.glob("/tmp/ssh-*/agent.*"), reverse=True)
    candidates += [f"{xdg}/ssh-agent.socket", f"{xdg}/keyring/ssh",
                   f"{xdg}/gnupg/S.gpg-agent.ssh"]
    seen = set()
    for sock in candidates:
        if sock in seen or not os.path.exists(sock):
            continue
        seen.add(sock)
        try:
            r = subprocess.run(["ssh-add", "-l"], env=dict(os.environ, SSH_AUTH_SOCK=sock),
                               capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and r.stdout.strip():
            return sock
    return None


def _sync_octl_roots(ws_dir: Path, upd, add_repo) -> tuple[int, int, str]:
    """Run `octl ws sync` in every repo-tool root under a slot, one at a time,
    reporting each directory to the job as it starts and finishes so the UI
    banner can narrate progress instead of going silent for the whole batch.

    Returns (ok_count, total_roots, combined output across all roots).
    """
    roots = _octl_ws_roots(ws_dir)
    ok_n = 0
    chunks = []
    # Without a usable SSH agent, git falls back to the identity file and
    # blocks on a passphrase prompt with no tty to answer it — BatchMode
    # turns that into an immediate failure instead of a multi-minute hang.
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0",
               GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10")
    # Point git at whichever agent actually holds a key (the service's own
    # SSH_AUTH_SOCK is often an empty gpg-agent emulation).
    sock = _find_ssh_auth_sock()
    if sock:
        env["SSH_AUTH_SOCK"] = sock
    for i, root in enumerate(roots, 1):
        label = str(root.relative_to(ws_dir)) if root != ws_dir else "."
        upd(current=f"octl ws sync · {label} ({i}/{len(roots)})")
        try:
            r = subprocess.run([_octl_bin(), "ws", "sync"], cwd=str(root), env=env,
                                capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            add_repo({"name": label, "role": "sync", "action": "error",
                      "detail": f"failed to run: {e}"})
            chunks.append(f"== {label} ==\n{e}")
            continue
        out = (r.stdout or "") + (r.stderr or "")
        chunks.append(f"== {label} ==\n{out}")
        if r.returncode == 0:
            ok_n += 1
            add_repo({"name": label, "role": "sync", "action": "checked-out",
                      "detail": "synced"})
        else:
            tail = [ln for ln in out.splitlines() if ln.strip()][-1:] or ["see output"]
            add_repo({"name": label, "role": "sync", "action": "error",
                      "detail": f"exited {r.returncode}: {tail[0][:150]}"})
    return ok_n, len(roots), "\n".join(chunks)[-8000:]



def run_workspace_alignment(job: dict, store_lock=None) -> None:
    """Background worker: align a workspace slot for a code-review task.

    Updates ``job`` in place (guarded by ALIGN_LOCK) so the UI can poll progress.

    If any review-branch checkout errors out, the whole slot is reverted: every
    repo is switched back to its default branch and the occupy is undone — a
    broken checkout must never squat a slot that looks assigned but isn't
    actually on the reviewer's branches.
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

    ws_dir = wm.slot_path(job["slot"])
    if not ws_dir.is_dir():
        upd(status="error", message=f"workspace dir not found: {ws_dir}", finished=NOW)
        return

    # Match repos to PR entries by their origin remote (owner/repo), NOT by dir
    # name — this is a repo/west workspace where dir names differ from repo names.
    worktrees = _discover_worktrees(ws_dir)
    upd(total=len(worktrees))
    left_alone = 0

    # Fast path: restore the composite state from a prior `wip` snapshot rather
    # than re-checking out every PR branch via gh. Falls back to the per-repo
    # loop below when no snapshot/wip is available or the slot is dirty.
    seen_review = _try_snapshot_restore(
        ws_dir, worktrees, pr_by_repo, f"review-{job['task_id']}", upd, add_repo)

    if seen_review is None:
        seen_review = set()
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

    review_checkout_failed = any(
        r["role"] == "review" and r["action"] == "error" for r in job["repos"])

    if review_checkout_failed:
        upd(current="reverting — checkout failed")
        for repo_dir in worktrees:
            rec = _checkout_default_record(repo_dir)
            if rec is not None:
                add_repo(rec)
        wm.release(job["slot"])
        errors = sum(1 for r in job["repos"]
                     if r["role"] == "review" and r["action"] == "error")
        msg = (f"{errors} branch checkout(s) failed — reverted to main and "
               f"released {job['slot']}")
        upd(status="error", current=None, finished=NOW, message=msg)
        note = f"dashboard: workspace {job['slot']} checkout failed — reverted, slot released"
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
        return

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

    # Snapshot the aligned composite state (best-effort) so re-aligning this
    # task later is a single cheap `wip load review-<id> && wip all checkout`.
    _wip_snapshot(ws_dir, f"review-{job['task_id']}")

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

    ws_dir = wm.slot_path(job["slot"])
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
        rec = _checkout_default_record(repo_dir)
        if rec is None:
            left_alone += 1
        else:
            add_repo(rec)

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

    # Once every repo is back on main, bring the workspace up to date so the
    # freed slot isn't left stale for whoever grabs it next.
    sync_ok_n, sync_total, sync_output = _sync_octl_roots(ws_dir, upd, add_repo)
    msg += f", synced {sync_ok_n}/{sync_total}"
    upd(status="done" if sync_ok_n == sync_total else "error", current=None,
        finished=NOW, message=msg, output=sync_output)


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
    ws_dir = wm.slot_path(job["slot"])
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
            [_octl_bin(), "ws", "build", "--remote", "--target", "pldm",
             "--from-file", str(cfg)],
            cwd=str(_octl_ws_root(ws_dir)), capture_output=True, text=True,
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
        bad = result.get("failed") or []
        parts = []
        if gone:
            parts.append(f"retired {len(gone)}: " + ", ".join(f["name"] for f in gone))
        if bad:
            parts.append(f"\u26a0 {len(bad)} PR(s) gh could not reach: "
                         + ", ".join(f"{b['repo']}#{b['number']}" for b in bad[:4]))
        job["message"] = " \u00b7 ".join(parts)


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


def run_vm_refresh(job: dict) -> None:
    """Probe target(s), mirror their logs, re-read EC2 — on a worker thread.

    This used to be done inline in the request handler. One unreachable target
    costs a 90 s ssh timeout and `octl vm list` revalidates SSO, so the whole
    call could outlive the browser's patience: the fetch gave up, the section
    never re-rendered, and the VM tab looked dead even though the probe was
    succeeding on the server. Streaming it as a job fixes that AND lets the
    banner show which target is being asked.
    """
    only = job.get("target") or None
    try:
        names = []
        try:
            names = [only] if only else vd.targets()
        except Exception:                                      # noqa: BLE001
            pass
        with ALIGN_LOCK:
            job["total"] = len(names) + 1
            job["current"] = f"probing {only}" if only else "probing targets"
        try:
            res = vd.refresh(only)
        except Exception as e:                                 # noqa: BLE001
            with ALIGN_LOCK:
                job.update(status="error", finished=NOW, current=None, message=str(e))
            return
        errs = res.get("errors") or {}
        with ALIGN_LOCK:
            for t in res.get("targets") or []:
                bad = errs.get(t)
                job["repos"].append({
                    "name": t, "action": "unreachable" if bad else "probed",
                    "icon": "\u26a0" if bad else "\u2713",
                    "tone": "bad" if bad else "ok",
                    "detail": bad or "job dirs read \u00b7 logs mirrored locally",
                })
            job["current"] = "octl vm list"
        # Scoped to a bare-metal target there is no instance to ask about, and
        # `octl vm list` costs an SSO revalidation — skip it.
        want_ec2 = True
        if only:
            try:
                want_ec2 = vd.has_instance(only)
            except Exception:                                  # noqa: BLE001
                want_ec2 = False
        if want_ec2:
            try:
                vd.machines(force=True)
            except Exception as e:                             # noqa: BLE001
                with ALIGN_LOCK:
                    job["repos"].append({"name": "octl vm list", "action": "failed",
                                         "icon": "\u26a0", "tone": "bad", "detail": str(e)})
        payload = vmlog_payload()
        with ALIGN_LOCK:
            job["current"] = None
            job["finished"] = NOW
            job["status"] = "done"
            job["result"] = payload
            live = len(payload.get("running") or [])
            scope = f"{only} only" if only else f"{res.get('probed', 0)} target(s)"
            parts = [f"probed {scope}",
                     f"{payload.get('logs_held', 0)} log(s) held locally"]
            if live:
                parts.append(f"{live} job(s) running")
            if errs:
                parts.append("\u26a0 " + ", ".join(f"{t}: {e}" for t, e in errs.items()))
            job["message"] = " \u00b7 ".join(parts)
    finally:
        VM_REFRESH_LOCK.release()


# The VM tab is refreshed by hand, so a reading can sit unconfirmed for as long
# as nobody clicks. That is tolerable for a settled job and wrong for a live
# one, and it is what let a single failed probe leave "running" rows on screen
# for days. The watchdog re-probes ONLY targets holding an unconfirmed job, so
# an idle fleet costs nothing and a watched job stays honest on its own.
VM_WATCHDOG_EVERY = 60           # seconds between sweeps; vd decides what is due


def run_vm_watchdog(stop: _threading.Event) -> None:
    """Background self-heal loop: re-probe targets with unconfirmed jobs."""
    while not stop.wait(VM_WATCHDOG_EVERY):
        if vd is None:
            continue
        # Never contend with a user-initiated refresh: skip this tick instead.
        if not VM_REFRESH_LOCK.acquire(blocking=False):
            continue
        try:
            vd.watchdog()
        except Exception:                                      # noqa: BLE001
            pass                 # a sweep that fails must not kill the loop
        finally:
            VM_REFRESH_LOCK.release()


def start_vm_refresh_job(target: str | None = None) -> str:
    """Register + launch a background VM probe; return its id."""
    jid = f"vmrefresh-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "kind": "vm-refresh", "slot": f"VM jobs \u00b7 {target}" if target else "VM jobs",
        "target": target or "",
        "status": "running", "started": NOW, "finished": None,
        "current": None, "total": 0, "message": "", "repos": [],
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    _threading.Thread(target=run_vm_refresh, args=(job,), daemon=True).start()
    return jid


# gh review state → (banner icon, tone) for each streamed review-scan row.
REVIEW_SCAN_ICONS = {
    "approved": ("\u2713", "ok"), "merged": ("\u2713", "ok"),
    "commented": ("\U0001f4ac", "warn"),
    "waiting-for-re-review": ("\u21bb", "warn"),
    "changes-requested": ("\u2715", "bad"), "closed": ("\u2715", "bad"),
    "error": ("\u26a0", "bad"),
}


def run_review_scan(job: dict) -> None:
    """Background worker: gh-probe every open review PR, streaming one row per
    changed/errored PR into the job so the UI banner can narrate progress.

    Releases REVIEW_SCAN_LOCK (acquired by the request handler) when done.
    """
    def upd(**kw):
        with ALIGN_LOCK:
            job.update(kw)

    def add_repo(rec):
        with ALIGN_LOCK:
            job["repos"].append(rec)

    def progress(i, total, tid, label, status, detail):
        if status is None:                       # about to fetch this PR
            upd(current=f"{label} ({i}/{total})", total=total, scanned=i)
            return
        if status == "nochange":
            return
        icon, tone = REVIEW_SCAN_ICONS.get(status, ("\u00b7", "warn"))
        add_repo({"name": f"{tid} {label}", "action": status,
                  "icon": icon, "tone": tone, "detail": detail})

    try:
        result = portal_review_scan(progress=progress)
    except Exception as e:
        upd(status="error", finished=NOW, current=None, message=str(e))
        return
    finally:
        REVIEW_SCAN_LOCK.release()

    parts = [f"{result['scanned']} PR(s) checked"]
    if result["changed"]:
        parts.append(f"{len(result['changed'])} changed")
    if result.get("removed"):
        parts.append(f"{len(result['removed'])} closed→removed")
    if result["completed"]:
        parts.append(f"{len(result['completed'])} merged→done")
    if result.get("cancelled"):
        parts.append(f"{len(result['cancelled'])} emptied→cancelled")
    if result["errors"]:
        parts.append(f"{len(result['errors'])} error(s)")
    upd(status="done", finished=NOW, current=None, result=result,
        message=" \u00b7 ".join(parts))


def start_review_scan_job() -> str:
    """Register + launch a background review scan; return its id."""
    jid = f"revscan-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "kind": "review-scan", "slot": "reviews",
        "status": "running", "started": NOW, "finished": None,
        "current": None, "total": 0, "scanned": 0, "message": "", "repos": [],
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    _threading.Thread(target=run_review_scan, args=(job,), daemon=True).start()
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


def run_workspace_sync(job: dict) -> None:
    """Background worker: run `octl ws sync` in a slot on demand, whether it's
    occupied or free — a manual refresh independent of occupy/release.
    """
    def upd(**kw):
        with ALIGN_LOCK:
            job.update(kw)

    def add_repo(rec):
        with ALIGN_LOCK:
            job["repos"].append(rec)

    ws_dir = wm.slot_path(job["slot"])
    if not ws_dir.is_dir():
        upd(status="error", message=f"workspace dir not found: {ws_dir}", finished=NOW)
        return
    upd(total=len(_octl_ws_roots(ws_dir)))
    ok_n, total, output = _sync_octl_roots(ws_dir, upd, add_repo)
    upd(status="done" if ok_n == total else "error", current=None, finished=NOW,
        message=f"{ok_n}/{total} synced", output=output)


def start_sync_job(slot_root: str) -> str:
    """Register + launch a background manual sync job for a slot; return id."""
    jid = f"align-{int(datetime.now().timestamp() * 1000)}"
    job = {
        "id": jid, "task_id": None, "slot": slot_root, "kind": "sync",
        "status": "running", "started": NOW, "finished": None,
        "current": None, "total": 0, "message": "", "repos": [],
    }
    with ALIGN_LOCK:
        ALIGN_JOBS[jid] = job
    th = _threading.Thread(target=run_workspace_sync, args=(job,), daemon=True)
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
            if self.path.startswith("/assets/"):
                name = self.path[len("/assets/"):].split("?")[0]
                ctype = ASSET_TYPES.get(Path(name).suffix)
                # Only the two known asset names, so a crafted path cannot escape.
                if name not in ("portal.css", "portal.js") or not ctype:
                    return self._send(404, json.dumps({"error": "not found"}))
                return self._send(200, asset_text(name), ctype)
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
            if self.path == "/api/pldm-jobs":
                return self._send(200, json.dumps(pldmq_payload(), ensure_ascii=False))
            if self.path == "/api/vm-jobs":
                return self._send(200, json.dumps(vmlog_payload(), ensure_ascii=False))
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

            # Re-read just the EC2 power state (`octl vm list`), no ssh probe.
            if self.path == "/api/vm-machines":
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                if not VM_REFRESH_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a VM operation is already running"}))
                try:
                    result = vd.portal_machines()
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    VM_REFRESH_LOCK.release()
                # A hard octl failure returns a bare {"error": ...}; a full
                # payload carries "machines" and may still hold a soft, purely
                # informational per-target probe error from the cached state.
                if "machines" not in result:
                    return self._send(502, json.dumps(result, ensure_ascii=False))
                return self._send(200, json.dumps(result, ensure_ascii=False))

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

            # The review scan probes every open review PR through gh (bodyless,
            # slow-ish). Runs as a background job that streams progress into
            # ALIGN_JOBS so the UI can show a live banner; the worker mutates the
            # task store and releases REVIEW_SCAN_LOCK when done.
            if self.path == "/api/review-scan":
                if not REVIEW_SCAN_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps(
                        {"error": "a review scan is already running"}))
                try:
                    jid = start_review_scan_job()
                except Exception as e:
                    REVIEW_SCAN_LOCK.release()
                    return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "align_job": jid}))

            # The PLDM queue scan follows every active tracked job through the
            # gh CLI (bodyless, slow-ish, zero tokens). It returns the refreshed
            # payload directly so the section can re-render in place.
            if self.path == "/api/pldm-scan":
                if pjq is None:
                    return self._send(503, json.dumps({"error": "pldm-job-queue not installed"}))
                if not PLDMQ_SCAN_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a PLDM scan is already running"}))
                try:
                    result = pjq.portal_scan()
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    PLDMQ_SCAN_LOCK.release()
                return self._send(200, json.dumps(result, ensure_ascii=False))

            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, json.dumps({"error": "invalid JSON"}))

            # Open one stored job log, or poll a live one. Deliberately does
            # NOT take VM_REFRESH_LOCK: reading a local file must never be
            # blocked by an in-flight probe, and the tail is polled on a timer.
            if self.path in ("/api/vm-log", "/api/vm-tail"):
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                target = str(payload.get("target") or "")
                tag = str(payload.get("tag") or "")
                kind = str(payload.get("kind") or "build")
                # The job id is what binds a log to the run that produced it.
                # Without it the slot's single log file is served to whichever
                # job asks, so a re-run of the same workspace shows the previous
                # run's output.
                job_id = str(payload.get("id") or "")
                try:
                    if self.path == "/api/vm-tail":
                        result = vd.portal_tail(target, tag, kind,
                                                payload.get("lines") or 200, job_id)
                    else:
                        result = vd.portal_log(target, tag, kind, job_id)
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps(result, ensure_ascii=False))

            # Manual `octl ws sync`, independent of occupy/free — usable on an
            # occupied or a free slot alike.
            if self.path.startswith("/api/ws-sync/"):
                slot = unquote(self.path[len("/api/ws-sync/"):])
                root = wm.normalize_slot(slot)
                if not root:
                    return self._send(400, json.dumps({"error": f"invalid slot: {slot!r}"}))
                return self._send(200, json.dumps(
                    {"ok": True, "align_job": start_sync_job(root)}, ensure_ascii=False))

            # The VM delegation probe is one ssh round trip per target plus an
            # `octl vm list`, so it is dispatched as a background job and polled
            # through /api/align/<id> like the PR scan. Running it inline made
            # the browser give up on a slow or unreachable target.
            #
            # An optional {"target": "<name>"} scopes it to one machine. That is
            # not a nicety: an unreachable lab desktop costs a 90 s ssh timeout,
            # so a fleet refresh is minutes while "just the VM" is seconds.
            if self.path == "/api/vm-refresh":
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                only = str(payload.get("target") or "") or None
                if only and not vd.is_target(only):
                    return self._send(400, json.dumps({"error": f"unknown target: {only}"}))
                if not VM_REFRESH_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a VM refresh is already running"}))
                try:
                    jid = start_vm_refresh_job(only)
                except Exception as e:
                    VM_REFRESH_LOCK.release()
                    return self._send(500, json.dumps({"error": str(e)}))
                return self._send(200, json.dumps({"ok": True, "job": jid}))

            # Mirror job logs locally without probing or asking octl anything.
            # The remote keeps one log per slot and overwrites it on the next
            # run, so this is what makes an old log openable at all.
            if self.path == "/api/vm-sync":
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                only = str(payload.get("target") or "") or None
                if not VM_REFRESH_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a VM operation is already running"}))
                try:
                    result = vd.portal_sync(only)
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    VM_REFRESH_LOCK.release()
                if "jobs" not in result:
                    return self._send(400, json.dumps(result, ensure_ascii=False))
                return self._send(200, json.dumps(result, ensure_ascii=False))

            # EC2 power control for a delegation target (`octl vm start|stop`).
            if self.path == "/api/vm-power":
                if vd is None:
                    return self._send(503, json.dumps({"error": "vm-delegate not installed"}))
                if not VM_REFRESH_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps({"error": "a VM operation is already running"}))
                try:
                    result = vd.portal_power(payload.get("action", ""), payload.get("id", ""))
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    VM_REFRESH_LOCK.release()
                # Same shape rule as /api/vm-machines: no "machines" key means
                # octl itself refused, anything else is a full payload.
                if "machines" not in result:
                    return self._send(400, json.dumps(result, ensure_ascii=False))
                return self._send(200, json.dumps(result, ensure_ascii=False))

            # --- tracked roster + groups -------------------------------------
            # Which workspaces the portal shows is user-editable; every mutation
            # answers with the whole refreshed payload so the view re-renders.
            def _roster_reply(msg, **extra):
                with lock:
                    wd = wm.load()
                    body = {"ok": True, "message": msg,
                            "tasks": _annotate_tasks(load()["tasks"], wd),
                            "workspaces": ws_ui_payload(wd)}
                body.update(extra)
                return self._send(200, json.dumps(body, ensure_ascii=False))

            if self.path == "/api/ws-track":
                ok, msg, _slot = wm.track(
                    payload.get("path", ""), label=payload.get("label"),
                    rev=payload.get("rev"), group=payload.get("group"),
                    badge=payload.get("badge"))
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                return _roster_reply(msg)

            if self.path.startswith("/api/ws-untrack/"):
                slot = unquote(self.path[len("/api/ws-untrack/"):])
                ok, msg = wm.untrack(slot)
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                return _roster_reply(msg)

            if self.path.startswith("/api/ws-meta/"):
                slot = unquote(self.path[len("/api/ws-meta/"):])
                ok, msg = wm.set_slot_fields(slot, payload)
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                return _roster_reply(msg)

            if self.path == "/api/ws-groups":
                action = payload.get("action", "add")
                if action == "add":
                    ok, msg, gid = wm.add_group(payload.get("name", ""), payload.get("color"))
                    if not ok:
                        return self._send(400, json.dumps({"error": msg}))
                    return _roster_reply(msg, group_id=gid)
                if action == "edit":
                    ok, msg = wm.edit_group(payload.get("id", ""), payload.get("name"),
                                            payload.get("color"))
                elif action == "delete":
                    ok, msg = wm.delete_group(payload.get("id", ""))
                else:
                    return self._send(400, json.dumps({"error": f"unknown action: {action}"}))
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                return _roster_reply(msg)

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
                    if is_pinned_slot(root):
                        # Pinned revision: refresh in place, never reset to main.
                        resp["align_job"] = start_sync_job(root)
                    elif root:
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

            # Posts a `/ci` comment on one, all, or only-failed PRs of a feature
            # to (re-)trigger CI. Serialised so a double-click can't double-fire.
            if self.path == "/api/pr-ci":
                if pm is None:
                    return self._send(503, json.dumps({"error": "pr-monitor not installed"}))
                with PR_CI_LOCK:
                    res = pm.trigger_ci(payload.get("feature_id"),
                                        scope=payload.get("scope", "all"),
                                        number=payload.get("number"))
                if res.get("error"):
                    code = 404 if "not found" in res["error"] else 400
                    return self._send(code, json.dumps(res, ensure_ascii=False))
                return self._send(200, json.dumps({"ok": True, **res}, ensure_ascii=False))

            # Tracking edits rewrite the memoir, which a running scan also
            # rewrites wholesale — so they take the scan lock rather than race it.
            if self.path in ("/api/pr-track", "/api/pr-untrack",
                             "/api/feature-track", "/api/feature-untrack"):
                if pm is None:
                    return self._send(503, json.dumps({"error": "pr-monitor not installed"}))
                if not PR_SCAN_LOCK.acquire(blocking=False):
                    return self._send(409, json.dumps(
                        {"error": "a PR scan is running \u2014 try again once it finishes"}))
                try:
                    ok, msg = pr_track_edit(self.path, payload)
                except Exception as e:
                    return self._send(500, json.dumps({"error": str(e)}))
                finally:
                    PR_SCAN_LOCK.release()
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                with lock:
                    return self._send(200, json.dumps(
                        {"ok": True, "message": msg, "prs": pr_payload()},
                        ensure_ascii=False))

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

    # Keeps VM job statuses honest without a click: see run_vm_watchdog.
    if vd is not None:
        threading.Thread(target=run_vm_watchdog, args=(stop,), daemon=True).start()

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
