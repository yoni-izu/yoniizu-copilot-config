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

# ----------------------------------------------------------------------------
# Constants & helpers
# ----------------------------------------------------------------------------

STORE = Path(os.path.expanduser("~/tasks/tasks.json"))
PROJECTS_ROOT = Path(os.path.expanduser("~/projects"))
DEFAULT_PROJECT = "iridium"

CATEGORIES = {"development", "code-review", "planning", "testing", "misc"}
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
    """Return workspace name if cwd is under ~/projects/<project>/<ws>/..."""
    p = Path(cwd) if cwd else Path.cwd()
    try:
        rel = p.resolve().relative_to(PROJECTS_ROOT.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 2:
        return None
    return parts[1]  # parts[0] = project, parts[1] = workspace name


def workspace_holder(d: dict, ws: str) -> dict | None:
    for t in d["tasks"]:
        if t.get("status") in ("done", "cancelled"):
            continue
        if t.get("workspace") == ws:
            return t
    return None


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

    # Workspace auto-assignment
    ws_name = args.workspace or (None if args.no_workspace else detect_workspace(args.cwd))
    status = "todo"
    notes = [f"{NOW}: initial submission"]
    assigned_ws = None
    skip_reason = None

    if args.backlog:
        status = "backlog"
        ws_name = None
    elif ws_name:
        holder = workspace_holder(d, ws_name)
        if holder is None:
            assigned_ws = ws_name
            notes.append(f"{NOW}: auto-assigned to workspace {ws_name}")
        else:
            status = "backlog"
            skip_reason = f"workspace '{ws_name}' occupied by {holder['id']} ({holder['title']})"
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
        "workspace": assigned_ws,
        "notes": notes,
    }
    d["tasks"].append(task)
    d["next_id"] += 1
    save(d)

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
        "workspace": None,
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
            t.get("workspace") or "—",
        ))
    _print_table(rows)

    active = sum(1 for t in tasks if t["status"] == "in-progress")
    blocked = sum(1 for t in tasks if t["status"] == "blocked")
    done = sum(1 for t in d["tasks"] if t["status"] == "done")
    total_est = sum(t.get("cost_hours", 0) for t in tasks)
    print(f"\n{active} active | {blocked} blocked | {done} done | total est: {total_est}h remaining")

    ws_map = {t["workspace"]: t["id"] for t in d["tasks"]
              if t.get("workspace") and t.get("status") not in ("done", "cancelled")}
    if ws_map:
        print("Workspaces: " + ", ".join(f"{k} → {v}" for k, v in sorted(ws_map.items())))
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
            t["id"], pr_nums, repos, authors, t["status"],
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
    t["status"] = new_status
    t["updated"] = TODAY
    if new_status == "done":
        t["completed"] = TODAY
        t["workspace"] = None
    elif new_status == "cancelled":
        t["workspace"] = None
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
    holder = workspace_holder(d, ws)
    if holder and holder["id"] != t["id"] and not args.force:
        print(f"[tm] workspace '{ws}' occupied by {holder['id']} ({holder['title']}). "
              f"Use --force to displace.", file=sys.stderr)
        return 1
    if holder and holder["id"] != t["id"] and args.force:
        holder["workspace"] = None
        append_note(holder, f"workspace {ws} released to {t['id']}")
    t["workspace"] = ws
    if t["status"] == "todo":
        t["status"] = "in-progress"
    t["updated"] = TODAY
    append_note(t, f"assigned to workspace {ws}")
    save(d)
    print(f"{t['id']} → workspace {ws} ({t['status']})")
    return 0


# ----------------------------------------------------------------------------
# Subcommand: workspaces
# ----------------------------------------------------------------------------

def cmd_workspaces(args: argparse.Namespace) -> int:
    d = load()
    project = args.project or DEFAULT_PROJECT
    proj_dir = PROJECTS_ROOT / project
    if not proj_dir.is_dir():
        print(f"[tm] project dir not found: {proj_dir}", file=sys.stderr)
        return 1
    ws_dirs = sorted(p.name for p in proj_dir.iterdir() if p.is_dir())
    by_ws: dict[str, dict] = {}
    for t in d["tasks"]:
        if t.get("workspace") and t.get("status") not in ("done", "cancelled"):
            by_ws[t["workspace"]] = t
    rows = [("Workspace", "Task ID", "Title", "Status")]
    occupied = 0
    for ws in ws_dirs:
        t = by_ws.get(ws)
        if t:
            occupied += 1
            title = t["title"]
            if len(title) > 50:
                title = title[:47] + "..."
            rows.append((ws, t["id"], title, t["status"]))
        else:
            rows.append((ws, "—", "—", "—"))
    _print_table(rows)
    print(f"\n{occupied} / {len(ws_dirs)} workspaces occupied")
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

