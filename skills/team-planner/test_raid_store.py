"""Isolated RAID migration and owner regression tests (never use live stores)."""
import os
from pathlib import Path
import shutil
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rd
import raid_store as db


LEGACY = """# RAID log
Unknown preamble **retained**.
### R-007 | Risk
- **Title:** A [link](https://example.invalid)
- **Description:** first **bold**
  second `code`
- **FW Owner:** Alice
- **P:** 3 | **I:** 5 | **Score:** 15 | **RAG:** Red
- **Mitigation:** """ + ("long " * 150) + """
  continuation
- **Status:** Open
- **Custom:** untouched unknown field
  extra narrative

## Closed
### R-019 | Risk
- **Title:** historical
- **FW Owner:** Bob
- **Status:** **Closed 2020-01-01** — done
"""


class RaidTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).resolve().parent / (".test-raid-" + uuid4().hex)
        self.directory.mkdir()
        self.env = patch.dict(os.environ, {"RAID_DB": str(self.directory / "raid.db")})
        self.env.start()
        self.legacy = self.directory / "raid.md"
        self.legacy.write_text(LEGACY, encoding="utf-8")

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.directory)

    def test_import_round_trip_duplicate_and_evidence(self):
        self.assertTrue(db.migrate(self.legacy))
        self.assertFalse(db.migrate(self.legacy))
        output = self.directory / "export.md"
        db.export(output)
        self.assertEqual(output.read_text(), LEGACY)
        self.assertEqual(len(list((self.directory / "migration-backups").rglob("*.bak"))), 1)
        rows = rd.parse()
        self.assertEqual([r["id"] for r in rows], ["R-007", "R-019"])
        self.assertIn("second `code`", rows[0]["description_raw"])
        self.assertGreater(len(rows[0]["mitigation_raw"]), 700)
        self.assertEqual(rows[0]["fields_raw"]["Custom"], "untouched unknown field\n  extra narrative")
        with db.edit() as conn:
            self.assertEqual(conn.execute("SELECT original FROM raid_imports").fetchone()[0], LEGACY)
            self.assertGreater(conn.execute("SELECT count(*) FROM audit").fetchone()[0], 1)

    def test_partial_edit_conflict_history_and_no_markdown_write(self):
        db.migrate(self.legacy)
        revision = db.snapshot()[1]
        self.assertTrue(rd.apply_edit("R-007", {"title": "new", "expected_revision": revision})[0])
        self.assertFalse(rd.apply_edit("R-007", {"description": "stale", "expected_revision": revision})[0])
        self.assertIn("first **bold**\n  second `code`", db.snapshot()[0])
        self.assertIn("untouched unknown field\n  extra narrative", db.snapshot()[0])
        self.assertTrue(rd.close_row("R-007", "all done")[0])
        ok, rid = rd.new_row({"title": "next", "fw_owner": "Alice"})
        self.assertTrue(ok)
        self.assertEqual(rid, "R-020")
        self.assertEqual(self.legacy.read_text(), LEGACY)
        self.assertEqual(rd.payload()["path"], str(db.path()))
        with self.assertRaises(db.Conflict):
            db.save("stale", expected_revision=revision)

    def test_transaction_rollback(self):
        db.migrate(self.legacy)
        with self.assertRaises(RuntimeError):
            with db.edit():
                db.save("bad")
                raise RuntimeError("abort")
        self.assertEqual(db.snapshot()[0], LEGACY)

    def test_concurrent_id_allocation_and_multiline_edits(self):
        db.migrate(self.legacy)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda n: rd.new_row(
                {"title": f"new {n}", "fw_owner": "Alice"}), range(8)))
        self.assertEqual(len({rid for ok, rid in results if ok}), 8)
        self.assertTrue(rd.apply_edit("R-007", {"description": "unindented\nmultiline **text**"})[0])
        self.assertEqual(rd.parse()[0]["description_raw"], "unindented\nmultiline **text**")

    def test_original_line_endings_are_lossless(self):
        original = LEGACY.replace("\n", "\r\n").encode()
        self.legacy.write_bytes(original)
        db.migrate(self.legacy)
        output = self.directory / "export.md"
        db.export(output)
        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
