"""Local-runner jobs (fw flash): started locally, status from the local log + pid."""
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import vd

WORKER = textwrap.dedent('''
    import json, signal, sys, time
    ev, phase, rc, hold = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
    def term(*_):
        if phase == "write":
            print("cancel REFUSED", flush=True); return
        print("cancelled", flush=True); sys.exit(130)
    signal.signal(signal.SIGTERM, term)
    with open(ev, "a") as fh:
        fh.write(json.dumps({"type": "phase", "phase": phase}) + "\\n")
    print("working", flush=True)
    end = time.time() + hold
    while time.time() < end:
        time.sleep(0.05)
    print("bye", flush=True)
    sys.exit(rc)
''')


class LocalJobs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.worker = self.tmp / "worker.py"
        self.worker.write_text(WORKER)
        self.log: list[dict] = []
        self.patches = [
            mock.patch.object(vd, "LOCAL_DIR", self.tmp / "local"),
            mock.patch.object(vd, "MCTL_DIR", self.tmp),
            mock.patch.object(vd, "_read_log", side_effect=lambda: list(self.log)),
            mock.patch.object(vd, "_append_log", side_effect=self.log.append),
            mock.patch.object(vd, "_read_state", return_value={"jobs": {}}),
            mock.patch.object(vd, "is_target", return_value=True),
            mock.patch.object(vd, "agent_env", return_value=(dict(vd.os.environ), "")),
            mock.patch.object(vd.shutil, "which", return_value=None),   # no systemd-run
        ]
        for p in self.patches:
            p.start()
        self.started = []

    def tearDown(self):
        for j in self.started:
            pid = vd._pid_of(j)
            if pid:
                try:
                    vd.os.kill(pid, 9)
                except OSError:
                    pass
        for p in reversed(self.patches):
            p.stop()

    def start(self, phase="upload", rc=0, hold=0.0):
        out = vd.start_local_job("srv22", "flash",
                                 [vd.sys.executable, str(self.worker), "{events}", phase,
                                  str(rc), str(hold)], label="fw-flash test")
        self.assertNotIn("error", out, out)
        self.started.append(out["job"])
        return out["job"]

    def wait(self, job, status, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            st = vd.local_status(job)
            if st["status"] == status:
                return st
            time.sleep(0.05)
        self.fail(f"status never became {status}: {vd.local_status(job)}")

    def test_finished_job_passes_and_is_listed(self):
        job = self.start(rc=0)
        st = self.wait(job, "passed")
        self.assertEqual(st["rc"], 0)
        self.assertEqual(st["last_line"], "bye")
        row = [r for r in vd.jobs() if r["id"] == job["id"]][0]
        self.assertEqual((row["status"], row["runner"], row["kind"]), ("passed", "local", "flash"))
        self.assertTrue(row["has_log"])
        self.assertEqual(vd.job_statuses()[job["id"]]["status"], "passed")
        lv = vd.log_view(job["id"])
        self.assertEqual(lv["mode"], "local")
        self.assertIn(str(vd.local_files(job)["log"]), lv["command"])
        self.assertIn("bye", vd.read_log("srv22", job["tag"], "flash", job_id=job["id"])["text"])

    def test_nonzero_rc_is_failed(self):
        job = self.start(rc=2)
        self.assertEqual(self.wait(job, "failed")["rc"], 2)

    def test_second_running_flash_on_same_target_is_refused(self):
        job = self.start(hold=5)
        self.wait(job, "running")
        out = vd.start_local_job("srv22", "flash", ["true"])
        self.assertIn("already running", out["error"])
        self.assertEqual(out["job_id"], job["id"])

    def test_cancel_before_write_stops_the_job(self):
        job = self.start(phase="upload", hold=30)
        self.wait(job, "running")
        time.sleep(0.3)
        self.assertTrue(vd.jobs()[0]["cancellable"])
        out = vd.cancel(job["id"])
        self.assertEqual(out["result"], "CANCELLED", out)
        self.assertEqual(vd.local_status(job)["rc"], 130)

    def test_cancel_during_write_is_refused_without_signalling(self):
        job = self.start(phase="write", hold=30)
        self.wait(job, "running")
        time.sleep(0.3)
        self.assertFalse(vd.jobs()[0]["cancellable"])
        with mock.patch.object(vd.os, "kill") as kill:
            out = vd.cancel(job["id"])
        self.assertIn("write phase", out["error"])
        kill.assert_not_called()

    def test_refresh_never_touches_local_jobs(self):
        job = self.start(rc=0)
        self.wait(job, "passed")
        state = {"jobs": {}, "checked": {}, "attempted": {}, "errors": {}}
        with mock.patch.object(vd, "_read_state", return_value=state), \
                mock.patch.object(vd, "targets", return_value=["srv22"]), \
                mock.patch.object(vd, "_write_state"), \
                mock.patch.object(vd, "_tag_to_ws", return_value={}):
            vd.refresh(sync=False, preprobed={"srv22": {}})
        self.assertNotIn(job["id"], state["jobs"])

    def test_unknown_kind_and_target_refused(self):
        self.assertIn("unsupported", vd.start_local_job("srv22", "build", ["true"])["error"])
        with mock.patch.object(vd, "is_target", return_value=False):
            self.assertIn("unknown target", vd.start_local_job("x", "flash", ["true"])["error"])


if __name__ == "__main__":
    unittest.main()
