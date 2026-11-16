"""Portal AI: request validation, private gating, run jobs and auto-review."""
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai  # noqa: E402
import portal  # noqa: E402

CFG = {"enabled": True, "model": "fusion", "daily_cap": 5, "timeout_s": 60,
       "review_timeout_s": 600, "allow_jira_github_reads": False}
URL = "https://github.com/example/repo/pull/1"


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)
        self.cfg = dict(CFG)
        self.queued = []
        self.store = ai.Store(self.root / "ai")
        for target, name, value in (
            (portal, "AI_STORE", self.store),
            (portal.portal_settings, "load", lambda: {"ai": self.cfg}),
            (portal, "enqueue_action", lambda job: self.queued.append(job) or f"j{len(self.queued)}"),
            (portal.tm.task_store, "DB_PATH", self.root / "tasks.db"),
            (portal.tm.task_store, "LEGACY", self.root / "absent.json"),
            (portal, "ALIGN_JOBS", {}),
        ):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        portal.AI_ACTIVE.clear()


class StartRunTests(Base):
    def test_validation(self):
        for body in ({"kind": "bogus", "section": "vm"}, {"kind": "explain", "section": "nope"},
                     {"kind": "ask", "section": "vm", "question": "  "},
                     {"kind": "ask", "section": "vm", "question": ["x"]},
                     {"kind": "brief"}):
            with self.assertRaises(ValueError, msg=body):
                portal.start_ai_run(body, False)
        self.assertEqual(self.queued, [])
        self.assertEqual(self.store.usage()["runs"], 0)

    def test_disabled_and_cap(self):
        self.cfg["enabled"] = False
        with self.assertRaisesRegex(ValueError, "turned off"):
            portal.start_ai_run({"kind": "explain", "section": "vm"}, False)
        self.cfg.update(enabled=True, daily_cap=1)
        portal.start_ai_run({"kind": "explain", "section": "vm"}, False)
        with self.assertRaises(ai.CapReached):
            portal.start_ai_run({"kind": "explain", "section": "vm"}, False)

    def test_queues_with_hash_link_and_private_flag(self):
        out = portal.start_ai_run({"kind": "explain", "section": "team"}, True)
        job = self.queued[0]
        self.assertEqual(job["kind"], "ai")
        self.assertEqual(job["link"], f"ai/{out['run_id']}")
        self.assertTrue(self.store.get(out["run_id"])["private"])
        out = portal.start_ai_run({"kind": "explain", "section": "vm"}, True)
        self.assertFalse(self.store.get(out["run_id"])["private"])

    def test_followup_rules(self):
        parent = self.store.save(ai.new_run("explain", section="team", private=True,
                                            session_id="12345678-1234-1234-1234-123456789abc"))
        body = {"kind": "ask", "section": "team", "question": "why?", "parent": parent["id"]}
        with self.assertRaises(PermissionError):
            portal.start_ai_run(body, False)
        portal.AI_ACTIVE.add(parent["id"])
        with self.assertRaisesRegex(ValueError, "previous answer"):
            portal.start_ai_run(body, True)
        portal.AI_ACTIVE.clear()
        out = portal.start_ai_run(body, True)
        run = self.store.get(out["run_id"])
        self.assertEqual(run["resume"], parent["session_id"])
        with self.assertRaisesRegex(ValueError, "not available"):
            portal.start_ai_run({**body, "parent": "ai-" + "0" * 32}, True)


