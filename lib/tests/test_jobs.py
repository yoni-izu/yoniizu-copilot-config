"""Shared job record: bin/job (shell) and lib/jobs.py (Python) agree."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
JOB = LIB.parent / "bin" / "job"
sys.path.insert(0, str(LIB))
import jobs  # noqa: E402


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "jobs"
        self.env = {**os.environ, "JOB_ROOT": str(self.root)}
        self.env.pop("PILOT_TARGET", None)

    def tearDown(self):
        for rec in jobs.list_jobs(base=self.root):
            if rec["status"] == "running" and rec["pid"]:
                try:
                    os.killpg(rec["pid"], 9)
                except OSError:
                    pass
        self.tmp.cleanup()

    def sh(self, *args, check=True):
        p = subprocess.run([str(JOB), *args], env=self.env, capture_output=True, text=True, timeout=30)
        if check and p.returncode != 0:
            self.fail(f"job {args} rc={p.returncode}\n{p.stdout}\n{p.stderr}")
        return p

    def run_job(self, *args):
        return self.sh("run", *args).stdout.strip()

    def settle(self, job_id, secs=10):
        deadline = time.time() + secs
        while time.time() < deadline:
            rec = jobs.get(job_id, self.root)
            if rec and rec["status"] != "running":
                return rec
            time.sleep(0.05)
        self.fail(f"{job_id} still running")

    def tsv(self, *args):
        rows = {}
        for line in self.sh("list", "--tsv", *args).stdout.splitlines():
            cols = line.split("\t")
            self.assertEqual(len(cols), 14, line)
            rows[cols[0]] = cols
        return rows

    def test_run_records_fields_and_both_sides_agree(self):
        cwd = Path(self.tmp.name) / "ws"
        cwd.mkdir()
        jid = self.run_job("--runner", "mctl", "--kind", "build", "--cwd", str(cwd),
                           "-T", "vm", "--label", "a b", "--", "pwd; echo $JOB_ID; exit 3")
        self.assertTrue(jid.startswith("mctl-vm-"), jid)
        rec = self.settle(jid)
        self.assertEqual((rec["status"], rec["rc"], rec["runner"], rec["kind"], rec["target"]),
                         ("failed", 3, "mctl", "build", "vm"))
        self.assertEqual(rec["host"], jobs.this_host())
        self.assertEqual(rec["cwd"], str(cwd))
        self.assertEqual(rec["last_line"], jid)
        self.assertIn(str(cwd), Path(rec["log"]).read_text())
        self.assertTrue(rec["finished_ts"])
        row = self.tsv()[jid]
        self.assertEqual((row[1], row[2], row[5], row[6]), ("mctl", "build", "failed", "3"))
        self.assertEqual(row[13], jid)

    def test_plain_strips_ansi_and_sets_term(self):
        jid = self.run_job("--plain", "printf '\\033[31mred\\033[0m %s\\n' \"$TERM\"")
        rec = self.settle(jid)
        self.assertEqual(rec["status"], "passed")
        log = Path(rec["log"]).read_text()
        self.assertIn("red dumb", log)
        self.assertNotIn("\x1b", log)

    def test_kill_marks_cancelled_and_reports(self):
        jid = self.run_job("-t", "sleeper", "sleep 30")
        self.assertEqual(jid, "sleeper")
        time.sleep(0.3)
        self.assertEqual(jobs.get(jid, self.root)["status"], "running")
        self.assertEqual(self.tsv()[jid][5], "running")
        self.assertIn("__JOB_KILL__ STOPPED", self.sh("kill", jid, "--wait", "3").stdout)
        self.assertEqual(jobs.get(jid, self.root)["status"], "cancelled")
        self.assertEqual(self.tsv()[jid][5], "cancelled")
        self.assertIn("__JOB_KILL__ DIED", self.sh("kill", jid).stdout)

    def test_python_stop_and_unstoppable_phase(self):
        jid = self.run_job("--unstoppable", "write verify",
                           f"echo '{{\"type\":\"phase\",\"phase\":\"write\"}}' > {self.root}/$JOB_ID/events.jsonl; sleep 30")
        time.sleep(0.4)
        self.assertIn("refusing", jobs.stop(jid, base=self.root)["error"])
        p = self.sh("kill", jid, check=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("REFUSED write", p.stdout)
        (self.root / jid / "events.jsonl").write_text('{"type":"phase","phase":"post"}\n')
        self.assertEqual(jobs.stop(jid, base=self.root)["result"], "STOPPED")
        self.assertEqual(jobs.get(jid, self.root)["status"], "cancelled")

    def test_busy_guard_local_and_remote(self):
        cwd = str(Path(self.tmp.name))
        jid = self.run_job("--cwd", cwd, "sleep 30")
        time.sleep(0.3)
        p = self.sh("busy", "--cwd", cwd, check=False)
        self.assertEqual(p.returncode, 3)
        self.assertIn(jid, p.stdout)
        self.assertEqual([r["id"] for r in jobs.busy(cwd, self.root)], [jid])
        self.sh("kill", jid)
        self.assertEqual(self.sh("busy", "--cwd", cwd).returncode, 0)
        jobs.create("mctl-other-1", runner="mctl", kind="build", cmd="x", cwd=cwd,
                    host="otherbox", base=self.root)
        self.assertEqual(self.tsv()["mctl-other-1"][5], "remote")
        self.assertEqual(self.sh("busy", "--cwd", cwd, check=False).returncode, 3)
        self.assertEqual([r["id"] for r in jobs.busy(cwd, self.root)], ["mctl-other-1"])
        (self.root / "mctl-other-1" / "rc").write_text("0\n")
        self.assertEqual(self.sh("busy", "--cwd", cwd).returncode, 0)
        self.assertEqual(jobs.busy(cwd, self.root), [])

    def test_legacy_dir_and_pid_reuse(self):
        legacy = self.root / "old-tag"
        legacy.mkdir(parents=True)
        (legacy / "cmd.sh").write_text("make\n")
        (legacy / "log").write_text("building\nok\n__JOB_DONE__ rc=0\n")
        (legacy / "started").write_text("2024-01-01T00:00:00Z\n")
        rec = jobs.get("old-tag", self.root)
        self.assertEqual((rec["runner"], rec["status"], rec["rc"], rec["last_line"]),
                         ("pilot", "passed", 0, "ok"))
        self.assertEqual(self.tsv()["old-tag"][5], "passed")
        reused = self.root / "reused"
        reused.mkdir()
        (reused / "pid").write_text(f"{os.getpid()}\n")       # alive, but not this job
        (reused / "started").write_text("2024-01-01T00:00:00Z\n")
        self.assertEqual(jobs.get("reused", self.root)["status"], "died")
        self.assertEqual(self.tsv()["reused"][5], "died")

    def test_list_filters_and_clean(self):
        jobs.create("pilot-a", runner="pilot", kind="job", cmd="x", base=self.root)
        (self.root / "pilot-a" / "rc").write_text("0\n")
        jobs.create("mctl-b", runner="mctl", kind="build", cmd="x", host="vmbox", base=self.root,
                    meta={"instance": "i-1"})
        self.assertEqual(jobs.get("mctl-b", self.root)["meta"], {"instance": "i-1"})
        self.assertEqual(set(self.tsv("--host", "vmbox")), {"mctl-b"})
        self.assertEqual(set(self.tsv("--runner", "pilot")), {"pilot-a"})
        self.assertEqual({r["id"] for r in jobs.list_jobs(runner="mctl", base=self.root)}, {"mctl-b"})
        old = time.time() - 40 * 86400
        for d in ("pilot-a", "mctl-b"):
            (self.root / d / "started").write_text("2000-01-01T00:00:00Z\n")
            for f in [*(self.root / d).iterdir(), self.root / d]:
                os.utime(f, (old, old))
        self.assertEqual(jobs.list_jobs(days=30, base=self.root), [])
        self.assertEqual(jobs.clean(30, base=self.root), ["pilot-a"])   # remote kept
        self.assertTrue((self.root / "mctl-b").exists())
        (self.root / "mctl-b" / "rc").write_text("1\n")              # its synced outcome
        for f in (self.root / "mctl-b" / "rc", self.root / "mctl-b"):
            os.utime(f, (old, old))
        self.assertEqual(jobs.clean(30, base=self.root), ["mctl-b"])    # finished copy pruned

    def test_rejects_unsafe_ids(self):
        self.assertNotEqual(self.sh("run", "-t", "../x", "true", check=False).returncode, 0)
        with self.assertRaises(ValueError):
            jobs.job_dir("../x", self.root)
        self.assertIsNone(jobs.get("a/b", self.root))

    def test_new_id_format(self):
        jid = jobs.new_id("mctl", "vm")
        self.assertRegex(jid, r"^mctl-vm-\d{8}T\d{6}Z-[0-9a-f]{4}$")
        self.assertTrue(jobs.safe(jid))


if __name__ == "__main__":
    unittest.main()
