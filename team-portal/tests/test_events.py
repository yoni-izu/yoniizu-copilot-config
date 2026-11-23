import base64
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import events  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class BusTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {"enabled": True, "queue": True, "machine_jobs": True, "ai": True,
                    "reachability": True, "windows_toast": True}
        self.toasts = []
        self.clock = Clock()
        self.bus = events.Bus(lambda: self.cfg, lambda t, b: self.toasts.append((t, b)), self.clock)

    def wait_toasts(self, n):
        for _ in range(50):
            if len(self.toasts) >= n:
                return
            time.sleep(0.01)

    def test_publish_and_since(self):
        self.bus.since(0)
        a = self.bus.publish("queue", "A done", "body", "ok", "#x")
        b = self.bus.publish("ai", "B")
        feed = self.bus.since(0)
        self.assertEqual(feed["seq"], 2)
        self.assertEqual([e["title"] for e in feed["events"]], ["A done", "B"])
        self.assertEqual(self.bus.since(a["seq"])["events"], [b])
        self.assertEqual(self.bus.since(2)["events"], [])

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.bus.publish("bogus", "x")

    def test_settings_filter(self):
        self.cfg["machine_jobs"] = False
        self.assertIsNone(self.bus.publish("machine_jobs", "x"))
        self.assertIsNotNone(self.bus.publish("queue", "x"))
        self.cfg["enabled"] = False
        self.assertIsNone(self.bus.publish("queue", "y"))
        self.assertEqual(self.bus.since(0)["seq"], 1)

    def test_toast_only_without_open_tab(self):
        self.bus.since(0)                      # tab heartbeat
        self.bus.publish("queue", "seen in tab")
        self.clock.now += events.PRESENCE_GRACE + 1
        self.assertFalse(self.bus.tab_open())
        self.bus.publish("queue", "nobody home", "b")
        self.wait_toasts(1)
        self.assertEqual(self.toasts, [("nobody home", "b")])

    def test_toast_disabled(self):
        self.cfg["windows_toast"] = False
        self.bus.publish("queue", "x")
        time.sleep(0.05)
        self.assertEqual(self.toasts, [])

    def test_ring_buffer_bounded(self):
        for i in range(events.KEEP + 20):
            self.bus.publish("queue", f"e{i}")
        feed = self.bus.since(0)
        self.assertEqual(len(feed["events"]), events.KEEP)
        self.assertEqual(feed["events"][-1]["title"], f"e{events.KEEP + 19}")

    def test_resize_keeps_newest_and_settings_signal_is_silent(self):
        self.assertEqual(self.bus.publish_settings()["kind"], "settings")
        self.wait_toasts(1)
        self.assertEqual(self.toasts, [])
        self.cfg["windows_toast"] = False
        for i in range(10):
            self.bus.publish("queue", f"e{i}")
        self.bus.resize(3)
        self.assertEqual([e["title"] for e in self.bus.since(0)["events"]], ["e7", "e8", "e9"])

    def test_domain_events_are_batched_and_never_toasted(self):
        event = self.bus.publish_domains(["team", "tasks", "team"])
        self.assertEqual(event["domains"], ["tasks", "team"])
        self.assertEqual(event["kind"], "domain")
        self.wait_toasts(1)
        self.assertEqual(self.toasts, [])

    def test_stream_replays_events_and_resets_after_gap_or_restart(self):
        cursor = self.bus.cursor()
        self.bus.publish("queue", "one")
        replay = self.bus.wait_after(cursor["instance"], cursor["seq"], 0)
        self.assertFalse(replay["reset"])
        self.assertEqual([event["title"] for event in replay["events"]], ["one"])

        for i in range(events.KEEP):
            self.bus.publish("queue", str(i))
        gap = self.bus.wait_after(cursor["instance"], cursor["seq"], 0)
        self.assertTrue(gap["reset"])

        restarted = events.Bus(lambda: self.cfg, lambda t, b: None, self.clock)
        reset = restarted.wait_after(cursor["instance"], cursor["seq"], 0)
        self.assertTrue(reset["reset"])

    def test_toast_script_never_embeds_raw_text(self):
        script = events.toast_script("T'; Remove-Item x", "<b>&'\"")
        self.assertNotIn("Remove-Item", script)
        data = script.split("FromBase64String('")[1].split("')")[0]
        xml = base64.b64decode(data).decode()
        self.assertIn("&lt;b&gt;&amp;", xml)
        self.assertIn("T'; Remove-Item x", xml)


if __name__ == "__main__":
    unittest.main()
