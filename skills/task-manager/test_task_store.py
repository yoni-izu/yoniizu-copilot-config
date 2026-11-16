import copy
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import task_store as store


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name, value in (("DB_PATH", self.root / "tasks.db"),
                            ("LEGACY", self.root / "tasks.json")):
            p = patch.object(store, name, value)
            p.start()
            self.addCleanup(p.stop)

    def seed(self):
        data = {"next_id": 3, "unknown": {"kept": [1, 2]}, "tasks": [
            {"id": "T-001", "title": "first", "notes": ["original"],
             "reviews": [{"pr_url": "pr/1", "extra": True}], "extra": {"a": 1}},
            {"id": "T-002", "title": "second", "notes": []}]}
        store.LEGACY.write_text(json.dumps(data))
        return data

    def test_auto_assignment_skips_incompatible_dedicated_workspace(self):
        import tm
        args = tm.build_parser().parse_args(["add", "new work", "--workspace", "one"])
        with patch.object(tm.wm, "normalize_slot", return_value="one"), \
                patch.object(tm.wm, "get_holder", return_value=None), \
                patch.object(tm.wm, "record", return_value={"purpose": "review"}), \
                patch.object(tm.wm, "occupy_task") as occupy, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tm.cmd_add(args), 0)
        occupy.assert_not_called()
        self.assertIn("dedicated to Review", output.getvalue())
        self.assertEqual(store.load()["tasks"][0]["status"], "backlog")

    def test_failed_assignment_does_not_claim_success_in_task_notes_or_summary(self):
        import tm
        args = tm.build_parser().parse_args(["add", "new work", "--workspace", "one"])
        with patch.object(tm.wm, "normalize_slot", return_value="one"), \
                patch.object(tm.wm, "get_holder", return_value=None), \
                patch.object(tm.wm, "record", return_value={"purpose": "general"}), \
                patch.object(tm.wm, "occupy_task", return_value=(False, "dedication changed", None)), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(tm.cmd_add(args), 0)
        task = store.load()["tasks"][0]
        self.assertEqual(task["status"], "backlog")
        self.assertNotIn("ws=one", output.getvalue())
        self.assertIn("dedication changed", error.getvalue())
        self.assertTrue(any("assignment failed" in note for note in task["notes"]))
        self.assertFalse(any("auto-assigned" in note for note in task["notes"]))

    def test_assignment_policy_race_reports_backlog_without_success_note(self):
        import tm
        args = tm.build_parser().parse_args(["add", "new work", "--workspace", "one"])
        with patch.object(tm.wm, "normalize_slot", return_value="one"), \
                patch.object(tm.wm, "get_holder", return_value=None), \
                patch.object(tm.wm, "record", return_value={"purpose": "development"}), \
                patch.object(tm.wm, "occupy_task", side_effect=ValueError("dedication changed")), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(tm.cmd_add(args), 0)
        task = store.load()["tasks"][0]
        self.assertEqual(task["status"], "backlog")
        self.assertNotIn("ws=one", output.getvalue())
        self.assertIn("dedication changed", error.getvalue())
        self.assertFalse(any("auto-assigned" in note for note in task["notes"]))

    def test_compatible_assignment_reports_success_only_after_acquiring_workspace(self):
        import tm
        args = tm.build_parser().parse_args(["add", "new work", "--workspace", "one"])
        with patch.object(tm.wm, "normalize_slot", return_value="one"), \
                patch.object(tm.wm, "get_holder", return_value=None), \
                patch.object(tm.wm, "record", return_value={"purpose": "development"}), \
                patch.object(tm.wm, "occupy_task", return_value=(True, "assigned", None)) as occupy, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(tm.cmd_add(args), 0)
        task = store.load()["tasks"][0]
        occupy.assert_called_once_with("one", task["id"], detail="one")
        self.assertEqual(task["status"], "todo")
        self.assertIn("ws=one", output.getvalue())
        self.assertTrue(any("auto-assigned" in note for note in task["notes"]))

    def test_lossless_idempotent_backed_up_import(self):
        original = self.seed()
        self.assertEqual(store.load(), original)
        self.assertEqual(len(list(self.root.glob("migration-backups/tasks/*"))), 1)
        original_text = store.LEGACY.read_text()
        with store.transaction() as data:
            data["tasks"][0]["title"] = "changed"
        self.assertEqual(store.LEGACY.read_text(), original_text)
        store.LEGACY.write_text("broken")
        self.assertEqual(store.load()["tasks"][0]["title"], "changed")
        self.assertEqual(store.load()["unknown"], {"kept": [1, 2]})

    def test_corrupt_input_raises_and_rolls_back(self):
        store.LEGACY.write_text("{broken")
        with self.assertRaises(json.JSONDecodeError):
            store.load()
        self.seed()
        self.assertEqual(len(store.load()["tasks"]), 2)

    def test_disjoint_fields_and_tasks_merge(self):
        self.seed()
        a, b = store.load(), store.load()
        a["tasks"][0]["title"] = "new"
        b["tasks"][0]["priority"] = "high"
        b["tasks"][1]["title"] = "other"
        store.save(a)
        store.save(b)
        saved = store.load()
        self.assertEqual(saved["tasks"][0]["title"], "new")
        self.assertEqual(saved["tasks"][0]["priority"], "high")
        self.assertEqual(saved["tasks"][1]["title"], "other")
        self.assertEqual(saved["tasks"][0]["reviews"][0]["extra"], True)

    def test_conflicting_changes_explicit_and_no_partial_write(self):
        self.seed()
        a, b = store.load(), store.load()
        a["tasks"][0]["title"] = "a"
        b["tasks"][0]["title"] = "b"
        b["tasks"][1]["title"] = "must rollback"
        store.save(a)
        with self.assertRaises(store.ConflictError):
            store.save(b)
        self.assertEqual(store.load()["tasks"][1]["title"], "second")

    def test_notes_append_merge_and_rewrite_rejected(self):
        self.seed()
        a, b = store.load(), store.load()
        a["tasks"][0]["notes"].append("a")
        b["tasks"][0]["notes"].append("b")
        store.save(a)
        store.save(b)
        self.assertEqual(store.load()["tasks"][0]["notes"], ["original", "a", "b"])
        b["tasks"][0]["notes"][0] = "edited"
        with self.assertRaisesRegex(store.ConflictError, "append-only"):
            store.save(b)

    def test_deletion_commutes_with_other_task_edit_but_not_same_task(self):
        self.seed()
        delete, edit, conflict = store.load(), store.load(), store.load()
        delete["tasks"].pop(0)
        edit["tasks"][1]["title"] = "survives"
        conflict["tasks"][0]["title"] = "stale"
        store.save(delete)
        store.save(edit)
        with self.assertRaises(store.ConflictError):
            store.save(conflict)
        self.assertEqual(store.load()["tasks"], [{"id": "T-002", "title": "survives", "notes": []}])

    def test_transaction_rolls_back(self):
        self.seed()
        original = store.load()
        with self.assertRaises(RuntimeError):
            with store.transaction() as data:
                data["tasks"].clear()
                store.save(data)
                raise RuntimeError("abort")
        self.assertEqual(store.load(), original)

    def test_review_entries_merge_independently(self):
        self.seed()
        with store.transaction() as data:
            data["tasks"][0]["reviews"].append({"pr_url": "pr/2", "unknown": 7})
        a, b = store.load(), store.load()
        a["tasks"][0]["reviews"][0]["review_status"] = "approved"
        b["tasks"][0]["reviews"][1]["review_status"] = "pending"
        store.save(a)
        store.save(b)
        reviews = store.load()["tasks"][0]["reviews"]
        self.assertEqual([r["review_status"] for r in reviews], ["approved", "pending"])
        self.assertEqual(reviews[1]["unknown"], 7)

    def test_concurrent_deletion_preserves_disjoint_edit(self):
        self.seed()
        barrier = threading.Barrier(2)
        errors = []

        def mutate(delete):
            try:
                data = store.load()
                if delete:
                    data["tasks"].pop(0)
                else:
                    data["tasks"][1]["title"] = "concurrent"
                barrier.wait()
                store.save(data)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=mutate, args=(i,)) for i in (True, False)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(40)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(store.load()["tasks"], [{"id": "T-002", "title": "concurrent", "notes": []}])

    def test_concurrent_atomic_allocation(self):
        errors = []
        barrier = threading.Barrier(8)

        def allocate():
            try:
                barrier.wait()
                with store.transaction() as data:
                    ident = data["next_id"]
                    data["next_id"] += 1
                    data["tasks"].append({"id": f"T-{ident:03}", "notes": []})
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=allocate) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(40)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        saved = store.load()
        self.assertEqual(saved["next_id"], 9)
        self.assertEqual(len({t["id"] for t in saved["tasks"]}), 8)

    def test_snapshot_deepcopy_and_plain_dict_guard(self):
        self.seed()
        data = copy.deepcopy(store.load())
        data["tasks"][0]["extra"]["a"] = 4
        store.save(data)
        with self.assertRaises(store.ConflictError):
            store.save(dict(data))

    def test_process_allocation_and_explicit_export(self):
        script = (
            "import task_store as s\n"
            "with s.transaction() as d:\n"
            " i=d['next_id']; d['next_id']+=1\n"
            " d['tasks'].append({'id':f'T-{i:03}', 'notes':[]})\n")
        env = dict(os.environ, TASKS_DB=str(store.DB_PATH),
                   TASKS_LEGACY_JSON=str(store.LEGACY))
        children = [subprocess.Popen([sys.executable, "-c", script], cwd=Path(__file__).parent,
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for _ in range(4)]
        for child in children:
            out, error = child.communicate(timeout=40)
            self.assertEqual(child.returncode, 0, (out, error))
        self.assertEqual(store.load()["next_id"], 5)
        exported = self.root / "explicit-export.json"
        subprocess.run([sys.executable, str(Path(store.__file__)), "export", str(exported)],
                       env=env, check=True, capture_output=True)
        self.assertEqual(json.loads(exported.read_text()), store.load())
        self.assertFalse(store.LEGACY.exists())


if __name__ == "__main__":
    unittest.main()
