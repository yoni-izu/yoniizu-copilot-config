#!/usr/bin/env python3
"""vd.py — delegation history for `mctl`, for the Team Portal's VM tab.

`mctl` dispatches detached build/test jobs to a target machine and keeps, on
that machine, exactly ONE log + pid pair per (workspace-tag, kind). The next run
of the same kind overwrites it, so the remote holds *current* state and no
history at all.

This module adds the missing half:

  ~/.mctl/joblog.jsonl   append-only, one line per dispatch, written by `mctl`
  ~/.mctl/jobstate.json  resolved status per job id, written by refresh()

`refresh()` runs `mctl -t <target> jobs-probe` — ONE ssh round trip per target,
no model involved — and binds each probe row to the NEWEST logged job for that
(target, tag, kind). Older logged jobs for the same slot can never be resolved
again (their log is gone), so they settle as ``superseded``.

Every entry point that walks the fleet takes an optional target name, so a
single machine can be refreshed on its own. That matters because one unreachable
target costs a 90 s ssh timeout: asking only the VM returns in a second, while a
fleet refresh waits for every dead lab desktop.

    vd.py list                 history + resolved status
    vd.py targets              per-target freshness, job counts and last error
    vd.py refresh [target]     probe + mirror logs (one target, or all)
    vd.py sync    [target]     mirror logs only
    vd.py log|tail <target> <tag> [kind]
    vd.py vms | start <id> | stop <id>
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MCTL_DIR = Path(os.path.expanduser("~/.mctl"))
JOBLOG = MCTL_DIR / "joblog.jsonl"
JOBSTATE = MCTL_DIR / "jobstate.json"
VMSTATE = MCTL_DIR / "vmstate.json"
TARGETS = MCTL_DIR / "targets"
MCTL = Path(os.path.expanduser("~/bin/mctl"))
PROJECTS = Path(os.path.expanduser("~/projects"))

PROBE_TIMEOUT = 90          # seconds per target; a dead box must not wedge the UI
OCTL_TIMEOUT = 120          # `octl vm` revalidates SSO first, so it is not fast
SYNC_TIMEOUT = 180          # rsync of the whole job-log tree
TAIL_TIMEOUT = 25           # one `tail -n` over ssh; the UI polls on a timer
LOG_MAX_BYTES = 512 * 1024  # how much of a stored log the portal will render

# Terminal = the outcome is final and no further probe can change it.
TERMINAL = {"passed", "failed", "died", "superseded", "gone"}


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------
def _read_log() -> list[dict]:
    if not JOBLOG.exists():
        return []
    out = []
    for line in JOBLOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("id"):
            out.append(rec)
    return out


def _read_state() -> dict:
    if not JOBSTATE.exists():
        return {"jobs": {}, "last_refresh": "", "errors": {}}
    try:
        d = json.loads(JOBSTATE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"jobs": {}, "last_refresh": "", "errors": {}}
    d.setdefault("jobs", {})
    d.setdefault("last_refresh", "")
    d.setdefault("errors", {})
    return d


def _write_state(state: dict) -> None:
    MCTL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = JOBSTATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(JOBSTATE)


def _append_log(rec: dict) -> None:
    MCTL_DIR.mkdir(parents=True, exist_ok=True)
    with JOBLOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def targets() -> list[str]:
    """Target names from the mctl registry."""
    if not TARGETS.exists():
        return []
    names = []
    for line in TARGETS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(line.split()[0])
    return names


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _tag(ws: str) -> str:
    """mctl's workspace tag: path relative to $HOME with '/' -> '_'."""
    home = os.path.expanduser("~")
    p = ws[len(home) + 1:] if ws.startswith(home + "/") else ws
    return p.replace("/", "_")


