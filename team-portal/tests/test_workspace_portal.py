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
        def execute(job):
            job.update(id="synthetic-job", status="running")
            tm.ALIGN_JOBS[job["id"]] = job
            tm.run_workspace_job(job)
            return job["id"]
        self.enqueue = patch.object(tm, "enqueue_action", side_effect=execute)
        self.enqueue.start()
        self.addCleanup(self.enqueue.stop)
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
        def physical(slot, action, progress=None, **options):
            self.assertEqual(options, {"portal_free": {"approved": None}})
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
        def physical(slot, action, progress=None, **options):
            progress({"step": "inspect", "label": "Checking repositories"})
            raise ValueError("dirty")
        with patch.object(tm.wm, "workspace_request", side_effect=physical), \
                patch.object(tm, "apply_workspace_edit") as apply:
            jid = tm.start_workspace_job("free", "remote")
        apply.assert_not_called()
        self.assertEqual([s["state"] for s in tm.ALIGN_JOBS[jid]["steps"]], ["error"])

    def test_free_requires_confirmation_without_releasing_or_claiming_success(self):
        approval = {"token": "a" * 64, "repos": [{"path": "/repo"}], "blockers": []}
        with patch.object(tm.wm, "workspace_request", return_value={
                "clean": False, "approval_required": approval}) as request, \
                patch.object(tm, "apply_workspace_edit") as apply:
            jid = tm.start_workspace_job("free", "remote", {"action": "free"})
        apply.assert_not_called()
        self.assertEqual(request.call_args.kwargs["portal_free"], {"approved": None})
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "approval_required")
        self.assertEqual(tm.ALIGN_JOBS[jid]["approval"], approval)
        self.assertIn("has not been returned or freed", tm.ALIGN_JOBS[jid]["message"])

    def test_sync_requires_confirmation_then_syncs_without_releasing(self):
        approval = {"token": "a" * 64, "repos": [{"path": "/repo"}], "blockers": []}
        with patch.object(tm.wm, "workspace_request", return_value={
                "clean": False, "approval_required": approval}) as request, \
                patch.object(tm, "apply_workspace_edit") as apply:
            jid = tm.start_workspace_job("sync", "one")
        apply.assert_not_called()
        self.assertEqual(request.call_args.kwargs["portal_sync"], {"approved": None})
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "approval_required")
        self.assertIn("Sync was blocked", tm.ALIGN_JOBS[jid]["message"])
        stash = {"path": "/repo", "branch": "dev/test", "oid": "b" * 40}
        with patch.object(tm.wm, "workspace_request",
                          return_value={"clean": True, "stashes": [stash]}) as request, \
                patch.object(tm, "apply_workspace_edit") as apply:
            confirmed = tm.start_workspace_job("sync", "one", {
                "force": True, "approval_job": jid})
        apply.assert_not_called()
        self.assertEqual(request.call_args.kwargs["portal_sync"], {"approved": "a" * 64})
        self.assertEqual(tm.ALIGN_JOBS[confirmed]["status"], "done")
        self.assertEqual(tm.ALIGN_JOBS[confirmed]["stashes"], [stash])

    def test_force_sync_rejects_return_approval_and_invalid_force(self):
        previous = {"kind": "workspace-action", "action": "free", "slot": "one",
                    "status": "approval_required",
                    "approval": {"token": "a" * 64, "blockers": []}}
        with patch.dict(tm.ALIGN_JOBS, {"return-job": previous}), \
                patch.object(tm, "enqueue_action") as enqueue:
            for payload in ({"force": True, "approval_job": "return-job"},
                            {"force": "true"}, {"force": True}):
                with self.assertRaises(ValueError):
                    tm.start_workspace_job("sync", "one", payload)
        enqueue.assert_not_called()

    def test_sync_error_is_not_converted_into_force_confirmation(self):
        with patch.object(tm.wm, "workspace_request", side_effect=ValueError("network failure")), \
                patch.object(tm, "apply_workspace_edit") as apply:
            jid = tm.start_workspace_job("sync", "one")
        apply.assert_not_called()
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "error")
        self.assertNotIn("approval", tm.ALIGN_JOBS[jid])

    def test_explicit_free_approval_passes_saved_snapshot_and_releases_after_return(self):
        previous = {"kind": "workspace-action", "action": "free", "slot": "remote",
                    "status": "approval_required",
                    "approval": {"token": "b" * 64, "blockers": []}}
        stash = {"path": "/repo", "branch": "dev/test", "oid": "c" * 40}
        with patch.dict(tm.ALIGN_JOBS, {"approval-job": previous}), \
                patch.object(tm.wm, "workspace_request",
                             return_value={"clean": True, "stashes": [stash]}) as request, \
                patch.object(tm, "apply_workspace_edit", return_value=(True, "freed")) as apply:
            jid = tm.start_workspace_job("free", "remote", {
                "action": "free", "force": True, "approval_job": "approval-job"})
            job = dict(tm.ALIGN_JOBS[jid])
        self.assertEqual(request.call_args.kwargs["portal_free"], {"approved": "b" * 64})
        apply.assert_called_once()
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["stashes"], [stash])

    def test_stale_free_approval_requires_fresh_confirmation(self):
        previous = {"kind": "workspace-action", "action": "free", "slot": "remote",
                    "status": "approval_required",
                    "approval": {"token": "a" * 64, "blockers": []}}
        updated = {"token": "b" * 64, "repos": [], "blockers": []}
        with patch.dict(tm.ALIGN_JOBS, {"approval-job": previous}), \
                patch.object(tm.wm, "workspace_request",
                             return_value={"clean": False, "approval_required": updated}), \
                patch.object(tm, "apply_workspace_edit") as apply:
            jid = tm.start_workspace_job("free", "remote", {
                "action": "free", "force": True, "approval_job": "approval-job"})
            job = dict(tm.ALIGN_JOBS[jid])
        apply.assert_not_called()
        self.assertEqual(job["status"], "approval_required")
        self.assertEqual(job["approval"], updated)

    def test_free_retains_stash_details_when_releasing_the_hold_fails(self):
        previous = {"kind": "workspace-action", "action": "free", "slot": "remote",
                    "status": "approval_required",
                    "approval": {"token": "a" * 64, "blockers": []}}
        stash = {"path": "/repo", "branch": "dev/test", "oid": "c" * 40}
        with patch.dict(tm.ALIGN_JOBS, {"approval-job": previous}), \
                patch.object(tm.wm, "workspace_request",
                             return_value={"clean": True, "stashes": [stash]}), \
                patch.object(tm, "apply_workspace_edit",
                             return_value=(False, "could not release hold")):
            jid = tm.start_workspace_job("free", "remote", {
                "action": "free", "force": True, "approval_job": "approval-job"})
            job = dict(tm.ALIGN_JOBS[jid])
        self.assertEqual(job["status"], "error")
        self.assertIn("could not release hold", job["message"])
        self.assertEqual(job["stashes"], [stash])

    def test_free_force_rejects_invalid_or_unrelated_approvals(self):
        previous = {"kind": "workspace-action", "action": "free", "slot": "one",
                    "status": "approval_required",
                    "approval": {"token": "a" * 64, "blockers": []}}
        invalid = [
            {"force": "true"},
            {"force": True},
            {"force": True, "approval_job": "missing"},
            {"force": True, "approval_job": "other-slot"},
            {"force": False, "approval_job": "other-slot"},
        ]
        with patch.dict(tm.ALIGN_JOBS, {"other-slot": previous}), \
                patch.object(tm, "enqueue_action") as enqueue:
            for payload in invalid:
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    tm.start_workspace_job("free", "remote", payload)
        enqueue.assert_not_called()

    def test_free_force_rejects_approval_with_safety_blockers(self):
        previous = {"kind": "workspace-action", "action": "free", "slot": "remote",
                    "status": "approval_required",
                    "approval": {"token": "a" * 64, "blockers": ["local target commits"]}}
        with patch.dict(tm.ALIGN_JOBS, {"approval-job": previous}), \
                patch.object(tm, "enqueue_action") as enqueue:
            with self.assertRaisesRegex(ValueError, "safety blockers"):
                tm.start_workspace_job("free", "remote", {
                    "force": True, "approval_job": "approval-job"})
        enqueue.assert_not_called()

    def test_multi_slot_steps_are_prefixed_per_workspace(self):
        job = {"steps": []}
        tm.record_job_step(job, {"step": "inspect", "label": "Checking"}, prefix="one")
        tm.record_job_step(job, {"step": "inspect", "label": "Checking"}, prefix="two")
        self.assertEqual([s["label"] for s in job["steps"]], ["one · Checking", "two · Checking"])
        self.assertEqual(job["steps"][0]["state"], "done")

    def test_occupy_checks_clean_before_metadata(self):
        with patch.object(tm.wm, "require_clean", side_effect=ValueError("not at revision")), \
                patch.object(tm, "apply_workspace_edit") as apply:
            jid = tm.start_workspace_job("occupy", "remote", {"task_id": "T-1"})
        apply.assert_not_called()
        self.assertEqual(tm.ALIGN_JOBS[jid]["status"], "error")

    def test_busy_workspace_can_be_submitted_for_later_execution(self):
        tm.WORKSPACE_BUSY.add("remote")
        with patch.object(tm, "enqueue_action", return_value="queued-job") as enqueue:
            jid = tm.start_workspace_job("sync", "remote")
        self.assertEqual(jid, "queued-job")
        self.assertEqual(enqueue.call_args.args[0]["request"]["slots"], ["remote"])

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


