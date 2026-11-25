"""Durable, bounded execution with FIFO ordering between conflicting actions."""
from __future__ import annotations

import copy
from collections import deque
from _thread import LockType
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Callable
import uuid


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def conflicts(left: frozenset[str], right: frozenset[str]) -> bool:
    if "*" in left or "*" in right:
        return True
    return any(a == b or a == b.split(":")[0] + ":*"
               or b == a.split(":")[0] + ":*" for a in left for b in right)


class ActionQueue:
    def __init__(self, path: Path, execute: Callable[[dict], None],
                 jobs: dict[str, dict], lock: LockType, *,
                 resources: Callable[[dict], set[str]] | None = None,
                 interrupt: Callable[[dict], bool] | None = None,
                 on_transition: Callable[[dict], None] | None = None,
                 max_workers: int = 3):
        if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        self.path = path
        self.execute = execute
        self.jobs = jobs
        self.lock = lock
        self._condition = threading.Condition()
        self._stopping = False
        self._failure: str | None = None
        self._thread: threading.Thread | None = None
        self._resources = resources
        self._interrupt = interrupt
        self.max_workers = max_workers
        self._running: dict[str, frozenset[str]] = {}
        self._workers: set[threading.Thread] = set()
        self._on_transition = on_transition
        self._notifications: deque[dict] = deque()
        self._notifier: threading.Thread | None = None
        recovered = []
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS actions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL, state TEXT NOT NULL, job TEXT NOT NULL)""")
            for ident, state, raw in conn.execute(
                    "SELECT id, state, job FROM actions ORDER BY sequence").fetchall():
                job = json.loads(raw)
                if state == "running":
                    job.update(status="error", finished=stamp(), current=None,
                               message="Interrupted by portal restart; not retried. "
                                       "Check external state before submitting again.")
                    self._write(conn, job)
                    recovered.append(copy.deepcopy(job))
                with self.lock:
                    self.jobs[ident] = job
        for job in recovered:
            self._transition(job)

    def _transition(self, job: dict, status: str | None = None) -> None:
        if self._on_transition is not None:
            snapshot = copy.deepcopy(job)
            if status:
                snapshot["status"] = status
            with self._condition:
                self._notifications.append(snapshot)
                self._condition.notify_all()

    def _notify(self) -> None:
        assert self._on_transition is not None
        while True:
            with self._condition:
                while not self._notifications:
                    if self._stopping and not self._workers:
                        return
                    self._condition.wait()
                job = self._notifications.popleft()
            try:
                self._on_transition(job)
            except Exception:
                logging.exception("Portal notification failed for action %s", job["id"])

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def _write(self, conn, job):
        conn.execute("UPDATE actions SET state=?, job=? WHERE id=?",
                     (job["status"], json.dumps(job), job["id"]))

    def enqueue(self, job: dict) -> str:
        with self._condition:
            if self._stopping:
                raise RuntimeError("Portal queue is stopping; retry after restart")
            job = copy.deepcopy(job)
            job.update(id="action-" + uuid.uuid4().hex, status="queued",
                       queued=stamp(), started=None, finished=None)
            with self._connect() as conn:
                conn.execute("INSERT INTO actions(id,state,job) VALUES(?,?,?)",
                             (job["id"], "queued", json.dumps(job)))
            with self.lock:
                self.jobs[job["id"]] = job
            self._transition(job)
            self._condition.notify_all()
            return job["id"]

    def settle(self, ident: str, message: str) -> bool:
        """Close an unused approval request so its stale confirmation cannot be replayed."""
        with self._condition:
            with self.lock:
                job = self.jobs.get(ident)
                if not job or job.get("status") != "approval_required":
                    return False
                job.update(status="done", message=message, approval=None, current=None)
            with self._connect() as conn, self.lock:
                self._write(conn, job)
            self._transition(job)
        return True

    def cancel(self, ident: str) -> str:
        """Cancel one action; returns ``"cancelled"`` or ``"cancelling"``.

        Queued work and pending approvals are cancelled immediately and never
        dispatched (the check runs under the scheduling lock, so it cannot race
        a dispatch). Running work is cancelled only when the ``interrupt`` hook
        accepts it; the worker then finishes as ``cancelled``. Raises
        ``LookupError`` for an unknown id and ``ValueError`` when the action is
        finished or cannot be interrupted safely.
        """
        with self._condition:
            with self.lock:
                job = self.jobs.get(ident)
                if not job:
                    raise LookupError("unknown action")
                status = job.get("status")
            if ident in self._running or status == "running":
                if job.get("cancel_requested"):
                    return "cancelling"
                if not (self._interrupt and self._interrupt(job)):
                    raise ValueError("this running action cannot be interrupted safely; "
                                     "wait for it to finish")
                with self.lock:
                    job.update(cancel_requested=stamp(), current="Cancelling\u2026")
                    with self._connect() as conn:
                        self._write(conn, {**job, "status": "running"})
                    self._transition(job, "cancelling")
                return "cancelling"
            if status not in {"queued", "approval_required"}:
                raise ValueError(f"action is already {status}")
            with self.lock:
                job.pop("blocked_by", None)
                job.pop("waiting_reason", None)
                job.update(status="cancelled", finished=stamp(), current=None,
                           approval=None,
                           message=("Cancelled before it started" if status == "queued"
                                    else "Cancelled instead of approving"))
            with self._connect() as conn, self.lock:
                self._write(conn, job)
            self._transition(job)
            self._condition.notify_all()
        return "cancelled"

    def snapshot(self) -> dict:
        with self._condition, self._connect() as conn:
            ids = [row[0] for row in conn.execute(
                "SELECT id FROM actions ORDER BY sequence")]
            with self.lock:
                jobs = [copy.deepcopy(self.jobs[ident]) for ident in ids]
            for job in jobs:
                if job["id"] in self._running:
                    job.update(status="running", finished=None)
            active = [job for job in jobs if job["status"] in {"queued", "running"}]
            recent = [job for job in jobs if job["status"] not in {"queued", "running"}][-20:]
            position = 0
            for job in active:
                if job["status"] == "queued":
                    position += 1
                    job["queue_position"] = position
            # Execution descriptors are private; the browser needs only job readings.
            for job in active + recent:
                job.pop("request", None)
            result: dict = {"actions": active + list(reversed(recent))}
            result["max_workers"] = self.max_workers
            if self._failure:
                result["error"] = self._failure
            return result

    def prune(self, days: int, now: datetime | None = None) -> dict:
        """Delete done or cancelled actions finished more than ``days`` ago.

        Queued/running work, failures and pending approvals are never removed,
        so recovery evidence survives. Compacts the store afterwards.
        """
        if days < 1:
            raise ValueError("retention must be at least one day")
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat(
            timespec="milliseconds")
        with self._condition:
            with self._connect() as conn:
                rows = conn.execute("SELECT id, job FROM actions WHERE state IN ('done','cancelled')").fetchall()
                expired = [ident for ident, raw in rows
                           if (json.loads(raw).get("finished") or "9999") < cutoff]
                conn.executemany("DELETE FROM actions WHERE id=?", [(i,) for i in expired])
                remaining = conn.execute("SELECT count(*) FROM actions").fetchone()[0]
            with self.lock:
                for ident in expired:
                    self.jobs.pop(ident, None)
            conn = sqlite3.connect(self.path, timeout=30)
            try:
                conn.execute("VACUUM")
            finally:
                conn.close()
        return {"removed": len(expired), "remaining": remaining, "cutoff": cutoff}

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("Action worker already started")
            self._thread = threading.Thread(target=self._work, name="portal-action-queue",
                                            daemon=True)
            if self._on_transition is not None:
                self._notifier = threading.Thread(target=self._notify,
                                                  name="portal-action-notifications", daemon=True)
                self._notifier.start()
            self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()

    def join(self, timeout: float = 5) -> None:
        deadline = time.monotonic() + timeout
        if self._thread is not None:
            self._thread.join(timeout)
        with self._condition:
            workers = list(self._workers)
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
        if self._notifier is not None:
            self._notifier.join(max(0, deadline - time.monotonic()))

    def _claims(self, job: dict) -> frozenset[str]:
        claims = self._resources(job) if self._resources else {"*"}
        if not claims or any(not isinstance(key, str) or not key for key in claims):
            raise ValueError("Action resources must be non-empty strings")
        return frozenset(claims)

    @contextmanager
    def idle_resources(self, resources: set[str]):
        """Keep dispatch out while changing metadata that governs queued work."""
        claims = frozenset(resources)
        with self._condition:
            with self.lock:
                pending = [job for job in self.jobs.values()
                           if job.get("status") in {"queued", "running"}]
            if any(conflicts(claims, held) for held in self._running.values()) or any(
                    conflicts(claims, self._claims(job)) for job in pending):
                raise ValueError("workspace has queued or running actions; wait before changing purpose")
            yield

    def _work(self) -> None:
        try:
            self._work_loop()
        except Exception as error:
            logging.exception("Portal action queue stopped unexpectedly")
            with self._condition:
                self._failure = f"Action queue stopped: {error}. Repair storage and restart the portal."
                self._stopping = True
                self._condition.notify_all()

    def _work_loop(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    return
                ready = []
                transitions = []
                with self._connect() as conn:
                    rows = conn.execute(
                        "SELECT id FROM actions WHERE state='queued' ORDER BY sequence"
                    ).fetchall()
                    earlier: dict[str, frozenset[str]] = {}
                    for ident, in rows:
                        with self.lock:
                            job = self.jobs[ident]
                        try:
                            claims = self._claims(job)
                        except Exception as error:
                            logging.exception("Cannot resolve resources for action %s", ident)
                            with self.lock:
                                job.update(status="error", finished=stamp(), current=None,
                                           message=f"Cannot resolve action resources: {error}")
                                self._write(conn, job)
                                transitions.append(copy.deepcopy(job))
                            continue
                        blockers = [key for key, held in {**self._running, **earlier}.items()
                                    if conflicts(claims, held)]
                        if blockers or len(self._running) >= self.max_workers:
                            with self.lock:
                                job.update(blocked_by=blockers, waiting_reason=(
                                    "Waiting for conflicting actions" if blockers
                                    else "Waiting for an execution slot"))
                            earlier[ident] = claims
                            continue
                        with self.lock:
                            job.pop("blocked_by", None)
                            job.pop("waiting_reason", None)
                            job.update(status="running", started=stamp())
                            self._write(conn, job)
                            transitions.append(copy.deepcopy(job))
                        self._running[ident] = claims
                        ready.append(job)
                # Commit running states before starting any external side effects.
                for snapshot in transitions:
                    self._transition(snapshot)
                for job in ready:
                    worker = threading.Thread(target=self._run, args=(job,),
                                              name=job["id"], daemon=True)
                    self._workers.add(worker)
                    try:
                        worker.start()
                    except Exception:
                        self._workers.remove(worker)
                        raise
                self._condition.wait()

    def _run(self, job: dict) -> None:
        try:
            self.execute(job)
            if job["status"] not in {"done", "error", "cancelled", "approval_required"}:
                raise RuntimeError("Action worker returned without a terminal result")
        except Exception as error:
            logging.exception("Portal action %s failed", job["id"])
            with self.lock:
                job.update(status="error", message=str(error), current=None)
        with self._condition:
            with self.lock:
                if job.get("cancel_requested") and job["status"] == "error":
                    job.update(status="cancelled", current=None,
                               message="Cancelled while running"
                               + (f" ({job['message']})" if job.get("message") else ""))
            try:
                with self._connect() as conn, self.lock:
                    job["finished"] = stamp()
                    self._write(conn, job)
                self._transition(job)
            except Exception as error:
                logging.exception("Portal action result could not be persisted")
                self._failure = f"Action queue stopped: {error}. Repair storage and restart the portal."
                self._stopping = True
                with self.lock:
                    job.update(status="error", current=None, finished=stamp(),
                               message=f"Action result could not be persisted: {error}. "
                                       "Check external state before submitting again.")
            finally:
                self._running.pop(job["id"], None)
                self._workers.discard(threading.current_thread())
                self._condition.notify_all()
