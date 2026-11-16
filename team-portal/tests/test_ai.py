"""ai.py: CLI argv safety, event parsing, compaction, budget and the runner."""
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai  # noqa: E402


def pairs(argv, flag):
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


class ArgvTests(unittest.TestCase):
    def test_read_profile_denies_writes_and_mcp_without_reads(self):
        argv = ai.build_argv("q", model="fusion", profile="read", allow_reads=False, binary="/x/copilot")
        self.assertEqual(argv[:3], ["/x/copilot", "-p", "q"])
        deny = pairs(argv, "--deny-tool")
        for rule in ("shell", "write", "GitHub", "workiq", "atlassian-mcp-server"):
            self.assertIn(rule, deny)
        self.assertIn("--disable-builtin-mcps", argv)
        self.assertIn("--no-custom-instructions", argv)
        self.assertEqual(pairs(argv, "--allow-tool"), [])
        self.assertEqual(pairs(argv, "--model"), ["fusion"])

    def test_read_profile_with_reads_allows_only_read_tools(self):
        argv = ai.build_argv("q", model="m", profile="read", allow_reads=True, binary="c")
        allow = pairs(argv, "--allow-tool")
        self.assertIn("github-mcp-server", allow)
        self.assertIn("atlassian-mcp-server(getJiraIssue)", allow)
        self.assertNotIn("--disable-builtin-mcps", argv)
        deny = pairs(argv, "--deny-tool")
        for tool in ai.ATLASSIAN_WRITES:
            self.assertIn(f"atlassian-mcp-server({tool})", deny)
        self.assertIn("GitHub", deny)

    def test_review_profile_never_publishes(self):
        argv = ai.build_argv("q", model="m", profile="review", allow_reads=True, binary="c")
        allow, deny = pairs(argv, "--allow-tool"), pairs(argv, "--deny-tool")
        self.assertIn("shell", allow)
        self.assertNotIn("--no-custom-instructions", argv)
        for rule in ("shell(git push)", "shell(gh pr review)", "shell(gh pr comment)",
                     "shell(gh api)", "GitHub", "shell(wip)"):
            self.assertIn(rule, deny)
        self.assertNotIn("shell(gh pr)", deny)

    def test_review_submit_profile_posts_reviews_only(self):
        argv = ai.build_argv("q", model="m", profile="review-submit", allow_reads=True,
                             binary="c")
        deny = pairs(argv, "--deny-tool")
        for rule in ("shell(gh pr review)", "shell(gh pr comment)", "GitHub"):
            self.assertNotIn(rule, deny)
        for rule in ("shell(gh pr merge)", "shell(gh api)", "shell(git push)", "workiq",
                     "GitHub(merge_pull_request)", "GitHub(push_files)",
                     "GitHub(update_pull_request)"):
            self.assertIn(rule, deny)
        self.assertNotIn("GitHub(pull_request_review_write)", deny)
        self.assertNotIn("GitHub(add_comment_to_pending_review)", deny)
        # The plain review profile is unchanged by the submit profile.
        self.assertIn("GitHub", pairs(ai.build_argv("q", model="m", profile="review",
                                                    allow_reads=True, binary="c"),
                                      "--deny-tool"))

    def test_review_prompts_by_mode(self):
        task = {"id": "T-1", "title": "t", "reviews": []}
        self.assertIn("/pr-review local draft-only", ai.review_prompt(task, "/w", "main"))
        rr = ai.review_prompt(task, "/w", "main", "rereview")
        self.assertIn("re-review", rr)
        self.assertIn(ai.NO_CHANGE_MARK, rr)
        with self.assertRaises(ValueError):
            ai.review_prompt(task, "/w", "main", "bogus")
        self.assertIn("Do NOT post", ai.submit_preview_prompt())
        sub = ai.submit_prompt("PREVIEW")
        self.assertTrue(sub.endswith("--- CONFIRMED SUBMISSION ---\nPREVIEW"))
        self.assertIn("`gh api` is not available", sub)
        self.assertLessEqual(len(ai.chat_turn_prompt("x" * 20000)), 8000 + 2000)

    def test_resume_validated(self):
        sid = "12345678-1234-1234-1234-123456789abc"
        argv = ai.build_argv("q", model="m", profile="read", allow_reads=False, resume=sid, binary="c")
        self.assertEqual(pairs(argv, "--resume"), [sid])
        with self.assertRaises(ai.AIError):
            ai.build_argv("q", model="m", profile="read", allow_reads=False,
                          resume="x; rm -rf /", binary="c")


