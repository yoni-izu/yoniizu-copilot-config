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

    def queue(self, execute, jobs=None, **options):
        queue = ActionQueue(self.path, execute, jobs if jobs is not None else {},
                            threading.Lock(), **options)
        self.queues.append(queue)
        return queue

    def wait_done(self, queue, count):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with sqlite3.connect(self.path) as conn:
                finished = conn.execute(
                    "SELECT count(*) FROM actions WHERE state IN "
                    "('done','error','cancelled','approval_required')").fetchone()[0]
            if finished == count:
                return
            time.sleep(.01)
        self.fail(f"Queue did not finish {count} actions: {queue.snapshot()}")

    def wait_reading(self, queue, ident, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = next(j for j in queue.snapshot()["actions"] if j["id"] == ident)
            if predicate(job):
                return job
            time.sleep(.01)
        self.fail(f"Action reading did not match: {job}")

    def test_independent_actions_overlap_and_running_is_durable_before_execution(self):
        release = threading.Event()
        self.addCleanup(release.set)
        entered = [threading.Event(), threading.Event()]
        def execute(job):
            with sqlite3.connect(self.path) as conn:
                self.assertEqual(conn.execute(
                    "SELECT state FROM actions WHERE id=?", (job["id"],)).fetchone()[0],
                    "running")
            entered[job["number"]].set()
            if not release.wait(5):
                raise TimeoutError("test release timed out")
            job["status"] = "done"
        queue = self.queue(execute, resources=lambda job: {job["resource"]})
        for number in range(2):
            queue.enqueue({"kind": "test", "number": number, "resource": f"workspace:{number}"})
        queue.start()
        self.assertTrue(all(event.wait(2) for event in entered))
        self.assertEqual(sum(j["status"] == "running" for j in queue.snapshot()["actions"]), 2)
        release.set()
        self.wait_done(queue, 2)
        self.assertTrue(all(j["status"] == "done" for j in queue.snapshot()["actions"]))

    def test_metadata_guard_excludes_pending_conflicts_but_allows_independent_resources(self):
        queue = self.queue(lambda job: None, resources=lambda job: {job["resource"]})
        queue.enqueue({"kind": "test", "resource": "workspace:a"})
        with self.assertRaisesRegex(ValueError, "queued or running"):
            with queue.idle_resources({"workspace:a"}):
                self.fail("conflicting metadata write admitted")
        with queue.idle_resources({"workspace:b"}):
            pass
        with self.assertRaisesRegex(ValueError, "queued or running"):
            with queue.idle_resources({"workspace:*"}):
                self.fail("wildcard conflict admitted")

    def test_metadata_guard_excludes_running_claims(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def execute(job):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test release timed out")
            job["status"] = "done"
        queue = self.queue(execute, resources=lambda job: {job["resource"]})
        queue.enqueue({"kind": "test", "resource": "workspace:a"})
        queue.start()
        self.assertTrue(entered.wait(2))
        with self.assertRaisesRegex(ValueError, "queued or running"):
            with queue.idle_resources({"workspace:a"}):
                self.fail("running conflict admitted")
        with queue.idle_resources({"workspace:b"}):
            pass
        release.set()
        self.wait_done(queue, 1)

    def test_conflicting_batches_keep_fifo_without_blocking_independent_work(self):
        release_first, release_batch = threading.Event(), threading.Event()
        self.addCleanup(release_first.set)
        self.addCleanup(release_batch.set)
        entered = {name: threading.Event() for name in ("first", "batch", "later", "other")}
        def execute(job):
            name = job["name"]
            entered[name].set()
            gate = {"first": release_first, "batch": release_batch}.get(name)
            if gate and not gate.wait(5):
                raise TimeoutError("test release timed out")
            job["status"] = "done"
        queue = self.queue(execute, resources=lambda job: set(job["resources"]))
        first = queue.enqueue({"kind": "sync", "name": "first", "resources": ["workspace:a"]})
        batch = queue.enqueue({"kind": "refresh", "name": "batch",
                               "resources": ["workspace:a", "workspace:b"]})
        later = queue.enqueue({"kind": "align", "name": "later", "resources": ["workspace:b"]})
        queue.enqueue({"kind": "other", "name": "other", "resources": ["workspace:c"]})
        queue.start()
        self.assertTrue(entered["first"].wait(2))
        self.assertTrue(entered["other"].wait(2))
        self.assertFalse(entered["batch"].is_set())
        self.assertFalse(entered["later"].is_set())
        waiting = self.wait_reading(queue, batch, lambda j: first in j.get("blocked_by", []))
        self.assertIn("conflicting", waiting["waiting_reason"])
        self.assertIn(batch, self.wait_reading(
            queue, later, lambda j: batch in j.get("blocked_by", []))["blocked_by"])
        release_first.set()
        self.assertTrue(entered["batch"].wait(2))
        self.assertFalse(entered["later"].is_set())
        release_batch.set()
        self.wait_done(queue, 4)
        self.assertTrue(entered["later"].is_set())

    def test_worker_limit_is_enforced_for_disjoint_resources(self):
        release = threading.Event()
        self.addCleanup(release.set)
        entered = [threading.Event() for _ in range(4)]
        def execute(job):
            entered[job["number"]].set()
            if not release.wait(5):
                raise TimeoutError("test release timed out")
            job["status"] = "done"
        queue = self.queue(execute, resources=lambda job: {f"workspace:{job['number']}"})
        ids = [queue.enqueue({"kind": "test", "number": number}) for number in range(4)]
        queue.start()
        self.assertTrue(all(event.wait(2) for event in entered[:3]))
        waiting = self.wait_reading(queue, ids[3], lambda j: bool(j.get("waiting_reason")))
        self.assertEqual(waiting["waiting_reason"], "Waiting for an execution slot")
        self.assertEqual(waiting["blocked_by"], [])
        self.assertEqual(queue.snapshot()["max_workers"], 3)
        self.assertFalse(entered[3].is_set())
        release.set()
        self.wait_done(queue, 4)

    def test_namespace_wildcard_excludes_all_members_but_not_other_domains(self):
        from action_queue import conflicts
        def clash(left, right):
            return conflicts(frozenset(left), frozenset(right))
        for left, right in [({"workspace:*"}, {"workspace:a"}),
                            ({"workspace:a"}, {"workspace:*"}),
                            ({"member:one"}, {"member:one"}),
                            ({"*"}, {"vm:one"}), ({"vm:one"}, {"*"})]:
            self.assertTrue(clash(left, right))
        self.assertFalse(clash({"workspace:*"}, {"vm:one"}))
        self.assertFalse(clash({"member:one"}, {"member:two"}))
        self.assertFalse(clash({"workspace:a"}, {"workspace:ab"}))

    def test_global_claim_is_a_fair_barrier(self):
        release = threading.Event()
        self.addCleanup(release.set)
        entered = threading.Event()
        order = []
        def execute(job):
            order.append(job["number"])
            if job["number"] == 0:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test release timed out")
            job["status"] = "done"
        queue = self.queue(execute, resources=lambda job: {job["resource"]})
        queue.enqueue({"kind": "test", "number": 0, "resource": "workspace:a"})
        barrier = queue.enqueue({"kind": "unknown", "number": 1, "resource": "*"})
        later = queue.enqueue({"kind": "test", "number": 2, "resource": "vm:b"})
        queue.start()
        self.assertTrue(entered.wait(2))
        self.wait_reading(queue, later, lambda j: barrier in j.get("blocked_by", []))
        self.assertEqual(order, [0])
        release.set()
        self.wait_done(queue, 3)
        self.assertEqual(order, [0, 1, 2])

    def test_resolution_failure_is_explicit_and_unrelated_jobs_still_execute(self):
        def resolve(job):
            if job["number"] == 0:
                raise ValueError("workspace no longer tracked")
            return {"workspace:b"}
        def execute(job):
            job["status"] = "done"
        queue = self.queue(execute, resources=resolve)
        failed = queue.enqueue({"kind": "test", "number": 0})
        queue.enqueue({"kind": "test", "number": 1})
        with self.assertLogs(level="ERROR"):
            queue.start()
            self.wait_done(queue, 2)
        self.assertIn("workspace no longer tracked", queue.jobs[failed]["message"])
        self.assertEqual(queue.jobs[failed]["status"], "error")

    def test_stop_waits_for_active_workers_and_retains_conflicting_pending(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def execute(job):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test release timed out")
            job["status"] = "done"
        queue = self.queue(execute, resources=lambda job: {"workspace:a"})
        first = queue.enqueue({"kind": "test"})
        waiting = queue.enqueue({"kind": "test"})
        queue.start()
        self.assertTrue(entered.wait(2))
        queue.stop()
        release.set()
        queue.join()
        self.assertFalse(queue._workers)
        self.assertEqual(queue.jobs[first]["status"], "done")
        self.assertEqual(queue.jobs[waiting]["status"], "queued")

    def test_claims_are_recomputed_for_recovered_pending_actions(self):
        queue = self.queue(lambda job: None)
        ident = queue.enqueue({"kind": "test"})
        resolver = unittest.mock.Mock(return_value={"workspace:current"})
        def execute(job):
            job["status"] = "done"
        recovered = self.queue(execute, resources=resolver)
        recovered.start()
        self.wait_done(recovered, 1)
        resolver.assert_called_once()
        self.assertEqual(resolver.call_args.args[0]["id"], ident)

    def test_terminal_progress_does_not_release_claims_or_report_idle_before_return(self):
        release, entered, later_entered = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def execute(job):
            job["status"] = "done"
            if job["number"] == 0:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test release timed out")
            else:
                later_entered.set()
        queue = self.queue(execute, resources=lambda job: {"workspace:a"})
        first = queue.enqueue({"kind": "test", "number": 0})
        later = queue.enqueue({"kind": "test", "number": 1})
        queue.start()
        self.assertTrue(entered.wait(2))
        readings = {j["id"]: j for j in queue.snapshot()["actions"]}
        self.assertEqual(readings[first]["status"], "running")
        self.assertIsNone(readings[first]["finished"])
        self.assertEqual(readings[later]["status"], "queued")
        self.assertFalse(later_entered.is_set())
        release.set()
        self.wait_done(queue, 2)

    def test_failure_releases_shared_claim_and_preserves_independent_running_action(self):
        release, entered = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def execute(job):
            if job["number"] == 0:
                raise ValueError("synthetic execution error")
            if job["number"] == 2:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test release timed out")
            job["status"] = "done"
        queue = self.queue(execute, resources=lambda job: {
            "workspace:b" if job["number"] == 2 else "workspace:a"})
        failed, following, unrelated = [queue.enqueue({"kind": "test", "number": n})
                                         for n in range(3)]
        with self.assertLogs(level="ERROR"):
            queue.start()
            self.assertTrue(entered.wait(2))
            self.wait_reading(queue, following, lambda j: j["status"] == "done")
        self.assertEqual(queue.jobs[failed]["status"], "error")
        self.assertEqual(queue.jobs[unrelated]["status"], "running")
        release.set()
        self.wait_done(queue, 3)

    def test_restart_fails_every_interrupted_worker_without_replay(self):
        queue = self.queue(lambda job: None)
        ids = [queue.enqueue({"kind": "test", "number": n}) for n in range(4)]
        with sqlite3.connect(self.path) as conn:
            for ident in ids[:3]:
                job = dict(queue.jobs[ident], status="running")
                conn.execute("UPDATE actions SET state='running',job=? WHERE id=?",
                             (json.dumps(job), ident))
        called = []
        def execute(job):
            called.append(job["id"])
            job["status"] = "done"
        recovered = self.queue(execute, resources=lambda job: {"workspace:a"})
        for ident in ids[:3]:
            self.assertEqual(recovered.jobs[ident]["status"], "error")
            self.assertIn("not retried", recovered.jobs[ident]["message"])
        recovered.start()
        self.wait_done(recovered, 4)
        self.assertEqual(called, ids[3:])

    def test_result_storage_failure_stops_dispatch_and_is_explicit(self):
        def execute(job):
            job["status"] = "done"
        queue = self.queue(execute)
        first = queue.enqueue({"kind": "test"})
        later = queue.enqueue({"kind": "test"})
        write = queue._write
        def fail_result(conn, job):
            if job["status"] == "done":
                raise sqlite3.OperationalError("disk unavailable")
            write(conn, job)
        with patch.object(queue, "_write", side_effect=fail_result), \
                self.assertLogs(level="ERROR"):
            queue.start()
            queue.join()
        reading = queue.snapshot()
        self.assertIn("disk unavailable", reading["error"])
        self.assertEqual(queue.jobs[first]["status"], "error")
        self.assertIn("could not be persisted", queue.jobs[first]["message"])
        self.assertEqual(queue.jobs[later]["status"], "queued")
        with self.assertRaises(RuntimeError):
            queue.enqueue({"kind": "test"})

    def test_worker_limit_rejects_invalid_values(self):
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.queue(lambda job: None, max_workers=limit)

    def test_prune_removes_only_old_successful_actions(self):
        jobs = {}
        queue = self.queue(lambda job: None, jobs)
        old, new = "2026-01-01T00:00:00.000+00:00", "2026-11-10T00:00:00.000+00:00"
        rows = [("old-done", "done", old), ("new-done", "done", new),
                ("old-error", "error", old), ("old-approval", "approval_required", old),
                ("old-cancelled", "cancelled", old), ("new-cancelled", "cancelled", new)]
        with sqlite3.connect(self.path) as conn:
            for seq, (ident, state, finished) in enumerate(rows, 1000):
                job = {"id": ident, "status": state, "finished": finished, "kind": "pr-scan"}
                conn.execute("INSERT INTO actions(sequence,id,state,job) VALUES(?,?,?,?)",
                             (seq, ident, state, json.dumps(job)))
                jobs[ident] = job
        from datetime import datetime, timezone
        result = queue.prune(90, now=datetime(2026, 11, 12, tzinfo=timezone.utc))
        self.assertEqual(result["removed"], 2)
        with sqlite3.connect(self.path) as conn:
            left = {r[0] for r in conn.execute("SELECT id FROM actions")}
        self.assertEqual(left, {"new-done", "old-error", "old-approval", "new-cancelled"})
        self.assertNotIn("old-done", jobs)
        self.assertIn("old-error", jobs)
        with self.assertRaises(ValueError):
            queue.prune(0)

    def test_cancel_queued_action_is_never_dispatched_and_survives_restart(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        called = []
        def execute(job):
            called.append(job["number"])
            if job["number"] == 1:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test release timed out")
            job["status"] = "done"
        queue = self.queue(execute)
        queue.enqueue({"kind": "test", "number": 1})
        queue.start()
        self.assertTrue(entered.wait(2))
        second = queue.enqueue({"kind": "test", "number": 2})
        third = queue.enqueue({"kind": "test", "number": 3})
        self.assertEqual(queue.cancel(second), "cancelled")
        reading = next(j for j in queue.snapshot()["actions"] if j["id"] == second)
        self.assertEqual(reading["status"], "cancelled")
        self.assertIn("before it started", reading["message"])
        self.assertTrue(reading["finished"])
        third_reading = next(j for j in queue.snapshot()["actions"] if j["id"] == third)
        self.assertEqual(third_reading["queue_position"], 1)
        with self.assertRaisesRegex(ValueError, "already cancelled"):
            queue.cancel(second)
        release.set()
        self.wait_done(queue, 3)
        self.assertEqual(called, [1, 3])
        queue.stop()
        queue.join()
        recovered = self.queue(lambda job: self.fail("cancelled action was replayed"))
        self.assertEqual(recovered.jobs[second]["status"], "cancelled")

    def test_cancel_unknown_finished_and_approval_actions(self):
        def execute(job):
            if job["number"] == 1:
                job.update(status="approval_required", approval={"token": "a" * 64})
            else:
                job["status"] = "done"
        queue = self.queue(execute)
        first = queue.enqueue({"kind": "workspace-action", "number": 1})
        second = queue.enqueue({"kind": "test", "number": 2})
        queue.start()
        self.wait_done(queue, 2)
        with self.assertRaises(LookupError):
            queue.cancel("action-missing")
        with self.assertRaisesRegex(ValueError, "already done"):
            queue.cancel(second)
        self.assertEqual(queue.cancel(first), "cancelled")
        self.assertIsNone(queue.jobs[first]["approval"])
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT state FROM actions WHERE id=?",
                                          (first,)).fetchone()[0], "cancelled")

    def test_running_cancel_needs_the_interrupt_hook(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def execute(job):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test release timed out")
            job["status"] = "done"
        queue = self.queue(execute)
        ident = queue.enqueue({"kind": "test"})
        queue.start()
        self.assertTrue(entered.wait(2))
        with self.assertRaisesRegex(ValueError, "cannot be interrupted"):
            queue.cancel(ident)
        release.set()
        self.wait_done(queue, 1)
        self.assertEqual(queue.jobs[ident]["status"], "done")

    def test_running_cancel_interrupts_and_finishes_cancelled(self):
        entered, stop = threading.Event(), threading.Event()
        self.addCleanup(stop.set)
        interrupted = []
        def interrupt(job):
            if job["kind"] != "ai":
                return False
            interrupted.append(job["id"])
            stop.set()
            return True
        def execute(job):
            entered.set()
            if job["kind"] == "ai":
                stop.wait(5)
                job.update(status="error", message="copilot killed")
            else:
                job["status"] = "done"
        queue = self.queue(execute, interrupt=interrupt)
        ident = queue.enqueue({"kind": "ai"})
        queue.start()
        self.assertTrue(entered.wait(2))
        self.assertEqual(queue.cancel(ident), "cancelling")
        self.assertEqual(queue.cancel(ident), "cancelling")
        self.wait_done(queue, 1)
        self.assertEqual(interrupted, [ident])
        job = queue.jobs[ident]
        self.assertEqual(job["status"], "cancelled")
        self.assertIn("copilot killed", job["message"])
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute("SELECT state FROM actions WHERE id=?",
                                          (ident,)).fetchone()[0], "cancelled")

    def test_running_cancel_that_loses_the_race_keeps_the_success(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def execute(job):
            entered.set()
            release.wait(5)
            job["status"] = "done"
        queue = self.queue(execute, interrupt=lambda job: True)
        ident = queue.enqueue({"kind": "ai"})
        queue.start()
        self.assertTrue(entered.wait(2))
        self.assertEqual(queue.cancel(ident), "cancelling")
        release.set()
        self.wait_done(queue, 1)
        self.assertEqual(queue.jobs[ident]["status"], "done")

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

    def test_used_and_superseded_workspace_approvals_are_settled(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        jobs, lock = {}, threading.Lock()

        def execute(job):
            with lock:
                if job["request"]["payload"].get("force"):
                    job.update(status="done", message="free complete")
                else:
                    job.update(status="approval_required",
                               approval={"token": "a" * 64, "blockers": []})

        queue = ActionQueue(Path(tmp.name) / "actions.db", execute, jobs, lock)
        queue.start()
        self.addCleanup(queue.join)
        self.addCleanup(queue.stop)

        def wait(ident, status):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with lock:
                    if jobs[ident]["status"] == status:
                        return
                time.sleep(.01)
            self.fail(f"{ident} never reached {status}: {jobs[ident]}")

        with patch.object(portal, "ACTION_QUEUE", queue), patch.object(portal, "ALIGN_JOBS", jobs), \
                patch.object(portal, "ALIGN_LOCK", lock), \
                patch.object(portal.wm, "record", return_value={"slot": "one"}):
            older = portal.start_workspace_job("free", "one")
            wait(older, "approval_required")
            newer = portal.start_workspace_job("free", "one")
            wait(newer, "approval_required")
            self.assertEqual(jobs[older]["status"], "done")
            self.assertIn("Superseded", jobs[older]["message"])
            with self.assertRaisesRegex(ValueError, "no longer available"):
                portal.start_workspace_job("free", "one", {"force": True, "approval_job": older})
            forced = portal.start_workspace_job("free", "one", {"force": True, "approval_job": newer})
            wait(forced, "done")
        self.assertEqual(jobs[newer]["status"], "done")
        self.assertIsNone(jobs[newer]["approval"])
        self.assertIn(f"Confirmation used by {forced}", jobs[newer]["message"])
        with sqlite3.connect(Path(tmp.name) / "actions.db") as conn:
            state = conn.execute("SELECT state FROM actions WHERE id=?", (newer,)).fetchone()[0]
        self.assertEqual(state, "done")

    def test_scan_lock_taken_at_execution_not_enqueue(self):
        job = {"kind": "pr-scan", "request": {"only": [1]}}
        with patch.object(portal, "PR_SCAN_LOCK") as lock, \
                patch.object(portal, "run_pr_scan") as run:
            portal.execute_action(job)
        lock.acquire.assert_called_once_with()
        run.assert_called_once_with(job, [1])

    def test_alignment_revalidates_task_holder_before_checkout(self):
        with patch.object(portal.wm, "get_holder", return_value={"task_id": "T-other"}), \
                patch.object(portal, "action_resources", return_value={"workspace:one"}), \
                patch.object(portal, "run_workspace_alignment") as run:
            with self.assertRaisesRegex(ValueError, "assignment changed"):
                portal.execute_action({"kind": "align", "slot": "one", "task_id": "T-1"})
        run.assert_not_called()

    def test_pldm_reresolves_feature_and_reserves_only_at_execution(self):
        queued = {"kind": "pldm", "feature_id": 1, "request": {"slot": "one"}}
        current = {"id": 1, "name": "Fresh feature"}
        with patch.object(portal.pm, "records", return_value=[current]), \
                patch.object(portal, "action_resources", return_value={"workspace:one"}), \
                patch.object(portal, "prepare_pldm_job",
                             return_value=({"slot": "one", "held": True}, "reserved")) as prepare, \
                patch.object(portal, "run_pldm_submit") as submit:
            portal.execute_action(queued)
        prepare.assert_called_once_with(current, {"slot": "one"})
        submit.assert_called_once_with(queued)
        self.assertTrue(queued["held"])

    def test_portal_resources_use_canonical_ids_remote_targets_and_mirror_sources(self):
        rec = {"id": "WS-R-test", "target": "machine", "slot": "remote"}
        with patch.object(portal.wm, "record", return_value=rec), \
                patch.object(portal.wm, "status_read",
                             return_value={"mirror": {"source_id": "WS-L-source"}}):
            claims = portal.action_resources({
                "kind": "workspace-action", "action": "sync",
                "request": {"slots": ["remote/fw"], "payload": {}}})
            alias_claims = portal.action_resources({
                "kind": "workspace-action", "action": "refresh",
                "request": {"slots": ["WS-R-test"], "payload": {}}})
        self.assertEqual(claims, {"workspace:WS-R-test", "vm:machine", "workspace:WS-L-source"})
        self.assertEqual(claims, alias_claims)

    def test_portal_batches_and_cross_kind_shared_domains(self):
        def record(slot):
            return {"id": "id-" + slot, "slot": slot, "target": ""}
        with patch.object(portal.wm, "record", side_effect=record), \
                patch.object(portal.wm, "status_read", return_value={}):
            batch = portal.action_resources({
                "kind": "workspace-action", "action": "refresh",
                "request": {"slots": ["one", "two"]}})
            align = portal.action_resources({"kind": "align", "slot": "one"})
            free = portal.action_resources({
                "kind": "workspace-action", "action": "free", "request": {"slots": ["two"]}})
            occupy = portal.action_resources({
                "kind": "workspace-action", "action": "occupy", "request": {"slots": ["two"]}})
            pldm = portal.action_resources({"kind": "pldm", "request": {"slot": "one"}})
        self.assertEqual(batch, {"workspace:id-one", "workspace:id-two"})
        self.assertIn("domain:tasks", align & free & occupy)
        self.assertIn("workspace:*", occupy)
        self.assertIn("workspace:id-one", pldm & align)
        self.assertIn("domain:prs", pldm & portal.action_resources({"kind": "pr-scan"}))
        self.assertEqual(portal.action_resources({"kind": "review-scan"}),
                         {"domain:tasks", "workspace:*"})
        self.assertIn("vm:*", portal.action_resources({"kind": "vm-refresh"}))
        self.assertEqual(portal.action_resources({"kind": "pldm", "request": {}}), {"*"})
        self.assertEqual(portal.action_resources({"kind": "future-member-action"}), {"*"})

    def test_remote_workspace_execution_uses_vm_lock_but_local_execution_does_not(self):
        for claims, remote in [({"workspace:one", "vm:machine"}, True),
                               ({"workspace:one"}, False)]:
            with self.subTest(remote=remote), \
                    patch.object(portal, "action_resources", return_value=claims), \
                    patch.object(portal, "VM_REFRESH_LOCK") as lock, \
                    patch.object(portal, "run_workspace_job") as run:
                job = {"kind": "workspace-action"}
                portal.execute_action(job)
                run.assert_called_once_with(job, None)
                self.assertEqual(lock.__enter__.called, remote)


if __name__ == "__main__":
    unittest.main()
