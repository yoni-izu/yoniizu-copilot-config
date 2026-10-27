from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from team_store import TeamStore
import team_ingest
from test_team_store import ARCHIVE, PROFILE


class TeamIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name)
        members = self.root / "members"
        (members / "tester" / "1on1").mkdir(parents=True)
        (members / "tester.md").write_text(PROFILE, encoding="utf-8")
        (members / "tester" / "1on1" / "2026-07-06.md").write_text(ARCHIVE, encoding="utf-8")
        self.store = TeamStore(self.root / "team.db")
        self.store.migrate(members)
        member = self.store.get_member("tester")
        self.store.update_member("tester", member["revision"], {
            "github_handle": "Tester", "jira_account_id": "acc-1",
            "base_country": "Poland",
        })

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _csv(self, name: str, text: str) -> Path:
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_add_member_validates_and_rejects_duplicates(self) -> None:
        self.store.add_member("newbie", {
            "display_name": "New Bie", "onboarding_date": "2026-09-07",
            "employment_type": "full-time", "main_ip_ownership": "D2D", "github_handle": "NewBie",
        })
        self.assertEqual(self.store.get_member("newbie")["github_handle"], "newbie")
        with self.assertRaises(ValueError):
            self.store.add_member("newbie", {"display_name": "Again"})
        with self.assertRaises(ValueError):
            self.store.add_member("other", {"display_name": "X", "github_handle": "newbie"})
        with self.assertRaises(ValueError):
            self.store.add_member("bad", {"display_name": "X", "employment_type": "intern"})
        with self.assertRaises(ValueError):
            self.store.add_member("bad", {"display_name": "X", "onboarding_date": "07/09/2026"})

    def test_member_update_requires_fresh_revision(self) -> None:
        with self.assertRaises(ValueError):
            self.store.update_member("tester", "stale", {"main_ip_ownership": "PCIe"})
        with self.assertRaises(ValueError):
            rev = self.store.get_member("tester")["revision"]
            self.store.update_member("tester", rev, {"member_key": "x"})

    def test_schema_upgrade_from_v1(self) -> None:
        db = self.root / "old.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO meta VALUES('team_schema_version','1');"
            "CREATE TABLE leave_records(leave_id TEXT PRIMARY KEY, member_key TEXT NOT NULL,"
            " leave_type TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,"
            " requested_at TEXT, approved_at TEXT, cancelled_at TEXT, amount_days REAL,"
            " amount_source TEXT, source TEXT NOT NULL, notes TEXT, created_at TEXT NOT NULL,"
            " updated_at TEXT NOT NULL);"
        )
        conn.close()
        with TeamStore(db).connect() as up:
            cols = {r[1] for r in up.execute("PRAGMA table_info(leave_records)")}
            version = up.execute("SELECT value FROM meta WHERE key='team_schema_version'").fetchone()[0]
            entry_cols = {r[1] for r in up.execute("PRAGMA table_info(meeting_entries)")}
            rb_cols = {r[1] for r in up.execute("PRAGMA table_info(roadblocks)")}
        self.assertTrue({"fields_json", "record_type", "record_id"} <= entry_cols)
        self.assertTrue({"impact", "unblocker", "visibility"} <= rb_cols)
        self.assertIn("status", cols)
        self.assertIn("external_ref", cols)
        self.assertEqual(version, "8")

    def test_leave_import_preview_then_apply_idempotent(self) -> None:
        path = self._csv("hr.csv", (
            "Employee,Type,Start,End,Status,Days,Request ID\n"
            "Tester,Vacation,2026-02-02,2026-02-06,Approved,5,R1\n"
            "Tester,Vacation,2026-09-07,2026-09-08,Approved,2,R2\n"
            "Tester,Vacation,2026-03-02,2026-03-03,Rejected,2,R3\n"
        ))
        preview = team_ingest.import_leave_csv(self.store, path)
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(len(preview["new"]), 2)
        self.assertEqual(preview["skipped_rejected"], [4])
        self.assertIsNone(team_ingest.leave_stats(self.store, "tester")["days_taken_this_year"])
        applied = team_ingest.import_leave_csv(self.store, path, apply=True)
        self.assertEqual(applied["status"], "imported")
        again = team_ingest.import_leave_csv(self.store, path, apply=True)
        self.assertEqual(again["status"], "skipped-already-imported")
        stats = team_ingest.leave_stats(self.store, "tester", date(2026, 7, 1))
        self.assertEqual(stats["days_taken_this_year"], 5.0)
        self.assertFalse(stats["days_estimated"])
        self.assertEqual(stats["next_approved_leave"]["start_date"], "2026-09-07")
        self.assertEqual(stats["last_approved_leave"]["start_date"], "2026-02-02")

    def test_leave_cancel_update_and_validation(self) -> None:
        first = self._csv("a.csv", "member,start,end,status,id\ntester,2026-04-06,2026-04-07,approved,X\n")
        team_ingest.import_leave_csv(self.store, first, apply=True)
        cancel = self._csv("b.csv", "member,start,end,status,id\ntester,2026-04-06,2026-04-07,cancelled,X\n")
        result = team_ingest.import_leave_csv(self.store, cancel, apply=True)
        self.assertEqual(len(result["updated"]), 1)
        stats = team_ingest.leave_stats(self.store, "tester", date(2026, 7, 1))
        self.assertEqual(stats["days_taken_this_year"], 0.0)
        bad = self._csv("c.csv", (
            "member,start,end,status\n"
            "nobody,2026-05-01,2026-05-01,approved\n"
            "tester,05/06/2026,05/06/2026,approved\n"
        ))
        rejected = team_ingest.import_leave_csv(self.store, bad, apply=True)
        self.assertEqual(rejected["status"], "rejected-validation-errors")
        self.assertEqual(len(rejected["errors"]), 2)
        self.assertFalse(rejected["applied"])

    def test_country_working_days(self) -> None:
        for country, friday, sunday in ((" Israel ", 0, 1), ("Poland", 1, 0), ("India", 1, 0)):
            with self.subTest(country=country):
                self.assertEqual(team_ingest.weekdays(date(2026, 8, 14), date(2026, 8, 14), country), friday)
                self.assertEqual(team_ingest.weekdays(date(2026, 8, 16), date(2026, 8, 16), country), sunday)
                self.assertEqual(team_ingest.weekdays(date(2026, 8, 15), date(2026, 8, 15), country), 0)
                self.assertEqual(team_ingest.weekdays(date(2026, 8, 1), date(2026, 8, 14), country), 10)
        with self.assertRaises(ValueError):
            team_ingest.weekdays(date(2026, 8, 1), date(2026, 8, 2), "")

    def test_leave_types_country_proration_and_weekend_cap(self) -> None:
        member = self.store.get_member("tester")
        self.store.update_member("tester", member["revision"], {"base_country": "Israel"})
        path = self._csv("types.csv", (
            "member,type,start,end,status,days\n"
            "tester,Vacation,2026-08-16,2026-08-26,approved,\n"
            "tester,Sick leave,2026-06-10,2026-06-11,approved,2\n"
            "tester,Vacation,2026-08-14,2026-08-15,approved,2\n"
            "tester,Privilege Day,2026-01-01,2026-01-01,approved,1\n"
            "tester,Vacation,2026-09-01,2026-09-01,pending,1\n"
            "tester,Sick,2026-09-02,2026-09-02,cancelled,1\n"
        ))
        team_ingest.import_leave_csv(self.store, path, apply=True)
        partial = team_ingest.leave_stats(self.store, "tester", date(2026, 6, 10))
        self.assertEqual(partial["sick_leave_days_this_year"], 1)
        self.assertTrue(partial["sick_leave_days_estimated"])
        stats = team_ingest.leave_stats(self.store, "tester", date(2026, 10, 26))
        self.assertEqual(stats["days_taken_this_year"], 9)
        self.assertEqual(stats["sick_leave_days_this_year"], 2)
        self.assertTrue(stats["days_estimated"])
        self.assertFalse(stats["sick_leave_days_estimated"])
        self.assertEqual(stats["last_approved_leave"]["leave_type"], "vacation")
        self.assertIsNone(stats["next_approved_leave"])
        private = self.store.payload("tester", "private")["members"][0]
        shared = self.store.payload("tester", "shared")["members"][0]
        self.assertEqual(private["leave"]["sick_leave_days_this_year"], 2)
        self.assertNotIn("sick_leave_days_this_year", json.dumps(shared))

    def test_unknown_member_country_and_type_coverage(self) -> None:
        path = self._csv("coverage.csv", (
            "member,type,start,end,status\n"
            "tester,Vacation,2026-10-26,2026-10-30,approved\n"
        ))
        team_ingest.import_leave_csv(self.store, path, apply=True)
        stats = team_ingest.leave_stats(self.store, "tester", date(2026, 10, 26))
        self.assertEqual(stats["days_taken_this_year"], 1)
        self.assertIsNone(stats["sick_leave_days_this_year"])
        self.store.add_member("uncovered", {"display_name": "Uncovered", "base_country": "Israel"})
        self.assertIsNone(team_ingest.leave_stats(self.store, "uncovered")["days_taken_this_year"])
        self.assertIsNone(team_ingest.leave_stats(self.store, "uncovered")["hr_last_import_at"])
        member = self.store.get_member("tester")
        self.store.update_member("tester", member["revision"], {"base_country": None})
        stats = team_ingest.leave_stats(self.store, "tester", date(2026, 10, 26))
        self.assertIsNone(stats["days_taken_this_year"])
        self.assertIn("base_country is unknown", stats["note"])
        with self.assertRaises(ValueError):
            team_ingest.leave_stats(self.store, "missing")

    def test_leave_year_boundary_and_nonfinite_amounts(self) -> None:
        path = self._csv("boundary.csv", (
            "member,type,start,end,status,days\n"
            "tester,Vacation,2025-12-29,2026-01-04,approved,5\n"
        ))
        team_ingest.import_leave_csv(self.store, path, apply=True)
        self.assertEqual(team_ingest.leave_stats(self.store, "tester", date(2026, 1, 4))["days_taken_this_year"], 2)
        for amount in ("nan", "inf", "-1"):
            bad = self._csv("invalid.csv", (
                "member,start,end,status,days\n"
                f"tester,2026-01-05,2026-01-05,approved,{amount}\n"
            ))
            self.assertEqual(team_ingest.import_leave_csv(self.store, bad, apply=True)["status"],
                             "rejected-validation-errors")

    def _sources(self) -> tuple[Path, Path]:
        tasks = self.root / "tasks.db"
        conn = sqlite3.connect(tasks)
        conn.executescript(
            "CREATE TABLE tasks(id TEXT PRIMARY KEY, position INTEGER, body TEXT);"
            "CREATE TABLE task_reviews(task_id TEXT, position INTEGER, body TEXT);"
        )
        for tid, jira, done in (("T-1", "ABC-1", "2026-07-01"), ("T-2", "ABC-1", "2026-07-03"), ("T-3", None, None)):
            conn.execute("INSERT INTO tasks VALUES(?,?,?)", (tid, 0, json.dumps({
                "id": tid, "title": f"Review: feature {tid}", "jira": jira, "completed": done})))
        reviews = [
            ("T-1", {"pr_url": "u/1", "repo": "r", "pr_number": 1, "author": "tester", "merged": True}),
            ("T-2", {"pr_url": "u/2", "repo": "r", "pr_number": 2, "author": "tester", "merged": True}),
            ("T-3", {"pr_url": "u/3", "repo": "r", "pr_number": 3, "author": "tester", "merged": False}),
            ("T-3", {"pr_url": "u/4", "repo": "r", "pr_number": 4, "author": "stranger", "merged": True}),
        ]
        for i, (tid, body) in enumerate(reviews):
            conn.execute("INSERT INTO task_reviews VALUES(?,?,?)", (tid, i, json.dumps(body)))
        conn.commit()
        conn.close()
        bugs = self.root / "bugs.db"
        conn = sqlite3.connect(bugs)
        conn.execute("CREATE TABLE bugs(key TEXT PRIMARY KEY, status TEXT, is_open INTEGER, updated TEXT, data TEXT)")
        for key, res, is_open, who in (("B-1", "Done", 0, "acc-1"), ("B-2", "Duplicate", 0, "acc-1"),
                                       ("B-3", None, 1, "acc-1"), ("B-4", "Done", 0, "other")):
            conn.execute("INSERT INTO bugs VALUES(?,?,?,?,?)", (key, "x", is_open, "", json.dumps({
                "key": key, "resolution": res, "resolved": "2026-07-02" if not is_open else None,
                "assignee_id": who, "assignee": "Tester"})))
        conn.commit()
        conn.close()
        return tasks, bugs

    def test_backfill_preview_apply_metrics_and_retract(self) -> None:
        tasks, bugs = self._sources()
        preview = team_ingest.backfill_evidence(self.store, tasks_db=tasks, bugs_db=bugs)
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["unmapped_pr_authors"], {"stranger": 1})
        self.assertEqual(preview["bug_completions"], 2)
        self.assertEqual(preview["features"], 2)
        self.assertEqual(self.store.list_entities("feature", "tester"), [])

        team_ingest.backfill_evidence(self.store, tasks_db=tasks, bugs_db=bugs, apply=True, confirm_merged=True)
        features = {f["grouping_basis"]: f for f in self.store.list_entities("feature", "tester")}
        jira_feature = features["review-task+jira-key"]
        self.assertEqual(jira_feature["status"], "delivered")
        self.assertEqual(jira_feature["confirmation_state"], "confirmed")
        self.assertEqual(jira_feature["delivered_at"], "2026-07-03")
        self.assertEqual(features["review-task"]["confirmation_state"], "candidate")
        m = self.store.metrics("tester", "quarter", date(2026, 7, 15))["metrics"]
        self.assertEqual(m["features_delivered"], 1)
        self.assertEqual(m["bugs_closed"], 1)
        with self.store.connect() as conn:
            refs = conn.execute("SELECT domain_name, target_id FROM typed_refs WHERE provenance LIKE 'backfill:%'").fetchall()
        self.assertEqual(sorted((r[0], r[1]) for r in refs),
                         [("bugs", "B-1"), ("bugs", "B-2"), ("tasks", "T-1"), ("tasks", "T-2"), ("tasks", "T-3")])

        again = team_ingest.backfill_evidence(self.store, tasks_db=tasks, bugs_db=bugs, apply=True)
        self.assertEqual(again["retract_bug_completions"], 0)
        again_features = {f["grouping_basis"]: f for f in self.store.list_entities("feature", "tester")}
        self.assertEqual(again_features["review-task+jira-key"]["confirmation_state"], "confirmed")

        conn = sqlite3.connect(bugs)
        conn.execute("UPDATE bugs SET is_open=1 WHERE key='B-1'")
        conn.commit()
        conn.close()
        reopened = team_ingest.backfill_evidence(self.store, tasks_db=tasks, bugs_db=bugs, apply=True)
        self.assertEqual(reopened["retract_bug_completions"], 1)
        self.assertEqual(self.store.metrics("tester", "total", date(2026, 7, 15))["metrics"]["bugs_closed"], 0)

    def test_lead_included_in_backfill(self) -> None:
        tasks, bugs = self._sources()
        self.store.add_member("lead", {
            "display_name": "Lead", "roster_role": "lead", "jira_account_id": "other",
            "base_country": "Israel",
        })
        with self.assertRaises(ValueError):
            self.store.add_member("x", {"display_name": "X", "roster_role": "boss"})
        summary = team_ingest.backfill_evidence(self.store, tasks_db=tasks, bugs_db=bugs)
        self.assertEqual(summary["bug_completions"], 3)
        self.assertIn("lead", summary["per_member"])

    def test_private_payload_carries_leave_stats(self) -> None:
        private = self.store.payload("tester", "private")["members"][0]
        shared = self.store.payload("tester", "shared")["members"][0]
        self.assertIn("leave", private)
        self.assertNotIn("leave", shared)


if __name__ == "__main__":
    unittest.main()
