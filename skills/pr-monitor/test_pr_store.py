"""Outgoing PR tests use isolated SQLite files and mocked GitHub only."""
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pm
import pr_store as db

LEGACY = """# Memoir
Unknown introductory narrative.
## [2] Active
- **Logged:** 2026-01-01
- **Description:** First **bold** sentence.
  Full [link](https://example.invalid) narrative.
- **Alien:** preserve me
  arbitrary continuation
- **PLDM Test:** queued
- **PLDM Details:** old job
- **PRs:**
  | Owner | Repo | PR# | Branch | Title | Status |
  |-------|------|-----|--------|-------|--------|
  | acme | one | 12 | branch | title | pending |
- **2026-01-02 (review):** Full **note**
  second line
## [9] Old — COMPLETE
- **Description:** History **retained**.
- **PRs:**
  | acme | old | 8 | old | old title | merged |
"""
CACHE = {"features": [{"id": 2, "prs": [
    {"owner": "acme", "repo": "one", "number": 12, "status": "ci-fail",
     "checks_pass": 17, "checks_fail": 1, "failed_runs": ["run"],
     "in_queue": False, "unknown_probe": {"kept": True}}]}],
    "last_scan": "2026-01-02 13:00", "unknown_top": "kept"}


class PrTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(__file__).resolve().parent / (".test-prs-" + uuid4().hex)
        self.directory.mkdir()
        self.env = patch.dict(os.environ, {"PRS_DB": str(self.directory / "prs.db")})
        self.env.start()
        self.legacy = self.directory / "memoir.md"
        self.cache = self.directory / "pr-monitor-cache.json"
        self.legacy.write_text(LEGACY, encoding="utf-8")
        self.cache.write_text(json.dumps(CACHE), encoding="utf-8")
        self.gh = patch.object(pm, "_gh", side_effect=AssertionError("unexpected network"))
        self.gh.start()

    def tearDown(self):
        self.gh.stop()
        self.env.stop()
        shutil.rmtree(self.directory)

    def migrate(self):
        db.migrate(self.legacy, self.cache)

    def test_lossless_import_duplicate_and_cache_authority(self):
        self.migrate()
        self.assertFalse(db.migrate(self.legacy, self.cache))
        output = self.directory / "export.md"
        db.export(output)
        self.assertEqual(output.read_text(), LEGACY)
        records = pm.records()
        self.assertEqual([f["id"] for f in records], [2, 9])
        self.assertEqual(records[0]["notes"][0]["text"], "Full **note**\n  second line")
        self.assertIn("Full [link]", records[0]["description"])
        pr = pm.payload()["features"][0]["prs"][0]
        self.assertEqual((pr["status"], pr["checks_pass"], pr["checks_fail"]), ("ci-fail", 17, 1))
        self.assertEqual(pr["unknown_probe"], {"kept": True})
        with db.edit() as conn:
            row = conn.execute("SELECT original,cache_original FROM pr_imports").fetchone()
            self.assertEqual(row[0], LEGACY)
            self.assertEqual(json.loads(row[1]), CACHE)

    def test_partial_edit_notes_conflict_new_id_and_history(self):
        self.migrate()
        revision = db.snapshot()[1]
        self.assertTrue(pm.edit_feature(2, summary="new", expected_revision=revision)[0])
        self.assertFalse(pm.edit_feature(2, description="stale", expected_revision=revision)[0])
        self.assertIn("arbitrary continuation", db.snapshot()[0])
        self.assertTrue(pm.add_note(2, "new **note**\n  literal second line")[0])
        self.assertIn("Full **note**\n  second line", db.snapshot()[0])
        self.assertTrue(pm.set_pldm(2, "passed", "proof")[0])
        self.assertEqual(pm.payload()["features"][0]["prs"][0]["checks_fail"], 1)
        self.assertTrue(pm.drop_feature(2)[0])
        self.assertEqual(pm.payload()["features"], [])
        self.assertEqual(pm.add_feature("Next", lookup=False)[2], 10)
        self.assertEqual(self.legacy.read_text(), LEGACY)
        self.assertEqual(json.loads(self.cache.read_text()), CACHE)

    def test_multiline_replacement_and_pr_mutations(self):
        self.migrate()
        self.assertTrue(pm.edit_feature(2, description="replacement\n  new line")[0])
        self.assertNotIn("Full [link]", pm.records()[0]["description"])
        self.assertTrue(pm.add_pr(2, "acme/two#13", lookup=False)[0])
        self.assertTrue(pm.remove_pr(2, 13)[0])
        self.assertEqual(pm.records()[0]["description"], "replacement\n  new line")
        self.assertEqual(pm.payload()["features"][0]["prs"][0]["status"], "ci-fail")

    def test_refresh_conflict_preserves_concurrent_note(self):
        self.migrate()
        def probe(pr):
            pm.add_note(2, "concurrent preserved")
            return {**pr, "status": "approved"}
        with patch.object(pm, "probe", side_effect=probe), patch.object(pm, "queue_states", return_value={}):
            result = pm.refresh()
        self.assertTrue(result["retry"])
        self.assertIn("concurrent preserved", db.snapshot()[0])
        self.assertEqual(pm.payload()["features"][0]["prs"][0]["status"], "ci-fail")

    def test_partial_refresh_and_archive_keep_probe_detail(self):
        self.migrate()
        with patch.object(pm, "probe", side_effect=lambda pr: {**pr, "status": "merged",
                                                              "checks_pass": 42}), \
             patch.object(pm, "queue_states", return_value={}):
            result = pm.refresh(only=2)
        self.assertEqual(len(result["purged"]), 1)
        self.assertEqual(pm.records()[0]["prs"][0]["checks_pass"], 42)
        self.assertEqual(pm.records()[0]["prs"][0]["status"], "merged")
        self.assertEqual(self.legacy.read_text(), LEGACY)

    def test_rollback_and_stale_save(self):
        self.migrate()
        revision = db.snapshot()[1]
        with self.assertRaises(RuntimeError):
            with db.edit():
                db.save("bad")
                raise RuntimeError("abort")
        self.assertEqual(db.snapshot()[0], LEGACY)
        db.save(LEGACY)
        with self.assertRaises(db.Conflict):
            db.save("stale", expected_revision=revision)

    def test_concurrent_notes_and_id_allocation(self):
        self.migrate()
        def mutate(n):
            self.assertTrue(pm.add_note(2, f"parallel note {n}")[0])
            return pm.add_feature(f"parallel feature {n}", lookup=False)[2]
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(mutate, range(8)))
        self.assertEqual(len(set(ids)), 8)
        for n in range(8):
            self.assertIn(f"parallel note {n}", db.snapshot()[0])
        original = next(f for f in pm.payload()["features"] if f["id"] == 2)
        self.assertEqual(original["prs"][0]["checks_fail"], 1)

    def test_original_line_endings_are_lossless(self):
        original = LEGACY.replace("\n", "\r\n").encode()
        self.legacy.write_bytes(original)
        self.migrate()
        output = self.directory / "export.md"
        db.export(output)
        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
