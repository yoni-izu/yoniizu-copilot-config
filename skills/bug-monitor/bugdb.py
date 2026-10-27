#!/usr/bin/env python3
"""bugdb — the single source of truth for bug data.

One SQLite file, ``~/tasks/bugs.db`` (override with ``BUGS_DB``), shared by:

* ``bug-monitor/bm.py``  — the Jira mirror, triage layer and duplicate families
* ``bug-triage/ph.py``   — the cross-platform phenomenon registry
* ``task-manager/tm.py`` — the Team Portal's Bugs section (reads *and* writes)

Nobody else opens the file. ``bm.load()``/``ph.load()`` still hand their callers
the same dict shapes the old ``bugs.json``/``phenomena.json`` had, so the command
logic is unchanged; what changed is how those dicts are persisted:

* **Row-level saves with a 3-way merge.** ``load_*`` snapshots every row it
  returns. ``save_*`` writes only rows that differ from that snapshot, and when
  another writer changed the same row in the meantime it merges field by field
  (append-only lists such as notes are concatenated) instead of overwriting.
* **Transactions.** ``with tx(actor):`` takes the SQLite write lock for a whole
  read-modify-write, so a CLI command and a portal edit can never interleave.
  WAL mode keeps readers unblocked.
* **Links live in exactly one place.** A phenomenon's Jira keys are rows in
  ``bug_links``; investigation notes linked to a bug or a phenomenon are rows in
  ``inv_links``. The legacy ``ph["jira"]``, ``ph["investigations"]`` and
  ``bug["triage"]["investigation"]`` fields are assembled from those tables on
  load and decomposed back into them on save.
* **Audit.** Every write lands in ``audit`` with the actor (``cli`` or
  ``portal``), the entity and the fields that changed.

Investigation notes stay Markdown files under
``~/.copilot-private/skills/bug-triage/investigations/``; the database only records which
bug/phenomenon each note belongs to. Weaker links — a Jira key in a note's file
name, or merely mentioned in its body — are derived at read time and never stored.

CLI: ``bugdb.py status | migrate | export [-o FILE] | audit [-n N]``.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(os.path.expanduser(os.environ.get("BUGS_DB", "~/tasks/bugs.db")))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from private_paths import private_root  # noqa: E402

INV_DIR = Path(os.path.expanduser(os.environ.get(
    "BUG_TRIAGE_INVESTIGATIONS",
    str(private_root() / "skills/bug-triage/investigations"))))
LEGACY_BUGS = Path(os.path.expanduser(os.environ.get(
    "BUGS_LEGACY_JSON", "~/tasks/bugs.json")))
LEGACY_PH = Path(os.path.expanduser(os.environ.get(
    "PH_STORE", "~/tasks/phenomena.json")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bugs (
    key     TEXT PRIMARY KEY,
    status  TEXT,
    is_open INTEGER,
    updated TEXT,
    data    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS families (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phenomena (
    id       TEXT PRIMARY KEY,
    status   TEXT,
    severity TEXT,
    owner    TEXT,
    updated  TEXT,
    data     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bug_links (
    ph_id   TEXT NOT NULL,
    bug_key TEXT NOT NULL,
    created TEXT,
    actor   TEXT,
    PRIMARY KEY (ph_id, bug_key)
);
CREATE INDEX IF NOT EXISTS bug_links_by_key ON bug_links (bug_key);
CREATE TABLE IF NOT EXISTS inv_links (
    note    TEXT NOT NULL,
    kind    TEXT NOT NULL CHECK (kind IN ('bug', 'ph')),
    target  TEXT NOT NULL,
    created TEXT,
    actor   TEXT,
    PRIMARY KEY (note, kind, target)
);
CREATE INDEX IF NOT EXISTS inv_links_by_target ON inv_links (kind, target);
CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    actor     TEXT,
    entity    TEXT,
    entity_id TEXT,
    action    TEXT,
    detail    TEXT
);
"""

