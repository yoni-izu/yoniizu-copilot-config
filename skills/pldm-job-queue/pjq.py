#!/usr/bin/env python3
"""pjq.py — PLDM Job Queue automation and tracker.

Every action here is deterministic shell work (``octl``, ``gh``) with no model
in the loop, so submitting a job, refreshing its state, and reading back a
failure analysis all cost zero tokens.

The tracker follows a submission through the three artefacts GitHub actually
exposes:

  1. the *dispatch* run   — ``remote_build.yaml`` / ``insert_pldm_job.yaml``,
     printed by octl when it dispatches;
  2. the *queue job id*   — emitted by the ``insert_pldm_job`` step, which is
     the only value that links a submission to its execution;
  3. the *execution* run  — ``pldmJobQ-<edk>-<queue-id>-<epoch>``, created by
     the queue service when an EDK frees up.

Store: ``~/tasks/pldm-jobs.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

STORE = Path(os.path.expanduser(os.environ.get(
    "PJQ_STORE", "~/tasks/pldm-jobs.json"))).expanduser()
CONFIG_DIR = Path(os.path.expanduser("~/tasks/pldm"))
REPO = os.environ.get("PJQ_REPO", "Elmnt-Internal/ip.infra.devops.workflow-entry")
QUEUE_WORKFLOW = os.environ.get("PJQ_QUEUE_WORKFLOW", "PLDM - job queue")
PORTAL_URL = "https://retool.elmnt.local/app/portal/PLDM_job_q"

IMAGE_TYPES = ["1die_w_gmem", "2die_w_gmem", "1die_wo_gmem", "4die_w_gmem"]
DISPATCH_TIMEOUT = 1800

# Non-terminal states are the only ones a scan needs to re-probe.
TERMINAL = {"passed", "failed", "cancelled", "error"}
STAGES = ["dispatching", "building", "queued", "running", "done"]

RUN_URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/(\d+)")
# The insert step prints the queue row it created; the id is the only handle
# that ties a submission to the pldmJobQ run the queue later launches.
QUEUE_ID_RE = re.compile(r"(?:job[_ -]?id|jobId|queue[_ -]?id)\D{0,4}(\d{2,8})", re.I)
# pldmJobQ-pldm-edk4-z2a-2466-1788534750
EXEC_TITLE_RE = re.compile(r"^pldmJobQ-(?P<edk>.+?)-(?P<qid>\d+)-(?P<epoch>\d+)$")
# gtest / pytest / octl summary lines worth keeping out of a 20 MB log.
FAIL_LINE_RE = re.compile(
    r"\[  FAILED  \]|\[==========\]|^FAILED |=+ \d+ failed|\[ERROR\]|"
    r"##\[error\]|^ERROR: |^E {3,}|\w+Error: |assert(?:ion)? fail|"
    r"\bTIMEOUT\b|Traceback|Failed tests:")
# The workflow YAML echoes its own error strings; those are source, not evidence.
FAIL_NOISE_RE = re.compile(r'echo "::error|continue_on_error|max_retries')
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M")  # noqa: E731


# ----------------------------------------------------------------------------
# store
# ----------------------------------------------------------------------------

def load() -> dict:
    if not STORE.exists():
        return {"jobs": [], "last_scan": None}
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"jobs": [], "last_scan": None}
    d.setdefault("jobs", [])
    d.setdefault("last_scan", None)
    return d


def save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STORE)


def _next_id(jobs: list[dict]) -> int:
    return max((j.get("id", 0) for j in jobs), default=0) + 1


def get(ident) -> dict | None:
    d = load()
    s = str(ident)
    for j in d["jobs"]:
        if str(j["id"]) == s or j.get("display_name") == s:
            return j
    return None


def record(**kw) -> dict:
    """Insert a submission into the tracker. Used by the CLI and by the portal
    after it dispatches through its own workspace-alignment chain."""
    d = load()
    job = {
        "id": _next_id(d["jobs"]),
        "display_name": kw.get("display_name") or f"pldm-{datetime.now():%m%d-%H%M}",
        "image_type": kw.get("image_type") or IMAGE_TYPES[0],
        "group": kw.get("group", "default"),
        "timeout": kw.get("timeout", 20),
        "args": kw.get("args", ""),
        "mode": kw.get("mode", "remote"),
        "workspace": kw.get("workspace", ""),
        "feature_id": kw.get("feature_id"),
        "feature": kw.get("feature", ""),
        "dispatch_run": kw.get("dispatch_run"),
        "dispatch_url": kw.get("dispatch_url", ""),
        "queue_job_id": kw.get("queue_job_id", ""),
        "exec_run": None,
        "exec_url": "",
        "edk": "",
        "stage": kw.get("stage", "building"),
        "status": kw.get("status", "building"),
        "submitted": NOW(),
        "updated": NOW(),
        "finished": None,
        "result": {},
        "note": kw.get("note", ""),
        "config": kw.get("config", ""),
        "octl_epoch": kw.get("octl_epoch"),
    }
    d["jobs"].append(job)
    save(d)
    return job


def update(job_id, **kw) -> dict | None:
    d = load()
    for j in d["jobs"]:
        if j["id"] == job_id:
            j.update(kw, updated=NOW())
            save(d)
            return j
    return None


# ----------------------------------------------------------------------------
# shell
# ----------------------------------------------------------------------------

def _gh(args: list[str], timeout: int = 60) -> tuple[bool, str]:
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout if r.returncode == 0 else r.stderr).strip()


def _gh_json(args: list[str], timeout: int = 60):
    ok, out = _gh(args, timeout)
    if not ok:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _octl_bin() -> str:
    return shutil.which("octl") or os.path.expanduser("~/.local/bin/octl") or "octl"


def _ws_root(ws_dir: Path) -> Path:
    """The repo-tool root to run octl from (prefers the `fw` checkout)."""
    if (ws_dir / ".repo").is_dir():
        return ws_dir
    kids = [c for c in sorted(ws_dir.iterdir()) if (c / ".repo").is_dir()] \
        if ws_dir.is_dir() else []
    for c in kids:
        if c.name == "fw":
            return c
    return kids[0] if kids else ws_dir


# ----------------------------------------------------------------------------
# submit
# ----------------------------------------------------------------------------

def write_config(path: Path, display_name: str, tests: list[dict]) -> Path:
    lines = [f'display_name: "{display_name}"', "tests:"]
    for t in tests:
        lines += [
            f'  - image_type: "{t.get("image_type", IMAGE_TYPES[0])}"',
            f'    group: "{t.get("group", "default")}"',
            f'    timeout_minutes: {int(t.get("timeout", 20))}',
            f'    args: "{t.get("args", "")}"',
            f'    extra_flags: "{t.get("extra_flags", "")}"',
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def preflight(ws_root: Path) -> list[str]:
    """Everything octl will reject on, checked before a 30-minute dispatch."""
    problems = []
    for key, want in (("user.name", None), ("user.email", "@elmnt.com")):
        r = subprocess.run(["git", "config", "--get", key], cwd=str(ws_root),
                           capture_output=True, text=True)
        val = r.stdout.strip()
        if not val:
            problems.append(f"git {key} is not set")
        elif want and not val.endswith(want):
            problems.append(f"git {key} {val!r} is not a valid {want} address")
    if not ws_root.is_dir():
        problems.append(f"workspace not found: {ws_root}")
    return problems


def submit(ws_dir: str, *, display_name: str = "", image_type: str = "",
           group: str = "default", timeout: int = 20, args: str = "",
           extra_flags: str = "", mode: str = "remote", from_file: str = "",
           feature_id=None, feature: str = "", dry_run: bool = False) -> tuple[bool, str, dict | None]:
    """Dispatch a PLDM job and record it. Returns (ok, message, job)."""
    ws_root = _ws_root(Path(os.path.expanduser(ws_dir or ".")).resolve())
    bad = preflight(ws_root)
    if bad:
        return False, "; ".join(bad), None

    name = (display_name or f"pldm-{datetime.now():%m%d-%H%M}").strip()
    if from_file:
        cfg = Path(os.path.expanduser(from_file)).resolve()
        if not cfg.is_file():
            return False, f"tests-config not found: {cfg}", None
    else:
        if mode == "local":
            return False, "local mode has no TUI — pass --from-file", None
        cfg = write_config(CONFIG_DIR / f"{name}.yaml", name, [{
            "image_type": image_type or IMAGE_TYPES[0], "group": group,
            "timeout": timeout, "args": args, "extra_flags": extra_flags}])

    cmd = [_octl_bin(), "ws", "build", f"--{mode}", "--target", "pldm",
           "--from-file", str(cfg)]
    if dry_run:
        return True, f"would run: {' '.join(cmd)}  (cwd {ws_root})", None

    try:
        r = subprocess.run(cmd, cwd=str(ws_root), capture_output=True, text=True,
                           timeout=DISPATCH_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"octl dispatch failed: {e}", None

    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        tail = [ln for ln in out.splitlines() if ln.strip()][-1:] or ["see octl output"]
        return False, f"octl exited {r.returncode}: {tail[0][:200]}", None

    m = RUN_URL_RE.search(out)
    job = record(display_name=name, image_type=image_type or IMAGE_TYPES[0],
                 group=group, timeout=timeout, args=args, mode=mode,
                 workspace=str(ws_root), feature_id=feature_id, feature=feature,
                 dispatch_run=int(m.group(1)) if m else None,
                 dispatch_url=m.group(0) if m else "", config=str(cfg),
                 stage="building" if mode == "remote" else "queued",
                 status="building" if mode == "remote" else "queued")
    return True, f"[{job['id']}] {name} dispatched" + (f" — {job['dispatch_url']}"
                                                       if job["dispatch_url"] else ""), job


def track(run: str, **kw) -> dict:
    """Adopt an already-dispatched run (URL or id) into the tracker."""
    m = RUN_URL_RE.search(run)
    rid = int(m.group(1)) if m else int(re.sub(r"\D", "", run))
    return record(dispatch_run=rid,
                  dispatch_url=m.group(0) if m else
                  f"https://github.com/{REPO}/actions/runs/{rid}", **kw)


# ----------------------------------------------------------------------------
# scan — zero-token state refresh
# ----------------------------------------------------------------------------

def _run_state(run_id: int) -> dict | None:
    return _gh_json(["run", "view", str(run_id), "-R", REPO, "--json",
                     "status,conclusion,displayTitle,createdAt,updatedAt,url,jobs"])


def _queue_id_from_dispatch(run_id: int) -> tuple[str, str]:
    """Read the queue row id out of the insert_pldm_job step log.

    Returns (queue_job_id, note). GitHub drops logs after ~90 days, so a miss
    here is expected on old runs and must not be treated as a failure.
    """
    data = _gh_json(["run", "view", str(run_id), "-R", REPO, "--json", "jobs"])
    if not data:
        return "", "dispatch run not readable"
    for j in data.get("jobs", []):
        if "insert_pldm_job" not in j.get("name", ""):
            continue
        log = _job_log(j["databaseId"])
        if not log:
            return "", "insert log unavailable (expired?)"
        hits = QUEUE_ID_RE.findall(log)
        if hits:
            return hits[-1], ""
        return "", "insert log has no queue id"
    return "", "no insert_pldm_job step in this run"


def _find_exec_run(queue_id: str) -> dict | None:
    """Locate the pldmJobQ run the queue launched for this queue row."""
    if not queue_id:
        return None
    runs = _gh_json(["run", "list", "-R", REPO, "-w", QUEUE_WORKFLOW, "-L", "60",
                     "--json", "databaseId,displayTitle,status,conclusion,createdAt,url"])
    for r in runs or []:
        m = EXEC_TITLE_RE.match(r.get("displayTitle", ""))
        if m and m.group("qid") == str(queue_id):
            return dict(r, edk=m.group("edk"))
    return None


def _job_log(job_id: int) -> str:
    """Raw log for one job.

    ``gh run view --job N --log`` returns *empty* for jobs owned by a reusable
    workflow — which is every PLDM execution job — so the REST logs endpoint is
    the only route that actually works here.
    """
    ok, out = _gh(["api", f"/repos/{REPO}/actions/jobs/{job_id}/logs"], timeout=240)
    return out if ok else ""


def _harvest(run_id: int) -> dict:
    """Compact failure analysis: which step broke, and the few log lines that
    say why. Keeps a later read down to a handful of lines instead of a
    multi-megabyte log."""
    data = _gh_json(["run", "view", str(run_id), "-R", REPO, "--json", "jobs"])
    if not data:
        return {}
    bad = [j for j in data.get("jobs", [])
           if j.get("conclusion") not in (None, "success", "skipped")]
    res = {"failed_jobs": [j["name"][:120] for j in bad[:6]], "lines": []}
    for j in bad[:2]:
        steps = [s["name"] for s in j.get("steps", [])
                 if s.get("conclusion") not in (None, "success", "skipped")]
        res.setdefault("failed_steps", []).extend(steps[:4])
        log = _job_log(j["databaseId"])
        if not log:
            continue
        keep = []
        for raw in log.splitlines():
            ln = ANSI_RE.sub("", raw)
            ln = re.sub(r"^\S*\d{4}-\d\d-\d\dT\S+Z ?", "", ln).strip()
            if ln and FAIL_LINE_RE.search(ln) and not FAIL_NOISE_RE.search(ln):
                keep.append(ln[:220])
        res["lines"].extend(keep[-25:])
    res["lines"] = res["lines"][-40:]
    for ln in reversed(res["lines"]):
        if "[==========]" in ln or re.search(r"\d+ failed", ln):
            res["summary"] = ln
            break
    return res


def _scan_one(job: dict) -> dict:
    """Advance one job through dispatch → queue → execution. Pure gh reads."""
    j = dict(job)

    if j.get("stage") == "building" and j.get("dispatch_run"):
        st = _run_state(j["dispatch_run"])
        if st is None:
            j["note"] = "dispatch run not readable via gh"
            return j
        if st["status"] != "completed":
            j["status"] = "building"
            j["note"] = "CI build in progress"
            return j
        if st.get("conclusion") != "success":
            j.update(stage="done", status="error", finished=NOW(),
                     note=f"build {st.get('conclusion')}", result=_harvest(j["dispatch_run"]))
            return j
        qid, note = _queue_id_from_dispatch(j["dispatch_run"])
        j.update(stage="queued", status="queued", queue_job_id=qid or "",
                 note=note or "build green, waiting for an EDK")

    if j.get("stage") in ("queued", "running") and not j.get("exec_run"):
        run = _find_exec_run(j.get("queue_job_id", ""))
        if run:
            j.update(exec_run=run["databaseId"], exec_url=run.get("url", ""),
                     edk=run.get("edk", ""), stage="running", status="running",
                     note="picked up by " + run.get("edk", "an EDK"))
        elif not j.get("queue_job_id"):
            j["note"] = ("no queue id — track it on the Portal: " + PORTAL_URL)

    if j.get("exec_run") and j.get("status") not in TERMINAL:
        st = _run_state(j["exec_run"])
        if st is None:
            j["note"] = "execution run not readable via gh"
        elif st["status"] != "completed":
            j.update(stage="running", status="running", note="test running on " + (j.get("edk") or "EDK"))
        else:
            c = st.get("conclusion")
            j.update(stage="done", finished=NOW(),
                     status={"success": "passed", "cancelled": "cancelled"}.get(c, "failed"),
                     note=f"execution {c}")
            if c != "success":
                j["result"] = _harvest(j["exec_run"])
    j["updated"] = NOW()
    return j


def scan(only=None, workers: int = 5) -> dict:
    """Re-probe every non-terminal job through `gh`. No model, no tokens."""
    d = load()
    todo = [j for j in d["jobs"]
            if (j.get("status") not in TERMINAL)
            and (only is None or str(j["id"]) == str(only) or j.get("display_name") == only)]
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            fresh = {r["id"]: r for r in ex.map(_scan_one, todo)}
        d["jobs"] = [fresh.get(j["id"], j) for j in d["jobs"]]
    d["last_scan"] = NOW()
    save(d)
    out = payload()
    out["scanned"] = len(todo)
    return out


# ----------------------------------------------------------------------------
# discover — surface jobs I posted outside this tool (from the octl log)
# ----------------------------------------------------------------------------
# The PLDM queue itself (retool) is not reachable token-free from here, and its
# GitHub runs are owned by a bot — so neither is a usable "list my jobs" source.
# octl, however, records every dispatch it makes locally: pjq mines that log.

OCTL_LOG = Path(os.path.expanduser(os.environ.get("PJQ_OCTL_LOG", "~/.octl.log"))).expanduser()
OCTL_LOOKBACK_H = int(os.environ.get("PJQ_OCTL_LOOKBACK_H", "72"))
# workflow_id octl stamps on a dispatch, e.g. octl-remote-build-yoni-izu-1788551416
OCTL_DISPATCH_RE = re.compile(r"remote-build-[\w.-]+?-(\d{9,11})$")


def _octl_dispatches(lookback_h: int = OCTL_LOOKBACK_H) -> list[dict]:
    """Parse ~/.octl.log for PLDM dispatches within the lookback window.

    octl emits one JSON object per line. A dispatch is the ``app_build_pldm.go``
    line carrying a ``workflow_id`` of ``*-remote-build-<user>-<epoch>``; the
    preceding ``Checking workspace status`` line on the same file gives the slot.
    """
    if not OCTL_LOG.exists():
        return []
    cutoff = datetime.now().timestamp() - lookback_h * 3600
    out, ws = [], ""
    for line in OCTL_LOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if "app_build_pldm.go" not in line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("workspace"):
            ws = rec["workspace"]
        wid = rec.get("workflow_id", "")
        m = OCTL_DISPATCH_RE.search(wid)
        if not m:
            continue
        epoch = int(m.group(1))
        if epoch < cutoff:
            continue
        out.append({"epoch": epoch, "run_name": wid, "workspace": ws,
                    "mode": "local" if "local" in wid else "remote",
                    "when": datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")})
    return out


def _correlate_run(run_name: str, runs: list[dict]) -> tuple[int | None, str]:
    """Best-effort: find the GitHub run whose title carries this octl run-name.

    The remote build often runs in a project CI repo rather than the queue repo,
    so a miss is normal — the job is still tracked and deep-links to the Portal.
    """
    for r in runs:
        if run_name in r.get("displayTitle", ""):
            return r["databaseId"], f"https://github.com/{REPO}/actions/runs/{r['databaseId']}"
    return None, ""


def discover(lookback_h: int = OCTL_LOOKBACK_H) -> int:
    """Adopt PLDM dispatches found in the octl log that aren't tracked yet.

    Returns the number of newly added jobs. Dedup is by the octl dispatch epoch.
    """
    d = load()
    known = {j.get("octl_epoch") for j in d["jobs"] if j.get("octl_epoch")}
    fresh = [dsp for dsp in _octl_dispatches(lookback_h) if dsp["epoch"] not in known]
    if not fresh:
        return 0
    runs = _gh_json(["run", "list", "-R", REPO, "-a", "-L", "80", "--json",
                     "databaseId,displayTitle"]) or []
    added = 0
    for dsp in fresh:
        slot = Path(dsp["workspace"]).parent.name if dsp["workspace"] else ""
        name = f"octl-{slot or dsp['mode']}-{dsp['when'][-5:]}"
        run_id, run_url = _correlate_run(dsp["run_name"], runs)
        job = record(display_name=name, mode=dsp["mode"], workspace=dsp["workspace"],
                     dispatch_run=run_id, dispatch_url=run_url or PORTAL_URL,
                     stage="building" if run_id else "queued",
                     status="building" if run_id else "queued",
                     note=("posted via octl" if run_id else
                           f"posted via octl @ {dsp['when']} \u2014 open Portal for live status"))
        update(job["id"], octl_epoch=dsp["epoch"], submitted=dsp["when"])
        added += 1
    return added


def portal_scan() -> dict:
    """Entry point for the portal's ↻ Scan queue button: discover + refresh."""
    discover()
    return scan()


