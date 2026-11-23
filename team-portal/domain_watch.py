"""Low-cost change detection for domain databases owned by portal sections."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

DATABASES = {
    "tasks": ("tasks.db",),
    "team": ("team.db",),
    "bugs": ("bugs.db",),
    "workspaces": ("workspaces.db",),
    "prs": ("prs.db",),
    "pr_failures": ("pr_failures.db",),
    "raid": ("raid.db",),
    "delegation": ("delegation.db",),
}


class Watcher:
    def __init__(self, root: Path, publish: Callable[[list[str]], object],
                 interval: float = 2.0, quiet: float = 0.75,
                 clock: Callable[[], float] = time.monotonic):
        self.root = Path(root)
        self.publish = publish
        self.interval = interval
        self.quiet = quiet
        self.clock = clock
        self.enabled = True
        self.domains = set(DATABASES)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._snapshot: dict[Path, tuple[int, int, int, int] | None] | None = None
        self._pending: dict[str, float] = {}

    def configure(self, enabled: bool = True, domains=None, interval: float | None = None,
                  quiet: float | None = None) -> None:
        """Apply new settings; newly watched files start from a fresh baseline."""
        with self._lock:
            self.domains = set(DATABASES) & set(DATABASES if domains is None else domains)
            if not enabled:
                self._snapshot = None
                self._pending.clear()
            else:
                self._pending = {d: t for d, t in self._pending.items() if d in self.domains}
            self.enabled = enabled
            if interval is not None:
                self.interval = interval
            if quiet is not None:
                self.quiet = quiet
        self._wake.set()

    def _files(self) -> dict[Path, str]:
        return {
            self.root / suffix: domain
            for domain, names in DATABASES.items()
            if domain in self.domains
            for name in names
            for suffix in (name, f"{name}-wal")
        }

    def _state(self, path: Path) -> tuple[int, int, int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size

    def tick(self) -> None:
        with self._lock:
            if not self.enabled:
                return
            files = self._files()
            current = {path: self._state(path) for path in files}
            now = self.clock()
            if self._snapshot is not None:
                for path, state in current.items():
                    if path in self._snapshot and self._snapshot[path] != state:
                        self._pending[files[path]] = now
            self._snapshot = current
            ready = [domain for domain, changed_at in self._pending.items()
                     if now - changed_at >= self.quiet]
            for domain in ready:
                del self._pending[domain]
        if ready:
            self.publish(sorted(ready))

    def run(self, stop: threading.Event) -> None:
        last_error = None
        while not stop.is_set():
            try:
                self.tick()
                last_error = None
            except OSError as error:
                message = str(error)
                if message != last_error:
                    print(f"[portal/domain-watch] {error}", file=sys.stderr)
                    last_error = message
            # Disabled: sleep until configure() wakes the loop.
            self._wake.wait(self.interval if self.enabled else None)
            self._wake.clear()

    def shutdown(self, stop: threading.Event) -> None:
        stop.set()
        self._wake.set()
