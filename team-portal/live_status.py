#!/usr/bin/env python3
"""Live machine status over one persistent SSH link per target.

Each configured target (Settings > Machines > live targets) gets a thread that
runs ``mctl -t <target> jobs-watch <interval>``: a single long-lived ssh session
streaming a jobs-probe block every interval. Every block is handed to
``on_block`` (the portal reconciles it through ``vd.refresh(preprobed=...)``),
so state changes show up within seconds without a fresh ssh handshake per poll.

The link never powers anything on: mctl only *discovers* a VM's address. A
dropped link reconnects with back-off; while it is down the regular probe and
watchdog remain the fallback. Reachability transitions (up -> down -> up) are
reported to ``on_reach`` after the first known state, so start-up is quiet.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

MCTL = Path(os.path.expanduser("~/bin/mctl"))
BACKOFF = (5, 15, 30, 60, 120)
END = "__MCTL_PROBE_END__"


class Link:
    def __init__(self, target: str, interval: int, on_block: Callable[[str, str], None],
                 on_reach: Callable[[str, bool, str], None], env: Callable[[], dict],
                 argv: Callable[[str, int], list[str]] | None = None):
        self.target = target
        self.interval = interval
        self.on_block = on_block
        self.on_reach = on_reach
        self.env = env
        self.argv = argv or (lambda t, i: [str(MCTL), "-t", t, "jobs-watch", str(i)])
        self.state = "connecting"
        self.since = time.time()
        self.last_block = 0.0
        self.blocks = 0
        self.error = ""
        self.retry_at = 0.0
        self._up: bool | None = None
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"live-{target}")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc and proc.poll() is None:
            # mctl's ssh child holds stdout; end the whole group.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except (ProcessLookupError, PermissionError):
                    break
                try:
                    proc.wait(3)
                    break
                except subprocess.TimeoutExpired:
                    continue
        self._thread.join(6)

    def snapshot(self) -> dict:
        return {"state": self.state, "since": self.since, "last_block": self.last_block,
                "blocks": self.blocks, "error": self.error, "interval": self.interval,
                "retry_at": self.retry_at}

    def _set(self, state: str, error: str = "") -> None:
        if state != self.state:
            self.since = time.time()
        self.state, self.error = state, error

    def _reach(self, up: bool, why: str) -> None:
        if self._up is not None and self._up != up:
            try:
                self.on_reach(self.target, up, why)
            except Exception:                                  # noqa: BLE001
                pass
        self._up = up

    def _loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            self._set("connecting")
            got_block, err = self._session()
            if self._stop.is_set():
                break
            if got_block:
                failures = 0
            self._reach(False, err)
            delay = BACKOFF[min(failures, len(BACKOFF) - 1)]
            failures += 1
            self.retry_at = time.time() + delay
            self._set("down", err or "link closed")
            self._stop.wait(delay)
        self._set("stopped")

    def _session(self) -> tuple[bool, str]:
        got_block, tail = False, []
        try:
            self._proc = subprocess.Popen(
                self.argv(self.target, self.interval), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=self.env(),
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                start_new_session=True)
        except OSError as e:
            return False, str(e)
        errs: list[str] = []
        drain = threading.Thread(target=lambda: errs.extend(self._proc.stderr), daemon=True)
        drain.start()
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            if line.startswith(END):
                got_block = True
                self.last_block = time.time()
                self.blocks += 1
                self._set("connected")
                self._reach(True, "")
                try:
                    self.on_block(self.target, "".join(tail))
                except Exception as e:                         # noqa: BLE001
                    self.error = f"status update failed: {e}"
                tail = []
            else:
                tail.append(line)
        rc = self._proc.wait()
        drain.join(2)
        for pipe in (self._proc.stdout, self._proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass
        lines = [ln.strip() for ln in errs if ln.strip()]
        return got_block, (lines[-1] if lines else f"ssh link ended (rc={rc})")


class Manager:
    def __init__(self, on_block, on_reach, env, settings: Callable[[], dict],
                 argv=None):
        self.on_block, self.on_reach, self.env = on_block, on_reach, env
        self.settings = settings
        self.argv = argv
        self.links: dict[str, Link] = {}
        self._lock = threading.Lock()

    def sync(self) -> None:
        """Start/stop links to match the current settings."""
        cfg = self.settings()
        want = list(cfg.get("live_targets") or [])
        every = int(cfg.get("live_interval_s") or 15)
        with self._lock:
            for name in list(self.links):
                link = self.links[name]
                if name not in want or link.interval != every:
                    link.stop()
                    del self.links[name]
            for name in want:
                if name not in self.links:
                    link = Link(name, every, self.on_block, self.on_reach, self.env, self.argv)
                    self.links[name] = link
                    link.start()

    def stop(self) -> None:
        with self._lock:
            for link in self.links.values():
                link.stop()
            self.links.clear()

    def snapshot(self) -> dict:
        with self._lock:
            return {name: link.snapshot() for name, link in self.links.items()}
