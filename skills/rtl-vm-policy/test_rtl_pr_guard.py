import unittest
from unittest.mock import patch

import rtl_pr_guard as guard


HEAD = "a" * 40
BASE = "b" * 40


def connection(field, nodes, count=None, more=False, cursor=None, **extra):
    return {
        **extra,
        field: {
            "totalCount": len(nodes) if count is None else count,
            "pageInfo": {"hasNextPage": more, "endCursor": cursor},
            "nodes": nodes,
        },
    }


class ClassificationTests(unittest.TestCase):
    def test_rtl_repository_stops_without_a_request(self):
        with patch.object(guard, "_query") as query:
            kind, _, _ = guard.classify_pr("org", "ip.d2d.rtl", 1)
        self.assertEqual(kind, "rtl")
        query.assert_not_called()

    def test_mixed_hdl_and_rtl_directory_are_blocked(self):
        for path in ("src/design.sv", "rtl/README.md", "ip/top.VHD",
                     "encrypted/block.vp", "encrypted/block.SVP"):
            with self.subTest(path=path), patch.object(
                guard, "_query", return_value=connection(
                    "files", [{"path": path, "changeType": "MODIFIED"}],
                    headRefOid=HEAD, baseRefOid=BASE,
                )
            ):
                self.assertEqual(guard.classify_pr("org", "ip.example.sw.fw", 2)[0], "rtl")

    def test_complete_paginated_software_metadata_returns_pinned_head(self):
        pages = [
            connection(
                "files", [{"path": "src/a.c", "changeType": "MODIFIED"}],
                count=2, more=True, cursor="cursor", headRefOid=HEAD, baseRefOid=BASE,
            ),
            connection(
                "files", [{"path": "include/a.h", "changeType": "ADDED"}],
                count=2, headRefOid=HEAD, baseRefOid=BASE,
            ),
        ]
        with patch.object(guard, "_query", side_effect=pages):
            self.assertEqual(guard.classify_pr("org", "ip.example.sw.fw", 2),
                             ("software", "complete changed-file metadata", HEAD))

    def test_incomplete_ambiguous_and_changing_metadata_fail_closed(self):
        cases = [
            [connection("files", [], count=1, headRefOid=HEAD, baseRefOid=BASE)],
            [connection("files", [{"path": "src/a.c", "changeType": "RENAMED"}],
                        headRefOid=HEAD, baseRefOid=BASE)],
            [connection("files", [{"path": "src/a.c", "changeType": "CHANGED"}],
                        headRefOid=HEAD, baseRefOid=BASE)],
            [connection("files", [{"path": "src/a.c", "changeType": "SURPRISE"}],
                        headRefOid=HEAD, baseRefOid=BASE)],
            [connection("files", [{"path": "../rtl/block.c", "changeType": "MODIFIED"}],
                        headRefOid=HEAD, baseRefOid=BASE)],
            [connection("files", [{"path": "src/a.c", "changeType": "MODIFIED"},
                                  {"path": "src/a.c", "changeType": "MODIFIED"}],
                        headRefOid=HEAD, baseRefOid=BASE)],
            [
                connection("files", [], count=1, more=True, cursor="next",
                           headRefOid=HEAD, baseRefOid=BASE),
                connection("files", [{"path": "src/a.c", "changeType": "MODIFIED"}],
                           count=1, headRefOid="c" * 40, baseRefOid=BASE),
            ],
        ]
        for pages in cases:
            with self.subTest(pages=pages), patch.object(guard, "_query", side_effect=pages):
                self.assertEqual(guard.classify_pr("org", "ip.example.sw.fw", 2)[0], "unknown")
        with patch.object(guard, "_query", side_effect=guard.MetadataError("unavailable")):
            self.assertEqual(guard.classify_pr("org", "ip.example.sw.fw", 2)[0], "unknown")

    def test_details_preserve_ambiguous_paths_and_both_revision_ids(self):
        files = [{"path": "drivers/v1/a.c", "changeType": "RENAMED"}]
        with patch.object(guard, "_query", return_value=connection(
                "files", files, headRefOid=HEAD, baseRefOid=BASE)):
            kind, _, head, base, result = guard.classify_pr_details("org", "ip.example.sw.fw", 2)
        self.assertEqual((kind, head, base, result), ("unknown", HEAD, BASE, files))

    def test_requested_graphql_fields_do_not_include_patches(self):
        self.assertNotIn("diffHunk", guard._FILES_QUERY)
        self.assertNotIn("patch", guard._FILES_QUERY.lower())
        self.assertNotIn("body", guard._REVIEWS_QUERY)
        self.assertNotIn("message", guard._ACTIVITY_QUERY.lower())
        self.assertNotIn("body", guard._ACTIVITY_QUERY.lower())
        self.assertNotIn("patch", guard._ACTIVITY_QUERY.lower())
        self.assertIn("messageHeadline", guard._SOFTWARE_ACTIVITY_QUERY)
        self.assertNotIn("messageBody", guard._SOFTWARE_ACTIVITY_QUERY)
        self.assertNotIn("diffHunk", guard._THREADS_QUERY)
        self.assertNotIn("diffHunk", guard._THREAD_COMMENTS_QUERY)


