"""fw_flash.py tests against a fake mctl (BMC + dest) and fake ssh; no lab access."""
import json
import os
import signal
import ssl
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import fw_flash  # noqa: E402

BUILD = "1.763.0-17112026-114708"
OLD = "1.700.0-01102026-101010"

FAKE_MCTL = textwrap.dedent(r'''
    #!/usr/bin/env python3
    import hashlib, os, sys, time
    from pathlib import Path
    st = Path(os.environ["FAKE_STATE"])
    args = sys.argv[1:]
    if args == ["targets"]:
        print("NAME KIND USER HOST BMC\n*srv remote user host root@bmc.example")
        sys.exit(0)
    if args[2] == "dest":
        print("user@host"); sys.exit(0)
    script = args[3]
    n = int(os.environ.get("FAKE_OAMS", "2"))
    slots = [int(i) for i in os.environ["FAKE_SLOTS"].split(",")] if "FAKE_SLOTS" in os.environ else range(n)
    device_state = os.environ.get("FAKE_DEVICE_STATE", "READY")
    build = (st / "build").read_text() if (st / "build").exists() else os.environ["FAKE_OLD"]
    if "__STATUS__" in script:
        print("__STATUS__")
        for i in slots: print(f"dev{i}[eid=0 cpld=yes state={device_state}]")
        print("__VERSION__")
        for i in slots:
            for _ in range(3): print(f"dev{i} version=0.5.23 build={build} uuid=x")
        print("__OAM__")
        for i in slots: print(f"dev{i} boot=1 rdy=1")
        print("__MTD__"); print("no"); print("__FWLOADER__"); print("")
        print("__TMP__"); print("tmpfs 1000 0 1000000 0% /tmp"); sys.exit(0)
    if "cat >" in script:
        data = sys.stdin.buffer.read()
        if os.environ.get("FAKE_BADUP"): data += b"x"
        print(hashlib.sha256(data).hexdigest() + "  /tmp/img"); sys.exit(0)
    if script == "mkdir -m 700 /tmp/fw-flash-redfish.lock":
        (st / "redfish.lock").mkdir()
        sys.exit(0)
    if script == "rmdir /tmp/fw-flash-redfish.lock":
        (st / "redfish.lock").rmdir()
        sys.exit(0)
    if script == "sha256sum /tmp/a1_update.img":
        data = (st / "a1_update.img").read_bytes()
        if os.environ.get("FAKE_BADUP"): data += b"x"
        (st / "stage-hashed").touch()
        print(hashlib.sha256(data).hexdigest() + "  /tmp/a1_update.img")
        sys.exit(0)
    if script.startswith("journalctl"):
        seen = set()
        for _ in range(1200):
            if device_state == "INIT_FAILED" and not (st / "polled").exists():
                time.sleep(0.05)
                continue
            for f in sorted(st.glob("updated-*")):
                i = f.name.split("-")[1]
                if i in seen: continue
                seen.add(i)
                print(f"2026-01-01T00:00:00 bmc orchestrator[1]: [spi_flash:{i}] flash update 50%", flush=True)
                word = "failed" if os.environ.get("FAKE_JFAIL") else "done"
                print(f"2026-01-01T00:00:01 bmc orchestrator[1]: [spi_flash:{i}] flash update {word}", flush=True)
            time.sleep(0.05)
        sys.exit(0)
    if "bmccli flash update" in script:
        i = script.split("-d ")[-1].split()[0]
        if not os.environ.get("FAKE_SKIP_UPDATE"):
            (st / f"updated-{i}").write_text("1")
            (st / "build").write_text(os.environ.get("FAKE_NEWBUILD", os.environ["FAKE_NEW"]))
            (st / "updated").write_text("1")
        print("queued"); sys.exit(0)
    if "bmccli device status" in script:
        (st / "polled").touch()
        for i in range(n): print(f"dev{i}[eid=0 cpld=yes state={device_state}]")
        sys.exit(0)
    if "bmccli device reset" in script:
        (st / "reset").touch()
        sys.exit(0)
    if "bmccli flash version -f" in script:
        i = script.split("-d ")[-1].split()[0]
        copies = int(os.environ.get("FAKE_POST_COPIES", "3")) if (st / "updated").exists() else 3
        read_build = os.environ["FAKE_OLD"] if os.environ.get("FAKE_STALE_OAM") == i else build
        for _ in range(copies): print(f"dev{i} version=0.5.23 build={read_build} uuid=x")
        if (st / "updated").exists() and os.environ.get("FAKE_POST_READ_FAIL"): sys.exit(1)
        sys.exit(0)
    sys.exit(0)
''').lstrip()

