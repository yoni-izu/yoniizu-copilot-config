"""Local HTTP adapters use owner APIs and reject stale browser snapshots."""
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal


class MutationTests(unittest.TestCase):
    def handler(self, path, payload):
        captured = {}

        class CaptureServer:
            def __init__(self, address, handler):
                captured["handler"] = handler
                raise RuntimeError("captured")

        with patch("http.server.ThreadingHTTPServer", CaptureServer), \
                patch.object(portal, "_server_running", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "captured"):
                portal.cmd_serve(SimpleNamespace(status=False, stop=False, port=0))
        handler = object.__new__(captured["handler"])
        body = json.dumps(payload).encode()
        handler.path = path
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler._send = lambda code, text, *args: (code, json.loads(text))
        return handler

    def test_workspace_metadata_edit_routes_to_owner_and_refreshes_cards(self):
        fields = {"label": "New label", "badge": "NEW", "group": "", "rev": "release"}
        handler = self.handler("/api/ws-meta/slot%20one", fields)
        with patch.object(portal.wm, "set_slot_fields", return_value=(True, "saved")) as edit, \
                patch.object(portal.wm, "load", return_value={"slots": {}}), \
                patch.object(portal.tm, "load", return_value={"tasks": []}), \
                patch.object(portal, "_annotate_tasks", return_value=[]), \
                patch.object(portal, "ws_ui_payload", return_value={"roster": []}):
            status, response = handler.do_POST()
        edit.assert_called_once_with("slot one", fields)
        self.assertEqual(status, 200)
        self.assertEqual(response["workspaces"], {"roster": []})

    def test_workspace_metadata_validation_error_is_reported(self):
        handler = self.handler("/api/ws-meta/one", {"rev": "release"})
        with patch.object(portal.wm, "set_slot_fields",
                          return_value=(False, "free the workspace before changing its revision")):
            status, response = handler.do_POST()
        self.assertEqual(status, 400)
        self.assertIn("free the workspace", response["error"])

    def test_dedicated_purpose_metadata_is_guarded_against_queued_actions(self):
        handler = self.handler("/api/ws-meta/one", {"purpose": "review"})
        with patch.object(portal, "ACTION_QUEUE") as queue, \
                patch.object(portal.wm, "record", return_value={"id": "WS-L-one"}), \
                patch.object(portal.wm, "set_slot_fields") as edit:
            queue.idle_resources.side_effect = ValueError("workspace has queued or running actions")
            status, response = handler.do_POST()
        self.assertEqual(status, 409)
        edit.assert_not_called()
        self.assertIn("queued", response["error"])

    def test_build_slot_selection_excludes_review_and_development_workspaces(self):
        rows = [{"slot": purpose, "purpose": purpose, "target": "", "rev": "main", "exists": True}
                for purpose in portal.wm.PURPOSES]
        with patch.object(portal.wm, "roster", return_value=rows), \
                patch.object(portal.wm, "groups", return_value=[]), \
                patch.object(portal.wm, "machine_targets", return_value=[]):
            self.assertEqual(portal.viable_slots(), ["general", "build-root", "mirror"])
            payload = portal.ws_ui_payload({}, cached_only=True)
            self.assertEqual(payload["canonical"], ["general", "build-root", "mirror"])
            self.assertEqual(payload["purposes"]["review"], "Review")

    def test_workspace_untrack_routes_explicit_boolean_override_to_owner(self):
        for payload in ({}, {"force": False}, {"force": True}):
            with self.subTest(payload=payload):
                handler = self.handler("/api/ws-untrack/slot%20one", payload)
                with patch.object(portal.wm, "untrack", return_value=(True, "untracked")) as untrack, \
                        patch.object(portal.wm, "load", return_value={"slots": {}}), \
                        patch.object(portal.tm, "load", return_value={"tasks": []}), \
                        patch.object(portal, "_annotate_tasks", return_value=[]), \
                        patch.object(portal, "ws_ui_payload", return_value={"roster": []}):
                    status, response = handler.do_POST()
                untrack.assert_called_once_with("slot one", force=payload.get("force", False))
                self.assertEqual(status, 200)
                self.assertEqual(response["workspaces"], {"roster": []})

    def test_workspace_untrack_rejects_non_boolean_override(self):
        for value in ("false", "true", 1, None, []):
            handler = self.handler("/api/ws-untrack/one", {"force": value})
            with patch.object(portal.wm, "untrack") as untrack:
                status, response = handler.do_POST()
            untrack.assert_not_called()
            self.assertEqual(status, 400)
            self.assertEqual(response["error"], "force must be a boolean")

    def test_workspace_untrack_reports_guard_and_operation_errors(self):
        for result, error, expected in (
                ((False, "free it first"), None, 400),
                (None, ValueError("workspace operation already running"), 409)):
            handler = self.handler("/api/ws-untrack/one", {"force": True})
            with patch.object(portal.wm, "untrack", return_value=result, side_effect=error):
                status, response = handler.do_POST()
            self.assertEqual(status, expected)
            self.assertIn("error", response)

    def test_pldm_job_queue_section_is_discontinued(self):
        self.assertFalse(hasattr(portal, "pldmq_payload"))
        self.assertFalse(hasattr(portal, "pjq"))
        source = (Path(portal.__file__).parent / "assets" / "portal.js").read_text()
        self.assertNotIn('id:"pldmq"', source)
        self.assertNotIn("/api/pldm-scan", source)
        for path, method in (("/api/pldm-jobs", "do_GET"), ("/api/pldm-scan", "do_POST")):
            status, _ = getattr(self.handler(path, {}), method)()
            self.assertEqual(status, 404, path)

    def test_action_queue_listing_routes_to_scheduler(self):
        handler = self.handler("/api/actions", {})
        readings = {"actions": [{"id": "test", "status": "queued", "queue_position": 1}]}
        with patch.object(portal, "ACTION_QUEUE") as queue:
            queue.snapshot.return_value = readings
            status, response = handler.do_GET()
        self.assertEqual(status, 200)
        self.assertEqual(response, readings)

    def test_action_queue_failure_is_http_error(self):
        handler = self.handler("/api/actions", {})
        with patch.object(portal, "ACTION_QUEUE") as queue:
            queue.snapshot.return_value = {"actions": [], "error": "storage failed"}
            status, response = handler.do_GET()
        self.assertEqual(status, 503)
        self.assertEqual(response["error"], "storage failed")

    def test_action_queue_storage_read_error_is_explicit(self):
        handler = self.handler("/api/actions", {})
        with patch.object(portal, "ACTION_QUEUE") as queue:
            queue.snapshot.side_effect = OSError("permission denied")
            status, response = handler.do_GET()
        self.assertEqual(status, 503)
        self.assertIn("permission denied", response["error"])

    def test_queued_job_reading_includes_position_but_not_private_descriptor(self):
        handler = self.handler("/api/align/test", {})
        job = {"id": "test", "status": "queued", "request": {"slots": ["private"]}}
        with patch.dict(portal.ALIGN_JOBS, {"test": job}), \
                patch.object(portal, "ACTION_QUEUE") as queue:
            queue.snapshot.return_value = {"actions": [
                {"id": "test", "status": "queued", "queue_position": 2}]}
            status, response = handler.do_GET()
        self.assertEqual(status, 200)
        self.assertEqual(response["queue_position"], 2)
        self.assertNotIn("request", response)

    def test_job_poll_uses_scheduler_running_state_until_worker_cleanup_finishes(self):
        handler = self.handler("/api/align/test", {})
        job = {"id": "test", "status": "done", "request": {"slots": ["private"]}}
        with patch.dict(portal.ALIGN_JOBS, {"test": job}), \
                patch.object(portal, "ACTION_QUEUE") as queue:
            queue.snapshot.return_value = {"actions": [
                {"id": "test", "status": "running", "finished": None}]}
            status, response = handler.do_GET()
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "running")
        self.assertNotIn("request", response)

    def test_job_poll_reports_scheduler_storage_errors(self):
        handler = self.handler("/api/align/test", {})
        with patch.dict(portal.ALIGN_JOBS, {"test": {"id": "test", "status": "running"}}), \
                patch.object(portal, "ACTION_QUEUE") as queue:
            queue.snapshot.side_effect = OSError("permission denied")
            status, response = handler.do_GET()
        self.assertEqual(status, 503)
        self.assertIn("permission denied", response["error"])

    def test_review_submission_is_accepted_while_another_review_holds_store_lock(self):
        handler = self.handler("/api/review-scan", {"task_ids": ["T-1"]})
        with portal.REVIEW_SCAN_LOCK:
            with patch.object(portal, "start_review_scan_job", return_value="queued"):
                status, response = handler.do_POST()
            self.assertTrue(portal.REVIEW_SCAN_LOCK.locked())
        self.assertEqual(status, 200)
        self.assertEqual(response["align_job"], "queued")

    def test_raid_edit_reaches_owner_with_revision(self):
        handler = self.handler("/api/raid/R-001", {"title": "new", "expected_revision": 4})
        with patch.object(portal.rd, "apply_edit", return_value=(True, "saved")) as edit, \
                patch.object(portal, "raid_payload", return_value={"rows": []}):
            status, response = handler.do_POST()
        self.assertEqual(status, 200)
        edit.assert_called_once_with("R-001", {"title": "new", "expected_revision": 4})
        self.assertTrue(response["ok"])

    def test_raid_close_preserves_revision_guard(self):
        handler = self.handler("/api/raid/R-001", {"close": "done", "expected_revision": 4})
        with patch.object(portal.rd, "apply_edit", return_value=(False, "revision conflict")) as edit:
            status, _ = handler.do_POST()
        self.assertEqual(status, 400)
        self.assertEqual(edit.call_args.args[1]["expected_revision"], 4)

    def test_stale_task_is_rejected_before_side_effects(self):
        task = {"id": "T-001", "status": "todo"}
        with patch.object(portal.tm, "load", return_value={"tasks": [task]}), \
                patch.object(portal.tm, "save") as save, \
                patch.object(portal.tm, "release_task_slot") as release:
            ok, message = portal.apply_task_edit(
                "T-001", {"status": "done", "expected_revision": "obsolete"})
        self.assertFalse(ok)
        self.assertIn("revision conflict", message)
        save.assert_not_called()
        release.assert_not_called()

    def test_bulk_task_edit_routes_to_owner_and_returns_refreshed_tasks(self):
        edits = [{"id": "T-001", "expected_revision": "rev-1", "priority": "high"}]
        handler = self.handler("/api/tasks/bulk-edit", {"edits": edits})
        with patch.object(portal, "apply_task_edits", return_value=(True, "updated 1 task(s)")) as edit, \
                patch.object(portal.wm, "load", return_value={"slots": {}}), \
                patch.object(portal.tm, "load", return_value={"tasks": []}), \
                patch.object(portal, "_annotate_tasks", return_value=[{"id": "T-001"}]), \
                patch.object(portal, "ws_ui_payload", return_value={"slots": {}}):
            status, response = handler.do_POST()
        self.assertEqual(status, 200)
        edit.assert_called_once_with(edits)
        self.assertEqual(response["tasks"], [{"id": "T-001"}])
        self.assertEqual(response["message"], "updated 1 task(s)")

    def test_review_scan_scope_all_queues_every_open_review(self):
        handler = self.handler("/api/review-scan", {"scope": "all"})
        with patch.object(portal, "start_review_scan_job", return_value="scan-all") as start:
            status, response = handler.do_POST()
        self.assertEqual((status, response["align_job"]), (200, "scan-all"))
        start.assert_called_once_with(None)

    def test_review_scan_rejects_unknown_scope_shapes(self):
        for payload in ({"scope": "everything"}, {"scope": "all", "task_ids": ["T-1"]}):
            handler = self.handler("/api/review-scan", payload)
            with patch.object(portal, "start_review_scan_job") as start:
                status, _ = handler.do_POST()
            self.assertEqual(status, 400)
            start.assert_not_called()

    def test_pr_scan_jira_sync_flag_reaches_job(self):
        for payload, kwargs in (({}, {}), ({"jira_sync": True}, {}),
                                ({"jira_sync": False}, {"jira": False})):
            handler = self.handler("/api/pr-scan", payload)
            with patch.object(portal, "start_pr_scan_job", return_value="job-1") as start:
                status, response = handler.do_POST()
            self.assertEqual((status, response["job"]), (200, "job-1"))
            start.assert_called_once_with(None, **kwargs)
        handler = self.handler("/api/pr-scan", {"jira_sync": "no"})
        with patch.object(portal, "start_pr_scan_job") as start:
            status, response = handler.do_POST()
        self.assertEqual(status, 400)
        start.assert_not_called()

    def test_pr_scan_without_jira_reports_skip_and_calls_owner_without_jira(self):
        refresh = Mock(return_value={"features": [], "jira_sync": [], "jira_sync_skipped": True})
        with patch.object(portal, "pm", SimpleNamespace(refresh=refresh)):
            job = {"repos": []}
            portal._run_pr_scan(job, jira_enabled=False)
        self.assertEqual(refresh.call_args.kwargs.get("jira"), False)
        self.assertEqual(job["status"], "done")
        self.assertIn("Jira sync skipped", job["message"])

    def test_queue_retention_endpoint_validates_and_prunes(self):
        for bad in ({}, {"days": 3}, {"days": "90"}, {"days": True}, {"days": 90, "x": 1}):
            handler = self.handler("/api/maintenance/queue-retention", bad)
            with patch.object(portal, "ACTION_QUEUE") as queue:
                status, _ = handler.do_POST()
            self.assertEqual(status, 400, bad)
            queue.prune.assert_not_called()
        handler = self.handler("/api/maintenance/queue-retention", {"days": 90})
        with patch.object(portal, "ACTION_QUEUE") as queue:
            queue.prune.return_value = {"removed": 2, "remaining": 5, "cutoff": "x"}
            status, response = handler.do_POST()
        queue.prune.assert_called_once_with(90)
        self.assertEqual((status, response["removed"]), (200, 2))
        handler = self.handler("/api/maintenance/queue-retention", {"days": 90})
        handler.headers = dict(handler.headers, Origin="http://evil.example", Host="127.0.0.1:8787")
        with patch.object(portal, "ACTION_QUEUE") as queue:
            status, _ = handler.do_POST()
        self.assertEqual(status, 403)
        queue.prune.assert_not_called()

    def test_settings_endpoint_maps_errors_and_journals_changes(self):
        ps = portal.portal_settings
        cases = ((ps.SettingsError("bad"), 400), (ps.RevisionConflict("stale"), 409),
                 (ps.ApplyError("x; settings rolled back"), 500))
        for error, code in cases:
            handler = self.handler("/api/settings", {"revision": "r", "settings": {}})
            with patch.object(ps, "save", side_effect=error), \
                    patch.object(portal.maintenance, "record") as record:
                status, _ = handler.do_POST()
            self.assertEqual(status, code)
            self.assertEqual(record.called, code == 500)
        handler = self.handler("/api/settings", {"settings": {}})
        with patch.object(ps, "save") as save:
            self.assertEqual(handler.do_POST()[0], 400)
        save.assert_not_called()
        handler = self.handler("/api/settings", {"revision": "r", "settings": {}})
        handler.headers = dict(handler.headers, Origin="http://evil.example", Host="127.0.0.1:8787")
        with patch.object(ps, "save") as save:
            self.assertEqual(handler.do_POST()[0], 403)
        save.assert_not_called()
        handler = self.handler("/api/settings", {"revision": "r",
                                                 "settings": {"routine": {"time": "07:00"}}})
        with patch.object(ps, "save", return_value={"changed": ["routine.time: a -> b"],
                                                    "applied": "timer armed", "revision": "n"}), \
                patch.object(ps, "payload", return_value={"settings": {}}), \
                patch.object(portal.maintenance, "record") as record:
            status, response = handler.do_POST()
        self.assertEqual((status, response["revision"]), (200, "n"))
        record.assert_called_once()
        self.assertEqual(record.call_args.kwargs["component"], "portal-settings")

    def test_machine_settings_wake_power_scheduler_and_notify_other_tabs(self):
        handler = self.handler("/api/settings", {"revision": "r",
                                                 "settings": {"machines": {"power_checks": False}}})
        with patch.object(portal.portal_settings, "save", return_value={
                "changed": ["machines.power_checks: true -> false"], "applied": "", "revision": "n"}), \
                patch.object(portal.portal_settings, "payload", return_value={"settings": {}}), \
                patch.object(portal, "_journal_settings"), \
                patch.object(portal, "LIVE", None), \
                patch.object(portal, "POWER_SCHEDULE") as scheduler, \
                patch.object(portal.EVENTS, "publish_settings") as notify:
            status, _ = handler.do_POST()
        self.assertEqual(status, 200)
        scheduler.wake.set.assert_called_once()
        notify.assert_called_once()

    def test_overview_brief_reads_missing_valid_and_corrupt_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "morning-brief.json"
            with patch.object(portal, "MORNING_BRIEF", path):
                self.assertIsNone(portal._read_morning_brief())
                path.write_text(json.dumps({"schema": 1, "steps": []}))
                self.assertEqual(portal._read_morning_brief()["schema"], 1)
                path.write_text("{broken")
                self.assertIn("unreadable", portal._read_morning_brief()["error"])
                path.write_text(json.dumps({"schema": 9}))
                self.assertIn("unsupported", portal._read_morning_brief()["error"])

    def test_review_scan_requires_selected_task_ids(self):
        handler = self.handler("/api/review-scan", {"task_ids": []})
        with patch.object(portal, "start_review_scan_job") as start:
            status, response = handler.do_POST()
        self.assertEqual(status, 400)
        self.assertIn("select", response["error"])
        start.assert_not_called()

    def test_review_scan_passes_only_selected_task_ids_to_job(self):
        task_ids = ["T-001", "T-003"]
        handler = self.handler("/api/review-scan", {"task_ids": task_ids})
        try:
            with patch.object(portal, "start_review_scan_job", return_value="scan-1") as start:
                status, response = handler.do_POST()
            self.assertEqual(status, 200)
            self.assertEqual(response["align_job"], "scan-1")
            start.assert_called_once_with(task_ids)
        finally:
            if portal.REVIEW_SCAN_LOCK.locked():
                portal.REVIEW_SCAN_LOCK.release()

    def test_feature_revision_reaches_owner(self):
        with patch.object(portal.pm, "edit_feature", return_value=(True, "saved")) as edit:
            ok, _ = portal.pr_track_edit("/api/feature-edit", {
                "feature_id": 2, "priority": "high", "expected_revision": 7})
        self.assertTrue(ok)
        edit.assert_called_once_with(2, priority="high", expected_revision=7)

    def test_jira_coverage_routes_confirmation_and_revocation_to_owner(self):
        for confirmed in (True, False):
            owner = SimpleNamespace(
                confirm_jira=Mock(return_value=(True, "saved")),
                payload=Mock(return_value={"features": [{"id": 2, "revision": 8}]}))
            handler = self.handler("/api/feature-jira-coverage", {
                "feature_id": 2, "key": "TEST-42", "confirmed": confirmed,
                "expected_revision": 7})
            with patch.object(portal, "pm", owner):
                status, response = handler.do_POST()
            self.assertEqual(status, 200)
            self.assertEqual(response["prs"]["features"][0]["revision"], 8)
            owner.confirm_jira.assert_called_once_with(
                2, "TEST-42", confirmed=confirmed, expected_revision=7)
            self.assertFalse(portal.PR_SCAN_LOCK.locked())

    def test_jira_coverage_rejects_missing_or_wrongly_typed_fields(self):
        valid = {"feature_id": 2, "key": "TEST-42", "confirmed": True,
                 "expected_revision": 7}
        invalid = {
            "feature_id": [True, False, "2", 2.0, None],
            "key": [True, 42, [], None],
            "confirmed": ["true", 1, 0, None],
            "expected_revision": [True, False, "7", 7.0, None],
        }
        for field, values in invalid.items():
            for value in [*values, "missing"]:
                with self.subTest(field=field, value=value):
                    body = {**valid, field: value}
                    if value == "missing":
                        del body[field]
                    owner = SimpleNamespace(confirm_jira=Mock())
                    handler = self.handler("/api/feature-jira-coverage", body)
                    with patch.object(portal, "pm", owner):
                        status, response = handler.do_POST()
                    self.assertEqual(status, 400)
                    self.assertIn(field, response["error"])
                    owner.confirm_jira.assert_not_called()
                    self.assertFalse(portal.PR_SCAN_LOCK.locked())

    def test_jira_coverage_preserves_owner_rejections_and_releases_lock(self):
        for result, error, expected in (
                ((False, "feature changed; reload"), None, 400),
                (None, RuntimeError("owner unavailable"), 500)):
            owner = SimpleNamespace(confirm_jira=Mock(return_value=result, side_effect=error))
            handler = self.handler("/api/feature-jira-coverage", {
                "feature_id": 2, "key": "TEST-42", "confirmed": True,
                "expected_revision": 7})
            with patch.object(portal, "pm", owner):
                status, response = handler.do_POST()
            self.assertEqual(status, expected)
            self.assertIn("error", response)
            self.assertFalse(portal.PR_SCAN_LOCK.locked())

    def test_jira_coverage_does_not_run_during_scan_or_without_owner(self):
        owner = SimpleNamespace(confirm_jira=Mock())
        handler = self.handler("/api/feature-jira-coverage", {})
        with patch.object(portal, "pm", owner), patch.object(portal, "PR_SCAN_LOCK") as lock:
            lock.acquire.return_value = False
            status, _ = handler.do_POST()
            lock.release.assert_not_called()
        self.assertEqual(status, 409)
        owner.confirm_jira.assert_not_called()
        with patch.object(portal, "pm", None):
            status, _ = handler.do_POST()
        self.assertEqual(status, 503)

    def test_pr_scan_keeps_archived_jira_failures_visible_and_not_successful(self):
        results = [
            {"feature_id": 2, "key": "TEST-1", "state": "updated",
             "target": "In Review", "message": "transitioned"},
            {"feature_id": 2, "key": "TEST-2", "state": "unchanged", "message": "current"},
            {"feature_id": 90, "key": "TEST-90", "state": "error",
             "target": "Done", "message": "Jira unreachable"},
            {"feature_id": 91, "key": "TEST-91", "state": "blocked",
             "message": "not assigned to authenticated user"},
        ]
        result = {"features": [{"id": 2}], "jira_sync": results, "scanned": 1}
        owner = SimpleNamespace(refresh=Mock(return_value=result))
        job = {"repos": []}
        with patch.object(portal, "pm", owner):
            portal._run_pr_scan(job, only=[2])
        self.assertEqual(job["status"], "error")
        self.assertIs(job["result"], result)
        self.assertEqual(result["jira_sync_counts"],
                         {"updated": 1, "unchanged": 1, "blocked": 1, "error": 1})
        self.assertEqual(result["jira_sync"], results)
        self.assertIn("archived retries", job["message"])
        self.assertIn("feature 90 / TEST-90: error", job["message"])
        self.assertIn("Jira unreachable", job["message"])
        self.assertIn("feature 91 / TEST-91: blocked", job["message"])
        self.assertEqual(owner.refresh.call_args.kwargs["only"], [2])

    def test_pr_scan_success_reports_jira_counts_and_legacy_scans_still_work(self):
        for results in ([], [{"feature_id": 2, "key": "TEST-42",
                             "state": "updated", "message": "Done"}]):
            result = {"features": [], "jira_sync": results}
            with patch.object(portal, "pm", SimpleNamespace(refresh=Mock(return_value=result))):
                job = {"repos": []}
                portal._run_pr_scan(job)
            self.assertEqual(job["status"], "done")
            self.assertEqual(result["jira_sync_counts"]["updated"], len(results))
            if results:
                self.assertIn("1 updated", job["message"])
                self.assertIn("0 error", job["message"])

    def test_pr_scan_with_no_active_features_keeps_archived_failure_and_owner_error(self):
        result = {"features": [], "error": "GitHub unavailable", "jira_sync": [{
            "feature_id": 90, "key": "TEST-90", "state": "error",
            "message": "Jira unavailable"}]}
        with patch.object(portal, "pm", SimpleNamespace(refresh=Mock(return_value=result))):
            job = {"repos": []}
            portal._run_pr_scan(job)
        self.assertEqual(job["status"], "error")
        self.assertEqual(job["result"]["jira_sync_counts"]["error"], 1)
        self.assertIn("GitHub unavailable", job["message"])
        self.assertIn("feature 90 / TEST-90: error", job["message"])

    def test_real_owner_coverage_scan_lifecycle_and_archived_jira_retry(self):
        owner = portal.pm
        database = Path(__file__).resolve().parent / f".prs-integration-{uuid4().hex}.db"
        def cleanup():
            for suffix in ("", "-wal", "-shm"):
                database.with_name(database.name + suffix).unlink(missing_ok=True)
        self.addCleanup(cleanup)

        class FakeJira:
            status = "In Progress"
            fail_done = False

            def __init__(self):
                self.updates = []

            def myself(self):
                return {"accountId": "portal-test-user"}

            def issue(self, key):
                return {"key": key, "fields": {
                    "status": {"id": self.status, "name": self.status,
                               "statusCategory": {
                                   "key": "done" if self.status == "Done" else "indeterminate"}},
                    "assignee": {"accountId": "portal-test-user"},
                    "issuetype": {"name": "Story", "subtask": False}}}

            def transitions(self, key):
                return [{"id": name, "name": name, "to": {
                    "id": name, "name": name, "statusCategory": {
                        "key": "done" if name == "Done" else "indeterminate"}}}
                        for name in ("In Review", "Done")]

            def transition(self, key, transition_id):
                self.updates.append((key, transition_id))
                if transition_id == "Done" and self.fail_done:
                    raise owner.jira_sync.JiraError("mock Jira Done update unavailable")
                self.status = transition_id

        jira = FakeJira()
        merged = False
        def probe(pr):
            return {**pr, "status": "merged" if merged else "pending",
                    "state": "MERGED" if merged else "OPEN"}

        with patch.dict("os.environ", {"PRS_DB": str(database)}), \
                patch.object(owner, "probe", side_effect=probe), \
                patch.object(owner, "queue_states", return_value={}), \
                patch.object(owner, "_gh", side_effect=AssertionError("unexpected live GitHub call")), \
                patch.object(owner.jira_sync, "JiraClient", return_value=jira):
            self.assertEqual(owner.db.path(), database)
            ok, message, fid = owner.add_feature(
                "Portal Jira integration", refs=[
                    "https://github.com/example/portal-test/pull/12",
                    "https://github.com/example/portal-test/pull/13"],
                jira="TEST-42", lookup=False)
            self.assertTrue(ok, message)
            feature = next(f for f in owner.payload()["features"] if f["id"] == fid)
            self.assertEqual(feature["jira_coverage"], [])

            handler = self.handler("/api/feature-jira-coverage", {
                "feature_id": fid, "key": "TEST-42", "confirmed": True,
                "expected_revision": feature["revision"]})
            status, response = handler.do_POST()
            self.assertEqual(status, 200, response)
            self.assertEqual(response["prs"]["features"][0]["jira_coverage"], ["TEST-42"])
            self.assertEqual(jira.updates, [])

            review_job = {"repos": []}
            portal._run_pr_scan(review_job)
            self.assertEqual(review_job["status"], "done", review_job["message"])
            self.assertEqual(jira.status, "In Review")
            self.assertEqual(jira.updates, [("TEST-42", "In Review")])
            self.assertEqual(review_job["result"]["jira_sync_counts"]["updated"], 1)
            self.assertEqual(review_job["result"]["jira_sync"][0]["target"], "In Review")

            merged = True
            jira.fail_done = True
            retired_job = {"repos": []}
            portal._run_pr_scan(retired_job)
            self.assertEqual(retired_job["status"], "error", retired_job["message"])
            self.assertIn("result", retired_job, retired_job["message"])
            self.assertEqual([f["id"] for f in retired_job["result"]["purged"]], [fid])
            self.assertEqual(retired_job["result"]["features"], [])
            self.assertEqual(retired_job["result"]["jira_sync_counts"]["error"], 1)
            self.assertIn("mock Jira Done update unavailable", retired_job["message"])

            archived_job = {"repos": []}
            portal._run_pr_scan(archived_job)
            self.assertEqual(archived_job["status"], "error", archived_job["message"])
            self.assertEqual(archived_job["result"]["features"], [])
            self.assertEqual(archived_job["result"]["jira_sync"][0]["feature_id"], fid)
            self.assertIn(f"feature {fid} / TEST-42: error", archived_job["message"])
            self.assertIn("archived retries", archived_job["message"])
            cached = owner.payload()
            self.assertEqual(cached["features"], [])
            self.assertEqual(cached["jira_sync"][0]["feature_id"], fid)
            self.assertEqual(cached["jira_sync"][0]["state"], "error")

            jira.fail_done = False
            retry_job = {"repos": []}
            portal._run_pr_scan(retry_job)
            self.assertEqual(retry_job["status"], "done", retry_job["message"])
            self.assertEqual(jira.status, "Done")
            self.assertEqual(retry_job["result"]["features"], [])
            self.assertEqual(retry_job["result"]["jira_sync_counts"]["updated"], 1)
            self.assertEqual(retry_job["result"]["jira_sync"][0]["target"], "Done")
            self.assertEqual(retry_job["result"]["jira_sync"][0]["feature_id"], fid)
            self.assertEqual(jira.updates[-1], ("TEST-42", "Done"))
            self.assertEqual(len(jira.updates), 4)

    def test_vm_workspace_check_routes_to_remote_scope(self):
        handler = self.handler("/api/ws-check-vm", {})
        with patch.object(portal, "start_workspace_job", return_value="job-1") as start:
            status, response = handler.do_POST()
        self.assertEqual(status, 200)
        self.assertEqual(response["align_job"], "job-1")
        self.assertEqual(start.call_args.args, ("refresh",))
        self.assertEqual(start.call_args.kwargs["scope"], "remote")

    def test_vm_workspace_check_surfaces_conflict(self):
        handler = self.handler("/api/ws-check-vm", {})
        with patch.object(portal, "start_workspace_job", side_effect=ValueError("already running")):
            status, response = handler.do_POST()
        self.assertEqual(status, 409)
        self.assertEqual(response["error"], "already running")

    def test_saved_workspace_get_uses_cached_owner_payload(self):
        handler = self.handler("/api/workspaces", {})
        with patch.object(portal.wm, "load", return_value={"tracked": {}}), \
                patch.object(portal, "ws_ui_payload", return_value={"roster": []}) as payload, \
                patch.object(portal, "start_workspace_job") as start:
            status, response = handler.do_GET()
        self.assertEqual(status, 200)
        self.assertEqual(response, {"roster": []})
        payload.assert_called_once_with({"tracked": {}}, cached_only=True)
        start.assert_not_called()

    def test_cached_workspace_payload_does_not_use_live_viability(self):
        rows = [{"slot": "one", "target": "", "rev": "main", "exists": True}]
        with patch.object(portal.wm, "roster", return_value=rows) as roster, \
                patch.object(portal.wm, "groups", return_value=[]), \
                patch.object(portal.wm, "machine_targets", return_value=[]), \
                patch.object(portal, "viable_slots", side_effect=AssertionError("live roster")):
            payload = portal.ws_ui_payload({}, cached_only=True)
        self.assertEqual(payload["canonical"], ["one"])
        roster.assert_called_once_with({}, cached_only=True)

    def test_saved_tasks_get_includes_cached_workspace_data_without_probes(self):
        handler = self.handler("/api/tasks", {})
        saved = {"tracked": {}, "slots": {}}
        with patch.object(portal.wm, "load", return_value=saved), \
                patch.object(portal.tm, "load", return_value={"tasks": []}), \
                patch.object(portal, "ws_ui_payload", return_value={"roster": []}) as payload, \
                patch.object(portal.wm, "workspace_request") as probe:
            status, response = handler.do_GET()
        self.assertEqual(status, 200)
        self.assertEqual(response, {"tasks": [], "workspaces": {"roster": []}})
        payload.assert_called_once_with(saved, cached_only=True)
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
