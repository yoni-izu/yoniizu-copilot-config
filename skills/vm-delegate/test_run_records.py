"""mctl runs carry a shared job id: vd reads THAT run's record, never the slot's."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import vd

JID = "mctl-vm-20250101T000000Z-ab12"


class RunRecords(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(vd.os.environ, {"JOB_ROOT": str(self.tmp / "jobs")})
        self.env.start()
        self.job = {"id": "x1", "target": "vm", "tag": "ws1", "kind": "build",
                    "pid": 42, "started": "2025-01-01T00:00:00", "job_id": JID}

    def tearDown(self):
        self.env.stop()

    def record(self, rc=None, stopped=False, log="line one\nline two\n"):
        d = vd.joblib.job_dir(JID)
        d.mkdir(parents=True)
        (d / "runner").write_text("mctl\n")
        (d / "host").write_text("elsewhere\n")
        (d / "log").write_text(log)
        if rc is not None:
            (d / "rc").write_text(f"{rc}\n")
            (d / "finished").write_text("2025-01-01T01:00:00Z\n")
        if stopped:
            (d / "stopped").write_text("x\n")
        return d

    def test_probe_reads_the_job_id_column_and_rejects_unsafe_ids(self):
        base = "ws1\tbuild\tdone\t0\t42\t1700000000\t10\t0\tcmd\tlast"
        rows = vd.parse_probe(f"{base}\t{JID}\nws2\tbuild\tdone\t0\t1\t1\t1\t0\tc\tl\t../evil\n"
                              "ws3\tbuild\tdone\t0\t1\t1\t1\t0\tc\tl\n")
        self.assertEqual(rows[("ws1", "build")]["job_id"], JID)
        self.assertEqual(rows[("ws2", "build")]["job_id"], "")
        self.assertEqual(rows[("ws3", "build")]["job_id"], "")

    def test_run_id_only_trusts_mctl_ids(self):
        self.assertEqual(vd.run_id(self.job), JID)
        self.assertEqual(vd.run_id({"job_id": "pilot-x"}), "")
        self.assertEqual(vd.run_id(None), "")

    def test_read_log_serves_the_synced_record_of_a_finished_run(self):
        self.record(rc=0)
        with mock.patch.object(vd, "_job_by_id", return_value=self.job), \
             mock.patch.object(vd, "tail_run") as tr, \
             mock.patch.object(vd, "tail_remote") as tslot:
            out = vd.read_log("vm", "ws1", "build", job_id="x1")
        self.assertEqual(out["source"], "local")
        self.assertIn("line two", out["text"])
        tr.assert_not_called()
        tslot.assert_not_called()

    def test_unfinished_record_is_not_served_as_final(self):
        self.record(rc=None)
        self.assertIsNone(vd._read_run_copy(self.job, 1000))

    def test_tail_uses_the_run_id_and_never_the_slot(self):
        with mock.patch.object(vd, "_job_by_id", return_value=self.job), \
             mock.patch.object(vd, "owns_slot", return_value=False), \
             mock.patch.object(vd, "tail_run", return_value="from the record") as tr, \
             mock.patch.object(vd, "tail_remote") as tslot:
            out = vd.tail("vm", "ws1", "build", job_id="x1")
        self.assertEqual(out["text"], "from the record")
        tr.assert_called_once_with("vm", JID, 200)
        tslot.assert_not_called()

    def test_superseded_run_takes_its_outcome_from_the_record(self):
        self.record(rc=2)
        self.assertEqual(vd._run_outcome(self.job)["status"], "failed")
        vd.joblib.job_dir(JID).joinpath("stopped").write_text("x\n")
        self.assertEqual(vd._run_outcome(self.job)["status"], "cancelled")

    def test_outcome_unknown_without_a_synced_rc(self):
        self.record(rc=None)
        self.assertIsNone(vd._run_outcome(self.job))


if __name__ == "__main__":
    unittest.main()
