import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal


class TeamApiTests(unittest.TestCase):
    def _capture_handler(self):
        captured = {}

        class CaptureServer:
            def __init__(self, address, handler):
                captured["handler"] = handler
                raise RuntimeError("captured")

        with patch("http.server.ThreadingHTTPServer", CaptureServer), \
                patch.object(portal, "_server_running", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "captured"):
                portal.cmd_serve(SimpleNamespace(status=False, stop=False, port=0))
        return captured["handler"]

    def _handler(self, path, body=None, headers=None):
        klass = self._capture_handler()
        handler = object.__new__(klass)
        payload = json.dumps(body).encode() if body is not None else b""
        handler.path = path
        handler.headers = {"Content-Length": str(len(payload))}
        if headers:
            handler.headers.update(headers)
        handler.rfile = io.BytesIO(payload)
        handler._send = lambda code, text, *_: (code, json.loads(text))
        return handler

    def test_team_get_shared(self):
        handler = self._handler("/api/team")
        with patch.object(portal.team_api, "payload", return_value={"members": []}) as payload:
            status, body = handler.do_GET()
        self.assertEqual(status, 200)
        self.assertEqual(body, {"members": []})
        payload.assert_called_once_with("shared", None)

    def test_team_get_private_requires_header(self):
        handler = self._handler("/api/team?audience=private")
        status, body = handler.do_GET()
        self.assertEqual(status, 403)
        self.assertIn("private-mode", body["error"].lower())

    def test_team_get_private_with_header(self):
        handler = self._handler("/api/team?audience=private&as_of=2026-10-25", headers={"X-Team-Private": "1"})
        with patch.object(portal.team_api, "payload", return_value={"members": [], "as_of": "2026-10-25"}) as payload:
            status, body = handler.do_GET()
        self.assertEqual(status, 200)
        payload.assert_called_once_with("private", "2026-10-25")
        self.assertEqual(body["as_of"], "2026-10-25")

    def test_team_post_requires_private_header(self):
        handler = self._handler("/api/team", body={"action": "member-set", "data": {}}, headers={"Host": "127.0.0.1:8787"})
        status, body = handler.do_POST()
        self.assertEqual(status, 403)
        self.assertIn("private mode", body["error"].lower())

    def test_team_post_applies_mutation(self):
        payload = {"action": "member-set", "data": {"member_key": "akshay"}}
        handler = self._handler("/api/team", body=payload, headers={"X-Team-Private": "1", "Host": "127.0.0.1:8787"})
        with patch.object(portal.team_api, "mutate", return_value={"ok": True}) as mutate:
            status, body = handler.do_POST()
        self.assertEqual(status, 200)
        self.assertEqual(body, {"result": {"ok": True}})
        mutate.assert_called_once_with("member-set", {"member_key": "akshay"})

    def test_bulk_requires_private_and_same_origin_and_reports_conflicts(self):
        command = {"action": "bulk-set", "data": {
            "entity_type": "assignment", "member_key": "test", "items": [
                {"id": "a1", "revision": "r1"}], "field": "status", "value": "done",
        }}
        with patch.object(portal.team_api, "mutate") as mutate:
            handler = self._handler("/api/team", body=command)
            self.assertEqual(handler.do_POST()[0], 403)
            handler = self._handler("/api/team", body=command, headers={
                "X-Team-Private": "1", "Host": "127.0.0.1:8787", "Origin": "https://other.test"})
            self.assertEqual(handler.do_POST()[0], 403)
            mutate.assert_not_called()
        handler = self._handler("/api/team", body=command, headers={
            "X-Team-Private": "1", "Host": "127.0.0.1:8787"})
        with patch.object(portal.team_api, "mutate", side_effect=ValueError("stale revision; no rows changed")):
            status, body = handler.do_POST()
        self.assertEqual(status, 409)
        self.assertIn("no rows changed", body["error"])


if __name__ == "__main__":
    unittest.main()