BM_META = ("version", "last_scan", "config", "next_family_id")
PH_META = ("version", "config", "next_id", "next_sighting")

KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,15})-(\d{1,7})\b")
PH_RE = re.compile(r"\bPH-\d{3,}\b")
_MISSING = object()
_local = threading.local()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


class Store(dict):
    """A loaded store. ``_snap`` remembers what each row looked like on load."""


# ----------------------------------------------------------------------------
# Connection / transactions
# ----------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is not None and getattr(_local, "path", None) == DB_PATH:
        return c
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None,
                        check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    c.executescript(SCHEMA)
    _local.conn, _local.path, _local.depth = c, DB_PATH, 0
    _migrate_legacy(c)
    return c


def actor() -> str:
    return getattr(_local, "actor", None) or os.environ.get("BUGS_DB_ACTOR") or "cli"


@contextmanager
def tx(who: str | None = None) -> Iterator[sqlite3.Connection]:
    """Hold the write lock across a load → mutate → save. Nests safely."""
    c = _conn()
    prev = getattr(_local, "actor", None)
    if who:
        _local.actor = who
    if _local.depth == 0:
        c.execute("BEGIN IMMEDIATE")
    _local.depth += 1
    ok = False
    try:
        yield c
        ok = True
    finally:
        _local.depth -= 1
        if _local.depth == 0:
            c.execute("COMMIT" if ok else "ROLLBACK")
        _local.actor = prev


def _audit(c: sqlite3.Connection, entity: str, eid: str, action: str,
           detail: str = "") -> None:
    c.execute("INSERT INTO audit (at, actor, entity, entity_id, action, detail) "
              "VALUES (?, ?, ?, ?, ?, ?)", (now(), actor(), entity, eid, action,
                                            detail[:500]))


# ----------------------------------------------------------------------------
# Merge
# ----------------------------------------------------------------------------

def merge3(base: Any, ours: Any, theirs: Any) -> Any:
    """Three-way merge of JSON values. Ours wins a true conflict; append-only
    lists keep both sides' new tail; counters (``next_*``) take the max."""
    if ours == base:
        return theirs
    if theirs == base or theirs == ours:
        return ours
    if isinstance(base, dict) and isinstance(ours, dict) and isinstance(theirs, dict):
        out: dict[str, Any] = {}
        for k in list(ours) + [k for k in theirs if k not in ours]:
            r = merge3(base.get(k, _MISSING), ours.get(k, _MISSING),
                       theirs.get(k, _MISSING))
            if r is not _MISSING:
                out[k] = r
        return out
    if (isinstance(base, list) and isinstance(ours, list) and isinstance(theirs, list)
            and ours[:len(base)] == base and theirs[:len(base)] == base):
        return theirs + ours[len(base):]
    return ours


def _changed_keys(old: dict | None, new: dict) -> str:
    if not old:
        return ""
    keys = [k for k in set(old) | set(new) if old.get(k) != new.get(k)]
    return ", ".join(sorted(keys))


def _save_rows(c: sqlite3.Connection, table: str, pk: str, entity: str,
               rows: dict[str, dict], snap: dict[str, str],
               cols: dict[str, Any] | None = None) -> None:
    """Persist ``rows`` (id → obj) against the load-time ``snap`` (id → json)."""
    extra = cols or {}
    db = {r[0]: r[1] for r in c.execute(f"SELECT {pk}, data FROM {table}")}
    for rid, obj in rows.items():
        js = _dump(obj)
        base = snap.get(rid)
        if base == js:
            continue
        cur = db.get(rid)
        note = ""
        if cur is not None and base is not None and cur != base:
            merged = merge3(json.loads(base), obj, json.loads(cur))
            if isinstance(merged, dict) and merged is not obj:
                obj.clear()
                obj.update(merged)
            js = _dump(obj)
            note = " (merged with a concurrent write)"
        if cur == js:
            snap[rid] = js
            continue
        names = ["data"] + list(extra)
        vals = [js] + [fn(obj) for fn in extra.values()]
        c.execute(f"INSERT INTO {table} ({pk}, {', '.join(names)}) "
                  f"VALUES (?{', ?' * len(names)}) ON CONFLICT({pk}) DO UPDATE SET "
                  + ", ".join(f"{n}=excluded.{n}" for n in names), [rid] + vals)
        _audit(c, entity, rid, "update" if cur is not None else "insert",
               _changed_keys(json.loads(cur) if cur else None, obj) + note)
        snap[rid] = js
    for rid in [r for r in snap if r not in rows]:
        if rid in db:
            c.execute(f"DELETE FROM {table} WHERE {pk} = ?", (rid,))
            _audit(c, entity, rid, "delete")
        del snap[rid]


