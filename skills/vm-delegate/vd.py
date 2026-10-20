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
    vd.py watchdog [max_age]   re-probe only targets holding unconfirmed jobs
    vd.py sync    [target]     mirror logs only
    vd.py log|tail <target> <tag> [kind]
    vd.py vms | start <id> | stop <id>

A status is a READING, not a fact. It is only true as of `checked`, the moment
the target last confirmed it, so a failed probe must never advance that stamp
and never invent a verdict -- otherwise an unreachable target leaves day-old
`running` rows rendering as freshly verified. Readings that go unconfirmed decay
on their own (flagged, then demoted to `unknown`) and the watchdog re-asks.
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

# A `jobs-probe` that fails at the ssh layer (rc=255: refused / reset / no route)
# is usually a blip -- a VPN hiccup or a sshd that was busy for a second. One
# such blip used to poison the whole view until somebody clicked Refresh again,
# so it is retried. A TIMEOUT is NOT retried: a box that stonewalled for
# PROBE_TIMEOUT is genuinely down and a second wait only doubles the stall.
PROBE_RETRIES = 2           # extra attempts after a connection-level failure
PROBE_BACKOFF = (2.0, 5.0)  # seconds to wait before each retry
SSH_FAIL_RC = 255           # ssh's own "could not establish the session"

# Staleness. A status is a READING, not a fact: it is only true as of the moment
# the target confirmed it. Past these ages an unconfirmed `running` stops being
# presented as live -- first flagged, then demoted -- because the common failure
# is a job that finished (or died) while the target was unreachable.
STALE_AFTER = 600           # 10 min unverified -> flag it, drop it from "live"
RUNNING_TTL = 6 * 3600      # 6 h unverified -> demote `running` to `unknown`
LIVE_RECHECK = 120          # a job believed RUNNING is reconfirmed this often
ERROR_BACKOFF = 600         # ... but a target that just failed is left alone

# Terminal = the outcome is final and no further probe can change it.
# `unknown` is deliberately NOT terminal: a demoted job must stay re-resolvable
# the moment the target answers again.
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
        return {"jobs": {}, "last_refresh": "", "last_attempt": "",
                "errors": {}, "attempted": {}}
    try:
        d = json.loads(JOBSTATE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"jobs": {}, "last_refresh": "", "last_attempt": "",
                "errors": {}, "attempted": {}}
    d.setdefault("jobs", {})
    d.setdefault("last_refresh", "")
    d.setdefault("last_attempt", "")
    d.setdefault("errors", {})
    d.setdefault("attempted", {})
    _disown_poisoned_stamps(d)
    return d


def _disown_poisoned_stamps(d: dict) -> None:
    """Drop `checked` stamps that an earlier version wrote on a FAILED probe.

    That version stamped every attempted target as checked, so an unreachable
    box kept reporting "verified seconds ago" forever. Such a stamp is
    identifiable: the current writer records an `attempted` time for every
    target it probes, so a target that is erroring and carries `checked` but no
    `attempted` was stamped by the old path and its stamp means nothing.
    """
    checked = d.get("checked") or {}
    tried = d.get("attempted") or {}
    errs = d.get("errors") or {}
    for name in [n for n in checked if errs.get(n) and n not in tried]:
        del checked[name]
    d["checked"] = checked


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
# --------------------------------------------------------------------------
# ssh agent — every probe is `ssh -o BatchMode=yes`, so it needs a key. A
# long-lived portal process keeps whatever SSH_AUTH_SOCK it was started with,
# and that is routinely the WRONG one: a gpg-agent socket that holds no
# identities. ssh then fails rc=255 with no stdout and no stderr, for every
# target, forever -- which reads as "the whole fleet is unreachable" and, worse,
# used to be recorded as a successful check. So the socket is validated, and a
# working one is looked for before the failure is reported.
# --------------------------------------------------------------------------
_AGENT: dict = {"sock": None, "error": "", "at": 0.0}
AGENT_RECHECK = 300        # re-validate the resolved socket this often


