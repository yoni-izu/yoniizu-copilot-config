from datetime import date
from pathlib import Path
import tempfile
import unittest

import team_onboarding
from team_store import TeamStore


INTAKE = {
    "member_key": "newbie", "display_name": "New Member", "onboarding_date": "2026-12-01",
    "base_country": "Israel", "github_handle": "NewBie", "buddy_key": "igal",
}


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = TeamStore(Path(self.tmp.name) / "team.db")
        self.store.init_db()
        self.store.portal_mutate("member-add", {
            "member_key": "yoni", "display_name": "Team Lead", "roster_role": "lead"})
        self.store.portal_mutate("member-add", {
            "member_key": "igal", "display_name": "Igal Buddy", "jira_account_id": "acc-igal",
            "github_handle": "igal-gh"})

    def tearDown(self):
        self.tmp.cleanup()

    def counts(self):
        with self.store.connect() as conn:
            return tuple(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                         for t in ("members", "assignments", "tracked_items", "typed_refs"))

    def onboard(self, **extra):
        data = {**INTAKE, **extra}
        plan = team_onboarding.preview(self.store, data)
        return plan, team_onboarding.apply(self.store, {**data, "plan_revision": plan["plan_revision"]})

    def test_preview_writes_nothing_and_reports_plan(self):
        before = self.counts()
        plan = team_onboarding.preview(self.store, INTAKE)
        self.assertEqual(self.counts(), before)
        self.assertEqual(plan["errors"], [])
        self.assertEqual(len(plan["tasks"]), len(team_onboarding.CHECKLIST))
        self.assertEqual(plan["member"]["github_handle"], "newbie")
        self.assertEqual(plan["buddy"]["member_key"], "igal")
        self.assertIn("jira_account_id", [g["field"] for g in plan["gaps"]])
        by_step = {t["step"]: t for t in plan["tasks"]}
        self.assertEqual(by_step["access"]["target_date"], "2026-11-26")
        self.assertEqual(by_step["access"]["owner_key"], "yoni")
        self.assertEqual(by_step["environment"]["owner_key"], "newbie")
        self.assertEqual(by_step["retrospective"]["target_date"], "2026-12-31")
        self.assertTrue(all(t["visibility"] == "private" for t in plan["tasks"]))

    def test_apply_registers_member_tasks_items_and_refs(self):
        _, applied = self.onboard()
        member = self.store.get_member("newbie")
        self.assertEqual(member["onboarding_date"], "2026-12-01")
        with self.store.connect() as conn:
            rows = conn.execute("SELECT * FROM assignments WHERE kind='onboarding'").fetchall()
            self.assertEqual(len(rows), len(applied["tasks"]))
            items = conn.execute(
                "SELECT member_key,status FROM tracked_items WHERE record_type='assignment'"
                " AND record_id LIKE 'asg-onb-newbie-%'").fetchall()
            self.assertEqual({tuple(r) for r in items}, {("newbie", "open")})
            self.assertEqual(len(items), len(rows))
            buddy = conn.execute(
                "SELECT target_id FROM typed_refs WHERE relation='onboarding-buddy'").fetchone()
            self.assertEqual(buddy[0], "igal")
            audit = conn.execute(
                "SELECT COUNT(*) FROM audit WHERE entity_id='newbie' AND action='onboard'").fetchone()
            self.assertEqual(audit[0], 1)

    def test_apply_requires_current_reviewed_plan(self):
        before = self.counts()
        with self.assertRaisesRegex(ValueError, "plan_revision"):
            team_onboarding.apply(self.store, INTAKE)
        plan = team_onboarding.preview(self.store, INTAKE)
        changed = {**INTAKE, "onboarding_date": "2026-12-02", "plan_revision": plan["plan_revision"]}
        with self.assertRaisesRegex(ValueError, "changed since preview"):
            team_onboarding.apply(self.store, changed)
        self.assertEqual(self.counts(), before)

    def test_conflicts_are_reported_and_nothing_written(self):
        for extra, message in (
            ({"member_key": "igal"}, "already exists"),
            ({"github_handle": "IGAL-GH"}, "github_handle"),
            ({"jira_account_id": "acc-igal"}, "jira_account_id"),
            ({"buddy_key": "ghost"}, "buddy not found"),
            ({"buddy_key": "newbie"}, "buddy must be"),
        ):
            with self.subTest(extra=extra):
                plan = team_onboarding.preview(self.store, {**INTAKE, **extra})
                self.assertTrue(any(message in e for e in plan["errors"]), plan["errors"])
                before = self.counts()
                with self.assertRaisesRegex(ValueError, message):
                    team_onboarding.apply(self.store, {**INTAKE, **extra,
                                                       "plan_revision": plan["plan_revision"]})
                self.assertEqual(self.counts(), before)

    def test_invalid_intake_is_rejected(self):
        for extra in ({"onboarding_date": "01/12/2026"}, {"member_key": "Bad Key"},
                      {"roster_role": "lead"}, {"employment_type": "pirate"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                team_onboarding.preview(self.store, {**INTAKE, **extra})
        with self.assertRaises(ValueError):
            team_onboarding.preview(self.store, {"member_key": "x", "display_name": "X"})

    def test_missing_lead_places_steps_on_member_with_warning(self):
        other = TeamStore(Path(self.tmp.name) / "nolead.db")
        other.init_db()
        plan = team_onboarding.preview(other, {**INTAKE, "buddy_key": None})
        self.assertEqual({t["owner_key"] for t in plan["tasks"]}, {"newbie"})
        self.assertTrue(plan["warnings"])
        self.assertEqual(plan["tasks"][2]["title"], "Assign an onboarding buddy")
        self.assertIn("buddy_key", [g["field"] for g in plan["gaps"]])

    def test_status_progress_overdue_and_audience(self):
        self.assertIsNone(team_onboarding.status(self.store, "igal"))
        self.onboard()
        self.store.portal_mutate("assignment-set", {
            "id": "asg-onb-newbie-access", "status": "done",
            "revision": self._assignment_revision("asg-onb-newbie-access")})
        progress = team_onboarding.status(self.store, "newbie", "private", date(2026, 12, 2))
        self.assertEqual(progress["total"], len(team_onboarding.CHECKLIST))
        self.assertEqual(progress["done"], 1)
        self.assertEqual(progress["overdue"], 4)  # machine, buddy, welcome, reading
        self.assertEqual(progress["next"], "Request laptop, development VM and workspace slot")
        self.assertIn("gaps", progress)
        shared = team_onboarding.status(self.store, "newbie", "shared", date(2026, 12, 2))
        self.assertEqual(shared["total"], 0)
        self.assertNotIn("gaps", shared)

    def _assignment_revision(self, ident):
        rows = self.store.portal_payload("private")["assignments"]
        return next(a["revision"] for a in rows if a["assignment_id"] == ident)

    def test_shared_tasks_and_portal_payload(self):
        self.onboard(task_visibility="shared")
        private = self.store.portal_payload("private", "2026-12-02")
        shared = self.store.portal_payload("shared", "2026-12-02")
        self.assertIn("newbie", private["onboarding"])
        self.assertIn("fields", private["onboarding_spec"])
        self.assertNotIn("onboarding_spec", shared)
        self.assertEqual(shared["onboarding"]["newbie"]["total"], len(team_onboarding.CHECKLIST))
        self.assertNotIn("gaps", shared["onboarding"]["newbie"])
        self.assertNotIn("igal", private["onboarding"])

    def test_portal_mutations(self):
        before = self.counts()
        preview = self.store.portal_mutate("onboarding-preview", INTAKE)
        self.assertEqual(self.counts(), before)
        result = self.store.portal_mutate("onboarding-apply", {
            **INTAKE, "plan_revision": preview["entity"]["plan_revision"]})
        self.assertEqual(result["id"], "newbie")
        self.assertEqual(result["entity"]["display_name"], "New Member")
        self.assertEqual(len(result["entity"]["onboarding"]["tasks"]), len(team_onboarding.CHECKLIST))


if __name__ == "__main__":
    unittest.main()
