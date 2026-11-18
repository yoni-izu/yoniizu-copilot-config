"""Flash FW endpoints: validation, argv, status folding and HTTP routes (vd faked)."""
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import portal  # noqa: E402
from test_portal_cancel import handler  # noqa: E402

IMG = {"directory": "/s/iridium/topic/arm/asic", "valid": True, "project": "iridium",
       "deployment": "topic", "platform": "arm", "variant": "asic", "image": "img.bin",
       "path": "/s/iridium/topic/arm/asic/img.bin", "size": 1024, "sha256": "ab" * 32}
BAD = dict(IMG, directory="/s/bad", valid=False, error="sha mismatch")


def fake_vd(**over):
    ns = SimpleNamespace(
        targets=lambda: ["srv22", "vm"], vm_targets=lambda: {"vm": "i-1"},
        agent_env=lambda: ({"PATH": "/bin"}, ""),
        start_local_job=Mock(return_value={"job": {"id": "flash-srv22-x", "target": "srv22"}}),
        LOCAL_UNSTOPPABLE={"write", "verify", "boot"})
    ns.__dict__.update(over)
    return ns


class FlashStartTests(unittest.TestCase):
    def setUp(self):
        self.vd = fake_vd()
        for name, val in (("vd", self.vd),
                          ("flash_images", lambda: {"images": [IMG, BAD], "root": "/s"}),
                          ("EVENTS", Mock()), ("_flash_watch", lambda job: None)):
            p = patch.object(portal, name, val)
            p.start()
            self.addCleanup(p.stop)

    def start(self, **kw):
        req = {"target": "srv22", "method": "bmc", "image": IMG["directory"], "oams": [3, 1]}
        req.update(kw)
        return portal.flash_start(req)

    def test_builds_argv_from_store_listing(self):
        res = self.start(reset_verify=True)
        self.assertEqual(res, {"job": "flash-srv22-x", "target": "srv22"})
        (target, kind, argv), kw = self.vd.start_local_job.call_args
        self.assertEqual((target, kind), ("srv22", "flash"))
        self.assertIn(str(portal.FW_FLASH), argv)
        self.assertEqual(argv[argv.index("--image") + 1], IMG["path"])
        self.assertEqual(argv[argv.index("--sha256") + 1], IMG["sha256"])
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "--oam"], ["1", "3"])
        self.assertIn("{events}", argv)
        self.assertIn("--reset-verify", argv)
        self.assertNotIn("--force", argv)
        self.assertEqual(kw["meta"]["oams"], [1, 3])
        self.assertEqual(kw["meta"]["method"], "bmc")
        portal.EVENTS.publish.assert_called_once()

    def test_rejects_bad_requests(self):
        cases = [dict(target="nope"), dict(target="vm"), dict(method="jtag"),
                 dict(oams=[]), dict(oams=["1"]), dict(oams=[True]), dict(oams=[99]),
                 dict(image="/etc"), dict(image=BAD["directory"]), dict(force="yes"),
                 dict(method="spi-bmc", oams=[0, 1]), dict(path="/x")]
        for c in cases:
            with self.subTest(c), self.assertRaises(ValueError):
                self.start(**c)
        self.vd.start_local_job.assert_not_called()

    def test_running_job_conflict(self):
        self.vd.start_local_job.return_value = {"error": "already running", "job_id": "j"}
        with self.assertRaises(FileExistsError):
            self.start()


class FlashStatusTests(unittest.TestCase):
    def test_folds_events(self):
        job = {"id": "flash-srv22-x", "runner": "local", "kind": "flash", "target": "srv22",
               "oams": [1, 3], "method": "bmc", "unit": "mctl-flash-srv22-x"}
        events = [{"type": "phase", "phase": "upload", "t": 1},
                  {"type": "phase", "phase": "write", "t": 2},
                  {"type": "oam", "oam": 1, "state": "writing", "pct": 40},
                  {"type": "oam", "oam": 3, "state": "queued"},
                  {"type": "image", "sha256": "ab", "size": 3, "expected": "1.2", "path": "/p"}]
        vd = fake_vd(_job_by_id=lambda i: job if i == job["id"] else None,
                     is_local=lambda j: bool(j) and j.get("runner") == "local",
                     local_status=lambda j: {"status": "running", "rc": None, "finished": ""},
                     local_events=lambda j: list(events),
                     _read_local=lambda j, n: {"text": "a\nb\n"})
        with patch.object(portal, "vd", vd):
            d = portal.flash_status(job["id"])
            self.assertEqual(d["phase"], "write")
            self.assertFalse(d["cancellable"])
            self.assertEqual([(o["oam"], o["state"], o["pct"]) for o in d["oams"]],
                             [(1, "writing", 40), (3, "queued", None)])
            events.append({"type": "result", "ok": False,
                           "oams": {"1": {"state": "verified", "after": "1.2"},
                                    "3": {"state": "unverified", "reason": "build unchanged"}}})
            d = portal.flash_status(job["id"])
            self.assertEqual(d["oams"][0]["after"], "1.2")
            self.assertEqual(d["oams"][1]["message"], "build unchanged")
            self.assertEqual(d["result"]["ok"], False)
            with self.assertRaises(LookupError):
                portal.flash_status("other")


class FlashHttpTests(unittest.TestCase):
    def test_cross_origin_rejected(self):
        h = handler("/api/flash/start", {"target": "srv22"},
                    {"Origin": "http://evil", "Host": "127.0.0.1:8787"})
        with patch.object(portal, "vd", fake_vd()):
            code, body = h.do_POST()
        self.assertEqual(code, 403)

    def test_discover_payload_and_errors(self):
        with patch.object(portal, "vd", fake_vd()):
            code, _ = handler("/api/flash/discover", {"target": "srv22", "x": 1}).do_POST()
            self.assertEqual(code, 400)
            failed = subprocess.CompletedProcess([], 1, "", "boom: bmc unreachable\n")
            with patch.object(portal.subprocess, "run", return_value=failed):
                code, body = handler("/api/flash/discover", {"target": "srv22"}).do_POST()
            self.assertEqual((code, body["error"]), (502, "boom: bmc unreachable"))
            ok = subprocess.CompletedProcess([], 0, json.dumps({"methods": {}, "default": ""}), "")
            with patch.object(portal.subprocess, "run", return_value=ok) as run:
                code, body = handler("/api/flash/discover", {"target": "srv22"}).do_POST()
            self.assertEqual(code, 200)
            self.assertEqual(run.call_args[0][0][-4:], ["discover", "--target", "srv22", "--json"])

    def test_start_conflict_is_409(self):
        with patch.object(portal, "flash_start", side_effect=FileExistsError("busy")), \
                patch.object(portal, "vd", fake_vd()):
            code, body = handler("/api/flash/start", {"target": "srv22"}).do_POST()
        self.assertEqual((code, body["error"]), (409, "busy"))

    def test_page_and_status_routes(self):
        h = handler("/flash?id=x", {})
        h._send = lambda code, text, *a: (code, text)
        code, html = h.do_GET()
        self.assertEqual(code, 200)
        self.assertIn("/assets/flash.js", html)
        h = handler("/api/flash/status?id=x", {})
        with patch.object(portal, "flash_status", side_effect=LookupError("unknown flash job")):
            code, body = h.do_GET()
        self.assertEqual(code, 404)
        self.assertIn("flash.js", portal.ASSET_NAMES)


if __name__ == "__main__":
    unittest.main()