def _sock_has_keys(sock: str) -> bool:
    if not sock or not Path(sock).exists():
        return False
    env = dict(os.environ, SSH_AUTH_SOCK=sock)
    try:
        p = subprocess.run(["ssh-add", "-l"], capture_output=True, text=True,
                           timeout=10, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return p.returncode == 0          # 1 = agent without identities, 2 = no agent


def _agent_candidates() -> list[str]:
    """Plausible agent sockets, most recently touched first."""
    pats = [Path("/tmp").glob("ssh-*/agent.*"),
            Path(f"/run/user/{os.getuid()}").glob("keyring/ssh"),
            Path(f"/run/user/{os.getuid()}").glob("ssh-agent*")]
    found: list[tuple[float, str]] = []
    for it in pats:
        try:
            for s in it:
                try:
                    found.append((s.stat().st_mtime, str(s)))
                except OSError:
                    continue
        except OSError:
            continue
    found.sort(reverse=True)
    return [s for _, s in found]


def agent_env() -> tuple[dict, str]:
    """(env for ssh subprocesses, error-or-empty).

    Prefers the inherited SSH_AUTH_SOCK when it actually holds identities;
    otherwise adopts the newest agent socket that does. The error is returned
    rather than raised so a probe can still run and report WHY it failed.
    """
    now = time.time()
    if _AGENT["sock"] and now - _AGENT["at"] < AGENT_RECHECK:
        return dict(os.environ, SSH_AUTH_SOCK=_AGENT["sock"]), _AGENT["error"]

    cur = os.environ.get("SSH_AUTH_SOCK", "")
    if cur and _sock_has_keys(cur):
        _AGENT.update(sock=cur, error="", at=now)
        return dict(os.environ), ""
    for cand in _agent_candidates():
        if cand != cur and _sock_has_keys(cand):
            _AGENT.update(sock=cand, error="", at=now)
            return dict(os.environ, SSH_AUTH_SOCK=cand), ""
    err = ("no ssh-agent with identities"
           + (f" (SSH_AUTH_SOCK={cur} holds none)" if cur else " and SSH_AUTH_SOCK is unset")
           + " \u2014 run ssh-add, or restart the portal from a shell that has one")
    _AGENT.update(sock=None, error=err, at=now)
    return dict(os.environ), err


def _probe_once(target: str) -> tuple[dict, str, bool]:
    """One ssh round trip. Returns (rows, error-or-empty, retryable)."""
    env, agent_err = agent_env()
    try:
        p = subprocess.run([str(MCTL), "-t", target, "jobs-probe"],
                           capture_output=True, text=True, timeout=PROBE_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        # Not retryable: it already cost PROBE_TIMEOUT seconds.
        return {}, f"'{target}' did not answer within {PROBE_TIMEOUT}s", False
    except OSError as e:
        return {}, str(e), True
    if p.returncode != 0 and not p.stdout.strip():
        err = (p.stderr or "").strip().splitlines()
        msg = err[-1] if err else f"jobs-probe failed (rc={p.returncode})"
        if p.returncode == SSH_FAIL_RC and agent_err:
            # ssh -o BatchMode=yes with no usable key fails at rc=255 with NO
            # diagnostic at all. Naming the real cause is the difference between
            # a fixable report and a target that silently never answers.
            msg = f"{msg} \u2014 {agent_err}"
            return {}, msg, False          # retrying without a key is pointless
        return {}, msg, p.returncode == SSH_FAIL_RC

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
    return rows, "", False


def probe(target: str) -> tuple[dict, str]:
    """Probe a target, retrying a connection-level failure. ({(tag,kind): row}, err)."""
    if not MCTL.exists():
        return {}, "mctl not installed"
    attempts = PROBE_RETRIES + 1
    err = ""
    for i in range(attempts):
        rows, err, retryable = _probe_once(target)
        if not err:
            return rows, ""
        if not retryable or i == attempts - 1:
            break
        time.sleep(PROBE_BACKOFF[min(i, len(PROBE_BACKOFF) - 1)])
    if err and PROBE_RETRIES:
        err = f"{err} (after {attempts} attempts)"
    return {}, err


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


def local_pid(target: str, tag: str, kind: str) -> Path:
    return local_jobdir(target, tag) / f"{kind}.pid"


# --------------------------------------------------------------------------
# provenance — a log is addressed by (target, tag, kind) and NOTHING else, so a
# second run in the same workspace writes the same path and the mirror of run N
# would be served for run N+1. mctl mirrors `<kind>.pid` next to `<kind>.log`,
# which is enough to name the run a stored log actually belongs to.
# --------------------------------------------------------------------------
def mirror_run(target: str, tag: str, kind: str) -> dict:
    """Which run produced the STORED copy: {exists, pid, mtime, at}."""
    log = local_log(target, tag, kind)
    if not log.exists():
        return {"exists": False, "pid": None, "mtime": 0.0, "at": ""}
    pid = None
    try:
        raw = local_pid(target, tag, kind).read_text(encoding="utf-8").strip()
        pid = int(raw) if raw.isdigit() else None
    except (OSError, ValueError):
        pid = None
    mt = log.stat().st_mtime
    return {"exists": True, "pid": pid, "mtime": mt,
            "at": datetime.fromtimestamp(mt).strftime("%Y-%m-%dT%H:%M:%S")}


def slot_dispatches(target: str, tag: str, kind: str) -> list[dict]:
    """Every dispatch that has ever owned this slot, newest first."""
    rows = [j for j in _read_log()
            if (j.get("target") or "vm") == target
            and j.get("tag", "") == tag and j.get("kind", "") == kind]
    rows.sort(key=lambda r: _epoch(r.get("started", "")), reverse=True)
    return rows


def _job_by_id(job_id: str) -> dict | None:
    for j in _read_log():
        if j.get("id") == job_id:
            return j
    return None


def owns_slot(job: dict) -> bool:
    """True when this dispatch is the newest for its slot, i.e. the remote log
    (and any live tail of it) is still ITS output rather than a successor's."""
    if not job:
        return False
    peers = slot_dispatches(job.get("target") or "vm", job.get("tag", ""), job.get("kind", ""))
    return bool(peers) and peers[0].get("id") == job.get("id")


def _mirror_is(job: dict, mirror: dict, next_start: float | None = None) -> bool:
    """Does the stored copy belong to THIS dispatch?"""
    if not mirror["exists"]:
        return False
    jpid = job.get("pid")
    if jpid and mirror["pid"]:
        return int(jpid) == int(mirror["pid"])
    # No pid to match on (adopted/legacy records): fall back to the time window
    # between this dispatch and the next one in the same slot.
    started = _epoch(job.get("started", ""))
    if not started:
        return False
    if next_start is None:
        peers = slot_dispatches(job.get("target") or "vm", job.get("tag", ""),
                                job.get("kind", ""))
        next_start = 0.0
        for p in peers:                           # peers are newest-first
            t = _epoch(p.get("started", ""))
            if t > started and (not next_start or t < next_start):
                next_start = t
    return mirror["mtime"] >= started - 60 and (not next_start or mirror["mtime"] < next_start)


def read_log(target: str, tag: str, kind: str, limit: int = LOG_MAX_BYTES,
             job_id: str = "") -> dict:
    """Serve a job's log, never a different run's.

    The stored copy is only this job's output when the run that wrote it matches
    (`<kind>.pid`, or the dispatch window when there is no pid). Otherwise the
    newest dispatch is served the LIVE remote log, and an older one is told its
    output is gone rather than being shown a successor's.
    """
    path = local_log(target, tag, kind)
    mirror = mirror_run(target, tag, kind)
    job = _job_by_id(job_id) if job_id else None

    if job:
        mine = _mirror_is(job, mirror)
        newest = owns_slot(job)
        if not mine:
            text = tail_remote(target, tag, kind, 400) if newest else ""
            if text:
                return {"error": "", "path": "", "text": text, "bytes": len(text),
                        "truncated": True, "source": "remote",
                        "provenance": "live log for this run (no stored copy yet)",
                        "job_pid": job.get("pid"), "log_pid": None}
            lost = (f"this run's log is gone \u2014 a later run in the same workspace "
                    f"({tag}) overwrote {kind}.log on the target, and the stored copy "
                    f"here belongs to "
                    + (f"pid {mirror['pid']}" if mirror["pid"] else "another run")
                    + f" from {mirror['at']}") if mirror["exists"] else (
                    f"no copy of {tag}/{kind}.log is held here and the target has none")
            return {"error": lost, "path": str(path), "text": "", "bytes": 0,
                    "truncated": False, "source": "none",
                    "job_pid": job.get("pid"), "log_pid": mirror["pid"]}

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
            "log_pid": mirror["pid"], "job_pid": (job or {}).get("pid"),
            "provenance": ("stored copy of this run" if job
                           else "stored copy of the newest mirrored run"),
            "mtime": mirror["at"]}


def sync_logs(target: str) -> dict:
    """`mctl -t <target> jobs-sync` — mirror every remote job log locally."""
    if not _safe_id(target):
        return {"target": target, "error": "not a target name", "logs": 0}
    if not MCTL.exists():
        return {"target": target, "error": "mctl not installed", "logs": 0}
    try:
        p = subprocess.run([str(MCTL), "-t", target, "jobs-sync"],
                           capture_output=True, text=True, timeout=SYNC_TIMEOUT,
                           env=agent_env()[0])
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


def tail_remote(target: str, tag: str, kind: str, lines: int = 200) -> str:
    """Last N lines of a LIVE remote log — one ssh, no login shell."""
    if not MCTL.exists() or not (_safe_id(target) and _safe_id(tag) and _safe_id(kind)):
        return ""
    try:
        p = subprocess.run([str(MCTL), "-t", target, "jobs-tail", tag, kind, str(lines)],
                           capture_output=True, text=True, timeout=TAIL_TIMEOUT,
                           env=agent_env()[0])
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


def tail(target: str, tag: str, kind: str, lines: int = 200, job_id: str = "") -> dict:
    """One poll of a running job's log. The UI calls this on a timer.

    The remote holds one log per slot, so tailing it only shows THIS job while
    this job is still the newest dispatch for the slot. Once a later run takes
    the slot over, the tail would silently switch to the successor's output --
    so it stops instead of lying.
    """
    job = _job_by_id(job_id) if job_id else None
    if job and not owns_slot(job):
        return {"error": f"a newer run in the same workspace ({tag}) now owns "
                         f"{kind}.log on {target} \u2014 this job's live tail ended",
                "text": "", "source": "none", "at": time.strftime("%H:%M:%S"),
                "superseded": True}
    text = tail_remote(target, tag, kind, lines)
    if text:
        return {"error": "", "text": text, "source": "remote",
                "at": time.strftime("%H:%M:%S")}
    held = read_log(target, tag, kind, job_id=job_id)
    if held.get("error"):
        return {"error": held.get("error")
                         or f"could not reach '{target}' and hold no local copy",
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
    # Only targets that ACTUALLY answered may be treated as verified. Everything
    # downstream keys off this set, because the bug this guards against is an
    # unreachable target leaving month-old verdicts looking freshly confirmed.
    ok_targets = {t for t in probed if not errors.get(t)}

    # A status entry whose dispatch record has vanished from the job log can
    # never be re-probed (the loop below walks the log), so it would sit at
    # whatever it last said -- typically a `running` frozen for days. It also
    # blocks re-adoption of that exact slot id further down. Drop it.
    if JOBLOG.exists() and jobs:
        logged = {j["id"] for j in jobs}
        for orphan in [k for k in jstate if k not in logged]:
            del jstate[orphan]

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
        if tkey[0] not in ok_targets:
            # The target did not answer. We learned NOTHING about this job, so
            # neither its status nor its `checked` stamp may move -- `checked`
            # is "when the target last confirmed this", and overwriting it here
            # is what made unverified verdicts render as freshly probed. Record
            # why instead, and leave the reading exactly as it was.
            cur["note"] = errors[tkey[0]]
            cur["unverified_since"] = cur.get("unverified_since") or now
            continue
        if tkey in seen:                  # a newer dispatch owns the remote slot
            cur.update(status="superseded", checked=now)
            cur.pop("unverified_since", None)
            continue
        seen.add(tkey)
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
        cur.pop("unverified_since", None)
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
        if t not in ok_targets:
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
    # `last_refresh` is what the UI ages ("probed 3m ago"), so it may only move
    # when a target actually answered. An all-failed round is an ATTEMPT, not a
    # refresh -- recording it as one is precisely how a dead probe used to
    # present day-old verdicts as current.
    state["last_attempt"] = now
    if ok_targets:
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
    seen_at.update({t: now for t in ok_targets})     # verified
    state["checked"] = seen_at
    tried_at = dict(state.get("attempted") or {})
    tried_at.update({t: now for t in probed})        # asked, answer or not
    state["attempted"] = tried_at
    _write_state(state)
    return {"probed": len(probed), "targets": sorted(probed), "errors": errors,
            "verified": sorted(ok_targets)}


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
    """Every delegated job, newest first, merged with its resolved status.

    A stored status is only as good as the moment the target confirmed it, so
    an unconfirmed ``running`` decays here rather than being asserted forever:
    flagged ``stale`` past STALE_AFTER, demoted to ``unknown`` past RUNNING_TTL.
    The decay is DERIVED, never written back -- the stored reading survives, and
    one successful probe restores the real status immediately.
    """
    state = _read_state()
    jstate = state["jobs"]
    now = time.time()
    log_rows = _read_log()
    # Per slot, the dispatch start times -- so each row can tell whether a later
    # run has taken the slot (and its log) over, without re-walking the log.
    slot_starts: dict[tuple[str, str, str], list[float]] = {}
    for j in log_rows:
        key = (j.get("target") or "vm", j.get("tag", ""), j.get("kind", ""))
        slot_starts.setdefault(key, []).append(_epoch(j.get("started", "")))
    for v in slot_starts.values():
        v.sort()
    mirrors: dict[tuple[str, str, str], dict] = {}
    out = []
    for j in log_rows:
        st = jstate.get(j["id"], {})
        status = st.get("status") or "unknown"
        checked = st.get("checked") or ""
        t_chk = _epoch(checked)
        unverified = (now - t_chk) if t_chk else 0.0
        stale = False
        demoted = ""
        if status == "running":
            if not t_chk or unverified > RUNNING_TTL:
                # Nobody has confirmed this in RUNNING_TTL. Builds do not run
                # that long unattended; far likelier it finished or died while
                # the target was unreachable. Say "unknown", not "running".
                demoted, status, stale = "running", "unknown", True
            elif unverified > STALE_AFTER:
                stale = True
        started = j.get("started", "")
        t0 = _epoch(started)
        fin = st.get("finished") or ""
        if fin:
            t1 = _epoch(fin)
        elif status in ("running", "unknown") and t_chk:
            t1 = t_chk              # elapsed is only known up to the last check
        elif status == "running":
            t1 = now
        else:
            t1 = 0.0
        dur = max(0.0, t1 - t0) if t0 and t1 else 0.0
        target = j.get("target") or "vm"
        tag = j.get("tag") or ""
        kind = j.get("kind") or "?"
        held = local_log(target, tag, kind)
        skey = (target, tag, kind)
        if skey not in mirrors:
            mirrors[skey] = mirror_run(target, tag, kind)
        mirror = mirrors[skey]
        starts = slot_starts.get(skey) or []
        later = [s for s in starts if s > t0]
        owns = not later                       # newest dispatch still owns the slot
        # The remote (and the mirror) hold ONE log per slot, so "there is a log
        # file" is not "there is THIS run's log". A second run in the same
        # workspace overwrites the first, and serving the leftover as the new
        # job's output is exactly how a fresh job shows a previous run's log.
        mine = _mirror_is(j, mirror, later[0] if later else 0.0)
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
            "started_ts": t0,
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
            "checked": checked,
            # How old the READING is, and whether it is still trustworthy.
            "verified_age": int(unverified) if t_chk else -1,
            "verified_age_h": _human_dur(unverified) if t_chk else "",
            "stale": stale,
            "demoted_from": demoted,
            "unverified_since": st.get("unverified_since") or "",
            "adopted": bool(j.get("adopted")),
            "active": status == "running" and not stale,
            # has_log now means "a stored copy OF THIS RUN", not "some file
            # exists at this slot's path".
            "has_log": mine,
            "log_is_mine": mine,
            "owns_slot": owns,
            "log_pid": mirror["pid"],
            "log_at": mirror["at"],
            "log_bytes": held.stat().st_size if (mine and held.exists()) else 0,
        })
    out.sort(key=lambda r: _epoch(r["started"]), reverse=True)
    return out