class ParseTests(unittest.TestCase):
    def test_last_assistant_message_and_usage(self):
        lines = [json.dumps(x) for x in (
            {"type": "session.start", "data": {"sessionId": "s1"}},
            {"type": "session.auto_mode_resolved", "data": {"chosenModel": "gpt-x"}},
            {"type": "assistant.message", "data": {"content": "thinking", "toolRequests": [{}]}},
            {"type": "assistant.message", "data": {"content": "final"}},
            {"type": "session.usage_checkpoint", "data": {"totalNanoAiu": 42}},
            {"type": "result", "sessionId": "s2", "exitCode": 0, "usage": {"premiumRequests": 1.5}},
        )] + ["not json", "{broken"]
        out = ai.parse_events(lines)
        self.assertEqual(out["answer"], "final")
        self.assertEqual(out["session_id"], "s2")
        self.assertEqual(out["model"], "gpt-x")
        self.assertEqual(out["premium"], 1.5)
        self.assertEqual(out["nano_aiu"], 42)
        self.assertEqual(out["tools"], 1)

    def test_progress_text(self):
        self.assertEqual(ai.progress_text({"type": "tool.execution_start",
                                           "data": {"toolName": "view"}}), "using view")
        self.assertIsNone(ai.progress_text({"type": "other"}))


class CompactTests(unittest.TestCase):
    def test_small_is_untouched(self):
        text, cut = ai.compact({"a": [1, 2]}, 1000)
        self.assertFalse(cut)
        self.assertEqual(json.loads(text), {"a": [1, 2]})

    def test_large_is_shrunk_within_budget(self):
        data = {"rows": [{"t": "x" * 500, "n": i} for i in range(500)]}
        text, cut = ai.compact(data, 5000)
        self.assertTrue(cut)
        self.assertLessEqual(len(text), 5000)


class StoreTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.store = ai.Store(Path(d.name))

    def test_cap(self):
        self.store.reserve(2)
        self.store.reserve(2)
        with self.assertRaises(ai.CapReached):
            self.store.reserve(2)
        self.assertEqual(self.store.usage()["runs"], 2)
        with self.assertRaises(ai.CapReached):
            ai.Store(self.store.root / "other").reserve(0)

    def test_runs_and_ids(self):
        run = self.store.save(ai.new_run("explain", section="vm"))
        self.assertRegex(run["id"], ai.RUN_ID_RE)
        self.assertEqual(self.store.get(run["id"])["section"], "vm")
        self.assertIsNone(self.store.get("../../etc/passwd"))
        self.store.save(ai.new_run("ask", section="raid", question="q"))
        self.assertEqual([r["section"] for r in self.store.recent(section="raid")], ["raid"])
        self.store.add_premium(1.25)
        self.assertEqual(self.store.usage()["premium"], 1.25)
        with self.assertRaises(ValueError):
            ai.new_run("bogus")


class ChatStoreTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.store = ai.Store(Path(d.name))

    def test_interactive_pool_is_separate(self):
        self.store.reserve(1)
        self.store.reserve(1, "interactive")
        with self.assertRaises(ai.CapReached):
            self.store.reserve(1, "interactive")
        with self.assertRaises(ai.CapReached):
            self.store.reserve(1)
        self.assertEqual((self.store.usage()["runs"], self.store.usage()["interactive"]), (1, 1))
        with self.assertRaises(ValueError):
            self.store.reserve(5, "bogus")

    def test_chats_round_trip_and_filters(self):
        a = self.store.save_chat(ai.new_chat("T-1", workspace="w"))
        b = self.store.save_chat(dict(ai.new_chat("T-2"), status="ended"))
        self.assertRegex(a["id"], ai.CHAT_ID_RE)
        self.assertEqual(self.store.get_chat(a["id"])["workspace"], "w")
        self.assertIsNone(self.store.get_chat("../x"))
        self.assertIsNone(self.store.get_chat("rc-" + "0" * 32))
        self.assertEqual({c["id"] for c in self.store.chats()}, {a["id"], b["id"]})
        self.assertEqual([c["id"] for c in self.store.chats(open_only=True)], [a["id"]])
        self.assertEqual([c["id"] for c in self.store.chats("T-2")], [b["id"]])


class RunnerTests(unittest.TestCase):
    def fake(self, body):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / "copilot"
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def test_success_streams_events(self):
        events = [{"type": "tool.execution_start", "data": {"toolName": "view"}},
                  {"type": "assistant.message", "data": {"content": "hello"}},
                  {"type": "result", "sessionId": "s", "exitCode": 0}]
        script = "".join(f"echo '{json.dumps(e)}'\n" for e in events)
        seen = []
        out = ai.run_copilot([self.fake(script)], cwd=None, timeout=20, on_line=seen.append)
        self.assertEqual(out["answer"], "hello")
        self.assertEqual(out["error"], "")
        self.assertEqual(out["rc"], 0)
        self.assertEqual(len(seen), 3)

    def test_failure_reports_stderr(self):
        out = ai.run_copilot([self.fake("echo boom >&2\nexit 3\n")], cwd=None, timeout=20)
        self.assertEqual(out["error"], "boom")
        self.assertEqual(out["rc"], 3)

    def test_timeout_kills_group(self):
        start = time.monotonic()
        out = ai.run_copilot([self.fake("sleep 30 &\nsleep 30\n")], cwd=None, timeout=1)
        self.assertLess(time.monotonic() - start, 15)
        self.assertIn("timed out", out["error"])

    def test_no_answer(self):
        out = ai.run_copilot([self.fake("exit 0\n")], cwd=None, timeout=20,
                             env=dict(os.environ))
        self.assertEqual(out["error"], "the model returned no answer")


if __name__ == "__main__":
    unittest.main()
