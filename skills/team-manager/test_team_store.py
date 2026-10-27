from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from team_store import TeamStore


PROFILE = """# Test Member

- **Jira Account ID:** `abc-123`
- **GitHub:** `tester`
- **Confluence personal space (1:1 host):** https://x/y — folder z

## Day-Off Log

| # | Requested | Dates | Type | Duration | Response | Responded | Notes |
|---|-----------|-------|------|----------|----------|-----------|-------|
| 1 | 2026-01-01 | 2026-01-02 | Vacation | 1d | Approved | 2026-01-01 | old |

## Notes

- note

## 1:1 History

### 2026-07-06
"""

ARCHIVE = """# 1:1 with Test Member — 2026-07-06

## Summary
Delivered stuff.

## Decisions
- Keep direction.

## Action items
| # | Action | Owner | Due | Status | Jira |
|---|---|---|---|---|---|
| 1 | Follow up item | me | 2026-07-10 | Open | ABC-7 |
"""


class TeamStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        root = Path(self.tmp.name)
        self.db = root / "team.db"
        self.members = root / "members"
        self.members.mkdir()
        (self.members / "tester.md").write_text(PROFILE, encoding="utf-8")
        member_dir = self.members / "tester"
        (member_dir / "1on1").mkdir(parents=True)
        (member_dir / "1on1" / "2026-07-06.md").write_text(ARCHIVE, encoding="utf-8")
        self.store = TeamStore(self.db)
        self.store.init_db()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_migration_imports_profile_archive_but_not_leave(self) -> None:
        summary = self.store.migrate(self.members)
        self.assertEqual(summary["members_imported"], 1)
        self.assertEqual(summary["meetings_imported"], 1)
        self.assertEqual(summary["leave_imported"], 0)
        member = self.store.get_member("tester")
        self.assertIsNotNone(member)
        meetings = self.store.list_entities("meeting", "tester")
        self.assertEqual(len(meetings), 1)
        assignments = self.store.list_entities("assignment", "tester")
        self.assertEqual(len(assignments), 1)

    def test_repeat_import_conflict_on_changed_source(self) -> None:
        self.store.migrate(self.members)
        changed = PROFILE + "\n- changed\n"
        (self.members / "tester.md").write_text(changed, encoding="utf-8")
        summary = self.store.migrate(self.members)
        self.assertEqual(summary["members_imported"], 0)
        self.assertIn(str(self.members / "tester.md"), summary["conflicts"])

    def test_revision_guard(self) -> None:
        self.store.migrate(self.members)
        ident = self.store.create_entity(
            "assignment",
            "tester",
            {
                "title": "Direction",
                "owner_label": "lead",
                "kind": "direction",
                "status": "assigned",
            },
        )
        rows = self.store.list_entities("assignment", "tester")
        revision = rows[0]["revision"]
        self.store.update_entity("assignment", ident, revision, {"status": "in-progress"})
        with self.assertRaises(ValueError):
            self.store.update_entity("assignment", ident, revision, {"status": "done"})

    def test_metrics_separate_types(self) -> None:
        self.store.migrate(self.members)
        self.store.create_entity(
            "feature",
            "tester",
            {
                "title": "F",
                "status": "delivered",
                "delivered_at": "2026-06-01",
                "grouping_basis": "jira-title",
                "confirmation_state": "confirmed",
            },
        )
        self.store.create_entity(
            "issue-completion",
            "tester",
            {
                "issue_key": "BUG-1",
                "issue_type": "Bug",
                "resolution": "Done",
                "completed_at": "2026-06-05",
                "source": "test",
            },
        )
        self.store.create_entity(
            "issue-completion",
            "tester",
            {
                "issue_key": "ATTR-1",
                "issue_type": "Attribute",
                "resolution": "Done",
                "completed_at": "2026-06-05",
                "source": "test",
            },
        )
        self.store.create_entity(
            "issue-completion",
            "tester",
            {
                "issue_key": "ST-1",
                "issue_type": "Story",
                "resolution": "Done",
                "completed_at": "2026-06-05",
                "source": "test",
            },
        )
        metrics = self.store.metrics("tester", "total", date(2026, 10, 1))
        self.assertEqual(metrics["metrics"]["features_delivered"], 1)
        self.assertEqual(metrics["metrics"]["bugs_closed"], 1)
        self.assertEqual(metrics["metrics"]["attributes_completed"], 1)
        self.assertEqual(metrics["metrics"]["stories_completed"], 1)

    def test_payload_respects_audience(self) -> None:
        self.store.migrate(self.members)
        self.store.create_entity(
            "assignment",
            "tester",
            {
                "title": "Private assignment",
                "owner_label": "lead",
                "kind": "direction",
                "status": "assigned",
            },
        )
        private = self.store.payload("tester", "private")
        shared = self.store.payload("tester", "shared")
        self.assertGreaterEqual(len(private["members"][0]["assignments"]), 1)
        self.assertEqual(len(shared["members"][0]["assignments"]), 0)

    def test_group_prs_by_single_jira_key(self) -> None:
        grouped = self.store.group_prs_by_jira_title(
            [
                {"pr_ref": "o/r#1", "pr_title": "[IPSESW-10] part a", "contributor": "a"},
                {"pr_ref": "o/r#2", "pr_title": "[IPSESW-10] part b", "contributor": "b"},
                {"pr_ref": "o/r#3", "pr_title": "[IPSESW-11][IPSESW-12] mixed"},
                {"pr_ref": "o/r#4", "pr_title": "no key"},
            ]
        )
        self.assertEqual(len(grouped["candidates"]), 1)
        self.assertEqual(grouped["candidates"][0]["jira_key"], "IPSESW-10")
        self.assertEqual(grouped["candidates"][0]["pr_count"], 2)
        self.assertEqual(len(grouped["ambiguous_multi_key"]), 1)
        self.assertEqual(len(grouped["without_jira_key"]), 1)


if __name__ == "__main__":
    unittest.main()