def _save_meta(c: sqlite3.Connection, prefix: str, d: dict, keys: tuple,
               snap: dict[str, str]) -> None:
    for k in keys:
        if k not in d:
            continue
        mk = f"{prefix}.{k}"
        js = _dump(d[k])
        if snap.get(mk) == js:
            continue
        row = c.execute("SELECT value FROM meta WHERE key = ?", (mk,)).fetchone()
        val = d[k]
        if row and mk in snap and row[0] != snap[mk]:
            theirs = json.loads(row[0])
            if k.startswith("next_") and isinstance(theirs, int):
                val = max(int(val), theirs)
            else:
                val = merge3(json.loads(snap[mk]), val, theirs)
            d[k] = val
            js = _dump(val)
        c.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) "
                  "DO UPDATE SET value = excluded.value", (mk, js))
        snap[mk] = js


def _meta(c: sqlite3.Connection, prefix: str, keys: tuple) -> dict[str, Any]:
    out = {}
    for k in keys:
        row = c.execute("SELECT value FROM meta WHERE key = ?",
                        (f"{prefix}.{k}",)).fetchone()
        if row:
            out[k] = json.loads(row[0])
    return out


# ----------------------------------------------------------------------------
# Investigation-note naming
# ----------------------------------------------------------------------------

def note_name(path_or_name: str) -> str:
    """The key a note is stored under: its file name when it lives in the
    investigations dir, otherwise the path as given."""
    p = Path(os.path.expanduser(str(path_or_name)))
    if p.name == str(path_or_name) or p.parent.resolve() == INV_DIR.resolve():
        return p.name
    return str(path_or_name)


def note_path(name: str) -> str:
    return str(INV_DIR / name) if "/" not in name else name


# ----------------------------------------------------------------------------
# bug-monitor store
# ----------------------------------------------------------------------------

def _bug_open(b: dict) -> int:
    return int(b.get("status_category") != "done")


def load_bugs() -> Store:
    c = _conn()
    d = Store(_meta(c, "bm", BM_META))
    snap: dict[str, dict[str, str]] = {"meta": {}, "bugs": {}, "families": {}}
    for k, v in d.items():
        snap["meta"][f"bm.{k}"] = _dump(v)
    notes: dict[str, list[str]] = {}
    for note, target in c.execute("SELECT note, target FROM inv_links WHERE kind='bug' "
                                  "ORDER BY created, rowid"):
        notes.setdefault(target, []).append(note)
    d["bugs"] = []
    for key, data in c.execute("SELECT key, data FROM bugs ORDER BY rowid"):
        snap["bugs"][key] = data
        b = json.loads(data)
        tri = b.setdefault("triage", {})
        linked = notes.get(key) or []
        tri["investigation"] = note_path(linked[-1]) if linked else None
        d["bugs"].append(b)
    d["families"] = []
    for fid, data in c.execute("SELECT id, data FROM families ORDER BY rowid"):
        snap["families"][fid] = data
        d["families"].append(json.loads(data))
    snap["inv"] = {b["key"]: (b["triage"] or {}).get("investigation") for b in d["bugs"]}
    d._snap = snap  # type: ignore[attr-defined]
    return d


def _strip_bug(b: dict) -> dict:
    out = dict(b)
    tri = dict(out.get("triage") or {})
    tri.pop("investigation", None)
    out["triage"] = tri
    return out


