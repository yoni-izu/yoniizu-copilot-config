"""Jira lifecycle tests never use personal stores or external services."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pm
import jira_sync
from jira_client import JiraClient, JiraError, NoRedirect, credentials


class FakeJira:
    def __init__(self):
        self.owner = "self"
        self.status = {"id": "1", "name": "In Progress", "statusCategory": {"key": "indeterminate"}}
        self.posts = []
        self.error = None
        self.choices = [
            {"id": "review", "to": {"name": "In-Review"}},
            {"id": "done", "to": {"name": "Done"}}]

    def myself(self):
        if self.error:
            raise JiraError(self.error)
        return {"accountId": "self"}

    def issue(self, key):
        return {"fields": {"assignee": {"accountId": self.owner},
                           "status": copy.deepcopy(self.status)}}

    def transitions(self, key):
        return self.choices

    def transition(self, key, transition_id):
        self.posts.append((key, transition_id))
        target = next(t["to"]["name"] for t in self.choices if t["id"] == transition_id)
        self.status = {"id": transition_id, "name": target,
                       "statusCategory": {"key": "done" if target == "Done" else "indeterminate"}}


class JiraSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"PRS_DB": str(Path(self.temp.name) / "prs.db")})
        env.start()
        self.addCleanup(env.stop)
        network = patch.object(pm, "_gh", side_effect=AssertionError("unexpected network"))
        network.start()
        self.addCleanup(network.stop)
        self.jira = FakeJira()
        _, _, self.fid = pm.add_feature("Feature", refs=["acme/one#1", "acme/two#2"],
                                        jira="TEST-1", lookup=False)

    def feature(self):
        return next(f for f in pm.records() if f["id"] == self.fid)

    def confirm(self):
        f = self.feature()
        self.assertTrue(pm.confirm_jira(self.fid, "TEST-1", expected_revision=f["revision"])[0])

    def scan(self, states=("pending", "pending"), **extra):
        def probe(pr):
            return {**pr, "status": states[pr["number"] - 1], "error": None,
                    "draft": False, **extra}
        with patch.object(pm, "probe", side_effect=probe), \
                patch.object(pm, "queue_states", return_value={}), \
                patch.object(jira_sync, "JiraClient", return_value=self.jira):
            return pm.refresh()

    def test_unconfirmed_is_never_sent_to_jira(self):
        with patch.object(jira_sync, "JiraClient", side_effect=AssertionError("no opt-in")), \
                patch.object(pm, "probe", side_effect=lambda p: {**p, "status": "approved"}), \
                patch.object(pm, "queue_states", return_value={}):
            result = pm.refresh()
        self.assertEqual(result["jira_sync"], [])
        self.assertEqual(self.feature()["jira_coverage"], [])

    def test_review_then_all_merged_done_and_no_repeat_posts(self):
        self.confirm()
        result = self.scan(("ci-fail", "approved"))
        self.assertEqual(result["jira_sync"][0]["state"], "updated")
        self.assertEqual(self.jira.posts, [("TEST-1", "review")])
        self.assertEqual(self.scan()["jira_sync"][0]["state"], "unchanged")
        self.scan(("merged", "pending"))
        self.assertEqual(len(self.jira.posts), 1)
        result = self.scan(("merged", "merged"))
        self.assertEqual(result["features"], [])
        self.assertEqual(len(result["purged"]), 1)
        self.assertEqual(self.jira.posts[-1], ("TEST-1", "done"))
        self.assertFalse(jira_sync.needs_retry(self.feature()))
        self.assertEqual(self.scan()["scanned"], 0)

    def test_archived_failure_is_retried_with_fresh_github_probes(self):
        self.confirm()
        self.jira.error = "temporary Jira outage"
        result = self.scan(("merged", "merged"))
        self.assertEqual(result["features"], [])
        self.assertEqual(result["jira_sync"][0]["state"], "error")
        self.assertEqual(pm.payload()["jira_sync"][0]["state"], "error")
        self.assertTrue(jira_sync.needs_retry(self.feature()))
        self.jira.error = None
        result = self.scan(("merged", "merged"))
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["jira_sync"][0]["target"], "Done")
        self.assertFalse(jira_sync.needs_retry(self.feature()))

    def test_foreign_owner_unassigned_draft_closed_and_probe_error_block(self):
        self.confirm()
        for owner in ("other", None):
            self.jira.owner = owner
            self.assertEqual(self.scan()["jira_sync"][0]["state"], "blocked")
        self.jira.owner = "self"
        for states, extra in [
                (("closed", "merged"), {}), (("pending", "pending"), {"draft": True}),
                (("merged", "merged"), {"error": "failed GitHub probe"})]:
            with self.subTest(states=states, extra=extra):
                result = self.scan(states, **extra)
                self.assertEqual(result["jira_sync"][0]["state"], "blocked")
        self.assertEqual(self.jira.posts, [])

    def test_approval_is_invalidated_by_pr_changes_even_if_original_set_restored(self):
        self.confirm()
        self.assertEqual(self.feature()["jira_coverage"], ["TEST-1"])
        self.assertTrue(pm.add_pr(self.fid, "acme/three#3", lookup=False)[0])
        self.assertEqual(self.feature()["jira_coverage"], [])
        self.assertTrue(pm.remove_pr(self.fid, 3)[0])
        self.assertEqual(self.feature()["jira_coverage"], [])
        self.confirm()
        self.assertEqual(self.feature()["jira_coverage"], ["TEST-1"])

    def test_revocation_stale_revision_and_invalid_confirmation(self):
        self.confirm()
        revision = self.feature()["revision"]
        pm.add_note(self.fid, "another edit")
        self.assertFalse(pm.confirm_jira(self.fid, "TEST-1", False, revision)[0])
        self.assertTrue(pm.confirm_jira(self.fid, "TEST-1", False)[0])
        self.assertEqual(self.feature()["jira_coverage"], [])
        self.assertFalse(pm.confirm_jira(self.fid, "OTHER-1")[0])
        self.assertFalse(pm.confirm_jira(self.fid, "TEST-1", "yes")[0])
        self.assertFalse(pm.confirm_jira(self.fid, "../TEST-1")[0])

    def test_shared_ticket_blocks_then_recovers(self):
        self.confirm()
        _, _, other = pm.add_feature("Partial other", jira="TEST-1", lookup=False)
        self.assertEqual(self.scan()["jira_sync"][0]["state"], "blocked")
        self.assertEqual(self.jira.posts, [])
        pm.drop_feature(other)
        self.assertEqual(self.scan()["jira_sync"][0]["state"], "updated")

    def test_terminal_ticket_never_reopened_and_missing_transition_explicit(self):
        self.confirm()
        self.jira.status = {"id": "done", "name": "Done", "statusCategory": {"key": "done"}}
        self.assertEqual(self.scan()["jira_sync"][0]["state"], "blocked")
        self.jira.status = {"id": "1", "name": "In Progress",
                            "statusCategory": {"key": "indeterminate"}}
        self.jira.choices = []
        result = self.scan()
        self.assertIn("No unique", result["jira_sync"][0]["message"])
        self.assertEqual(self.jira.posts, [])

    def test_same_day_last_confirmation_wins_and_cross_date_order(self):
        f = self.feature()
        prs = jira_sync.pr_set(f)
        f["notes"] = [
            {"date": "2026-03-02", "label": "jira-coverage",
             "text": json.dumps({"key": "TEST-1", "prs": prs, "confirmed": False})},
            {"date": "2026-03-02", "label": "jira-coverage",
             "text": json.dumps({"key": "TEST-1", "prs": prs, "confirmed": True})},
            {"date": "2026-03-01", "label": "jira-coverage",
             "text": json.dumps({"key": "TEST-1", "prs": prs, "confirmed": False})}]
        self.assertEqual(jira_sync.coverage(f), ["TEST-1"])

    def test_changed_local_feature_or_jira_owner_during_preflight_never_posts(self):
        self.confirm()
        original = self.jira.transitions

        def transitions(key):
            pm.add_note(self.fid, "concurrent edit")
            return original(key)

        with patch.object(self.jira, "transitions", side_effect=transitions):
            result = self.scan()
        self.assertIn("Feature changed", result["jira_sync"][0]["message"])
        original_issue = self.jira.issue
        calls = 0

        def issue(key):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.jira.owner = "other"
            return original_issue(key)

        with patch.object(self.jira, "issue", side_effect=issue):
            result = self.scan()
        self.assertIn("ownership", result["jira_sync"][0]["message"])
        self.assertEqual(self.jira.posts, [])

    def test_transition_readback_required(self):
        self.confirm()
        with patch.object(self.jira, "transition"):
            result = self.scan()
        self.assertEqual(result["jira_sync"][0]["state"], "error")
        self.assertIn("not verified", result["jira_sync"][0]["message"])

    def test_ambiguous_transition_and_unknown_status_category_do_not_write(self):
        self.confirm()
        self.jira.choices.append({"id": "review2", "to": {"name": "In Review"}})
        self.assertEqual(self.scan()["jira_sync"][0]["state"], "blocked")
        self.jira.status["statusCategory"] = {"key": "unexpected"}
        self.assertEqual(self.scan()["jira_sync"][0]["state"], "error")
        self.assertEqual(self.jira.posts, [])

    def test_missing_auth_is_reported_without_hiding_pr_refresh(self):
        self.confirm()
        with patch.object(jira_sync, "JiraClient", side_effect=JiraError("credentials missing")), \
                patch.object(pm, "probe", side_effect=lambda p: {**p, "status": "approved"}), \
                patch.object(pm, "queue_states", return_value={}):
            result = pm.refresh()
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["jira_sync"][0]["state"], "error")
        self.assertIn("credentials missing", result["jira_sync"][0]["message"])

    def test_dropped_feature_is_not_retried(self):
        self.confirm()
        pm.drop_feature(self.fid)
        with patch.object(jira_sync, "JiraClient", side_effect=AssertionError("dropped")):
            result = pm.refresh()
        self.assertEqual(result["scanned"], 0)

    def test_only_confirmed_ticket_and_all_open_states_target_review(self):
        pm.edit_feature(self.fid, jira="TEST-1, TEST-2")
        self.confirm()
        for state in ("pending", "ci-fail", "changes-requested", "needs-rebase",
                      "approved", "merge-queue"):
            result = self.scan((state, "merged"))
            self.assertEqual([r["key"] for r in result["jira_sync"]], ["TEST-1"])
            self.assertEqual(result["jira_sync"][0]["target"], "In Review")
        self.assertEqual(self.jira.posts, [("TEST-1", "review")])

    def test_unknown_github_state_is_failed_probe(self):
        pr = self.feature()["prs"][0]
        with patch.object(pm, "_gh", return_value=(True, '{"state": "UNKNOWN"}')):
            result = pm.probe(pr)
        self.assertIn("unknown", result["error"])

    def test_no_jira_sync_refreshes_github_only_and_skips_archived_retries(self):
        self.confirm()
        with patch.object(jira_sync, "JiraClient", side_effect=AssertionError("jira disabled")), \
                patch.object(pm, "probe", side_effect=lambda p: {**p, "status": "approved"}), \
                patch.object(pm, "queue_states", return_value={}):
            result = pm.refresh(jira=False)
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["jira_sync"], [])
        self.assertTrue(result["jira_sync_skipped"])
        self.jira.error = "offline"
        self.scan(("merged", "merged"))
        self.assertTrue(jira_sync.needs_retry(self.feature()))
        with patch.object(jira_sync, "JiraClient", side_effect=AssertionError("jira disabled")), \
                patch.object(pm, "probe", side_effect=AssertionError("archived retry probed")), \
                patch.object(pm, "queue_states", return_value={}), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            code = pm.main(["refresh", "--no-jira-sync"])
        self.assertEqual(code, 0)
        self.assertIn("Jira lifecycle sync skipped", output.getvalue())

    def test_cli_confirmation_and_refresh_failure_exit_code(self):
        with patch("sys.stdout", new_callable=io.StringIO):
            code = pm.main(["confirm-jira", str(self.fid), "TEST-1",
                            "--expected-revision", str(self.feature()["revision"])])
        self.assertEqual(code, 0)
        self.assertEqual(self.feature()["jira_coverage"], ["TEST-1"])
        self.jira.error = "offline"
        with patch.object(pm, "probe", side_effect=lambda p: {**p, "status": "pending"}), \
                patch.object(pm, "queue_states", return_value={}), \
                patch.object(jira_sync, "JiraClient", return_value=self.jira), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            code = pm.main(["refresh"])
        self.assertEqual(code, 1)
        self.assertIn("Jira", output.getvalue())
        self.assertIn("offline", output.getvalue())


class ClientTests(unittest.TestCase):
    def client(self):
        with patch("jira_client.load_private_json", return_value={"site": "https://jira.invalid"}), \
                patch("jira_client.credentials", return_value=("test", "secret")), \
                patch.dict(os.environ, {"JIRA_SITE": "https://jira.invalid"}):
            return JiraClient()

    def test_transition_request_and_empty_success_response(self):
        client = self.client()
        with patch.object(client.opener, "open") as send:
            send.return_value.__enter__.return_value.read.return_value = b""
            client.transition("TEST-1", "42")
        request = send.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), {"transition": {"id": "42"}})
        self.assertEqual(send.call_args.kwargs["timeout"], 30)

    def test_http_and_invalid_json_fail_explicitly_without_response_body(self):
        client = self.client()
        error = urllib.error.HTTPError("url", 403, "denied", {}, io.BytesIO(b"sensitive body"))
        with patch.object(client.opener, "open", side_effect=error):
            with self.assertRaisesRegex(JiraError, "HTTP 403") as raised:
                client.myself()
        self.assertNotIn("sensitive body", str(raised.exception))
        with patch.object(client.opener, "open") as send:
            send.return_value.__enter__.return_value.read.return_value = b"not-json"
            with self.assertRaisesRegex(JiraError, "invalid JSON"):
                client.myself()

    def test_redirects_and_unsafe_site_configuration_are_rejected(self):
        with self.assertRaisesRegex(JiraError, "redirected"):
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid")
        for site in ("http://jira.invalid", "https://user:secret@jira.invalid",
                     "https://jira.invalid/?secret=value"):
            with patch("jira_client.load_private_json", return_value={}), \
                    patch.dict(os.environ, {"JIRA_SITE": site}), \
                    patch("jira_client.credentials") as auth:
                with self.assertRaisesRegex(JiraError, "HTTPS"):
                    JiraClient()
                auth.assert_not_called()

    def test_existing_credential_sources_without_reading_personal_files(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {"HOME": directory}, clear=True):
            root = Path(directory)
            root.joinpath(".atlassian-token").write_text("file-token\n")
            root.joinpath(".atlassian-email").write_text("test@example.invalid")
            self.assertEqual(credentials("https://jira.invalid"),
                             ("test@example.invalid", "file-token"))
            root.joinpath(".netrc").write_text(
                "machine jira.invalid login netrc-user password netrc-token\n")
            root.joinpath(".netrc").chmod(0o600)
            self.assertEqual(credentials("https://jira.invalid"), ("netrc-user", "netrc-token"))
            with patch.dict(os.environ, {"ATLASSIAN_EMAIL": "env-user",
                                         "ATLASSIAN_TOKEN": "env-token"}):
                self.assertEqual(credentials("https://jira.invalid"), ("env-user", "env-token"))


if __name__ == "__main__":
    unittest.main()
