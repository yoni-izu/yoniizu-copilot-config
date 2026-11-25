import sys
from pathlib import Path
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import events  # noqa: E402
import portal  # noqa: E402


class DisconnectAfterFlush:
    def __init__(self, count=1):
        self.data = bytearray()
        self.count = count
        self.flushes = 0

    def write(self, data):
        self.data.extend(data)

    def flush(self):
        self.flushes += 1
        if self.flushes >= self.count:
            raise OSError("test client disconnected")


def stream_response(last_event_id="", since="", flush_count=1):
    captured = {}

    class CaptureServer:
        def __init__(self, address, handler):
            captured["handler"] = handler
            raise RuntimeError("captured")

    with patch("http.server.ThreadingHTTPServer", CaptureServer), \
            patch.object(portal, "_server_running", return_value=None):
        try:
            portal.cmd_serve(SimpleNamespace(status=False, stop=False, port=0))
        except RuntimeError as error:
            if str(error) != "captured":
                raise

    handler = object.__new__(captured["handler"])
    handler.path = "/api/stream" + (f"?since={since}" if since else "")
    handler.headers = {"Last-Event-ID": last_event_id}
    handler.connection = SimpleNamespace(settimeout=lambda seconds: None)
    handler.wfile = DisconnectAfterFlush(flush_count)
    handler.send_response = lambda code: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    handler.do_GET()
    return bytes(handler.wfile.data)


class PortalStreamTests(unittest.TestCase):
    def setUp(self):
        self.bus = events.Bus(lambda: {"enabled": True, "windows_toast": False})
        self.events_patch = patch.object(portal, "EVENTS", self.bus)
        self.events_patch.start()
        self.addCleanup(self.events_patch.stop)
        self.live_patch = patch.object(portal, "live_payload", return_value={"changed": 0})
        self.live_patch.start()
        self.addCleanup(self.live_patch.stop)
        self.ai_patch = patch.object(portal, "ai_usage_payload", return_value={"used": 0})
        self.ai_patch.start()
        self.addCleanup(self.ai_patch.stop)

    def test_initial_connection_requests_resync(self):
        response = stream_response()
        self.assertIn(b"event: reset\n", response)
        self.assertIn(b'"live":{"changed":0}', response)

    def test_served_page_embeds_cursor_before_loading_assets(self):
        cursor = self.bus.cursor()
        with patch.object(portal.wm, "load", return_value={}), \
                patch.object(portal, "asset_stamp", return_value="1"), \
                patch.object(portal, "boot_payload", return_value="{}"):
            html = portal.render_dashboard_html([], event_cursor=cursor)
        expected = f"window.PORTAL_EVENT_CURSOR={{\"instance\":\"{cursor['instance']}\",\"seq\":0}}"
        self.assertIn(expected, html)

    def test_initial_page_cursor_receives_state_and_changes_since_render(self):
        cursor = self.bus.cursor()
        self.bus.publish("queue", "after render")
        response = stream_response(
            since=f"{cursor['instance']}:{cursor['seq']}", flush_count=2)
        self.assertIn(b"event: hello\n", response)
        self.assertIn(b"event: message\n", response)
        self.assertIn(b'"title":"after render"', response)

    def test_reconnect_replays_events_after_last_event_id(self):
        cursor = self.bus.cursor()
        self.bus.publish("queue", "scan finished")
        response = stream_response(f"{cursor['instance']}:{cursor['seq']}")
        self.assertIn(b"event: message\n", response)
        self.assertIn(b'"title":"scan finished"', response)


