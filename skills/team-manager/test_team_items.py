from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from team_store import TeamStore


class TeamItemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TeamStore(Path(self.tmp.name) / "team.db")
        self.store.init_db()
        for key, role in (("lead", "lead"), ("member", "member"), ("other", "member")):
            self.store.portal_mutate("member-add", {
                "member_key": key, "display_name": key.title(), "roster_role": role,
            })

    def tearDown(self):
        self.tmp.cleanup()

    def meeting(self, entries, day="2026-11-01", member="member", **extra):
        return self.store.portal_mutate("meeting-create", {
            "member_key": member, "meeting_date": day, "entries": entries, **extra,
        })["entity"]

    def prepared(self, day="2026-11-08", member="member", **extra):
        return self.store.portal_mutate("meeting-prepare", {
            "member_key": member, "meeting_date": day, **extra,
        })["entity"]["entries"]

    def serial(self, entry):
        return {k: v for k, v in entry.items() if k in {
            "entry_id", "tag", "body", "fields", "visibility", "priority", "item_id",
            "item_revision", "tracking_status", "tracking_owner", "tracking_due",
            "follow_up", "follow_up_from",
        }}

    def item(self, ident):
        return next(i for i in self.store.portal_payload("private")["items"] if i["item_id"] == ident)

    def change(self, ident, **fields):
        item = self.item(ident)
        return self.store.portal_mutate("item-set", {
            "id": ident, "revision": item["revision"], **fields,
        })["entity"]

    def test_all_rows_are_items_and_follow_up_is_common(self):
        meeting = self.meeting([
            {"tag": "notes", "body": "A note", "follow_up": True, "visibility": "shared"},
            {"tag": "decision", "fields": {"decision": "A decision"}, "follow_up": True},
            {"tag": "feedback_received", "fields": {"feedback": "Feedback", "follow_up": "Keep this text"},
             "follow_up": True},
            {"tag": "growth", "fields": {"goal": "A goal"}, "follow_up": True},
            {"tag": "legacy-kind", "body": "Legacy", "follow_up": True},
        ])
        self.assertEqual(len({e["item_id"] for e in meeting["entries"]}), 5)
        carried = self.prepared()
        self.assertEqual(len(carried), 5)
        self.assertTrue(all(e["follow_up"] and e["tracking_status"] == "open" for e in carried))
        feedback = next(e for e in carried if e["tag"] == "feedback_received")
        self.assertEqual(feedback["fields"]["follow_up"], "Keep this text")

    def test_carry_reuses_identity_and_preserves_previous_meeting(self):
        first = self.meeting([{
            "tag": "discussion", "fields": {"topic": "Review", "follow_up": True},
            "visibility": "shared",
        }])
        ident = first["entries"][0]["item_id"]
        second_rows = [self.serial(e) for e in self.prepared()]
        second_rows[0]["fields"]["outcome"] = "Progress this week"
        second = self.meeting(second_rows, day="2026-11-08")
        third = self.meeting([self.serial(e) for e in self.prepared("2026-11-15")], day="2026-11-15")
        self.assertEqual(second["entries"][0]["item_id"], ident)
        self.assertEqual(third["entries"][0]["item_id"], ident)
        self.assertEqual(third["entries"][0]["follow_up_from"], "2026-11-08")
        item = self.item(ident)
        self.assertEqual(len(item["occurrences"]), 3)
        saved_first = next(m for m in self.store.portal_payload("private")["meetings"]
                           if m["meeting_id"] == first["meeting_id"])
        self.assertEqual(saved_first["entries"][0]["fields"], first["entries"][0]["fields"])
        self.assertEqual(saved_first["revision"], first["revision"])

    def test_follow_up_stops_when_cleared_done_or_cancelled_and_can_resume(self):
        for changes in ({"follow_up": False}, {"status": "done"}, {"status": "cancelled"}):
            with self.subTest(changes=changes):
                meeting = self.meeting([{"tag": "notes", "body": str(changes), "follow_up": True}])
                ident = meeting["entries"][0]["item_id"]
                self.change(ident, **changes)
                self.assertNotIn(ident, [e["item_id"] for e in self.prepared()])
                self.change(ident, status="open", follow_up=True)
                self.assertIn(ident, [e["item_id"] for e in self.prepared()])
                self.change(ident, follow_up=False)

    def test_carry_scopes_member_date_and_log_type(self):
        self.meeting([{"tag": "notes", "body": "Expected", "follow_up": True}])
        self.meeting([{"tag": "notes", "body": "Future", "follow_up": True}], day="2026-12-01")
        self.meeting([{"tag": "notes", "body": "Other", "follow_up": True}], member="other")
        self.meeting([{"tag": "notes", "body": "Not a 1:1", "follow_up": True}],
                     session_type="conversation")
        self.assertEqual([e["body"] for e in self.prepared()], ["Expected"])
        self.assertEqual(self.prepared("2026-10-30"), [])
        self.assertEqual(len(self.prepared("2026-11-01")), 1)
        self.assertEqual(self.prepared(session_type="meeting"), [])

    def test_tasks_keep_one_assignment_and_authoritative_task_state(self):
        first = self.meeting([{
            "tag": "action", "fields": {"title": "A task", "owner": "me", "target_date": "2026-11-20"},
            "follow_up": True, "visibility": "shared",
        }])
        ident, assignment = first["entries"][0]["item_id"], first["entries"][0]["record_id"]
        for day in ("2026-11-08", "2026-11-15"):
            self.meeting([self.serial(e) for e in self.prepared(day)], day=day)
        payload = self.store.portal_payload("private")
        self.assertEqual(len(payload["assignments"]), 1)
        self.assertEqual(self.item(ident)["assignment_id"], assignment)
        changed = self.change(ident, status="in-progress", target_date="2026-11-22")
        self.assertEqual((changed["status"], changed["target_date"]), ("in-progress", "2026-11-22"))
        task = self.store.portal_payload("private")["assignments"][0]
        self.store.portal_mutate("assignment-set", {
            "id": assignment, "revision": task["revision"], "status": "done",
        })
        self.assertEqual(self.item(ident)["status"], "done")
        self.assertEqual(self.prepared("2026-11-22"), [])

    def test_carried_status_does_not_duplicate_task_progress(self):
        task = self.store.portal_mutate("assignment-create", {
            "member_key": "member", "title": "Task",
        })["entity"]
        self.meeting([{
            "tag": "status", "fields": {"assignment_id": task["assignment_id"], "note": "Progress"},
            "follow_up": True,
        }])
        self.meeting([self.serial(e) for e in self.prepared()], day="2026-11-08")
        task = self.store.portal_payload("private")["assignments"][0]
        self.assertEqual(len(task["updates"]), 1)

    def test_private_items_and_private_history_never_enter_shared_payload(self):
        meeting = self.meeting([
            {"tag": "notes", "body": "PRIVATE-CONTENT", "follow_up": True},
            {"tag": "discussion", "fields": {"topic": "Public"}, "follow_up": True, "visibility": "shared"},
        ], title="PRIVATE-TITLE")
        ident = meeting["entries"][1]["item_id"]
        self.store.portal_mutate("item-note", {
            "id": ident, "revision": self.item(ident)["revision"], "note": "PRIVATE-UPDATE",
        })
        shared = self.store.portal_payload("shared")
        self.assertNotIn("PRIVATE-", json.dumps(shared))
        self.assertEqual(len(shared["items"]), 1)
        self.change(ident, visibility="private")
        self.assertEqual(self.store.portal_payload("shared")["meetings"], [])
        self.assertEqual(self.store.portal_payload("shared")["items"], [])

    def test_duplicate_cross_member_and_stale_carries_rollback(self):
        first = self.meeting([{"tag": "notes", "body": "Follow", "follow_up": True}])
        entry = self.serial(self.prepared()[0])
        for rows, member in (([entry, entry], "member"), ([entry], "other")):
            with self.assertRaises(ValueError):
                self.meeting(rows, day="2026-11-08", member=member)
        self.change(first["entries"][0]["item_id"], priority="high")
        with self.assertRaisesRegex(ValueError, "revision"):
            self.meeting([entry], day="2026-11-08")
        self.assertEqual(len(self.store.portal_payload("private")["meetings"]), 1)

    def test_invalid_tracking_values_are_rejected(self):
        for extra in ({"follow_up": "yes"}, {"tracking_status": "invented"},
                      {"tracking_owner": "somebody"}, {"tracking_due": "tomorrow"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.meeting([{"tag": "notes", "body": "No", **extra}])

    def test_deleting_an_occurrence_keeps_item_used_by_later_meetings(self):
        first = self.meeting([{
            "tag": "roadblock", "fields": {"blocker": "Need a board"}, "follow_up": True,
        }])
        second = self.meeting([self.serial(e) for e in self.prepared()], day="2026-11-08")
        self.store.portal_mutate("meeting-delete", {
            "id": first["meeting_id"], "revision": first["revision"],
        })
        item = self.item(second["entries"][0]["item_id"])
        self.assertTrue(item["follow_up"])
        self.assertEqual(len(item["occurrences"]), 1)
        self.assertEqual(len(self.prepared("2026-11-15")), 1)

    def test_v10_upgrade_registers_old_rows_flags_and_tasks_once(self):
        old = self.meeting([
            {"tag": "discussion", "fields": {"topic": "Existing", "follow_up": True}},
            {"tag": "notes", "body": "Historical text"},
            {"tag": "action", "fields": {"title": "Task"}, "visibility": "shared"},
        ])
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute("ALTER TABLE meeting_entries DROP COLUMN item_id")
            conn.execute("ALTER TABLE meeting_entries DROP COLUMN follow_up_from")
            conn.execute("DROP TABLE item_updates")
            conn.execute("DROP TABLE tracked_items")
            conn.execute("UPDATE meta SET value='10' WHERE key='team_schema_version'")
        payload = self.store.portal_payload("private")
        self.assertEqual(len(payload["items"]), 3)
        discussion = next(i for i in payload["items"] if i["item_type"] == "discussion")
        self.assertEqual((discussion["status"], discussion["follow_up"]), ("unspecified", True))
        self.assertEqual(next(i for i in payload["items"] if i["item_type"] == "notes")["status"], "unspecified")
        self.assertEqual(next(i for i in payload["items"] if i["item_type"] == "action")["status"], "todo")
        again = self.store.portal_payload("private")
        self.assertEqual([i["item_id"] for i in again["items"]], [i["item_id"] for i in payload["items"]])
        self.assertEqual(old["entries"][0]["fields"], payload["meetings"][0]["entries"][0]["fields"])

    def test_backdated_prep_does_not_copy_a_future_occurrence(self):
        self.meeting([{"tag": "notes", "body": "Original", "follow_up": True}])
        rows = [self.serial(e) for e in self.prepared("2026-11-15")]
        rows[0]["body"] = "Future discussion"
        self.meeting(rows, day="2026-11-15")
        self.assertEqual(self.prepared("2026-11-08"), [])
        self.assertEqual(self.prepared("2026-11-22")[0]["body"], "Future discussion")

    def test_standalone_task_follow_up_can_join_a_one_on_one(self):
        task = self.store.portal_mutate("assignment-create", {
            "member_key": "member", "title": "Standalone task",
        })["entity"]
        item = next(i for i in self.store.portal_payload("private")["items"]
                    if i.get("assignment_id") == task["assignment_id"])
        self.change(item["item_id"], follow_up=True)
        rows = self.prepared(day="2099-01-01")
        self.assertEqual(len(rows), 1)
        self.meeting([self.serial(e) for e in rows], day="2099-01-01")
        self.assertEqual(len(self.store.portal_payload("private")["assignments"]), 1)

    def test_task_visibility_changes_are_reflected_in_items(self):
        task = self.store.portal_mutate("assignment-create", {
            "member_key": "member", "title": "Task",
        })["entity"]
        self.store.portal_payload("private")
        self.store.portal_mutate("assignment-set", {
            "id": task["assignment_id"], "revision": task["revision"], "visibility": "shared",
        })
        self.assertEqual(len(self.store.portal_payload("shared")["items"]), 1)

    def test_completing_roadblock_updates_its_owned_record(self):
        meeting = self.meeting([{
            "tag": "roadblock", "fields": {"blocker": "Board needed"}, "follow_up": True,
        }])
        entry = meeting["entries"][0]
        changed = self.change(entry["item_id"], status="done")
        self.assertEqual(changed["status"], "done")
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM roadblocks WHERE roadblock_id=?",
                                         (entry["record_id"],)).fetchone()[0], "resolved")
        self.assertEqual(self.prepared(), [])
        self.change(entry["item_id"], status="open")
        self.assertEqual(len(self.prepared()), 1)

    def test_bulk_item_edits_are_atomic_across_types(self):
        meeting = self.meeting([
            {"tag": "notes", "body": "Note"},
            {"tag": "action", "fields": {"title": "Task", "owner": "member"}},
        ])
        items = [self.item(e["item_id"]) for e in meeting["entries"]]
        request = {"entity_type": "item", "member_key": "member", "field": "priority",
                   "value": "high", "items": [{"id": i["item_id"], "revision": i["revision"]} for i in items]}
        request["items"][1]["revision"] = "stale"
        with self.assertRaisesRegex(ValueError, "revision"):
            self.store.portal_mutate("bulk-set", request)
        self.assertTrue(all(self.item(i["item_id"])["priority"] == "medium" for i in items))
        request["items"][1]["revision"] = items[1]["revision"]
        self.store.portal_mutate("bulk-set", request)
        self.assertTrue(all(self.item(i["item_id"])["priority"] == "high" for i in items))

    def test_reimported_source_entry_keeps_identity(self):
        meeting = self.meeting([{"tag": "notes", "body": "Imported"}])
        entry = meeting["entries"][0]
        with self.store.connect() as conn:
            conn.execute("UPDATE meeting_entries SET item_id=NULL WHERE entry_id=?", (entry["entry_id"],))
        payload = self.store.portal_payload("private")
        self.assertEqual(payload["meetings"][0]["entries"][0]["item_id"], entry["item_id"])
        self.assertEqual(len(payload["items"]), 1)

    def test_shared_save_preserves_items_hidden_by_current_visibility(self):
        meeting = self.meeting([
            {"tag": "notes", "body": "PRIVATE-HIDDEN-LATER", "visibility": "shared"},
            {"tag": "decision", "fields": {"decision": "Visible"}, "visibility": "shared"},
        ])
        self.change(meeting["entries"][0]["item_id"], visibility="private")
        shared = self.store.portal_payload("shared")["meetings"][0]
        self.store.portal_mutate("meeting-shared-save", {
            "id": shared["meeting_id"], "revision": shared["edit_token"],
            "meeting_date": shared["meeting_date"],
            "entries": [self.serial(e) for e in shared["entries"]],
        })
        private = self.store.portal_payload("private")["meetings"][0]
        self.assertEqual(len(private["entries"]), 2)
        self.assertNotIn("PRIVATE-", json.dumps(self.store.portal_payload("shared")))

    def test_backup_does_not_upgrade_source_or_overwrite_a_backup(self):
        self.meeting([{"tag": "notes", "body": "Backup content"}])
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute("UPDATE meta SET value='10' WHERE key='team_schema_version'")
        destination = Path(self.tmp.name) / "backups" / "team.db"
        self.store.backup(destination)
        for path in (self.store.db_path, destination):
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute(
                    "SELECT value FROM meta WHERE key='team_schema_version'"
                ).fetchone()[0], "10")
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            self.store.backup(destination)
        with sqlite3.connect(destination) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_upgrade_failure_rolls_back_registration_and_version(self):
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute("UPDATE meta SET value='10' WHERE key='team_schema_version'")
        with patch.object(self.store, "_backfill_tracked_items", side_effect=ValueError("Bad source")):
            with self.assertRaisesRegex(ValueError, "Bad source"):
                self.store.connect()
        with sqlite3.connect(self.store.db_path) as conn:
            self.assertEqual(conn.execute(
                "SELECT value FROM meta WHERE key='team_schema_version'"
            ).fetchone()[0], "10")


if __name__ == "__main__":
    unittest.main()
