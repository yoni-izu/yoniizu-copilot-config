"""Daily maintenance routine: isolated steps, safe restart, rotation, brief shape."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import routine


NOW = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)


class RoutineTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        state = self.root / "state"
        state.mkdir()
        for name in routine.PORTAL_DBS:
            with sqlite3.connect(state / name) as conn:
                conn.execute("CREATE TABLE t(x)")
        owners = self.root / "tasks"
        owners.mkdir()
        with sqlite3.connect(owners / "tasks.db") as conn:
            conn.execute("CREATE TABLE t(x)")
        for name, value in (("STATE_DIR", state), ("BRIEF_PATH", state / "morning-brief.json"),
                            ("SNAPSHOT_DIR", state / "backups" / "daily"),
                            ("OWNER_DB_DIR", owners)):
            patcher = patch.object(routine, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        settings_patch = patch.object(routine.portal_settings, "SETTINGS_PATH",
                                      state / "portal-settings.json")
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.state = state

    def test_failed_step_is_isolated_and_marks_run_failed(self):
        r = routine.Routine(NOW)
        r.run_step("a", "A", lambda: "fine")
        r.run_step("b", "B", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        r.run_step("c", "C", lambda: (_ for _ in ()).throw(routine.StepWarning("hmm")))
        self.assertEqual([s["status"] for s in r.steps], ["ok", "failed", "warn"])
        self.assertIn("boom", r.steps[1]["summary"])
        self.assertEqual(r.brief()["run"]["status"], "failed")

    def test_brief_shape_and_section_order(self):
        r = routine.Routine(NOW)
        r.run_step("a", "A", lambda: "fine")
        r.section("ok1", "Fine", "ok", "all good")
        r.section("bad1", "Bad", "bad", "broken", [{"text": "x", "link": {"k": "pr", "id": 1}}])
        r.section("warn1", "Warn", "warn", "hmm")
        brief = r.brief()
        self.assertEqual(brief["schema"], 1)
        self.assertEqual(brief["run"]["status"], "ok")
        self.assertEqual([s["id"] for s in brief["sections"]], ["bad1", "warn1", "ok1"])
        json.dumps(brief)

    def test_health_restart_skipped_while_action_running(self):
        with patch.object(routine, "healthy", return_value=False), \
                patch.object(routine, "running_actions", return_value=[{"status": "running"}]), \
                patch.object(routine.subprocess, "run") as run:
            with self.assertRaisesRegex(routine.StepWarning, "restart skipped"):
                routine.Routine(NOW).health()
        run.assert_not_called()

    def test_health_never_restarts_when_auto_restart_off(self):
        cfg = routine.portal_settings.validate({"routine": {"auto_restart": False}})
        with patch.object(routine, "healthy", return_value=False), \
                patch.object(routine, "running_actions", return_value=[]), \
                patch.object(routine.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "automatic restart is off"):
                routine.Routine(NOW, cfg).health()
        run.assert_not_called()

    def test_disabled_steps_are_marked_off_and_do_not_degrade_run(self):
        cfg = routine.portal_settings.validate(
            {"routine": {"steps": {"bugs": False, "team": False}}})
        r = routine.Routine(NOW, cfg)
        called = []
        for name in ("health", "queue", "integrity", "reviews", "prs", "bugs", "workspaces",
                     "team", "housekeeping", "ai_brief", "selftest"):
            setattr(r, name, (lambda n: lambda: called.append(n) or "fine")(name))
        brief = r.daily()
        status = {s["id"]: s["status"] for s in brief["steps"]}
        self.assertEqual(status["bugs"], "off")
        self.assertEqual(status["team"], "off")
        self.assertNotIn("bugs", called)
        self.assertIn("prs", called)
        self.assertEqual(brief["run"]["status"], "ok")

    def test_ai_brief_paragraph_success_and_cap_warning(self):
        r = routine.Routine(NOW)
        r.steps = [{"id": "prs", "status": "ok", "summary": "fine", "extra": "x"}]
        posted = {}

        def http(method, path, body=None, **kw):
            posted.update(body)
            return 200, {"job": "j1", "run_id": "ai-1"}
        run = {"id": "ai-1", "answer": "All quiet.", "answer_html": "<p>All quiet.</p>", "model": "m"}
        with patch.object(routine, "http", http), \
                patch.object(routine, "wait_job", return_value={"status": "done"}), \
                patch.object(routine, "api", return_value=run):
            self.assertIn("m", r.ai_brief())
        self.assertEqual(posted["kind"], "brief")
        self.assertEqual(posted["context"]["steps"], [{"id": "prs", "status": "ok", "summary": "fine"}])
        self.assertEqual(r.brief()["ai"]["text"], "All quiet.")
        r2 = routine.Routine(NOW)
        with patch.object(routine, "http", return_value=(429, {"error": "daily AI cap reached"})):
            with self.assertRaisesRegex(routine.StepWarning, "cap"):
                r2.ai_brief()
        self.assertNotIn("ai", r2.brief())

    def test_ai_step_off_in_settings_never_calls_portal(self):
        cfg = routine.portal_settings.validate({"routine": {"steps": {"ai": False}}})
        r = routine.Routine(NOW, cfg)
        for name in ("health", "queue", "integrity", "reviews", "prs", "bugs", "workspaces",
                     "team", "housekeeping", "pulse_snapshot", "selftest"):
            setattr(r, name, lambda: "fine")
        with patch.object(routine, "http", side_effect=AssertionError("called")):
            brief = r.daily()
        self.assertEqual({s["id"]: s["status"] for s in brief["steps"]}["ai"], "off")

    def test_housekeeping_uses_configured_keep_and_retention(self):
        cfg = routine.portal_settings.validate(
            {"routine": {"snapshot_keep": 3, "queue_retention_days": 30}})
        calls = []
        with patch.object(routine, "snapshot_portal_dbs", side_effect=lambda d, k: calls.append(k) or "snap"), \
                patch.object(routine, "api", side_effect=lambda m, p, b=None, **kw: calls.append(b) or {}), \
                patch.object(routine.maintenance, "DB_PATH", self.state / "portal-maintenance.db"), \
                patch.object(routine.subprocess, "run", return_value=Mock(returncode=0)):
            routine.Routine(NOW, cfg).housekeeping()
        self.assertEqual(calls, [3, {"days": 30}])

    def test_health_restarts_idle_unhealthy_portal_once(self):
        with patch.object(routine, "healthy", side_effect=[False, True]), \
                patch.object(routine, "running_actions", return_value=[]), \
                patch.object(routine.time, "sleep"), \
                patch.object(routine.subprocess, "run") as run:
            r = routine.Routine(NOW)
            with self.assertRaisesRegex(routine.StepWarning, "restarted"):
                r.health()
        run.assert_called_once()
        self.assertIn(routine.SERVICE, run.call_args.args[0])

    def test_health_ok_does_nothing(self):
        with patch.object(routine, "healthy", return_value=True), \
                patch.object(routine.subprocess, "run") as run:
            self.assertEqual(routine.Routine(NOW).health(), "portal healthy")
        run.assert_not_called()

    def test_integrity_checks_portal_and_owner_dbs_read_only(self):
        self.assertEqual(routine.Routine(NOW).integrity(), "3 database(s) ok")
        (self.root / "tasks" / "broken.db").write_bytes(b"not a database" * 100)
        r = routine.Routine(NOW)
        with self.assertRaisesRegex(RuntimeError, "broken.db"):
            r.integrity()
        self.assertEqual(r.sections[0]["tone"], "bad")

    def test_snapshots_rotate_daily_dir_only(self):
        cutover = self.state / "backups" / "portal-cutover-keep"
        cutover.mkdir(parents=True)
        for day in range(1, 17):
            routine.snapshot_portal_dbs(f"2026-11-{day:02d}", keep=14)
        days = sorted(p.name for p in routine.SNAPSHOT_DIR.iterdir())
        self.assertEqual(len(days), 14)
        self.assertEqual(days[0], "2026-11-03")
        self.assertTrue(cutover.is_dir())
        with sqlite3.connect(routine.SNAPSHOT_DIR / "2026-11-16" / "action-queue.db") as conn:
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master").fetchone()[0], "t")

    def test_prs_runs_scan_without_jira_and_lists_blocked_features(self):
        calls = []

        def api(method, path, body=None, **kw):
            calls.append((method, path, body))
            if path == "/api/pr-scan":
                return {"job": "j1"}
            return {"features": [
                {"id": 7, "name": "Feat", "complete": False,
                 "blocked": [{"status": "ci-fail"}, {"status": "needs-rebase"}]},
                {"id": 8, "name": "Done", "complete": True, "blocked": [{"status": "ci-fail"}]},
                {"id": 9, "name": "Green", "complete": False, "blocked": []}]}

        with patch.object(routine, "api", side_effect=api), \
                patch.object(routine, "wait_job", return_value={"status": "done", "message": "ok"}):
            r = routine.Routine(NOW)
            r.prs()
        self.assertIn(("POST", "/api/pr-scan", {"jira_sync": False}), calls)
        section = r.sections[0]
        self.assertEqual(section["tone"], "warn")
        self.assertEqual(len(section["items"]), 1)
        self.assertEqual(section["items"][0]["link"], {"k": "pr", "id": 7})
        self.assertIn("CI failing", section["items"][0]["text"])

    def test_reviews_scans_all_and_lists_untracked_requests_without_adding(self):
        def api(method, path, body=None, **kw):
            self.assertEqual((path, body), ("/api/review-scan", {"scope": "all"}))
            return {"align_job": "r1"}

        listing = {"candidates": [
            {"repo": "x", "pr_number": 1, "url": "https://github.com/o/x/pull/1",
             "title": "New", "author": "a", "tracked": None},
            {"repo": "y", "pr_number": 2, "url": "https://github.com/o/y/pull/2",
             "title": "Old", "author": "b", "tracked": "T-9"}]}
        done = subprocess.CompletedProcess([], 0, json.dumps(listing), "")
        with patch.object(routine, "api", side_effect=api), \
                patch.object(routine, "wait_job", return_value={
                    "status": "done", "message": "2 PR(s) checked",
                    "result": {"completed": ["T-3"]}}), \
                patch.object(routine.subprocess, "run", return_value=done) as run:
            r = routine.Routine(NOW)
            r.reviews()
        self.assertIn("scan-reviews", run.call_args.args[0])
        self.assertNotIn("add-review", " ".join(run.call_args.args[0]))
        items = r.sections[0]["items"]
        self.assertEqual(items[0]["link"], {"k": "task", "id": "T-3"})
        self.assertEqual(items[1]["link"], {"href": "https://github.com/o/x/pull/1"})
        self.assertEqual(len(items), 2)

    def test_team_scans_each_member_and_summarises_recent_activity(self):
        roster = {"members": [{"member_key": "m1", "display_name": "Ann"},
                              {"member_key": "m2", "display_name": "Bob"}],
                  "activity": {"members": {
                      "m1": {"observations": [
                          {"kind": "pr", "observed_at": "2026-11-12T01:00:00+00:00"},
                          {"kind": "pr", "observed_at": "2026-11-11T09:00:00+00:00"},
                          {"kind": "jira", "observed_at": "2026-11-01T00:00:00+00:00"}]},
                      "m2": {"observations": []}}}}
        scans = []

        def api(method, path, body=None, **kw):
            self.assertEqual(kw.get("headers"), {"X-Team-Private": "1"})
            if method == "POST":
                scans.append(body["data"]["member_key"])
                return {"result": {"status": "ok"}}
            return roster

        with patch.object(routine, "api", side_effect=api):
            r = routine.Routine(NOW)
            summary = r.team()
        self.assertEqual(scans, ["m1", "m2"])
        self.assertIn("quiet: Bob", summary)
        self.assertEqual(r.sections[0]["items"][0], {"text": "Ann: 2 pr",
                                                      "link": {"k": "member", "id": "m1"}})

    def test_team_partial_scan_reports_failing_source_and_kind(self):
        roster = {"members": [{"member_key": "m1", "display_name": "Ann"}],
                  "activity": {"members": {}}}

        def api(method, path, body=None, **kw):
            if method == "POST":
                return {"result": {"status": "partial", "errors": [
                    {"source": "jira", "kind": "missing-identity", "message": "x"}]}}
            return roster

        with patch.object(routine, "api", side_effect=api):
            r = routine.Routine(NOW)
            with self.assertRaises(routine.StepWarning):
                r.team()
        texts = [i["text"] for i in r.sections[0]["items"]]
        self.assertIn("Scan problem \u2014 Ann: partial (jira: missing-identity)", texts)

    def test_daily_skips_portal_steps_when_portal_down_and_journals(self):
        with patch.object(routine, "healthy", return_value=False), \
                patch.object(routine, "running_actions", return_value=None), \
                patch.object(routine.subprocess, "run",
                             side_effect=subprocess.CalledProcessError(1, "systemctl")), \
                patch.object(routine, "api", side_effect=RuntimeError("down")), \
                patch.object(routine.Routine, "selftest", return_value="ok"), \
                patch.object(routine.maintenance, "record", side_effect=[41, 42]) as record:
            self.assertEqual(routine.run_daily(), 1)
        brief = json.loads(routine.BRIEF_PATH.read_text())
        statuses = {s["id"]: s["status"] for s in brief["steps"]}
        self.assertEqual(statuses["health"], "failed")
        self.assertEqual(statuses["prs"], "skipped")
        self.assertEqual(statuses["selftest"], "ok")
        self.assertEqual(brief["run"]["journal_id"], 41)
        terminal = record.call_args_list[1]
        self.assertEqual(terminal.kwargs["parent"], 41)
        self.assertEqual(terminal.args[1], "failed")

    def test_housekeeping_calls_retention_endpoint(self):
        journal = self.state / "portal-maintenance.db"
        export = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(routine, "api", return_value={"removed": 3, "remaining": 4}) as api, \
                patch.object(routine.maintenance, "DB_PATH", journal), \
                patch.object(routine.subprocess, "run", return_value=export):
            summary = routine.Routine(NOW).housekeeping()
        api.assert_called_once_with("POST", "/api/maintenance/queue-retention",
                                    {"days": 90})
        self.assertIn("removed 3", summary)


    def _pulse_api(self, fail=()):
        payloads = {"/api/tasks": {"tasks": [{"id": "T-1", "status": "todo", "category": "development"}],
                                   "workspaces": {"slots": {}, "roster": []}},
                    "/api/raid": {"rows": [{"rag": "red", "type": "Risk"}]},
                    "/api/actions": {"actions": []}}
        def fake(method, path, *a, **k):
            self.assertEqual(method, "GET")       # the snapshot never mutates
            if path in fail:
                raise RuntimeError("down")
            return payloads.get(path, {})
        return fake

    def test_pulse_snapshot_records_values_in_the_brief(self):
        if not routine.find_node():
            self.skipTest("node unavailable")
        r = routine.Routine(NOW)
        with patch.object(routine, "api", side_effect=self._pulse_api()):
            r.run_step("pulse", "Pulse snapshot", r.pulse_snapshot)
        self.assertEqual(r.steps[-1]["status"], "ok", r.steps[-1])
        values = r.brief()["pulse"]["values"]
        self.assertEqual(values["tasks"], 1)
        self.assertEqual(values["raid.hot"], 1)

    def test_pulse_snapshot_warns_on_missing_source_or_node(self):
        if routine.find_node():
            r = routine.Routine(NOW)
            with patch.object(routine, "api", side_effect=self._pulse_api(fail=("/api/bugs",))):
                r.run_step("pulse", "Pulse snapshot", r.pulse_snapshot)
            self.assertEqual(r.steps[-1]["status"], "warn")
            self.assertIn("unavailable: bugs", r.steps[-1]["summary"])
            self.assertIn("pulse", r.brief())
        r = routine.Routine(NOW)
        with patch.object(routine, "find_node", return_value=None), \
                patch.object(routine, "api") as api:
            r.run_step("pulse", "Pulse snapshot", r.pulse_snapshot)
        api.assert_not_called()
        self.assertEqual(r.steps[-1]["status"], "warn")
        self.assertNotIn("pulse", r.brief())


if __name__ == "__main__":
    unittest.main()
