import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "skills" / "task-manager", ROOT / "skills" / "workspace-manager",
           ROOT / "team-portal"):
    sys.path.insert(0, str(_p))
import portal as tm
import workspace_store


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class WorkspaceJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = patch.object(tm.wm, "STORE", Path(self.tmp.name) / "workspaces.db")
        self.store.start()
        self.addCleanup(self.store.stop)
        self.legacy = patch.object(workspace_store, "LEGACY", Path(self.tmp.name) / "none.json")
        self.legacy.start()
        self.addCleanup(self.legacy.stop)
        self.local = {"slot": "one", "id": "WS-L-" + "a" * 32,
                      "target": "", "label": "one", "rev": "main", "exists": True}
        self.remote = dict(self.local, slot="remote", id="WS-R-" + "b" * 32, target="vm")
        self.roster = patch.object(tm.wm, "roster", return_value=[self.local, self.remote])
        self.roster.start()
        self.addCleanup(self.roster.stop)
        self.rec = patch.object(tm.wm, "record", side_effect=lambda slot:
                                self.local if slot == "one" else self.remote)
        self.rec.start()
        self.addCleanup(self.rec.stop)
        self.thread = patch.object(tm._threading, "Thread", ImmediateThread)
        self.thread.start()
        self.addCleanup(self.thread.stop)
        self.log = patch.object(tm.wm, "log_action")
        self.log.start()
        self.addCleanup(self.log.stop)
        tm.WORKSPACE_BUSY.clear()

    def test_bulk_refresh_never_probes_remote(self):
        with patch.object(tm.wm, "workspace_request", return_value={"clean": True}) as request:
            jid = tm.start_workspace_job("refresh")
        self.assertEqual(request.call_args_list[0].args, ("one", "status"))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "done")

    def test_card_refresh_is_scoped_to_remote(self):
        with patch.object(tm.wm, "workspace_request", return_value={"clean": True}) as request:
            jid = tm.start_workspace_job("refresh", "remote")
        self.assertEqual(request.call_args.args, ("remote", "status"))
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "done")

    def test_bulk_vm_check_never_probes_local(self):
        with patch.object(tm.wm, "workspace_request", return_value={"clean": True}) as request:
            jid = tm.start_workspace_job("refresh", scope="remote")
        request.assert_called_once()
        self.assertEqual(request.call_args.args, ("remote", "status"))
        self.assertEqual(tm.ALIGN_JOBS[jid]["slot"], "remote workspaces")
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "done")
        self.assertFalse(tm.WORKSPACE_BUSY)

    def test_empty_vm_roster_finishes_without_probe(self):
        with patch.object(tm.wm, "roster", return_value=[self.local]), \
                patch.object(tm.wm, "workspace_request") as request:
            jid = tm.start_workspace_job("refresh", scope="remote")
        request.assert_not_called()
        self.assertEqual(tm.ALIGN_JOBS[jid]["total"], 0)
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "done")

    def test_vm_check_reports_errors_and_clears_busy_state(self):
        with patch.object(tm.wm, "workspace_request", side_effect=ValueError("machine unavailable")):
            jid = tm.start_workspace_job("refresh", scope="remote")
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "error")
        self.assertIn("machine unavailable", tm.ALIGN_JOBS[jid]["message"])
        self.assertFalse(tm.WORKSPACE_BUSY)

    def test_invalid_scope_does_not_start_job(self):
        with self.assertRaisesRegex(ValueError, "unknown workspace scope"):
            tm.start_workspace_job("refresh", scope="all")

    def test_free_failure_does_not_release_holder(self):
        with patch.object(tm.wm, "workspace_request", side_effect=ValueError("dirty")), \
                patch.object(tm, "apply_workspace_edit") as apply:
            jid = tm.start_workspace_job("free", "remote")
        apply.assert_not_called()
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "error")
        self.assertIn("dirty", tm.ALIGN_JOBS[jid]["message"])
        self.assertFalse(tm.WORKSPACE_BUSY)

    def test_free_success_releases_after_physical_completion(self):
        events = []
        def physical(*args, **kwargs):
            events.append("physical")
            return {"clean": True}
        def apply(*args):
            events.append("release")
            return True, "freed"
        with patch.object(tm.wm, "workspace_request", side_effect=physical), \
                patch.object(tm, "apply_workspace_edit", side_effect=apply):
            jid = tm.start_workspace_job("free", "remote")
        self.assertEqual(events, ["physical", "release"])
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "done")

    def test_free_reports_live_steps_including_release(self):
        def physical(slot, action, progress=None):
            progress({"step": "return", "label": "Returning", "done": 1, "total": 2,
                      "current": "repo-a", "error": "fetch denied"})
            progress({"step": "return", "label": "Returning", "done": 2, "total": 2,
                      "current": "repo-b"})
            progress({"step": "sync", "label": "Running octl ws sync", "detail": "Fetching..."})
            return {"clean": True}
        with patch.object(tm.wm, "workspace_request", side_effect=physical), \
                patch.object(tm, "apply_workspace_edit", return_value=(True, "freed")):
            jid = tm.start_workspace_job("free", "remote")
        steps = tm.ALIGN_JOBS[jid]["steps"]
        self.assertEqual([s["key"] for s in steps], ["return", "sync", "release"])
        self.assertTrue(all(s["state"] == "done" for s in steps))
        self.assertEqual((steps[0]["done"], steps[0]["total"]), (2, 2))
        self.assertEqual(steps[0]["errors"], ["repo-a: fetch denied"])
        self.assertEqual(steps[1]["detail"], "Fetching...")

    def test_failed_step_is_marked_error(self):
        def physical(slot, action, progress=None):
            progress({"step": "inspect", "label": "Checking repositories"})
            raise ValueError("dirty")
        with patch.object(tm.wm, "workspace_request", side_effect=physical), \
                patch.object(tm, "apply_workspace_edit") as apply:
            jid = tm.start_workspace_job("free", "remote")
        apply.assert_not_called()
        self.assertEqual([s["state"] for s in tm.ALIGN_JOBS[jid]["steps"]], ["error"])

    def test_multi_slot_steps_are_prefixed_per_workspace(self):
        job = {"steps": []}
        tm.record_job_step(job, {"step": "inspect", "label": "Checking"}, prefix="one")
        tm.record_job_step(job, {"step": "inspect", "label": "Checking"}, prefix="two")
        self.assertEqual([s["label"] for s in job["steps"]], ["one · Checking", "two · Checking"])
        self.assertEqual(job["steps"][0]["state"], "done")

    def test_occupy_checks_clean_before_metadata_and_no_busy_overlap(self):
        with patch.object(tm.wm, "require_clean", side_effect=ValueError("not at revision")), \
                patch.object(tm, "apply_workspace_edit") as apply:
            jid = tm.start_workspace_job("occupy", "remote", {"task_id": "T-1"})
        apply.assert_not_called()
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "error")
        tm.WORKSPACE_BUSY.add("remote")
        with self.assertRaisesRegex(ValueError, "already running"):
            tm.start_workspace_job("sync", "remote")

    def test_remote_slots_never_used_for_local_pldm(self):
        self.assertEqual(tm.viable_slots(), ["one"])

    def test_history_http_handler_decodes_id_and_merges_logs(self):
        captured = {}
        class CaptureServer:
            def __init__(self, address, handler):
                captured["handler"] = handler
                raise RuntimeError("handler captured")
        with patch("http.server.ThreadingHTTPServer", CaptureServer), \
                patch.object(tm, "_server_running", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "handler captured"):
                tm.cmd_serve(SimpleNamespace(status=False, stop=False, port=0))
        handler = object.__new__(captured["handler"])
        handler.path = "/api/workspace-log/%6Fne"
        handler._send = lambda code, body, *args: (code, __import__("json").loads(body))
        with patch.object(tm.wm, "action_log", return_value=[{"at": "2026-01-02", "action": "local"}]), \
                patch.object(tm.wm, "status_read", return_value={
                    "log": [{"at": "2026-01-01", "action": "remote"}]}):
            code, body = handler.do_GET()
        self.assertEqual(code, 200)
        self.assertEqual([r["action"] for r in body["events"]], ["local", "remote"])


if __name__ == "__main__":
    unittest.main()
