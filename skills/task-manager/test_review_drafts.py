import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
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
        args = dict(ident="T-001", filename=str(self.file), heads={self.url: self.sha})
        args.update(kwargs)
        return tm.attach_review_draft(**args)

    def test_one_task_level_report_covers_several_prs_and_preserves_other_data(self):
        before = tm.load()["tasks"][0]
        draft = self.attach(heads={self.url: self.sha, self.other: "C" * 40})
        task = tm.load()["tasks"][0]
        self.assertEqual(task["status"], before["status"])
        self.assertEqual(task["notes"], before["notes"])
        self.assertEqual(task["reviews"], before["reviews"])
        self.assertEqual(len(task["review_drafts"]), 1)
        stored = task["review_drafts"][0]
        self.assertEqual(stored["id"], draft["id"])
        self.assertEqual(stored["kind"], "report")
        self.assertEqual([(h["repo"], h["head_sha"]) for h in stored["heads"]],
                         [("repo", self.sha), ("other", "c" * 40)])
        read = tm.read_review_draft("T-001", draft["id"])
        self.assertEqual(read["content"], "# Review\nA finding.")
        self.assertEqual(len(read["heads"]), 2)

    def test_refinement_updates_same_id_and_new_pass_keeps_history(self):
        first = self.attach()
        self.file.write_text("Refined", encoding="utf-8")
        with patch.object(tm, "datetime") as clock:
            clock.now.return_value.isoformat.return_value = "2026-10-29T12:00:00+00:00"
            updated = self.attach(heads={self.url: self.sha, self.other: self.sha})
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["created_at"], first["created_at"])
        self.assertNotEqual(updated["updated_at"], first["updated_at"])
        self.assertEqual(len(updated["heads"]), 2)
        self.assertEqual(tm.read_review_draft("T-001", first["id"])["content"], "Refined")
        second = self.drafts / "second.md"
        second.write_text("Next pass", encoding="utf-8")
        self.attach(filename=str(second), heads={self.url: "b" * 40})
        self.assertEqual(len(tm.load()["tasks"][0]["review_drafts"]), 2)

    def test_invalid_registration_does_not_mutate_store(self):
        original = copy.deepcopy(tm.load())
        cases = [
            {"ident": "T-002"}, {"ident": "unknown"},
            {"heads": {"https://github.com/example/wrong/pull/1": self.sha}},
            {"heads": {self.url: self.sha, "https://github.com/example/wrong/pull/1": self.sha}},
            {"heads": {self.url: "short"}},
            {"heads": {}},
            {"heads": [(self.url, self.sha), (self.url, "b" * 40)]},
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
        self.assertEqual(tm.read_review_draft("T-001", registered["id"])["heads"][0]["head_sha"],
                         self.sha)

    def add_legacy(self, index, name, body, sha, updated):
        (self.drafts / name).write_text(body, encoding="utf-8")
        with tm.task_store.transaction() as data:
            data["tasks"][0]["reviews"][index].setdefault("drafts", []).append(
                {"id": uuid.uuid4().hex, "file": name, "head_sha": sha,
                 "created_at": updated, "updated_at": updated})
        return tm.load()["tasks"][0]["reviews"][index]["drafts"][-1]

    def test_legacy_per_pr_attachment_remains_readable(self):
        legacy = self.add_legacy(0, "pr-1-repo.md", "Old", self.sha, "2026-10-01")
        read = tm.read_review_draft("T-001", legacy["id"])
        self.assertEqual(read["kind"], "legacy-pr")
        self.assertEqual(read["heads"][0]["repo"], "repo")
        self.assertEqual(read["content"], "Old")

    def test_combine_builds_one_report_from_latest_per_pr_drafts(self):
        self.add_legacy(0, "a-old.md", "STALE", "1" * 40, "2026-10-01")
        self.add_legacy(0, "a-new.md", "# Repo review\n```sh\n# keep\n```", "2" * 40, "2026-10-02")
        self.add_legacy(1, "b.md", "## Other finding", "3" * 40, "2026-10-03")
        before = copy.deepcopy(tm.load()["tasks"][0]["reviews"])
        files = {p.name: p.read_text() for p in self.drafts.iterdir()}
        combined = tm.combine_legacy_review_drafts("T-001")
        task = tm.load()["tasks"][0]
        self.assertEqual(task["reviews"], before)
        for name, body in files.items():
            self.assertEqual((self.drafts / name).read_text(), body)
        self.assertEqual(combined["kind"], "combined")
        self.assertEqual([h["head_sha"] for h in combined["heads"]], ["2" * 40, "3" * 40])
        content = tm.read_review_draft("T-001", combined["id"])["content"]
        self.assertNotIn("STALE", content)
        self.assertIn("## repo#1", content)
        self.assertIn("## other#1", content)
        self.assertIn("## Repo review", content)
        self.assertIn("### Other finding", content)
        self.assertIn("# keep", content)
        self.assertNotIn("## keep", content)
        self.assertTrue(combined["file"].startswith("T-001-combined-"))

    def test_combine_without_legacy_drafts_fails_cleanly(self):
        original = copy.deepcopy(tm.load())
        with self.assertRaises(ValueError):
            tm.combine_legacy_review_drafts("T-001")
        self.assertEqual(tm.load(), original)
        self.assertEqual([p.name for p in self.drafts.iterdir()], ["first.md"])

    def test_cli_repeated_head_legacy_form_and_errors_are_explicit(self):
        args = ["review-draft", "T-001", "--file", str(self.file),
                "--head", f"{self.url}={self.sha}", "--head", f"{self.other}={self.sha}"]
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tm.main(args), 0)
        self.assertIn('"task_id": "T-001"', output.getvalue())
        self.assertEqual(len(tm.load()["tasks"][0]["review_drafts"][0]["heads"]), 2)
        legacy = ["review-draft", "T-001", "--file", str(self.file),
                  "--pr-url", self.url, "--head-sha", self.sha]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(tm.main(legacy), 0)
        self.assertEqual(len(tm.load()["tasks"][0]["review_drafts"]), 1)
        for bad in (["review-draft", "T-001", "--file", str(self.file)],
                    ["review-draft", "T-001", "--file", str(self.file), "--head", "nosha"],
                    ["review-draft", "T-001", "--file", str(self.file), "--pr-url", self.url]):
            with redirect_stderr(io.StringIO()) as error:
                self.assertEqual(tm.main(bad), 1)
            self.assertIn("draft not attached", error.getvalue())
        self.file.unlink()
        with redirect_stderr(io.StringIO()) as error:
            self.assertEqual(tm.main(args), 1)
        self.assertIn("draft not attached", error.getvalue())


if __name__ == "__main__":
    unittest.main()
