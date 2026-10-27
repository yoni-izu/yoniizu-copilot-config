#!/usr/bin/env python3
"""workspace-manager CLI + library — owns workspace slot occupancy.

``~/tasks/workspaces.db`` is the single source of truth for which physical
workspace slot (``~/projects/<project>/<slot>``) is currently occupied and by
what. A slot can be held by:

  * a **task** — a logged task-manager item (``type: task``), or
  * an **ad-hoc** purpose (``type: adhoc``) — any reason you grab a workspace
    for that you don't want to log a dedicated task for (monitor PRs, debug an
    issue, run an experiment, ...).

This module is imported by task-manager's ``tm.py`` (which delegates every
workspace mutation here) and is also runnable as a standalone CLI. It carries
no task-store persistence knowledge: title lookup and legacy hold migration use
the owning task_store API.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import fcntl
import functools
import threading
import uuid
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from datetime import date
from pathlib import Path

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

import workspace_store

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "task-manager"))
import task_store

STORE = workspace_store.DB_PATH
PROJECTS_ROOT = Path(os.path.expanduser("~/projects"))
DEFAULT_PROJECT = "iridium"

ADHOC_TAGS = ["monitor", "debug", "development", "experiment", "other"]
HOLDER_TYPES = ["task", "adhoc"]

TODAY = date.today().isoformat()
_STORE_LOCK = threading.RLock()
_LOCK_STATE = threading.local()


@contextmanager
def store_lock():
    with _STORE_LOCK:
        if getattr(_LOCK_STATE, "depth", 0):
            yield
            return
        STORE.parent.mkdir(parents=True, exist_ok=True)
        with STORE.with_suffix(".lock").open("a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            _LOCK_STATE.depth = 1
            try:
                yield
            finally:
                _LOCK_STATE.depth = 0
                fcntl.flock(f, fcntl.LOCK_UN)


def serialized(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with store_lock():
            return fn(*args, **kwargs)
    return wrapped


def new_id(remote: bool = False) -> str:
    return f"WS-{'R' if remote else 'L'}-{uuid.uuid4().hex}"


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------------
# Store I/O
# ----------------------------------------------------------------------------

def _empty() -> dict:
    return {"version": 1, "slots": {}, "tracked": {}, "groups": []}


@serialized
def load() -> dict:
    workspace_store.DB_PATH = Path(STORE)
    return workspace_store.load(lambda: dict(_empty(), tracked=_seed_tracked()))


def save(d: dict) -> None:
    workspace_store.DB_PATH = Path(STORE)
    workspace_store.save(d)


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
    if ws.startswith("WS-"):
        for slot, rec in load()["tracked"].items():
            if rec.get("id") == ws:
                return slot
        return ws
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
    + [{"slot": "a1_power_on", "badge": "A1", "rev": "a1_power_on_branch",
        "label": "A1 Power-On"}]
    + [{"slot": f"a1_power_on_{n:03d}", "badge": f"A1\u00b7{n}",
        "rev": "a1_power_on_branch", "label": f"A1 Power-On {n:03d}"}
       for n in (2, 3, 4)]
)


def _seed_tracked() -> dict:
    out = {}
    for rec in _SEED_TRACKED:
        slot = rec["slot"]
        out[slot] = {
            "id": new_id(),
            "path": str(PROJECTS_ROOT / DEFAULT_PROJECT / slot),
            "label": rec.get("label") or slot,
            "badge": rec["badge"],
            "rev": rec.get("rev", "main"),
            "group": "",
            "order": len(out),
        }
    return out


@serialized
def tracked(d: dict | None = None, persist: bool = True) -> dict:
    """Roster of tracked workspaces: ``slot -> record``. Seeds on first use."""
    own = d if d is not None else load()
    if "tracked" not in own:
        own["tracked"] = _seed_tracked()
        if persist:
            save(own)
    return own["tracked"]


def roster(d: dict | None = None, *, cached_only: bool = False) -> list[dict]:
    """Tracked records; cached_only avoids even local directory existence checks."""
    own = d if d is not None else load()
    recs = tracked(own, persist=d is None)
    out = []
    for slot, rec in recs.items():
        reading = status_read(rec["id"])
        out.append({
            "slot": slot,
            "id": rec["id"],
            "target": rec.get("target") or "",
            "location": "remote" if rec.get("target") else "local",
            "status": reading,
            "path": rec.get("path") or str(PROJECTS_ROOT / DEFAULT_PROJECT / slot),
            "label": rec.get("label") or slot,
            "badge": rec.get("badge") or slot[:2].upper(),
            "rev": rec.get("rev") or "main",
            "group": rec.get("group") or "",
            "order": rec.get("order", 0),
            "exists": reading.get("exists") if cached_only or rec.get("target") else Path(os.path.expanduser(
                rec.get("path") or str(PROJECTS_ROOT / DEFAULT_PROJECT / slot))).is_dir(),
        })
    out.sort(key=lambda r: (r["order"], r["slot"]))
    return out


def slot_path(slot: str, d: dict | None = None) -> Path:
    """On-disk directory for a slot — the tracked path, else the project dir."""
    root = normalize_slot(slot) or ""
    rec = tracked(d if d is not None else load(), persist=False).get(root)
    if rec and rec.get("target"):
        raise ValueError("remote workspace paths must be accessed through mctl")
    if rec and rec.get("path"):
        return Path(os.path.expanduser(rec["path"]))
    return PROJECTS_ROOT / DEFAULT_PROJECT / root


def machine_targets() -> list[str]:
    path = Path.home() / ".mctl/targets"
    if not path.exists():
        return []
    return [parts[0] for line in path.read_text().splitlines()
            if (parts := line.split()) and not parts[0].startswith("#")]


def record(slot: str, d: dict | None = None) -> dict:
    own = d if d is not None else load()
    root = normalize_slot(slot)
    rec = tracked(own, persist=False).get(root)
    if rec is None:
        raise ValueError(f"workspace is not tracked: {slot}")
    return dict(rec, slot=root)


def _state_path(wid: str, suffix: str = ".json") -> Path:
    import re
    if not re.fullmatch(r"WS-[LR]-[0-9a-f]{32}", wid):
        raise ValueError(f"invalid workspace id: {wid}")
    return STORE.parent / "workspace-state" / (wid + suffix)


def workspace_serialized(fn):
    @functools.wraps(fn)
    def wrapped(slot, *args, **kwargs):
        wid = record(slot)["id"]
        held = getattr(_LOCK_STATE, "workspaces", set())
        if wid in held:
            return fn(slot, *args, **kwargs)
        path = _state_path(wid, ".operation.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise ValueError(f"workspace operation already running: {slot}") from e
            _LOCK_STATE.workspaces = held | {wid}
            try:
                return fn(slot, *args, **kwargs)
            finally:
                _LOCK_STATE.workspaces = held
                fcntl.flock(f, fcntl.LOCK_UN)
    return wrapped


def status_read(wid: str) -> dict:
    workspace_store.DB_PATH = Path(STORE)
    return workspace_store.status_read(wid)


@serialized
def status_write(slot: str, reading: dict) -> None:
    rec = record(slot)
    workspace_store.status_write(rec["id"], reading)


@serialized
def log_action(slot: str, action: str, outcome: str, detail: str = "") -> None:
    rec = record(slot)
    workspace_store.append_event(rec["id"], {
        "at": stamp(), "workspace_id": rec["id"],
        "action": action, "outcome": outcome, "detail": detail})


def action_log(slot: str) -> list[dict]:
    return workspace_store.events(record(slot)["id"])


@serialized
def identity(path: str, target: str = "") -> dict:
    raw = path.rstrip("/")
    if not target:
        raw = str(Path(path).expanduser().resolve())
    d = load()
    candidates = [(slot, rec) for slot, rec in tracked(d).items()
                  if (rec.get("target") or "") == target
                  and (raw == rec["path"] or raw.startswith(rec["path"].rstrip("/") + "/"))]
    if candidates:
        slot, rec = max(candidates, key=lambda item: len(item[1]["path"]))
        return dict(rec, slot=slot)
    ok, msg, slot = track(raw, target=target)
    if not ok:
        raise ValueError(msg)
    return record(slot)


@serialized
def mirror_state(target: str, path: str, source_id: str, source_path: str,
                 state: str, detail: str = "") -> dict:
    rec = identity(path, target)
    reading = status_read(rec["id"])
    reading["mirror"] = None if state == "released" else {
        "source_id": source_id, "source_path": source_path,
        "state": state, "updated": stamp(),
    }
    status_write(rec["slot"], reading)
    log_action(rec["slot"], "mirror-" + state, "ok" if state != "failed" else "error", detail)
    return rec


def _run_with_progress(args: list, timeout: int, progress) -> subprocess.CompletedProcess:
    """Run the runtime, forwarding its WS_PROGRESS stderr events to ``progress`` live."""
    proc = subprocess.Popen(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout: list[str] = []
    reader = threading.Thread(target=lambda: stdout.append(proc.stdout.read()), daemon=True)
    reader.start()
    expired: list[bool] = []
    timer = threading.Timer(timeout, lambda: (expired.append(True), proc.kill()))
    timer.start()
    stderr = []
    try:
        for line in proc.stderr:
            if line.startswith("WS_PROGRESS:"):
                try:
                    event = json.loads(line[len("WS_PROGRESS:"):])
                    if isinstance(event, dict):
                        progress(event)
                except (ValueError, KeyError, TypeError):
                    pass  # a malformed or failing progress event must not abort the operation
            else:
                stderr.append(line)
        code = proc.wait()
        reader.join()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    if expired:
        raise subprocess.TimeoutExpired(args, timeout)
    return subprocess.CompletedProcess(args, code, "".join(stdout), "".join(stderr))


@workspace_serialized
def workspace_request(slot: str, action: str = "status", *, _adopted: bool = False,
                      progress=None, **options) -> dict:
    rec = record(slot)
    request = {"id": rec["id"], "path": rec["path"], "rev": rec["rev"], "action": action}
    request.update(options)
    if action == "sync" and get_holder(slot) and not status_read(rec["id"]).get("mirror"):
        log_action(slot, action, "rejected", "workspace is occupied")
        raise ValueError("workspace is occupied; free it before syncing")
    if rec.get("target"):
        args = [str(Path.home() / "bin/mctl"), "-t", rec["target"], "workspace",
                action, rec["path"], rec["id"], rec["rev"]]
        if options:
            args.append(json.dumps(options))
    else:
        args = [sys.executable, str(Path(__file__).with_name("workspace_runtime.py")),
                json.dumps(request)]
    result = {}
    try:
        timeout = 2400 if action != "status" else 180
        r = (_run_with_progress(args, timeout, progress) if progress else
             subprocess.run(args, text=True, capture_output=True, timeout=timeout))
        if r.returncode and not r.stdout.lstrip().startswith("{"):
            raise RuntimeError((r.stderr or r.stdout).strip()[-3000:] or f"exit {r.returncode}")
        result = json.loads(r.stdout)
        if not isinstance(result, dict):
            raise ValueError("invalid workspace response: expected a JSON object")
        if r.returncode and result.get("ok") is not False:
            raise RuntimeError((r.stderr or r.stdout).strip()[-3000:] or f"exit {r.returncode}")
        if result.get("workspace_id") and result.get("path"):
            if _adopted:
                raise RuntimeError("workspace identity changed during verification; retry after investigation")
            prefix = "WS-R-" if rec.get("target") else "WS-L-"
            if not result["workspace_id"].startswith(prefix):
                raise ValueError("checkout identity belongs to a different local/remote role; "
                                 "refusing to overwrite or silently reclassify it")
            # A checkout already registered by another controller owns its ID.
            with store_lock():
                d = load()
                duplicate = next((r for key, r in d["tracked"].items()
                                  if key != rec["slot"] and r["id"] == result["workspace_id"]), None)
                if duplicate:
                    raise ValueError("this physical workspace is already tracked via "
                                     f"{duplicate.get('target') or 'local'}:{duplicate['path']}")
                prior_events = action_log(rec["slot"])
                item = d["tracked"][rec["slot"]]
                _state_path(result["workspace_id"])
                item["id"] = result["workspace_id"]
                item["path"] = result["path"]
                save(d)
                for event in prior_events:
                    workspace_store.append_event(item["id"], event)
                log_action(rec["slot"], "identity-adopt", "ok",
                           f"adopted {item['id']} from machine; prior controller history: {rec['id']}")
            return workspace_request(slot, action, _adopted=True, progress=progress, **options)
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "workspace operation failed")
        if not isinstance(result.get("status"), dict):
            raise ValueError("invalid workspace response: status reading missing")
        reading = result["status"]
        canonical = reading.get("path")
        if canonical and canonical != rec["path"]:
            with store_lock():
                d = load()
                if any(r["id"] != rec["id"] and r.get("target") == rec.get("target")
                       and r["path"] == canonical for r in d["tracked"].values()):
                    raise ValueError("this canonical machine/path is already tracked under another ID")
                d["tracked"][rec["slot"]]["path"] = canonical
                save(d)
        reading["attempted"] = stamp()
        reading["log"] = result.get("log", [])
        status_write(slot, reading)
        log_action(slot, action, "ok")
        return reading
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        reading = status_read(rec["id"])
        reading.update(error=str(e), attempted=stamp())
        if isinstance(result, dict) and "mirror" in result:
            reading["mirror"] = result["mirror"]
        status_write(slot, reading)
        log_action(slot, action, "error", str(e))
        raise ValueError(str(e)) from e


def require_clean(slot: str) -> None:
    reading = workspace_request(slot)
    if reading.get("mirror"):
        raise ValueError("workspace is a mirror; free it before assigning independent work")
    if not reading.get("clean"):
        raise ValueError("; ".join(reading.get("reasons") or ["workspace is not clean"]))


def guarded_occupancy(fn):
    @workspace_serialized
    def locked(slot, *args, **kwargs):
        if kwargs.pop("_validated", False) is not True:
            require_clean(slot)
        result = fn(slot, *args, **kwargs)
        log_action(slot, "occupy", "ok" if result[0] else "rejected", result[1])
        return result

    @functools.wraps(fn)
    def wrapped(slot, *args, **kwargs):
        try:
            return locked(slot, *args, **kwargs)
        except ValueError as e:
            if normalize_slot(slot) in tracked():
                log_action(slot, "occupy", "rejected", str(e))
            return False, str(e), None
    return wrapped


@serialized
def track(path: str, label: str | None = None, rev: str | None = None,
          group: str | None = None, badge: str | None = None,
          target: str | None = None):
    """Add a workspace directory to the tracked roster. ``(ok, msg, slot)``."""
    raw = (path or "").strip()
    if not raw:
        return False, "a workspace path is required", None
    if target and target not in machine_targets():
        return False, f"unknown machine target: {target}", None
    if target and not (raw.startswith("/") or raw.startswith("~/")):
        return False, "remote paths must be absolute or start with ~/", None
    p = Path(raw) if target else Path(os.path.expanduser(raw)).resolve()
    if not target and not p.is_dir():
        return False, f"not a directory: {p}", None
    slot = p.name
    d = load()
    recs = tracked(d, persist=False)
    if any(r.get("path") == str(p) and (r.get("target") or "") == (target or "")
           for r in recs.values()):
        return False, f"'{p}' is already tracked on {target or 'local'}", None
    if group and not get_group(group, d):
        return False, f"unknown group: {group}", None
    wid = new_id(bool(target))
    if target or slot in recs:
        slot = wid
    recs[slot] = {
        "id": wid,
        "target": target or "",
        "path": str(p),
        "label": (label or "").strip() or p.name,
        "badge": (badge or "").strip() or p.name[:2].upper(),
        "rev": (rev or "").strip() or "main",
        "group": group or "",
        "order": max((r.get("order", 0) for r in recs.values()), default=-1) + 1,
    }
    save(d)
    log_action(slot, "track", "ok", f"{target or 'local'}:{p}")
    return True, f"tracking '{slot}' ({p})", slot


@workspace_serialized
@serialized
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
    if status_read(recs[root]["id"]).get("mirror"):
        return False, "release the mirror before stopping tracking"
    log_action(root, "untrack", "ok", "directory left untouched")
    recs.pop(root)
    save(d)
    return True, f"stopped tracking '{root}'"


@workspace_serialized
@serialized
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
    if fields.get("rev") and fields["rev"] != rec.get("rev"):
        if get_holder(root, d) or status_read(rec["id"]).get("mirror"):
            log_action(root, "metadata", "rejected", "cannot change revision while occupied or mirroring")
            return False, "free the workspace before changing its revision"
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
    log_action(root, "metadata", "ok", json.dumps(fields))
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


@serialized
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


@serialized
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


@serialized
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
    own = d if d is not None else load()
    if get_holder(slot, own):
        return False
    rec = tracked(own, persist=False).get(normalize_slot(slot))
    if not rec:
        return True
    reading = status_read(rec["id"])
    if rec.get("target") and (not reading.get("checked") or reading.get("error")
                              or reading.get("exists") is False):
        return False
    return not reading.get("mirror")


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


@guarded_occupancy
@serialized
def occupy_task(slot: str, task_id: str, detail: str | None = None,
                force: bool = False, since: str | None = None,
                d: dict | None = None, autosave: bool = True):
    """Assign a task to a slot.

    Returns ``(ok, message, None)``. Both the target and any previous task hold
    must be freed before reassigning. ``force`` is retained for API compatibility.
    """
    root = normalize_slot(slot)
    if not root:
        return False, f"invalid slot: {slot!r}", None
    own = d if d is not None else load()

    current = get_holder(root, own)
    if current and not (current.get("type") == "task" and current.get("task_id") == task_id):
        return False, f"slot '{root}' occupied by {_holder_label(current)}; free it first", None
    # Preserve the acquire date if this task is simply being re-stamped / moved.
    prev_slot = slot_of_task(task_id, own)
    prev_since = None
    if prev_slot:
        prev_since = (own["slots"][prev_slot]["holder"] or {}).get("since")
        if prev_slot != root:
            return False, f"{task_id} already holds {prev_slot}; free it before moving the task", None

    displaced = None

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


@guarded_occupancy
@serialized
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
        return False, f"slot '{root}' occupied by {_holder_label(current)}; free it first", None

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


@workspace_serialized
def release(slot: str, d: dict | None = None, autosave: bool = True,
            reset: bool = True):
    """Free only after a verified baseline return/sync; retain holder on failure.

    ``reset=False`` is for a caller that has just completed the same guarded
    physical operation (the portal background worker).
    """
    root = normalize_slot(slot)
    if not root:
        return False, None
    if reset:
        workspace_request(root, "free")
    with store_lock():
        own = d if d is not None else load()
        holder = get_holder(root, own)
        _set_holder(own, root, None)
        if autosave and d is None:
            save(own)
        log_action(root, "release-hold", "ok")
    return True, holder


def release_task(task_id: str, d: dict | None = None, autosave: bool = True):
    """Release whatever slot a task holds. Returns the freed slot or ``None``."""
    own = d if d is not None else load()
    slot = slot_of_task(task_id, own)
    if not slot:
        return None
    release(slot, d=d, autosave=autosave)
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
    tdata = task_store.load()

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
        ok, message, _ = occupy_task(
            root, winner["id"], detail=winner.get("workspace"),
            since=winner.get("workspace_since"), force=True, d=d, autosave=False)
        if not ok:
            raise ValueError(f"migration stopped without stripping task fields: {message}")
        log.append(f"{root} ← {winner['id']} ({winner.get('workspace')})")
        for loser in holders[1:]:
            log.append(f"  dropped {loser['id']} from {root} "
                       f"(was {loser.get('workspace')!r}; slot taken by {winner['id']})")

    save(d)

    if strip:
        for t in tdata.get("tasks", []):
            t.pop("workspace", None)
            t.pop("workspace_since", None)
        task_store.save(tdata)
        log.append("stripped legacy workspace/workspace_since from task store")
    return log


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def _resolve_task_title(task_id: str) -> str:
    for t in task_store.load().get("tasks", []):
        if t.get("id") == task_id:
            return t.get("title", "")
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
        print(f"[wm] {msg}. Free the old hold and verify a clean baseline before retrying.", file=sys.stderr)
        return 1
    if displaced:
        print(f"[wm] displaced {_holder_label(displaced)}")
    print(msg)
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    try:
        ok, holder = release(args.slot)
    except ValueError as e:
        print(f"[wm] release rejected: {e}", file=sys.stderr)
        return 1
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

    for command in ("refresh", "sync", "log"):
        a = sub.add_parser(command)
        a.add_argument("slot")
        a.set_defaults(func=lambda args, command=command: _workspace_cli(args, command))

    a = sub.add_parser("identity", help="find or register a workspace identity")
    a.add_argument("path")
    a.add_argument("--target", default="")
    a.set_defaults(func=lambda args: _print_identity(args))

    a = sub.add_parser("mirror-state", help="record the mirror lifecycle")
    a.add_argument("target")
    a.add_argument("path")
    a.add_argument("--source-id", required=True)
    a.add_argument("--source-path", required=True)
    a.add_argument("--state", choices=["syncing", "active", "failed", "released"], required=True)
    a.add_argument("--detail", default="")
    a.set_defaults(func=lambda args: _print_mirror_state(args))

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
    a.add_argument("--force", action="store_true", help="legacy flag; held slots must still be freed first")
    a.set_defaults(func=cmd_occupy)

    a = sub.add_parser("release", aliases=["free"], help="free a slot")
    a.add_argument("slot")
    a.set_defaults(func=cmd_release)

    a = sub.add_parser("whereami", help="report the slot for the current (or given) dir")
    a.add_argument("--cwd", help="override the directory to resolve")
    a.set_defaults(func=cmd_whereami)

    a = sub.add_parser("migrate", help="import legacy per-task workspace holds")
    a.add_argument("--keep-fields", action="store_true",
                   help="do not strip workspace/workspace_since from the task store")
    a.set_defaults(func=cmd_migrate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def _print_identity(args) -> int:
    print(json.dumps(identity(args.path, args.target)))
    return 0


def _workspace_cli(args, command) -> int:
    try:
        value = action_log(args.slot) if command == "log" else workspace_request(
            args.slot, "status" if command == "refresh" else command)
        print(json.dumps(value, indent=2))
        return 0
    except ValueError as e:
        print(f"[wm] {e}", file=sys.stderr)
        return 1


def _print_mirror_state(args) -> int:
    print(json.dumps(mirror_state(args.target, args.path, args.source_id,
                                 args.source_path, args.state, args.detail)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
