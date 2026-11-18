"""Cancelling queue actions: owner cleanup, AI interruption, machine jobs, HTTP."""
import io
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai  # noqa: E402
from action_queue import ActionQueue  # noqa: E402
import portal  # noqa: E402
from test_portal_ai import Base  # noqa: E402


def handler(path, payload, headers=None):
    captured = {}

    class CaptureServer:
        def __init__(self, address, h):
            captured["handler"] = h
            raise RuntimeError("captured")

    with patch("http.server.ThreadingHTTPServer", CaptureServer), \
            patch.object(portal, "_server_running", return_value=None):
        try:
            portal.cmd_serve(SimpleNamespace(status=False, stop=False, port=0))
        except RuntimeError:
            pass
    h = object.__new__(captured["handler"])
    body = json.dumps(payload).encode()
    h.path = path
    h.headers = {"Content-Length": str(len(body)), **(headers or {})}
    h.rfile = io.BytesIO(body)
    h._send = lambda code, text, *args: (code, json.loads(text))
    return h


class QueueCancelTests(Base):
    """Real ActionQueue + real portal executor; only copilot is faked."""

    def setUp(self):
        super().setUp()
        self.queue = ActionQueue(self.root / "actions.db", portal.execute_and_notify,
                                 portal.ALIGN_JOBS, portal.ALIGN_LOCK,
                                 resources=portal.action_resources,
                                 interrupt=portal.interrupt_action, max_workers=2)
        p = patch.object(portal, "ACTION_QUEUE", self.queue)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.queue.join)
        self.addCleanup(self.queue.stop)
        for name, value in (("copilot_bin", lambda: "/bin/copilot"),):
            p = patch.object(portal.ai, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(portal, "ai_section_data", lambda s, priv: {"section": s})
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(portal.EVENTS, "publish")
        self.published = p.start()
        self.addCleanup(p.stop)

    def enqueue_ai(self):
        out = portal.start_ai_run({"kind": "explain", "section": "vm"}, False)
        return self.queue.enqueue(self.queued[-1]), out["run_id"]

    def wait_status(self, ident, statuses):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with portal.ALIGN_LOCK:
                job = dict(portal.ALIGN_JOBS[ident])
            if job["status"] in statuses and ident not in self.queue._running:
                return job
            time.sleep(.02)
        self.fail(f"action never reached {statuses}: {job}")

    def test_queued_ai_cancel_never_runs_and_closes_the_run_record(self):
        ident, run_id = self.enqueue_ai()
        with patch.object(portal.ai, "run_copilot", side_effect=AssertionError("ran")):
            out = portal.cancel_action(ident)
            self.queue.start()
            time.sleep(.2)
        self.assertEqual(out["status"], "cancelled")
        self.assertNotIn("request", out["job"])
        self.assertEqual(portal.ALIGN_JOBS[ident]["status"], "cancelled")
        run = self.store.get(run_id)
        self.assertEqual(run["status"], "error")
        self.assertIn("cancelled before it started", run["error"])

    def test_running_ai_is_interrupted_and_finishes_cancelled_without_notification(self):
        entered = threading.Event()

        def fake(argv, **kw):
            entered.set()
            self.assertTrue(kw["cancel"].wait(5), "cancel event was never set")
            return {"answer": "", "error": ai.CANCELLED_ERROR}
        with patch.object(portal.ai, "run_copilot", fake):
            ident, run_id = self.enqueue_ai()
            self.queue.start()
            self.assertTrue(entered.wait(5))
            self.assertIn(ident, portal.AI_CANCEL)
            self.assertEqual(portal.cancel_action(ident)["status"], "cancelling")
            job = self.wait_status(ident, {"cancelled", "error", "done"})
        self.assertEqual(job["status"], "cancelled")
        self.assertIn(ai.CANCELLED_ERROR, job["message"])
        self.assertEqual(self.store.get(run_id)["error"], ai.CANCELLED_ERROR)
        self.assertNotIn(ident, portal.AI_CANCEL)
        self.published.assert_not_called()

    def test_running_action_without_an_interrupt_point_is_refused(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def fake(job):
            entered.set()
            release.wait(5)
            job["status"] = "done"
        with patch.object(portal, "run_vm_refresh", fake), \
                patch.object(portal, "VM_REFRESH_LOCK", threading.Lock()):
            ident = self.queue.enqueue({"kind": "vm-refresh", "target": "", "slot": "m"})
            self.queue.start()
            self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(ValueError, "cannot be interrupted"):
                portal.cancel_action(ident)
            release.set()
            self.assertEqual(self.wait_status(ident, {"done", "error"})["status"], "done")


class SettleTests(Base):
    def test_queued_chat_open_becomes_interrupted_and_its_turn_fails(self):
        chat = self.store.save_chat(ai.new_chat("T-1", ws_slot="one", workspace="One"))
        run = self.store.save(ai.new_run("auto-review", section="work", task_id="T-1"))
        portal._settle_cancelled({"kind": "review-chat", "op": "open",
                                  "chat_id": chat["id"], "run_id": run["id"]})
        chat = self.store.get_chat(chat["id"])
        self.assertEqual(chat["status"], portal.CHAT_INTERRUPTED)
        self.assertIn("cancelled", chat["interrupted"])
        self.assertIn("send the message again", self.store.get(run["id"])["error"])

    def test_finished_run_is_left_alone(self):
        run = self.store.save(dict(ai.new_run("explain", section="vm"), status="done"))
        portal._settle_cancelled({"kind": "ai", "run_id": run["id"]})
        self.assertEqual(self.store.get(run["id"])["status"], "done")

    def test_wait_action_returns_on_cancelled_sub_job(self):
        portal.ALIGN_JOBS["action-x"] = {"status": "cancelled", "message": "stop"}
        self.assertEqual(portal._wait_action("action-x", timeout=3)["status"], "cancelled")

    def test_interrupt_hook_only_accepts_registered_jobs(self):
        self.assertFalse(portal.interrupt_action({"id": "action-none"}))
        job = {"id": "action-reg"}
        with portal.ai_cancellable(job) as event:
            self.assertTrue(portal.interrupt_action(job))
            self.assertTrue(event.is_set())
        self.assertFalse(portal.interrupt_action(job))

    def test_cancelled_failure_is_not_published_as_a_failure(self):
        with patch.object(portal.EVENTS, "publish") as publish:
            portal.notify_action({"kind": "ai", "status": "error", "cancel_requested": "t"})
            publish.assert_not_called()
            portal.notify_action({"kind": "ai", "status": "error"})
            publish.assert_called_once()


class VmCancelTests(unittest.TestCase):
    ROW = {"id": "j1", "status": "running", "target": "lab1", "kind": "build",
           "tag": "ws", "ws_label": "one/fw"}

    def test_start_validates_the_machine_job_and_claims_its_target(self):
        fake_vd = SimpleNamespace(jobs=lambda: [self.ROW, dict(self.ROW, id="j2", status="passed")])
        queued = []
        with patch.object(portal, "vd", fake_vd), \
                patch.object(portal, "enqueue_action", lambda job: queued.append(job) or "a1"):
            self.assertEqual(portal.start_vm_cancel_job("j1"), "a1")
            with self.assertRaises(LookupError):
                portal.start_vm_cancel_job("nope")
            with self.assertRaisesRegex(ValueError, "not running"):
                portal.start_vm_cancel_job("j2")
        job = queued[0]
        self.assertEqual((job["kind"], job["vm_job"], job["target"]), ("vm-cancel", "j1", "lab1"))
        self.assertEqual(portal.action_resources(job), {"domain:delegation", "vm:lab1"})

    def test_run_goes_through_vm_delegate_and_reports_its_outcome(self):
        cancel = Mock(return_value={"jobs": [], "acted": "cancelled build ws",
                                    "cancel": {"result": "CANCELLED"}})
        job = {"kind": "vm-cancel", "vm_job": "j1", "target": "lab1"}
        with patch.object(portal, "vd", SimpleNamespace(portal_cancel=cancel)):
            portal.run_vm_cancel(job)
            cancel.assert_called_once_with("j1")
            self.assertEqual(job["status"], "done")
            self.assertEqual(job["result"]["cancel"]["result"], "CANCELLED")
            cancel.return_value = {"error": "not cancelled: MISMATCH pid=9"}
            portal.run_vm_cancel(job)
        self.assertEqual(job["status"], "error")
        self.assertIn("MISMATCH", job["message"])


class EndpointTests(unittest.TestCase):
    ID = "action-" + "a" * 32

    def test_cancel_endpoint_maps_outcomes(self):
        for error, code in ((LookupError("x"), 404), (ValueError("already done"), 409),
                            (RuntimeError("down"), 503)):
            with patch.object(portal, "cancel_action", side_effect=error):
                status, _ = handler(f"/api/actions/{self.ID}/cancel", {}).do_POST()
            self.assertEqual(status, code)
        with patch.object(portal, "cancel_action",
                          return_value={"status": "cancelled", "job": {"id": self.ID}}) as c:
            status, body = handler(f"/api/actions/{self.ID}/cancel", {}).do_POST()
        c.assert_called_once_with(self.ID)
        self.assertEqual((status, body["status"]), (200, "cancelled"))

    def test_cancel_endpoint_rejects_cross_origin(self):
        with patch.object(portal, "cancel_action") as c:
            status, _ = handler(f"/api/actions/{self.ID}/cancel", {},
                                {"Origin": "http://evil.example", "Host": "127.0.0.1:8787"}).do_POST()
        self.assertEqual(status, 403)
        c.assert_not_called()

    def test_vm_cancel_endpoint(self):
        with patch.object(portal, "start_vm_cancel_job", return_value="a1") as s:
            for bad in ({}, {"id": 3}, {"id": "j1", "x": 1}):
                status, _ = handler("/api/vm-cancel", bad).do_POST()
                self.assertEqual(status, 400, bad)
            status, _ = handler("/api/vm-cancel", {"id": "j1"},
                                {"Origin": "http://evil.example", "Host": "h"}).do_POST()
            self.assertEqual(status, 403)
            s.assert_not_called()
            status, body = handler("/api/vm-cancel", {"id": "j1"}).do_POST()
        self.assertEqual((status, body["job"]), (200, "a1"))
        with patch.object(portal, "start_vm_cancel_job", side_effect=ValueError("not running")):
            self.assertEqual(handler("/api/vm-cancel", {"id": "j1"}).do_POST()[0], 409)

    def test_vm_main_endpoint(self):
        iid = "i-012e7b37b8bfe283b"
        fake = SimpleNamespace(portal_set_main=Mock(return_value={"machines": [], "main_instance": iid}))
        with patch.object(portal, "vd", fake):
            for bad in ({}, {"id": 3}, {"id": "vm"}, {"id": "i-xyz"}, {"id": iid, "user": "x"},
                        {"id": iid + "; rm"}):
                self.assertEqual(handler("/api/vm-main", bad).do_POST()[0], 400, bad)
            status, _ = handler("/api/vm-main", {"id": iid},
                                {"Origin": "http://evil.example", "Host": "h"}).do_POST()
            self.assertEqual(status, 403)
            fake.portal_set_main.assert_not_called()
            status, body = handler("/api/vm-main", {"id": iid}).do_POST()
            self.assertEqual((status, body["main_instance"]), (200, iid))
            fake.portal_set_main.assert_called_once_with(iid)
            fake.portal_set_main.return_value = {"error": "not one of your VMs"}
            self.assertEqual(handler("/api/vm-main", {"id": iid}).do_POST()[0], 400)
            self.assertTrue(portal.VM_REFRESH_LOCK.acquire(blocking=False))
            try:
                self.assertEqual(handler("/api/vm-main", {"id": iid}).do_POST()[0], 409)
            finally:
                portal.VM_REFRESH_LOCK.release()
        with patch.object(portal, "vd", None):
            self.assertEqual(handler("/api/vm-main", {"id": iid}).do_POST()[0], 503)

    def test_vm_target_endpoint(self):
        data = {"action": "remove", "name": "desk"}
        result = {"jobs": [], "targets": [], "machines": [], "message": "Target desk removed"}
        fake = SimpleNamespace(portal_target=Mock(return_value=result))
        live = Mock()
        with patch.object(portal, "vd", fake), patch.object(portal, "LIVE", live), \
                patch.object(portal, "vmlog_payload", return_value=result):
            status, _ = handler("/api/vm-target", data,
                                {"Origin": "http://evil.example", "Host": "h"}).do_POST()
            self.assertEqual(status, 403)
            fake.portal_target.assert_not_called()
            self.assertEqual(handler("/api/vm-target", data).do_POST(), (200, result))
            fake.portal_target.assert_called_once_with(data)
            live.sync.assert_called_once()
            fake.portal_target.side_effect = ValueError("running jobs")
            self.assertEqual(handler("/api/vm-target", data).do_POST()[0], 400)
            self.assertFalse(portal.VM_REFRESH_LOCK.locked())
            fake.portal_target.side_effect = OSError("storage unavailable")
            self.assertEqual(handler("/api/vm-target", data).do_POST()[0], 500)
            fake.portal_target.side_effect = None
            fake.portal_target.return_value = {"error": "mctl failed"}
            self.assertEqual(handler("/api/vm-target", data).do_POST()[0], 400)
            with portal.VM_REFRESH_LOCK:
                self.assertEqual(handler("/api/vm-target", data).do_POST()[0], 409)
        with patch.object(portal, "vd", None):
            self.assertEqual(handler("/api/vm-target", data).do_POST()[0], 503)

    def test_removed_targets_are_not_enabled_for_live_status(self):
        cfg = {"live_targets": ["desk"], "live_interval_s": 15}
        with patch.object(portal.portal_settings, "load", return_value={"machines": cfg}), \
                patch.object(portal, "vd", SimpleNamespace(targets=lambda: ["vm"])):
            self.assertEqual(portal.registered_machine_settings(),
                             {"live_targets": [], "live_interval_s": 15})
            self.assertEqual(cfg["live_targets"], ["desk"], "saved settings must not be rewritten")

    def test_every_registered_target_gets_a_terminal(self):
        fake = SimpleNamespace(targets=lambda: ["vm", "desk"])
        with patch.object(portal, "vd", fake):
            self.assertEqual(portal.term_spec({"kind": "shell", "target": "desk"})["target"], "desk")
            with self.assertRaises(portal.terminal.TermError):
                portal.term_spec({"kind": "shell", "target": "gone"})
        with patch.object(portal, "vd", None), self.assertRaises(portal.terminal.TermError):
            portal.term_spec({"kind": "shell", "target": "vm"})

    def test_bmc_terminal_needs_a_registered_bmc(self):
        fake = SimpleNamespace(targets=lambda: ["vm", "desk"], target_bmcs=lambda: {"desk": "bmc-desk"})
        with patch.object(portal, "vd", fake):
            self.assertEqual(portal.term_spec({"kind": "bmc", "target": "desk"})["argv"][1:], ["-t", "desk", "bmc"])
            for target in ("vm", "gone", ""):
                with self.assertRaises(portal.terminal.TermError):
                    portal.term_spec({"kind": "bmc", "target": target})

    def test_legacy_terminal_targets_setting_is_ignored_not_rejected(self):
        out = portal.portal_settings.validate(
            {"machines": {"terminal_targets": ["vm"], "live_targets": ["vm", "desk"]}})
        self.assertNotIn("terminal_targets", out["machines"])
        self.assertEqual(out["machines"]["live_targets"], ["vm", "desk"])
        with self.assertRaises(portal.portal_settings.SettingsError):
            portal.portal_settings.validate({"machines": {"bogus": 1}})


if __name__ == "__main__":
    unittest.main()