def save_bugs(d: dict) -> None:
    snap = getattr(d, "_snap", None) or {"meta": {}, "bugs": {}, "families": {},
                                         "inv": {}}
    with tx() as c:
        _save_meta(c, "bm", d, BM_META, snap["meta"])
        stripped = {b["key"]: _strip_bug(b) for b in d.get("bugs", [])}
        _save_rows(c, "bugs", "key", "bug", stripped, snap["bugs"], {
            "status": lambda b: b.get("status"),
            "is_open": _bug_open,
            "updated": lambda b: b.get("updated"),
        })
        # A merge rewrote the stripped copy in place; push it back into the bug.
        for b in d.get("bugs", []):
            inv = (b.get("triage") or {}).get("investigation")
            s = stripped[b["key"]]
            if s is not b:
                b.update({k: v for k, v in s.items() if k != "triage"})
                b["triage"] = dict(s.get("triage") or {}, investigation=inv)
        _save_rows(c, "families", "id", "family",
                   {f["id"]: f for f in d.get("families", [])}, snap["families"])
        for b in d.get("bugs", []):
            key = b["key"]
            new = (b.get("triage") or {}).get("investigation")
            old = snap["inv"].get(key)
            if new == old:
                continue
            if old and not new:
                c.execute("DELETE FROM inv_links WHERE kind='bug' AND target=? AND note=?",
                          (key, note_name(old)))
                _audit(c, "bug", key, "unlink-note", note_name(old))
            if new:
                _link_note(c, note_name(new), "bug", key)
            snap["inv"][key] = new
    d._snap = snap  # type: ignore[attr-defined]


# ----------------------------------------------------------------------------
# bug-triage (phenomenon) store
# ----------------------------------------------------------------------------

def load_ph() -> Store:
    c = _conn()
    store = Store(_meta(c, "ph", PH_META))
    snap: dict[str, Any] = {"meta": {f"ph.{k}": _dump(v) for k, v in store.items()},
                            "ph": {}, "jira": {}, "inv": {}}
    jira: dict[str, list[str]] = {}
    for pid, key in c.execute("SELECT ph_id, bug_key FROM bug_links ORDER BY created, rowid"):
        jira.setdefault(pid, []).append(key)
    invs: dict[str, list[str]] = {}
    for note, target in c.execute("SELECT note, target FROM inv_links WHERE kind='ph' "
                                  "ORDER BY created, rowid"):
        invs.setdefault(target, []).append(note_path(note))
    store["phenomena"] = []
    for pid, data in c.execute("SELECT id, data FROM phenomena ORDER BY rowid"):
        snap["ph"][pid] = data
        ph = json.loads(data)
        ph["jira"] = list(jira.get(pid, []))
        ph["investigations"] = list(invs.get(pid, []))
        snap["jira"][pid] = list(ph["jira"])
        snap["inv"][pid] = list(ph["investigations"])
        store["phenomena"].append(ph)
    store._snap = snap  # type: ignore[attr-defined]
    return store


def _strip_ph(ph: dict) -> dict:
    return {k: v for k, v in ph.items() if k not in ("jira", "investigations")}