# ----------------------------------------------------------------------------
# payload / reporting
# ----------------------------------------------------------------------------

def _age_hours(stamp: str) -> float:
    try:
        return (datetime.now() - datetime.strptime(stamp, "%Y-%m-%d %H:%M")).total_seconds() / 3600
    except (ValueError, TypeError):
        return 0.0


def payload() -> dict:
    d = load()
    jobs = []
    for j in sorted(d["jobs"], key=lambda x: -x["id"]):
        j = dict(j)
        j["age_h"] = round(_age_hours(j.get("submitted", "")), 1)
        j["active"] = j.get("status") not in TERMINAL
        jobs.append(j)
    counts: dict[str, int] = {}
    for j in jobs:
        counts[j.get("status", "?")] = counts.get(j.get("status", "?"), 0) + 1
    return {"jobs": jobs, "counts": counts, "last_scan": d.get("last_scan"),
            "portal": PORTAL_URL, "images": IMAGE_TYPES}


ICON = {"passed": "\u2705", "failed": "\U0001f534", "running": "\U0001f535",
        "queued": "\u23f3", "building": "\U0001f6e0", "cancelled": "\u26ab",
        "error": "\u2715", "pending": "\u26aa"}


def _print(pl: dict) -> None:
    print(f"[pjq] {len(pl['jobs'])} job(s)"
          + (f" \u00b7 last scan {pl['last_scan']}" if pl.get("last_scan") else ""))
    for j in pl["jobs"]:
        print(f"  {ICON.get(j['status'], ' ')} [{j['id']}] {j['display_name']:<28} "
              f"{j['image_type']:<13} {j['stage']:<11} {j['status']:<9} "
              f"{(j.get('edk') or '-'):<14} {j['age_h']}h")
        if j.get("note"):
            print(f"        {j['note']}")


