"""Deadline-driven EC2 observations; machine state remains owned by vm-delegate."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import sys
import threading
import time


class PowerSchedule:
    def __init__(self, path: Path, read, probe, settings, operation_lock, changed,
                 refresh_s: float = 60, clock=time.time):
        self.path = path
        self.read = read
        self.probe = probe
        self.settings = settings
        self.operation_lock = operation_lock
        self.changed = changed
        self.refresh_s = refresh_s
        self.clock = clock
        self.wake = threading.Event()
        self._lock = threading.Lock()
        self.error = ""
        self.rows = {}
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            data = {"schema": 1, "rows": {}}
        if not isinstance(data, dict) or data.get("schema") != 1 \
                or not isinstance(data.get("rows"), dict):
            raise ValueError("Invalid power scheduler state")
        for ident, row in data["rows"].items():
            if not isinstance(ident, str) or not isinstance(row, dict) \
                    or not isinstance(row.get("token"), str) \
                    or type(row.get("attempts")) is not int or row["attempts"] < 0 \
                    or any(not isinstance(row.get(k), (int, float))
                           or not math.isfinite(row[k]) for k in ("due", "seen", "last_attempt")) \
                    or not isinstance(row.get("error"), str):
                raise ValueError("Invalid power scheduler attempt record")
        self.rows = data["rows"]

    def snapshot(self):
        with self._lock:
            return {"error": self.error, "instances": copy.deepcopy(self.rows)}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as out:
            os.chmod(tmp, 0o600)
            json.dump({"schema": 1, "rows": self.rows}, out, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, self.path)

    def _sync(self, snapshot, cfg, now):
        rows = {}
        seen = snapshot.get("age") or 0
        for machine in snapshot.get("machines", []):
            ident, state = machine.get("id"), machine.get("state")
            if not ident or state not in ("running", "pending", "stopping", "shutting-down"):
                continue
            old = self.rows.get(ident)
            expiry = machine.get("stop_ts")
            if state in ("pending", "stopping", "shutting-down"):
                # Repeated observations of the same transition must not reset
                # the retry budget, including running -> stopping at lease end.
                token = old["token"] if old else "transition:" + state
                due = now + cfg["power_retry_s"]
            elif type(expiry) in (int, float) and math.isfinite(expiry) and expiry > 0:
                token = "lease:" + str(expiry)
                due = expiry + cfg["power_grace_s"]
            else:
                continue
            row = copy.deepcopy(old) if old and old["token"] == token else {
                "token": token, "attempts": 0, "due": due, "seen": seen,
                "last_attempt": 0, "error": ""}
            if row["token"].startswith("lease:") and row["attempts"] == 0:
                row["due"] = due if state == "running" else row["due"]
            if row["attempts"]:
                row["due"] = row["last_attempt"] + cfg["power_retry_s"]
            if seen > row["seen"]:
                # A newer explicit owner observation allows a fresh bounded
                # reconciliation. Our own observations update seen in step().
                row.update(attempts=0, due=due, seen=seen, last_attempt=0, error="")
            rows[ident] = row
        self.rows = rows

    def step(self):
        cfg = self.settings()
        if not cfg["power_checks"]:
            return self.refresh_s
        if not self.operation_lock.acquire(blocking=False):
            return min(self.refresh_s, cfg["power_retry_s"])
        try:
            now = self.clock()
            with self._lock:
                before = copy.deepcopy(self.rows)
                self._sync(self.read(), cfg, now)
                if before != self.rows or self.error:
                    self._save()
                due = {ident: row["token"] for ident, row in self.rows.items()
                       if row["due"] <= now
                       and row["attempts"] < 1 + cfg["power_retry_limit"]}
                if due:
                    for ident in due:
                        self.rows[ident]["attempts"] += 1
                        self.rows[ident]["last_attempt"] = now
                        self.rows[ident]["due"] = now + cfg["power_retry_s"]
                        self.rows[ident]["error"] = "Power check interrupted or awaiting confirmation"
                    # Persist before the external call so a restart cannot
                    # replay an attempt whose outcome was interrupted.
                    self._save()
            if due:
                try:
                    result = self.probe()
                    error = result.get("error", "")
                except Exception as exc:
                    error = f"Power check failed: {exc}"
                    print(f"[portal/power] {error}", file=sys.stderr)
                with self._lock:
                    observed = self.read()
                    for ident in due:
                        if ident in self.rows:
                            self.rows[ident]["seen"] = observed.get("age") or 0
                    self._sync(observed, cfg, self.clock())
                    for ident, token in due.items():
                        row = self.rows.get(ident)
                        if row and row["token"] == token:
                            row["last_attempt"] = self.clock()
                            row["due"] = row["last_attempt"] + cfg["power_retry_s"]
                            row["error"] = error or "Expected power transition not confirmed"
                            if row["attempts"] >= 1 + cfg["power_retry_limit"]:
                                row["error"] += "; automatic checks exhausted, use Check VM status"
                    self._save()
            with self._lock:
                self.error = ""
                changed = before != self.rows or bool(due)
                future = [row["due"] for row in self.rows.values()
                          if row["attempts"] < 1 + cfg["power_retry_limit"]]
            if changed:
                self.changed()
            return min(self.refresh_s, max(0.1, min(future) - self.clock())) if future \
                else self.refresh_s
        finally:
            self.operation_lock.release()

    def run(self, stop):
        while not stop.is_set():
            self.wake.clear()
            try:
                delay = self.step()
            except Exception as exc:
                error = f"Power scheduler unavailable: {exc}"
                print(f"[portal/power] {error}", file=sys.stderr)
                with self._lock:
                    new_error = self.error != error
                    self.error = error
                if new_error:
                    self.changed()
                delay = self.refresh_s
            self.wake.wait(delay)

    def shutdown(self):
        self.wake.set()