def save_ph(store: dict) -> None:
    snap = getattr(store, "_snap", None) or {"meta": {}, "ph": {}, "jira": {}, "inv": {}}
    with tx() as c:
        _save_meta(c, "ph", store, PH_META, snap["meta"])
        _save_rows(c, "phenomena", "id", "phenomenon",
                   {p["id"]: _strip_ph(p) for p in store.get("phenomena", [])},
                   snap["ph"], {
                       "status": lambda p: p.get("status"),
                       "severity": lambda p: p.get("severity"),
                       "owner": lambda p: p.get("owner"),
                       "updated": lambda p: p.get("updated"),
                   })
        for ph in store.get("phenomena", []):
            pid = ph["id"]
            new_j = [k.strip().upper() for k in ph.get("jira") or [] if k and k.strip()]
            old_j = snap["jira"].get(pid, [])
            for k in new_j:
                if k not in old_j:
                    _link_bug(c, pid, k)
            for k in old_j:
                if k not in new_j:
                    c.execute("DELETE FROM bug_links WHERE ph_id=? AND bug_key=?", (pid, k))
                    _audit(c, "phenomenon", pid, "unlink-bug", k)
            snap["jira"][pid] = new_j
            new_i = [note_name(p) for p in ph.get("investigations") or []]
            old_i = [note_name(p) for p in snap["inv"].get(pid, [])]
            for n in new_i:
                if n not in old_i:
                    _link_note(c, n, "ph", pid)
            for n in old_i:
                if n not in new_i:
                    c.execute("DELETE FROM inv_links WHERE kind='ph' AND target=? AND note=?",
                              (pid, n))
                    _audit(c, "phenomenon", pid, "unlink-note", n)
            snap["inv"][pid] = [note_path(n) for n in new_i]
    store._snap = snap  # type: ignore[attr-defined]


def _link_bug(c: sqlite3.Connection, pid: str, key: str) -> None:
    if c.execute("INSERT OR IGNORE INTO bug_links (ph_id, bug_key, created, actor) "
                 "VALUES (?, ?, ?, ?)", (pid, key, now(), actor())).rowcount:
        _audit(c, "phenomenon", pid, "link-bug", key)


def _link_note(c: sqlite3.Connection, note: str, kind: str, target: str) -> None:
    if c.execute("INSERT OR IGNORE INTO inv_links (note, kind, target, created, actor) "
                 "VALUES (?, ?, ?, ?, ?)", (note, kind, target, now(), actor())).rowcount:
        _audit(c, "bug" if kind == "bug" else "phenomenon", target, "link-note", note)


# ----------------------------------------------------------------------------
# Investigation notes: index, viewer, evidence map
# ----------------------------------------------------------------------------

_INDEX_CACHE: dict[str, Any] = {"sig": None, "rows": []}


def _jira_prefixes(c: sqlite3.Connection) -> set[str]:
    pre = {r[0].split("-")[0] for r in c.execute("SELECT key FROM bugs")}
    pre |= {r[0].split("-")[0] for r in c.execute("SELECT bug_key FROM bug_links")}
    return pre


def _is_jira(prefix: str, known: set[str]) -> bool:
    return prefix != "PH" and (prefix in known or prefix.endswith("SW"))


def investigation_index() -> list[dict[str, Any]]:
    """Every note in the investigations dir with its title, date, and the Jira
    keys / phenomenon ids it names. Cached on the directory's mtimes."""
    if not INV_DIR.is_dir():
        return []
    files = sorted(p for p in INV_DIR.glob("*.md") if p.name.lower() != "readme.md")
    sig = tuple((p.name, p.stat().st_mtime) for p in files)
    if sig == _INDEX_CACHE["sig"]:
        return _INDEX_CACHE["rows"]
    known = _jira_prefixes(_conn())
    rows = []
    for p in files:
        text = p.read_text(errors="replace")
        title = next((ln.lstrip("#").strip() for ln in text.splitlines()
                      if ln.startswith("# ")), p.stem)
        m = re.search(r"(\d{4})(\d{2})(\d{2})(?:-(\d{2})(\d{2}))?", p.stem)
        when = (f"{m[1]}-{m[2]}-{m[3]}" + (f" {m[4]}:{m[5]}" if m[4] else "")) if m else \
            datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        keys: list[str] = []
        for pre, num in KEY_RE.findall(text):
            k = f"{pre}-{num}"
            if _is_jira(pre, known) and k not in keys:
                keys.append(k)
        fm = KEY_RE.match(p.stem)
        file_key = f"{fm[1]}-{fm[2]}" if fm and _is_jira(fm[1], known) else None
        fp = PH_RE.match(p.stem)
        rows.append({
            "name": p.name, "title": title, "date": when, "size": p.stat().st_size,
            "keys": keys, "file_key": file_key,
            "file_ph": fp[0] if fp else None,
            "phs": list(dict.fromkeys(PH_RE.findall(text))),
        })
    rows.sort(key=lambda r: r["date"], reverse=True)
    _INDEX_CACHE.update(sig=sig, rows=rows)
    return rows


