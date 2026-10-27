import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "maintenance", Path(__file__).resolve().parents[1] / "maintenance.py")
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "maintenance.db"
        replacement = patch.object(maintenance, "DB_PATH", self.path)
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_records_completion_and_audit_without_rewriting_start(self):
        started = maintenance.record("Approved extraction", "started")
        completed = maintenance.record("Verified", "completed", parent=started,
                                       validation="Fixture tests pass")
        rows = maintenance.history()
        self.assertEqual([r["id"] for r in rows], [completed, started])
        self.assertEqual(rows[0]["parent"], started)
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM audit").fetchone()[0], 2)
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                conn.execute("UPDATE actions SET summary='rewritten'")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                conn.execute("DELETE FROM actions")

    def test_rejects_missing_parent_and_empty_summary(self):
        with self.assertRaises(ValueError):
            maintenance.record("Result", "completed", parent=999)
        with self.assertRaises(ValueError):
            maintenance.record(" ", "started")
        self.assertEqual(maintenance.history(), [])


if __name__ == "__main__":
    unittest.main()
