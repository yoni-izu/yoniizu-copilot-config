"""Shared job records (``~/.jobs/<id>/``) in the Machines job view.

One adapter over the two owners of those records:

* vm-delegate (``vd.payload()``) lists and stops runner ``mctl`` and ``flash``
  records -- its rows only gain ``source``/``runner_name`` here;
* machine-pilot (``pilot_jobs.py``) lists every other runner (``pilot`` and
  ad-hoc ``job run --runner NAME``) plus the legacy BMC ``bmcjob`` dirs; this
  module maps those onto the vd row shape.

Ids: ``job:<record id>`` for shared records; legacy ``pj:L:<id>`` / ``pj:B:...``
are still accepted.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.expanduser("~/.copilot/skills/machine-pilot/scripts"))
try:
    import pilot_jobs  # noqa: E402
except ImportError:                                   # machine-pilot not installed
    pilot_jobs = None

UNATTRIBUTED = "(unattributed)"
HOW = {"recorded": "", "cmd": "machine guessed from the command",
       "bmc": "machine guessed from the BMC host", "host": "machine guessed from the host name",
       "tag": "machine guessed from the tag", "none": "no machine recorded or recognised"}


PRUNE_EVERY_S = 6 * 3600
_LAST_PRUNE = {"at": 0.0}


def is_pilot_id(job_id) -> bool:
    """A row this module serves (not a vd job)."""
    return isinstance(job_id, str) and job_id.startswith(("pj:", "job:"))


def pilot_id(job_id: str) -> str:
    """``job:<id>`` -> machine-pilot's ``pj:L:<id>``; legacy ids pass through."""
    return "pj:L:" + job_id[4:] if job_id.startswith("job:") else job_id


def source_of(j: dict) -> str:
    """Source-filter value of a vd row: its local runner is the flash runner."""
    return "flash" if j.get("runner") == "local" else "delegated"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S") if ts else ""


def _dur(secs: float) -> str:
    if secs <= 0:
        return ""
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s" if m else f"{s}s"


def to_row(r: dict, now: float) -> dict:
    """One pilot_jobs record -> a Machines table row."""
    running = r["status"] == "running"
    end = now if running else (r.get("finished_ts") or r["started_ts"])
    dur = max(0, int(end - r["started_ts"])) if r["started_ts"] else 0
    checked = r.get("checked_ts") or 0
    vage = int(now - checked) if checked else 0
    notes = [n for n in (HOW.get(r.get("target_how"), ""),
                         r.get("error") and f"last BMC read failed: {r['error']}") if n]
    bmc = r["source"] == "bmc"
    return {
        "id": r["id"] if bmc else "job:" + r["tag"], "job_id": "" if bmc else r["tag"],
        "runner_name": "bmcjob" if bmc else (r.get("runner") or "pilot"), "target": r["target"] or UNATTRIBUTED, "attributed": bool(r["target"]),
        "dest": "bmc" if bmc else "", "kind": "bmcjob" if bmc else "job",
        "ws": "", "ws_label": r["tag"], "tag": r["tag"], "cmd": r.get("cmd", ""),
        "started": _iso(r["started_ts"]), "finished": "" if running else _iso(end),
        "started_ts": r["started_ts"], "duration": dur, "duration_h": _dur(dur),
        "age_h": int((now - r["started_ts"]) // 3600) if r["started_ts"] else 0,
        "status": r["status"], "rc": r.get("rc"), "pid": r.get("pid"),
        "last_line": r.get("last_line", ""), "note": "; ".join(notes),
        "checked": _iso(checked), "verified_age": vage, "verified_age_h": _dur(vage),
        "stale": bool(r.get("stale")), "active": running,
        "has_log": True, "owns_slot": True, "runner": "pilot",
        "cancellable": bool(r.get("stoppable")) and not r.get("stop_refusal"),
        "stop_refusal": r.get("stop_refusal", ""),
        "source": "pilot-bmc" if bmc else "pilot-local", "log_bytes": r.get("log_bytes", 0),
    }


def rows(cfg: dict, now: float | None = None) -> tuple[list[dict], str]:
    """(rows, error) for the configured window; never raises."""
    if not cfg.get("pilot_jobs", True):
        return [], ""
    if pilot_jobs is None:
        return [], "machine-pilot unavailable"
    now = time.time() if now is None else now
    try:
        recs = pilot_jobs.list_jobs(int(cfg.get("pilot_jobs_days") or 14), now=now)
    except Exception as e:                                   # noqa: BLE001
        return [], f"machine-pilot jobs: {e}"
    return [to_row(r, now) for r in recs], ""


def merge(body: dict, cfg: dict, now: float | None = None) -> dict:
    """Add pilot rows to a vd.payload() body in place (counts, running, stale, targets)."""
    for j in body.get("jobs") or []:
        j.setdefault("source", source_of(j))
        j.setdefault("runner_name", "flash" if j["source"] == "flash" else "mctl")
    extra, err = rows(cfg, now)
    on = bool(cfg.get("pilot_jobs", True))
    body["pilot"] = {"enabled": on, "jobs": len(extra), "error": err,
                     "days": int(cfg.get("pilot_jobs_days") or 14),
                     "bmc": pilot_jobs.bmc_state() if on and pilot_jobs else {}}
    if not extra:
        return body
    jobs = (body.get("jobs") or []) + extra
    jobs.sort(key=lambda j: j.get("started") or "", reverse=True)
    body["jobs"] = jobs
    counts = dict(body.get("counts") or {})
    per = {}
    for j in extra:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
        t = per.setdefault(j["target"], {"jobs": 0, "running": 0, "stale": 0})
        t["jobs"] += 1
        if j["status"] == "running":
            t["running"] += not j["stale"]
            t["stale"] += j["stale"]
    body["counts"] = counts
    running = [j for j in extra if j["status"] == "running" and not j["stale"]]
    stale = [j for j in extra if j["status"] == "running" and j["stale"]]
    body["running"] = (body.get("running") or []) + running
    if isinstance(body.get("stale"), list):
        body["stale"] = body["stale"] + stale
    body["stale_n"] = (body.get("stale_n") or 0) + len(stale)
    for ts in body.get("target_state") or []:
        add = per.get(ts.get("name"))
        if add:
            for k in ("jobs", "running", "stale"):
                ts[k] = (ts.get(k) or 0) + add[k]
    return body


def prune(cfg: dict, now: float | None = None) -> list[str]:
    """Drop finished shared records past ``job_retention_days`` (0 keeps all).

    Runs at most every ``PRUNE_EVERY_S``; running and unresolved records stay.
    """
    now = time.time() if now is None else now
    days = int(cfg.get("job_retention_days") or 0)
    if days <= 0 or pilot_jobs is None or now - _LAST_PRUNE["at"] < PRUNE_EVERY_S:
        return []
    _LAST_PRUNE["at"] = now
    return pilot_jobs.jobs.clean(days, base=pilot_jobs.JOBS_ROOT, now=now)


def snapshot(cfg: dict) -> dict:
    """{id: (status, label)} for running/finished-recently transition notices."""
    out, _ = rows(cfg)
    return {r["id"]: r for r in out}
