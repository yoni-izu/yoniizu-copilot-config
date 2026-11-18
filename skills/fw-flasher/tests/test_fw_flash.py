"""fw_flash.py tests against a fake mctl (BMC + dest) and fake ssh; no lab access."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

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
    if args[2] == "dest":
        print("user@host"); sys.exit(0)
    script = args[3]
    n = int(os.environ.get("FAKE_OAMS", "2"))
    build = (st / "build").read_text() if (st / "build").exists() else os.environ["FAKE_OLD"]
    if "__STATUS__" in script:
        print("__STATUS__")
        for i in range(n): print(f"dev{i}[eid=0 cpld=yes state=READY]")
        print("__VERSION__")
        for i in range(n):
            for _ in range(3): print(f"dev{i} version=0.5.23 build={build} uuid=x")
        print("__OAM__")
        for i in range(n): print(f"dev{i} boot=1 rdy=1")
        print("__MTD__"); print("no"); print("__FWLOADER__"); print("")
        print("__TMP__"); print("tmpfs 1000 0 1000000 0% /tmp"); sys.exit(0)
    if "cat >" in script:
        data = sys.stdin.buffer.read()
        if os.environ.get("FAKE_BADUP"): data += b"x"
        print(hashlib.sha256(data).hexdigest() + "  /tmp/img"); sys.exit(0)
    if script.startswith("journalctl"):
        seen = set()
        for _ in range(1200):
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
        (st / f"updated-{i}").write_text("1")
        (st / "build").write_text(os.environ.get("FAKE_NEWBUILD", os.environ["FAKE_NEW"]))
        (st / "updated").write_text("1")
        print("queued"); sys.exit(0)
    if "bmccli device status" in script:
        for i in range(n): print(f"dev{i}[eid=0 cpld=yes state=READY]")
        sys.exit(0)
    if "bmccli flash version -f" in script:
        i = script.split("-d ")[-1].split()[0]
        for _ in range(3): print(f"dev{i} version=0.5.23 build={build} uuid=x")
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
        self.tmp = Path(tempfile.mkdtemp())
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

    def run_flash(self, *extra, oams=(0, 1)):
        ev = self.tmp / "events.jsonl"
        argv = ["run", "--target", "srv", "--method", "bmc", "--image", str(self.image),
                "--events", str(ev), "--job-id", "t1", "--timeout", "20", *extra]
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
