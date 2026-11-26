"""jobs — the shared detached-job record (``~/.jobs/<id>/``).

One directory per job, plain files only, so a POSIX shell (``bin/job``) and
Python read and write the same record. See ``jobs.md`` next to this file for
the full contract. Runners: ``pilot`` (``job run``), ``mctl`` (remote
build/test/run, mirrored back here by ``mctl jobs-sync``) and ``flash`` (the
vm-delegate local runner).

Liveness is a property of the machine running the process, so a record whose
``host`` is another machine (an NFS-shared home, or a mirror of a remote job)
is never judged with ``kill -0`` here: its status is ``remote``.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

DONE_RE = re.compile(r"__(?:BMC|MCTL)?JOB_DONE__ rc=(-?\d+)|__MCTL_DONE__ rc=(-?\d+)")
SAFE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
TERMINAL = {"passed", "failed", "cancelled", "died"}
# A running record on another host cannot be verified from here; past this age
# it no longer blocks its workspace.
REMOTE_BUSY_TTL = 6 * 3600
_ERR = re.compile(r"error:|error failed|: failed|fail ", re.IGNORECASE)
_BENIGN = re.compile(r"error failed removing stage container|common\.star.*load.*error"
                     r"|load.*common\.star.*error", re.IGNORECASE)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def root() -> Path:
    return Path(os.environ.get("JOB_ROOT") or Path.home() / ".jobs")


def this_host() -> str:
    return socket.gethostname().split(".", 1)[0].lower()


def safe(value: str) -> bool:
    return bool(SAFE.match(value or "")) and ".." not in value


def new_id(runner: str, where: str = "") -> str:
    where = re.sub(r"[^A-Za-z0-9._-]", "", where or this_host())[:40] or "local"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{runner}-{where}-{stamp}-{secrets.token_hex(2)}"


def _utc(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(text: str) -> float:
    text = (text or "").strip()
    if text.isdigit():
        return float(text)
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=timezone.utc).timestamp() if fmt.endswith("Z") else dt.timestamp()
    return 0.0


def _read(p: Path, limit: int = 4096) -> str:
    try:
        with open(p, "rb") as fh:
            return fh.read(limit).decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _write(p: Path, text: str) -> None:
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    os.replace(tmp, p)


def job_dir(job_id: str, base: Path | None = None) -> Path:
    if not safe(job_id):
        raise ValueError(f"unsafe job id: {job_id!r}")
    return (base or root()) / job_id


def create(job_id: str, *, runner: str, kind: str, cmd: str, target: str = "",
           cwd: str = "", host: str | None = None, label: str = "", meta: dict | None = None,
           extra: dict | None = None, base: Path | None = None) -> Path:
    """Write a fresh record (no process yet). ``extra`` adds plain one-line files."""
    for name, value in (("runner", runner), ("kind", kind)):
        if not safe(value):
            raise ValueError(f"unsafe {name}: {value!r}")
    if target and not safe(target):
        raise ValueError(f"unsafe target: {target!r}")
    d = job_dir(job_id, base)
    d.mkdir(parents=True, exist_ok=False)
    fields = {"runner": runner, "kind": kind, "host": this_host() if host is None else host,
              "target": target, "cwd": cwd, "label": label,
              "user": os.environ.get("USER", ""), "started": _utc()}
    fields.update(extra or {})
    for name, value in fields.items():
        if value:
            _write(d / name, str(value).replace("\n", " "))
    (d / "cmd.sh").write_text(cmd.rstrip("\n") + "\n", encoding="utf-8")
    if meta:
        _write(d / "meta.json", json.dumps(meta, sort_keys=True))
    return d


def _pid(d: Path) -> int | None:
    raw = _read(d / "pid", 32)
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def pid_runs(pid: int | None, d: Path) -> bool:
    """Alive AND still this job (argv carries the dir, or env has JOB_ID=<id>)."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        pass
    except OSError:
        return False
    proc = Path(f"/proc/{pid}")
    if not proc.is_dir():
        # Gone between kill -0 and here (reaped zombie), unless there is no /proc at all.
        return not Path("/proc/self").is_dir()
    try:
        argv = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        argv = ""
    if str(d).rstrip("/") in argv:
        return True
    try:
        env = (proc / "environ").read_bytes().split(b"\0")
    except OSError:
        return False
    return f"JOB_ID={d.name}".encode() in env