class RunJobTests(Base):
    def run_job(self, body, result):
        out = portal.start_ai_run(body, False)
        job = dict(self.queued[-1], status="running")
        seen = {}

        def fake(argv, **kw):
            seen["argv"], seen["kw"] = argv, kw
            return result
        with patch.object(portal.ai, "run_copilot", fake), \
                patch.object(portal.ai, "copilot_bin", lambda: "/bin/copilot"), \
                patch.object(portal, "ai_section_data", lambda s, p: {"section": s}):
            portal.run_ai_job(job)
        return job, self.store.get(out["run_id"]), seen

    def test_explain_success(self):
        job, run, seen = self.run_job({"kind": "explain", "section": "vm"},
                                      {"answer": "**ok**\nmore", "error": "", "premium": 1,
                                       "session_id": "s", "model": "m"})
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["message"], "**ok**")
        self.assertEqual(run["status"], "done")
        self.assertIn('{"section":"vm"}', seen["argv"][2])
        self.assertIn("shell", ai.PROFILES["read"]["deny"])
        self.assertEqual(seen["kw"]["timeout"], 60)
        self.assertEqual(self.store.usage()["premium"], 1)
        self.assertIn("<strong>ok</strong>", portal.ai_public(run)["answer_html"])
        self.assertNotIn("prompt", portal.ai_public(run))

    def test_brief_uses_supplied_context_and_errors_surface(self):
        job, run, seen = self.run_job({"kind": "brief", "context": {"date": "D", "steps": []}},
                                      {"answer": "", "error": "boom"})
        self.assertEqual(job["status"], "error")
        self.assertEqual(run["error"], "boom")
        self.assertNotIn("context", run)
        self.assertIn('"date":"D"', seen["argv"][2])


class RouteTests(Base):
    def setUp(self):
        super().setUp()
        captured = {}

        class CaptureServer:
            def __init__(self, address, handler):
                captured["handler"] = handler
                raise RuntimeError("captured")
        with patch("http.server.ThreadingHTTPServer", CaptureServer), \
                patch.object(portal, "_server_running", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "captured"):
                portal.cmd_serve(SimpleNamespace(status=False, stop=False, port=0))
        self.handler_class = captured["handler"]

    def get(self, path, private=False):
        h = object.__new__(self.handler_class)
        h.path = path
        h.headers = {"X-Team-Private": "1"} if private else {}
        h.wfile = io.BytesIO()
        res = {}
        h.send_response = lambda status: res.update(status=status)
        h.send_header = lambda k, v: None
        h.end_headers = lambda: None
        h.do_GET()
        res["body"] = json.loads(h.wfile.getvalue().decode() or "null")
        return res

    def test_private_runs_are_gated(self):
        team = self.store.save(ai.new_run("explain", section="team", private=True, answer="secret"))
        vm = self.store.save(ai.new_run("explain", section="vm", answer="x"))
        self.assertEqual(self.get(f"/api/ai/run/{team['id']}")["status"], 403)
        self.assertEqual(self.get(f"/api/ai/run/{team['id']}", True)["body"]["answer"], "secret")
        self.assertEqual(self.get(f"/api/ai/run/{vm['id']}")["status"], 200)
        self.assertEqual(self.get("/api/ai/run/ai-bad")["status"], 404)
        ids = [r["id"] for r in self.get("/api/ai/runs")["body"]["runs"]]
        self.assertEqual(ids, [vm["id"]])
        self.assertEqual(len(self.get("/api/ai/runs", True)["body"]["runs"]), 2)


