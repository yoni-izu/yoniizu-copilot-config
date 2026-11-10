"""Durable, single-worker FIFO for portal-owned action execution."""
from __future__ import annotations

import copy
from _thread import LockType
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Callable
import uuid


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ActionQueue:
    def __init__(self, path: Path, execute: Callable[[dict], None],
                 jobs: dict[str, dict], lock: LockType):
        self.path = path
        self.execute = execute
        self.jobs = jobs
        self.lock = lock
        self._condition = threading.Condition()
        self._stopping = False
        self._failure: str | None = None
        self._thread: threading.Thread | None = None
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
                with self.lock:
                    self.jobs[ident] = job

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
            self._condition.notify_all()
            return job["id"]

    def snapshot(self) -> dict:
        with self._condition, self._connect() as conn:
            ids = [row[0] for row in conn.execute(
                "SELECT id FROM actions ORDER BY sequence")]
            with self.lock:
                jobs = [copy.deepcopy(self.jobs[ident]) for ident in ids]
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
            if self._failure:
                result["error"] = self._failure
            return result

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                raise RuntimeError("Action worker already started")
            self._thread = threading.Thread(target=self._work, name="portal-action-queue",
                                            daemon=True)
            self._thread.start()

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()

    def join(self, timeout: float = 5) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _work(self) -> None:
        try:
            self._work_loop()
        except Exception as error:
            logging.exception("Portal action queue stopped unexpectedly")
            with self._condition:
                self._failure = f"Action queue stopped: {error}. Repair storage and restart the portal."
                self._stopping = True

    def _work_loop(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    return
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT id FROM actions WHERE state='queued' ORDER BY sequence LIMIT 1"
                    ).fetchone()
                    if row is not None:
                        with self.lock:
                            job = self.jobs[row[0]]
                            job.update(status="running", started=stamp())
                            self._write(conn, job)
                if row is None:
                    self._condition.wait()
                    continue
            try:
                self.execute(job)
                if job["status"] not in {"done", "error", "approval_required"}:
                    raise RuntimeError("Action worker returned without a terminal result")
            except Exception as error:
                logging.exception("Portal action %s failed", job["id"])
                with self.lock:
                    job.update(status="error", message=str(error), current=None)
            with self._condition, self._connect() as conn, self.lock:
                job["finished"] = stamp()
                self._write(conn, job)