FAKE_SSH = textwrap.dedent(r'''
    #!/usr/bin/env bash
    s="${@: -1}"
    case "$s" in *__FWCTL__*)
      printf '__FWCTL__\nno\n__UID__\n1000\n__SUDO__\nno\n__ELMOD__\n\n__BDF__\n0000:01:00.0 0000:02:00.0\n__FTDI__\n0\n__END__\n';;
    esac
''').lstrip()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.state = self.tmp / "state"
        self.state.mkdir()
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        (bin_ / "mctl").write_text(FAKE_MCTL)
        (bin_ / "ssh").write_text(FAKE_SSH)
        for f in bin_.iterdir():
            f.chmod(0o755)
        self.env = {"FW_FLASH_MCTL": str(bin_ / "mctl"), "FAKE_STATE": str(self.state),
                    "FAKE_OLD": OLD, "FAKE_NEW": BUILD, "FW_FLASH_POLL": "0.1",
                    "PATH": f"{bin_}:{os.environ['PATH']}"}
        self.old_env = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        fw_flash.POLL = 0.1
        self.image = self.tmp / "spi_flash_image_arm_asic.bin"
        self.image.write_bytes(b"\0" * 64 + f"manifest version: {BUILD}\0".encode() * 3 + b"\xff" * 64)

    def tearDown(self):
        for k, v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k in ("FAKE_NEWBUILD", "FAKE_JFAIL", "FAKE_BADUP"):
            os.environ.pop(k, None)

    def run_flash(self, *extra, oams=(0, 1), timeout=20):
        ev = self.tmp / "events.jsonl"
        argv = ["run", "--target", "srv", "--method", "bmc", "--image", str(self.image),
                "--events", str(ev), "--job-id", "t1", "--timeout", str(timeout), *extra]
        for o in oams:
            argv += ["--oam", str(o)]
        rc = fw_flash.main(argv)
        events = [json.loads(l) for l in ev.read_text().splitlines()] if ev.exists() else []
        return rc, events


class Parsing(unittest.TestCase):
    def test_parse_dev_kv_formats(self):
        text = ("dev0[eid=0 cpld=yes state=READY]\n"
                f"dev1 version=0.5.23 build={BUILD} uuid=a\ndev1 version=0.5.23 build={OLD} uuid=a\n"
                "dev2=0.5.23-17112026-114708\n")
        r = fw_flash.parse_dev_kv(text)
        self.assertEqual(r[0]["state"], "READY")
        self.assertEqual(r[1]["builds"], [BUILD, OLD])
        self.assertEqual(r[1]["build"], BUILD)
        self.assertEqual(r[2]["values"], ["0.5.23-17112026-114708"])

    def test_same_build_matches_date_stamp(self):
        self.assertTrue(fw_flash.same_build("0.5.23-17112026-114708", BUILD))
        self.assertFalse(fw_flash.same_build("0.5.23-17112026-114709", BUILD))
        self.assertFalse(fw_flash.same_build("", BUILD))

    def test_classify_journal(self):
        self.assertEqual(fw_flash.classify_journal("[spi_flash:3] flash update 42%"), (3, "progress", 42))
        self.assertEqual(fw_flash.classify_journal("[spi_flash:1] flash update done")[:2], (1, "done"))
        self.assertEqual(fw_flash.classify_journal("dev 2 nor write failed")[:2], (2, "fail"))
        self.assertIsNone(fw_flash.classify_journal("[thermal:1] temp 40C"))

    def test_image_version_most_frequent(self):
        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(f"{OLD} {BUILD} {BUILD}".encode())
        self.assertEqual(fw_flash.image_version(Path(fh.name)), BUILD)
        os.unlink(fh.name)