class ReviewMetadataTests(unittest.TestCase):
    def test_activity_metadata_has_no_commit_messages(self):
        pr = connection(
            "commits", [{"commit": {"oid": HEAD, "committedDate": "2026-01-03T00:00:00Z"}}],
            state="OPEN", mergedAt=None, mergeable="MERGEABLE",
            headRefOid=HEAD, baseRefOid=BASE,
        )
        with patch.object(guard, "_query", return_value=pr):
            result = guard.activity_state("org", "ip.example.rtl", 1)
        self.assertEqual(result["headRefOid"], HEAD)
        self.assertEqual(result["commits"], [{"oid": HEAD, "committedDate": "2026-01-03T00:00:00Z"}])

    def test_activity_rejects_incomplete_and_changing_pagination(self):
        pages = [
            connection("commits", [], count=1, more=True, cursor="next",
                       state="OPEN", headRefOid=HEAD, baseRefOid=BASE),
            connection("commits", [{"commit": {"oid": HEAD, "committedDate": "2026-01-03"}}],
                       count=1, state="OPEN", headRefOid="c" * 40, baseRefOid=BASE),
        ]
        with patch.object(guard, "_query", side_effect=pages):
            with self.assertRaises(guard.MetadataError):
                guard.activity_state("org", "ip.example.rtl", 1)
        bad = connection("commits", [{"commit": {}}], state="OPEN",
                         headRefOid=HEAD, baseRefOid=BASE)
        with patch.object(guard, "_query", return_value=bad):
            with self.assertRaises(guard.MetadataError):
                guard.activity_state("org", "ip.example.rtl", 1)

    def test_software_activity_preserves_headline_and_checks_revision(self):
        pr = connection(
            "commits", [{"commit": {"oid": HEAD, "committedDate": "2026-01-03",
                                    "messageHeadline": "Fix the issue"}}],
            state="OPEN", mergedAt=None, mergeable="MERGEABLE",
            headRefOid=HEAD, baseRefOid=BASE,
        )
        with patch.object(guard, "_query", return_value=pr):
            result = guard.activity_state("org", "ip.example.sw.fw", 1,
                                          include_headline=True, expected_head=HEAD)
        self.assertEqual(result["commits"][0]["messageHeadline"], "Fix the issue")
        with patch.object(guard, "_query", return_value=pr):
            with self.assertRaises(guard.MetadataError):
                guard.activity_state("org", "ip.example.sw.fw", 1,
                                     include_headline=True, expected_head="c" * 40)

    def test_review_decisions_preserve_state_without_body(self):
        pr = connection(
            "reviews",
            [{"author": {"login": "reviewer"}, "state": "APPROVED",
              "submittedAt": "2026-01-01T00:00:00Z"}],
            state="OPEN", merged=False,
        )
        with patch.object(guard, "_query", return_value=pr):
            result, reviews = guard.review_state("org", "ip.example.rtl", 1)
        self.assertEqual(result, {"state": "open", "merged": False})
        self.assertEqual(reviews[0]["user"]["login"], "reviewer")
        self.assertEqual(reviews[0]["state"], "APPROVED")
        self.assertNotIn("body", reviews[0])

    def test_outdated_rtl_thread_never_fetches_comments(self):
        metadata = (
            {"headRefOid": HEAD, "baseRefOid": BASE},
            [{"id": "thread1", "path": "old/design.sv", "line": 1}],
        )
        with patch.object(guard, "_connection_pages", return_value=metadata), \
             patch.object(guard, "_thread_page") as fetch:
            with self.assertRaises(guard.MetadataError):
                guard.review_threads("org", "ip.example.sw.fw", 1, HEAD)
        fetch.assert_not_called()

    def test_changed_head_never_fetches_comments(self):
        metadata = ({"headRefOid": "c" * 40}, [{"id": "thread1", "path": "src/a.c"}])
        with patch.object(guard, "_connection_pages", return_value=metadata), \
             patch.object(guard, "_thread_page") as fetch:
            with self.assertRaises(guard.MetadataError):
                guard.review_threads("org", "ip.example.sw.fw", 1, HEAD)
        fetch.assert_not_called()

    def test_software_thread_returns_replies_without_diff_hunks(self):
        thread = {"id": "thread1", "path": "src/a.c", "line": 3, "originalLine": 3}
        metadata = ({"headRefOid": HEAD}, [thread])
        first = {"author": {"login": "reviewer"}, "createdAt": "2026-01-01", "body": "Please fix"}
        reply = {"author": {"login": "author"}, "createdAt": "2026-01-02", "body": "fixed"}
        page = {"id": "thread1", "path": "src/a.c",
                **connection("comments", [first, reply])}
        with patch.object(guard, "_connection_pages", return_value=metadata), \
             patch.object(guard, "_thread_page", return_value=page):
            threads = guard.review_threads("org", "ip.example.sw.fw", 1, HEAD)
        self.assertEqual(threads[0]["comments"], [first, reply])

    def test_incomplete_thread_pagination_is_not_reported_as_empty(self):
        thread = {"id": "thread1", "path": "src/a.c"}
        page = {"id": "thread1", "path": "src/a.c", **connection("comments", [], count=1)}
        with patch.object(guard, "_connection_pages", return_value=({"headRefOid": HEAD}, [thread])), \
             patch.object(guard, "_thread_page", return_value=page):
            with self.assertRaises(guard.MetadataError):
                guard.review_threads("org", "ip.example.sw.fw", 1, HEAD)

    def test_new_thread_during_fetch_stops_before_returning_results(self):
        thread = {"id": "thread1", "path": "src/a.c"}
        page = {"id": "thread1", "path": "src/a.c", **connection("comments", [])}
        with patch.object(guard, "review_thread_metadata",
                          side_effect=[[thread], [thread, {"id": "new", "path": "src/b.c"}]]), \
             patch.object(guard, "_thread_page", return_value=page):
            with self.assertRaises(guard.MetadataError):
                guard.review_threads("org", "ip.example.sw.fw", 1, HEAD)


if __name__ == "__main__":
    unittest.main()
