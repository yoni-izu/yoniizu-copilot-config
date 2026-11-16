#!/usr/bin/env python3
"""Portal notification events: an in-memory feed plus a Windows toast fallback.

Producers (action queue completions, live machine status, AI runs) call
``publish``. Open portal tabs poll ``GET /api/events?since=<seq>`` and raise
browser notifications; every poll is also a presence heartbeat. When no tab has
polled for ``PRESENCE_GRACE`` seconds the event is shown as a Windows toast via
``powershell.exe`` (WSL), so a finished job is still noticed with the browser
closed. The feed is deliberately volatile: events are notifications, not
records; each owner keeps its own history.
"""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from typing import Callable
from xml.sax.saxutils import escape

KINDS = ("queue", "machine_jobs", "ai", "reachability")
PRESENCE_GRACE = 90.0  # background tabs may be throttled to one poll a minute
KEEP = 200
POWERSHELL_PATHS = ("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                    "/mnt/c/WINDOWS/System32/WindowsPowerShell/v1.0/powershell.exe")
# The AppUserModelID of Windows PowerShell; toasts need a registered app id.
TOAST_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


class Bus:
    def __init__(self, settings: Callable[[], dict] | None = None,
                 toaster: Callable[[str, str], None] | None = None,
                 clock: Callable[[], float] = time.time):
        self._lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=KEEP)
        self._seq = 0
        self._seen = 0.0
        self._settings = settings or (lambda: {})
        self._toaster = toaster if toaster is not None else windows_toast
        self._clock = clock

    def publish(self, kind: str, title: str, body: str = "", tone: str = "",
                link: str = "") -> dict | None:
        if kind not in KINDS:
            raise ValueError(f"unknown event kind {kind}")
        cfg = self._settings() or {}
        if not cfg.get("enabled", True) or not cfg.get(kind, True):
            return None
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "kind": kind, "title": title[:200],
                     "body": body[:600], "tone": tone, "link": link,
                     "at": self._clock()}
            self._events.append(event)
            tab_open = self._clock() - self._seen < PRESENCE_GRACE
        if not tab_open and cfg.get("windows_toast", True):
            threading.Thread(target=self._toast, args=(title, body), daemon=True).start()
        return event

    def _toast(self, title: str, body: str) -> None:
        try:
            self._toaster(title, body)
        except Exception:                                      # noqa: BLE001
            pass

    def since(self, seq: int, heartbeat: bool = True) -> dict:
        with self._lock:
            if heartbeat:
                self._seen = self._clock()
            events = [e for e in self._events if e["seq"] > seq]
            return {"seq": self._seq, "events": events}

    def tab_open(self) -> bool:
        with self._lock:
            return self._clock() - self._seen < PRESENCE_GRACE


def _powershell() -> str | None:
    found = shutil.which("powershell.exe")
    if found:
        return found
    for path in POWERSHELL_PATHS:
        if os.path.exists(path):
            return path
    return None


def toast_script(title: str, body: str) -> str:
    """PowerShell that shows one toast. Text travels as base64, never as code."""
    xml = ("<toast><visual><binding template=\"ToastGeneric\">"
           f"<text>{escape(title)}</text><text>{escape(body)}</text>"
           "</binding></visual></toast>")
    data = base64.b64encode(xml.encode("utf-8")).decode()
    return (
        "$ErrorActionPreference='Stop';"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
        "ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,"
        "ContentType=WindowsRuntime]|Out-Null;"
        f"$x=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{data}'));"
        "$d=New-Object Windows.Data.Xml.Dom.XmlDocument;$d.LoadXml($x);"
        "$t=[Windows.UI.Notifications.ToastNotification]::new($d);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        f"'{TOAST_APP_ID}').Show($t)")


def windows_toast(title: str, body: str) -> None:
    exe = _powershell()
    if not exe:
        return
    encoded = base64.b64encode(toast_script(title, body).encode("utf-16-le")).decode()
    subprocess.run([exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                   capture_output=True, timeout=30, check=False, cwd="/")
