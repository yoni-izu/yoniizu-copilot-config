#!/usr/bin/env python3
"""Daily Team Portal maintenance and morning brief.

Run by ``portal-maintenance.timer`` (schedule and tunables come from the
portal Settings section, ``state/portal-settings.json``; default 08:00). Every domain refresh goes through
the running portal's HTTP API so the single action-queue worker, the scan locks
and each owner's validation apply exactly as for a button press. The routine
never writes an owner store directly; it only reads owner SQLite files in
read-only mode for an integrity check.

Steps are isolated: one failure is recorded and the run continues. The outcome
is one started/terminal pair in the maintenance journal plus
``state/morning-brief.json`` rendered by the Overview "Morning brief" tile.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

PORTAL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PORTAL_ROOT.parent / "lib"))
sys.path.insert(0, str(PORTAL_ROOT))
from private_paths import private_root  # noqa: E402
import maintenance  # noqa: E402
import portal_settings  # noqa: E402

STATE_DIR = private_root() / "team-portal" / "state"
BRIEF_PATH = STATE_DIR / "morning-brief.json"
SNAPSHOT_DIR = STATE_DIR / "backups" / "daily"
PORTAL_DBS = ("action-queue.db", "portal-maintenance.db")
OWNER_DB_DIR = Path(os.environ.get("ROUTINE_OWNER_DB_DIR", "~/tasks")).expanduser()
TM = PORTAL_ROOT.parent / "skills" / "task-manager" / "tm.py"
BASE_URL = os.environ.get("PORTAL_URL", "http://127.0.0.1:8787")
SERVICE = "task-dashboard.service"
JOB_TIMEOUT = 1800


class StepWarning(Exception):
    """Step finished but needs attention (recorded as ``warn``)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


# --------------------------------------------------------------------- HTTP