class Discover(Base):
    def test_discover_reasons(self):
        res = fw_flash.discover("srv")
        self.assertEqual(res["default"], "bmc")
        m = res["methods"]
        self.assertTrue(m["bmc"]["available"])
        self.assertEqual([o["build"] for o in m["bmc"]["oams"]], [OLD, OLD])
        self.assertIn("fw_ctl is not installed", m["pcie"]["reason"])
        self.assertIn("fw_ctl is not installed", m["spi-ftdi"]["reason"])
        self.assertIn("MTD", m["spi-bmc"]["reason"])
        self.assertNotIn("redfish", m)


class RunBmc(Base):
    def test_happy_path_verifies_every_oam(self):
        rc, ev = self.run_flash()
        self.assertEqual(rc, 0)
        res = [e for e in ev if e["type"] == "result"][-1]
        self.assertTrue(res["ok"])
        self.assertEqual({k: v["state"] for k, v in res["oams"].items()},
                         {"0": "verified", "1": "verified"})
        self.assertEqual(res["oams"]["0"]["before"], OLD)
        self.assertTrue(any(e["type"] == "log" and "[journal]" in e["line"] for e in ev))
        phases = [e["phase"] for e in ev if e["type"] == "phase"]
        self.assertEqual(phases[:5], ["check", "preflight", "upload", "write", "verify"])

    def test_init_failed_can_flash_without_force(self):
        with patch.dict(os.environ, FAKE_DEVICE_STATE="INIT_FAILED", FAKE_OLD=BUILD):
            rc, ev = self.run_flash(oams=(0,))
        self.assertEqual(rc, 0)
        self.assertTrue(any(e["type"] == "log" and "state INIT_FAILED" in e["line"] for e in ev))
        self.assertEqual(ev[-1]["oams"]["0"]["state"], "verified")

    def test_init_failed_does_not_verify_a_skipped_same_build_write(self):
        with patch.dict(os.environ, FAKE_DEVICE_STATE="INIT_FAILED", FAKE_OLD=BUILD,
                        FAKE_SKIP_UPDATE="1"):
            rc, ev = self.run_flash(oams=(0,), timeout=1)
        self.assertEqual(rc, 2)
        self.assertTrue(any(e.get("state") == "queued" for e in ev))
        self.assertEqual(ev[-1]["oams"]["0"]["state"], "unverified")

    def test_updating_state_still_blocks_flash(self):
        with patch.dict(os.environ, FAKE_DEVICE_STATE="UPDATING"):
            rc, ev = self.run_flash(oams=(0,))
        self.assertEqual(rc, 1)
        self.assertIn("UPDATING", ev[-1]["error"])
        self.assertFalse((self.state / "updated").exists())

    def test_readback_mismatch_fails(self):
        os.environ["FAKE_NEWBUILD"] = "1.111.0-01012020-000000"
        rc, ev = self.run_flash()
        self.assertEqual(rc, 2)
        res = [e for e in ev if e["type"] == "result"][-1]
        self.assertFalse(res["ok"])
        self.assertEqual(res["oams"]["0"]["state"], "failed")
        self.assertIn("read-back", res["oams"]["0"]["reason"])

    def test_journal_failure_fails_oam(self):
        os.environ["FAKE_JFAIL"] = "1"
        rc, ev = self.run_flash(oams=(1,))
        self.assertEqual(rc, 2)
        res = [e for e in ev if e["type"] == "result"][-1]
        self.assertEqual(res["oams"]["1"]["state"], "failed")

    def test_upload_sha_mismatch_aborts_before_write(self):
        os.environ["FAKE_BADUP"] = "1"
        rc, ev = self.run_flash()
        self.assertEqual(rc, 1)
        self.assertFalse((self.state / "updated").exists())
        self.assertIn("error", [e["phase"] for e in ev if e["type"] == "phase"])

    def test_sha_pin_mismatch_refuses(self):
        rc, _ = self.run_flash("--sha256", "0" * 64)
        self.assertEqual(rc, 1)
        self.assertFalse((self.state / "updated").exists())