class ReviewBase(Base):
    def setUp(self):
        super().setUp()
        with portal.tm.task_store.transaction() as data:
            data["tasks"] = [{"id": "T-001", "title": "Review X", "category": "code-review",
                              "status": "in-progress",
                              "reviews": [{"pr_url": URL, "repo": "repo", "pr_number": 1}]},
                             {"id": "T-002", "title": "Code", "category": "dev",
                              "status": "in-progress", "reviews": []}]
        self.rec = {"id": "w1", "slot": "review_ws", "label": "review_ws", "purpose": "review",
                    "rev": "main", "path": str(self.root)}
        self.holder = {}
        self.deployed = {"review_ws": {"project": "iridium", "revision": "main", "roots": []}}
        self.bases = {"main"}
        for name, value in (
            ("slot_of_task", lambda tid: self.holder.get(tid)),
            ("record", lambda slot: self.rec),
            ("roster", lambda cached_only=True: [self.rec, {**self.rec, "id": "w2", "slot": "x",
                                                          "purpose": "dev"}]),
            ("is_free", lambda slot: True),
            ("deployed_manifest", lambda slot: self.deployed.get(slot) or {"error": "no checkout"}),
        ):
            p = patch.object(portal.wm, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(portal, "_pr_bases", lambda t: set(self.bases))
        p.start()
        self.addCleanup(p.stop)

    def add_draft(self, name="d.md"):
        draft_file = self.root / name
        draft_file.write_text("# d")
        with patch.object(portal.tm, "review_drafts_root", lambda: self.root):
            portal.tm.attach_review_draft("T-001", str(draft_file), {URL: "a" * 40})


class AutoReviewTests(ReviewBase):
    def test_start_refusals(self):
        with self.assertRaisesRegex(ValueError, "code-review"):
            portal.start_auto_review("T-002")
        with patch.object(portal.wm, "is_free", lambda slot: False):
            with self.assertRaisesRegex(ValueError, "no free review workspace"):
                portal.start_auto_review("T-001")
        self.holder["T-001"] = "x"
        with patch.object(portal.wm, "record", lambda slot: {**self.rec, "purpose": "dev"}):
            with self.assertRaisesRegex(ValueError, "not a review"):
                portal.start_auto_review("T-001")
        self.assertEqual(self.queued, [])

    def test_start_queues_and_refuses_duplicate(self):
        out = portal.start_auto_review("T-001")
        job = self.queued[0]
        self.assertEqual((job["kind"], job["ws_slot"], job["held"], job["link"]),
                         ("auto-review", "review_ws", False, "task/T-001"))
        self.assertEqual(portal.action_resources(job), {"auto-review:w1"})
        portal.ALIGN_JOBS["j1"] = dict(job, status="queued")
        with self.assertRaisesRegex(ValueError, "already queued"):
            portal.start_auto_review("T-001")
        self.assertEqual(out["workspace"], "review_ws")

    def two_review_slots(self, first: dict, free=lambda slot: True):
        recs = [{**self.rec, "id": "w0", "slot": "rv0", "label": "rv0"}, self.rec]
        self.deployed["rv0"] = first
        return (patch.object(portal.wm, "roster", lambda cached_only=True: recs),
                patch.object(portal.wm, "is_free", free))

    def test_skips_review_workspace_with_other_deployed_manifest(self):
        a, b = self.two_review_slots({"project": "iridium", "revision": "a1_power_on_branch"})
        with a, b:
            out = portal.start_auto_review("T-001")
        self.assertEqual((out["workspace"], out["manifest"]), ("review_ws", "any project @ main"))
        self.assertEqual(self.queued[0]["need"], {"project": "", "revision": "main"})

    def test_skips_occupied_review_workspace(self):
        a, b = self.two_review_slots({"project": "iridium", "revision": "main"},
                                     free=lambda slot: slot != "rv0")
        with a, b:
            self.assertEqual(portal.start_auto_review("T-001")["workspace"], "review_ws")

    def test_refuses_with_reasons_when_no_review_workspace_matches(self):
        self.bases = {"a1_power_on_branch"}
        a, b = self.two_review_slots({"error": "no octl checkout (.repo/metadata.json) in /x"})
        with a, b:
            with self.assertRaisesRegex(ValueError, "no free review workspace") as ctx:
                portal.start_auto_review("T-001")
        msg = str(ctx.exception)
        self.assertIn("a1_power_on_branch", msg)
        self.assertIn("rv0: no octl checkout", msg)
        self.assertIn("review_ws: deploys revision 'main'", msg)
        self.assertEqual(self.queued, [])

    def test_project_comes_from_project_repos_and_must_match(self):
        with portal.tm.task_store.transaction() as data:
            data["tasks"][0]["reviews"].append(
                {"pr_url": "https://github.com/o/project.atlantic2.sw.fw.infra/pull/9"})
        with self.assertRaisesRegex(ValueError, "deploys project 'iridium', not 'atlantic2'"):
            portal.start_auto_review("T-001")
        self.deployed["review_ws"]["project"] = "atlantic2"
        self.assertEqual(portal.start_auto_review("T-001")["manifest"], "atlantic2 @ main")

    def test_mixed_bases_or_projects_are_refused(self):
        self.bases = {"main", "a1_power_on_branch"}
        with self.assertRaisesRegex(ValueError, "different base branches"):
            portal.start_auto_review("T-001")
        self.bases = {"main"}
        with portal.tm.task_store.transaction() as data:
            data["tasks"][0]["reviews"] += [
                {"pr_url": "https://github.com/o/project.iridium.sw.fw.infra/pull/1"},
                {"pr_url": "https://github.com/o/project.atlantic2.sw.fw.infra/pull/2"}]
        with self.assertRaisesRegex(ValueError, "several projects"):
            portal.start_auto_review("T-001")

    def test_held_workspace_with_wrong_manifest_is_refused(self):
        self.holder["T-001"] = "review_ws"
        self.deployed["review_ws"]["revision"] = "a1_power_on_branch"
        with self.assertRaisesRegex(ValueError, "holds review_ws, which cannot review"):
            portal.start_auto_review("T-001")

    def run_review(self, occupy="done", draft=True, error="", redeployed=None,
                   mode="create", answer="done"):
        portal.start_auto_review("T-001", mode)
        if redeployed:
            self.deployed["review_ws"] = redeployed
        job = dict(self.queued[-1], status="running")
        calls = []

        def ws_job(action, slot, body):
            calls.append(action)
            return f"sub-{action}"

        def fake_run(argv, **kw):
            calls.append(("copilot", kw["cwd"], kw["timeout"]))
            if draft:
                self.add_draft(f"d{len(calls)}-{mode}.md")
            return {"answer": answer, "error": error}
        with patch.object(portal, "start_workspace_job", ws_job), \
                patch.object(portal, "_wait_action", lambda jid: {"status": occupy if "occupy" in jid else "done"}), \
                patch.object(portal.ai, "run_copilot", fake_run), \
                patch.object(portal.ai, "copilot_bin", lambda: "/bin/copilot"):
            portal.run_auto_review(job)
        return job, calls

    def test_happy_path_occupies_reviews_notes_and_frees(self):
        job, calls = self.run_review()
        self.assertEqual(job["status"], "done", job.get("message"))
        self.assertEqual(calls, ["occupy", ("copilot", str(self.root), 600), "free"])
        self.assertIn("released", job["message"])
        task = portal._task("T-001")
        self.assertIn("initial auto review draft done", json.dumps(task.get("notes")))

    def test_manifest_changed_while_queued_refuses_before_occupying(self):
        job, calls = self.run_review(redeployed={"project": "iridium",
                                                 "revision": "a1_power_on_branch"})
        self.assertEqual(job["status"], "error")
        self.assertIn("can no longer review", job["message"])
        self.assertIn("a1_power_on_branch", job["message"])
        self.assertEqual(calls, [])

    def test_occupy_failure_stops(self):
        job, calls = self.run_review(occupy="error")
        self.assertEqual(job["status"], "error")
        self.assertEqual(calls, ["occupy"])

    def test_no_draft_is_error_but_still_frees(self):
        job, calls = self.run_review(draft=False)
        self.assertEqual(job["status"], "error")
        self.assertIn("no draft", job["message"])
        self.assertEqual(calls[-1], "free")

    def test_held_slot_is_not_occupied_or_freed(self):
        self.holder["T-001"] = "review_ws"
        job, calls = self.run_review()
        self.assertEqual(job["status"], "done", job.get("message"))
        self.assertEqual([c for c in calls if isinstance(c, str)], [])


if __name__ == "__main__":
    unittest.main()


SID = "12345678-1234-1234-1234-123456789abc"


class ReviewModeTests(ReviewBase):
    run_review = AutoReviewTests.run_review

    def test_mode_gating(self):
        with self.assertRaisesRegex(ValueError, "mode must"):
            portal.start_auto_review("T-001", "bogus")
        with self.assertRaisesRegex(ValueError, "no review draft yet"):
            portal.start_auto_review("T-001", "rereview")
        self.add_draft()
        with self.assertRaisesRegex(ValueError, "already has a review draft"):
            portal.start_auto_review("T-001")
        out = portal.start_auto_review("T-001", "rereview")
        self.assertEqual(out["mode"], "rereview")
        job = self.queued[-1]
        self.assertEqual(job["mode"], "rereview")
        self.assertTrue(job["title"].startswith("Re-review T-001"))
        self.assertEqual(self.store.get(out["run_id"])["mode"], "rereview")

    def test_rereview_registers_new_draft(self):
        self.add_draft("first.md")
        job, calls = self.run_review(mode="rereview")
        self.assertEqual(job["status"], "done", job.get("message"))
        self.assertIn("AI re-review draft done", json.dumps(portal._task("T-001")["notes"]))

    def test_rereview_without_new_commits_is_done_with_marker(self):
        self.add_draft("first.md")
        job, _ = self.run_review(mode="rereview", draft=False,
                                 answer="Nothing new.\n" + ai.NO_CHANGE_MARK)
        self.assertEqual(job["status"], "done", job.get("message"))
        self.assertIn("draft unchanged", job["message"])
        # Without the marker, finishing without a new draft is an error.
        job, _ = self.run_review(mode="rereview", draft=False, answer="ok")
        self.assertEqual(job["status"], "error")

    def test_open_chat_blocks_auto_review(self):
        self.store.save_chat(ai.new_chat("T-001", status="open"))
        with self.assertRaisesRegex(ValueError, "interactive review of T-001 is open"):
            portal.start_auto_review("T-001")


class ReviewChatTests(ReviewBase):
    def setUp(self):
        super().setUp()
        self.cfg["interactive_daily_cap"] = 10
        self.calls = []
        self.argvs = []

    def start(self):
        out = portal.start_review_chat("T-001")
        return out, self.store.get_chat(out["chat_id"])

    def run_job(self, job, result=None, occupy="done", free="done"):
        job = dict(job, status="running")

        def ws_job(action, slot, body):
            self.calls.append(action)
            return f"sub-{action}"

        def fake_run(argv, **kw):
            self.argvs.append(argv)
            return result or {"answer": "ok", "session_id": SID}
        statuses = {"sub-occupy": occupy, "sub-free": free}
        with patch.object(portal, "start_workspace_job", ws_job), \
                patch.object(portal, "_wait_action", lambda jid: {"status": statuses[jid]}), \
                patch.object(portal.ai, "run_copilot", fake_run), \
                patch.object(portal.ai, "copilot_bin", lambda: "/bin/copilot"):
            portal.run_review_chat_job(job)
        return job

    def opened(self):
        out, _ = self.start()
        self.run_job(self.queued[-1])
        return out["chat_id"]

    def test_start_creates_chat_and_resumes_existing(self):
        out, chat = self.start()
        job = self.queued[-1]
        self.assertFalse(out["resumed"])
        self.assertEqual((job["kind"], job["op"], job["link"]),
                         ("review-chat", "open", f"review-chat/{chat['id']}"))
        self.assertEqual(portal.action_resources(job),
                         {f"review-chat:{chat['id']}", "auto-review:w1"})
        self.assertEqual((chat["status"], chat["workspace"], len(chat["turns"])),
                         ("starting", "review_ws", 1))
        use = self.store.usage()
        self.assertEqual((use["runs"], use["interactive"]), (0, 1))
        again = portal.start_review_chat("T-001")
        self.assertEqual((again["chat_id"], again["resumed"]), (chat["id"], True))
        self.assertEqual(len(self.queued), 1)
        with self.assertRaisesRegex(ValueError, "interactive review"):
            portal.start_auto_review("T-001", "create")

    def test_start_refusals(self):
        portal.start_auto_review("T-001")
        portal.ALIGN_JOBS["j1"] = dict(self.queued[0], status="queued")
        with self.assertRaisesRegex(ValueError, "already queued"):
            portal.start_review_chat("T-001")
        portal.ALIGN_JOBS.clear()
        self.cfg["interactive_daily_cap"] = 0
        with self.assertRaises(ai.CapReached):
            portal.start_review_chat("T-001")
        with self.assertRaisesRegex(ValueError, "code-review"):
            portal.start_review_chat("T-002")

    def test_open_occupies_and_records_session(self):
        chat_id = self.opened()
        chat = self.store.get_chat(chat_id)
        self.assertEqual((chat["status"], chat["session_id"], chat["occupied_here"]),
                         ("open", SID, True))
        self.assertEqual(self.calls, ["occupy"])
        self.assertNotIn("--resume", self.argvs[0])
        self.assertEqual(self.store.get(chat["turns"][0])["status"], "done")

    def test_failed_open_ends_chat_and_frees(self):
        self.start()
        job = self.run_job(self.queued[-1], {"answer": "", "error": "boom"})
        chat = self.store.chats("T-001")[0]
        self.assertEqual(job["status"], "error")
        self.assertEqual(chat["status"], "ended")
        self.assertIn("failed to start: boom", chat["end_reason"])
        self.assertEqual(self.calls, ["occupy", "free"])

    def test_turns_resume_the_session_read_only(self):
        chat_id = self.opened()
        with self.assertRaisesRegex(ValueError, "message is required"):
            portal.review_chat_action(chat_id, "turn", {"message": "  "})
        out = portal.review_chat_action(chat_id, "turn", {"message": "why line 3?"})
        job = self.queued[-1]
        self.assertEqual((job["op"], job["run_id"]), ("turn", out["run_id"]))
        portal.ALIGN_JOBS["busy"] = dict(job, status="queued")
        with self.assertRaisesRegex(ValueError, "wait for the current answer"):
            portal.review_chat_action(chat_id, "turn", {"message": "again"})
        portal.ALIGN_JOBS.clear()
        self.run_job(job)
        argv = self.argvs[-1]
        self.assertEqual(argv[argv.index("--resume") + 1], SID)
        self.assertIn("why line 3?", argv[argv.index("-p") + 1])
        deny = [argv[i + 1] for i, a in enumerate(argv) if a == "--deny-tool"]
        self.assertIn("GitHub", deny)
        self.assertIn("shell(gh pr review)", deny)
        self.assertEqual(self.store.usage()["interactive"], 2)
        public = portal.review_chat_public(self.store.get_chat(chat_id))
        self.assertEqual([t["status"] for t in public["turns"]], ["done", "done"])
        self.assertFalse(public["busy"])

    def test_not_ready_unknown_and_ended(self):
        out, _ = self.start()
        with self.assertRaisesRegex(ValueError, "not ready"):
            portal.review_chat_action(out["chat_id"], "turn", {"message": "hi"})
        with self.assertRaises(LookupError):
            portal.review_chat_action("rc-" + "0" * 32, "turn", {"message": "hi"})
        with self.assertRaisesRegex(ValueError, "unknown review chat action"):
            portal.review_chat_action(out["chat_id"], "merge", {})
        portal._chat_update(out["chat_id"], status="ended")
        with self.assertRaisesRegex(ValueError, "has ended"):
            portal.review_chat_action(out["chat_id"], "end", {})

    def preview(self, chat_id):
        out = portal.review_chat_action(chat_id, "preview", {})
        self.run_job(self.queued[-1], {"answer": "PREVIEW TEXT", "session_id": SID})
        return out["run_id"]

    def test_submit_needs_latest_confirmed_preview(self):
        chat_id = self.opened()
        with self.assertRaisesRegex(ValueError, "confirm the latest submit preview"):
            portal.review_chat_action(chat_id, "submit", {"preview_run": "ai-x"})
        first = self.preview(chat_id)
        second = self.preview(chat_id)
        with self.assertRaisesRegex(ValueError, "confirm the latest submit preview"):
            portal.review_chat_action(chat_id, "submit", {"preview_run": first})
        portal.review_chat_action(chat_id, "turn", {"message": "drop finding 2"})
        with self.assertRaisesRegex(ValueError, "moved on"):
            portal.review_chat_action(chat_id, "submit", {"preview_run": second})
        self.assertNotIn("submit", [j["op"] for j in self.queued])

    def test_confirmed_submit_posts_with_submit_profile(self):
        chat_id = self.opened()
        preview = self.preview(chat_id)
        self.assertEqual(self.store.get_chat(chat_id)["preview_run"], preview)
        portal.review_chat_action(chat_id, "submit", {"preview_run": preview})
        job = self.queued[-1]
        self.assertEqual((job["op"], job["preview_run"]), ("submit", preview))
        self.run_job(job, {"answer": "posted https://x", "session_id": SID})
        argv = self.argvs[-1]
        deny = [argv[i + 1] for i, a in enumerate(argv) if a == "--deny-tool"]
        self.assertNotIn("GitHub", deny)
        self.assertNotIn("shell(gh pr review)", deny)
        self.assertIn("shell(gh pr merge)", deny)
        self.assertIn("shell(gh api)", deny)
        self.assertIn("PREVIEW TEXT", argv[argv.index("-p") + 1])
        chat = self.store.get_chat(chat_id)
        self.assertIsNone(chat["preview_run"])
        self.assertEqual(chat["submitted_run"], chat["turns"][-1])
        self.assertIn("submitted to GitHub", json.dumps(portal._task("T-001")["notes"]))

    def test_end_frees_or_keeps_the_workspace(self):
        chat_id = self.opened()
        with self.assertRaisesRegex(ValueError, "free must be"):
            portal.review_chat_action(chat_id, "end", {"free": "yes"})
        portal.review_chat_action(chat_id, "end", {"free": False})
        job = self.run_job(self.queued[-1])
        chat = self.store.get_chat(chat_id)
        self.assertEqual((chat["status"], chat["occupied_here"]), ("ended", True))
        self.assertIn("kept for T-001", job["message"])
        self.assertEqual(self.calls, ["occupy"])
        self.assertIn("interactive AI review ended", json.dumps(portal._task("T-001")["notes"]))

        self.store.save_chat(dict(chat, status="open"))
        portal.review_chat_action(chat_id, "end", {"free": True})
        job = self.run_job(self.queued[-1])
        self.assertEqual(self.calls, ["occupy", "free"])
        self.assertEqual(job["status"], "done")
        self.assertIn("review_ws released", job["message"])
        self.assertFalse(self.store.get_chat(chat_id)["occupied_here"])

    def test_sweep_ends_idle_chats(self):
        chat_id = self.opened()
        self.assertEqual(portal.sweep_review_chats(), [])
        later = portal.time.time() + portal.REVIEW_CHAT_IDLE_S + 60
        self.assertEqual(portal.sweep_review_chats(later), [chat_id])
        job = self.queued[-1]
        self.assertEqual((job["op"], job["free"]), ("end", True))
        self.assertIn("idle 24 h", job["reason"])

    def test_recover_keeps_interrupted_start_and_its_workspace(self):
        out, chat = self.start()
        run = self.store.get(chat["turns"][0])
        self.store.save(dict(run, status="running"))
        n = len(self.queued)
        with patch.object(portal.wm, "slot_of_task", lambda tid: chat["ws_slot"]):
            portal.recover_review_chats()
        self.assertEqual(len(self.queued), n)  # nothing ended, nothing freed
        self.assertIn("interrupted", self.store.get(run["id"])["error"])
        chat = self.store.get_chat(out["chat_id"])
        self.assertEqual((chat["status"], chat["occupied_here"]), ("interrupted", True))
        self.assertIn("portal restart", chat["interrupted"])
        self.assertTrue(self.store.chats("T-001", open_only=True))
        again = portal.start_review_chat("T-001")
        self.assertEqual((again["chat_id"], again["resumed"]), (chat["id"], True))
        with self.assertRaisesRegex(ValueError, "not ready"):
            portal.review_chat_action(chat["id"], "turn", {"message": "hi"})

    def test_recover_leaves_pending_jobs_alone(self):
        out, chat = self.start()
        portal.ALIGN_JOBS["j1"] = dict(self.queued[-1], status="queued")
        portal.recover_review_chats()
        self.assertEqual(self.store.get_chat(out["chat_id"])["status"], "starting")
        self.assertEqual(self.store.get(chat["turns"][0])["status"], "queued")
        portal.ALIGN_JOBS.clear()
        # an open chat with a queued turn keeps the turn; a lost turn is failed
        open_id = out["chat_id"]
        self.run_job(self.queued[0])
        turn = portal.review_chat_action(open_id, "turn", {"message": "q"})
        portal.ALIGN_JOBS["j2"] = dict(self.queued[-1], status="queued")
        portal.recover_review_chats()
        self.assertEqual(self.store.get(turn["run_id"])["status"], "queued")
        portal.ALIGN_JOBS.clear()
        portal.recover_review_chats()
        self.assertIn("send the message again", self.store.get(turn["run_id"])["error"])
        self.assertEqual(self.store.get_chat(open_id)["status"], "open")

    def test_retry_reopens_without_reoccupying(self):
        out, chat = self.start()
        with self.assertRaisesRegex(ValueError, "only an interrupted start"):
            portal.review_chat_action(out["chat_id"], "retry", {})
        with patch.object(portal.wm, "slot_of_task", lambda tid: chat["ws_slot"]):
            portal.recover_review_chats()
        used = self.store.usage()["interactive"]
        res = portal.review_chat_action(out["chat_id"], "retry", {})
        job = self.queued[-1]
        self.assertEqual((job["op"], job["run_id"]), ("open", res["run_id"]))
        chat = self.store.get_chat(out["chat_id"])
        self.assertEqual((chat["status"], chat["interrupted"], len(chat["turns"])),
                         ("starting", None, 2))
        self.assertEqual(self.store.usage()["interactive"], used + 1)
        with patch.object(portal.wm, "slot_of_task", lambda tid: chat["ws_slot"]):
            self.run_job(job)
        self.assertEqual(self.calls, [])  # workspace already held: no second occupy
        chat = self.store.get_chat(out["chat_id"])
        self.assertEqual((chat["status"], chat["session_id"]), ("open", SID))

    def test_open_after_restart_recognises_its_occupied_workspace(self):
        out, chat = self.start()
        with patch.object(portal.wm, "slot_of_task", lambda tid: chat["ws_slot"]):
            self.run_job(self.queued[-1])
        chat = self.store.get_chat(out["chat_id"])
        self.assertEqual((self.calls, chat["occupied_here"]), ([], True))

    def test_interrupted_chat_can_be_ended_and_freed(self):
        out, chat = self.start()
        with patch.object(portal.wm, "slot_of_task", lambda tid: chat["ws_slot"]):
            portal.recover_review_chats()
        portal.review_chat_action(out["chat_id"], "end", {"free": True})
        self.run_job(self.queued[-1])
        self.assertEqual(self.calls, ["free"])
        self.assertEqual(self.store.get_chat(out["chat_id"])["status"], "ended")


class ReviewChatRouteTests(RouteTests):
    def raw(self, path):
        h = object.__new__(self.handler_class)
        h.path = path
        h.headers = {}
        h.wfile = io.BytesIO()
        res = {}
        h.send_response = lambda status: res.update(status=status)
        h.send_header = lambda k, v: None
        h.end_headers = lambda: None
        h.do_GET()
        return res["status"], h.wfile.getvalue().decode()

    def test_page_and_chat_endpoints(self):
        self.assertIn("aichat.js", portal.ASSET_NAMES)
        status, page = self.raw("/ai-chat?chat=rc-" + "0" * 32)
        self.assertEqual(status, 200)
        self.assertIn("/assets/aichat.js", page)
        self.assertIn("PortalAIChat.mountFromQuery", page)
        chat = self.store.save_chat(ai.new_chat("T-001", status="open", workspace="w"))
        self.store.save_chat(dict(ai.new_chat("T-002"), status="ended"))
        body = self.get("/api/ai/review-chats?open=1")["body"]
        self.assertEqual([c["id"] for c in body["chats"]], [chat["id"]])
        self.assertEqual(len(self.get("/api/ai/review-chats")["body"]["chats"]), 2)
        res = self.get(f"/api/ai/review-chat/{chat['id']}")
        self.assertEqual((res["status"], res["body"]["busy"]), (200, False))
        self.assertIn("interactive_cap", res["body"]["usage"])
        self.assertEqual(self.get("/api/ai/review-chat/rc-bad")["status"], 404)

    def test_private_runs_are_gated(self):
        pass  # covered by RouteTests