def _tag_to_ws() -> dict[str, str]:
    """Reverse the tag for jobs discovered on the target but never logged here.

    The tag is lossy (workspace names contain '_'), so it is reversed by
    tagging the directories that actually exist rather than by string surgery.
    """
    out: dict[str, str] = {}
    if not PROJECTS.is_dir():
        return out
    for proj in PROJECTS.iterdir():
        if not proj.is_dir():
            continue
        for slot in proj.iterdir():
            if not slot.is_dir():
                continue
            for cand in (slot, slot / "fw"):
                if cand.is_dir():
                    out[_tag(str(cand))] = str(cand)
    return out


def _epoch(iso: str) -> float:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(iso, fmt).timestamp()
        except (ValueError, TypeError):
            continue
    return 0.0


def _human_dur(secs: float) -> str:
    if secs <= 0:
        return ""
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _status_of(state: str, rc) -> str:
    if state == "RUNNING":
        return "running"
    if state == "DONE":
        return "passed" if str(rc) == "0" else "failed"
    if state == "DIED":
        return "died"
    return "gone"


# --------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------
def probe(target: str) -> tuple[dict, str]:
    """One ssh round trip. Returns ({(tag,kind): row}, error-or-empty)."""
    if not MCTL.exists():
        return {}, "mctl not installed"
    try:
        p = subprocess.run([str(MCTL), "-t", target, "jobs-probe"],
                           capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {}, f"'{target}' did not answer within {PROBE_TIMEOUT}s"
    except OSError as e:
        return {}, str(e)
    if p.returncode != 0 and not p.stdout.strip():
        err = (p.stderr or "").strip().splitlines()
        return {}, (err[-1] if err else f"jobs-probe failed (rc={p.returncode})")

    rows: dict[tuple[str, str], dict] = {}
    for line in p.stdout.splitlines():
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 8:
            continue
        tag, kind, state, rc, pid, mtime, lines, errors = parts[:8]
        cmd = parts[8] if len(parts) > 8 else ""
        last = parts[9] if len(parts) > 9 else ""
        rows[(tag, kind)] = {
            "state": state,
            "rc": int(rc) if rc.isdigit() else None,
            "pid": int(pid) if pid.isdigit() else None,
            "mtime": float(mtime) if mtime.replace(".", "").isdigit() else 0.0,
            "lines": int(lines) if lines.isdigit() else 0,
            "errors": int(errors) if errors.isdigit() else 0,
            "cmd": cmd,
            "last_line": last,
        }
    return rows, ""


# --------------------------------------------------------------------------
# logs — the remote keeps ONE log per (tag, kind) and overwrites it on the next
# run of that slot, so a log only survives if it is mirrored here first.
# --------------------------------------------------------------------------
# target/tag/kind reach BOTH a filesystem path and a remote shell command (mctl
# jobs-tail interpolates the tag into an ssh command line), so they are
# allow-listed, not escaped. An mctl tag is "$HOME-relative path with / -> _",
# which never needs anything outside this set.
_SAFE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _safe_id(value: str) -> bool:
    return bool(_SAFE_ID.match(value or "")) and ".." not in value


def local_jobdir(target: str, tag: str) -> Path:
    return MCTL_DIR / "jobs" / target / tag


def local_log(target: str, tag: str, kind: str) -> Path:
    return local_jobdir(target, tag) / f"{kind}.log"


def sync_logs(target: str) -> dict:
    """`mctl -t <target> jobs-sync` — mirror every remote job log locally."""
    if not _safe_id(target):
        return {"target": target, "error": "not a target name", "logs": 0}
    if not MCTL.exists():
        return {"target": target, "error": "mctl not installed", "logs": 0}
    try:
        p = subprocess.run([str(MCTL), "-t", target, "jobs-sync"],
                           capture_output=True, text=True, timeout=SYNC_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"target": target, "error": f"log sync timed out ({SYNC_TIMEOUT}s)", "logs": 0}
    except OSError as e:
        return {"target": target, "error": str(e), "logs": 0}
    root = MCTL_DIR / "jobs" / target
    n = len(list(root.rglob("*.log"))) if root.is_dir() else 0
    err = ""
    if p.returncode != 0:
        lines = (p.stderr or "").strip().splitlines()
        err = lines[-1] if lines else f"jobs-sync failed (rc={p.returncode})"
    return {"target": target, "error": err, "logs": n}


def read_log(target: str, tag: str, kind: str, limit: int = LOG_MAX_BYTES) -> dict:
    """Serve a stored log. Falls back to the live remote copy if none is held."""
    path = local_log(target, tag, kind)
    if not path.exists():
        text = tail_remote(target, tag, kind, 400)
        if not text:
            return {"error": f"no local copy of {tag}/{kind}.log and the target has none",
                    "path": str(path), "text": "", "bytes": 0, "truncated": False,
                    "source": "none"}
        return {"error": "", "path": "", "text": text, "bytes": len(text),
                "truncated": True, "source": "remote"}
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > limit:
            fh.seek(size - limit)
            fh.readline()                      # drop the partial first line
        raw = fh.read()
    return {"error": "", "path": str(path), "source": "local",
            "text": _clean(raw.decode("utf-8", "replace")),
            "bytes": size, "truncated": size > limit,
            "mtime": datetime.fromtimestamp(path.stat().st_mtime)
                             .strftime("%Y-%m-%dT%H:%M:%S")}


def tail_remote(target: str, tag: str, kind: str, lines: int = 200) -> str:
    """Last N lines of a LIVE remote log — one ssh, no login shell."""
    if not MCTL.exists() or not (_safe_id(target) and _safe_id(tag) and _safe_id(kind)):
        return ""
    try:
        p = subprocess.run([str(MCTL), "-t", target, "jobs-tail", tag, kind, str(lines)],
                           capture_output=True, text=True, timeout=TAIL_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return _clean(p.stdout)


def _clean(text: str) -> str:
    """Strip ANSI colour and CR so the browser's <pre> stays readable."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\x1b" and i + 1 < n and text[i + 1] == "[":
            j = i + 2
            while j < n and text[j] not in "ABCDEFGHJKSTfmnsulh":
                j += 1
            i = j + 1
            continue
        if c != "\r":
            out.append(c)
        i += 1
    return "".join(out)


def tail(target: str, tag: str, kind: str, lines: int = 200) -> dict:
    """One poll of a running job's log. The UI calls this on a timer."""
    text = tail_remote(target, tag, kind, lines)
    if text:
        return {"error": "", "text": text, "source": "remote",
                "at": time.strftime("%H:%M:%S")}
    held = read_log(target, tag, kind)
    if held.get("error"):
        return {"error": f"could not reach '{target}' and hold no local copy",
                "text": "", "source": "none", "at": time.strftime("%H:%M:%S")}
    body = held["text"].splitlines()[-lines:]
    return {"error": "", "text": "\n".join(body), "source": "local-fallback",
            "at": time.strftime("%H:%M:%S")}


def refresh(only: str | None = None, sync: bool = True) -> dict:
    jobs = _read_log()
    state = _read_state()
    jstate = state["jobs"]
    errors: dict[str, str] = {}

    # EVERY registered target is probed, not just the ones with an unresolved
    # job. Gating on "has pending work" was self-defeating: once the last job
    # settled, the target was never asked again, so a job started afterwards --
    # the one the user actually wants to watch -- could never be discovered.
    pending: set[str] = set(targets())
    for j in jobs:
        if jstate.get(j["id"], {}).get("status") not in TERMINAL:
            pending.add(j.get("target") or "vm")
    pulled = MCTL_DIR / "jobs"
    if pulled.is_dir():
        pending |= {d.name for d in pulled.iterdir() if d.is_dir()}
    if only:
        pending &= {only}
        if not pending:
            return {"probed": 0, "targets": [], "errors": {},
                    "error": f"unknown target: {only}"}

    probed: dict[str, dict] = {}
    for t in sorted(pending):
        rows, err = probe(t)
        probed[t] = rows
        if err:
            errors[t] = err
        elif sync:
            sync_logs(t)          # keep a local copy before the slot is reused

    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    # A probe row describes the CURRENT remote slot, which belongs to the
    # newest dispatch for that (target, tag, kind). Anything older is history.
    seen: set[tuple[str, str, str]] = set()
    for j in sorted(jobs, key=lambda r: _epoch(r.get("started", "")), reverse=True):
        jid = j["id"]
        cur = jstate.setdefault(jid, {})
        if cur.get("status") in TERMINAL:
            seen.add((j.get("target") or "vm", j.get("tag", ""), j.get("kind", "")))
            continue
        tkey = (j.get("target") or "vm", j.get("tag", ""), j.get("kind", ""))
        if tkey[0] not in probed:
            continue                      # target not queried this round
        if tkey in seen:                  # a newer dispatch owns the remote slot
            cur.update(status="superseded", checked=now)
            continue
        seen.add(tkey)
        if errors.get(tkey[0]):
            cur.update(status=cur.get("status") or "running", checked=now,
                       note=errors[tkey[0]])
            continue
        row = probed[tkey[0]].get((tkey[1], tkey[2]))
        if row is None:
            cur.update(status="gone", checked=now,
                       note="no log on the target (cleaned or never started)")
            continue
        status = _status_of(row["state"], row["rc"])
        cur.update(status=status, rc=row["rc"], pid=row["pid"], lines=row["lines"],
                   errors=row["errors"], last_line=row["last_line"], checked=now)
        if row["cmd"] and not j.get("cmd"):
            cur["cmd"] = row["cmd"]
        cur.pop("note", None)
        if status == "running":
            cur["finished"] = ""
        elif not cur.get("finished"):
            cur["finished"] = (datetime.fromtimestamp(row["mtime"]).strftime("%Y-%m-%dT%H:%M:%S")
                               if row["mtime"] else now)

    # Jobs that exist on a target but are not represented by an OPEN record in
    # the log are adopted. Two cases qualify:
    #   * the slot was never logged here (pre-dates the log, or was launched
    #     from another machine);
    #   * the slot WAS logged, that record is already terminal, and the remote
    #     log has been written since -- i.e. the tag was re-run. Without this
    #     second case a repeat build of a workspace we have built before is
    #     invisible forever, which is exactly the job you want to watch.
    newest: dict[tuple[str, str, str], float] = {}
    for j in jobs:
        key = (j.get("target") or "vm", j.get("tag", ""), j.get("kind", ""))
        newest[key] = max(newest.get(key, 0.0), _epoch(j.get("started", "")))
    open_slots = {(j.get("target") or "vm", j.get("tag", ""), j.get("kind", ""))
                  for j in jobs if jstate.get(j["id"], {}).get("status") not in TERMINAL}
    rev = _tag_to_ws()
    for t, rows in probed.items():
        if errors.get(t):
            continue
        for (tag, kind), row in rows.items():
            key = (t, tag, kind)
            if key in open_slots:
                continue                       # an unresolved record owns it
            if key in newest and row["mtime"] <= newest[key] + 60:
                continue                       # settled record, slot untouched
            started = (datetime.fromtimestamp(row["mtime"]).strftime("%Y-%m-%dT%H:%M:%S")
                       if row["mtime"] else now)
            rec = {"id": f"{int(row['mtime'] * 1000) or int(time.time() * 1000)}-{kind}-adopted",
                   "target": t, "dest": "", "kind": kind,
                   "ws": rev.get(tag, ""), "tag": tag,
                   "cmd": row["cmd"], "pid": row["pid"], "jobdir": "",
                   "started": started, "state": "adopted", "adopted": True}
            if rec["id"] in jstate:
                continue                       # already adopted this very slot
            _append_log(rec)
            status = _status_of(row["state"], row["rc"])
            jstate[rec["id"]] = {"status": status, "rc": row["rc"], "pid": row["pid"],
                                 "lines": row["lines"], "errors": row["errors"],
                                 "last_line": row["last_line"], "checked": now,
                                 "finished": "" if status == "running" else started}

    state["jobs"] = jstate
    state["last_refresh"] = now
    # A single-target refresh must not erase what we know about the others, nor
    # claim the whole fleet was just checked. Errors are merged per target and
    # every target carries its own timestamp.
    prev_errors = dict(state.get("errors") or {})
    for t in probed:
        prev_errors.pop(t, None)
    prev_errors.update(errors)
    state["errors"] = prev_errors
    seen_at = dict(state.get("checked") or {})
    seen_at.update({t: now for t in probed})
    state["checked"] = seen_at
    _write_state(state)
    return {"probed": len(probed), "targets": sorted(probed), "errors": errors}


# --------------------------------------------------------------------------
# machines — EC2 power state behind `octl vm`
# --------------------------------------------------------------------------
def _tagval(tags: list, key: str) -> str:
    for t in tags or []:
        if isinstance(t, str) and t.startswith(key + "="):
            return t[len(key) + 1:]
    return ""


def _target_ips() -> dict[str, str]:
    """Cached private IP per mctl target, so an instance can be matched to one."""
    out = {}
    for name in targets():
        f = MCTL_DIR / f"{name}.ip"
        try:
            ip = f.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if ip:
            out[name] = ip
    return out


def machines(force: bool = False) -> dict:
    """EC2 instances from `octl vm list --output json`, cached in vmstate.json.

    The call revalidates SSO and takes tens of seconds, so the portal serves the
    cache and only re-queries when the user asks for it.
    """
    if not force and VMSTATE.exists():
        try:
            return json.loads(VMSTATE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    if not force:
        return {"machines": [], "checked": "", "error": ""}

    try:
        p = subprocess.run(["octl", "vm", "list", "--output", "json", "--silent"],
                           capture_output=True, text=True, timeout=OCTL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"machines": [], "checked": "", "error": f"octl vm list timed out ({OCTL_TIMEOUT}s)"}
    except OSError as e:
        return {"machines": [], "checked": "", "error": str(e)}

    raw = p.stdout.strip()
    # octl brackets the payload with noise: `INF ... Validating SSO credentials`
    # lines before it, and -- when a newer octl exists -- an "A new release is
    # available" banner after it. Decode the first complete JSON value and let
    # raw_decode ignore whatever trails it.
    start = raw.find("{")
    try:
        if start < 0:
            raise ValueError("no json object in octl output")
        data, _ = json.JSONDecoder().raw_decode(raw[start:])
    except ValueError:
        err = (p.stderr or raw or "").strip().splitlines()
        return {"machines": [], "checked": "",
                "error": err[-1] if err else "could not parse octl vm list"}

    ips = _target_ips()
    now = time.time()
    out = []
    for inst in data.get("aws") or []:
        tags = inst.get("tags") or []
        lease = _tagval(tags, "lease_expires_at")
        left = 0.0
        if lease:
            try:
                left = (datetime.strptime(lease, "%Y-%m-%dT%H:%M:%SZ")
                        .replace(tzinfo=timezone.utc).timestamp() - now)
            except ValueError:
                left = 0.0
        ip = inst.get("private_ip") or ""
        out.append({
            "name": inst.get("name") or "?",
            "id": inst.get("instance_id") or "",
            "type": inst.get("instance_type") or "",
            "state": (inst.get("state") or "unknown").lower(),
            "ip": ip,
            "target": next((t for t, v in ips.items() if v == ip), ""),
            "desc": _tagval(tags, "description"),
            "lease": lease,
            "lease_left": int(left),
            "lease_left_h": _human_dur(left) if left > 0 else ("expired" if lease else ""),
        })
    out.sort(key=lambda m: (m["state"] != "running", m["name"]))
    state = {"machines": out, "checked": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "error": "", "age": int(now)}
    MCTL_DIR.mkdir(parents=True, exist_ok=True)
    VMSTATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def power(action: str, instance_id: str) -> dict:
    """`octl vm start|stop <id>`, then re-read the instance list."""
    if action not in ("start", "stop"):
        return {"error": f"unsupported action: {action}"}
    if not instance_id.startswith("i-"):
        return {"error": f"not an instance id: {instance_id}"}
    cmd = ["octl", "vm", action, instance_id]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=OCTL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"error": f"octl vm {action} timed out ({OCTL_TIMEOUT}s)"}
    except OSError as e:
        return {"error": str(e)}
    if p.returncode != 0:
        err = ((p.stderr or "") + (p.stdout or "")).strip().splitlines()
        return {"error": err[-1] if err else f"octl vm {action} failed (rc={p.returncode})"}
    # A restarted VM almost always comes back on a different private IP.
    if action == "start":
        for f in MCTL_DIR.glob("*.ip"):
            f.unlink(missing_ok=True)
    return {"ok": True, "action": action, "id": instance_id, **machines(force=True)}


# --------------------------------------------------------------------------
# payload
# --------------------------------------------------------------------------
def _ws_label(ws: str, tag: str) -> str:
    if not ws:
        return tag or "?"
    home = os.path.expanduser("~")
    p = ws[len(home) + 1:] if ws.startswith(home + "/") else ws
    parts = p.split("/")
    return "/".join(parts[1:]) if parts and parts[0] == "projects" else p


def jobs() -> list[dict]:
    """Every delegated job, newest first, merged with its resolved status."""
    state = _read_state()
    jstate = state["jobs"]
    now = time.time()
    out = []
    for j in _read_log():
        st = jstate.get(j["id"], {})
        status = st.get("status") or "unknown"
        started = j.get("started", "")
        t0 = _epoch(started)
        fin = st.get("finished") or ""
        t1 = _epoch(fin) if fin else (now if status == "running" else 0.0)
        dur = max(0.0, t1 - t0) if t0 and t1 else 0.0
        target = j.get("target") or "vm"
        tag = j.get("tag") or ""
        kind = j.get("kind") or "?"
        held = local_log(target, tag, kind)
        out.append({
            "id": j["id"],
            "target": target,
            "dest": j.get("dest") or "",
            "kind": kind,
            "ws": j.get("ws") or "",
            "ws_label": _ws_label(j.get("ws") or "", tag),
            "tag": tag,
            "cmd": j.get("cmd") or st.get("cmd") or "",
            "jobdir": j.get("jobdir") or "",
            "started": started,
            "finished": fin,
            "duration": int(dur),
            "duration_h": _human_dur(dur),
            "age_h": int((now - t0) / 3600) if t0 else 0,
            "status": status,
            "rc": st.get("rc"),
            "pid": st.get("pid") if st.get("pid") is not None else j.get("pid"),
            "lines": st.get("lines") or 0,
            "errors": st.get("errors") or 0,
            "last_line": st.get("last_line") or "",
            "note": st.get("note") or "",
            "checked": st.get("checked") or "",
            "adopted": bool(j.get("adopted")),
            "active": status in ("running", "unknown"),
            "has_log": held.exists(),
            "log_bytes": held.stat().st_size if held.exists() else 0,
        })
    out.sort(key=lambda r: _epoch(r["started"]), reverse=True)
    return out


def payload() -> dict:
    """Portal payload. Read-only — refreshing is an explicit separate call."""
    try:
        rows = jobs()
    except Exception as e:                                   # noqa: BLE001
        return {"jobs": [], "running": [], "counts": {}, "targets": [],
                "target_state": [],
                "machines": [], "machines_checked": "", "machines_age_h": "",
                "logs_held": 0, "last_refresh": "", "refresh_age_h": "",
                "log": str(JOBLOG), "error": str(e)}
    state = _read_state()
    vms = machines()
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    errs = dict(state.get("errors") or {})
    if vms.get("error"):
        errs["octl"] = vms["error"]
    now = time.time()
    checked = vms.get("checked") or ""
    last = state.get("last_refresh") or ""
    root = MCTL_DIR / "jobs"

    # Per-target freshness. `last_refresh` is fleet-wide and goes stale the
    # moment one target is refreshed on its own, so each target also carries
    # when IT was last asked -- otherwise a single-target refresh makes the
    # whole fleet look freshly checked.
    seen_at = state.get("checked") or {}
    machine_of = {m["target"]: m for m in (vms.get("machines") or []) if m.get("target")}
    tgts = []
    for name in targets():
        at = seen_at.get(name) or ""
        m = machine_of.get(name)
        tgts.append({
            "name": name,
            "checked": at,
            "age_h": _human_dur(now - _epoch(at)) if at else "",
            "error": errs.get(name, ""),
            "jobs": sum(1 for r in rows if r["target"] == name),
            "running": sum(1 for r in rows if r["target"] == name and r["status"] == "running"),
            "instance": (m or {}).get("id", ""),
            "state": (m or {}).get("state", ""),
        })

    return {
        "jobs": rows,
        "running": [r for r in rows if r["status"] == "running"],
        "counts": counts,
        "targets": [t["name"] for t in tgts],
        "target_state": tgts,
        "machines": vms.get("machines") or [],
        "machines_checked": checked,
        # How stale each half of the view is. Without this a day-old "stopped"
        # reading is indistinguishable from a live one -- the exact reason the
        # VM looked dead while ssh to it worked fine.
        "machines_age_h": _human_dur(now - _epoch(checked)) if checked else "",
        "last_refresh": last,
        "refresh_age_h": _human_dur(now - _epoch(last)) if last else "",
        "logs_held": len(list(root.rglob("*.log"))) if root.is_dir() else 0,
        "log": str(JOBLOG),
        "error": "; ".join(f"{k}: {v}" for k, v in errs.items()),
    }


def is_target(name: str) -> bool:
    return bool(name) and name in targets()


def has_instance(name: str) -> bool:
    """True when an mctl target is backed by an EC2 instance we can power-query."""
    ip = (MCTL_DIR / f"{name}.ip")
    if not ip.exists():
        return False
    want = ip.read_text(encoding="utf-8").strip()
    cached = machines()
    return any(m.get("ip") == want for m in cached.get("machines") or [])


def portal_refresh(only: str | None = None) -> dict:
    """Probe target(s), mirror their logs, and re-read the EC2 power state.

    Scoped to one target, the EC2 query is skipped unless that target is
    actually backed by an instance -- asking `octl vm list` costs an SSO
    revalidation and tells you nothing about a bare-metal lab desktop.
    """
    if only and not is_target(only):
        return {"error": f"unknown target: {only}"}
    res = refresh(only)
    if not only or has_instance(only):
        machines(force=True)
    out = payload()
    out["probed"] = res["probed"]
    out["scope"] = only or ""
    return out


def portal_machines() -> dict:
    """Re-read only the EC2 power state — no ssh, no job probe."""
    st = machines(force=True)
    if st.get("error"):
        return {"error": st["error"]}
    out = payload()
    out["checked_machines"] = len(st.get("machines") or [])
    return out


def portal_power(action: str, instance_id: str) -> dict:
    """Start/stop an instance, then return the full refreshed payload."""
    res = power(action, instance_id)
    if res.get("error"):
        return res
    out = payload()
    out["acted"] = f"{action} {instance_id}"
    return out


def portal_log(target: str, tag: str, kind: str) -> dict:
    """Open a stored (or, failing that, live) job log."""
    kind = kind or "build"
    if not _safe_id(target) or not _safe_id(tag) or not _safe_id(kind):
        return {"error": "target, tag and kind must be plain identifiers"}
    out = read_log(target, tag, kind)
    out["target"], out["tag"], out["kind"] = target, tag, kind
    return out


def portal_tail(target: str, tag: str, kind: str, lines: int = 200) -> dict:
    """One poll of a live job log, for the portal's tail panel."""
    kind = kind or "build"
    if not _safe_id(target) or not _safe_id(tag) or not _safe_id(kind):
        return {"error": "target, tag and kind must be plain identifiers"}
    try:
        n = max(20, min(int(lines or 200), 2000))
    except (TypeError, ValueError):
        n = 200
    out = tail(target, tag, kind, n)
    out["target"], out["tag"], out["kind"] = target, tag, kind
    return out


def portal_sync(only: str | None = None) -> dict:
    """Mirror job logs locally, without probing or touching EC2."""
    if only and not is_target(only):
        return {"error": f"unknown target: {only}"}
    results = [sync_logs(t) for t in ([only] if only else targets())]
    out = payload()
    out["synced"] = results
    out["scope"] = only or ""
    bad = [r for r in results if r.get("error")]
    if bad:
        out["sync_error"] = "; ".join(f"{r['target']}: {r['error']}" for r in bad)
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "list"
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd == "refresh":
        only = argv[2] if len(argv) > 2 else None
        if only and not is_target(only):
            print(f"[vd] unknown target: {only} (have: {', '.join(targets())})",
                  file=sys.stderr)
            return 1
        res = refresh(only)
        print(f"[vd] probed {res['probed']} target(s): {', '.join(res['targets']) or '-'}")
        for t, e in (res["errors"] or {}).items():
            print(f"[vd] {t}: {e}", file=sys.stderr)
        return 0
    if cmd == "targets":
        for t in payload()["target_state"]:
            flag = f"\u26a0 {t['error']}" if t["error"] else "ok"
            print(f"{t['name']:10} {t['state'] or '-':10} jobs={t['jobs']:<3} "
                  f"running={t['running']:<3} checked {t['age_h'] or 'never':>10} ago  {flag}")
        return 0
    if cmd in ("vms", "machines"):
        st = machines(force=True)
        if st.get("error"):
            print(f"[vd] {st['error']}", file=sys.stderr)
            return 1
        for m in st["machines"]:
            print(f"{m['state']:10} {m['name']:28} {m['id']:21} {m['type']:14} "
                  f"{m['ip'] or '-':16} lease {m['lease_left_h'] or '-':>9}  "
                  f"{('-> ' + m['target']) if m['target'] else ''}")
        return 0
    if cmd in ("start", "stop"):
        if len(argv) < 3:
            print(f"usage: vd.py {cmd} <instance-id>", file=sys.stderr)
            return 2
        res = power(cmd, argv[2])
        if res.get("error"):
            print(f"[vd] {res['error']}", file=sys.stderr)
            return 1
        print(f"[vd] {cmd} requested for {argv[2]}")
        return 0
    if cmd == "payload":
        print(json.dumps(payload(), indent=2, ensure_ascii=False))
        return 0
    if cmd == "sync":
        for res in ([sync_logs(argv[2])] if len(argv) > 2
                    else [sync_logs(t) for t in targets()]):
            if res.get("error"):
                print(f"[vd] {res['target']}: {res['error']}", file=sys.stderr)
            else:
                print(f"[vd] {res['target']}: {res['logs']} log(s) held locally")
        return 0
    if cmd in ("log", "tail"):
        if len(argv) < 4:
            print(f"usage: vd.py {cmd} <target> <tag> [kind]", file=sys.stderr)
            return 2
        target, tag = argv[2], argv[3]
        kind = argv[4] if len(argv) > 4 else "build"
        res = tail(target, tag, kind) if cmd == "tail" else read_log(target, tag, kind)
        if res.get("error"):
            print(f"[vd] {res['error']}", file=sys.stderr)
            return 1
        print(res["text"])
        return 0
    if cmd == "list":
        rows = jobs()
        if not rows:
            print(f"no delegated jobs recorded ({JOBLOG})")
            return 0
        for r in rows:
            rc = "" if r["rc"] is None else f" rc={r['rc']}"
            print(f"{r['started']:19}  {r['target']:8} {r['kind']:6} "
                  f"{r['status']:11}{rc:7} {r['duration_h']:>8}  {r['ws_label']}")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