def show(ident) -> int:
    j = get(ident)
    if not j:
        print(f"[pjq] no such job: {ident}")
        return 1
    print(f"[{j['id']}] {j['display_name']}  \u2014 {j['stage']}/{j['status']}")
    for k in ("feature", "image_type", "group", "timeout", "mode", "workspace",
              "queue_job_id", "edk", "submitted", "updated", "finished", "note",
              "dispatch_url", "exec_url", "config"):
        if j.get(k):
            print(f"  {k:<14} {j[k]}")
    res = j.get("result") or {}
    if res.get("summary"):
        print(f"  summary        {res['summary']}")
    for name in res.get("failed_jobs", []):
        print(f"  failed job     {name}")
    for name in res.get("failed_steps", []):
        print(f"  failed step    {name}")
    if res.get("lines"):
        print("  --- log evidence ---")
        for ln in res["lines"]:
            print(f"    {ln}")
    return 0


def bind(ident, queue_id: str = "", exec_run: str = "") -> tuple[bool, str]:
    """Link a job to its queue row or execution run by hand.

    The queue id is normally scraped from the insert step's log, but that log
    expires after ~90 days and local-mode dispatches don't always print it; the
    Portal row always shows the id, so let it be pasted in.
    """
    j = get(ident)
    if not j:
        return False, f"no such job: {ident}"
    kw: dict = {}
    if queue_id:
        kw["queue_job_id"] = str(queue_id).strip()
    if exec_run:
        m = RUN_URL_RE.search(exec_run)
        rid = int(m.group(1)) if m else int(re.sub(r"\D", "", exec_run))
        kw.update(exec_run=rid, stage="running", status="running",
                  exec_url=f"https://github.com/{REPO}/actions/runs/{rid}")
    if not kw:
        return False, "pass --queue-id and/or --exec-run"
    update(j["id"], **kw)
    return True, f"[{j['id']}] {j['display_name']} bound \u2014 rescan to refresh"