# Map review_status → task status delta
REVIEW_TO_TASK = {
    "pending": None,
    "in-progress": "in-progress",
    "commented": "blocked",
    "changes-requested": "blocked",
    "waiting-for-re-review": "todo",
    "approved": "done_if_all",
    "merged": "done_if_all",
}


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

    # Derive task status
    all_resolved = all(p["review_status"] in ("approved", "merged") for p in prs)
    desired = REVIEW_TO_TASK[args.status]
    if desired == "done_if_all" and all_resolved:
        t["status"] = "done"
        t["completed"] = TODAY
        t["workspace"] = None
    elif desired == "done_if_all":
        t["status"] = "in-progress"
    elif desired:
        t["status"] = desired
        if desired in ("blocked", "todo"):
            t["workspace"] = None  # free slot when ball is with author / awaiting re-review
    t["updated"] = TODAY
    append_note(t, f"review-update: {args.status}" + (f" on #{args.pr}" if args.pr else " on all PRs"))

    # Auto-purge if all PRs are resolved (merged or closed without merge)
    if all(pr_resolved(p) for p in prs):
        d["tasks"] = [x for x in d["tasks"] if x["id"] != t["id"]]
        save(d)
        reason = "merged" if all(p.get("merged") for p in prs) else "merged/closed"
        print(f"{t['id']} all PRs {reason} → purged")
        return 0

    save(d)
    print(f"{t['id']} status={t['status']} review_status={args.status}")
    return 0


# ----------------------------------------------------------------------------
# Subcommand: review-check (purge merged)
# ----------------------------------------------------------------------------

