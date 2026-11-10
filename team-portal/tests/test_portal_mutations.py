"""Local HTTP adapters use owner APIs and reject stale browser snapshots."""
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
