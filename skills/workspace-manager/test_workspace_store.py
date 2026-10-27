import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import workspace_store as store


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name, value in (("DB_PATH", self.root / "workspaces.db"),
                            ("LEGACY", self.root / "workspaces.json")):
            p = patch.object(store, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.wid = "WS-L-" + "a" * 32

    def seed(self):
        data = {"version": 1, "tracked": {"one": {"id": self.wid, "path": "/checkout",
                                                "unknown": {"keep": 1}}},
                "slots": {"one": {"holder": {"task_id": "T-1", "notes": []}}},
                "groups": [{"name": "special", "unknown": True}], "future": [1, 2]}
        store.LEGACY.write_text(json.dumps(data))
        return data

    def test_lossless_backed_up_migration_includes_status_and_history(self):
        original = self.seed()
        directory = self.root / "workspace-state"
        directory.mkdir()
        (directory / f"{self.wid}.json").write_text(json.dumps({"checked": "yesterday", "extra": 8}))
        event = {"at": "today", "action": "test", "unknown": 1}
        (directory / f"{self.wid}.jsonl").write_text(json.dumps(event) + "\n")
        self.assertEqual(store.load(), original)
        self.assertEqual(store.events(self.wid), [event])
        self.assertEqual(store.status_read(self.wid)["extra"], 8)
        self.assertEqual(len(list(self.root.glob("migration-backups/workspaces/*"))), 3)
        store.append_event(self.wid, {"action": "second"})
        store.LEGACY.write_text("corrupt after cutover")
        self.assertEqual(store.load(), original)
        self.assertEqual(len(store.events(self.wid)), 2)
        self.assertEqual((directory / f"{self.wid}.jsonl").read_text(), json.dumps(event) + "\n")

    def test_corrupt_input_no_empty_fallback(self):
        store.LEGACY.write_text("[]")
        with self.assertRaises(ValueError):
            store.load()
        self.seed()
        self.assertEqual(store.load()["tracked"]["one"]["id"], self.wid)

    def test_unknown_fields_disjoint_merges_and_conflicts(self):
        self.seed()
        a, b = store.load(), store.load()
        a["tracked"]["one"]["label"] = "a"
        b["tracked"]["one"]["badge"] = "b"
        store.save(a)
        store.save(b)
        saved = store.load()
        self.assertEqual(saved["tracked"]["one"]["unknown"], {"keep": 1})
        self.assertEqual(saved["tracked"]["one"]["label"], "a")
        self.assertEqual(saved["tracked"]["one"]["badge"], "b")
        a, b = store.load(), store.load()
        a["tracked"].pop("one")
        b["tracked"]["one"]["path"] = "/changed"
        store.save(a)
        with self.assertRaises(store.ConflictError):
            store.save(b)

    def test_transaction_rolls_back_roster_status_and_events(self):
        self.seed()
        original = store.load()
        with self.assertRaises(RuntimeError):
            with store.transaction() as data:
                data["tracked"].clear()
                store.status_write(self.wid, {"clean": True})
                store.append_event(self.wid, {"action": "rolled-back"})
                raise RuntimeError("abort")
        self.assertEqual(store.load(), original)
        self.assertEqual(store.status_read(self.wid), {})
        self.assertEqual(store.events(self.wid), [])

    def test_concurrent_holds_allocate_one_winner(self):
        barrier = threading.Barrier(6)
        winners, errors = [], []

        def reserve(i):
            try:
                barrier.wait()
                with store.transaction() as data:
                    if "one" not in data["slots"]:
                        data["slots"]["one"] = {"holder": {"task_id": str(i)}}
                        winners.append(i)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reserve, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(40)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(winners), 1)
        self.assertEqual(store.load()["slots"]["one"]["holder"]["task_id"], str(winners[0]))


if __name__ == "__main__":
    unittest.main()
