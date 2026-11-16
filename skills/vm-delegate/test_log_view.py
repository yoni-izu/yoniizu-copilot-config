"""The portal's log viewer always tails the log on its machine (no pager, no mirror)."""
import unittest
from unittest import mock

import vd

JOB = {"id": "j1", "target": "vm", "tag": "ws1", "kind": "build", "pid": 4242}


class LogViewTest(unittest.TestCase):
    def view(self, *, running: bool, owns: bool, pid=4242):
        job = dict(JOB, pid=pid)
        state = {"jobs": {"j1": {"status": "running" if running else "done", "pid": pid}}}
        with mock.patch.object(vd, "_job_by_id", return_value=job), \
                mock.patch.object(vd, "_read_state", return_value=state), \
                mock.patch.object(vd, "owns_slot", return_value=owns):
            return vd.log_view("j1")

    def test_live_job_follows_with_tail_f(self):
        out = self.view(running=True, owns=True)
        self.assertEqual(out["mode"], "remote")
        self.assertTrue(out["running"])
        self.assertIn("tail -n 300 -F build.log", out["command"])

    def test_finished_job_tails_on_the_machine_not_a_pager(self):
        out = self.view(running=False, owns=True)
        self.assertEqual(out["mode"], "remote")
        self.assertFalse(out["running"])
        self.assertIn(f"tail -n {vd.LOG_VIEW_LINES} build.log", out["command"])
        self.assertNotIn("less", out["command"])
        self.assertNotIn("-F", out["command"])

    def test_overwritten_run_tails_its_timestamped_archive_on_the_machine(self):
        with mock.patch.object(vd, "mirror_run", side_effect=AssertionError("mirror read")):
            out = self.view(running=False, owns=False)
        self.assertEqual(out["mode"], "remote")
        self.assertIn('runs/"build-*-4242.log', out["command"])
        self.assertIn(f'tail -n {vd.LOG_VIEW_LINES} "$f"', out["command"])
        self.assertNotIn("less", out["command"])

    def test_overwritten_run_without_pid_is_an_error_not_a_local_copy(self):
        out = self.view(running=False, owns=False, pid=None)
        self.assertIn("error", out)
        self.assertNotIn("mode", out)


if __name__ == "__main__":
    unittest.main()
