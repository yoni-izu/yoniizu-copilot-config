"""Portal task mutations remain inside the task-manager owner API."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import task_store
import tm


class PortalBulkEditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.db_path = patch.object(task_store, "DB_PATH", root / "tasks.db")
        self.legacy_path = patch.object(task_store, "LEGACY", root / "tasks.json")
        self.db_path.start()
        self.legacy_path.start()
        self.addCleanup(self.db_path.stop)
        self.addCleanup(self.legacy_path.stop)
        with task_store.transaction() as data:
            data["next_id"] = 4
            data["tasks"] = [
                {"id": "T-001", "title": "First", "category": "development",
                 "status": "todo", "priority": "medium", "complexity": "small",
                 "cost_hours": 2, "notes": []},
                {"id": "T-002", "title": "Second", "category": "planning",
                 "status": "backlog", "priority": "low", "complexity": None,
                 "notes": []},
                {"id": "T-003", "title": "Review", "category": "code-review",
                 "status": "in-progress", "priority": "high", "notes": [],
                 "reviews": [{"pr_url": "https://github.com/acme/repo/pull/3"}]},
            ]

    def revisions(self):
        return {task["id"]: tm.task_revision(task) for task in tm.load()["tasks"]}

    def test_multiple_fields_update_multiple_tasks_in_one_transaction(self):
        revisions = self.revisions()
        edits = [
            {"id": "T-001", "expected_revision": revisions["T-001"],
             "status": "blocked", "priority": "high"},
            {"id": "T-002", "expected_revision": revisions["T-002"],
             "status": "blocked", "priority": "high"},
        ]
        with patch.object(tm, "task_slot", return_value=None):
            ok, message = tm.apply_task_edits(edits)
        self.assertTrue(ok)
        self.assertEqual(message, "updated 2 task(s)")
        saved = {task["id"]: task for task in tm.load()["tasks"]}
        for ident in ("T-001", "T-002"):
            self.assertEqual(saved[ident]["status"], "blocked")
            self.assertEqual(saved[ident]["priority"], "high")
            self.assertTrue(any("dashboard bulk edit:" in note
                                for note in saved[ident]["notes"]))

    def test_stale_revision_rejects_the_entire_batch(self):
        revisions = self.revisions()
        edits = [
            {"id": "T-001", "expected_revision": revisions["T-001"], "priority": "critical"},
            {"id": "T-002", "expected_revision": "stale", "priority": "critical"},
        ]
        ok, message = tm.apply_task_edits(edits)
        self.assertFalse(ok)
        self.assertIn("revision conflict", message)
        saved = {task["id"]: task for task in tm.load()["tasks"]}
        self.assertEqual(saved["T-001"]["priority"], "medium")
        self.assertEqual(saved["T-002"]["priority"], "low")

    def test_invalid_category_rejects_batch_without_partial_updates(self):
        revisions = self.revisions()
        edits = [
            {"id": "T-001", "expected_revision": revisions["T-001"], "priority": "critical"},
            {"id": "T-003", "expected_revision": revisions["T-003"], "category": "planning"},
        ]
        ok, message = tm.apply_task_edits(edits)
        self.assertFalse(ok)
        self.assertIn("cannot drop code-review", message)
        saved = {task["id"]: task for task in tm.load()["tasks"]}
        self.assertEqual(saved["T-001"]["priority"], "medium")
        self.assertEqual(saved["T-003"]["category"], "code-review")

    def test_terminal_status_rejects_tasks_that_still_hold_a_workspace(self):
        revisions = self.revisions()
        with patch.object(tm, "task_slot", return_value="slot-1"):
            ok, message = tm.apply_task_edits([{
                "id": "T-001", "expected_revision": revisions["T-001"], "status": "done",
            }])
        self.assertFalse(ok)
        self.assertIn("occupies a workspace", message)
        self.assertEqual(
            next(task for task in tm.load()["tasks"] if task["id"] == "T-001")["status"],
            "todo")

    def test_bulk_edit_cannot_mark_unmerged_review_done(self):
        revision = self.revisions()["T-003"]
        with patch.object(tm, "task_slot", return_value=None):
            ok, message = tm.apply_task_edits([{
                "id": "T-003", "expected_revision": revision, "status": "done",
            }])
        self.assertFalse(ok)
        self.assertIn("before all PRs merge", message)
        review = next(task for task in tm.load()["tasks"] if task["id"] == "T-003")
        self.assertEqual(review["status"], "in-progress")

    def test_review_scan_probes_only_selected_tasks(self):
        data = {"tasks": [
            {"id": "T-001", "category": "code-review", "status": "in-progress",
             "reviews": [{"owner": "acme", "repo": "one", "pr_number": 1,
                          "review_status": "pending"}]},
            {"id": "T-002", "category": "code-review", "status": "in-progress",
             "reviews": [{"owner": "acme", "repo": "two", "pr_number": 2,
                          "review_status": "pending"}]},
        ]}
        with patch.object(tm, "load", return_value=data), \
                patch.object(tm, "save") as save, \
                patch.object(tm, "_fetch_pr_review_state",
                             return_value=({"merged": False, "state": "open"}, [])) as fetch:
            result = tm.portal_review_scan(task_ids=["T-001"])
        self.assertEqual(result["scanned"], 1)
        fetch.assert_called_once_with("acme", "one", 1)
        save.assert_called_once()

    def test_selected_review_scan_rejects_unknown_task_ids(self):
        with patch.object(tm, "load", return_value={"tasks": []}):
            with self.assertRaisesRegex(ValueError, "no longer exist"):
                tm.portal_review_scan(task_ids=["T-missing"])


class StaleWorkspaceEditTests(unittest.TestCase):
    """A task holding occupancy on a no-longer-tracked slot stays editable."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for target, attr, value in (
                (task_store, "DB_PATH", root / "tasks.db"),
                (task_store, "LEGACY", root / "tasks.json"),
                (tm.wm, "STORE", root / "workspaces.db"),
                (tm.wm.workspace_store, "LEGACY", root / "workspaces.json")):
            p = patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        with task_store.transaction() as data:
            data["next_id"] = 2
            data["tasks"] = [
                {"id": "T-001", "title": "Stale", "category": "development",
                 "status": "in-progress", "priority": "high", "notes": []}]
        tm.wm.save({
            "slots": {"main": {"holder": {
                "type": "task", "task_id": "T-001", "detail": "main",
                "since": "2026-08-05", "notes": []}}},
            "tracked": {"one": {"id": tm.wm.new_id(), "path": str(root / "one"),
                                "label": "one", "rev": "main"}},
            "groups": []})

    def test_terminal_status_edit_releases_stale_untracked_slot(self):
        with patch.object(tm.wm, "workspace_request") as request:
            ok, message = tm.apply_task_edit("T-001", {"status": "cancelled"})
        request.assert_not_called()
        self.assertTrue(ok, message)
        self.assertIsNone(tm.task_slot("T-001"))
        self.assertIsNone(tm.wm.get_holder("main"))
        self.assertEqual(tm.find_task(tm.load(), "T-001")["status"], "cancelled")

    def test_clearing_stale_workspace_field_succeeds(self):
        with patch.object(tm.wm, "workspace_request") as request:
            ok, message = tm.apply_task_edit("T-001", {"workspace": ""})
        request.assert_not_called()
        self.assertTrue(ok, message)
        self.assertIsNone(tm.task_slot("T-001"))
        self.assertIn("workspace main → (cleared)", message)


if __name__ == "__main__":
    unittest.main()