def http(method: str, path: str, body: dict | None = None, *, timeout: float = 60,
         headers: dict | None = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE_URL + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    try:
        return status, json.loads(raw or b"null")
    except ValueError:
        return status, {"raw": raw.decode("utf-8", "replace")[:500]}


def api(method: str, path: str, body: dict | None = None, **kw) -> Any:
    status, data = http(method, path, body, **kw)
    if status != 200:
        message = data.get("error") if isinstance(data, dict) else data
        raise RuntimeError(f"{method} {path} -> HTTP {status}: {message}")
    return data


def wait_job(job_id: str, timeout: float = JOB_TIMEOUT, poll: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        job = api("GET", f"/api/align/{job_id}")
        if job.get("status") in ("done", "error", "approval_required"):
            return job
        if time.monotonic() > deadline:
            raise RuntimeError(f"action {job_id} still {job.get('status')} after {int(timeout)}s")
        time.sleep(poll)


def healthy() -> bool:
    try:
        with urllib.request.urlopen(BASE_URL + "/health", timeout=10) as response:
            return response.status == 200 and response.read().strip() == b"ok"
    except (OSError, urllib.error.URLError):
        return False


def running_actions() -> list[dict] | None:
    """Queued/running actions, or None when the queue cannot be read."""
    try:
        data = api("GET", "/api/actions", timeout=10)
    except (OSError, RuntimeError, urllib.error.URLError):
        return None
    return [a for a in data.get("actions") or [] if a.get("status") in ("queued", "running")]


# --------------------------------------------------------------------- steps

class Routine:
    def __init__(self, now: datetime | None = None, settings: dict | None = None):
        self.now = now or utcnow()
        self.cfg = (settings or portal_settings.load())["routine"]
        self.steps: list[dict] = []
        self.sections: list[dict] = []
        self.pulse: dict | None = None
        self.ai: dict | None = None

    def run_step(self, ident: str, label: str, fn: Callable[[], str | None]) -> None:
        started = time.monotonic()
        try:
            summary, status = fn() or "", "ok"
        except StepWarning as warning:
            summary, status = str(warning), "warn"
        except Exception as error:  # noqa: BLE001 - steps are isolated by design
            summary, status = f"{type(error).__name__}: {error}", "failed"
        self.steps.append({"id": ident, "label": label, "status": status,
                           "summary": summary[:600],
                           "duration_s": round(time.monotonic() - started, 1)})
        print(f"[routine] {ident}: {status} {summary}", flush=True)

    def section(self, ident: str, title: str, tone: str, summary: str,
                items: list[dict] | None = None) -> None:
        self.sections.append({"id": ident, "title": title, "tone": tone,
                              "summary": summary, "items": (items or [])[:12]})

    # 1. health ------------------------------------------------------------
    def health(self) -> str:
        if healthy():
            return "portal healthy"
        if not self.cfg["auto_restart"]:
            raise RuntimeError("portal unhealthy; automatic restart is off in Settings")
        active = running_actions()
        if active:
            raise StepWarning(f"portal unhealthy but {len(active)} action(s) running; restart skipped")
        subprocess.run(["systemctl", "--user", "restart", SERVICE], check=True, timeout=60)
        for _ in range(30):
            time.sleep(2)
            if healthy():
                self.section("health", "Portal health", "warn",
                             "Portal was unhealthy and was restarted automatically.")
                raise StepWarning("portal was unhealthy; restarted and healthy again")
        raise RuntimeError("portal unhealthy after automatic restart")

    # 2. action queue ------------------------------------------------------
    def queue(self) -> str:
        actions = api("GET", "/api/actions").get("actions") or []
        since = iso(self.now - timedelta(days=1))
        counts: dict[str, int] = {}
        for action in actions:
            counts[action.get("status", "?")] = counts.get(action.get("status", "?"), 0) + 1
        failed = [a for a in actions if a.get("status") == "error"
                  and (a.get("finished") or "") >= since]
        approvals = [a for a in actions if a.get("status") == "approval_required"]
        stuck = [a for a in actions if a.get("status") == "running"
                 and (a.get("started") or "9999") < iso(self.now - timedelta(hours=6))]
        items = [{"text": f"{a.get('kind')} {a.get('slot') or ''}: {a.get('message') or 'failed'}"[:200],
                  "link": {"k": "section", "id": "work"}} for a in failed + approvals + stuck]
        tone = "bad" if stuck else "warn" if failed or approvals else "ok"
        summary = (f"{len(actions)} stored action(s); {len(failed)} failed in 24 h, "
                   f"{len(approvals)} awaiting approval, {len(stuck)} running > 6 h")
        self.section("queue", "Action queue", tone, summary, items)
        if stuck:
            raise StepWarning(summary)
        return summary

    # 3. integrity ---------------------------------------------------------
    def integrity(self) -> str:
        paths = [STATE_DIR / name for name in PORTAL_DBS]
        paths += sorted(OWNER_DB_DIR.glob("*.db")) if OWNER_DB_DIR.is_dir() else []
        bad = []
        checked = 0
        for path in paths:
            if not path.is_file():
                continue
            checked += 1
            try:
                conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
                try:
                    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
                finally:
                    conn.close()
            except sqlite3.Error as error:
                result = str(error)
            if result != "ok":
                bad.append(f"{path.name}: {result}")
        if bad:
            self.section("integrity", "Database integrity", "bad",
                         f"{len(bad)} of {checked} database(s) failed the integrity check.",
                         [{"text": line} for line in bad])
            raise RuntimeError("; ".join(bad))
        return f"{checked} database(s) ok"

    # 4. reviews -----------------------------------------------------------
    def reviews(self) -> str:
        job = wait_job(api("POST", "/api/review-scan", {"scope": "all"})["align_job"])
        result = job.get("result") or {}
        notes = [job.get("message") or ""]
        items = [{"text": f"{tid} merged \u2192 done", "link": {"k": "task", "id": tid}}
                 for tid in (result.get("completed") or []) if isinstance(tid, str)]
        out = subprocess.run([sys.executable, str(TM), "scan-reviews", "--json"],
                             capture_output=True, text=True, timeout=300, check=False)
        untracked: list[dict] = []
        if out.returncode == 0:
            candidates = json.loads(out.stdout or "{}").get("candidates") or []
            untracked = [c for c in candidates if not c.get("tracked")]
            items += [{"text": f"New request: {c.get('repo')}#{c.get('pr_number')} "
                               f"{c.get('title') or ''} ({c.get('author')})"[:200],
                       "link": {"href": c.get("url")}} for c in untracked]
            notes.append(f"{len(untracked)} untracked review request(s)")
        else:
            notes.append("review discovery failed: " + (out.stderr.strip() or "gh error")[:200])
        tone = "bad" if job.get("status") == "error" else "warn" if untracked else "ok"
        summary = " \u00b7 ".join(n for n in notes if n)
        self.section("reviews", "Reviews", tone, summary, items)
        if job.get("status") == "error" or out.returncode != 0:
            raise StepWarning(summary)
        return summary

    # 5. PRs ---------------------------------------------------------------
    def prs(self) -> str:
        job = wait_job(api("POST", "/api/pr-scan", {"jira_sync": False})["job"])
        data = api("GET", "/api/prs")
        attention = [f for f in data.get("features") or []
                     if not f.get("complete") and f.get("blocked")]
        labels = {"ci-fail": "CI failing", "changes-requested": "changes requested",
                  "needs-rebase": "needs rebase"}
        items = [{"text": f"{f.get('name')}: " + ", ".join(sorted({
                     labels.get(p.get("status"), p.get("status") or "?")
                     for p in f.get("blocked") or []})),
                  "link": {"k": "pr", "id": f.get("id")}} for f in attention]
        summary = (f"{job.get('message') or 'scan finished'} \u00b7 "
                   f"{len(attention)} feature(s) need attention")
        tone = "bad" if job.get("status") == "error" else "warn" if attention else "ok"
        self.section("prs", "My PRs", tone, summary, items)
        if job.get("status") == "error":
            raise StepWarning(summary)
        return summary

    # 6. bugs --------------------------------------------------------------
    def bugs(self) -> str:
        data = api("POST", "/api/bug-scan", timeout=900)
        scan = data.get("scan") or {}

        def count(value: Any) -> int:
            return len(value) if isinstance(value, (list, tuple, set)) else int(value or 0)

        new, changed, closed = (count(scan.get(k)) for k in ("new", "changed", "closed"))
        items = [{"text": f"New: {key}", "link": {"k": "bug", "id": key}}
                 for key in (scan.get("new") if isinstance(scan.get("new"), list) else [])
                 if isinstance(key, str)]
        summary = (f"{count(scan.get('fetched'))} fetched \u00b7 {new} new \u00b7 "
                   f"{changed} changed \u00b7 {closed} closed")
        self.section("bugs", "HSI bugs", "warn" if new else "ok", summary,
                     items or ([{"text": "Open the bug list", "link": {"k": "section", "id": "bugs"}}]
                               if new or changed else []))
        return summary

    # 7. workspaces --------------------------------------------------------
    def workspaces(self) -> str:
        job = wait_job(api("POST", "/api/ws-refresh")["align_job"])
        rows = job.get("repos") or []
        dirty = [r for r in rows if r.get("detail") != "clean"]
        items = [{"text": f"{r.get('name')}: {r.get('detail')}"[:200],
                  "link": {"k": "section", "id": "work"}} for r in dirty]
        summary = f"{len(rows)} local workspace(s) checked \u00b7 {len(dirty)} not at a clean baseline"
        tone = "bad" if job.get("status") == "error" else "warn" if dirty else "ok"
        self.section("workspaces", "Local workspaces", tone, summary, items)
        if job.get("status") == "error":
            raise StepWarning(job.get("message") or summary)
        return summary

    # 8. team activity -----------------------------------------------------
    def team(self) -> str:
        private = {"X-Team-Private": "1"}
        roster = api("GET", "/api/team?audience=private", headers=private)
        members = {m["member_key"]: m.get("display_name") or m["member_key"]
                   for m in roster.get("members") or [] if m.get("member_key")}
        failed = []
        for key in members:
            try:
                result = api("POST", "/api/team", {"action": "activity-scan",
                                                   "data": {"member_key": key}},
                             headers=private, timeout=600)["result"]
                if result.get("status") not in ("ok", "busy"):
                    why = ", ".join(f"{e.get('source')}: {e.get('kind')}"
                                    for e in result.get("errors") or [])
                    failed.append(f"{members[key]}: {result.get('status')}"
                                  + (f" ({why})" if why else ""))
            except (RuntimeError, OSError) as error:
                failed.append(f"{members[key]}: {error}")
        activity = (api("GET", "/api/team?audience=private", headers=private)
                    .get("activity") or {}).get("members") or {}
        since = iso(self.now - timedelta(hours=self.cfg["activity_window_hours"]))
        items, quiet = [], []
        for key, name in members.items():
            fresh = [o for o in (activity.get(key) or {}).get("observations") or []
                     if (o.get("observed_at") or "") >= since]
            if not fresh:
                quiet.append(name)
                continue
            kinds: dict[str, int] = {}
            for obs in fresh:
                kinds[obs.get("kind") or "activity"] = kinds.get(obs.get("kind") or "activity", 0) + 1
            items.append({"text": f"{name}: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())),
                          "link": {"k": "member", "id": key}})
        summary = (f"{len(members)} member(s) scanned \u00b7 {len(items)} active in "
                   f"{self.cfg['activity_window_hours']} h"
                   + (f" \u00b7 quiet: {', '.join(quiet)}" if quiet else "")
                   + (f" \u00b7 {len(failed)} scan problem(s)" if failed else ""))
        items += [{"text": f"Scan problem \u2014 {line}"[:200]} for line in failed]
        self.section("team", "Team activity", "warn" if failed else "ok", summary, items)
        if failed:
            raise StepWarning(summary)
        return summary

    # 9. housekeeping ------------------------------------------------------
    def housekeeping(self) -> str:
        notes = [snapshot_portal_dbs(self.now.date().isoformat(), self.cfg["snapshot_keep"])]
        result = api("POST", "/api/maintenance/queue-retention",
                     {"days": self.cfg["queue_retention_days"]})
        notes.append(f"queue retention removed {result.get('removed', 0)}, kept {result.get('remaining', 0)}")
        conn = sqlite3.connect(maintenance.DB_PATH, timeout=30)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()
        notes.append("journal vacuumed")
        out = subprocess.run([sys.executable, str(PORTAL_ROOT / "portal.py"), "dashboard"],
                             capture_output=True, text=True, timeout=300, check=False)
        if out.returncode != 0:
            raise RuntimeError("static export failed: " + (out.stderr.strip() or out.stdout.strip())[-300:])
        notes.append("static export refreshed")
        return " \u00b7 ".join(notes)

    # 10. pulse snapshot ---------------------------------------------------
    PULSE_SOURCES = {"bugs": "/api/bugs", "triage": "/api/triage", "prs": "/api/prs",
                     "raid": "/api/raid", "vmlog": "/api/vm-jobs", "team": "/api/team",
                     "settings": "/api/settings"}

    def pulse_snapshot(self) -> str:
        """Freeze the Overview pulse numbers so the page can show what moved
        since this brief. Uses the same pure module the browser runs."""
        node = find_node()
        if not node:
            raise StepWarning("node not found; Overview deltas unavailable until the next run")
        tasks = api("GET", "/api/tasks")
        data: dict[str, Any] = {"tasks": tasks.get("tasks") or [],
                                "workspaces": tasks.get("workspaces") or {}}
        missing = []
        for key, path in self.PULSE_SOURCES.items():
            try:
                data[key] = api("GET", path)
            except Exception:  # noqa: BLE001 - a missing source just drops its card values
                missing.append(key)
        data["actions"] = (api("GET", "/api/actions").get("actions") or [])
        out = subprocess.run([node, str(PORTAL_ROOT / "assets" / "pulse.js"), "--snapshot"],
                             input=json.dumps(data), capture_output=True, text=True,
                             timeout=120, check=False)
        if out.returncode != 0:
            raise RuntimeError("pulse.js failed: " + (out.stderr.strip() or out.stdout.strip())[-300:])
        self.pulse = json.loads(out.stdout)
        summary = f"{len(self.pulse.get('values') or {})} Overview values recorded"
        if missing:
            raise StepWarning(summary + "; unavailable: " + ", ".join(missing))
        return summary

    # 10b. AI paragraph --------------------------------------------------
    def ai_brief(self) -> str:
        """Ask the portal's AI for the opening paragraph, from this brief only."""
        context = {"date": self.now.date().isoformat(),
                   "steps": [{k: s.get(k) for k in ("id", "status", "summary")}
                             for s in self.steps],
                   "sections": self.sections}
        status, data = http("POST", "/api/ai/run", {"kind": "brief", "context": context})
        if status in (400, 429):
            raise StepWarning((data or {}).get("error") or f"HTTP {status}")
        if status != 200:
            raise RuntimeError(f"POST /api/ai/run -> HTTP {status}: {(data or {}).get('error')}")
        job = wait_job(data["job"], timeout=900)
        run = api("GET", f"/api/ai/run/{data['run_id']}")
        if job.get("status") != "done" or not run.get("answer"):
            raise StepWarning("AI paragraph unavailable: " + (run.get("error") or job.get("message") or "no answer"))
        self.ai = {"run_id": run["id"], "text": run["answer"], "html": run.get("answer_html", ""),
                   "model": run.get("model"), "premium": run.get("premium", 0)}
        return f"AI paragraph written ({run.get('model') or 'model'})"

    # 11. self-test --------------------------------------------------------
    def selftest(self) -> str:
        tests = PORTAL_ROOT / "tests"
        node = find_node()
        # Package-qualified names: plain discovery clashes with the same-named
        # test_review_drafts module in skills/task-manager once portal is imported.
        modules = sorted(f"tests.{p.stem}" for p in tests.glob("test_*.py"))
        runs = [[sys.executable, "-m", "unittest", *modules]]
        if node:
            runs.append([node, "--test", *sorted(str(p) for p in tests.glob("*.js"))])
        failures = []
        for cmd in runs:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                                 cwd=PORTAL_ROOT, check=False)
            if out.returncode != 0:
                failures.append(f"{Path(cmd[0]).name}: " + (out.stderr or out.stdout).strip()[-300:])
        if failures:
            self.section("selftest", "Portal self-test", "bad", "Regression tests failed.",
                         [{"text": line} for line in failures])
            raise RuntimeError("; ".join(failures))
        if not node:
            raise StepWarning("python portal tests passed; node not found, JS tests skipped")
        return "python and JS portal tests passed"

    def daily(self) -> dict:
        self.run_step("health", "Health", self.health)
        self.run_step("queue", "Action queue", self.queue)
        self.run_step("integrity", "Integrity", self.integrity)
        portal_up = self.steps[0]["status"] != "failed"
        for ident, label, fn in (("reviews", "Reviews", self.reviews),
                                 ("prs", "PRs", self.prs),
                                 ("bugs", "Bugs", self.bugs),
                                 ("workspaces", "Workspaces", self.workspaces),
                                 ("team", "Team activity", self.team),
                                 ("housekeeping", "Housekeeping", self.housekeeping)):
            if ident in self.cfg["steps"] and not self.cfg["steps"][ident]:
                self.steps.append({"id": ident, "label": label, "status": "off",
                                   "summary": "turned off in Settings", "duration_s": 0})
            elif portal_up:
                self.run_step(ident, label, fn)
            else:
                self.steps.append({"id": ident, "label": label, "status": "skipped",
                                   "summary": "portal unavailable", "duration_s": 0})
        if portal_up:
            self.run_step("pulse", "Pulse snapshot", self.pulse_snapshot)
        else:
            self.steps.append({"id": "pulse", "label": "Pulse snapshot", "status": "skipped",
                               "summary": "portal unavailable", "duration_s": 0})
        if not self.cfg["steps"].get("ai", True):
            self.steps.append({"id": "ai", "label": "AI summary", "status": "off",
                               "summary": "turned off in Settings", "duration_s": 0})
        elif portal_up:
            self.run_step("ai", "AI summary", self.ai_brief)
        else:
            self.steps.append({"id": "ai", "label": "AI summary", "status": "skipped",
                               "summary": "portal unavailable", "duration_s": 0})
        self.run_step("selftest", "Self-test", self.selftest)
        return self.brief()

    def brief(self) -> dict:
        states = {s["status"] for s in self.steps}
        status = "failed" if "failed" in states else "degraded" if states & {"warn", "skipped"} else "ok"
        order = {"bad": 0, "warn": 1, "ok": 2}
        out = {"schema": 1, "generated_at": iso(utcnow()),
               "run": {"started_at": iso(self.now), "finished_at": iso(utcnow()), "status": status},
               "steps": self.steps,
               "sections": sorted(self.sections, key=lambda s: order.get(s["tone"], 3))}
        if self.pulse:
            out["pulse"] = self.pulse
        if self.ai:
            out["ai"] = self.ai
        return out


# --------------------------------------------------------------------- helpers

def find_node() -> str | None:
    """``node`` on PATH, else the newest nvm install (systemd has no nvm PATH)."""
    found = shutil.which("node")
    if found:
        return found
    installs = sorted(Path("~/.nvm/versions/node").expanduser().glob("v*/bin/node"),
                      key=lambda p: tuple(int(x) for x in p.parts[-3].lstrip("v").split(".")
                                          if x.isdigit()))
    return str(installs[-1]) if installs else None


def snapshot_portal_dbs(day: str, keep: int = 14) -> str:
    """Consistent sqlite backups of the portal-owned DBs; rotate to ``keep`` days.

    Only ``backups/daily/`` is rotated; cutover/migration backups are untouched.
    """
    target = SNAPSHOT_DIR / day
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in PORTAL_DBS:
        source_path = STATE_DIR / name
        if not source_path.is_file():
            continue
        source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30)
        dest = sqlite3.connect(target / name)
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        copied += 1
    days = sorted(p for p in SNAPSHOT_DIR.iterdir() if p.is_dir() and len(p.name) == 10)
    for old in days[:-keep] if keep > 0 else []:
        shutil.rmtree(old)
    return f"{copied} portal DB snapshot(s) in backups/daily/{day} (keeping {keep})"