def cancel(ident) -> tuple[bool, str]:
    """Cancel the GitHub run backing a job. A row still *waiting* in the queue
    has no run yet — that one can only be cancelled from the Portal."""
    j = get(ident)
    if not j:
        return False, f"no such job: {ident}"
    run = j.get("exec_run") or j.get("dispatch_run")
    if not run:
        return False, f"nothing dispatched yet \u2014 cancel it on {PORTAL_URL}"
    ok, out = _gh(["run", "cancel", str(run), "-R", REPO])
    if ok:
        update(j["id"], status="cancelled", stage="done", finished=NOW(),
               note="cancelled by pjq")
        return True, f"cancelled run {run}"
    return False, out or "gh run cancel failed"


def forget(ident) -> tuple[bool, str]:
    d = load()
    keep = [j for j in d["jobs"] if str(j["id"]) != str(ident)
            and j.get("display_name") != str(ident)]
    if len(keep) == len(d["jobs"]):
        return False, f"no such job: {ident}"
    d["jobs"] = keep
    save(d)
    return True, f"dropped {ident}"


def prune(days: int = 30) -> int:
    d = load()
    before = len(d["jobs"])
    d["jobs"] = [j for j in d["jobs"]
                 if j.get("status") not in TERMINAL or _age_hours(j.get("submitted", "")) < days * 24]
    save(d)
    return before - len(d["jobs"])


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pjq", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit", help="dispatch a PLDM job and track it")
    s.add_argument("workspace", nargs="?", default=".", help="workspace slot dir")
    s.add_argument("-n", "--display-name", default="")
    s.add_argument("-i", "--image-type", default=IMAGE_TYPES[0], choices=IMAGE_TYPES)
    s.add_argument("-g", "--group", default="default")
    s.add_argument("-t", "--timeout", type=int, default=20)
    s.add_argument("-a", "--args", default="")
    s.add_argument("--extra-flags", default="")
    s.add_argument("--local", action="store_true", help="use local build artifacts")
    s.add_argument("--from-file", default="", help="reuse a tests-config YAML")
    s.add_argument("--feature", default="", help="pr-monitor feature name")
    s.add_argument("--feature-id", type=int)
    s.add_argument("--dry-run", action="store_true")

    t = sub.add_parser("track", help="adopt an already-dispatched run")
    t.add_argument("run", help="Actions run URL or id")
    t.add_argument("-n", "--display-name", default="")
    t.add_argument("-i", "--image-type", default=IMAGE_TYPES[0])
    t.add_argument("--feature", default="")

    sc = sub.add_parser("scan", help="refresh every non-terminal job (zero tokens)")
    sc.add_argument("-j", "--job", help="only this job id / display name")

    dc = sub.add_parser("discover", help="adopt PLDM jobs posted via octl (zero tokens)")
    dc.add_argument("-H", "--hours", type=int, default=OCTL_LOOKBACK_H,
                    help="how far back in the octl log to look")

    sub.add_parser("list", help="show tracked jobs")
    sh = sub.add_parser("show", help="details + failure analysis for one job")
    sh.add_argument("job")
    an = sub.add_parser("analyze", help="re-harvest logs for one job")
    an.add_argument("job")
    b = sub.add_parser("bind", help="link a job to its queue row / execution run")
    b.add_argument("job")
    b.add_argument("-q", "--queue-id", default="", help="queue row id from the Portal")
    b.add_argument("-r", "--exec-run", default="", help="pldmJobQ run URL or id")
    c = sub.add_parser("cancel", help="cancel the run backing a job")
    c.add_argument("job")
    f = sub.add_parser("forget", help="drop a job from the tracker")
    f.add_argument("job")
    p = sub.add_parser("prune", help="drop terminal jobs older than N days")
    p.add_argument("-d", "--days", type=int, default=30)
    sub.add_parser("payload", help="emit the portal JSON")
    sub.add_parser("images", help="list known image types")

    a = ap.parse_args(argv)

    if a.cmd == "submit":
        ok, msg, _ = submit(a.workspace, display_name=a.display_name,
                            image_type=a.image_type, group=a.group,
                            timeout=a.timeout, args=a.args,
                            extra_flags=a.extra_flags,
                            mode="local" if a.local else "remote",
                            from_file=a.from_file, feature=a.feature,
                            feature_id=a.feature_id, dry_run=a.dry_run)
        print(f"[pjq] {msg}")
        return 0 if ok else 1
    if a.cmd == "track":
        j = track(a.run, display_name=a.display_name, image_type=a.image_type,
                  feature=a.feature)
        print(f"[pjq] tracking [{j['id']}] {j['display_name']} \u2192 {j['dispatch_url']}")
        return 0
    if a.cmd == "scan":
        pl = scan(a.job)
        _print(pl)
        print(f"\n[pjq] probed {pl['scanned']} active job(s) at {pl['last_scan']}")
        return 0
    if a.cmd == "discover":
        n = discover(a.hours)
        print(f"[pjq] discovered {n} new job(s) from {OCTL_LOG}")
        if n:
            _print(scan())
        return 0
    if a.cmd == "list":
        _print(payload())
        return 0
    if a.cmd == "show":
        return show(a.job)
    if a.cmd == "analyze":
        j = get(a.job)
        if not j:
            print(f"[pjq] no such job: {a.job}")
            return 1
        run = j.get("exec_run") or j.get("dispatch_run")
        if not run:
            print("[pjq] nothing dispatched yet")
            return 1
        update(j["id"], result=_harvest(run))
        return show(a.job)
    if a.cmd == "cancel":
        ok, msg = cancel(a.job)
        print(f"[pjq] {msg}")
        return 0 if ok else 1
    if a.cmd == "bind":
        ok, msg = bind(a.job, a.queue_id, a.exec_run)
        print(f"[pjq] {msg}")
        return 0 if ok else 1
    if a.cmd == "forget":
        ok, msg = forget(a.job)
        print(f"[pjq] {msg}")
        return 0 if ok else 1
    if a.cmd == "prune":
        print(f"[pjq] dropped {prune(a.days)} old job(s)")
        return 0
    if a.cmd == "images":
        for i in IMAGE_TYPES:
            print(i)
        return 0
    print(json.dumps(payload(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