def _tail(p: Path, limit: int = 8192) -> tuple[str, int]:
    try:
        size = p.stat().st_size
        with open(p, "rb") as fh:
            fh.seek(max(0, size - limit))
            return fh.read().decode("utf-8", "replace"), size
    except OSError:
        return "", 0


def last_line(text: str) -> tuple[str, int | None]:
    """(last meaningful line, rc from a legacy sentinel)."""
    rc = None
    last = ""
    for line in reversed(text.splitlines()):
        m = DONE_RE.search(line)
        if m:
            if rc is None:
                rc = int(m.group(1) or m.group(2))
            continue
        if "__MCTL_CANCELLED__" in line:
            continue
        clean = _ANSI.sub("", line).replace("\r", "").strip()
        if clean and not last:
            last = clean[:240]
        if last and rc is not None:
            break
    return last, rc


def count_errors(path: Path, limit: int = 4 * 1024 * 1024) -> int:
    text, _ = _tail(path, limit)
    n = 0
    for line in text.splitlines():
        if _ERR.search(line) and not _BENIGN.search(line):
            n += 1
    return n


def events(d: Path) -> list[dict]:
    out = []
    try:
        text = (d / "events.jsonl").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def phase(d: Path) -> str:
    cur = ""
    for e in events(d):
        if e.get("type") == "phase":
            cur = e.get("phase") or cur
    return cur


def is_local(rec: dict) -> bool:
    return rec.get("host", "") in ("", this_host())


def read(d: Path, *, errors: bool = False) -> dict | None:
    """The record in ``d`` with its derived status, or None if it is not one."""
    if not d.is_dir() or not safe(d.name):
        return None
    try:
        mtime = d.stat().st_mtime
    except OSError:
        return None
    meta = {}
    raw = _read(d / "meta.json", 65536)
    if raw:
        try:
            meta = json.loads(raw)
        except ValueError:
            meta = {}
    rc_s = _read(d / "rc", 32)
    text, size = _tail(d / "log")
    last, rc_log = last_line(text)
    rc = int(rc_s) if re.fullmatch(r"-?\d+", rc_s) else rc_log
    rec = {
        "id": d.name, "dir": str(d),
        "runner": _read(d / "runner", 64) or "pilot",
        "kind": _read(d / "kind", 64) or "job",
        "host": _read(d / "host", 128).lower(),
        "target": _read(d / "target", 128),
        "cwd": _read(d / "cwd", 1024),
        "local_cwd": _read(d / "local_cwd", 1024),
        "user": _read(d / "user", 64),
        "label": _read(d / "label", 512),
        "cmd": _read(d / "cmd.sh", 2000),
        "started_ts": _epoch(_read(d / "started", 64)) or mtime,
        "finished_ts": _epoch(_read(d / "finished", 64)),
        "pid": _pid(d), "rc": rc, "stopped": (d / "stopped").exists(),
        "last_line": last, "log_bytes": size, "log": str(d / "log"),
        "meta": meta, "mtime": mtime,
    }
    rec["local"] = is_local(rec)
    rec["phase"] = phase(d) if (d / "events.jsonl").exists() else ""
    rec["unstoppable"] = sorted(set(_read(d / "unstoppable", 512).split()))
    rec["status"] = status(rec, d)
    if not rec["finished_ts"] and rec["status"] in TERMINAL:
        for name in ("rc", "stopped", "log"):
            try:
                rec["finished_ts"] = (d / name).stat().st_mtime
                break
            except OSError:
                continue
    if errors:
        rec["errors"] = count_errors(d / "log")
    return rec


def status(rec: dict, d: Path | None = None) -> str:
    d = d or Path(rec["dir"])
    if not rec.get("local", is_local(rec)):
        return "remote"
    rc = rec.get("rc")
    if rc is None and pid_runs(rec.get("pid"), d):
        return "running"
    if rc is None:
        # rc was read before the pid check: the job may have finished in between.
        late = _read(d / "rc", 32)
        if re.fullmatch(r"-?\d+", late):
            rc = rec["rc"] = int(late)
    if rec.get("stopped") and (rc is None or rc != 0):
        return "cancelled"
    if rc is None:
        # A just-launched job has no pid file for a moment.
        if not rec.get("pid") and time.time() - rec.get("started_ts", 0) < 15:
            return "running"
        return "died"
    return "passed" if rc == 0 else "failed"


