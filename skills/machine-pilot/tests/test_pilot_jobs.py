import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pilot_jobs as pj  # noqa: E402

REG = {"targets": {"srv12", "srv22", "pd02"}, "bmc": {"bmc-sa-els37-csr": "srv12"},
       "host": {"sa-srv22-csr": "srv22"}, "target_bmc": {"srv12": "bmc-sa-els37-csr"},
       "mctl_bmc": {"srv12": "bmc-sa-els37-csr"}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.jobs = root / "jobs"
        self.jobs.mkdir()
        patches = [mock.patch.object(pj, "JOBS_ROOT", self.jobs),
                   mock.patch.object(pj, "BMC_CACHE", root / "cache"),
                   mock.patch.object(pj, "_registry", lambda: REG)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def job(self, tag, cmd="true", rc="0", target=None, pid=None, started="2026-01-01T10:00:00Z"):
        d = self.jobs / tag
        d.mkdir()
        (d / "cmd.sh").write_text(cmd + "\n")
        (d / "started").write_text(started + "\n")
        (d / "log").write_text("hello\nlast words\n" + (f"__JOB_DONE__ rc={rc}\n" if rc else ""))
        if rc:
            (d / "rc").write_text(rc + "\n")
        if pid:
            (d / "pid").write_text(f"{pid}\n")
        if target:
            (d / "target").write_text(target + "\n")
        return d


class Attribution(unittest.TestCase):
    def test_order_and_known_only(self):
        self.assertEqual(pj.attribute("x", "t", "srv22", REG), ("srv22", "recorded"))
        self.assertEqual(pj.attribute("mctl -t srv12 sh ls", "a", "", REG), ("srv12", "cmd"))
        # job's own -t TAG must not be read as a machine
        self.assertEqual(pj.attribute("job run -t build1 make", "a", "", REG), ("", "none"))
        self.assertEqual(pj.attribute("ssh root@bmc-sa-els37-csr x", "a", "", REG), ("srv12", "bmc"))
        self.assertEqual(pj.attribute("ssh me@sa-srv22-csr.lab x", "a", "", REG), ("srv22", "host"))
        self.assertEqual(pj.attribute("true", "pd02-flash-1", "", REG), ("pd02", "tag"))

    def test_stop_refusal(self):
        self.assertTrue(pj.stop_refusal("fw-flash -t srv12 img"))
        self.assertTrue(pj.stop_refusal("bmccli device reset"))
        self.assertEqual(pj.stop_refusal("sleep 300"), "")


class Local(Base):
    def test_status_rc_and_window(self):
        self.job("ok", rc="0", target="srv12")
        self.job("bad", rc="2")
        self.job("gone", rc="", pid=None)
        old = self.job("old", rc="0", started="2020-01-01T00:00:00Z")
        for f in [old, *old.iterdir()]:
            os.utime(f, (0, 0))
        rows = {r["tag"]: r for r in pj.list_local(14, reg=REG)}
        self.assertEqual(set(rows), {"ok", "bad", "gone"})
        self.assertEqual((rows["ok"]["status"], rows["ok"]["target"]), ("passed", "srv12"))
        self.assertEqual((rows["bad"]["status"], rows["bad"]["rc"]), ("failed", 2))
        self.assertEqual(rows["gone"]["status"], "died")
        self.assertEqual(rows["ok"]["last_line"], "last words")
        self.assertIn("old", {r["tag"] for r in pj.list_local(None, reg=REG)})

    def test_reused_pid_is_not_running(self):
        self.job("reuse", rc="", pid=os.getpid())   # alive, but not this job's wrapper
        (r,) = pj.list_local(None, reg=REG)
        self.assertEqual(r["status"], "died")
        self.assertFalse(r["stoppable"])

    def test_other_host_and_other_runners(self):
        d = self.job("nfs", rc="", pid=os.getpid())
        (d / "host").write_text("someotherbox\n")
        m = self.job("mctl-vm-1", rc="0")
        (m / "runner").write_text("mctl\n")
        f = self.job("flash-srv22-1", rc="0")
        (f / "runner").write_text("flash\n")
        a = self.job("adhoc-1", rc="0")
        (a / "runner").write_text("adhoc\n")             # an ad-hoc `job run --runner adhoc`
        rows = {r["tag"]: r for r in pj.list_local(None, reg=REG)}
        self.assertEqual(set(rows), {"nfs", "adhoc-1"})    # mctl/flash are vm-delegate's
        self.assertEqual(rows["adhoc-1"]["runner"], "adhoc")
        r = rows["nfs"]
        self.assertEqual((r["tag"], r["status"], r["host"]), ("nfs", "unknown", "someotherbox"))
        self.assertFalse(r["stoppable"])
        self.assertIn("someotherbox", r["error"])

    def test_stop_and_log_view_of_a_real_job(self):
        d = self.jobs / "live"
        d.mkdir()
        (d / "cmd.sh").write_text("sleep 30\n")
        (d / "log").write_text("")
        p = subprocess.Popen(["bash", "-c", "sleep 30; true", str(d)], start_new_session=True)
        self.addCleanup(lambda: p.poll() is None and p.kill())
        (d / "pid").write_text(f"{p.pid}\n")
        time.sleep(0.1)
        (r,) = pj.list_local(None, reg=REG)
        self.assertEqual(r["status"], "running")
        view = pj.log_view("pj:L:live")
        self.assertIn("tail -n 300 -F", view["command"])
        res = pj.stop("pj:L:live")
        self.assertEqual(res["result"], "STOPPED")
        p.wait(timeout=5)
        (r,) = pj.list_local(None, reg=REG)
        self.assertEqual(r["status"], "cancelled")

    def test_refuses_unsafe_and_unknown(self):
        self.assertIn("error", pj.stop("pj:L:../etc"))
        self.assertIn("error", pj.stop("pj:L:nope"))
        self.job("done", rc="0")
        self.assertIn("not running", pj.stop("pj:L:done")["error"])


BMC_OUT = ("__J__\tfl1\t42\t\t1\t2026-01-01T10:00:00Z\t\tfw_ctl flash write x\tprogress 40%\n"
           "__J__\tok1\t43\t0\t0\t2026-01-01T09:00:00Z\t2026-01-01T09:05:00Z\techo hi\thi\n"
           "__J__\t../x\t1\t0\t0\t\t\tbad\tbad\n__END__\n")


class Bmc(Base):
    def runner(self, out="", rc=0, err=""):
        return mock.Mock(return_value=subprocess.CompletedProcess([], rc, out, err))

    def test_refresh_parse_and_refusal(self):
        now = pj._utc_epoch("2026-01-01T10:10:00Z")
        res = pj.refresh_bmc("srv12", run=self.runner(BMC_OUT), now=now)
        self.assertEqual((res["jobs"], res["error"]), (2, ""))
        rows = {r["tag"]: r for r in pj.list_bmc(None, now=now, reg=REG)}
        self.assertEqual(set(rows), {"fl1", "ok1"})
        self.assertEqual(rows["fl1"]["id"], "pj:B:srv12:fl1")
        self.assertEqual(rows["fl1"]["status"], "running")
        self.assertTrue(rows["fl1"]["stop_refusal"])
        self.assertEqual(rows["ok1"]["status"], "passed")

    def test_failed_read_keeps_rows_and_ages_them(self):
        t0 = 1_800_000_000
        pj.refresh_bmc("srv12", run=self.runner(BMC_OUT), now=t0)
        res = pj.refresh_bmc("srv12", run=self.runner("", 255, "Permission denied"), now=t0 + 700)
        self.assertIn("Permission denied", res["error"])
        cache = json.loads((pj.BMC_CACHE / "srv12.json").read_text())
        self.assertEqual(cache["checked"], t0)
        rows = {r["tag"]: r for r in pj.list_bmc(None, now=t0 + 700, reg=REG)}
        self.assertTrue(rows["fl1"]["stale"])
        self.assertFalse(rows["fl1"]["stoppable"])
        rows = {r["tag"]: r for r in pj.list_bmc(None, now=t0 + 7 * 3600, reg=REG)}
        self.assertEqual(rows["fl1"]["status"], "unknown")

    def test_auto_refresh_backs_off_after_rejected_login(self):
        t0 = 1_800_000_000
        pj.refresh_bmc("srv12", run=self.runner("", 255, "root@b: Permission denied (publickey,password)."), now=t0)
        run = self.runner(BMC_OUT)
        self.assertTrue(pj.refresh_bmc("srv12", run=run, now=t0 + 60, auto=True)["skipped"])
        run.assert_not_called()
        self.assertEqual(pj.refresh_bmc("srv12", run=run, now=t0 + 60)["jobs"], 2)
        self.assertEqual(pj.refresh_bmc("srv12", run=run, now=t0 + 7 * 3600, auto=True)["jobs"], 2)

    def test_unregistered_target_is_skipped(self):
        run = self.runner(BMC_OUT)
        self.assertTrue(pj.refresh_bmc("pd02", run=run)["skipped"])
        run.assert_not_called()

    def test_no_bmcjob_dir(self):
        res = pj.refresh_bmc("srv12", run=self.runner("__NOBMCJOBS__\n"))
        self.assertEqual((res["present"], res["jobs"], res["error"]), (False, 0, ""))

    def test_bmc_stop_verifies_then_signals(self):
        now = time.time()
        out = BMC_OUT.replace("fw_ctl flash write x", "sleep 99")
        pj.refresh_bmc("srv12", run=self.runner(out), now=now)
        run = mock.Mock(side_effect=[subprocess.CompletedProcess([], 0, "SIGNALLED\n", ""),
                                     subprocess.CompletedProcess([], 0, out, "")])
        res = pj.stop("pj:B:srv12:fl1", run=run)
        self.assertEqual(res["result"], "STOPPED")
        script = run.call_args_list[0].args[0][-1]
        self.assertIn("/var/lib/bmcjobs/fl1", script)
        self.assertIn("NOTOURS", script)
        self.assertIn("kill -TERM -$p", script)
        rows = pj.parse_bmc("__J__\tx\t5\t\t0\t\t\tsleep\t\t1\n__END__\n")[0]
        self.assertTrue(rows[0]["stopped"])


if __name__ == "__main__":
    unittest.main()