class WorkspaceReturnIntegrationTests(unittest.TestCase):
    def test_force_sync_rechecks_free_state_before_stashing(self):
        import test_workspaces

        for occupied in (False, True):
            with self.subTest(occupied=occupied):
                fixture = test_workspaces.RuntimeTests()
                fixture.setUp()
                try:
                    with patch.object(tm.wm, "STORE", fixture.home / "tasks/workspaces.db"), \
                            patch.object(workspace_store, "LEGACY", fixture.home / "absent.json"), \
                            patch.dict(tm.ALIGN_JOBS, {}, clear=True):
                        tm.wm.save({"version": 1, "slots": {}, "groups": [], "tracked": {"slot": {
                            "id": fixture.request["id"], "path": str(fixture.slot),
                            "label": "Temporary slot", "rev": "main", "target": ""}}})
                        fixture.dev()
                        (fixture.repo / "untracked").write_text("keep")
                        sequence = iter(("sync-preflight", "sync-confirmed"))

                        def enqueue(job):
                            job.update(id=next(sequence), status="running")
                            tm.ALIGN_JOBS[job["id"]] = job
                            tm.run_workspace_job(job)
                            return job["id"]

                        with patch.object(tm, "enqueue_action", side_effect=enqueue):
                            first = tm.start_workspace_job("sync", "slot")
                            self.assertEqual(tm.ALIGN_JOBS[first]["status"], "approval_required")
                            self.assertIsNone(tm.wm.get_holder("slot"))
                            if occupied:
                                with patch.object(tm.wm, "require_clean"):
                                    self.assertTrue(tm.wm.occupy_adhoc("slot", "new hold")[0])
                            second = tm.start_workspace_job("sync", "slot", {
                                "force": True, "approval_job": first})
                            job = tm.ALIGN_JOBS[second]
                            if occupied:
                                self.assertEqual(job["status"], "error")
                                self.assertIn("occupied", job["message"])
                                self.assertIsNotNone(tm.wm.get_holder("slot"))
                                self.assertEqual(fixture.git(fixture.repo, "stash", "list"), "")
                                self.assertFalse((fixture.home / "octl-calls").exists())
                            else:
                                self.assertEqual(job["status"], "done", job["message"])
                                self.assertIsNone(tm.wm.get_holder("slot"))
                                self.assertEqual(fixture.git(fixture.repo, "branch", "--show-current"), "main")
                                self.assertEqual(len(job["stashes"]), 1)
                finally:
                    fixture.doCleanups()

    def test_confirmed_return_preserves_work_then_releases_real_temporary_hold(self):
        import test_workspaces

        fixture = test_workspaces.RuntimeTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        store = fixture.home / "tasks/workspaces.db"
        with patch.object(tm.wm, "STORE", store), \
                patch.object(workspace_store, "LEGACY", fixture.home / "absent.json"), \
                patch.dict(tm.ALIGN_JOBS, {}, clear=True):
            tm.wm.save({"version": 1, "slots": {}, "groups": [], "tracked": {"slot": {
                "id": fixture.request["id"], "path": str(fixture.slot),
                "label": "Temporary slot", "rev": "main", "target": ""}}})
            with patch.object(tm.wm, "workspace_request", return_value={"clean": True}):
                self.assertTrue(tm.wm.occupy_adhoc("slot", "test return")[0])
            fixture.dev()
            (fixture.repo / "untracked").write_text("preserve me")
            branch_head = fixture.git(fixture.repo, "rev-parse", "HEAD")
            sequence = iter(("preflight-job", "confirmed-job"))

            def enqueue(job):
                job.update(id=next(sequence), status="running")
                tm.ALIGN_JOBS[job["id"]] = job
                tm.run_workspace_job(job)
                return job["id"]

            with patch.object(tm, "enqueue_action", side_effect=enqueue):
                first = tm.start_workspace_job("free", "slot", {"action": "free"})
                self.assertEqual(tm.ALIGN_JOBS[first]["status"], "approval_required")
                self.assertIsNotNone(tm.wm.get_holder("slot"))
                self.assertEqual(fixture.git(fixture.repo, "stash", "list"), "")
                self.assertFalse((fixture.home / "octl-calls").exists())

                second = tm.start_workspace_job("free", "slot", {
                    "action": "free", "force": True, "approval_job": first})
                job = tm.ALIGN_JOBS[second]
                self.assertEqual(job["status"], "done", job.get("message"))
                self.assertIsNone(tm.wm.get_holder("slot"))
                self.assertEqual(fixture.git(fixture.repo, "branch", "--show-current"), "main")
                self.assertEqual(fixture.git(fixture.repo, "rev-parse", "dev/test"), branch_head)
                stash = job["stashes"][0]
                self.assertIn(stash["oid"], fixture.git(fixture.repo, "stash", "list", "--format=%H"))
                self.assertIn(stash["oid"], tm.wm.action_log("slot")[-2]["detail"])


if __name__ == "__main__":
    unittest.main()
