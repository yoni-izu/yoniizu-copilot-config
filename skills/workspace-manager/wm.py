#!/usr/bin/env python3
"""workspace-manager CLI + library — owns workspace slot occupancy.

``~/tasks/workspaces.json`` is the single source of truth for which physical
workspace slot (``~/projects/<project>/<slot>``) is currently occupied and by
what. A slot can be held by:

  * a **task** — a logged task-manager item (``type: task``), or
  * an **ad-hoc** purpose (``type: adhoc``) — any reason you grab a workspace
    for that you don't want to log a dedicated task for (monitor PRs, debug an
    issue, run an experiment, ...).

This module is imported by task-manager's ``tm.py`` (which delegates every
workspace mutation here) and is also runnable as a standalone CLI. It carries
**no** task-store knowledge beyond the task id string it stores in a holder —
resolving that id back to a task is the caller's job.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

STORE = Path(os.path.expanduser("~/tasks/workspaces.json"))
TASKS_STORE = Path(os.path.expanduser("~/tasks/tasks.json"))
PROJECTS_ROOT = Path(os.path.expanduser("~/projects"))
DEFAULT_PROJECT = "iridium"

ADHOC_TAGS = ["monitor", "debug", "development", "experiment", "other"]
HOLDER_TYPES = ["task", "adhoc"]

TODAY = date.today().isoformat()


# ----------------------------------------------------------------------------
# Store I/O
# ----------------------------------------------------------------------------

def _empty() -> dict:
    return {"version": 1, "slots": {}, "tracked": {}, "groups": []}


def load() -> dict:
    if not STORE.exists():
        return _empty()
    try:
        with STORE.open() as f:
            d = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty()
    d.setdefault("version", 1)
    d.setdefault("slots", {})
    d.setdefault("tracked", {})
    d.setdefault("groups", [])
    return d


def save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("w") as f:
        json.dump(d, f, indent=2)


# ----------------------------------------------------------------------------
# Slot helpers
# ----------------------------------------------------------------------------

def normalize_slot(ws: str | None) -> str | None:
    """Return the root slot name for a workspace string.

    Sub-paths collapse to their top slot: ``one/fw`` -> ``one``. Whitespace is
    stripped. Empty / ``None`` returns ``None``.
    """
    if not ws:
        return None
    ws = ws.strip().strip("/")
    if not ws:
        return None
    return ws.split("/")[0]


def canonical_slots(project: str | None = None) -> list[str]:
    """Workspace slot names discovered under ``~/projects/<project>/``.

    Falls back to the six canonical slots when the project dir is unreadable so
    callers/dashboards always have a stable set to render.
    """
    proj = PROJECTS_ROOT / (project or DEFAULT_PROJECT)
    try:
        slots = sorted(p.name for p in proj.iterdir() if p.is_dir())
    except OSError:
        slots = []
    return slots or ["one", "two", "three", "four", "five", "six"]


# ----------------------------------------------------------------------------
# Tracked roster + groups
# ----------------------------------------------------------------------------
# Which workspaces the dashboard shows is a user-editable roster, not a scan of
# the project dir: a slot may live anywhere on disk, and a directory that merely
# exists isn't necessarily one you want tracked.

# Seeded into an empty store so an existing install keeps the roster it had.
_SEED_TRACKED = (
    [{"slot": s, "badge": str(i + 1), "rev": "main"}
     for i, s in enumerate(["one", "two", "three", "four", "five", "six"])]
    + [{"slot": "a1_power_on", "badge": "A1", "rev": "a1_power_on",
        "label": "A1 Power-On"}]
    + [{"slot": f"a1_power_on_{n:03d}", "badge": f"A1\u00b7{n}",
        "rev": "a1_power_on", "label": f"A1 Power-On {n:03d}"}
       for n in (2, 3, 4)]
)


def _seed_tracked() -> dict:
    out = {}
    for rec in _SEED_TRACKED:
        slot = rec["slot"]
        out[slot] = {
            "path": str(PROJECTS_ROOT / DEFAULT_PROJECT / slot),
            "label": rec.get("label") or slot,
            "badge": rec["badge"],
            "rev": rec.get("rev", "main"),
            "group": "",
            "order": len(out),
        }
    return out


def tracked(d: dict | None = None, persist: bool = True) -> dict:
    """Roster of tracked workspaces: ``slot -> record``. Seeds on first use."""
    own = d if d is not None else load()
    if not own.get("tracked"):
        own["tracked"] = _seed_tracked()
        if persist and d is None:
            save(own)
    return own["tracked"]


def roster(d: dict | None = None) -> list[dict]:
    """Tracked workspaces as an ordered list of UI-ready records."""
    own = d if d is not None else load()
    recs = tracked(own, persist=d is None)
    out = []
    for slot, rec in recs.items():
        out.append({
            "slot": slot,
            "path": rec.get("path") or str(PROJECTS_ROOT / DEFAULT_PROJECT / slot),
            "label": rec.get("label") or slot,
            "badge": rec.get("badge") or slot[:2].upper(),
            "rev": rec.get("rev") or "main",
            "group": rec.get("group") or "",
            "order": rec.get("order", 0),
            "exists": Path(os.path.expanduser(
                rec.get("path") or str(PROJECTS_ROOT / DEFAULT_PROJECT / slot))).is_dir(),
        })
    out.sort(key=lambda r: (r["order"], r["slot"]))
    return out


def slot_path(slot: str, d: dict | None = None) -> Path:
    """On-disk directory for a slot — the tracked path, else the project dir."""
    root = normalize_slot(slot) or ""
    rec = tracked(d if d is not None else load(), persist=False).get(root)
    if rec and rec.get("path"):
        return Path(os.path.expanduser(rec["path"]))
    return PROJECTS_ROOT / DEFAULT_PROJECT / root


def track(path: str, label: str | None = None, rev: str | None = None,
          group: str | None = None, badge: str | None = None):
    """Add a workspace directory to the tracked roster. ``(ok, msg, slot)``."""
    raw = (path or "").strip()
    if not raw:
        return False, "a workspace path is required", None
    p = Path(os.path.expanduser(raw)).resolve()
    if not p.is_dir():
        return False, f"not a directory: {p}", None
    slot = p.name
    d = load()
    recs = tracked(d, persist=False)
    if slot in recs:
        return False, f"'{slot}' is already tracked ({recs[slot].get('path')})", None
    if group and not get_group(group, d):
        return False, f"unknown group: {group}", None
    recs[slot] = {
        "path": str(p),
        "label": (label or "").strip() or slot,
        "badge": (badge or "").strip() or slot[:2].upper(),
        "rev": (rev or "").strip() or "main",
        "group": group or "",
        "order": max((r.get("order", 0) for r in recs.values()), default=-1) + 1,
    }
    save(d)
    return True, f"tracking '{slot}' ({p})", slot


def untrack(slot: str):
    """Drop a workspace from the roster. Refuses while it is held."""
    root = normalize_slot(slot)
    if not root:
        return False, f"invalid slot: {slot!r}"
    d = load()
    recs = tracked(d, persist=False)
    if root not in recs:
        return False, f"'{root}' is not tracked"
    if get_holder(root, d):
        return False, (f"'{root}' is held by {_holder_label(get_holder(root, d))}"
                       " \u2014 free it first")
    recs.pop(root)
    save(d)
    return True, f"stopped tracking '{root}'"


def set_slot_fields(slot: str, fields: dict):
    """Update roster metadata (``group``/``label``/``rev``/``badge``) for a slot."""
    root = normalize_slot(slot)
    if not root:
        return False, f"invalid slot: {slot!r}"
    d = load()
    recs = tracked(d, persist=False)
    rec = recs.get(root)
    if rec is None:
        return False, f"'{root}' is not tracked"
    if "group" in fields:
        gid = (fields.get("group") or "").strip()
        if gid and not get_group(gid, d):
            return False, f"unknown group: {gid}"
        rec["group"] = gid
    for key in ("label", "rev", "badge"):
        if key in fields:
            val = (fields.get(key) or "").strip()
            rec[key] = val or (root if key == "label" else rec.get(key, ""))
    save(d)
    return True, f"updated '{root}'"


# --- groups -----------------------------------------------------------------

GROUP_COLORS = ["#2563eb", "#a855f7", "#16a34a", "#f59e0b", "#ef4444", "#0891b2"]


def groups(d: dict | None = None) -> list[dict]:
    own = d if d is not None else load()
    return own.get("groups", [])


def get_group(gid: str, d: dict | None = None) -> dict | None:
    return next((g for g in groups(d) if g["id"] == gid), None)


def _group_id(name: str, existing: set) -> str:
    base = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "group"
    gid, n = base, 2
    while gid in existing:
        gid, n = f"{base}-{n}", n + 1
    return gid


def add_group(name: str, color: str | None = None):
    name = (name or "").strip()
    if not name:
        return False, "a group name is required", None
    d = load()
    gs = d.setdefault("groups", [])
    if any(g["name"].lower() == name.lower() for g in gs):
        return False, f"a group named '{name}' already exists", None
    gid = _group_id(name, {g["id"] for g in gs})
    gs.append({"id": gid, "name": name,
               "color": (color or "").strip() or GROUP_COLORS[len(gs) % len(GROUP_COLORS)]})
    save(d)
    return True, f"group '{name}' added", gid


def edit_group(gid: str, name: str | None = None, color: str | None = None):
    d = load()
    g = get_group(gid, d)
    if not g:
        return False, f"unknown group: {gid}"
    if name is not None:
        new = name.strip()
        if not new:
            return False, "a group name is required"
        if any(o["id"] != gid and o["name"].lower() == new.lower() for o in groups(d)):
            return False, f"a group named '{new}' already exists"
        g["name"] = new
    if color is not None and color.strip():
        g["color"] = color.strip()
    save(d)
    return True, f"group '{g['name']}' updated"


def delete_group(gid: str):
    """Remove a group; its workspaces fall back to ungrouped."""
    d = load()
    g = get_group(gid, d)
    if not g:
        return False, f"unknown group: {gid}"
    d["groups"] = [o for o in groups(d) if o["id"] != gid]
    for rec in tracked(d, persist=False).values():
        if rec.get("group") == gid:
            rec["group"] = ""
    save(d)
    return True, f"group '{g['name']}' deleted"



def slot_from_cwd(cwd: str | None = None) -> str | None:
    """Root slot name if ``cwd`` sits under ``~/projects/<project>/<slot>/``."""
    p = Path(cwd) if cwd else Path.cwd()
    try:
        rel = p.resolve().relative_to(PROJECTS_ROOT.resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if len(parts) < 2:
        return None
    return parts[1]  # parts[0] = project, parts[1] = slot name


def get_holder(slot: str, d: dict | None = None) -> dict | None:
    """Holder record for a slot (normalized), or ``None`` when free."""
    slot = normalize_slot(slot)
    if not slot:
        return None
    d = d if d is not None else load()
    rec = d["slots"].get(slot)
    if not rec:
        return None
    return rec.get("holder") or None


def is_free(slot: str, d: dict | None = None) -> bool:
    return get_holder(slot, d) is None


def all_holders(d: dict | None = None) -> dict[str, dict]:
    """Map of slot -> holder for every currently-occupied slot."""
    d = d if d is not None else load()
    return {s: rec["holder"] for s, rec in d["slots"].items()
            if rec and rec.get("holder")}


def slot_of_task(task_id: str, d: dict | None = None) -> str | None:
    """Which slot (if any) a given task id currently holds."""
    d = d if d is not None else load()
    for s, rec in d["slots"].items():
        h = rec.get("holder") if rec else None
        if h and h.get("type") == "task" and h.get("task_id") == task_id:
            return s
    return None


# ----------------------------------------------------------------------------
# Mutations (single choke-points for occupancy)
# ----------------------------------------------------------------------------

def _set_holder(d: dict, slot: str, holder: dict | None) -> None:
    if holder is None:
        d["slots"].pop(slot, None)
    else:
        d["slots"][slot] = {"holder": holder}


def occupy_task(slot: str, task_id: str, detail: str | None = None,
                force: bool = False, since: str | None = None,
                d: dict | None = None, autosave: bool = True):
    """Assign a task to a slot.

    Returns ``(ok, message, displaced_holder)``. Refuses a busy slot unless
    ``force`` (which displaces the current holder). Moving a task that already
    holds another slot releases the old slot first and preserves its acquire
    date.
    """
    root = normalize_slot(slot)
    if not root:
        return False, f"invalid slot: {slot!r}", None
    own = d if d is not None else load()

    # Preserve the acquire date if this task is simply being re-stamped / moved.
    prev_slot = slot_of_task(task_id, own)
    prev_since = None
    if prev_slot:
        prev_since = (own["slots"][prev_slot]["holder"] or {}).get("since")
        if prev_slot != root:
            _set_holder(own, prev_slot, None)

    current = get_holder(root, own)
    displaced = None
    if current and not (current.get("type") == "task" and current.get("task_id") == task_id):
        if not force:
            return False, f"slot '{root}' occupied by {_holder_label(current)}", None
        displaced = current

    holder = {
        "type": "task",
        "task_id": task_id,
        "detail": normalize_or_keep(detail, root),
        "since": since or prev_since or TODAY,
        "notes": [],
    }
    _set_holder(own, root, holder)
    if autosave and d is None:
        save(own)
    return True, f"{task_id} → {root}", displaced


def occupy_adhoc(slot: str, label: str, tag: str = "other",
                 force: bool = False, since: str | None = None,
                 d: dict | None = None, autosave: bool = True):
    """Grab a slot for an ad-hoc (non-task) purpose.

    Returns ``(ok, message, displaced_holder)``.
    """
    root = normalize_slot(slot)
    if not root:
        return False, f"invalid slot: {slot!r}", None
    if not (label or "").strip():
        return False, "an ad-hoc hold needs a label/purpose", None
    if tag not in ADHOC_TAGS:
        return False, f"invalid tag: {tag} (use one of {', '.join(ADHOC_TAGS)})", None
    own = d if d is not None else load()

    current = get_holder(root, own)
    displaced = None
    if current:
        if not force:
            return False, f"slot '{root}' occupied by {_holder_label(current)}", None
        displaced = current

    holder = {
        "type": "adhoc",
        "label": label.strip(),
        "tag": tag,
        "since": since or TODAY,
        "notes": [],
    }
    _set_holder(own, root, holder)
    if autosave and d is None:
        save(own)
    return True, f"{root} ← {label.strip()} ({tag})", displaced


def release(slot: str, d: dict | None = None, autosave: bool = True):
    """Free a slot. Returns ``(ok, released_holder_or_None)``."""
    root = normalize_slot(slot)
    if not root:
        return False, None
    own = d if d is not None else load()
    holder = get_holder(root, own)
    if not holder:
        return True, None
    _set_holder(own, root, None)
    if autosave and d is None:
        save(own)
    return True, holder


def release_task(task_id: str, d: dict | None = None, autosave: bool = True):
    """Release whatever slot a task holds. Returns the freed slot or ``None``."""
    own = d if d is not None else load()
    slot = slot_of_task(task_id, own)
    if not slot:
        return None
    _set_holder(own, slot, None)
    if autosave and d is None:
        save(own)
    return slot


# ----------------------------------------------------------------------------
# Formatting helpers
# ----------------------------------------------------------------------------

def normalize_or_keep(detail: str | None, root: str) -> str:
    """Detail path for a task holder: keep a sub-path, else the root slot."""
    if detail and detail.strip():
        return detail.strip().strip("/")
    return root


def _holder_label(holder: dict | None) -> str:
    if not holder:
        return "free"
    if holder.get("type") == "task":
        return f"task {holder.get('task_id')}"
    return f"{holder.get('label')} [{holder.get('tag', 'other')}]"


def holder_summary(holder: dict | None) -> str:
    if not holder:
        return "—"
    if holder.get("type") == "task":
        d = holder.get("detail")
        extra = f" ({d})" if d and d != normalize_slot(d) else ""
        return f"task {holder.get('task_id')}{extra}"
    return f"[adhoc/{holder.get('tag', 'other')}] {holder.get('label')}"


# ----------------------------------------------------------------------------
# Migration from the legacy task `workspace` field
# ----------------------------------------------------------------------------

def migrate_from_tasks(strip: bool = True) -> list[str]:
    """One-time import of legacy per-task ``workspace`` holds into this store.

    Active (non-done/non-cancelled) tasks carrying a ``workspace`` become task
    holders here (one per slot; on a root collision the most-recently-worked
    task wins). When ``strip`` is set the legacy ``workspace``/``workspace_since``
    fields are removed from the task store afterward. Returns a report log.
    """
    log: list[str] = []
    if not TASKS_STORE.exists():
        return ["no tasks.json — nothing to migrate"]
    with TASKS_STORE.open() as f:
        tdata = json.load(f)

    def _last_worked(t: dict) -> str:
        best = t.get("updated") or t.get("created") or "0000-00-00"
        for n in t.get("notes", []):
            if len(n) >= 10 and n[4] == "-" and n[7] == "-":
                best = max(best, n[:10])
        return best

    d = load()
    # Collect candidate holders grouped by root slot.
    by_slot: dict[str, list[dict]] = {}
    for t in tdata.get("tasks", []):
        ws = t.get("workspace")
        if not ws or t.get("status") in ("done", "cancelled"):
            continue
        root = normalize_slot(ws)
        if not root:
            continue
        by_slot.setdefault(root, []).append(t)

    for root, holders in sorted(by_slot.items()):
        holders.sort(key=_last_worked, reverse=True)
        winner = holders[0]
        occupy_task(root, winner["id"], detail=winner.get("workspace"),
                    since=winner.get("workspace_since"), force=True, d=d, autosave=False)
        log.append(f"{root} ← {winner['id']} ({winner.get('workspace')})")
        for loser in holders[1:]:
            log.append(f"  dropped {loser['id']} from {root} "
                       f"(was {loser.get('workspace')!r}; slot taken by {winner['id']})")

    save(d)

    if strip:
        for t in tdata.get("tasks", []):
            t.pop("workspace", None)
            t.pop("workspace_since", None)
        with TASKS_STORE.open("w") as f:
            json.dump(tdata, f, indent=2)
        log.append("stripped legacy workspace/workspace_since from tasks.json")
    return log


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def _resolve_task_title(task_id: str) -> str:
    try:
        with TASKS_STORE.open() as f:
            for t in json.load(f).get("tasks", []):
                if t.get("id") == task_id:
                    return t.get("title", "")
    except (OSError, json.JSONDecodeError):
        pass
    return ""


def _print_table(rows: list[tuple]) -> None:
    if not rows:
        return
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    for i, r in enumerate(rows):
        line = "  ".join(str(c).ljust(widths[j]) for j, c in enumerate(r))
        print(line.rstrip())
        if i == 0:
            print("  ".join("-" * w for w in widths))


def cmd_list(args: argparse.Namespace) -> int:
    d = load()
    slots = canonical_slots(args.project)
    # Include occupied slots that aren't in the canonical project dir listing.
    for s in d["slots"]:
        if s not in slots and get_holder(s, d):
            slots.append(s)
    rows = [("Slot", "Holder", "Kind", "Since", "Detail")]
    occupied = 0
    for s in slots:
        h = get_holder(s, d)
        if not h:
            rows.append((s, "—", "free", "—", "—"))
            continue
        occupied += 1
        if h.get("type") == "task":
            tid = h.get("task_id", "")
            title = _resolve_task_title(tid)
            holder = f"{tid}" + (f" · {title[:40]}" if title else "")
            detail = h.get("detail") or s
            kind = "task"
        else:
            holder = h.get("label", "")
            detail = ""
            kind = f"adhoc/{h.get('tag', 'other')}"
        rows.append((s, holder, kind, h.get("since") or "—", detail))
    _print_table(rows)
    print(f"\n{occupied} / {len(slots)} slots occupied")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    h = get_holder(args.slot)
    root = normalize_slot(args.slot)
    if not h:
        print(f"{root}: free")
        return 0
    print(f"{root}: {holder_summary(h)} (since {h.get('since') or '—'})")
    return 0


def cmd_occupy(args: argparse.Namespace) -> int:
    if args.task:
        ok, msg, displaced = occupy_task(args.slot, args.task, detail=args.detail,
                                         force=args.force)
    else:
        if not args.label:
            print("[wm] provide --task T-NNN or --label \"purpose\"", file=sys.stderr)
            return 2
        ok, msg, displaced = occupy_adhoc(args.slot, args.label, tag=args.tag,
                                          force=args.force)
    if not ok:
        print(f"[wm] {msg}. Use --force to displace.", file=sys.stderr)
        return 1
    if displaced:
        print(f"[wm] displaced {_holder_label(displaced)}")
    print(msg)
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    ok, holder = release(args.slot)
    root = normalize_slot(args.slot)
    if not ok:
        print(f"[wm] invalid slot: {args.slot}", file=sys.stderr)
        return 1
    if holder is None:
        print(f"{root} already free")
        return 0
    print(f"{root} released ({_holder_label(holder)})")
    return 0


def cmd_whereami(args: argparse.Namespace) -> int:
    slot = slot_from_cwd(args.cwd)
    if not slot:
        print("not inside a project workspace")
        return 0
    h = get_holder(slot)
    print(f"{slot}: {holder_summary(h)}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    for line in migrate_from_tasks(strip=not args.keep_fields):
        print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wm", description="workspace-manager CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("list", help="show all slots and their holders")
    a.add_argument("--project", help=f"project dir under ~/projects (default {DEFAULT_PROJECT})")
    a.set_defaults(func=cmd_list)

    a = sub.add_parser("status", help="show a single slot's holder")
    a.add_argument("slot")
    a.set_defaults(func=cmd_status)

    a = sub.add_parser("occupy", help="occupy a slot with a task or an ad-hoc purpose")
    a.add_argument("slot")
    a.add_argument("--task", help="task id (T-NNN) to hold the slot")
    a.add_argument("--label", help="ad-hoc purpose text (when not a task)")
    a.add_argument("--tag", choices=ADHOC_TAGS, default="other",
                   help="ad-hoc category (default: other)")
    a.add_argument("--detail", help="explicit workspace sub-path for a task holder")
    a.add_argument("--force", action="store_true", help="displace the current holder")
    a.set_defaults(func=cmd_occupy)

    a = sub.add_parser("release", aliases=["free"], help="free a slot")
    a.add_argument("slot")
    a.set_defaults(func=cmd_release)

    a = sub.add_parser("whereami", help="report the slot for the current (or given) dir")
    a.add_argument("--cwd", help="override the directory to resolve")
    a.set_defaults(func=cmd_whereami)

    a = sub.add_parser("migrate", help="import legacy per-task workspace holds")
    a.add_argument("--keep-fields", action="store_true",
                   help="do not strip workspace/workspace_since from tasks.json")
    a.set_defaults(func=cmd_migrate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
