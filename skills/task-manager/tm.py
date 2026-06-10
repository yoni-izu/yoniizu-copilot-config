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
    t.setdefault("notes", []).append(f"{TODAY}: {msg}")


def pr_entries(t: dict) -> list[dict]:
    """Unified accessor — yields all PR entries whether legacy or list-based."""
    if t.get("reviews"):
        return t["reviews"]
    if t.get("review"):
        return [t["review"]]
    return []


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
    notes = [f"{TODAY}: initial submission"]
    assigned_ws = None
    skip_reason = None

    if args.backlog:
        status = "backlog"
        ws_name = None
    elif ws_name:
        holder = workspace_holder(d, ws_name)
        if holder is None:
            assigned_ws = ws_name
            notes.append(f"{TODAY}: auto-assigned to workspace {ws_name}")
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
        "notes": [f"{today}: initial submission" +
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

    rows = [("ID", "Title", "Feature", "Cat", "Pri", "Status", "Cx", "Est", "Age", "WS")]
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
    rows = [("ID", "PRs", "Repos", "Authors", "Status", "Files", "+/-", "Age", "Feature")]
    n = 0
    for t in d["tasks"]:
        if t.get("category") != "code-review":
            continue
        if t.get("status") in ("done", "cancelled"):
            continue
        prs = pr_entries(t)
        if all(p.get("merged") for p in prs):
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

    # Auto-purge if all merged
    if all(p.get("merged") for p in prs):
        d["tasks"] = [x for x in d["tasks"] if x["id"] != t["id"]]
        save(d)
        print(f"{t['id']} all PRs merged → purged")
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
        prs = pr_entries(t)
        changed = False
        for p in prs:
            if p.get("merged"):
                continue
            try:
                info = gh_pr_json(p["owner"], p["repo"], p["pr_number"], "state,merged,closed")
            except Exception as e:
                updates.append(f"{t['id']} {p['repo']}#{p['pr_number']}: fetch failed ({e})")
                continue
            if info.get("merged"):
                p["merged"] = True
                p["review_status"] = "merged"
                changed = True
                updates.append(f"{t['id']} {p['repo']}#{p['pr_number']}: merged")
            elif info.get("closed") and not info.get("merged"):
                p["review_status"] = "merged"  # legacy: 'merged' status doubles as 'closed' per skill
                append_note(t, f"{p['repo']}#{p['pr_number']}: closed without merge")
                changed = True
                updates.append(f"{t['id']} {p['repo']}#{p['pr_number']}: closed")
        if changed:
            t["updated"] = TODAY
        if prs and all(p.get("merged") for p in prs):
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

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
