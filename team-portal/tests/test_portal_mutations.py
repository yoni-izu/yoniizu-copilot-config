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
