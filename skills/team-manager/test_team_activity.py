from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from team_store import TeamStore
import team_activity as ta

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def jira_issue(key: str, updated: datetime, assignee: str | None = "acc-1",
               reporter: str | None = None, status: str = "In Progress") -> dict:
    return {"key": key, "fields": {
        "summary": f"S {key}", "status": {"name": status, "statusCategory": {"key": "indeterminate"}},
        "issuetype": {"name": "Story"}, "project": {"key": "SWRND"},
        "assignee": {"accountId": assignee} if assignee else None,
        "reporter": {"accountId": reporter} if reporter else None,
        "updated": updated.strftime("%Y-%m-%dT%H:%M:%S.000+0000")}}


class FakeJira:
    def __init__(self, pages: list[dict] | Exception) -> None:
        self.pages, self.calls = pages, []

    def search_page(self, jql, fields, page_token, page_size):
        self.calls.append((jql, page_token))
        if isinstance(self.pages, Exception):
            raise self.pages
        return self.pages[0 if page_token is None else int(page_token)]


def pr_item(repo: str, number: int, updated: datetime, merged: bool = False) -> dict:
    return {"number": number, "title": f"PR {number}", "state": "closed" if merged else "open",
            "html_url": f"https://github.com/{repo}/pull/{number}",
            "repository_url": f"https://api.github.com/repos/{repo}",
            "updated_at": _iso(updated), "created_at": _iso(updated),
            "pull_request": {"merged_at": _iso(updated) if merged else None}}


class FakeGitHub:
    def __init__(self, authored: list[dict] | None = None, reviewed: list[dict] | None = None,
                 reviews: dict | None = None, error: Exception | None = None) -> None:
        self.authored, self.reviewed = authored or [], reviewed or []
        self.reviews, self.error, self.queries = reviews or {}, error, []

    def search_issues(self, query, page, per_page):
        self.queries.append((query, page))
        if self.error:
            raise self.error
        rows = self.authored if "author:" in query else self.reviewed
        chunk = rows[(page - 1) * per_page: page * per_page]
        return {"total_count": len(rows), "incomplete_results": False, "items": chunk}

    def pr_reviews(self, repo, number, page, per_page):
        rows = self.reviews.get((repo, number), [])
        return rows[(page - 1) * per_page: page * per_page]


class TeamActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.store = TeamStore(Path(self.tmp.name) / "team.db")
        self.store.add_member("tester", {"display_name": "Tester", "jira_account_id": "acc-1",
                                         "github_handle": "Tester"})
        self.store.add_member("nobody", {"display_name": "No Body"})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _state(self, source: str) -> dict:
        return ta.activity_payload(self.store, ["tester"])["members"]["tester"]["sources"][source]

    def test_payload_before_any_scan_is_read_only(self) -> None:
        payload = ta.activity_payload(self.store, ["tester", "ghost"])
        self.assertFalse(payload["initialized"])
        with closing(self.store.connect()) as conn:
            self.assertFalse(ta._tables_ready(conn))
        tester = payload["members"]["tester"]
        self.assertEqual(tester["observations"], [])
        self.assertEqual(tester["sources"]["jira"]["next_mode"], "first")
        self.assertEqual(tester["errors"], [])
        self.assertEqual(payload["members"]["ghost"]["errors"][0]["kind"], "unknown-member")

    def test_first_scan_paginates_and_persists(self) -> None:
        jira = FakeJira([
            {"issues": [jira_issue("A-1", NOW - timedelta(days=2), reporter="acc-1")],
             "nextPageToken": "1", "isLast": False},
            {"issues": [jira_issue("A-2", NOW - timedelta(days=1)),
                        jira_issue("OLD-1", NOW - timedelta(days=9))], "isLast": True},
        ])
        authored = [pr_item("org/r", n, NOW - timedelta(hours=150 - n)) for n in range(1, 151)]
        reviewed = [pr_item("org/x", 7, NOW - timedelta(hours=3))]
        reviews = {("org/x", 7): [
            {"id": 11, "user": {"login": "tester"}, "state": "APPROVED",
             "submitted_at": _iso(NOW - timedelta(hours=3))},
            {"id": 12, "user": {"login": "someone"}, "state": "COMMENTED",
             "submitted_at": _iso(NOW - timedelta(hours=3))},
            {"id": 13, "user": {"login": "tester"}, "state": "COMMENTED",
             "submitted_at": _iso(NOW - timedelta(days=30))}]}
        gh = FakeGitHub(authored, reviewed, reviews)
        result = ta.scan_activity(self.store, "tester", jira_client=jira, github_client=gh, now=NOW)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(jira.calls), 2)
        self.assertIn('updated >= "-10080m"', jira.calls[0][0])
        self.assertEqual(result["sources"]["jira"]["mode"], "first")
        self.assertEqual(result["sources"]["jira"]["fetched"], 3)  # A-1 assigned+reported, A-2
        self.assertEqual(result["sources"]["github"]["fetched"], 151)
        self.assertEqual([p for q, p in gh.queries if "author:" in q], [1, 2])
        self.assertIn("updated:>=2026-10-19T12:00:00Z", gh.queries[0][0])
        payload = ta.activity_payload(self.store, ["tester"])
        tester = payload["members"]["tester"]
        self.assertTrue(payload["initialized"])
        self.assertEqual(tester["counts"], {"jira-assigned": 2, "jira-reported": 1,
                                            "github-pr-authored": 150, "github-pr-review": 1})
        self.assertEqual(tester["sources"]["jira"]["cursor_at"], _iso(NOW))
        self.assertEqual(tester["last_scan"]["status"], "ok")
        self.assertIsNone(tester["running_scan"])

    def test_incremental_scan_uses_cursor_and_is_idempotent(self) -> None:
        issue = jira_issue("A-1", NOW - timedelta(hours=1))
        ta.scan_activity(self.store, "tester", jira_client=FakeJira([{"issues": [issue], "isLast": True}]),
                         github_client=FakeGitHub(), now=NOW)
        later = NOW + timedelta(hours=2)
        jira = FakeJira([{"issues": [jira_issue("A-1", later - timedelta(minutes=5), status="Done")],
                          "isLast": True}])
        gh = FakeGitHub()
        result = ta.scan_activity(self.store, "tester", jira_client=jira, github_client=gh, now=later)
        self.assertEqual(result["sources"]["jira"]["mode"], "incremental")
        self.assertIn('updated >= "-130m"', jira.calls[0][0])  # 2h + 10min overlap
        self.assertIn("updated:>=2026-10-26T11:50:00Z", gh.queries[0][0])
        self.assertEqual((result["sources"]["jira"]["new"], result["sources"]["jira"]["changed"]), (0, 1))
        obs = ta.activity_payload(self.store, ["tester"])["members"]["tester"]["observations"]
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]["state"], "Done")

    def test_first_scan_failure_keeps_no_cursor_and_reports_error(self) -> None:
        jira = FakeJira(ta.ActivitySourceError("auth", "Jira search HTTP 401"))
        gh = FakeGitHub([pr_item("org/r", 1, NOW - timedelta(hours=1))])
        result = ta.scan_activity(self.store, "tester", jira_client=jira, github_client=gh, now=NOW)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["errors"], [{"source": "jira", "kind": "auth",
                                             "message": "Jira search HTTP 401"}])
        self.assertIsNone(self._state("jira")["cursor_at"])
        self.assertEqual(self._state("jira")["last_status"], "error")
        self.assertEqual(self._state("github")["cursor_at"], _iso(NOW))
        member = ta.activity_payload(self.store, ["tester"])["members"]["tester"]
        self.assertEqual(member["errors"][0]["kind"], "auth")

    def test_incremental_failure_does_not_advance_cursor_or_write(self) -> None:
        ta.scan_activity(self.store, "tester", jira_client=FakeJira([{"issues": [], "isLast": True}]),
                         github_client=FakeGitHub(), now=NOW)
        later = NOW + timedelta(days=1)
        broken = FakeGitHub([pr_item("org/r", 1, later)], error=ta.ActivitySourceError("timeout", "slow"))
        result = ta.scan_activity(self.store, "tester",
                                  jira_client=FakeJira([{"issues": [], "isLast": True}]),
                                  github_client=broken, now=later)
        self.assertEqual(result["sources"]["github"]["status"], "error")
        self.assertEqual(result["sources"]["github"]["cursor_after"], _iso(NOW))
        self.assertEqual(self._state("github")["cursor_at"], _iso(NOW))
        self.assertEqual(self._state("github")["last_success_at"] is not None, True)
        self.assertEqual(self._state("jira")["cursor_at"], _iso(later))
        unexpected = FakeJira(RuntimeError("boom"))
        result = ta.scan_activity(self.store, "tester", jira_client=unexpected,
                                  github_client=broken, now=later)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["sources"]["jira"]["error_kind"], "unexpected")
        self.assertEqual(self._state("jira")["cursor_at"], _iso(later))

    def test_missing_identities_and_credentials_are_explicit(self) -> None:
        result = ta.scan_activity(self.store, "nobody", jira_client=FakeJira([]),
                                  github_client=FakeGitHub(), now=NOW)
        self.assertEqual(result["status"], "failed")
        self.assertEqual({e["kind"] for e in result["errors"]}, {"missing-identity"})
        with mock.patch.dict("os.environ", {"HOME": self.tmp.name}, clear=True), \
                mock.patch("netrc.netrc", side_effect=FileNotFoundError):
            with self.assertRaises(ta.ActivitySourceError) as ctx:
                ta.jira_credentials()
        self.assertEqual(ctx.exception.kind, "auth")
        with self.assertRaises(LookupError):
            ta.scan_activity(self.store, "ghost", jira_client=FakeJira([]), github_client=FakeGitHub())
        with self.assertRaises(ValueError):
            ta.scan_activity(self.store, "tester", sources=["slack"])

    def test_concurrent_scan_is_rejected_and_stale_lease_reclaimed(self) -> None:
        ta.initialize_activity(self.store)
        with closing(self.store.connect()) as conn:
            scan_id, _ = ta._acquire(conn, "tester", NOW)
        jira = FakeJira([{"issues": [], "isLast": True}])
        busy = ta.scan_activity(self.store, "tester", jira_client=jira, github_client=FakeGitHub(),
                                now=NOW + timedelta(minutes=1))
        self.assertEqual(busy["status"], "busy")
        self.assertEqual(busy["running_scan"]["scan_id"], scan_id)
        self.assertEqual(busy["errors"][0]["kind"], "busy")
        self.assertEqual(jira.calls, [])
        later = NOW + ta.LEASE + timedelta(minutes=1)
        result = ta.scan_activity(self.store, "tester", jira_client=jira, github_client=FakeGitHub(),
                                  now=later)
        self.assertEqual(result["status"], "ok")
        with closing(self.store.connect()) as conn:
            status = conn.execute("SELECT status FROM activity_scans WHERE scan_id=?",
                                  (scan_id,)).fetchone()[0]
        self.assertEqual(status, "abandoned")

    def test_truncated_source_saves_but_never_advances_cursor(self) -> None:
        ta.scan_activity(self.store, "tester", jira_client=FakeJira([{"issues": [], "isLast": True}]),
                         github_client=FakeGitHub(), now=NOW)
        success_before = self._state("jira")["last_success_at"]
        later = NOW + timedelta(days=1)
        pages = [{"issues": [jira_issue(f"A-{i}", later - timedelta(hours=i))],
                  "nextPageToken": str(i + 1), "isLast": False} for i in range(3)]
        with mock.patch.object(ta, "JIRA_MAX_PAGES", 2):
            result = ta.scan_activity(self.store, "tester", jira_client=FakeJira(pages),
                                      github_client=FakeGitHub(), now=later)
        jira = result["sources"]["jira"]
        self.assertEqual(result["status"], "partial")
        self.assertEqual((jira["status"], jira["error_kind"], jira["truncated"]),
                         ("incomplete", "incomplete", True))
        self.assertEqual(jira["cursor_after"], _iso(NOW))
        self.assertEqual(jira["new"], 2)
        self.assertEqual([e["kind"] for e in result["errors"]], ["incomplete"])
        state = self._state("jira")
        self.assertEqual((state["cursor_at"], state["last_status"]), (_iso(NOW), "incomplete"))
        self.assertEqual(state["last_success_at"], success_before)
        member = ta.activity_payload(self.store, ["tester"])["members"]["tester"]
        self.assertEqual(member["counts"].get("jira-assigned"), 2)
        self.assertEqual(member["errors"][0]["kind"], "incomplete")
        retry = FakeJira([{"issues": [jira_issue("A-0", later)], "isLast": True}])
        ta.scan_activity(self.store, "tester", jira_client=retry, github_client=FakeGitHub(),
                         now=later + timedelta(minutes=1))
        self.assertIn('updated >= "-1451m"', retry.calls[0][0])  # old cursor: 24h + 1m + 10m overlap

    def test_first_scan_truncated_github_desc_order_keeps_no_cursor(self) -> None:
        authored = [pr_item("org/r", n, NOW - timedelta(minutes=n)) for n in range(1, 251)]
        with mock.patch.object(ta, "GH_MAX_PAGES", 2):
            result = ta.scan_activity(self.store, "tester", sources=["github"],
                                      github_client=FakeGitHub(authored), now=NOW)
        gh = result["sources"]["github"]
        self.assertEqual(result["status"], "partial")  # data saved, not a success
        self.assertEqual((gh["status"], gh["fetched"], gh["cursor_after"]), ("incomplete", 200, None))
        state = self._state("github")
        self.assertIsNone(state["cursor_at"])
        self.assertIsNone(state["last_success_at"])
        self.assertEqual(state["next_mode"], "first")
        self.assertEqual(ta.activity_payload(self.store, ["tester"])["members"]["tester"]
                         ["counts"]["github-pr-authored"], 200)

    def test_review_pr_cap_marks_incomplete(self) -> None:
        reviewed = [pr_item("org/x", n, NOW - timedelta(minutes=n)) for n in range(1, 4)]
        with mock.patch.object(ta, "GH_MAX_REVIEW_PRS", 2):
            result = ta.scan_activity(self.store, "tester", sources=["github"],
                                      github_client=FakeGitHub(reviewed=reviewed), now=NOW)
        self.assertEqual(result["sources"]["github"]["status"], "incomplete")
        self.assertIn("reviewed PR cap", result["errors"][0]["message"])
        self.assertIsNone(self._state("github")["cursor_at"])

    def test_github_review_pagination(self) -> None:
        reviews = [{"id": i, "user": {"login": "tester"}, "state": "COMMENTED",
                    "submitted_at": _iso(NOW - timedelta(minutes=i))} for i in range(1, 106)]
        gh = FakeGitHub(reviewed=[pr_item("org/x", 3, NOW - timedelta(minutes=1))],
                        reviews={("org/x", 3): reviews})
        result = ta.scan_activity(self.store, "tester", sources=["github"], github_client=gh, now=NOW)
        self.assertEqual(result["sources"]["github"]["fetched"], 105)
        self.assertEqual(list(result["sources"]), ["github"])
        again = ta.scan_activity(self.store, "tester", sources=["github"], github_client=gh,
                                 now=NOW + timedelta(minutes=5))
        self.assertEqual(again["sources"]["github"]["new"], 0)

    def test_default_clients_map_errors_without_network(self) -> None:
        import subprocess
        import urllib.error
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(ta.ActivitySourceError) as ctx:
                ta.GhCliClient().search_issues("q", 1, 100)
        self.assertEqual(ctx.exception.kind, "tool-missing")
        failed = subprocess.CompletedProcess([], 1, "", "To get started run: gh auth login")
        with mock.patch("subprocess.run", return_value=failed):
            with self.assertRaises(ta.ActivitySourceError) as ctx:
                ta.GhCliClient().pr_reviews("o/r", 1, 1, 100)
        self.assertEqual(ctx.exception.kind, "auth")
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 1)):
            with self.assertRaises(ta.ActivitySourceError) as ctx:
                ta.GhCliClient(timeout=1).search_issues("q", 1, 100)
        self.assertEqual(ctx.exception.kind, "timeout")
        client = ta.JiraRestClient()
        err = urllib.error.HTTPError("u", 401, "no", {}, None)
        err.read = lambda: b"unauthorized"
        with mock.patch.object(ta, "jira_credentials", return_value=("e", "t")), \
                mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(ta.ActivitySourceError) as ctx:
                client.search_page("jql", [], None, 100)
        self.assertEqual(ctx.exception.kind, "auth")


if __name__ == "__main__":
    unittest.main()
