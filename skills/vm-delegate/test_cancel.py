"""Cancelling a delegated job goes through `mctl cancel` and only hits a live, owned run."""
import subprocess
import unittest
from unittest import mock

import vd

JOB = {"id": "j1", "target": "vm", "tag": "ws1", "kind": "build", "pid": 4242}


def _proc(stdout="", rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr=stderr)


class CancelTest(unittest.TestCase):
    def cancel(self, *, status="running", owns=True, pid=4242, run=None, job=JOB):
        state = {"jobs": {"j1": {"status": status, "pid": pid}}}
        run = run or mock.Mock(return_value=_proc("__MCTL_CANCEL__ CANCELLED\n"))
        with mock.patch.object(vd, "_job_by_id", return_value=job), \
                mock.patch.object(vd, "_read_state", return_value=state), \
                mock.patch.object(vd, "owns_slot", return_value=owns), \
                mock.patch.object(vd, "agent_env", return_value=({}, "")), \
                mock.patch.object(vd.subprocess, "run", run), \
                mock.patch.object(vd, "refresh", return_value={"errors": {}}) as refresh:
            out = vd.cancel("j1")
        return out, run, refresh

    def test_running_owned_job_is_cancelled_through_mctl_with_its_pid(self):
        out, run, refresh = self.cancel()
        self.assertEqual(out["result"], "CANCELLED")
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:], ["-t", "vm", "cancel", "ws1", "build", "4242"])
        refresh.assert_called_once_with("vm")

    def test_not_running_is_refused_without_touching_the_target(self):
        out, run, _ = self.cancel(status="passed")
        self.assertIn("not running", out["error"])
        run.assert_not_called()

    def test_superseded_slot_is_refused(self):
        out, run, _ = self.cancel(owns=False)
        self.assertIn("later run", out["error"])
        run.assert_not_called()

    def test_missing_pid_is_refused(self):
        out, run, _ = self.cancel(pid=None, job=dict(JOB, pid=None))
        self.assertIn("pid", out["error"])
        run.assert_not_called()

    def test_unsafe_tag_is_refused(self):
        out, run, _ = self.cancel(job=dict(JOB, tag="ws;rm -rf"))
        self.assertIn("unsafe", out["error"])
        run.assert_not_called()

    def test_unknown_job(self):
        with mock.patch.object(vd, "_job_by_id", return_value=None):
            self.assertEqual(vd.cancel("nope"), {"error": "unknown job"})

    def test_mctl_failure_without_a_result_is_an_error(self):
        run = mock.Mock(return_value=_proc("", 1, "ssh: connect refused"))
        out, _, refresh = self.cancel(run=run)
        self.assertEqual(out["error"], "ssh: connect refused")
        refresh.assert_not_called()

    def test_portal_cancel_reports_a_pid_mismatch_as_an_error(self):
        run = mock.Mock(return_value=_proc("__MCTL_CANCEL__ MISMATCH pid=99\n"))
        state = {"jobs": {"j1": {"status": "running", "pid": 4242}}}
        with mock.patch.object(vd, "_job_by_id", return_value=JOB), \
                mock.patch.object(vd, "_read_state", return_value=state), \
                mock.patch.object(vd, "owns_slot", return_value=True), \
                mock.patch.object(vd, "agent_env", return_value=({}, "")), \
                mock.patch.object(vd.subprocess, "run", run), \
                mock.patch.object(vd, "refresh", return_value={"errors": {}}), \
                mock.patch.object(vd, "payload", return_value={"jobs": []}):
            out = vd.portal_cancel("j1")
        self.assertIn("MISMATCH", out["error"])
        self.assertEqual(out["cancel"]["result"], "MISMATCH pid=99")

    def test_cancelled_probe_state_is_terminal(self):
        self.assertEqual(vd._status_of("CANCELLED", 130), "cancelled")
        self.assertIn("cancelled", vd.TERMINAL)


if __name__ == "__main__":
    unittest.main()
