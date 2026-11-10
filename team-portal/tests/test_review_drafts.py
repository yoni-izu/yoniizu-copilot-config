"""Draft HTTP reads use registered task attachments, never arbitrary paths."""
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal


class DraftViewerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.root = root
        self.drafts = root / "drafts"
        self.drafts.mkdir()
        for module, name, value in (
            (portal.tm.task_store, "DB_PATH", root / "tasks.db"),
            (portal.tm.task_store, "LEGACY", root / "absent.json"),
            (portal.tm, "review_drafts_root", lambda: self.drafts),
        ):
            mock = patch.object(module, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.url = "https://github.com/example/repo/pull/1"
        with portal.tm.task_store.transaction() as data:
            data["tasks"] = [{"id": "T-001", "title": "Review", "category": "code-review",
                              "reviews": [{"pr_url": self.url, "repo": "repo", "pr_number": 1}]}]
        self.file = self.drafts / "review.md"
        self.file.write_text(
            "# Draft\n\n## Summary\n\n- Finding\n- [x] Reproduced\n\n"
            "| Check | Result |\n| --- | --- |\n| Build | passed |\n\n"
            "```python\nprint('safe')\n```\n\n"
            "~~obsolete~~\n\nhttps://example.com\n\n"
            "[unsafe](javascript:alert(1))\n\n"
            "</pre><script>alert('unsafe')</script>\nFinding"
        )
        self.draft = portal.tm.attach_review_draft("T-001", self.url, str(self.file), "a" * 40)
        self.url_path = f'/review-drafts/T-001/{self.draft["id"]}'
        captured = {}

        class CaptureServer:
            def __init__(self, address, handler):
                captured["handler"] = handler
                raise RuntimeError("captured")

        with patch("http.server.ThreadingHTTPServer", CaptureServer), \
                patch.object(portal, "_server_running", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "captured"):
                portal.cmd_serve(SimpleNamespace(status=False, stop=False, port=0))
        self.handler_class = captured["handler"]

    def request(self, path):
        handler = object.__new__(self.handler_class)
        handler.path = path
        handler.wfile = io.BytesIO()
        result = {"headers": {}}
        handler.send_response = lambda status: result.update(status=status)
        handler.send_header = lambda key, value: result["headers"].update({key: value})
        handler.end_headers = lambda: None
        handler.do_GET()
        result["body"] = handler.wfile.getvalue().decode()
        return result

    def test_registered_document_renders_gfm_safely_in_dark_theme(self):
        result = self.request(self.url_path)
        self.assertEqual(result["status"], 200)
        self.assertIn("text/html", result["headers"]["Content-Type"])
        self.assertEqual(result["headers"]["Cache-Control"], "no-store")
        self.assertIn("default-src 'none'", result["headers"]["Content-Security-Policy"])
        self.assertEqual(result["headers"]["X-Content-Type-Options"], "nosniff")
        self.assertIn("Finding", result["body"])
        self.assertIn("a" * 40, result["body"])
        self.assertIn('href="/#task/T-001"', result["body"])
        self.assertIn("not submitted to GitHub", result["body"])
        self.assertIn("<h1>Draft</h1>", result["body"])
        self.assertIn("<h2>Summary</h2>", result["body"])
        self.assertIn("<table>", result["body"])
        self.assertIn("<s>obsolete</s>", result["body"])
        self.assertIn('class="task-list-item-checkbox"', result["body"])
        self.assertIn('<code class="language-python">', result["body"])
        self.assertIn('href="https://example.com"', result["body"])
        self.assertNotIn('href="javascript:', result["body"])
        self.assertIn("color-scheme:dark", result["body"])
        self.assertIn("--bg:#090d1a", result["body"])
        self.assertIn("background:var(--panel)", result["body"])
        self.assertNotIn("<script>", result["body"])
        self.assertIn("&lt;script&gt;", result["body"])

    def test_missing_file_surfaces_error_without_filename(self):
        self.file.unlink()
        result = self.request(self.url_path)
        self.assertEqual(result["status"], 404)
        self.assertIn("missing", json.loads(result["body"])["error"])
        self.assertNotIn(str(self.root), result["body"])

    def test_unknown_ids_and_traversal_do_not_resolve_a_document(self):
        paths = ["/review-drafts/T-001/../../secret.md",
                 "/review-drafts/T-001/%2e%2e%2fsecret.md",
                 "/review-drafts/T-001/" + "f" * 32,
                 "/review-drafts/T-999/" + self.draft["id"]]
        for path in paths:
            with self.subTest(path=path):
                self.assertIn(self.request(path)["status"], (400, 404))

    def test_symlink_swap_and_unreadable_file_report_errors(self):
        outside = self.root / "outside.md"
        outside.write_text("NOT FOR PORTAL")
        self.file.unlink()
        self.file.symlink_to(outside)
        result = self.request(self.url_path)
        self.assertEqual(result["status"], 400)
        self.assertNotIn("NOT FOR PORTAL", result["body"])
        with patch.object(portal.tm, "read_review_draft", side_effect=PermissionError("denied")), \
                patch("sys.stderr", new_callable=io.StringIO) as errors:
            result = self.request(self.url_path)
        self.assertEqual(result["status"], 500)
        self.assertIn("read failed", errors.getvalue())

    def test_tasks_api_and_bootstrap_preserve_metadata_without_document_contents(self):
        data = portal.tm.load()
        patches = (
            patch.object(portal.tm, "load", return_value=data),
            patch.object(portal.wm, "load", return_value={"slots": {}}),
            patch.object(portal, "ws_ui_payload", return_value={}),
        )
        with patches[0], patches[1], patches[2]:
            response = self.request("/api/tasks")
        task = json.loads(response["body"])["tasks"][0]
        self.assertEqual(task["reviews"][0]["drafts"][0]["id"], self.draft["id"])
        self.assertNotIn("Finding", response["body"])
        with patch.object(portal, "ws_ui_payload", return_value={}), \
                patch.object(portal, "bug_payload", return_value={}), \
                patch.object(portal, "triage_payload", return_value={}), \
                patch.object(portal, "overview_payload", return_value={}), \
                patch.object(portal, "pr_payload", return_value={}), \
                patch.object(portal, "raid_payload", return_value={}), \
                patch.object(portal, "pldmq_payload", return_value={}), \
                patch.object(portal, "vmlog_payload", return_value={}), \
                patch.object(portal.team_api, "payload", return_value={}):
            boot = json.loads(portal.boot_payload(data["tasks"], {"slots": {}}))
        self.assertEqual(boot["tasks"][0]["reviews"][0]["drafts"][0]["id"], self.draft["id"])
        self.assertNotIn("Finding", json.dumps(boot))


if __name__ == "__main__":
    unittest.main()
