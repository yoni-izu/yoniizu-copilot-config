import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live_status  # noqa: E402


def wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


class LiveStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.blocks, self.reach = [], []
        self.saved = live_status.BACKOFF
        live_status.BACKOFF = (0.05, 0.05)

    def tearDown(self):
        live_status.BACKOFF = self.saved
        self.tmp.cleanup()

    def script(self, body):
        path = self.dir / "fake-mctl"
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return lambda t, i: [str(path), t, str(i)]

    def link(self, argv):
        return live_status.Link("vm", 1, lambda t, x: self.blocks.append((t, x)),
                                lambda t, up, why: self.reach.append((t, up, why)),
                                lambda: dict(os.environ), argv)

    def test_blocks_parsed_and_state_connected(self):
        argv = self.script('for i in 1 2; do printf "J\\tbuild\\t%s \\xe2\\n" $i;'
                           ' echo "__MCTL_PROBE_END__ 1"; done; sleep 5\n')
        link = self.link(argv)
        link.start()
        try:
            self.assertTrue(wait(lambda: len(self.blocks) == 2))
            self.assertEqual(link.snapshot()["state"], "connected")
            self.assertEqual(self.blocks[0][0], "vm")
            self.assertIn("build\t1", self.blocks[0][1])
            self.assertIn("\ufffd", self.blocks[0][1])     # invalid utf-8 replaced
            self.assertNotIn("__MCTL", self.blocks[1][1])
        finally:
            link.stop()
        self.assertEqual(link.snapshot()["state"], "stopped")

    def test_drop_reports_transition_and_reconnects(self):
        marker = self.dir / "n"
        argv = self.script(f'n=$(cat {marker} 2>/dev/null || echo 0); echo $((n+1)) > {marker}\n'
                           'if [ "$n" = 1 ]; then echo "ssh: connect timed out" >&2; exit 255; fi\n'
                           'echo "__MCTL_PROBE_END__ 1"; [ "$n" = 0 ] && exit 0; sleep 5\n')
        link = self.link(argv)
        link.start()
        try:
            self.assertTrue(wait(lambda: len(self.reach) >= 2))
            self.assertEqual(self.reach[0][:2], ("vm", False))
            self.assertEqual(self.reach[1][:2], ("vm", True))
            self.assertTrue(wait(lambda: link.snapshot()["state"] == "connected"))
        finally:
            link.stop()

    def test_unreachable_from_start_is_quiet(self):
        argv = self.script('echo "ssh: no route" >&2; exit 255\n')
        link = self.link(argv)
        link.start()
        try:
            self.assertTrue(wait(lambda: link.snapshot()["state"] == "down"))
            self.assertIn("no route", link.snapshot()["error"])
            time.sleep(0.2)
            self.assertEqual(self.reach, [])
        finally:
            link.stop()

    def test_manager_follows_settings(self):
        cfg = {"live_targets": ["vm"], "live_interval_s": 15}
        argv = self.script('echo "__MCTL_PROBE_END__ 1"; sleep 5\n')
        mgr = live_status.Manager(lambda t, x: self.blocks.append(t), lambda *a: None,
                                  lambda: dict(os.environ), lambda: cfg, argv)
        mgr.sync()
        try:
            self.assertTrue(wait(lambda: mgr.snapshot().get("vm", {}).get("state") == "connected"))
            cfg["live_targets"] = []
            mgr.sync()
            self.assertEqual(mgr.snapshot(), {})
            cfg["live_targets"] = ["vm"]
            cfg["live_interval_s"] = 30
            mgr.sync()
            self.assertEqual(mgr.snapshot()["vm"]["interval"], 30)
        finally:
            mgr.stop()


if __name__ == "__main__":
    unittest.main()