def cmd_review_check(args: argparse.Namespace) -> int:
    d = load()
    purged = []
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
        if prs and all(pr_resolved(p) for p in prs):
            purged.append(t["id"])
            continue
        keep.append(t)
    d["tasks"] = keep
    save(d)
    for u in updates:
        print(u)
    if purged:
        print("purged: " + ", ".join(purged))
    if not updates and not purged:
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
        task["workspace"] = None
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
  .rv-waiting-for-re-review{background:#ede9fe;color:#6d28d9;} .rv-approved{background:#dcfce7;color:#15803d;}
  .rv-merged{background:#dcfce7;color:#15803d;}
  @media (prefers-color-scheme: dark){
    .rv-pending{background:#37415133;color:#9ca3af;} .rv-in-progress{background:#78350f44;color:#fcd34d;}
    .rv-commented{background:#1e3a8a33;color:#93c5fd;} .rv-changes-requested{background:#7f1d1d44;color:#fca5a5;}
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
  .ro-note { color:var(--muted); font-size:12px; font-style:italic; }
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
</div>

<script>
let TASKS = __DATA__;
let SERVED = false;
const JIRA_BASE = "https://elementlabs.atlassian.net/browse/";
const STATUS_ORDER = ["in-progress","blocked","todo","backlog","done","cancelled"];
const PRI_ORDER = ["critical","high","medium","low"];
const CPX_ORDER = ["trivial","small","medium","large","epic"];
const CATEGORIES = ["development","code-review","planning","testing","misc"];

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
    case "reviewstatus": { const p=prs(t); return p.length?(p.map(r=>r.review_status||"").sort()[0]||""):"~"; }
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
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function feature(t){
  let d=(t.description||"").trim();
  let f = d ? d.split(". ")[0] : (t.title||"").replace(/^Review:\s*/i,"");
  f=f.replace(/\s+/g," ").trim();
  return f.length>90 ? f.slice(0,89)+"…" : f;
}

// ---- build filter dropdowns ----
function uniq(arr){ return [...new Set(arr)]; }
const DD_GROUPS = ["status","category","priority","complexity","workspace"];
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
  ["status","category","priority","complexity","workspace"].forEach(g=>{
    document.getElementById("f-"+g).innerHTML="";
  });
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
  const hideDone=document.getElementById("o-hidedone").checked;
  const needJira=document.getElementById("o-jira").checked;
  const needWs=document.getElementById("o-ws").checked;
  const reviewOnly=document.getElementById("o-review").checked;

  return TASKS.filter(t=>{
    if(activeTab!=="all" && t.category!==activeTab) return false;
    if(hideDone && (t.status==="done"||t.status==="cancelled")) return false;
    if(st.length && !st.includes(t.status)) return false;
    if(cat.length && !cat.includes(t.category)) return false;
    if(pri.length && !pri.includes(t.priority)) return false;
    if(cpx.length && !cpx.includes(t.complexity)) return false;
    if(ws.length && !ws.includes(t.workspace)) return false;
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
      const ss=[...new Set(p.map(r=>r.review_status||"pending"))];
      return `<td>${ss.map(s=>`<span class="badge rv-${esc(s)}">${esc(s)}</span>`).join(" ")}</td>`; }
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
  return `<div class="editor" data-id="${esc(t.id)}">
    <div class="fields">
      <label>Status<select class="e-status">${selOpts(STATUS_ORDER,t.status)}</select></label>
      <label>Priority<select class="e-priority">${selOpts(PRI_ORDER,t.priority)}</select></label>
      <label>Category<select class="e-category">${selOpts(CATEGORIES,t.category)}</select></label>
      <label>Complexity<select class="e-complexity"><option value="">—</option>${selOpts(CPX_ORDER,t.complexity||"")}</select></label>
      <label>Est (h)<input type="number" step="0.5" class="e-cost" value="${t.cost_hours!=null?t.cost_hours:""}" style="min-width:90px"></label>
      <label>Workspace<input type="text" class="e-workspace" value="${esc(t.workspace||"")}" style="min-width:140px"></label>
    </div>
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
  const prHtml = p.length ? `<div><h4>Pull Requests</h4><div class="pr-list">`+
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
  return TASKS.filter(t=>(cat==="all"||t.category===cat) && !(["done","cancelled"].includes(t.status))).length;
}
function buildTabs(){
  const present=CATEGORIES.filter(c=>TASKS.some(t=>t.category===c));
  const tabs=[["all","All"], ...present.map(c=>[c,c])];
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
function selectTab(tab){
  activeTab=tab;
  document.querySelectorAll("#tabs .tab").forEach(b=>b.classList.toggle("on",b.dataset.tab===tab));
  // category filter is redundant inside a category tab: clear + hide it
  const gc=document.getElementById("grp-category");
  if(gc) gc.style.display = tab==="all" ? "" : "none";
  if(tab!=="all"){
    document.querySelectorAll('input[data-group="category"]').forEach(cb=>{ cb.checked=false; });
    updateDdLabel("category");
  }
  applyTabPreset(tab);
  render();
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
    const allPrs=scope.flatMap(t=>prs(t));
    const awaiting=allPrs.filter(r=>["pending","in-progress"].includes(r.review_status)).length;
    const changes=allPrs.filter(r=>r.review_status==="changes-requested").length;
    cards.push(["PRs",allPrs.length],["Awaiting",awaiting],["Changes&nbsp;req",changes]);
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
      if(r.ok){ TASKS=await r.json(); }
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
      if(r.ok){ TASKS=await r.json(); }
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


def render_dashboard_html(tasks: list[dict]) -> str:
    payload = json.dumps(tasks, ensure_ascii=False).replace("</", "<\\/")
    return (DASHBOARD_TEMPLATE
            .replace("__DATA__", payload)
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
    workspace, add_note. Returns (ok, message).
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
                t["workspace"] = None
            elif new == "cancelled":
                t["workspace"] = None
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
        if ws == "":
            if t.get("workspace"):
                changes.append(f"workspace {t['workspace']} → (cleared)")
            t["workspace"] = None
        elif ws != t.get("workspace"):
            holder = workspace_holder(d, ws)
            if holder and holder["id"] != t["id"]:
                return False, f"workspace '{ws}' occupied by {holder['id']}"
            t["workspace"] = ws
            if t["status"] == "todo":
                t["status"] = "in-progress"
            changes.append(f"workspace → {ws}")

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
                    return self._send(200, json.dumps(load()["tasks"], ensure_ascii=False))
            return self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            if not self.path.startswith("/api/task/"):
                return self._send(404, json.dumps({"error": "not found"}))
            ident = self.path[len("/api/task/"):]
            from urllib.parse import unquote
            ident = unquote(ident)
            length = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, json.dumps({"error": "invalid JSON"}))
            with lock:
                ok, msg = apply_task_edit(ident, payload)
                if not ok:
                    return self._send(400, json.dumps({"error": msg}))
                tasks = load()["tasks"]
            return self._send(200, json.dumps({"ok": True, "message": msg, "tasks": tasks},
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
    a = sub.add_parser("review-check", help="poll GitHub for merges, purge merged tasks")
    a.set_defaults(func=cmd_review_check)

    # review-msg
    a = sub.add_parser("review-msg", help="print a review-request message for a task")
    a.add_argument("id")
    a.set_defaults(func=cmd_review_msg)

    # re-review
    a = sub.add_parser(
        "re-review",
        help="mark PRs for re-review: find their task, add any new PRs, set waiting-for-re-review",
    )
    a.add_argument("urls", nargs="+", metavar="PR_URL")
    a.set_defaults(func=cmd_re_review)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
