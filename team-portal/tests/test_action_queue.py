import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from action_queue import ActionQueue
import portal


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "actions.db"
        self.queues = []
        self.addCleanup(self.stop_queues)

    def stop_queues(self):
        for queue in self.queues:
            queue.stop()
            queue.join()

    def queue(self, execute, jobs=None):
        queue = ActionQueue(self.path, execute, jobs if jobs is not None else {},
                            threading.Lock())
        self.queues.append(queue)
        return queue

    def wait_done(self, queue, count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with sqlite3.connect(self.path) as conn:
                finished = conn.execute(
                    "SELECT count(*) FROM actions WHERE state IN "
                    "('done','error','approval_required')").fetchone()[0]
            if finished == count:
                return
            time.sleep(.01)
        self.fail(f"Queue did not finish {count} actions: {queue.snapshot()}")

    def test_fifo_single_worker_and_waiting_positions(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        order = []
        def execute(job):
            order.append(job["number"])
            if job["number"] == 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test release timed out")
            job["status"] = "done"
        queue = self.queue(execute)
        first = queue.enqueue({"kind": "test", "number": 1})
        queue.start()
        self.assertTrue(entered.wait(2))
        second = queue.enqueue({"kind": "test", "number": 2})
        third = queue.enqueue({"kind": "test", "number": 3})
        readings = {j["id"]: j for j in queue.snapshot()["actions"]}
        self.assertEqual(order, [1])
        self.assertEqual(readings[first]["status"], "running")
        self.assertEqual(readings[second]["queue_position"], 1)
        self.assertEqual(readings[third]["queue_position"], 2)
        release.set()
        self.wait_done(queue, 3)
        self.assertEqual(order, [1, 2, 3])
        self.assertEqual(len({first, second, third}), 3)

    def test_failure_is_visible_and_next_action_executes(self):
        def execute(job):
            if job["number"] == 1:
                raise ValueError("synthetic execution error")
            job["status"] = "done"
        queue = self.queue(execute)
        first = queue.enqueue({"kind": "test", "number": 1})
        second = queue.enqueue({"kind": "test", "number": 2})
        with self.assertLogs(level="ERROR"):
            queue.start()
            self.wait_done(queue, 2)
        self.assertEqual(queue.jobs[first]["status"], "error")
        self.assertIn("synthetic execution error", queue.jobs[first]["message"])
        self.assertEqual(queue.jobs[second]["status"], "done")

    def test_restart_preserves_pending_and_fails_running_without_replay(self):
        initial = self.queue(lambda job: None)
        first = initial.enqueue({"kind": "test", "request": {"target": "one"}})
        second = initial.enqueue({"kind": "test", "request": {"target": "two"}})
        with sqlite3.connect(self.path) as conn:
            job = dict(initial.jobs[first], status="running")
            conn.execute("UPDATE actions SET state='running',job=? WHERE id=?",
                         (json.dumps(job), first))
        called = []
        def execute(job):
            called.append(job["id"])
            self.assertEqual(job["request"]["target"], "two")
            job["status"] = "done"
        recovered = self.queue(execute)
        self.assertEqual(recovered.jobs[first]["status"], "error")
        self.assertIn("not retried", recovered.jobs[first]["message"])
        self.assertEqual(recovered.jobs[second]["status"], "queued")
        self.assertTrue(all("request" not in j for j in recovered.snapshot()["actions"]))
        recovered.start()
        self.wait_done(recovered, 2)
        self.assertEqual(called, [second])

    def test_stop_leaves_pending_on_disk_and_rejects_new_actions(self):
        queue = self.queue(lambda job: None)
        first = queue.enqueue({"kind": "test"})
        queue.stop()
        with self.assertRaisesRegex(RuntimeError, "stopping"):
            queue.enqueue({"kind": "test"})
        recovered = self.queue(lambda job: None)
        self.assertEqual(recovered.jobs[first]["status"], "queued")

    def test_approval_is_terminal_persistent_and_does_not_block_the_queue(self):
        called = []
        def execute(job):
            called.append(job["number"])
            if job["number"] == 1:
                job.update(status="approval_required", approval={"token": "a" * 64})
            else:
                job["status"] = "done"
        queue = self.queue(execute)
        first = queue.enqueue({"kind": "workspace-action", "number": 1})
        queue.enqueue({"kind": "test", "number": 2})
        queue.start()
        self.wait_done(queue, 2)
        self.assertEqual(called, [1, 2])
        queue.stop()
        queue.join()
        recovered = self.queue(lambda job: self.fail("approval was replayed"))
        self.assertEqual(recovered.jobs[first]["status"], "approval_required")
        self.assertEqual(recovered.jobs[first]["approval"]["token"], "a" * 64)

    def test_recent_history_is_bounded_but_pending_not_hidden(self):
        queue = self.queue(lambda job: None)
        for i in range(25):
            ident = queue.enqueue({"kind": "test"})
            with sqlite3.connect(self.path) as conn:
                job = dict(queue.jobs[ident], status="done")
                conn.execute("UPDATE actions SET state='done',job=? WHERE id=?",
                             (json.dumps(job), ident))
            queue.jobs[ident] = job
        waiting = queue.enqueue({"kind": "test"})
        readings = queue.snapshot()["actions"]
        self.assertEqual(len(readings), 21)
        self.assertEqual(readings[0]["id"], waiting)

    def test_concurrent_submissions_have_unique_ids_and_execute_in_committed_order(self):
        order = []
        def execute(job):
            order.append(job["id"])
            job["status"] = "done"
        queue = self.queue(execute)
        threads = [threading.Thread(target=queue.enqueue, args=({"kind": "test"},))
                   for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        expected = [job["id"] for job in queue.snapshot()["actions"]]
        self.assertEqual(len(set(expected)), 12)
        queue.start()
        self.wait_done(queue, 12)
        self.assertEqual(order, expected)

    def test_infrastructure_failure_is_logged_and_reported_without_consuming_pending(self):
        queue = self.queue(lambda job: None)
        ident = queue.enqueue({"kind": "test"})
        with patch.object(queue, "_work_loop", side_effect=OSError("disk unavailable")), \
                self.assertLogs(level="ERROR"):
            queue.start()
            queue.join()
        reading = queue.snapshot()
        self.assertIn("disk unavailable", reading["error"])
        self.assertEqual(reading["actions"][0]["id"], ident)
        self.assertEqual(reading["actions"][0]["status"], "queued")
        with self.assertRaises(RuntimeError):
            queue.enqueue({"kind": "test"})


class IntegrationTests(unittest.TestCase):
    def test_all_starters_submit_serializable_descriptors_without_threads(self):
        captured = []
        def enqueue(job):
            captured.append(json.loads(json.dumps(job)))
            return "test-action"
        with patch.object(portal, "enqueue_action", side_effect=enqueue), \
                patch.object(portal._threading, "Thread") as thread, \
                patch.object(portal.wm, "record", return_value={"slot": "one"}), \
                patch.object(portal, "prepare_pldm_job", return_value=(None, "dry run OK")):
            self.assertEqual(portal.start_pr_scan_job([1]), "test-action")
            self.assertEqual(portal.start_review_scan_job(["T-1"]), "test-action")
            self.assertEqual(portal.start_vm_refresh_job("machine"), "test-action")
            self.assertEqual(portal.start_alignment_job("T-1", "one"), "test-action")
            self.assertEqual(portal.start_workspace_job("sync", "one"), "test-action")
            self.assertEqual(portal.start_pldm_job({"id": 1, "name": "test"}, {})[0], "test-action")
        thread.assert_not_called()
        self.assertEqual([j["kind"] for j in captured], [
            "pr-scan", "review-scan", "vm-refresh", "align", "workspace-action", "pldm"])

    def test_scan_lock_taken_at_execution_not_enqueue(self):
        job = {"kind": "pr-scan", "request": {"only": [1]}}
        with patch.object(portal, "PR_SCAN_LOCK") as lock, \
                patch.object(portal, "run_pr_scan") as run:
            portal.execute_action(job)
        lock.acquire.assert_called_once_with()
        run.assert_called_once_with(job, [1])

    def test_alignment_revalidates_task_holder_before_checkout(self):
        with patch.object(portal.wm, "get_holder", return_value={"task_id": "T-other"}), \
                patch.object(portal, "run_workspace_alignment") as run:
            with self.assertRaisesRegex(ValueError, "assignment changed"):
                portal.execute_action({"kind": "align", "slot": "one", "task_id": "T-1"})
        run.assert_not_called()

    def test_pldm_reresolves_feature_and_reserves_only_at_execution(self):
        queued = {"kind": "pldm", "feature_id": 1, "request": {"slot": "one"}}
        current = {"id": 1, "name": "Fresh feature"}
        with patch.object(portal.pm, "records", return_value=[current]), \
                patch.object(portal, "prepare_pldm_job",
                             return_value=({"slot": "one", "held": True}, "reserved")) as prepare, \
                patch.object(portal, "run_pldm_submit") as submit:
            portal.execute_action(queued)
        prepare.assert_called_once_with(current, {"slot": "one"})
        submit.assert_called_once_with(queued)
        self.assertTrue(queued["held"])


if __name__ == "__main__":
    unittest.main()