def write_brief(brief: dict) -> None:
    BRIEF_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BRIEF_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, BRIEF_PATH)


def run_daily() -> int:
    journal_id = maintenance.record(
        "Daily portal maintenance run (timer)", "started", component="portal-routine")
    routine = Routine()
    try:
        brief = routine.daily()
    except Exception as error:  # noqa: BLE001 - the journal must always close
        maintenance.record(f"Daily maintenance aborted: {error}", "failed",
                           component="portal-routine", parent=journal_id)
        raise
    brief["run"]["journal_id"] = journal_id
    write_brief(brief)
    lines = [f"{s['id']}={s['status']}: {s['summary']}" for s in brief["steps"]]
    status = brief["run"]["status"]
    maintenance.record(
        f"Daily maintenance {status}", "failed" if status == "failed" else "completed",
        component="portal-routine", parent=journal_id,
        validation="; ".join(lines)[:4000],
        deployment="no deployment; brief written to state/morning-brief.json")
    return 1 if status == "failed" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("daily", help="run every maintenance step and write the morning brief")
    sub.add_parser("brief", help="print the latest morning brief")
    args = parser.parse_args(argv)
    if args.cmd == "brief":
        print(BRIEF_PATH.read_text(encoding="utf-8") if BRIEF_PATH.is_file() else "no brief yet")
        return 0
    return run_daily()


if __name__ == "__main__":
    raise SystemExit(main())
