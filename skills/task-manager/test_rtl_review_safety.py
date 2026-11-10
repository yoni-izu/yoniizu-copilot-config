import io
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from unittest.mock import patch

import tm


HEAD = "a" * 40


def pr_metadata(commits=None, state="OPEN"):
    return {
        "state": state, "mergeable": "MERGEABLE", "headRefOid": HEAD,
        "mergedAt": None, "commits": commits or [],
    }


class ReviewSafetyTests(unittest.TestCase):
    def test_review_intake_never_requests_pr_body(self):
        data = {"title": "review", "author": {"login": "author"}, "headRefName": "branch"}
        for repo in ("ip.x.rtl", "ip.x.unknown", "ip.x.sw.fw"):
            with self.subTest(repo=repo), patch.object(tm, "gh_pr_json", return_value=data) as fetch:
                details = tm.fetch_pr_details(f"https://github.com/org/{repo}/pull/1")
                self.assertNotIn("body", fetch.call_args.args[3].split(","))
                self.assertEqual(details["body"], "")
                self.assertEqual(details["title"], "review")

    def test_review_intake_discards_unexpected_description(self):
        data = {"title": "review", "body": "unexpected", "author": {"login": "author"}}
        with patch.object(tm, "gh_pr_json", return_value=data):
            details = tm.fetch_pr_details("https://github.com/org/ip.x.sw.fw/pull/1")
        self.assertEqual(details["body"], "")

    def test_rtl_threads_are_skipped_not_reported_as_no_activity(self):
        with patch.object(tm.rtl_pr_guard, "classify_pr", return_value=("rtl", "RTL repository", None)), \
             patch.object(tm.rtl_pr_guard, "review_threads") as threads, \
             patch.object(tm.rtl_pr_guard, "activity_state", return_value=pr_metadata()) as activity, \
             patch.object(tm, "gh_pr_json") as pr_view:
            summary = tm._scan_one_pr("me", "org", "ip.x.rtl", 1, "2026-01-01")
        threads.assert_not_called()
        pr_view.assert_not_called()
        activity.assert_called_once_with("org", "ip.x.rtl", 1, include_headline=False, expected_head=None)
        self.assertFalse(summary["threads_checked"])
        self.assertEqual(summary["classification"], "threads_unchecked")
        self.assertIn("VM-hosted", summary["thread_check_reason"])

    def test_unknown_classification_skips_comments_but_keeps_commit_activity(self):
        commits = [{"committedDate": "2026-01-03T00:00:00Z", "oid": HEAD}]
        with patch.object(tm.rtl_pr_guard, "classify_pr", return_value=("unknown", "metadata unavailable", None)), \
             patch.object(tm.rtl_pr_guard, "review_threads") as threads, \
             patch.object(tm.rtl_pr_guard, "activity_state", return_value=pr_metadata(commits)), \
             patch.object(tm, "gh_pr_json") as pr_view:
            summary = tm._scan_one_pr("me", "org", "ip.x.sw.fw", 1, "2026-01-01")
        threads.assert_not_called()
        pr_view.assert_not_called()
        self.assertEqual(summary["classification"], "needs_re_review")
        self.assertFalse(summary["threads_checked"])

    def test_non_rtl_replies_keep_existing_flags(self):
        thread = {
            "path": "src/a.c", "line": 12,
            "comments": [
                {"author": {"login": "me"}, "createdAt": "2026-01-01", "body": "Please fix"},
                {"author": {"login": "author"}, "createdAt": "2026-01-02", "body": "fixed"},
            ],
        }
        with patch.object(tm.rtl_pr_guard, "classify_pr", return_value=("software", "complete", HEAD)), \
             patch.object(tm.rtl_pr_guard, "review_threads", return_value=[thread]), \
             patch.object(tm.rtl_pr_guard, "activity_state", return_value=pr_metadata()) as activity:
            summary = tm._scan_one_pr("me", "org", "ip.x.sw.fw", 1, "2026-01-01")
        activity.assert_called_once_with("org", "ip.x.sw.fw", 1, include_headline=True, expected_head=HEAD)
        self.assertTrue(summary["threads_checked"])
        self.assertEqual(summary["classification"], "replies_only")
        self.assertEqual(summary["threads_with_reply"], 1)
        self.assertTrue(summary["replies"][0]["addressed"])

    def test_thread_error_is_unknown_not_no_activity(self):
        with patch.object(tm.rtl_pr_guard, "classify_pr", return_value=("software", "complete", HEAD)), \
             patch.object(tm.rtl_pr_guard, "review_threads",
                          side_effect=tm.rtl_pr_guard.MetadataError("incomplete")), \
             patch.object(tm.rtl_pr_guard, "activity_state", return_value=pr_metadata()):
            summary = tm._scan_one_pr("me", "org", "ip.x.sw.fw", 1, "2026-01-01")
        self.assertEqual(summary["classification"], "threads_unchecked")
        self.assertEqual(summary["thread_check_reason"], "incomplete")

    def test_blocked_scan_prints_unchecked_bucket(self):
        task = {"id": "T-1", "title": "review", "category": "code-review", "status": "blocked",
                "reviews": [{"owner": "org", "repo": "ip.x.rtl", "pr_number": 1}]}
        summary = {
            "repo": "ip.x.rtl", "pr": 1, "submitted_at": "", "head": "abc",
            "new_commits": 0, "mergeable": None, "replies": [],
            "classification": "threads_unchecked", "threads_checked": False,
            "thread_check_reason": "VM-hosted RTL agent required",
        }
        output = io.StringIO()
        with patch.object(tm, "load", return_value={"tasks": [task]}), \
             patch.object(tm, "gh_me", return_value="me"), \
             patch.object(tm, "_scan_one_pr", return_value=summary), redirect_stdout(output):
            self.assertEqual(tm.cmd_blocked_scan(Namespace(update=False, json=False)), 0)
        self.assertIn("Thread activity unknown", output.getvalue())
        self.assertNotIn("No activity", output.getvalue())


if __name__ == "__main__":
    unittest.main()
