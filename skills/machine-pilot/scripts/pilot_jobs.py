#!/usr/bin/env python3
"""pilot_jobs — machine-pilot's detached jobs as data (owner API for the portal).

Two runners, one record shape:

* local ``job``  -> ``~/.jobs/<TAG>/``            (read from disk, no ssh)
* BMC ``bmcjob`` -> ``/var/lib/bmcjobs/<TAG>/``   (read over ``mctl -t T bmc``
  only when ``refresh_bmc`` is called; the last reading is cached under
  ``~/.pilot/bmcjobs-cache/`` and a failed read never makes it look fresh)

A job is tied to a machine by, in order: the ``target`` file written by
``job run --target``; a ``-t/--target`` naming a known target in its command;
a BMC host or machine host in its command; a tag token equal to a known
target. Anything else is unattributed (``target == ""``).

CLI: ``pilot_jobs.py list [--days N] | refresh-bmc TARGET | stop ID [--force]``
(prints JSON).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL.parents[1] / "lib"))
import jobs  # noqa: E402  (shared ~/.jobs record: lib/jobs.py)
try:
    from private_paths import private_root  # noqa: E402
    STATIONS = private_root() / "skills" / "machine-pilot" / "references" / "stations.tsv"
except Exception:                                            # noqa: BLE001
    STATIONS = Path("/nonexistent")

JOBS_ROOT = jobs.root()
PILOT_DIR = Path.home() / ".pilot"
BMC_CACHE = PILOT_DIR / "bmcjobs-cache"
MCTL_TARGETS = Path.home() / ".mctl" / "targets"
MCTL = Path(os.path.expanduser("~/bin/mctl"))
BMC_ROOT = "/var/lib/bmcjobs"
LOG_VIEW_LINES = 20000
STALE_AFTER = 600              # a BMC reading older than this is unverified
RUNNING_TTL = 6 * 3600         # ... and older than this no longer says "running"
BMC_TIMEOUT = 45
AUTH_BACKOFF = 6 * 3600        # automatic refreshes stop retrying a rejected login
AUTH_FAIL = re.compile(r"permission denied|authentication fail", re.IGNORECASE)
SAFE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Stopping one of these mid-way can leave a part unbootable or a tray half-reset;
# the portal refuses them and only the CLI's --force overrides.
UNSAFE_STOP = re.compile(
    r"fw[-_]?flash|fwloader|fw_ctl\b.*\b(flash|write|stream)|bmccli\s+(device\s+reset|flash|power)"
    r"|power[-_ ]?cycle|\b48\s*v\b|\bmux\b|gpio|\bspi_flash\b.*\bwrite\b|\bflashcp\b|\bmtd_?write\b",
    re.IGNORECASE)


# ------------------------------------------------------------------ registry
def _registry() -> dict:
    """{targets:set, bmc:{host->target}, host:{host->target}, target_bmc:{target->bmc}}."""
    targets, bmc, host, target_bmc, mctl_bmc = set(), {}, {}, {}, {}

    def short(h: str) -> str:
        return h.rsplit("@", 1)[-1].split(".", 1)[0].lower()

    if STATIONS.is_file():
        for line in STATIONS.read_text(errors="replace").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            f = (line.split("\t") + [""] * 4)[:4]
            name = f[0].strip()
            if not SAFE.match(name):
                continue
            targets.add(name)
            if "@" in f[1]:
                host.setdefault(short(f[1]), name)
            for b in f[3].split(","):
                b = short(b.strip())
                if b and b not in ("-", "?"):
                    bmc.setdefault(b, name)
                    target_bmc.setdefault(name, b)
    if MCTL_TARGETS.is_file():
        for line in MCTL_TARGETS.read_text(errors="replace").splitlines():
            f = line.split()
            if not f or f[0].startswith("#") or not SAFE.match(f[0]):
                continue
            targets.add(f[0])
            if len(f) >= 4 and f[1] == "remote":
                host[short(f[3])] = f[0]
            if len(f) >= 5 and f[4] not in ("-", ""):
                b = short(f[4])
                bmc[b] = f[0]
                target_bmc[f[0]] = b
                mctl_bmc[f[0]] = b
    return {"targets": targets, "bmc": bmc, "host": host, "target_bmc": target_bmc,
            "mctl_bmc": mctl_bmc}


def attribute(cmd: str, tag: str, recorded: str = "", reg: dict | None = None) -> tuple[str, str]:
    """-> (target, how); how in recorded|cmd|bmc|host|tag|none."""
    reg = reg or _registry()
    known = reg["targets"]
    if recorded and SAFE.match(recorded):
        return recorded, "recorded"
    for m in re.finditer(r"(?:^|\s)(?:-t|--target)[ =]['\"]?([A-Za-z0-9._-]+)", cmd or ""):
        if m.group(1) in known:
            return m.group(1), "cmd"
    low = (cmd or "").lower()
    for b, t in reg["bmc"].items():
        if re.search(r"(?<![a-z0-9-])" + re.escape(b) + r"(?![a-z0-9-])", low):
            return t, "bmc"
    for h, t in reg["host"].items():
        if re.search(r"(?<![a-z0-9-])" + re.escape(h) + r"(?![a-z0-9-])", low):
            return t, "host"
    for tok in re.split(r"[-_.]", tag or ""):
        if tok in known:
            return tok, "tag"
    return "", "none"


# ------------------------------------------------------------------ helpers
def _utc_epoch(text: str) -> float:
    return jobs._epoch(text) if (text or "").strip() else 0.0


def stop_refusal(cmd: str) -> str:
    m = UNSAFE_STOP.search(cmd or "")
    return (f"refusing to stop: the command looks disturb-critical ({m.group(0)!r}); "
            "interrupting it can leave the hardware half-done") if m else ""


def _status(rc, alive: bool, stopped: bool = False) -> str:
    if alive:
        return "running"
    if stopped:
        return "cancelled"
    if rc is None:
        return "died"
    return "passed" if rc == 0 else "failed"


# ------------------------------------------------------------------ local jobs
VD_RUNNERS = ("mctl", "flash")      # listed (and stopped) by vm-delegate, not here


def list_local(days: int | None = 14, now: float | None = None, reg: dict | None = None) -> list[dict]:
    """``job run`` records from the shared ``~/.jobs`` root.

    Every runner except the ones vm-delegate lists itself (``VD_RUNNERS``) --
    ``pilot`` and any ad-hoc ``job run --runner NAME``.

    A record written on another host (NFS-shared home) cannot be checked from
    here: it is listed as ``unknown`` with its host, and is never stoppable.
    """
    now = time.time() if now is None else now
    reg = reg or _registry()
    out = []
    for rec in jobs.list_jobs(days=days or None, base=JOBS_ROOT, now=now):
        if rec["runner"] in VD_RUNNERS:
            continue
        tag = rec["id"]
        target, how = attribute(rec["cmd"], tag, rec["target"], reg)
        remote = rec["status"] == "remote"
        status = "unknown" if remote else rec["status"]
        out.append({
            "id": f"pj:L:{tag}", "source": "local", "tag": tag, "target": target,
            "target_how": how, "bmc": reg["target_bmc"].get(target, ""),
            "host": rec["host"], "runner": rec["runner"], "kind": rec["kind"], "cwd": rec["cwd"], "label": rec["label"],
            "cmd": rec["cmd"], "started_ts": rec["started_ts"], "finished_ts": rec["finished_ts"],
            "status": status, "rc": rec["rc"], "pid": rec["pid"], "last_line": rec["last_line"],
            "log_bytes": rec["log_bytes"], "checked_ts": now, "stale": False,
            "error": f"runs on {rec['host']}; status is read there" if remote else "",
            "stoppable": status == "running", "stop_refusal": stop_refusal(rec["cmd"]),
        })
    return out


def _local_dir(job_id: str) -> Path:
    tag = job_id.split(":", 2)[2]
    if not jobs.safe(tag):
        raise ValueError("unsafe job tag")
    return JOBS_ROOT / tag


# ------------------------------------------------------------------ BMC jobs
_BMC_LIST = (
    "R=" + BMC_ROOT + "; [ -d $R ] || { echo __NOBMCJOBS__; exit 0; }; "
    "for d in $R/*/; do [ -d \"$d\" ] || continue; t=$(basename \"$d\"); "
    "p=$(cat \"$d/pid\" 2>/dev/null); rc=$(cat \"$d/rc\" 2>/dev/null); "
    "s=$(cat \"$d/started\" 2>/dev/null); a=0; k=0; [ -e \"$d/stopped\" ] && k=1; "
    "if [ -n \"$p\" ] && [ -z \"$rc\" ] && kill -0 \"$p\" 2>/dev/null; then a=1; fi; "
    "f=$(date -u -r \"$d/rc\" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null); "
    "c=$(head -c 600 \"$d/cmd.sh\" 2>/dev/null | tr '\\t\\n' '  '); "
    "l=$(grep -v '^__BMCJOB_DONE__' \"$d/log\" 2>/dev/null | tail -n 1 | head -c 240 | tr '\\t' ' '); "
    "printf '__J__\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \"$t\" \"$p\" \"$rc\" \"$a\" \"$s\" \"$f\" \"$c\" \"$l\" \"$k\"; "
    "done; echo __END__")


def _mctl_bmc(target: str, script: str, timeout: int = BMC_TIMEOUT, run=subprocess.run):
    return run([str(MCTL), "-t", target, "bmc", script], capture_output=True, text=True,
               timeout=timeout, stdin=subprocess.DEVNULL)


def _cache_path(target: str) -> Path:
    return BMC_CACHE / f"{target}.json"


def _load_cache(target: str) -> dict:
    try:
        return json.loads(_cache_path(target).read_text())
    except (OSError, ValueError):
        return {}


def parse_bmc(text: str) -> tuple[list[dict], bool]:
    """-> (rows, has_bmcjob_dir)."""
    if "__NOBMCJOBS__" in text:
        return [], False
    rows = []
    for line in text.splitlines():
        if not line.startswith("__J__\t"):
            continue
        f = (line.split("\t") + [""] * 10)[1:10]
        tag = f[0]
        if not SAFE.match(tag):
            continue
        rows.append({"tag": tag, "pid": int(f[1]) if f[1].isdigit() else None,
                     "rc": int(f[2]) if re.fullmatch(r"-?\d+", f[2]) else None,
                     "alive": f[3] == "1", "started": f[4], "finished": f[5],
                     "cmd": f[6].strip(), "last_line": f[7].strip(), "stopped": f[8] == "1"})
    return rows, True


def refresh_bmc(target: str, run=subprocess.run, now: float | None = None,
                auto: bool = False) -> dict:
    """Read the BMC's bmcjob dirs once (read-only) and cache the reading.

    ``auto`` (portal refreshes) does not retry a login the BMC rejected within
    AUTH_BACKOFF, so a wrong stored password cannot lock the account out.
    """
    now = time.time() if now is None else now
    reg = _registry()
    if not SAFE.match(target or "") or target not in reg["mctl_bmc"]:
        return {"target": target, "error": "no BMC registered for this mctl target", "skipped": True}
    cache = _load_cache(target)
    if auto and AUTH_FAIL.search(cache.get("error") or "") \
            and now - float(cache.get("attempted") or 0) < AUTH_BACKOFF:
        return {"target": target, "skipped": True, "jobs": len(cache.get("rows") or []),
                "error": "BMC login was rejected; not retrying automatically for "
                         f"{AUTH_BACKOFF // 3600} h (run pilot_jobs.py refresh-bmc {target})"}
    cache.update({"target": target, "bmc": reg["mctl_bmc"][target], "attempted": now})
    try:
        p = _mctl_bmc(target, _BMC_LIST, run=run)
        text = (p.stdout or "")
        if p.returncode != 0 or "__END__" not in text and "__NOBMCJOBS__" not in text:
            raise RuntimeError(((p.stderr or "").strip().splitlines() or [f"rc={p.returncode}"])[-1][:200])
        rows, present = parse_bmc(text)
        cache.update({"rows": rows, "present": present, "checked": now, "error": ""})
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as e:
        cache["error"] = str(e) or e.__class__.__name__
    BMC_CACHE.mkdir(parents=True, exist_ok=True)
    tmp = _cache_path(target).with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    os.replace(tmp, _cache_path(target))
    return {"target": target, "jobs": len(cache.get("rows") or []),
            "present": cache.get("present", False), "error": cache.get("error", "")}


def list_bmc(days: int | None = 14, now: float | None = None, reg: dict | None = None) -> list[dict]:
    now = time.time() if now is None else now
    reg = reg or _registry()
    horizon = now - days * 86400 if days else 0
    out = []
    if not BMC_CACHE.is_dir():
        return out
    for f in BMC_CACHE.glob("*.json"):
        target = f.stem
        if not SAFE.match(target):
            continue
        cache = _load_cache(target)
        checked = float(cache.get("checked") or 0)
        age = now - checked if checked else 1e12
        for r in cache.get("rows") or []:
            started_ts = _utc_epoch(r.get("started", ""))
            fin = _utc_epoch(r.get("finished", ""))
            status = _status(r.get("rc"), bool(r.get("alive")), bool(r.get("stopped")))
            stale = False
            if status == "running":
                if age > RUNNING_TTL:
                    status, stale = "unknown", True
                elif age > STALE_AFTER:
                    stale = True
            if horizon and status != "running" and max(started_ts, fin) < horizon:
                continue
            out.append({
                "id": f"pj:B:{target}:{r['tag']}", "source": "bmc", "tag": r["tag"],
                "target": target, "target_how": "bmc", "bmc": cache.get("bmc", ""),
                "cmd": r.get("cmd", ""), "started_ts": started_ts, "finished_ts": fin,
                "status": status, "rc": r.get("rc"), "pid": r.get("pid"),
                "last_line": r.get("last_line", ""), "log_bytes": 0,
                "checked_ts": checked, "stale": stale, "error": cache.get("error", ""),
                "stoppable": status == "running" and not stale,
                "stop_refusal": stop_refusal(r.get("cmd", "")),
            })
    out.sort(key=lambda r: r["started_ts"], reverse=True)
    return out


def bmc_targets() -> list[str]:
    """mctl targets whose BMC can be read with ``mctl -t T bmc``."""
    return sorted(_registry()["mctl_bmc"])


def bmc_state() -> dict:
    """{target: {checked, attempted, error, present, jobs}} from the cache."""
    out = {}
    if BMC_CACHE.is_dir():
        for f in BMC_CACHE.glob("*.json"):
            c = _load_cache(f.stem)
            out[f.stem] = {k: c.get(k) for k in ("checked", "attempted", "error", "present", "bmc")}
            out[f.stem]["jobs"] = len(c.get("rows") or [])
    return out


def list_jobs(days: int | None = 14, now: float | None = None, bmc: bool = True) -> list[dict]:
    reg = _registry()
    rows = list_local(days, now, reg) + (list_bmc(days, now, reg) if bmc else [])
    rows.sort(key=lambda r: r["started_ts"], reverse=True)
    return rows


def find(job_id: str) -> dict | None:
    for r in list_jobs(days=None):
        if r["id"] == job_id:
            return r
    return None


def _split_bmc_id(job_id: str) -> tuple[str, str]:
    parts = job_id.split(":", 3)
    if len(parts) != 4 or not SAFE.match(parts[2]) or not SAFE.match(parts[3]):
        raise ValueError("unsafe BMC job id")
    return parts[2], parts[3]


# ------------------------------------------------------------------ log + stop
def log_view(job_id: str) -> dict:
    """Same contract as vd.log_view: {mode:"local", command, title, running, target}."""
    job = find(job_id or "")
    if not job:
        return {"error": "unknown pilot job"}
    running = job["status"] == "running"
    if job["source"] == "local":
        log = _local_dir(job_id) / "log"
        if not log.exists():
            return {"error": f"no log for {job['tag']}"}
        lp = shlex.quote(str(log))
        cmd = f"tail -n 300 -F {lp}" if running else f"tail -n {LOG_VIEW_LINES} {lp}"
    else:
        target, tag = _split_bmc_id(job_id)
        path = f"{BMC_ROOT}/{tag}/log"
        remote = (f"tail -n 300 -f {path}" if running else f"tail -n {LOG_VIEW_LINES} {path}")
        cmd = f"exec {shlex.quote(str(MCTL))} -t {shlex.quote(target)} bmc {shlex.quote(remote)}"
    where = job["target"] or "local"
    return {"mode": "local", "target": job["target"], "running": running, "command": cmd,
            "title": ("tail -F " if running else "tail ") + f"{where}:{job['source']}job {job['tag']}"}


def stop(job_id: str, force: bool = False, run=subprocess.run) -> dict:
    job = find(job_id or "")
    if not job:
        return {"error": "unknown pilot job"}
    if job["status"] != "running":
        return {"error": f"job is not running (status: {job['status']})"}
    if job["stale"]:
        return {"error": "the BMC reading is unverified; refresh it before stopping"}
    if job["stop_refusal"] and not force:
        return {"error": job["stop_refusal"]}
    if job["source"] == "local":
        return jobs.stop(_local_dir(job_id).name, base=JOBS_ROOT, by="portal") | \
            {"job_id": job_id, "target": job["target"]}
    target, tag = _split_bmc_id(job_id)
    jd = f"{BMC_ROOT}/{tag}"
    script = (f"d={jd}; p=$(cat $d/pid 2>/dev/null); "
              "[ -n \"$p\" ] || { echo NOPID; exit 3; }; "
              "[ -z \"$(cat $d/rc 2>/dev/null)\" ] || { echo DONE; exit 3; }; "
              f"tr '\\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q '{jd}' || {{ echo NOTOURS; exit 3; }}; "
              "date -u +%Y-%m-%dT%H:%M:%SZ > $d/stopped; kill -TERM -$p 2>/dev/null || kill -TERM $p; echo SIGNALLED")
    try:
        p = _mctl_bmc(target, script, run=run)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": f"could not reach the BMC: {e}"}
    out = (p.stdout or "").strip().splitlines()
    word = out[-1] if out else ""
    refresh_bmc(target, run=run)
    if word != "SIGNALLED":
        return {"error": {"NOPID": "job has no pid on the BMC",
                          "DONE": "job already finished on the BMC",
                          "NOTOURS": "the recorded pid no longer runs this job"}.get(
            word, ((p.stderr or "").strip() or f"stop failed (rc={p.returncode})")[:200])}
    return {"result": "STOPPED", "job_id": job_id, "target": target, "pid": job["pid"]}


# ------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pilot_jobs", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--days", type=int, default=14)
    ls.add_argument("--no-bmc", action="store_true")
    rb = sub.add_parser("refresh-bmc")
    rb.add_argument("target")
    st = sub.add_parser("stop")
    st.add_argument("id")
    st.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        res = list_jobs(a.days or None, bmc=not a.no_bmc)
    elif a.cmd == "refresh-bmc":
        res = refresh_bmc(a.target)
    else:
        res = stop(a.id, force=a.force)
    print(json.dumps(res, indent=1))
    return 1 if isinstance(res, dict) and res.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
