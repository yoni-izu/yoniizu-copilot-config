from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from team_store import TeamStore


class TeamPortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = TeamStore(Path(self.tmp.name) / "team.db")
        self.store.init_db()
        self.store.portal_mutate("member-add", {
            "member_key": "yoni", "display_name": "Team Lead", "roster_role": "lead",
            "goals": "PRIVATE-GOAL", "profile_notes": "PRIVATE-PROFILE",
        })

    def tearDown(self):
        self.tmp.cleanup()

    def meeting(self, **extra):
        return self.store.portal_mutate("meeting-create", {
            "member_key": "yoni", "meeting_date": "2026-10-25", "title": "PRIVATE-TITLE",
            "entries": [
                {"tag": "decision", "body": "Public decision", "visibility": "shared"},
                {"tag": "feedback", "body": "PRIVATE-ENTRY"},
            ], **extra,
        })["entity"]

    def save(self, meeting, **extra):
        return self.store.portal_mutate("meeting-save", {
            "id": meeting["meeting_id"], "revision": meeting["revision"],
            "meeting_date": meeting["meeting_date"], "title": meeting["title"],
            "state": meeting["state"],
            "entries": [{k: e[k] for k in ("entry_id", "tag", "body", "visibility")}
                        for e in meeting["entries"]], **extra,
        })["entity"]

    def test_flat_payload_private_and_shared_no_leaks(self):
        self.meeting()
        self.store.create_entity("feedback", "yoni", {"kind": "peer", "body": "PRIVATE-FEEDBACK",
                                                     "visibility": "shared"})
        self.store.portal_mutate("assignment-create", {
            "member_key": "yoni", "title": "PRIVATE-ASSIGNMENT",
        })
        self.store.portal_mutate("assignment-create", {
            "member_key": "yoni", "title": "Public direction", "visibility": "shared",
        })
        private = self.store.portal_payload("private", "2026-10-25")
        shared = self.store.portal_payload(as_of="2026-10-25")
        self.assertEqual(shared["members"][0]["roster_role"], "lead")
        self.assertIn("yoni", shared["metrics"])
        self.assertEqual(shared["meetings"][0]["title"], "1:1 session")
        self.assertEqual(len(shared["meetings"][0]["entries"]), 1)
        self.assertEqual(len(shared["assignments"]), 1)
        encoded = json.dumps(shared)
        for secret in ("PRIVATE-", '"revision"', '"history"', '"full_markdown"', '"feedback_id"'):
            self.assertNotIn(secret, encoded)
        self.assertEqual(shared["feedback"], [])
        self.assertIn("PRIVATE-FEEDBACK", json.dumps(private))
        self.assertIn("revision", private["members"][0])
        self.assertEqual(self.store.payload("yoni", "shared")["members"][0]["meetings"],
                         shared["meetings"])
        self.assertNotIn("PRIVATE-", self.store.export_entity(
            "meeting", private["meetings"][0]["meeting_id"], "shared"))
        for entity in private["assignments"]:
            if entity["visibility"] == "private":
                with self.assertRaises(ValueError):
                    self.store.export_entity("assignment", entity["assignment_id"], "shared")
        with self.assertRaises(ValueError):
            self.store.export_entity("feedback", private["feedback"][0]["feedback_id"], "shared")
        with self.assertRaises(ValueError):
            self.store.portal_payload("public")

    def test_profile_goals_edit_revision_guard(self):
        before = self.store.get_member("yoni")
        result = self.store.portal_mutate("member-set", {
            "id": "yoni", "revision": before["revision"], "goals": "Published goals",
            "goals_visibility": "shared", "profile_notes": "Public profile",
            "profile_visibility": "shared",
        })
        self.assertNotEqual(result["revision"], before["revision"])
        self.assertEqual(self.store.portal_payload()["members"][0]["goals"], "Published goals")
        with self.assertRaises(ValueError):
            self.store.portal_mutate("member-set", {
                "id": "yoni", "revision": before["revision"], "goals": "stale",
            })
        with self.assertRaises(ValueError):
            self.store.portal_mutate("member-set", {
                "id": "yoni", "revision": result["revision"], "display_name": "",
            })

    def test_bulk_tasks_atomic_revision_guard_and_field_validation(self):
        tasks = [self.store.portal_mutate("assignment-create", {
            "member_key": "yoni", "title": title, "description": "Keep description",
            "refs": [{"domain_name": "bugs", "target_type": "bug", "target_id": "B-123"}],
        })["entity"] for title in ("First", "Second")]
        items = [{"id": t["assignment_id"], "revision": t["revision"]} for t in tasks]
        command = {"entity_type": "assignment", "member_key": "yoni", "items": items,
                   "field": "priority", "value": "high"}
        self.store.portal_mutate("assignment-set", {**items[1], "status": "done"})
        with self.assertRaisesRegex(ValueError, "stale revision"):
            self.store.portal_mutate("bulk-set", command)
        current = self.store.portal_payload("private")["assignments"]
        self.assertTrue(all(t["priority"] == "medium" for t in current))
        command["items"] = [{"id": t["assignment_id"], "revision": t["revision"]} for t in current]
        result = self.store.portal_mutate("bulk-set", command)
        self.assertEqual(result["count"], 2)
        changed = self.store.portal_payload("private")["assignments"]
        self.assertTrue(all(t["priority"] == "high" for t in changed))
        self.assertTrue(all(t["description"] == "Keep description" and len(t["refs"]) == 1 for t in changed))
        command["items"] = [{"id": t["assignment_id"], "revision": t["revision"]} for t in changed]
        for patch in (
            {"field": "priority", "value": "urgent"},
            {"field": "title", "value": "Overwrite"},
            {"field": "target_date", "value": ""},
            {"field": "target_date", "value": "2026-02-30"},
            {"items": [command["items"][0], command["items"][0]]},
            {"items": []}, {"member_key": "other"},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.store.portal_mutate("bulk-set", {**command, **patch})
        self.store.portal_mutate("bulk-set", {**command, "field": "target_date", "value": None})

    def test_bulk_log_history_privacy_and_entry_preservation(self):
        logs = [self.meeting(state="finalized"), self.meeting()]
        command = {"entity_type": "meeting", "member_key": "yoni",
                   "items": [{"id": t["meeting_id"], "revision": t["revision"]} for t in logs],
                   "field": "title_visibility", "value": "shared"}
        self.assertEqual(self.store.portal_mutate("bulk-set", command)["count"], 2)
        changed = self.store.portal_payload("private")["meetings"]
        by_id = {t["meeting_id"]: t for t in changed}
        for before in logs:
            after = by_id[before["meeting_id"]]
            self.assertEqual(after["entries"], before["entries"])
            self.assertEqual(after["title"], before["title"])
            self.assertEqual(after["title_visibility"], "shared")
            self.assertEqual(after["state"], "amended" if before["finalized"] else before["state"])
            self.assertTrue(any(h["snapshot"]["title_visibility"] == "private" for h in after["history"]))
        self.assertNotIn("PRIVATE-ENTRY", json.dumps(self.store.portal_payload()["meetings"]))
        with self.assertRaisesRegex(ValueError, "stale revision"):
            self.store.portal_mutate("bulk-set", {**command, "field": "meeting_date", "value": "2026-11-01"})
        self.assertTrue(all(t["meeting_date"] == "2026-10-25" for t in self.store.portal_payload("private")["meetings"]))
        fresh = {**command, "items": [{"id": t["meeting_id"], "revision": t["revision"]} for t in changed]}
        for patch in ({"field": "session_type", "value": "bad"}, {"field": "meeting_date", "value": ""},
                      {"field": "entries", "value": []}, {"field": "title_visibility", "value": "public"}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.store.portal_mutate("bulk-set", {**fresh, **patch})
        self.store.portal_mutate("bulk-set", {**fresh, "field": "session_type", "value": "conversation"})
        self.assertTrue(all(t["session_type"] == "conversation" for t in self.store.portal_payload("private")["meetings"]))

    def test_focus_is_independent_of_tasks_and_privacy_filtered(self):
        before = self.store.get_member("yoni")
        self.store.portal_mutate("member-set", {
            "id": "yoni", "revision": before["revision"], "focus": "Bring-up",
            "focus_visibility": "shared",
        })
        shared = self.store.portal_payload()
        self.assertEqual(shared["members"][0]["focus"], "Bring-up")
        self.assertEqual(shared["assignments"], [])
        before = self.store.get_member("yoni")
        self.store.portal_mutate("member-set", {
            "id": "yoni", "revision": before["revision"], "focus_visibility": "private",
        })
        self.assertNotIn("focus", self.store.portal_payload()["members"][0])

    def test_session_defaults_and_action_tasks_save_atomically(self):
        meeting = self.store.portal_mutate("meeting-create", {
            "member_key": "yoni", "entries": [{"tag": "notes", "body": "Freestyle\nnotes"}],
            "action_items": [{"title": "Follow up", "target_date": "2026-11-01"}],
        })["entity"]
        self.assertEqual(meeting["title"], f"1:1 with Manager - {date.today().isoformat()}")
        self.assertEqual(meeting["meeting_date"], date.today().isoformat())
        tasks = self.store.portal_payload("private")["assignments"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["origin_meeting_id"], meeting["meeting_id"])
        self.assertEqual(tasks[0]["visibility"], "private")
        self.assertEqual(self.store.portal_payload()["assignments"], [])
        with self.assertRaises(ValueError):
            self.store.portal_mutate("meeting-create", {
                "member_key": "yoni", "entries": [], "action_items": [
                    {"title": "Valid"}, {"title": "Invalid", "target_date": "not-a-date"},
                ],
            })
        self.assertEqual(len(self.store.portal_payload("private")["meetings"]), 1)
        self.assertEqual(len(self.store.portal_payload("private")["assignments"]), 1)

    def test_session_edit_creates_only_new_actions_and_keeps_old_tasks(self):
        created = self.store.portal_mutate("meeting-create", {
            "member_key": "yoni", "entries": [],
            "action_items": [{"title": "First action", "visibility": "shared"}],
        })["entity"]
        changed = self.save(created, entries=[{"tag": "notes", "body": "Updated body"}],
                            action_items=[{"title": "Second action"}])
        self.save(changed)
        tasks = self.store.portal_payload("private")["assignments"]
        self.assertEqual(len(tasks), 2)
        self.assertEqual({task["title"] for task in tasks}, {"First action", "Second action"})
        shared = self.store.portal_payload()["assignments"]
        self.assertEqual([task["title"] for task in shared], ["First action"])

    def test_meeting_history_reorder_finalize_amend_and_stale(self):
        original = self.meeting()
        reordered = self.save(original, entries=[
            {k: e[k] for k in ("entry_id", "tag", "body", "visibility")}
            for e in reversed(original["entries"])
        ], state="finalized")
        self.assertEqual(reordered["entries"][0]["body"], "PRIVATE-ENTRY")
        amended = self.save(reordered, title="amended title")
        self.assertEqual(amended["state"], "amended")
        self.assertEqual(len(amended["history"]), 3)
        self.assertEqual(amended["history"][0]["snapshot"]["title"], original["title"])
        with self.assertRaises(ValueError):
            self.save(original)
        with self.assertRaises(ValueError):
            self.save(amended, state="working")
        with self.store.connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM meeting_revisions")
        before_count = len(self.store.portal_payload("private")["meetings"])
        with self.assertRaises(ValueError):
            self.meeting(entries=[{"entry_id": "x", "tag": "note", "body": "x"},
                                  {"entry_id": "x", "tag": "note", "body": "y"}])
        self.assertEqual(before_count, len(self.store.portal_payload("private")["meetings"]))
        with self.assertRaises(ValueError):
            self.meeting(entries=[{"entry_id": amended["entries"][0]["entry_id"],
                                  "tag": "note", "body": "stolen"}])
        self.assertEqual(before_count, len(self.store.portal_payload("private")["meetings"]))

    def test_titles_without_safe_entries_and_visibility_default(self):
        m = self.meeting(entries=[{"tag": "note", "body": "PRIVATE-ONLY"}])
        self.assertEqual(self.store.portal_payload()["meetings"], [])
        self.save(m, visibility="shared", title_visibility="shared")
        self.assertEqual(self.store.portal_payload()["meetings"], [])

    def test_state_free_edits_preserve_legacy_sessions(self):
        for state in ("working", "prep", "live", "finalized"):
            original = self.meeting(state=state)
            updated = self.store.portal_mutate("meeting-save", {
                "id": original["meeting_id"], "revision": original["revision"],
                "title": "Edited", "meeting_date": original["meeting_date"],
                "entries": [{"tag": "notes", "body": "Updated", "visibility": "shared"}],
            })["entity"]
            self.assertEqual(updated["state"], "amended" if original["finalized"] else state)
            self.assertEqual(updated["finalized"], original["finalized"])

    def test_log_types_and_default_titles_keep_history(self):
        for kind, prefix in (("one_on_one", "1:1"), ("conversation", "Conversation"),
                             ("meeting", "Meeting"), ("note", "Note")):
            created = self.store.portal_mutate("meeting-create", {
                "member_key": "yoni", "session_type": kind, "meeting_date": "2026-10-20",
                "title": "", "entries": [{"tag": "notes", "body": "Shared", "visibility": "shared"}],
            })["entity"]
            who = "Manager" if kind == "one_on_one" else "Team Lead"
            self.assertEqual(created["title"], f"{prefix} with {who} - 2026-10-20")
            self.assertEqual(created["session_type"], kind)
            saved = self.store.portal_mutate("meeting-save", {
                "id": created["meeting_id"], "revision": created["revision"],
                "title": "", "meeting_date": "2026-10-21", "entries": [],
            })["entity"]
            self.assertEqual(saved["title"], created["title"])
            self.assertEqual(saved["session_type"], kind)
        custom = self.meeting(title="Custom subject")
        self.assertEqual(custom["title"], "Custom subject")
        with self.assertRaisesRegex(ValueError, "log type"):
            self.meeting(session_type="not-real")

    def test_rich_tasks_refs_notes_and_atomic_session_actions(self):
        refs = [
            {"domain_name": "github", "target_type": "pr",
             "target_id": "https://github.com/example/repo/pull/12", "relation": "implements"},
            {"domain_name": "bugs", "target_type": "bug", "target_id": "BUG-12"},
        ]
        task = self.store.portal_mutate("assignment-create", {
            "member_key": "yoni", "title": "Investigate", "category": "debug",
            "priority": "high", "jira_key": "ABC-12", "refs": refs,
            "description": "Check trace", "visibility": "shared",
        })["entity"]
        self.assertEqual(task["category"], "debug")
        self.assertEqual(task["priority"], "high")
        self.assertEqual(len(task["refs"]), 2)
        ident = task["assignment_id"]
        updated = self.store.portal_mutate("assignment-set", {
            "id": ident, "revision": task["revision"], "category": "research", "refs": refs,
        })["entity"]
        self.assertEqual(updated["assignment_id"], ident)
        self.assertEqual(len(updated["refs"]), 2)
        noted = self.store.portal_mutate("assignment-note", {
            "id": ident, "revision": updated["revision"], "note": "PRIVATE-PROGRESS",
        })["entity"]
        self.assertEqual(noted["updates"][0]["note"], "PRIVATE-PROGRESS")
        with self.assertRaises(ValueError):
            self.store.portal_mutate("assignment-set", {
                "id": ident, "revision": updated["revision"], "category": "development",
            })
        shared = self.store.portal_payload()["assignments"][0]
        self.assertEqual(shared["category"], "research")
        self.assertNotIn("PRIVATE-PROGRESS", json.dumps(shared))
        before = len(self.store.portal_payload("private")["meetings"])
        with self.assertRaises(ValueError):
            self.meeting(action_items=[{"title": "Bad PR", "refs": [
                {"domain_name": "github", "target_type": "pr", "target_id": "javascript:bad"}
            ]}])
        self.assertEqual(len(self.store.portal_payload("private")["meetings"]), before)
        meeting = self.meeting(action_items=[{
            "title": "Implement", "category": "development", "priority": "low", "refs": refs,
        }])
        tasks = self.store.portal_payload("private")["assignments"]
        action = next(t for t in tasks if t.get("origin_meeting_id") == meeting["meeting_id"])
        self.assertEqual(action["category"], "development")
        self.assertEqual(len(action["refs"]), 2)

    def test_assignment_updates_private_default_and_guard(self):
        asg = self.store.portal_mutate("assignment-create", {
            "member_key": "yoni", "title": "Public work", "visibility": "shared",
        })["entity"]
        noted = self.store.portal_mutate("assignment-note", {
            "id": asg["assignment_id"], "revision": asg["revision"], "note": "PRIVATE-NOTE",
        })["entity"]
        self.assertEqual(self.store.portal_payload()["assignments"][0]["updates"], [])
        with self.assertRaises(ValueError):
            self.store.portal_mutate("assignment-set", {
                "id": asg["assignment_id"], "revision": asg["revision"], "status": "done",
            })
        done = self.store.portal_mutate("assignment-set", {
            "id": noted["assignment_id"], "revision": noted["revision"],
            "status": "done", "outcome": "Verified",
        })["entity"]
        self.assertEqual(done["status"], "done")
        with self.assertRaises(ValueError):
            self.store.portal_mutate("assignment-set", {
                "id": done["assignment_id"], "revision": done["revision"], "visibility": "public",
            })

    def test_calendar_seed_idempotence_overlap_and_no_future_inference(self):
        self.assertEqual(self.store.calendar()["milestones"], [])
        first = self.store.seed_approved_calendar()
        second = self.store.seed_approved_calendar()
        self.assertEqual(first, second)
        self.assertEqual(len(first["milestones"]), 2)
        self.assertEqual(len(first["sprints"]), 5)
        self.assertEqual(first["overlaps"], [{
            "milestone_ids": ["MS9", "2027-Q1"],
            "started_at": "2027-01-01", "ended_at": "2027-01-04",
        }])
        for when, expected in (("2026-10-19", "MS9.1"), ("2026-11-02", "MS9.2"),
                               ("2027-01-03", "MS9.5"), ("2027-01-04", "unknown-sprint-window")):
            self.assertEqual(self.store.metrics("yoni", "current-sprint", date.fromisoformat(when))
                             ["coverage"]["label"], expected)
        self.assertEqual(self.store.metrics("yoni", "last-sprint", date(2026, 11, 2))
                         ["coverage"]["label"], "MS9.1")
        saved = self.store.portal_mutate("calendar-save", {
            "revision": first["revision"], "milestones": first["milestones"], "sprints": first["sprints"],
        })
        self.assertEqual(saved["revision"], first["revision"])
        with self.assertRaises(ValueError):
            self.store.portal_mutate("calendar-save", {
                "revision": "wrong", "milestones": first["milestones"], "sprints": first["sprints"],
            })
        bad = {**first["sprints"][0], "ended_at": "2028-01-01"}
        with self.assertRaises(ValueError):
            self.store.portal_mutate("calendar-save", {
                "revision": first["revision"], "milestones": first["milestones"], "sprints": [bad],
            })

    def test_metric_as_of_and_end_exclusive_unknown_windows(self):
        self.store.seed_approved_calendar()
        for n, day in enumerate(("2026-10-18", "2026-10-19", "2026-10-25T23:59:59Z",
                                 "2026-10-26", "2027-01-03", "2027-01-04")):
            self.store.create_entity("issue-completion", "yoni", {
                "issue_key": f"B-{n}", "issue_type": "Bug", "resolution": "Done",
                "completed_at": day, "source": "test",
            })
        self.assertEqual(self.store.metrics("yoni", "total", date(2026, 10, 25))
                         ["metrics"]["bugs_closed"], 3)
        self.assertEqual(self.store.metrics("yoni", "MS9", date(2027, 1, 10))
                         ["metrics"]["bugs_closed"], 4)
        self.assertEqual(self.store.metrics("yoni", "2027-Q1", date(2027, 1, 10))
                         ["metrics"]["bugs_closed"], 2)
        unknown = self.store.metrics("yoni", "current-sprint", date(2027, 1, 4))
        self.assertIsNone(unknown["metrics"]["bugs_closed"])
        self.assertFalse(unknown["coverage"]["window_known"])

    def test_baselines_safe_refs_revision_and_calendar_orphans(self):
        self.store.seed_approved_calendar()
        b = self.store.portal_mutate("baseline-save", {
            "member_key": "yoni", "scope_id": "MS9", "work_refs": ["HSI-1", "o/r#2"],
        })["entity"]
        self.assertEqual(self.store.calendar("shared")["baselines"], [])
        public = self.store.portal_mutate("baseline-save", {
            "id": b["baseline_id"], "revision": b["revision"], "member_key": "yoni",
            "scope_id": "MS9", "work_refs": ["HSI-1"], "visibility": "shared",
        })["entity"]
        self.assertEqual(self.store.calendar("shared")["baselines"][0]["work_refs"], ["HSI-1"])
        with self.assertRaises(ValueError):
            self.store.portal_mutate("baseline-save", {
                "member_key": "yoni", "scope_id": "MS9", "work_refs": ["private manager commentary"],
            })
        cal = self.store.calendar()
        with self.assertRaises(ValueError):
            self.store.portal_mutate("calendar-save", {
                "revision": cal["revision"], "milestones": [], "sprints": [],
            })
        self.assertEqual(public["member_key"], "yoni")

    def _member(self, key="ana"):
        self.store.portal_mutate("member-add", {"member_key": key, "display_name": "Ana Lee"})

    def _rows(self, sql, *args):
        with self.store.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def test_typed_items_validate_and_create_owned_records(self):
        self._member()
        task = self.store.portal_mutate("assignment-create", {"member_key": "ana", "title": "Bringup"})["entity"]
        made = self.store.portal_mutate("meeting-create", {"member_key": "ana", "entries": [
            {"tag": "notes", "body": "short notes"},
            {"tag": "action", "fields": {"title": "Write plan", "owner": "member", "priority": "high"},
             "visibility": "shared"},
            {"tag": "action", "fields": {"title": "Ask PM", "owner": "me"}},
            {"tag": "decision", "fields": {"decision": "Use DMA", "decided_by": "joint"}},
            {"tag": "discussion", "fields": {"topic": "Onboarding", "follow_up": True}},
            {"tag": "status", "fields": {"assignment_id": task["assignment_id"], "status": "in-progress",
                                         "note": "PHY up"}},
            {"tag": "roadblock", "fields": {"blocker": "No board", "unblocker": "other",
                                            "unblocker_name": "Lab"}},
            {"tag": "feedback_given", "fields": {"polarity": "positive", "behavior": "Clear review"}},
            {"tag": "feedback_received", "fields": {"feedback": "More context please"}},
            {"tag": "growth", "fields": {"goal": "Own D2D"}},
            {"tag": "personal", "fields": {"text": "PRIVATE-PERSONAL"}, "visibility": "shared"},
        ]})["entity"]
        self.assertEqual(made["title"], f"1:1 with Ana Lee - {date.today().isoformat()}")
        by_tag = {}
        for e in made["entries"]:
            by_tag.setdefault(e["tag"], []).append(e)
        owners = {r["title"]: (r["member_key"], r["owner_label"]) for r in self._rows(
            "SELECT title,member_key,owner_label FROM assignments WHERE kind='action-item'")}
        self.assertEqual(owners, {"Write plan": ("ana", "ana"), "Ask PM": ("yoni", "yoni")})
        self.assertEqual(self._rows("SELECT status FROM assignments WHERE assignment_id=?",
                                    task["assignment_id"])[0]["status"], "in-progress")
        self.assertEqual(by_tag["status"][0]["body"], "Bringup: PHY up")
        rb = self._rows("SELECT * FROM roadblocks")[0]
        self.assertEqual((rb["title"], rb["unblocker"], rb["status"]), ("No board", "Lab", "open"))
        kinds = sorted(r["kind"] for r in self._rows("SELECT kind FROM feedback_items"))
        self.assertEqual(kinds, ["given-positive", "received"])
        self.assertEqual(by_tag["personal"][0]["visibility"], "private")
        self.assertEqual(by_tag["decision"][0]["body"], "Use DMA")
        self.assertTrue(by_tag["discussion"][0]["fields"]["follow_up"])
        shared = self.store.portal_payload("shared")
        text = json.dumps(shared)
        self.assertNotIn("PRIVATE-PERSONAL", text)
        self.assertIn("Write plan", text)
        for bad in ({"tag": "decision", "fields": {}},
                    {"tag": "decision", "fields": {"decision": "x", "bogus": 1}},
                    {"tag": "roadblock", "fields": {"blocker": "x", "status": "maybe"}},
                    {"tag": "status", "fields": {"assignment_id": "nope", "note": "x"}}):
            with self.assertRaises(ValueError):
                self.store.portal_mutate("meeting-create", {"member_key": "ana", "entries": [bad]})

    def test_typed_item_edits_update_in_place_and_removal_drops_owned_records(self):
        self._member()
        made = self.store.portal_mutate("meeting-create", {"member_key": "ana", "entries": [
            {"tag": "action", "fields": {"title": "Write plan"}},
            {"tag": "roadblock", "fields": {"blocker": "No board"}},
            {"tag": "feedback_given", "fields": {"polarity": "constructive", "behavior": "Late"}},
        ]})["entity"]
        rb_id = next(e["record_id"] for e in made["entries"] if e["tag"] == "roadblock")
        self.store.portal_mutate("roadblock-raid-link", {"roadblock_id": rb_id, "raid_id": "I-007"})
        entries = [{"entry_id": e["entry_id"], "tag": e["tag"], "fields": dict(e["fields"]),
                    "visibility": e["visibility"]} for e in made["entries"]]
        entries[0]["fields"]["title"] = "Renamed in log"
        entries[1]["fields"].update(status="resolved")
        saved = self.store.portal_mutate("meeting-save", {
            "id": made["meeting_id"], "revision": made["revision"], "meeting_date": made["meeting_date"],
            "title": made["title"], "entries": entries[:2]})["entity"]
        self.assertEqual(len(self._rows("SELECT * FROM assignments")), 1)
        self.assertEqual(self._rows("SELECT title FROM assignments")[0]["title"], "Write plan")
        self.assertEqual(saved["entries"][0]["fields"]["title"], "Write plan")
        rb = self._rows("SELECT * FROM roadblocks")
        self.assertEqual((len(rb), rb[0]["status"], rb[0]["resolved_at"]), (1, "resolved", made["meeting_date"]))
        self.assertEqual(saved["entries"][1]["raid_id"], "I-007")
        self.assertEqual(self._rows("SELECT * FROM feedback_items"), [])
        again = self.store.portal_mutate("meeting-save", {
            "id": saved["meeting_id"], "revision": saved["revision"], "meeting_date": saved["meeting_date"],
            "title": saved["title"], "entries": [entries[0]]})["entity"]
        self.assertEqual(len(again["entries"]), 1)
        self.assertEqual(self._rows("SELECT * FROM roadblocks"), [])
        self.assertEqual(len(self._rows("SELECT * FROM assignments")), 1)

    def test_lead_page_one_on_one_is_with_manager(self):
        made = self.store.portal_mutate("meeting-create", {"member_key": "yoni", "entries": [
            {"tag": "action", "fields": {"title": "Send brief", "owner": "member"}}]})["entity"]
        self.assertTrue(made["title"].startswith("1:1 with Manager - "))
        row = self._rows("SELECT member_key,owner_label FROM assignments")[0]
        self.assertEqual((row["member_key"], row["owner_label"]), ("yoni", "manager"))
        payload = self.store.portal_payload("private")
        self.assertEqual(payload["manager_name"], "Manager")
        self.assertIn("roadblock", payload["log_item_types"])

    def test_shared_leave_dates_only(self):
        with self.store.connect() as conn:
            conn.execute("""INSERT INTO imports(
                import_id,source_path,source_hash,source_kind,status,imported_at)
                VALUES('hr','x','x','hr-leave','imported','2026-10-24')""")
            conn.execute("""INSERT INTO leave_records(
                leave_id,member_key,leave_type,start_date,end_date,amount_days,source,notes,created_at,updated_at)
                VALUES('lv','yoni','PRIVATE-TYPE','2026-10-24','2026-10-27',2,'HR','PRIVATE-REASON','x','x')""")
        shared = self.store.portal_payload(as_of="2026-10-25")["members"][0]["leave"]
        self.assertEqual(shared["current_leave"], {"start_date": "2026-10-24", "end_date": "2026-10-27"})
        self.assertNotIn("days_taken_this_year", shared)
        self.assertNotIn("PRIVATE", json.dumps(shared))


if __name__ == "__main__":
    unittest.main()
