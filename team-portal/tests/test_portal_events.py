import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
