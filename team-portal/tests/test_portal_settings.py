"""Portal settings: validation, revision checks, timer drop-in apply and rollback."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal_settings as ps


def ok(*args, **kw):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.dropin = root / "systemd" / "portal-maintenance.timer.d" / "schedule.conf"
        for name, value in (("SETTINGS_PATH", root / "state" / "portal-settings.json"),
                            ("BRIEF_PATH", root / "state" / "morning-brief.json"),
                            ("DROPIN", self.dropin)):
            patcher = patch.object(ps, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.calls = []
        patcher = patch.object(ps, "systemctl",
                               side_effect=lambda *a, **kw: self.calls.append(a) or ok())
        patcher.start()
        self.addCleanup(patcher.stop)

    def rev(self):
        return ps.payload(include_status=False)["revision"]

    def test_defaults_when_no_file(self):
        self.assertEqual(ps.load(), ps.DEFAULTS)
        self.assertEqual(ps.load()["routine"]["time"], "08:00")
        self.assertEqual(ps.load()["ai"]["interactive_daily_cap"], 150)

    def test_validation_rejects_bad_values(self):
        for bad in ({"routine": {"time": "8:00"}}, {"routine": {"time": "24:00"}},
                    {"routine": {"enabled": "yes"}}, {"routine": {"snapshot_keep": 0}},
                    {"routine": {"queue_retention_days": 3}}, {"routine": {"bogus": 1}},
                    {"routine": {"steps": {"health": False}}}, {"other": {}},
                    {"routine": {"activity_window_hours": True}}, [],
                    {"routine": {"days": []}}, {"routine": {"days": ["Mon", "Mon"]}},
                    {"routine": {"days": ["Monday"]}}, {"routine": {"days": "Mon"}},
                    {"routine": {"weekdays_only": True}},
                    {"ai": {"interactive_daily_cap": -1}},
                    {"ai": {"interactive_daily_cap": 5000}}):
            with self.assertRaises(ps.SettingsError, msg=bad):
                ps.save(bad, self.rev())
        self.assertFalse(ps.SETTINGS_PATH.exists())

    def test_time_change_writes_dropin_and_rearms_timer(self):
        result = ps.save({"routine": {"time": "06:30",
                                      "days": ["Thu", "Sun", "Mon", "Tue", "Wed"]}}, self.rev())
        self.assertIn("routine.time: \"08:00\" -> \"06:30\"", result["changed"])
        self.assertIn("OnCalendar=\nOnCalendar=Sun,Mon,Tue,Wed,Thu *-*-* 06:30:00\n",
                      self.dropin.read_text())
        self.assertEqual(self.calls, [("daemon-reload",), ("enable", ps.TIMER), ("restart", ps.TIMER)])
        self.assertEqual(ps.load()["routine"]["time"], "06:30")
        self.assertEqual(result["revision"], self.rev())

    def test_days_default_to_every_day_and_weekend_runs_can_be_restored(self):
        self.assertEqual(ps.on_calendar(ps.load()["routine"]), "*-*-* 08:00:00")
        ps.save({"routine": {"days": ["Sat"]}}, self.rev())
        self.assertIn("OnCalendar=Sat *-*-* 08:00:00\n", self.dropin.read_text())
        ps.save({"routine": {"days": list(reversed(ps.WEEK_DAYS))}}, self.rev())
        self.assertEqual(ps.load()["routine"]["days"], list(ps.WEEK_DAYS))
        self.assertIn("OnCalendar=*-*-* 08:00:00\n", self.dropin.read_text())

    def test_disable_turns_timer_off(self):
        ps.save({"routine": {"enabled": False}}, self.rev())
        self.assertEqual(self.calls[-1], ("disable", "--now", ps.TIMER))

    def test_non_schedule_change_keeps_timer_once_dropin_exists(self):
        ps.save({"routine": {"time": "07:00"}}, self.rev())
        self.calls.clear()
        result = ps.save({"routine": {"snapshot_keep": 5, "steps": {"team": False}}}, self.rev())
        self.assertEqual(self.calls, [])
        self.assertEqual(result["applied"], "schedule unchanged")
        cfg = ps.load()["routine"]
        self.assertEqual((cfg["snapshot_keep"], cfg["steps"]["team"], cfg["steps"]["bugs"]),
                         (5, False, True))

    def test_stale_revision_conflicts(self):
        stale = self.rev()
        ps.save({"routine": {"time": "07:00"}}, stale)
        with self.assertRaises(ps.RevisionConflict):
            ps.save({"routine": {"time": "09:00"}}, stale)
        self.assertEqual(ps.load()["routine"]["time"], "07:00")

    def test_no_op_save_writes_nothing(self):
        result = ps.save({"routine": {"time": "08:00"}}, self.rev())
        self.assertEqual(result["changed"], [])
        self.assertFalse(ps.SETTINGS_PATH.exists())
        self.assertEqual(self.calls, [])

    def test_apply_failure_rolls_back_settings(self):
        ps.save({"routine": {"time": "07:00"}}, self.rev())
        before = ps.SETTINGS_PATH.read_bytes()

        def fail_restart(*args, **kw):
            self.calls.append(args)
            return SimpleNamespace(returncode=1 if args[0] == "restart" and "05:00" in
                                   self.dropin.read_text() else 0, stdout="", stderr="boom")
        with patch.object(ps, "systemctl", side_effect=fail_restart):
            with self.assertRaisesRegex(ps.ApplyError, "rolled back"):
                ps.save({"routine": {"time": "05:00"}}, self.rev())
        self.assertEqual(ps.SETTINGS_PATH.read_bytes(), before)
        self.assertIn("07:00", self.dropin.read_text())

    def test_corrupt_file_falls_back_and_is_reported(self):
        ps.SETTINGS_PATH.parent.mkdir(parents=True)
        ps.SETTINGS_PATH.write_text("{broken")
        self.assertEqual(ps.load(), ps.DEFAULTS)
        with patch.object(ps, "_linger", return_value="no"):
            self.assertIn("defaults in effect", ps.status()["problem"])

    def test_status_reports_timer_linger_and_last_run(self):
        ps.BRIEF_PATH.parent.mkdir(parents=True)
        ps.BRIEF_PATH.write_text(json.dumps({"generated_at": "2026-11-15T18:22:00+00:00",
                                             "run": {"status": "ok", "journal_id": 7}}))
        show = SimpleNamespace(returncode=0, stderr="", stdout=(
            "ActiveState=active\nUnitFileState=enabled\n"
            "NextElapseUSecRealtime=Mon 2026-11-16 08:00:00 IST\nLastTriggerUSec=n/a\n"))
        with patch.object(ps, "systemctl", return_value=show), \
                patch.object(ps, "_linger", return_value="no"):
            status = ps.status()
        self.assertEqual(status["timer"]["next"], "Mon 2026-11-16 08:00:00 IST")
        self.assertEqual(status["last_run"]["journal_id"], 7)
        self.assertEqual(status["linger"], "no")


if __name__ == "__main__":
    unittest.main()