def read_investigation(name: str) -> str | None:
    """Return a note's Markdown, or None. Accepts a bare file name only."""
    if not name or name != Path(name).name or not name.endswith(".md"):
        return None
    p = (INV_DIR / name).resolve()
    if p.parent != INV_DIR.resolve() or not p.is_file():
        return None
    return p.read_text(errors="replace")


RANK = {"explicit": 0, "filename": 1, "mention": 2}

# Who raised it. The `aqua-external` GitHub org is the customer's own tracker,
# so any reference into it settles the question; otherwise we default to
# internal. Explicit overrides (a phenomenon tag / a bug triage field) win.
ORIGINS = ("customer", "internal")
_AQUA_RE = re.compile(
    r"aqua-external/([\w.-]+)(?:/(?:issues|pull|discussions)/(\d+))?", re.I)


def aqua_ref(text: str) -> str | None:
    m = _AQUA_RE.search(text or "")
    if not m:
        return None
    return f"aqua-external/{m[1]}" + (f"#{m[2]}" if m[2] else "")


def ph_origin(ph: dict[str, Any]) -> tuple[str, str]:
    """(origin, why) for a phenomenon."""
    tags = {str(t).lower() for t in ph.get("tags") or []}
    for o in ORIGINS:
        if o in tags:
            return o, f"tagged {o}"
    ref = aqua_ref(json.dumps(ph, ensure_ascii=False))
    if ref:
        return "customer", f"{ref} (customer git)"
    src = next((x.get("source") for x in ph.get("sightings") or []
                if x.get("source") in ("customer", "email")), None)
    if src:
        return "customer", f"sighting reported via {src}"
    return "internal", "no customer evidence"


def bug_origin(bug: dict[str, Any], phs: list[dict[str, Any]]) -> tuple[str, str]:
    """(origin, why) for a Jira bug: manual call > own text > linked phenomena."""
    manual = (bug.get("triage") or {}).get("origin")
    if manual in ORIGINS:
        return manual, "set manually"
    labels = {str(x).lower() for x in bug.get("labels") or []}
    if labels & {"customer", "customer-reported", "customer_bug"}:
        return "customer", "Jira label"
    text = json.dumps({k: bug.get(k) for k in
                       ("summary", "description", "links", "notes", "ci_runs")},
                      ensure_ascii=False, default=str)
    ref = aqua_ref(text)
    if ref:
        return "customer", f"{ref} (customer git)"
    for p in phs:
        if p.get("origin") == "customer":
            return "customer", f"via {p['id']}: {p.get('origin_why', '')}".rstrip(": ")
    return "internal", "no customer evidence"


