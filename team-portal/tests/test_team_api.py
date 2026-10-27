from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import team_api


class TeamApiMutationShapeTests(unittest.TestCase):
    def test_bulk_fields_are_delegated_unchanged(self):
        command = {"entity_type": "assignment", "member_key": "test",
                   "items": [{"id": "a1", "revision": "r1"}], "field": "status", "value": "done"}
        with patch.object(team_api, "TeamStore") as store:
            team_api.mutate("bulk-set", command)
        store.return_value.portal_mutate.assert_called_once_with("bulk-set", command)

    def test_scan_uses_owner_without_record_mutation(self):
        with patch.object(team_api, "TeamStore") as store, \
                patch.object(team_api, "scan_activity", return_value={"status": "ok"}) as scan:
            result = team_api.mutate("activity-scan", {"member_key": "yotam"})
        scan.assert_called_once_with(store.return_value, "yotam")
        store.return_value.portal_mutate.assert_not_called()
        self.assertEqual(result["status"], "ok")

    def test_scan_rejects_unexpected_fields_and_missing_member(self):
        with self.assertRaises(ValueError):
            team_api.mutate("activity-scan", {"member_key": "yotam", "since": "yesterday"})
        with patch.object(team_api, "TeamStore"), \
                patch.object(team_api, "scan_activity", side_effect=LookupError("unknown member")):
            with self.assertRaisesRegex(ValueError, "unknown member"):
                team_api.mutate("activity-scan", {"member_key": "missing"})

    def test_assignment_set_legacy_shape_is_flattened(self):
        with patch.object(team_api, "TeamStore") as store:
            inst = store.return_value
            inst.portal_mutate.return_value = {"ok": True}
            team_api.mutate("assignment-set", {"assignment_id": "a1", "revision": "r1", "patch": {"status": "done"}})
        inst.portal_mutate.assert_called_once_with(
            "assignment-set",
            {"id": "a1", "revision": "r1", "status": "done"},
        )

    def test_meeting_create_legacy_shape_is_flattened(self):
        with patch.object(team_api, "TeamStore") as store:
            inst = store.return_value
            inst.portal_mutate.return_value = {"ok": True}
            team_api.mutate(
                "meeting-create",
                {"member_key": "akshay", "data": {"title": "1:1", "meeting_date": "2026-10-01", "entries": []}},
            )
        inst.portal_mutate.assert_called_once_with(
            "meeting-create",
            {"member_key": "akshay", "title": "1:1", "meeting_date": "2026-10-01", "entries": []},
        )

    def test_member_set_alias_is_mapped_to_id(self):
        with patch.object(team_api, "TeamStore") as store:
            inst = store.return_value
            inst.portal_mutate.return_value = {"ok": True}
            team_api.mutate("member-set", {"member_key": "yotam", "revision": "r1", "main_ip_ownership": "D2D"})
        inst.portal_mutate.assert_called_once_with(
            "member-set",
            {"id": "yotam", "revision": "r1", "main_ip_ownership": "D2D"},
        )


if __name__ == "__main__":
    unittest.main()
