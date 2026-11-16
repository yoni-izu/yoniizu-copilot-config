"""PR scans cancel a review task once every PR in it was closed without merge."""
import io
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from unittest.mock import patch

import tm


def pr(repo, num, **extra):
    return {"owner": "acme", "repo": repo, "pr_number": num,
            "review_status": "pending", **extra}


def review_task(*prs, status="in-progress"):
    return {"id": "T-1", "title": "review", "category": "code-review",
            "status": status, "notes": [], "reviews": list(prs)}


CLOSED = ({"merged": False, "state": "closed"}, [])
OPEN = ({"merged": False, "state": "open"}, [])


class ClosedPrCancelTests(unittest.TestCase):
    def setUp(self):
        for target in ("save", "release_task_slot"):
            p = patch.object(tm, target)
            p.start()
            self.addCleanup(p.stop)

    def portal_scan(self, task, states):
        with patch.object(tm, "load", return_value={"tasks": [task]}), \
                patch.object(tm, "_fetch_pr_review_state", side_effect=states) as fetch:
            return tm.portal_review_scan(), fetch

    def test_portal_scan_cancels_when_all_prs_closed(self):
        task = review_task(pr("one", 1), pr("two", 2))
        result, _ = self.portal_scan(task, [CLOSED, CLOSED])
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(result["cancelled"], ["T-1"])
        tm.release_task_slot.assert_called_once_with(task)

    def test_portal_scan_keeps_task_with_an_open_pr(self):
        task = review_task(pr("one", 1), pr("two", 2))
        result, _ = self.portal_scan(task, [CLOSED, OPEN])
        self.assertNotEqual(task["status"], "cancelled")
        self.assertEqual(result["cancelled"], [])
        self.assertEqual([p["repo"] for p in task["reviews"]], ["two"])

    def test_portal_scan_cancels_stale_closed_stub_task_without_fetching(self):
        task = review_task(pr("one", 1, closed=True))
        result, fetch = self.portal_scan(task, [])
        fetch.assert_not_called()
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(result["cancelled"], ["T-1"])

    def test_selected_portal_scan_accepts_stale_closed_stub_task(self):
        task = review_task(pr("one", 1, closed=True))
        with patch.object(tm, "load", return_value={"tasks": [task]}), \
                patch.object(tm, "_fetch_pr_review_state") as fetch:
            result = tm.portal_review_scan(task_ids=["T-1"])
        fetch.assert_not_called()
        self.assertEqual(result["cancelled"], ["T-1"])

    def test_merged_and_closed_mix_is_done_not_cancelled(self):
        task = review_task(pr("one", 1), pr("two", 2))
        self.portal_scan(task, [CLOSED, ({"merged": True, "state": "closed"}, [])])
        self.assertEqual(task["status"], "done")

    def test_review_check_cancels_when_all_prs_closed(self):
        task = review_task(pr("one", 1), pr("two", 2))
        closed = {"mergedAt": None, "closed": True, "state": "CLOSED"}
        with patch.object(tm, "load", return_value={"tasks": [task]}), \
                patch.object(tm, "gh_pr_json", return_value=closed), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(tm.cmd_review_check(Namespace()), 0)
        self.assertEqual(task["status"], "cancelled")
        self.assertIn("all PRs closed → cancelled", out.getvalue())

    def test_review_check_cancels_stale_closed_stub_task(self):
        task = review_task(pr("one", 1, closed=True))
        with patch.object(tm, "load", return_value={"tasks": [task]}), \
                patch.object(tm, "gh_pr_json") as fetch, \
                redirect_stdout(io.StringIO()):
            tm.cmd_review_check(Namespace())
        fetch.assert_not_called()
        self.assertEqual(task["status"], "cancelled")

    def test_blocked_scan_update_cancels_when_all_prs_closed(self):
        task = review_task(pr("one", 1, review_status="commented"), status="blocked")
        summary = {
            "repo": "one", "pr": 1, "submitted_at": "", "head": "abc",
            "new_commits": 0, "mergeable": None, "replies": [],
            "classification": "closed", "threads_checked": True,
        }
        with patch.object(tm, "load", return_value={"tasks": [task]}), \
                patch.object(tm, "gh_me", return_value="me"), \
                patch.object(tm, "_scan_one_pr", return_value=summary), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(tm.cmd_blocked_scan(Namespace(update=True, json=False)), 0)
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(task["reviews"], [])
        self.assertIn("all PRs closed → cancelled: T-1", out.getvalue())

    def test_blocked_scan_without_update_does_not_cancel(self):
        task = review_task(pr("one", 1, review_status="commented"), status="blocked")
        summary = {
            "repo": "one", "pr": 1, "submitted_at": "", "head": "abc",
            "new_commits": 0, "mergeable": None, "replies": [],
            "classification": "closed", "threads_checked": True,
        }
        with patch.object(tm, "load", return_value={"tasks": [task]}), \
                patch.object(tm, "gh_me", return_value="me"), \
                patch.object(tm, "_scan_one_pr", return_value=summary), \
                redirect_stdout(io.StringIO()):
            tm.cmd_blocked_scan(Namespace(update=False, json=False))
        self.assertEqual(task["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