def get(job_id: str, base: Path | None = None, **kw) -> dict | None:
    try:
        return read(job_dir(job_id, base), **kw)
    except ValueError:
        return None


def list_jobs(*, days: float | None = None, runner: str | set | None = None,
              host: str | None = None, base: Path | None = None, now: float | None = None,
              errors: bool = False) -> list[dict]:
    """Records newest first. ``days`` keeps running jobs and anything touched since."""
    base = base or root()
    now = time.time() if now is None else now
    horizon = now - days * 86400 if days else 0.0
    runners = {runner} if isinstance(runner, str) else set(runner or ())
    out = []
    try:
        entries = list(base.iterdir())
    except OSError:
        return out
    for d in entries:
        if not d.is_dir() or d.name.startswith("."):
            continue
        rec = read(d, errors=errors)
        if not rec:
            continue
        if runners and rec["runner"] not in runners:
            continue
        if host is not None and rec["host"] not in ({host, ""} if host == this_host() else {host}):
            continue
        if horizon and rec["status"] != "running" and max(rec["mtime"], rec["started_ts"]) < horizon:
            continue
        out.append(rec)
    out.sort(key=lambda r: r["started_ts"], reverse=True)
    return out


def busy(cwd: str, base: Path | None = None, now: float | None = None) -> list[dict]:
    """Jobs that hold ``cwd``: running here, or unfinished on another host (unverifiable)."""
    now = time.time() if now is None else now
    out = []
    for rec in list_jobs(base=base, now=now):
        if not cwd or rec["cwd"] != cwd:
            continue
        if rec["status"] == "running":
            out.append(rec)
        elif rec["status"] == "remote" and rec["rc"] is None and not rec["stopped"] \
                and now - rec["started_ts"] < REMOTE_BUSY_TTL:
            out.append(rec)
    return out


def stop(job_id: str, *, refuse_phases: set | frozenset = frozenset(), by: str = "",
         base: Path | None = None, wait: float = 5.0) -> dict:
    """TERM a local running job's process group after writing its ``stopped`` marker.

    Returns ``{"result": STOPPED|STOP-PENDING, ...}`` or ``{"error": ...}``.
    """
    rec = get(job_id, base)
    if not rec:
        return {"error": "unknown job"}
    if not rec["local"]:
        return {"error": f"job runs on {rec['host']}; stop it there"}
    if rec["status"] != "running":
        return {"error": f"job is not running (status: {rec['status']})"}
    if rec["phase"] and rec["phase"] in (set(refuse_phases) | set(rec["unstoppable"])):
        return {"error": f"refusing to stop during the {rec['phase']} phase"}
    d = Path(rec["dir"])
    pid = rec["pid"]
    if not pid_runs(pid, d):
        return {"error": "job has no live process to signal"}
    _write(d / "stopped", f"{_utc()} {by}".strip())
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as e:
            return {"error": str(e)}
    deadline = time.time() + wait
    while time.time() < deadline and pid_runs(pid, d):
        time.sleep(0.1)
    alive = pid_runs(pid, d)
    return {"result": "STOP-PENDING" if alive else "STOPPED", "job_id": job_id,
            "pid": pid, "target": rec["target"]}


def tail(job_id: str, lines: int = 200, base: Path | None = None) -> str:
    rec = get(job_id, base)
    if not rec:
        return ""
    text, _ = _tail(Path(rec["log"]), 1024 * 1024)
    body = [l for l in text.splitlines() if not DONE_RE.search(l)]
    return "\n".join(_ANSI.sub("", l).replace("\r", "") for l in body[-lines:])


def clean(days: float = 30, base: Path | None = None, now: float | None = None) -> list[str]:
    """Remove finished local records not touched for ``days``. Returns removed ids."""
    import shutil
    now = time.time() if now is None else now
    removed = []
    for rec in list_jobs(base=base, now=now):
        if rec["status"] == "running" or (rec["status"] == "remote" and rec["rc"] is None):
            continue                     # a finished copy synced from another host may go
        if now - rec["mtime"] > days * 86400:
            shutil.rmtree(rec["dir"], ignore_errors=True)
            removed.append(rec["id"])
    return removed