def evidence() -> dict[str, Any]:
    """Who is linked to what, with how sure we are.

    ``bugs[key]``  → {"phenomena": [{id, source}], "investigations": [{name, source}]}
    ``phenomena[id]`` → {"bugs": [key], "investigations": [{name, source}]}
    Sources, strongest first: explicit (a stored link) > filename (the key/id
    opens the note's file name) > mention (named somewhere in the note body).
    """
    c = _conn()
    bugs: dict[str, dict[str, list]] = {}
    phs: dict[str, dict[str, list]] = {}

    def add(table: dict, owner: str, field: str, item: dict, ident: str) -> None:
        slot = table.setdefault(owner, {"phenomena": [], "investigations": [], "bugs": []})
        lst = slot[field]
        old = next((x for x in lst if x[ident] == item[ident]), None)
        if old is None:
            lst.append(item)
        elif RANK.get(item.get("source"), 9) < RANK.get(old.get("source"), 9):
            old.update(item)

    for pid, key in c.execute("SELECT ph_id, bug_key FROM bug_links"):
        add(bugs, key, "phenomena", {"id": pid, "source": "explicit"}, "id")
        add(phs, pid, "bugs", {"key": key, "source": "explicit"}, "key")
    for note, kind, target in c.execute("SELECT note, kind, target FROM inv_links"):
        add(bugs if kind == "bug" else phs, target, "investigations",
            {"name": note, "source": "explicit"}, "name")
    for n in investigation_index():
        if n["file_key"]:
            add(bugs, n["file_key"], "investigations",
                {"name": n["name"], "source": "filename"}, "name")
        for k in n["keys"]:
            add(bugs, k, "investigations", {"name": n["name"], "source": "mention"}, "name")
        if n["file_ph"]:
            add(phs, n["file_ph"], "investigations",
                {"name": n["name"], "source": "filename"}, "name")
        for p in n["phs"]:
            add(phs, p, "investigations", {"name": n["name"], "source": "mention"}, "name")
    for table in (bugs, phs):
        for slot in table.values():
            for lst in slot.values():
                lst.sort(key=lambda x: RANK.get(x.get("source"), 9))
    return {"bugs": bugs, "phenomena": phs}


def ph_brief() -> dict[str, dict[str, Any]]:
    """id → {title, status, severity, owner, root_cause, origin, origin_why}."""
    out = {}
    for pid, data in _conn().execute("SELECT id, data FROM phenomena"):
        ph = json.loads(data)
        out[pid] = {k: ph.get(k) for k in
                    ("title", "status", "severity", "owner", "root_cause")}
        out[pid]["origin"], out[pid]["origin_why"] = ph_origin(ph)
    return out


def bug_evidence() -> dict[str, dict[str, Any]]:
    """bug key → {phenomena: [{id, source, title, status, severity, root_cause}],
    investigations: [{name, source}], root_cause_from: PH-id | None}.

    A bug linked to a phenomenon that carries a root cause takes it from there:
    the phenomenon is the root cause's single home."""
    ev = evidence()["bugs"]
    brief = ph_brief()
    out = {}
    for key, slot in ev.items():
        phs = [dict(x, **brief.get(x["id"], {})) for x in slot["phenomena"]]
        src = next((x["id"] for x in phs if x.get("root_cause")), None)
        out[key] = {"phenomena": phs, "investigations": slot["investigations"],
                    "root_cause_from": src}
    return out


def recent_audit(limit: int = 50, entity_id: str | None = None) -> list[dict]:
    c = _conn()
    q = "SELECT at, actor, entity, entity_id, action, detail FROM audit"
    args: list[Any] = []
    if entity_id:
        q += " WHERE entity_id = ?"
        args.append(entity_id)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    cols = ("at", "actor", "entity", "entity_id", "action", "detail")
    return [dict(zip(cols, r)) for r in c.execute(q, args)]


# ----------------------------------------------------------------------------
# One-time import of the legacy JSON stores
# ----------------------------------------------------------------------------

def _migrate_legacy(c: sqlite3.Connection) -> None:
    """Import bugs.json / phenomena.json once, then rename them *.migrated."""
    for prefix, path, importer in (("bm", LEGACY_BUGS, _import_bugs),
                                   ("ph", LEGACY_PH, _import_ph)):
        if not path.exists():
            continue
        if c.execute("SELECT 1 FROM meta WHERE key = ?", (f"{prefix}.migrated",)).fetchone():
            continue
        data = json.loads(path.read_text())
        c.execute("BEGIN IMMEDIATE")
        try:
            _local.actor = "migration"
            importer(c, data)
            c.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                      (f"{prefix}.migrated", _dump({"at": now(), "from": str(path)})))
            _audit(c, "store", prefix, "migrate", str(path))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            _local.actor = None
        path.rename(path.with_name(path.name + ".migrated"))