class RunRedfish(Base):
    def setUp(self):
        super().setUp()
        self.slots = (0, 3)
        self.rf_slots = self.slots
        self.posts = []
        self.task_status = "Completed"
        self.task_refs = ("OAM_TASK_5",)
        self.omit_location = False
        self.busy_at = None
        self.http_error = None
        self.test_creds = {"BMC_USER": "test-user", "BMC_PASSWORD": "fake-secret-for-test",
                           "FAKE_SLOTS": "0,3"}
        env_patch = patch.dict(os.environ, self.test_creds)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        conns = patch.object(fw_flash.http.client, "HTTPSConnection", side_effect=self.connect)
        conns.start()
        self.addCleanup(conns.stop)
        original_stop = fw_flash.JournalFollower.stop
        stop = patch.object(fw_flash.JournalFollower, "stop", lambda f: original_stop(f, 0.15))
        stop.start()
        self.addCleanup(stop.stop)

    def connect(self, host, *, context, timeout):
        self.assertEqual(host, "bmc.example")
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        return self

    def request(self, method, path, *, body, headers):
        self.assertEqual(headers["Authorization"], "Basic dGVzdC11c2VyOmZha2Utc2VjcmV0LWZvci10ZXN0")
        self.status, self.location, self.payload = 200, None, None
        if self.http_error:
            if isinstance(self.http_error, OSError):
                raise self.http_error
            self.status = self.http_error
            return
        if path == fw_flash.REDFISH_INVENTORY:
            self.payload = {"Members": [
                {"@odata.id": f"{path}/OAM{i}_XDPE0", "Version": "1.2.3"} for i in self.rf_slots
            ] + [{"@odata.id": f"{path}/OAM7_XDPE0", "Version": "NA"}]}
        elif path == fw_flash.REDFISH_TASKS:
            existing = (("TASK_4",) if self.busy_at == len(self.posts) else ())
            ids = existing + (self.task_refs if len(self.posts) == 2 else ())
            self.payload = {"Members": [{"@odata.id": f"{path}/{i}"} for i in ids]}
        elif path.startswith(fw_flash.REDFISH_TASKS + "/"):
            self.payload = {"Name": "UpdateA1", "TaskState": (
                "Running" if path.endswith("TASK_4") else self.task_status), "TaskStatus": "OK"}
        elif path == fw_flash.REDFISH_UPLOAD:
            self.assertEqual(method, "POST")
            self.assertEqual(headers["Content-Type"], "application/octet-stream")
            self.assertEqual(headers["Content-Length"], str(self.image.stat().st_size))
            self.assertEqual(self.posts, [])
            self.assertTrue((self.state / "redfish.lock").is_dir())
            (self.state / "a1_update.img").write_bytes(body.read())
            self.posts.append("upload")
        elif path == fw_flash.REDFISH_UPDATE:
            self.assertEqual(method, "POST")
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(json.loads(body), {})
            self.assertEqual(self.posts, ["upload"])
            self.assertTrue((self.state / "stage-hashed").exists())
            self.posts.append("update")
            self.status = 202
            if not self.omit_location:
                self.location = f"https://bmc.example{fw_flash.REDFISH_TASKS}/{self.task_refs[0]}"
            if self.task_status == "Completed" and not os.getenv("FAKE_SKIP_UPDATE"):
                (self.state / "build").write_text(BUILD)
                (self.state / "updated").touch()
                for i in self.slots:
                    (self.state / f"updated-{i}").touch()
        else:
            self.fail(f"unexpected Redfish request: {method} {path}")

    def getresponse(self):
        return self

    def read(self):
        return json.dumps(self.payload).encode() if self.payload is not None else b""

    def getheader(self, name):
        return self.location if name == "Location" else None

    def close(self):
        pass

    def run_redfish(self, *extra, scope=("--all-oams", "--expect-oams", "2"), timeout=3):
        ev = self.tmp / "events.jsonl"
        argv = ["run", "--target", "srv", "--method", "redfish", "--image", str(self.image),
                "--sha256", fw_flash.sha256(self.image), "--events", str(ev),
                "--timeout", str(timeout), *scope, *extra]
        rc = fw_flash.main(argv)
        return rc, [json.loads(line) for line in ev.read_text().splitlines()]

    def test_explicit_whole_tray_written_but_boot_deferred(self):
        rc, events = self.run_redfish()
        self.assertEqual(self.posts, ["upload", "update"])
        self.assertEqual(rc, 2)
        self.assertFalse(events[-1]["ok"])
        self.assertEqual({k: (v["state"], v["before"], v["after"], v["reason"])
                          for k, v in events[-1]["oams"].items()},
                         {str(i): ("written", OLD, BUILD, "boot not verified") for i in self.slots})
        self.assertTrue(any("WRITTEN, BOOT NOT VERIFIED" in e.get("message", "") for e in events))
        self.assertTrue(any("[journal]" in e.get("line", "") for e in events))
        self.assertFalse((self.state / "redfish.lock").exists())
        self.assertFalse((self.state / "reset").exists())

    def test_requires_full_tray_opt_in_and_matching_inventories(self):
        with self.assertRaises(SystemExit) as err:
            self.run_redfish(scope=("--oam", "0"))
        self.assertEqual(err.exception.code, 2)
        self.rf_slots = (0, 1)
        rc, events = self.run_redfish()
        self.assertEqual(rc, 1)
        self.assertIn("inventory mismatch", events[-1]["error"])
        self.assertEqual(self.posts, [])

    def test_rejects_bad_tls_and_missing_credentials(self):
        self.http_error = ssl.SSLCertVerificationError("untrusted test certificate")
        rc, events = self.run_redfish()
        self.assertEqual(rc, 1)
        self.assertIn("untrusted test certificate", events[-1]["error"])
        with patch.dict(os.environ, BMC_PASSWORD=""):
            rc, events = self.run_redfish()
        self.assertEqual(rc, 1)
        self.assertIn("approved credential store", events[-1]["error"])
        self.assertEqual(self.posts, [])

    def test_redfish_redirect_is_not_followed(self):
        self.http_error = 307
        rc, events = self.run_redfish()
        self.assertEqual(rc, 1)
        self.assertIn("HTTP 307", events[-1]["error"])
        self.assertEqual(self.posts, [])

    def test_busy_before_trigger_and_bad_staged_hash_block_update(self):
        self.busy_at = 0
        rc, events = self.run_redfish()
        self.assertEqual(rc, 1)
        self.assertIn("queue not idle", events[-1]["error"])
        self.assertEqual(self.posts, [])

        self.busy_at = 1
        rc, events = self.run_redfish()
        self.assertEqual(rc, 1)
        self.assertIn("queue not idle", events[-1]["error"])
        self.assertEqual(self.posts, ["upload"])
        self.posts.clear()
        self.busy_at = None
        with patch.dict(os.environ, FAKE_BADUP="1"):
            rc, events = self.run_redfish()
        self.assertEqual(rc, 1)
        self.assertIn("staged image SHA-256", events[-1]["error"])
        self.assertEqual(self.posts, ["upload"])
        self.assertFalse((self.state / "redfish.lock").exists())

    def test_falls_back_to_one_named_task_and_rejects_two(self):
        self.omit_location = True
        rc, events = self.run_redfish()
        self.assertEqual(rc, 2)
        self.assertEqual(events[-1]["oams"]["0"]["state"], "written")
        self.posts.clear()
        self.task_refs = ("OAM_TASK_5", "TASK_6")
        rc, events = self.run_redfish()
        self.assertEqual(rc, 1)
        self.assertIn("multiple new UpdateA1 tasks", events[-1]["error"])
        self.assertTrue((self.state / "redfish.lock").exists())

    def test_failed_task_releases_lock_but_timeout_retains_it(self):
        self.task_status = "Exception"
        rc, events = self.run_redfish()
        self.assertEqual(rc, 1)
        self.assertIn("Exception", events[-1]["error"])
        self.assertFalse((self.state / "redfish.lock").exists())
        self.posts.clear()
        self.task_status = "Running"
        rc, events = self.run_redfish(timeout=1)
        self.assertEqual(rc, 1)
        self.assertIn("did not finish", events[-1]["error"])
        self.assertEqual(self.posts, ["upload", "update"])
        self.assertTrue((self.state / "redfish.lock").exists())

    def test_rejects_stale_or_incomplete_readback_per_oam(self):
        with patch.dict(os.environ, FAKE_STALE_OAM="3"):
            rc, events = self.run_redfish()
        self.assertEqual(rc, 2)
        self.assertEqual(events[-1]["oams"]["0"]["state"], "written")
        self.assertIn("three matching FPT", events[-1]["oams"]["3"]["reason"])
        self.posts.clear()
        (self.state / "updated").unlink()
        with patch.dict(os.environ, FAKE_POST_COPIES="2"):
            rc, events = self.run_redfish()
        self.assertEqual(rc, 2)
        self.assertEqual(events[-1]["oams"]["0"]["state"], "failed")

    def test_identical_build_without_per_oam_write_evidence_is_unverified(self):
        with patch.dict(os.environ, FAKE_OLD=BUILD, FAKE_SKIP_UPDATE="1"):
            rc, events = self.run_redfish()
        self.assertEqual(rc, 2)
        self.assertEqual(events[-1]["oams"]["0"]["state"], "unverified")
        self.assertIn("no per-OAM write evidence", events[-1]["oams"]["0"]["reason"])

    def test_identical_build_with_per_oam_write_evidence_is_written(self):
        with patch.dict(os.environ, FAKE_OLD=BUILD):
            rc, events = self.run_redfish()
        self.assertEqual(rc, 2)
        self.assertEqual({v["state"] for v in events[-1]["oams"].values()}, {"written"})


class Cancel(Base):
    def test_sigterm_before_write_cancels(self):
        ev = self.tmp / "ev.jsonl"
        env = dict(os.environ, FAKE_UPLOAD_SLEEP="1")
        # slow upload: wrap mctl so the upload step sleeps, giving time to cancel
        mctl = Path(self.env["FW_FLASH_MCTL"])
        mctl.write_text(mctl.read_text().replace('if "cat >" in script:',
                                                 'if "cat >" in script:\n    time.sleep(3)'))
        p = subprocess.Popen([sys.executable, str(HERE.parent / "scripts" / "fw_flash.py"), "run",
                              "--target", "srv", "--method", "bmc", "--image", str(self.image),
                              "--oam", "0", "--events", str(ev)], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.time() + 10
        while time.time() < deadline:
            if ev.exists() and '"upload"' in ev.read_text():
                break
            time.sleep(0.05)
        p.send_signal(signal.SIGTERM)
        out, _ = p.communicate(timeout=20)
        self.assertEqual(p.returncode, 130, out.decode())
        self.assertFalse((self.state / "updated").exists())
        res = [json.loads(l) for l in ev.read_text().splitlines()][-1]
        self.assertTrue(res.get("cancelled"))


if __name__ == "__main__":
    unittest.main()
