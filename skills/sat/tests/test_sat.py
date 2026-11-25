import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[3]
SAT = ROOT / "bin" / "sat"
MCTL = ROOT / "bin" / "mctl"
PILOT = ROOT / "skills" / "machine-pilot" / "scripts" / "pilot.py"
TOKEN = "sat_pat_test-only"


class SatHandler(BaseHTTPRequestHandler):
    seen_auth = []
    seen_paths = []
    cross_origin = False
    details_mode = False
    cards_failure = False
    bad_firmware = False
    details_status = 0

    def log_message(self, fmt, *args):
        pass

    def respond(self, status, payload, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).seen_auth.append(self.headers.get("Authorization"))
        parsed = urlsplit(self.path)
        type(self).seen_paths.append(parsed.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/api/v1/me":
            self.respond(200, {"user": "test"})
        elif parsed.path == "/api/v1/unauthorized":
            self.respond(401, {"error": "not signed in"}, {"X-Request-Id": "req-test"})
        elif parsed.path == "/api/v1/redirect":
            self.send_response(302)
            self.send_header("Location", "https://other.invalid/api/v1/me")
            self.end_headers()
        elif parsed.path == "/api/v1/devices":
            if type(self).cross_origin:
                self.respond(200, [{"name": "first"}], {
                    "Link": '<https://other.invalid/api/v1/devices?cursor=2>; rel="next"'
                })
            elif query.get("cursor") == ["two"]:
                self.respond(200, [{"name": "second"}])
            else:
                next_url = "http://%s/api/v1/devices?cursor=two" % self.headers.get("Host")
                self.respond(200, [{"name": "first"}], {"Link": '<%s>; rel="next"' % next_url})
        elif parsed.path == "/api/v1/devices/sat-lab1/cards":
            if type(self).cards_failure:
                self.respond(403, {"error": "denied " + TOKEN})
            else:
                self.respond(200, [{"name": "card-a", "serial": "serial-a"}])
        elif parsed.path == "/api/v1/component-update/firmware":
            if type(self).bad_firmware:
                self.respond(200, {"unexpected": True})
            else:
                self.respond(200, {"setups": {
                    "sat-lab1": {"oam_a1": {"versions": [
                        {"version": "0.5.23", "build": "test", "parts": ["card-a"]}],
                        "not_available": ["card-b"]}},
                    "other-setup": {"private": "not for this card"},
                }})
        elif parsed.path.startswith("/api/v1/devices/"):
            name = parsed.path.rsplit("/", 1)[-1]
            if type(self).details_status:
                self.respond(type(self).details_status, {"error": "try later"}, {"Retry-After": "30"})
            elif type(self).details_mode:
                self.respond(200, {"name": name, "state": "alloc-in-use", "cards_count": 1,
                                   "current": {"assignee": ["owner@example.test"], "end": 2000000000},
                                   "unrelated": "not for this card"})
            else:
                self.respond(200, {"name": name})
        else:
            self.respond(404, {"error": "not found"})


class SatClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SatHandler.seen_auth = []
        SatHandler.seen_paths = []
        SatHandler.cross_origin = False
        SatHandler.details_mode = False
        SatHandler.cards_failure = False
        SatHandler.bad_firmware = False
        SatHandler.details_status = 0
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SatHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = dict(os.environ)
        self.env["HOME"] = str(self.home)
        self.env["SAT_URL"] = self.url
        self.env["SAT_TOKEN"] = TOKEN
        self.env["COPILOT_PRIVATE_DIR"] = str(self.home / ".private")
        self.env.pop("SAT_TOKEN_FILE", None)
        self.env.pop("SAT_CA_FILE", None)
        SatHandler.seen_auth = []
        SatHandler.seen_paths = []
        SatHandler.cross_origin = False
        SatHandler.details_mode = False
        SatHandler.cards_failure = False
        SatHandler.bad_firmware = False
        SatHandler.details_status = 0

    def tearDown(self):
        self.temp.cleanup()

    def run_sat(self, *args, command=SAT, token=True):
        env = dict(self.env)
        if not token:
            env.pop("SAT_TOKEN", None)
        argv = ([sys.executable, str(command), *args] if command == PILOT
                else [str(command), *args])
        return subprocess.run(argv, env=env, text=True, capture_output=True, timeout=10)

    def test_reads_authenticated_json(self):
        result = self.run_sat("me")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"user": "test"})
        self.assertEqual(SatHandler.seen_auth, ["Bearer " + TOKEN])
        self.assertNotIn(TOKEN, result.stdout)

    def test_follows_same_origin_pagination(self):
        result = self.run_sat("devices", "--limit", "1", "--all-pages")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [{"name": "first"}, {"name": "second"}])
        self.assertEqual(len(SatHandler.seen_auth), 2)

    def test_refuses_cross_origin_pagination(self):
        SatHandler.cross_origin = True
        result = self.run_sat("devices", "--all-pages")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different origin", result.stderr)
        self.assertEqual(len(SatHandler.seen_auth), 1)

    def test_reports_http_error_and_request_id_without_token(self):
        result = self.run_sat("get", "/api/v1/unauthorized")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HTTP 401", result.stderr)
        self.assertIn("req-test", result.stderr)
        self.assertNotIn(TOKEN, result.stderr)

    def test_does_not_follow_redirects_with_credentials(self):
        result = self.run_sat("get", "/api/v1/redirect")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HTTP 302", result.stderr)
        self.assertEqual(len(SatHandler.seen_auth), 1)

    def test_token_file_requires_private_permissions(self):
        token_path = self.home / "sat-token"
        token_path.write_text(TOKEN)
        token_path.chmod(0o600)
        self.env.pop("SAT_TOKEN")
        self.env["SAT_TOKEN_FILE"] = str(token_path)
        result = subprocess.run([str(SAT), "me"], env=self.env, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(SatHandler.seen_auth, ["Bearer " + TOKEN])

        token_path.chmod(0o644)
        result = subprocess.run([str(SAT), "me"], env=self.env, text=True,
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("permissions are too broad", result.stderr)

    def test_rejects_non_loopback_http_and_bad_api_path(self):
        self.env["SAT_URL"] = "http://sat.example.invalid"
        result = subprocess.run([str(SAT), "me"], env=self.env, text=True,
                                capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must use HTTPS", result.stderr)

        self.env["SAT_URL"] = self.url
        result = self.run_sat("get", "/api/v1/devices?state=alloc-available")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no query or fragment", result.stderr)

    def test_mctl_and_pilot_share_read_only_cli(self):
        result = self.run_sat("sat", "me", command=MCTL)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"user": "test"})

    def test_setup_details_include_only_the_selected_setup_and_all_three_reads(self):
        SatHandler.details_mode = True
        result = self.run_sat("details", "sat-lab1")
        self.assertEqual(result.returncode, 0, result.stderr)
        details = json.loads(result.stdout)
        self.assertTrue(details["complete"])
        self.assertEqual(details["device"]["data"]["current"]["assignee"], ["owner@example.test"])
        self.assertEqual(details["cards"]["data"][0]["serial"], "serial-a")
        self.assertEqual(details["firmware"]["data"]["oam_a1"]["versions"][0]["version"], "0.5.23")
        self.assertNotIn("other-setup", result.stdout)
        self.assertNotIn("unrelated", result.stdout)
        self.assertEqual(SatHandler.seen_paths, [
            "/api/v1/devices/sat-lab1",
            "/api/v1/devices/sat-lab1/cards",
            "/api/v1/component-update/firmware",
        ])

    def test_setup_details_report_partial_failure_and_redact_credentials(self):
        SatHandler.details_mode = True
        SatHandler.cards_failure = True
        SatHandler.bad_firmware = True
        result = self.run_sat("details", "sat-lab1")
        self.assertNotEqual(result.returncode, 0)
        details = json.loads(result.stdout)
        self.assertFalse(details["complete"])
        self.assertTrue(details["device"]["ok"])
        self.assertIn("HTTP 403", details["cards"]["error"])
        self.assertIn("[REDACTED]", details["cards"]["error"])
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        self.assertIn("unexpected firmware", details["firmware"]["error"])

    def test_setup_details_do_not_continue_after_auth_or_rate_limit(self):
        for status in (401, 429):
            SatHandler.seen_paths = []
            SatHandler.details_status = status
            result = self.run_sat("details", "sat-lab1")
            self.assertNotEqual(result.returncode, 0)
            details = json.loads(result.stdout)
            self.assertIn("HTTP %s" % status, details["device"]["error"])
            self.assertIn("Not requested", details["cards"]["error"])
            self.assertIn("Not requested", details["firmware"]["error"])
            self.assertEqual(SatHandler.seen_paths, ["/api/v1/devices/sat-lab1"])

    def test_pilot_details_default_to_the_target_name_and_honor_an_override(self):
        private_skill = self.home / ".private" / "skills" / "machine-pilot" / "references"
        private_skill.mkdir(parents=True)
        (private_skill / "stations.tsv").write_text("lab1\tyoni@host\tbench\t-\t?\t\t\n")
        targets = self.home / ".mctl" / "targets"
        targets.parent.mkdir(parents=True)
        targets.write_text("lab1 remote yoni SA-LAB1-CSR.lab.example -\n")
        result = self.run_sat("-t", "lab1", "sat", "setup", command=PILOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(SatHandler.seen_paths, ["/api/v1/devices/sa-lab1-csr"])
        targets.write_text("lab1 remote yoni host -\n")

        result = self.run_sat("stations", "set", "lab1", "sat_setup=sat-lab1", command=PILOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        SatHandler.details_mode = True
        result = self.run_sat("-t", "lab1", "sat", "details", command=PILOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["setup"], "sat-lab1")

    def test_pilot_rejects_a_mapping_for_a_different_mctl_host(self):
        private_skill = self.home / ".private" / "skills" / "machine-pilot" / "references"
        private_skill.mkdir(parents=True)
        stations = private_skill / "stations.tsv"
        stations.write_text("lab1\tyoni@old-host\tbench\t-\t?\t\t\tsat-lab1\n")
        targets = self.home / ".mctl" / "targets"
        targets.parent.mkdir(parents=True)
        targets.write_text("lab1 remote yoni current-host -\n")
        result = self.run_sat("-t", "lab1", "sat", "details", command=PILOT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match its mctl host", result.stderr)
        self.assertEqual(SatHandler.seen_paths, [])

        stations.write_text("lab1\tyoni@CURRENT-HOST\tbench\t-\t?\t\t\tsat-lab1\n")
        SatHandler.details_mode = True
        result = self.run_sat("-t", "lab1", "sat", "details", command=PILOT)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_mapping_inherits_the_registered_mctl_host(self):
        targets = self.home / ".mctl" / "targets"
        targets.parent.mkdir(parents=True)
        targets.write_text("lab1 remote yoni host.example -\n")
        result = self.run_sat("stations", "set", "lab1", "sat_setup=sat-lab1", command=PILOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("host=yoni@host.example", result.stdout)
        SatHandler.details_mode = True
        result = self.run_sat("-t", "lab1", "sat", "details", command=PILOT)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pilot_maps_private_station_to_sat_setup(self):
        private_skill = self.home / ".private" / "skills" / "machine-pilot" / "references"
        private_skill.mkdir(parents=True)
        stations = private_skill / "stations.tsv"
        stations.write_text("lab1\tyoni@host\tbench\t-\t?\t\t\n")

        result = self.run_sat("stations", "set", "lab1", "sat_setup=sat-lab1", command=PILOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sat_setup=sat-lab1", result.stdout)
        self.assertTrue(stations.read_text().rstrip().endswith("\tsat-lab1"))

        result = self.run_sat("-t", "lab1", "sat", "setup", command=PILOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"name": "sat-lab1"})
        self.assertEqual(SatHandler.seen_paths, ["/api/v1/devices/sat-lab1"])

        result = self.run_sat("stations", "set", "lab1", "sat_setup=bad;name", command=PILOT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be a SAT device name", result.stderr)

        result = self.run_sat("sat", "me", command=PILOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"user": "test"})


class SatPinnedCertificateTests(unittest.TestCase):
    """A self-signed SAT certificate is trusted only when explicitly pinned."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cert = Path(cls.temp.name) / "cert.pem"
        key = Path(cls.temp.name) / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "1",
                        "-subj", "/CN=localhost"], check=True, capture_output=True)
        cls.cert = cert
        SatHandler.seen_auth = []
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), SatHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "https://localhost:%d" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
        cls.temp.cleanup()

    def run_me(self, **extra):
        home = Path(self.temp.name) / "home"
        home.mkdir(exist_ok=True)
        env = dict(os.environ, HOME=str(home), SAT_URL=self.url, SAT_TOKEN=TOKEN, **extra)
        env.pop("SAT_TOKEN_FILE", None)
        if "SAT_CA_FILE" not in extra:
            env.pop("SAT_CA_FILE", None)
        return subprocess.run([str(SAT), "me"], env=env, text=True,
                              capture_output=True, timeout=10)

    def test_rejects_unpinned_self_signed_certificate(self):
        SatHandler.seen_auth = []
        result = self.run_me()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CERTIFICATE_VERIFY_FAILED", result.stderr)
        self.assertEqual(SatHandler.seen_auth, [])

    def test_trusts_only_explicitly_pinned_certificate(self):
        result = self.run_me(SAT_CA_FILE=str(self.cert))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"user": "test"})

    def test_missing_explicit_certificate_file_fails_closed(self):
        SatHandler.seen_auth = []
        result = self.run_me(SAT_CA_FILE=str(Path(self.temp.name) / "missing.pem"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SAT_CA_FILE not found", result.stderr)
        self.assertEqual(SatHandler.seen_auth, [])


if __name__ == "__main__":
    unittest.main()