def _import_bugs(c: sqlite3.Connection, d: dict) -> None:
    for k in BM_META:
        if k in d:
            c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"bm.{k}", _dump(d[k])))
    for b in d.get("bugs", []):
        inv = (b.get("triage") or {}).get("investigation")
        s = _strip_bug(b)
        c.execute("INSERT OR REPLACE INTO bugs (key, status, is_open, updated, data) "
                  "VALUES (?, ?, ?, ?, ?)", (b["key"], b.get("status"), _bug_open(b),
                                             b.get("updated"), _dump(s)))
        if inv:
            _link_note(c, note_name(inv), "bug", b["key"])
    for f in d.get("families", []):
        c.execute("INSERT OR REPLACE INTO families (id, data) VALUES (?, ?)",
                  (f["id"], _dump(f)))


def _import_ph(c: sqlite3.Connection, d: dict) -> None:
    for k in PH_META:
        if k in d:
            c.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"ph.{k}", _dump(d[k])))
    for ph in d.get("phenomena", []):
        c.execute("INSERT OR REPLACE INTO phenomena (id, status, severity, owner, updated, "
                  "data) VALUES (?, ?, ?, ?, ?, ?)",
                  (ph["id"], ph.get("status"), ph.get("severity"), ph.get("owner"),
                   ph.get("updated"), _dump(_strip_ph(ph))))
        for k in ph.get("jira") or []:
            _link_bug(c, ph["id"], k.strip().upper())
        for p in ph.get("investigations") or []:
            _link_note(c, note_name(p), "ph", ph["id"])


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def export() -> dict[str, Any]:
    """A readable JSON dump of the whole database (for diffing / backups)."""
    bm_store = load_bugs()
    ph_store = load_ph()
    c = _conn()
    return {
        "exported": now(),
        "db": str(DB_PATH),
        "bug_monitor": {k: v for k, v in bm_store.items()},
        "bug_triage": {k: v for k, v in ph_store.items()},
        "bug_links": [dict(zip(("ph_id", "bug_key", "created", "actor"), r))
                      for r in c.execute("SELECT ph_id, bug_key, created, actor "
                                         "FROM bug_links ORDER BY ph_id, bug_key")],
        "inv_links": [dict(zip(("note", "kind", "target", "created", "actor"), r))
                      for r in c.execute("SELECT note, kind, target, created, actor "
                                         "FROM inv_links ORDER BY kind, target, note")],
    }


def cmd_status(_args: argparse.Namespace) -> int:
    c = _conn()
    print(f"db: {DB_PATH}  ({DB_PATH.stat().st_size // 1024} KB)")
    for t in ("bugs", "families", "phenomena", "bug_links", "inv_links", "audit"):
        print(f"  {t:<10} {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    for k in ("bm.migrated", "ph.migrated", "bm.last_scan"):
        row = c.execute("SELECT value FROM meta WHERE key = ?", (k,)).fetchone()
        print(f"  {k:<14} {json.loads(row[0]) if row else '-'}")
    print(f"  notes dir  {INV_DIR}  ({len(investigation_index())} notes)")
    return 0


def cmd_migrate(_args: argparse.Namespace) -> int:
    _conn()
    return cmd_status(_args)


def cmd_export(args: argparse.Namespace) -> int:
    text = json.dumps(export(), indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(os.path.expanduser(args.output)).write_text(text)
        print(f"exported → {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    for r in reversed(recent_audit(args.n, args.id)):
        print(f"{r['at']}  {r['actor']:<9} {r['entity']:<10} {r['entity_id']:<18} "
              f"{r['action']:<12} {r['detail'] or ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bugdb.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="tables, counts, migration state").set_defaults(func=cmd_status)
    sub.add_parser("migrate", help="import legacy bugs.json / phenomena.json "
                   "(also happens automatically on first open)").set_defaults(func=cmd_migrate)
    s = sub.add_parser("export", help="dump the whole database as JSON")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_export)
    s = sub.add_parser("audit", help="recent writes, oldest first")
    s.add_argument("-n", type=int, default=30)
    s.add_argument("--id", help="only this bug key / phenomenon id")
    s.set_defaults(func=cmd_audit)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
