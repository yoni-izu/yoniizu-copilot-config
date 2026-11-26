"""Shared job records in the Machines view: merge, ids, stop/log routing, retention, settings."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs_view  # noqa: E402
import portal  # noqa: E402
import portal_settings as ps  # noqa: E402
from test_portal_cancel import handler  # noqa: E402

NOW = 1_700_000_000.0
CFG = {"pilot_jobs": True, "pilot_jobs_days": 14}


def rec(ident, status="running", target="srv12", source="local", **kw):
    r = {"id": ident, "tag": ident.split(":")[-1], "source": source, "status": status,
         "target": target, "target_how": "recorded" if target else "none",
         "started_ts": NOW - 3700, "finished_ts": None if status == "running" else NOW - 100,
         "rc": None if status == "running" else 0, "pid": 42, "cmd": "sleep 300",
         "last_line": "tick", "checked_ts": NOW - 5, "stale": False, "stoppable": status == "running",
         "stop_refusal": "", "log_bytes": 10}
    r.update(kw)
    return r


def fake_pilot(records, bmc=None):
    return SimpleNamespace(list_jobs=Mock(return_value=records), bmc_state=lambda: bmc or {},
                           find=lambda i: next((r for r in records if r["id"] == i), None),
                           stop=Mock(return_value={"result": "STOPPED"}),
                           log_view=Mock(return_value={"mode": "local", "command": "tail -n 5 /x/log",
                                                       "title": "tail srv12:localjob t",
                                                       "running": False, "target": "srv12"}))


class MergeTests(unittest.TestCase):
    def body(self):
        return {"jobs": [{"id": "d1", "status": "running", "target": "srv12",
                          "started": "2023-11-14T20:00:00"}],
                "counts": {"running": 1}, "running": [{"id": "d1"}], "stale_n": 0,
                "target_state": [{"name": "srv12", "jobs": 1, "running": 1, "stale": 0}]}

    def test_rows_merge_into_counts_targets_and_running(self):
        recs = [rec("pj:L:a"), rec("pj:L:b", "failed", target=""),
                rec("pj:B:srv12:c", source="bmc", stale=True, stoppable=False)]
        with patch.object(jobs_view, "pilot_jobs", fake_pilot(recs)):
            body = jobs_view.merge(self.body(), CFG, NOW)
        by = {j["id"]: j for j in body["jobs"]}
        self.assertEqual((by["d1"]["source"], by["d1"]["runner_name"]), ("delegated", "mctl"))
        self.assertEqual((by["job:a"]["source"], by["job:a"]["kind"], by["job:a"]["duration_h"]),
                         ("pilot-local", "job", "1h 01m"))
        self.assertEqual((by["job:a"]["job_id"], by["job:a"]["runner_name"]), ("a", "pilot"))
        self.assertTrue(by["job:a"]["cancellable"])
        self.assertEqual((by["job:b"]["target"], by["job:b"]["attributed"]),
                         (jobs_view.UNATTRIBUTED, False))
        self.assertEqual((by["pj:B:srv12:c"]["kind"], by["pj:B:srv12:c"]["dest"]), ("bmcjob", "bmc"))
        self.assertFalse(by["pj:B:srv12:c"]["cancellable"])
        self.assertEqual(body["counts"], {"running": 3, "failed": 1})
        self.assertEqual([j["id"] for j in body["running"]], ["d1", "job:a"])
        self.assertEqual(body["stale_n"], 1)
        self.assertEqual(body["target_state"][0], {"name": "srv12", "jobs": 3, "running": 2, "stale": 1})
        self.assertEqual((body["pilot"]["jobs"], body["pilot"]["days"]), (3, 14))

    def test_flash_rows_and_adhoc_runners_are_labelled(self):
        body = self.body()
        body["jobs"].append({"id": "f1", "status": "passed", "runner": "local",
                             "target": "srv22", "started": "2023-11-14T19:00:00"})
        with patch.object(jobs_view, "pilot_jobs", fake_pilot([rec("pj:L:x", runner="adhoc")])):
            body = jobs_view.merge(body, CFG, NOW)
        by = {j["id"]: j for j in body["jobs"]}
        self.assertEqual((by["f1"]["source"], by["f1"]["runner_name"]), ("flash", "flash"))
        self.assertEqual(by["job:x"]["runner_name"], "adhoc")

    def test_job_ids_map_to_the_owner_and_legacy_ids_pass(self):
        self.assertTrue(jobs_view.is_pilot_id("job:a") and jobs_view.is_pilot_id("pj:L:a"))
        self.assertFalse(jobs_view.is_pilot_id("d1"))
        self.assertEqual(jobs_view.pilot_id("job:a"), "pj:L:a")
        self.assertEqual(jobs_view.pilot_id("pj:B:srv12:c"), "pj:B:srv12:c")

    def test_refused_command_is_not_cancellable(self):
        r = rec("pj:L:f", stop_refusal="flashing: refuse")
        self.assertFalse(jobs_view.to_row(r, NOW)["cancellable"])

    def test_disabled_or_broken_owner_leaves_payload_alone(self):
        with patch.object(jobs_view, "pilot_jobs", fake_pilot([rec("pj:L:a")])):
            body = jobs_view.merge(self.body(), dict(CFG, pilot_jobs=False), NOW)
        self.assertEqual(len(body["jobs"]), 1)
        broken = SimpleNamespace(list_jobs=Mock(side_effect=OSError("boom")), bmc_state=dict)
        with patch.object(jobs_view, "pilot_jobs", broken):
            body = jobs_view.merge(self.body(), CFG, NOW)
        self.assertEqual((len(body["jobs"]), body["counts"]), (1, {"running": 1}))
        self.assertIn("boom", body["pilot"]["error"])


class StopTests(unittest.TestCase):
    def test_start_routes_pilot_ids_and_refuses_unsafe_states(self):
        recs = [rec("pj:L:a"), rec("pj:L:done", "passed"), rec("pj:L:stale", stale=True),
                rec("pj:L:flash", stop_refusal="flash in progress"), rec("pj:L:anon", target="")]
        queued = []
        with patch.object(jobs_view, "pilot_jobs", fake_pilot(recs)), \
                patch.object(portal, "vd", None), \
                patch.object(portal, "enqueue_action", lambda job: queued.append(job) or "a1"):
            self.assertEqual(portal.start_vm_cancel_job("job:a"), "a1")
            self.assertEqual(portal.start_vm_cancel_job("pj:L:anon"), "a1")
            with self.assertRaises(LookupError):
                portal.start_vm_cancel_job("pj:L:nope")
            for ident, why in (("pj:L:done", "not running"), ("pj:L:stale", "unverified"),
                               ("pj:L:flash", "flash in progress")):
                with self.assertRaisesRegex(ValueError, why):
                    portal.start_vm_cancel_job(ident)
        self.assertEqual((queued[0]["kind"], queued[0]["vm_job"], queued[0]["target"]),
                         ("vm-cancel", "job:a", "srv12"))
        self.assertEqual(portal.action_resources(queued[0]), {"domain:delegation", "vm:srv12"})
        self.assertEqual(queued[1]["target"], jobs_view.UNATTRIBUTED)

    def test_run_goes_through_the_owner_and_reports(self):
        fake = fake_pilot([])
        job = {"kind": "vm-cancel", "vm_job": "job:a", "target": "srv12"}
        with patch.object(jobs_view, "pilot_jobs", fake), \
                patch.object(portal, "observe_pilot_jobs"), \
                patch.object(portal.EVENTS, "publish_domains"):
            portal.run_pilot_stop(job)
            fake.stop.assert_called_once_with("pj:L:a")
            self.assertEqual((job["status"], job["message"]), ("done", "stopped"))
            fake.stop.return_value = {"error": "NOTOURS pid=42"}
            portal.run_pilot_stop(job)
        self.assertEqual(job["status"], "error")
        self.assertIn("NOTOURS", job["message"])

    def test_endpoint_accepts_pilot_ids_without_vm_delegate(self):
        with patch.object(portal, "vd", None), \
                patch.object(portal, "start_vm_cancel_job", return_value="a1") as s:
            status, _ = handler("/api/vm-cancel", {"id": "pj:L:a"}).do_POST()
            self.assertEqual(status, 200)
            s.assert_called_once_with("pj:L:a")
            status, _ = handler("/api/vm-cancel", {"id": "j1"}).do_POST()
            self.assertEqual(status, 503)


class LogTests(unittest.TestCase):
    def test_pilot_log_goes_through_the_owner(self):
        fake = fake_pilot([])
        with patch.object(jobs_view, "pilot_jobs", fake), patch.object(portal, "vd", None):
            spec = portal.term_spec({"kind": "log", "job": "job:t"})
        fake.log_view.assert_called_once_with("pj:L:t")
        self.assertEqual(spec["argv"], ["bash", "-c", "tail -n 5 /x/log"])


class RetentionTests(unittest.TestCase):
    def setUp(self):
        jobs_view._LAST_PRUNE["at"] = 0.0
        self.addCleanup(jobs_view._LAST_PRUNE.update, at=0.0)

    def fake(self):
        return SimpleNamespace(JOBS_ROOT=Path("/r"),
                               jobs=SimpleNamespace(clean=Mock(return_value=["old"])))

    def test_prunes_with_the_setting_and_throttles(self):
        fake = self.fake()
        with patch.object(jobs_view, "pilot_jobs", fake):
            self.assertEqual(jobs_view.prune({"job_retention_days": 30}, NOW), ["old"])
            self.assertEqual(jobs_view.prune({"job_retention_days": 30}, NOW + 60), [])
            jobs_view.prune({"job_retention_days": 30}, NOW + jobs_view.PRUNE_EVERY_S)
        fake.jobs.clean.assert_called_with(30, base=Path("/r"), now=NOW + jobs_view.PRUNE_EVERY_S)
        self.assertEqual(fake.jobs.clean.call_count, 2)

    def test_zero_keeps_everything(self):
        fake = self.fake()
        with patch.object(jobs_view, "pilot_jobs", fake):
            self.assertEqual(jobs_view.prune({"job_retention_days": 0}, NOW), [])
        fake.jobs.clean.assert_not_called()


class SettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = patch.object(ps, "SETTINGS_PATH", Path(tmp.name) / "s.json")
        p.start()
        self.addCleanup(p.stop)

    def test_pilot_settings_validated(self):
        m = ps.load()["machines"]
        self.assertEqual((m["pilot_jobs"], m["pilot_jobs_days"], m["pilot_bmc_on_refresh"],
                          m["pilot_poll_s"]), (True, 14, True, 5))
        rev = ps.payload(include_status=False)["revision"]
        self.assertEqual(m["job_retention_days"], 30)
        for bad in ({"pilot_jobs_days": 0}, {"pilot_jobs_days": 91}, {"pilot_poll_s": 1},
                    {"job_retention_days": -1}, {"job_retention_days": 366},
                    {"pilot_jobs": "yes"}, {"pilot_bmc_on_refresh": 1}):
            with self.assertRaises(ps.SettingsError, msg=bad):
                ps.save({"machines": bad}, rev)
        ps.save({"machines": {"pilot_jobs_days": 30, "pilot_jobs": False}}, rev)
        self.assertEqual(ps.load()["machines"]["pilot_jobs_days"], 30)
        self.assertFalse(ps.load()["machines"]["pilot_jobs"])


if __name__ == "__main__":
    unittest.main()
