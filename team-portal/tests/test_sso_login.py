"""Remote SSO login: link/code parsing, process lifecycle, HTTP endpoint."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sso_login  # noqa: E402

DEVICE = "https://elmnt.awsapps.com/start/#/device?user_code=ABCD-EFGH"


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class ParseTests(unittest.TestCase):
    def test_safe_url_allows_only_aws_sso_https(self):
        self.assertEqual(sso_login.safe_url(DEVICE), DEVICE)
        self.assertTrue(sso_login.safe_url("https://device.sso.eu-central-1.amazonaws.com/"))
        for bad in ("http://elmnt.awsapps.com/start", "https://elmnt.awsapps.com.evil.com/",
                    "https://evil.com/?q=x.awsapps.com", "https://u:p@elmnt.awsapps.com/",
                    "https://elmnt.awsapps.com:8443/", "https://oidc.eu-central-1.amazonaws.com/x",
                    "javascript:alert(1)"):
            self.assertIsNone(sso_login.safe_url(bad), bad)

    def test_parse_finds_link_and_code_through_ansi_noise(self):
        out = ("\x1b[32mINF\x1b[0m Attempting to open your browser\r\n"
               "If it does not open, open the following URL:\r\n\r\n"
               "https://elmnt.awsapps.com/start/#/device\r\n\r\n"
               "Then enter the code:\r\n\r\nWXYZ-1234\r\n")
        self.assertEqual(sso_login.parse(out),
                         ("https://elmnt.awsapps.com/start/#/device", "WXYZ-1234"))
        self.assertEqual(sso_login.parse(f"open {DEVICE}."), (DEVICE, "ABCD-EFGH"))
        self.assertEqual(sso_login.parse("go to https://evil.com/ code ABCD-EFGH"),
                         (None, "ABCD-EFGH"))

    def test_redact_hides_link_and_code(self):
        self.assertEqual(sso_login._redact(f"bad {DEVICE} ABCD-EFGH"), "bad [link] [code]")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def script(self, body):
        path = Path(self.tmp.name) / "fake.sh"
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(0o755)
        return lambda target: [str(path), target]

    def test_success_exposes_link_then_hides_it(self):
        logins = sso_login.SsoLogins(self.script(
            f'echo "[$MCTL_AUTOSTART] $1"; echo "Open {DEVICE}"; sleep 0.3; exit 0'))
        self.assertEqual(logins.start("vm", ["vm"])["state"], "starting")
        self.assertTrue(wait_for(lambda: logins.status()["vm"]["state"] == "waiting"))
        st = logins.status()["vm"]
        self.assertEqual((st["url"], st["code"]), (DEVICE, "ABCD-EFGH"))
        self.assertTrue(wait_for(lambda: logins.status()["vm"]["state"] == "done"))
        st = logins.status()["vm"]
        self.assertEqual(st["message"], "Logged in")
        self.assertIsNone(st["url"])
        self.assertIsNone(st["code"])

    def test_autostart_disabled_for_mctl(self):
        seen = Path(self.tmp.name) / "env"
        logins = sso_login.SsoLogins(self.script(f'echo "$MCTL_AUTOSTART" > {seen}'))
        logins.start("vm", ["vm"])
        self.assertTrue(wait_for(lambda: logins.status()["vm"]["state"] != "starting"))
        self.assertEqual(seen.read_text().strip(), "0")

    def test_failure_message_is_redacted(self):
        logins = sso_login.SsoLogins(self.script(
            f'echo "token {DEVICE} ABCD-EFGH rejected"; exit 3'))
        logins.start("vm", ["vm"])
        self.assertTrue(wait_for(lambda: logins.status()["vm"]["state"] == "failed"))
        msg = logins.status()["vm"]["message"]
        self.assertIn("exited 3", msg)
        self.assertNotIn("ABCD-EFGH", msg)
        self.assertNotIn("awsapps", msg)

    def test_unknown_target_rejected_and_duplicate_start_reuses(self):
        logins = sso_login.SsoLogins(self.script("sleep 5"))
        with self.assertRaises(sso_login.SsoError):
            logins.start("nope", ["vm"])
        first = logins.start("vm", ["vm"])
        self.assertEqual(logins.start("vm", ["vm"])["started"], first["started"])
        self.assertEqual(logins.cancel("vm")["state"], "cancelled")
        with self.assertRaises(sso_login.SsoError):
            logins.cancel("other")

    def test_cancel_kills_the_process(self):
        logins = sso_login.SsoLogins(self.script(f'echo "{DEVICE}"; sleep 30'))
        logins.start("vm", ["vm"])
        self.assertTrue(wait_for(lambda: logins.status()["vm"]["state"] == "waiting"))
        proc = logins._runs["vm"]["proc"]
        logins.cancel("vm")
        self.assertTrue(wait_for(lambda: proc.poll() is not None))
        self.assertEqual(logins.status()["vm"]["state"], "cancelled")

    def test_timeout_kills_and_fails(self):
        clock = [1000.0]
        logins = sso_login.SsoLogins(self.script("sleep 30"), now=lambda: clock[0])
        logins.start("vm", ["vm"])
        proc = logins._runs["vm"]["proc"]
        clock[0] += sso_login.TIMEOUT_S + 1
        self.assertTrue(wait_for(lambda: logins.status()["vm"]["state"] == "failed"))
        self.assertIn("Timed out", logins.status()["vm"]["message"])
        self.assertTrue(wait_for(lambda: proc.poll() is not None))

    def test_finished_entries_expire(self):
        clock = [1000.0]
        logins = sso_login.SsoLogins(self.script("exit 0"), now=lambda: clock[0])
        logins.start("vm", ["vm"])
        self.assertTrue(wait_for(lambda: logins.status()["vm"]["state"] == "done"))
        clock[0] += sso_login.KEEP_S + 1
        self.assertEqual(logins.status(), {})


class EndpointTests(unittest.TestCase):
    def setUp(self):
        import portal
        from test_portal_cancel import handler
        self.portal, self.handler = portal, handler

    def test_post_validates_and_dispatches(self):
        fake = unittest.mock.Mock()
        fake.start.return_value = {"target": "vm", "state": "starting"}
        fake.cancel.return_value = {"target": "vm", "state": "cancelled"}
        vd = unittest.mock.Mock(targets=lambda: ["vm"])
        with patch.object(self.portal, "SSO_LOGINS", fake), patch.object(self.portal, "vd", vd):
            for bad in ({"target": "vm"}, {"target": "vm", "action": "rm"},
                        {"target": 1, "action": "start"},
                        {"target": "vm", "action": "start", "cmd": "x"}):
                self.assertEqual(self.handler("/api/sso-login", bad).do_POST()[0], 400, bad)
            status, _ = self.handler("/api/sso-login", {"target": "vm", "action": "start"},
                                     {"Origin": "http://evil", "Host": "127.0.0.1:8787"}).do_POST()
            self.assertEqual(status, 403)
            self.assertEqual(self.handler("/api/sso-login", {"target": "vm", "action": "start"}).do_POST(),
                             (200, {"login": {"target": "vm", "state": "starting"}}))
            fake.start.assert_called_once_with("vm", ["vm"])
            self.assertEqual(self.handler("/api/sso-login", {"target": "vm", "action": "cancel"}).do_POST()[0], 200)
            fake.cancel.side_effect = sso_login.SsoError("no SSO login")
            self.assertEqual(self.handler("/api/sso-login", {"target": "vm", "action": "cancel"}).do_POST()[0], 400)
        with patch.object(self.portal, "vd", None):
            self.assertEqual(self.handler("/api/sso-login", {"target": "vm", "action": "start"}).do_POST()[0], 503)


if __name__ == "__main__":
    unittest.main()
