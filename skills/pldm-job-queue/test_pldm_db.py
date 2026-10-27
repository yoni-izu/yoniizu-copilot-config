"""Fixture-only PLDM migration and concurrent update tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest
from unittest.mock import patch
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pldm_db
import pjq


class PldmTests(unittest.TestCase):
    def setUp(self):
        self.root = HERE / (".pldm-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.legacy = self.root / "pldm-jobs.json"
        self.path = self.root / "pldm.db"
        self.store = pldm_db.Store(self.path, self.legacy)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(pjq, "STORE", self.legacy))
        self.stack.enter_context(patch.object(pjq, "DB", self.path))
        # No external commands may escape this test suite.
        self.stack.enter_context(patch.object(pjq.subprocess, "run",
                                              side_effect=AssertionError("network forbidden")))

    def seed(self):
        value = {"jobs": [{"id": 12, "status": "running", "dispatch_run": "123456789012",
                           "queue_job_id": "00042", "exec_run": 999,
                           "unknown": {"future": [1, 2]}, "updated": "old"}],
                 "last_scan": "2026-10-01 10:00", "future_root": {"a": 1}}
        self.legacy.write_text(json.dumps(value))
        return value

    def audit_count(self):
        with sqlite3.connect(self.path) as c:
            return c.execute("SELECT count(*) FROM audit").fetchone()[0]

    def test_import_replay_export_backup_and_no_legacy_write(self):
        value = self.seed()
        original = self.legacy.read_bytes()
        self.assertEqual(self.store.load(), value)
        self.assertEqual(self.store.load(), value)
        self.assertEqual(len(list((self.root / "migration-backups/pldm").glob("*.bak"))), 1)
        pjq.update(12, note="new", checked="2026-10-25T13:00:00")
        self.assertEqual(self.legacy.read_bytes(), original)
        # Changed/malformed legacy data is ignored after the one-time import.
        self.legacy.write_text("not JSON")
        self.assertEqual(pjq.load()["jobs"][0]["note"], "new")
        self.assertEqual(pjq.load()["jobs"][0]["unknown"], value["jobs"][0]["unknown"])

    def test_corruption_is_explicit_and_import_can_retry(self):
        for value in ("{", "[]", '{"jobs":{}}', '{"jobs":[{"id":1},{"id":1}]}'):
            self.legacy.write_text(value)
            with self.assertRaises(ValueError):
                self.store.load()
        self.assertEqual(self.audit_count(), 0)
        value = self.seed()
        self.assertEqual(self.store.load(), value)

    def test_concurrent_record_ids_and_discovery_dedup(self):
        self.store.load()
        with ThreadPoolExecutor(max_workers=8) as workers:
            rows = list(workers.map(lambda n: pjq.record(note=str(n)), range(30)))
        self.assertEqual(len({r["id"] for r in rows}), 30)
        with ThreadPoolExecutor(max_workers=8) as workers:
            rows = list(workers.map(lambda _: pjq.record(octl_epoch=123), range(20)))
        self.assertEqual(len({r["id"] for r in rows}), 1)
        self.assertEqual(len(pjq.load()["jobs"]), 31)

    def test_patch_does_not_overwrite_concurrent_insert_or_other_job(self):
        one = pjq.record(note="one")
        two = pjq.record(note="two")
        snapshot = pjq.load()
        snapshot["jobs"][0]["note"] = "scanned"
        pjq.update(two["id"], note="other")
        three = pjq.record(note="concurrent insert")
        pjq.save(snapshot)
        jobs = {j["id"]: j for j in pjq.load()["jobs"]}
        self.assertEqual(jobs[one["id"]]["note"], "scanned")
        self.assertEqual(jobs[two["id"]]["note"], "other")
        self.assertIn(three["id"], jobs)

    def test_stale_scan_cannot_overwrite_cancel_and_audit_rolls_back(self):
        one = pjq.record(status="running")
        snapshot = pjq.load()
        snapshot["jobs"][0]["status"] = "passed"
        pjq.update(one["id"], status="cancelled", checked="newer")
        count = self.audit_count()
        with self.assertRaisesRegex(ValueError, "concurrent"):
            pjq.save(snapshot)
        self.assertEqual(self.audit_count(), count)
        self.assertEqual(pjq.get(one["id"])["status"], "cancelled")

    def test_forget_prune_do_not_resurrect_or_reuse_ids(self):
        one = pjq.record(status="running")
        snapshot = pjq.load()
        self.assertTrue(pjq.forget(one["id"])[0])
        snapshot["jobs"][0]["note"] = "old scan"
        with self.assertRaisesRegex(ValueError, "concurrent"):
            pjq.save(snapshot)
        two = pjq.record()
        self.assertGreater(two["id"], one["id"])
        self.assertIsNone(pjq.get(one["id"]))

    def test_failed_poll_keeps_checked_and_records_attempt_error(self):
        with patch.object(pjq, "_run_state", return_value=None):
            row = pjq._scan_one({"id": 1, "stage": "building", "dispatch_run": 3,
                                 "checked": "2026-10-01", "updated": "old"})
        self.assertEqual(row["checked"], "2026-10-01")
        self.assertEqual(row["updated"], "old")
        self.assertEqual(row["last_attempt"], row["error_at"])

    def test_future_version_rejected_and_transaction_rollback(self):
        self.store.load()
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE meta SET value='999' WHERE key='pldm_version'")
        with self.assertRaisesRegex(ValueError, "version"):
            self.store.load()

    def test_save_audit_failure_rolls_back_changes(self):
        row = pjq.record(note="old")
        count = self.audit_count()
        with patch.object(pldm_db, "audit", side_effect=RuntimeError("audit failure")):
            with self.assertRaises(RuntimeError):
                pjq.update(row["id"], note="new")
        self.assertEqual(pjq.get(row["id"])["note"], "old")
        self.assertEqual(self.audit_count(), count)

    def test_plain_dictionary_cannot_bypass_snapshot_conflict_checks(self):
        pjq.record(note="original")
        with self.assertRaisesRegex(ValueError, "snapshot"):
            pjq.save(dict(pjq.load()))
        self.assertEqual(pjq.load()["jobs"][0]["note"], "original")


if __name__ == "__main__":
    unittest.main()
