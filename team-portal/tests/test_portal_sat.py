"""SAT machine-card adapter tests; all owner and network operations are mocked."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import portal  # noqa: E402
from test_portal_cancel import handler  # noqa: E402


SNAPSHOT = {
    "setup": "sat-lab1",
    "requested_at": "2026-01-02T03:04:05+00:00",
    "complete": False,
    "device": {"ok": True, "data": {"name": "sat-lab1", "current": None}},
    "cards": {"ok": False, "error": "SAT API HTTP 403"},
    "firmware": {"ok": True, "data": None},
}


class SatPortalTests(unittest.TestCase):
    def setUp(self):
        self.rows = {"lab1": {"kind": "remote"}, "vm": {"kind": "vm"}}
        self.patches = [
            patch.object(portal.vd, "target_rows", return_value=self.rows),
            patch.object(portal.terminal, "PILOT", Path(__file__)),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_uses_pilot_and_preserves_partial_failures(self):
        result = SimpleNamespace(returncode=1, stdout=json.dumps(SNAPSHOT), stderr="")
        with patch.object(portal.subprocess, "run", return_value=result) as run:
            actual = portal.sat_machine_payload("lab1")
        self.assertEqual(actual["target"], "lab1")
        self.assertEqual(actual["cards"]["error"], "SAT API HTTP 403")
        self.assertTrue(actual["device"]["ok"])
        self.assertEqual(run.call_args.args[0][1:], ["-t", "lab1", "sat", "details"])
        self.assertEqual(run.call_args.kwargs["timeout"], 50)

    def test_missing_mapping_and_timeout_remain_explicit(self):
        result = SimpleNamespace(returncode=2, stdout="", stderr="pilot: no SAT setup mapped")
        with patch.object(portal.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "no SAT setup mapped"):
                portal.sat_machine_payload("lab1")
        with patch.object(portal.subprocess, "run", side_effect=subprocess.TimeoutExpired("pilot", 50)):
            with self.assertRaisesRegex(RuntimeError, "timed out.*ownership is unverified"):
                portal.sat_machine_payload("lab1")
        with patch.object(portal.vd, "target_rows", side_effect=OSError("unreadable")):
            with self.assertRaisesRegex(RuntimeError, "target registry is unavailable"):
                portal.sat_machine_payload("lab1")

    def test_rejects_non_lab_targets_and_invalid_owner_responses(self):
        for name in ("", "missing", "vm"):
            with self.assertRaisesRegex(ValueError, "registered lab-machine target"):
                portal.sat_machine_payload(name)
        for text in ("not json", json.dumps({"setup": "lab1"})):
            result = SimpleNamespace(returncode=0, stdout=text, stderr="")
            with patch.object(portal.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "invalid SAT"):
                    portal.sat_machine_payload("lab1")

    def test_http_endpoint_reports_data_and_errors(self):
        request = handler("/api/vm-sat?target=lab1", {})
        with patch.object(portal, "sat_machine_payload", return_value={"target": "lab1"}) as read:
            self.assertEqual(request.do_GET(), (200, {"target": "lab1"}))
            read.assert_called_once_with("lab1")
        with patch.object(portal, "sat_machine_payload", side_effect=ValueError("not a lab target")):
            self.assertEqual(request.do_GET(), (400, {"error": "not a lab target"}))
        with patch.object(portal, "sat_machine_payload", side_effect=RuntimeError("SAT not configured")):
            self.assertEqual(request.do_GET(), (503, {"error": "SAT not configured"}))

    def test_slow_sat_read_does_not_block_job_payload(self):
        started, release = threading.Event(), threading.Event()

        def delayed(target):
            started.set()
            release.wait(3)
            return {"target": target}

        sat_request = handler("/api/vm-sat?target=lab1", {})
        jobs_request = handler("/api/vm-jobs", {})
        with patch.object(portal, "sat_machine_payload", side_effect=delayed), \
                patch.object(portal, "vmlog_payload", return_value={"jobs": []}):
            worker = threading.Thread(target=sat_request.do_GET)
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                self.assertEqual(jobs_request.do_GET(), (200, {"jobs": []}))
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_portal_to_pilot_to_local_sat_server(self):
        class FakeSat(BaseHTTPRequestHandler):
            paths = []

            def log_message(self, fmt, *args):
                pass

            def do_GET(self):
                self.paths.append(self.path)
                if self.headers.get("Authorization") != "Bearer sat_pat_fixture":
                    self.send_error(401)
                    return
                data = {
                    "/api/v1/devices/setup-a": {
                        "name": "setup-a", "state": "alloc-in-use", "cards_count": 1,
                        "current": {"assignee": ["owner@example.test"], "end": 2000000000,
                                    "description": "not needed"}},
                    "/api/v1/devices/setup-a/cards": [{"name": "card-a", "serial": "serial-a"}],
                    "/api/v1/component-update/firmware": {"setups": {
                        "setup-a": {"oam_a1": {"versions": [{"version": "0.5.23"}]}},
                        "other": {"version": "must not be returned"}}},
                }.get(self.path)
                if data is None:
                    self.send_error(404)
                    return
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeSat)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                targets = root / ".mctl" / "targets"
                targets.parent.mkdir(parents=True)
                targets.write_text("lab1 remote test host.example -\n")
                stations = root / "private" / "skills" / "machine-pilot" / "references" / "stations.tsv"
                stations.parent.mkdir(parents=True)
                stations.write_text("lab1\ttest@host.example\tbench\t-\t?\t\t\tsetup-a\n")
                env = {"HOME": temp, "COPILOT_PRIVATE_DIR": str(root / "private"),
                       "SAT_URL": f"http://127.0.0.1:{server.server_port}",
                       "SAT_TOKEN": "sat_pat_fixture"}
                pilot = Path(__file__).resolve().parents[2] / "skills/machine-pilot/scripts/pilot.py"
                with patch.dict(os.environ, env), patch.object(portal.terminal, "PILOT", pilot):
                    result = portal.sat_machine_payload("lab1")
                self.assertTrue(result["complete"])
                self.assertEqual(result["device"]["data"]["current"]["assignee"], ["owner@example.test"])
                self.assertNotIn("description", result["device"]["data"]["current"])
                self.assertEqual(result["cards"]["data"][0]["serial"], "serial-a")
                self.assertEqual(result["firmware"]["data"]["oam_a1"]["versions"][0]["version"], "0.5.23")
                self.assertNotIn("other", json.dumps(result))
                self.assertEqual(FakeSat.paths, ["/api/v1/devices/setup-a",
                                                 "/api/v1/devices/setup-a/cards",
                                                 "/api/v1/component-update/firmware"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)


if __name__ == "__main__":
    unittest.main()