class JobNotificationTests(unittest.TestCase):
    def setUp(self):
        self.rows = {}
        self.cfg = {"notify": {"status_updates": True}}
        self.bus = events.Bus(lambda: {"windows_toast": False})
        self.observer = events.JobTransitions(lambda: copy.deepcopy(self.rows), portal.notify_machine_job)
        for name, value in (("EVENTS", self.bus), ("MACHINE_NOTIFICATIONS", self.observer)):
            p = patch.object(portal, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(portal.portal_settings, "load", return_value=self.cfg)
        p.start()
        self.addCleanup(p.stop)

    def row(self, status, kind="build", **extra):
        return dict(status=status, kind=kind, target="lab", ws="one/fw", rc=None, **extra)

    def titles(self):
        return [event["title"] for event in self.bus.since(0)["events"]]

    def test_history_is_silent_then_each_transition_is_announced_once(self):
        self.rows["old"] = self.row("passed")
        portal.observe_machine_jobs()
        self.assertEqual(self.titles(), [])
        for status in ("running", "running", "cancelled", "cancelled"):
            self.rows["new"] = self.row(status)
            portal.observe_machine_jobs()
        self.assertEqual(self.titles(), ["Build running on lab", "Build cancelled on lab"])
        self.assertEqual(self.bus.since(0)["events"][-1]["link"], "#vm")

    def test_settled_without_observed_running_and_unknown_are_not_success(self):
        portal.observe_machine_jobs()
        self.rows["fast"] = self.row("failed")
        self.rows["unconfirmed"] = self.row("unknown")
        portal.observe_machine_jobs()
        self.assertEqual(len(self.titles()), 2)
        self.assertEqual([e["tone"] for e in self.bus.since(0)["events"]], ["bad", "warn"])

    def test_muting_nonterminal_does_not_mute_outcomes_or_replay_on_enable(self):
        portal.observe_machine_jobs()
        self.cfg["notify"]["status_updates"] = False
        self.rows["j"] = self.row("running")
        portal.observe_machine_jobs()
        self.cfg["notify"]["status_updates"] = True
        portal.observe_machine_jobs()
        self.assertEqual(self.titles(), [])
        self.cfg["notify"]["status_updates"] = False
        self.rows["j"] = self.row("passed")
        portal.observe_machine_jobs()
        self.assertEqual(len(self.titles()), 1)

    def test_flash_verified_and_not_verified_are_deduplicated(self):
        portal.observe_machine_jobs()
        self.rows["flash"] = self.row("running", kind="flash", last_line="writing")
        portal.observe_machine_jobs()
        self.rows["flash"].update(status="failed", rc=2, last_line="read-back mismatch")
        portal.observe_machine_jobs()
        portal.observe_machine_jobs()
        events_ = self.bus.since(0)["events"]
        self.assertEqual([e["title"] for e in events_],
                         ["Flash started \u00b7 lab", "Flash NOT verified \u00b7 lab"])
        self.assertEqual(events_[-1]["body"], "read-back mismatch")
        self.assertEqual(events_[-1]["link"], "/flash?id=flash")

    def test_quiet_operations_keep_failures_and_cancellations(self):
        for kind, op in (("vm-refresh", ""), ("review-chat", "turn"), ("review-chat", "preview")):
            self.bus = events.Bus(lambda: {"windows_toast": False})
            with patch.object(portal, "EVENTS", self.bus):
                for status in ("queued", "running", "done", "error", "cancelled"):
                    portal.notify_action({"kind": kind, "op": op, "status": status})
            self.assertEqual(len(self.bus.since(0)["events"]), 2)

    def test_action_nonterminal_toggle_keeps_approval_and_outcomes(self):
        self.cfg["notify"]["status_updates"] = False
        for status in ("queued", "running", "cancelling", "approval_required", "done", "cancelled"):
            portal.notify_action({"kind": "ai", "status": status})
        self.assertEqual(self.titles(), ["AI needs approval", "AI finished", "AI cancelled"])

    def test_domain_and_live_observations_share_one_transition_baseline(self):
        portal.observe_machine_jobs()
        self.rows["j"] = self.row("running")
        owner = SimpleNamespace(
            parse_probe=lambda text: {}, job_statuses=lambda: copy.deepcopy(self.rows),
            refresh=Mock(), sync_logs=Mock())
        with patch.object(portal, "vd", owner):
            portal.publish_domain_changes(["delegation"])
            portal.live_block("lab", "synthetic block")
            self.rows["j"]["status"] = "cancelled"
            portal.publish_domain_changes(["delegation"])
            portal.observe_machine_jobs()
        events_ = [event for event in self.bus.since(0)["events"] if event["kind"] == "machine_jobs"]
        self.assertEqual([e["title"] for e in events_], ["Build running on lab", "Build cancelled on lab"])

    def test_watchdog_observes_local_jobs_even_without_remote_work(self):
        portal.observe_machine_jobs()
        self.rows["local"] = self.row("passed", kind="flash")
        stop = Mock()
        stop.wait.side_effect = [False, True]
        with patch.object(portal, "vd", SimpleNamespace(watchdog=Mock())) as owner:
            portal.run_vm_watchdog(stop)
        owner.watchdog.assert_called_once()
        self.assertEqual(self.titles(), ["Flash verified \u00b7 lab"])


if __name__ == "__main__":
    unittest.main()