def payload() -> dict:
    """Portal payload. Read-only — refreshing is an explicit separate call."""
    try:
        rows = jobs()
    except Exception as e:                                   # noqa: BLE001
        return {"jobs": [], "running": [], "stale": [], "stale_n": 0, "counts": {},
                "targets": [], "target_state": [],
                "machines": [], "machines_checked": "", "machines_age_h": "",
                "logs_held": 0, "last_refresh": "", "refresh_age_h": "",
                "last_attempt": "", "attempt_age_h": "", "unreachable": [],
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
    tried_at = state.get("attempted") or {}
    machine_of = {m["target"]: m for m in (vms.get("machines") or []) if m.get("target")}
    tgts = []
    for name in targets():
        at = seen_at.get(name) or ""
        tried = tried_at.get(name) or ""
        m = machine_of.get(name)
        tgts.append({
            "name": name,
            "checked": at,
            "age_h": _human_dur(now - _epoch(at)) if at else "",
            # When we last ASKED, which differs from `checked` exactly when the
            # target is failing to answer -- the case that must never look fresh.
            "attempted": tried,
            "attempt_age_h": _human_dur(now - _epoch(tried)) if tried else "",
            "error": errs.get(name, ""),
            "jobs": sum(1 for r in rows if r["target"] == name),
            "running": sum(1 for r in rows if r["target"] == name and r["status"] == "running"),
            "stale": sum(1 for r in rows if r["target"] == name and r["stale"]),
            "instance": (m or {}).get("id", ""),
            "state": (m or {}).get("state", ""),
        })

    attempt = state.get("last_attempt") or ""
    stale_rows = [r for r in rows if r["stale"]]
    return {
        "jobs": rows,
        # The browser ticks a running job's elapsed time locally, so it needs an
        # absolute start AND this clock to correct for any skew against its own.
        "now_ts": now,
        # "Running now" must only ever contain CONFIRMED-live jobs. A reading
        # nobody has reconfirmed is listed separately so it is acted on, not
        # believed.
        "running": [r for r in rows if r["status"] == "running" and not r["stale"]],
        "stale": stale_rows,
        "stale_n": len(stale_rows),
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
        # Last time a probe was ATTEMPTED. When it trails `last_refresh`, every
        # target is refusing us and nothing on screen has been reconfirmed.
        "last_attempt": attempt,
        "attempt_age_h": _human_dur(now - _epoch(attempt)) if attempt else "",
        "unreachable": sorted(t["name"] for t in tgts if t["error"]),
        "stale_after": STALE_AFTER,
        "running_ttl": RUNNING_TTL,
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


def unverified_targets(max_age: float = STALE_AFTER) -> list[str]:
    """Targets carrying a job whose status has not been confirmed recently.

    This is the watchdog's work list, and it is tiered on purpose:

      * a job believed RUNNING is the one being watched, so it is reconfirmed
        every LIVE_RECHECK -- otherwise the live view drifts minutes behind;
      * any other unresolved job can wait for `max_age`;
      * a target whose last probe FAILED is left alone for ERROR_BACKOFF, so an
        unreachable box costs one timeout every ten minutes, not every sweep.

    A target with nothing unresolved is never probed: an ssh per idle machine
    buys nothing.
    """
    state = _read_state()
    jstate = state["jobs"]
    errs = state.get("errors") or {}
    now = time.time()
    want: set[str] = set()
    known = set(targets())
    for j in _read_log():
        st = jstate.get(j["id"], {})
        if st.get("status") in TERMINAL:
            continue
        t = j.get("target") or "vm"
        if t not in known:
            continue
        need = LIVE_RECHECK if st.get("status") == "running" else max_age
        if errs.get(t):
            need = max(need, ERROR_BACKOFF)
        t_chk = _epoch(st.get("checked") or "")
        if not t_chk or (now - t_chk) > need:
            want.add(t)
    return sorted(want)


def watchdog(max_age: float = STALE_AFTER) -> dict:
    """One self-healing pass: re-probe only targets holding unconfirmed jobs."""
    due = unverified_targets(max_age)
    if not due:
        return {"probed": 0, "targets": [], "errors": {}, "skipped": "nothing unconfirmed"}
    errors: dict[str, str] = {}
    done: list[str] = []
    for t in due:
        # One target at a time: an unreachable box costs PROBE_TIMEOUT, and a
        # fleet-wide sweep behind it would stall the healthy ones for minutes.
        res = refresh(t)
        errors.update(res.get("errors") or {})
        done.extend(res.get("targets") or [])
    return {"probed": len(done), "targets": done, "errors": errors}


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


def portal_log(target: str, tag: str, kind: str, job_id: str = "") -> dict:
    """Open a job's log — its own, never a later run's in the same workspace."""
    kind = kind or "build"
    if not _safe_id(target) or not _safe_id(tag) or not _safe_id(kind):
        return {"error": "target, tag and kind must be plain identifiers"}
    out = read_log(target, tag, kind, job_id=job_id or "")
    out["target"], out["tag"], out["kind"] = target, tag, kind
    out["job_id"] = job_id or ""
    return out


def portal_tail(target: str, tag: str, kind: str, lines: int = 200,
                job_id: str = "") -> dict:
    """One poll of a live job log, for the portal's tail panel."""
    kind = kind or "build"
    if not _safe_id(target) or not _safe_id(tag) or not _safe_id(kind):
        return {"error": "target, tag and kind must be plain identifiers"}
    try:
        n = max(20, min(int(lines or 200), 2000))
    except (TypeError, ValueError):
        n = 200
    out = tail(target, tag, kind, n, job_id=job_id or "")
    out["target"], out["tag"], out["kind"] = target, tag, kind
    out["job_id"] = job_id or ""
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
    if cmd == "watchdog":
        age = float(argv[2]) if len(argv) > 2 else STALE_AFTER
        res = watchdog(age)
        if res.get("skipped"):
            print(f"[vd] watchdog: {res['skipped']}")
            return 0
        print(f"[vd] watchdog re-probed {res['probed']} target(s): "
              f"{', '.join(res['targets']) or '-'}")
        for t, e in (res["errors"] or {}).items():
            print(f"[vd] {t}: {e}", file=sys.stderr)
        return 0
    if cmd == "targets":
        for t in payload()["target_state"]:
            flag = f"\u26a0 {t['error']}" if t["error"] else "ok"
            seen = f"verified {t['age_h']} ago" if t["checked"] else "never verified"
            print(f"{t['name']:10} {t['state'] or '-':10} jobs={t['jobs']:<3} "
                  f"running={t['running']:<3} stale={t['stale']:<3} {seen:>22}  {flag}")
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
            mark = " \u26a0 unverified " + (r["verified_age_h"] or "ever") if r["stale"] else ""
            print(f"{r['started']:19}  {r['target']:8} {r['kind']:6} "
                  f"{r['status']:11}{rc:7} {r['duration_h']:>8}  {r['ws_label']}{mark}")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
