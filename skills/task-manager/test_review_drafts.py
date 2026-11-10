import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import tm


class ReviewDraftTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.drafts = self.root / "private" / "drafts"
        self.drafts.mkdir(parents=True)
        for module, name, value in (
            (tm.task_store, "DB_PATH", self.root / "tasks.db"),
            (tm.task_store, "LEGACY", self.root / "no-legacy.json"),
            (tm, "review_drafts_root", lambda: self.drafts),
        ):
            mock = patch.object(module, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.url = "https://github.com/example/repo/pull/1"
        self.other = "https://github.com/example/other/pull/1"
        with tm.task_store.transaction() as data:
            data["tasks"] = [
                {"id": "T-001", "title": "Review", "category": "code-review",
                 "status": "in-progress", "notes": ["keep"],
                 "reviews": [{"pr_url": url, "repo": repo, "pr_number": 1,
                              "review_status": "pending", "extra": True}
                             for url, repo in ((self.url, "repo"), (self.other, "other"))]},
                {"id": "T-002", "title": "Other", "category": "development"},
            ]
        self.file = self.drafts / "first.md"
        self.file.write_text("# Review\nA finding.", encoding="utf-8")
        self.sha = "a" * 40

    def attach(self, **kwargs):
        args = dict(ident="T-001", pr_url=self.url, filename=str(self.file), head_sha=self.sha)
        args.update(kwargs)
        return tm.attach_review_draft(**args)

    def test_persistent_pr_scoped_attachment_preserves_status_and_other_data(self):
        before = tm.load()["tasks"][0]
        draft = self.attach()
        task = tm.load()["tasks"][0]
        self.assertEqual(task["status"], before["status"])
        self.assertEqual(task["notes"], before["notes"])
        self.assertEqual(task["reviews"][1], before["reviews"][1])
        self.assertEqual(task["reviews"][0]["review_status"], "pending")
        self.assertTrue(task["reviews"][0]["extra"])
        self.assertEqual(task["reviews"][0]["drafts"][0]["id"], draft["id"])
        self.assertEqual(tm.read_review_draft("T-001", draft["id"])["content"], "# Review\nA finding.")

    def test_refinement_updates_same_id_and_new_pass_keeps_history(self):
        first = self.attach()
        self.file.write_text("Refined", encoding="utf-8")
        with patch.object(tm, "datetime") as clock:
            clock.now.return_value.isoformat.return_value = "2026-10-29T12:00:00+00:00"
            updated = self.attach()
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["created_at"], first["created_at"])
        self.assertNotEqual(updated["updated_at"], first["updated_at"])
        self.assertEqual(tm.read_review_draft("T-001", first["id"])["content"], "Refined")
        second = self.drafts / "second.md"
        second.write_text("Next pass", encoding="utf-8")
        self.attach(filename=str(second), head_sha="b" * 40)
        self.assertEqual(len(tm.load()["tasks"][0]["reviews"][0]["drafts"]), 2)

    def test_same_number_other_repo_is_separate(self):
        self.attach(pr_url=self.other)
        reviews = tm.load()["tasks"][0]["reviews"]
        self.assertNotIn("drafts", reviews[0])
        self.assertEqual(len(reviews[1]["drafts"]), 1)

    def test_invalid_registration_does_not_mutate_store(self):
        original = copy.deepcopy(tm.load())
        cases = [
            {"ident": "T-002"}, {"ident": "unknown"},
            {"pr_url": "https://github.com/example/wrong/pull/1"},
            {"head_sha": "short"},
            {"filename": str(self.root / "outside.md")},
            {"filename": str(self.drafts / "missing.md")},
        ]
        for args in cases:
            with self.subTest(args=args), self.assertRaises((ValueError, OSError)):
                self.attach(**args)
            self.assertEqual(tm.load(), original)

    def test_escape_and_symlink_are_rejected_on_attach_and_read(self):
        outside = self.root / "outside.md"
        outside.write_text("private other data")
        link = self.drafts / "link.md"
        link.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.attach(filename=str(link))
        registered = self.attach()
        self.file.unlink()
        self.file.symlink_to(outside)
        with self.assertRaises(ValueError):
            tm.read_review_draft("T-001", registered["id"])
        with self.assertRaises(ValueError):
            tm.read_review_draft("T-002", registered["id"])

    def test_missing_oversized_and_invalid_utf8_fail(self):
        registered = self.attach()
        self.file.unlink()
        with self.assertRaises(FileNotFoundError):
            tm.read_review_draft("T-001", registered["id"])
        for content in (b"x" * 2_000_001, b"\xff"):
            self.file.write_bytes(content)
            with self.assertRaises(ValueError):
                self.attach()
        self.file.write_bytes(b"x" * 2_000_000)
        self.attach()

    def test_legacy_single_review_supported(self):
        with tm.task_store.transaction() as data:
            task = data["tasks"][0]
            task["review"] = task.pop("reviews")[0]
        registered = self.attach()
        self.assertEqual(tm.read_review_draft("T-001", registered["id"])["head_sha"], self.sha)

    def test_cli_success_and_error_are_explicit(self):
        args = ["review-draft", "T-001", "--pr-url", self.url, "--file", str(self.file),
                "--head-sha", self.sha]
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tm.main(args), 0)
        self.assertIn('"task_id": "T-001"', output.getvalue())
        self.file.unlink()
        with redirect_stderr(io.StringIO()) as error:
            self.assertEqual(tm.main(args), 1)
        self.assertIn("draft not attached", error.getvalue())


if __name__ == "__main__":
    unittest.main()
