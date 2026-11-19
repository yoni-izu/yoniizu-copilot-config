"""Remote AWS SSO login (`octl login`) on a registered mctl target.

The portal runs ``mctl -t <target> term "<octl login>"`` with ``MCTL_AUTOSTART=0``
(so a stopped VM is never started), watches the live output for the device
authorization link and one-time code, and hands them to the browser, which opens
the link locally. Only links on AWS SSO hosts are ever returned as openable;
the code and link stay in memory and are never logged.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from urllib.parse import urlsplit

MCTL = Path(os.path.expanduser("~/bin/mctl"))
REMOTE_CMD = "bash -lc 'OCTL_PLAIN=true octl login'"
TIMEOUT_S = 600
KEEP_S = 900                     # finished entries linger for the card to show
TAIL_CHARS = 4000

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
URL_RE = re.compile(r"https://[^\s'\"<>\x1b]+")
CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
ALLOWED_HOST_RE = re.compile(
    r"^(?:[a-z0-9-]+\.awsapps\.com|device\.sso\.[a-z0-9-]+\.amazonaws\.com)$")
SIGNIN_HOST_RE = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d+\.signin\.aws$")


class SsoError(ValueError):
    """Bad request (HTTP 400)."""


def safe_url(url: str) -> str | None:
    """The URL when it is an https AWS SSO device page, otherwise None."""
    try:
        parts = urlsplit(url.rstrip(".,;)"))
        if parts.scheme != "https" or parts.username or parts.password or parts.port:
            return None
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not (ALLOWED_HOST_RE.fullmatch(host)
            or (SIGNIN_HOST_RE.fullmatch(host) and parts.path == "/platform/authorize")):
        return None
    return parts.geturl()


def parse(text: str) -> tuple[str | None, str | None]:
    """(openable link, one-time code) found in octl's output so far."""
    clean = ANSI_RE.sub("", text)
    url = None
    for cand in URL_RE.findall(clean):
        ok = safe_url(cand)
        if ok:
            url = ok
            # The device URL usually carries the code itself.
            if "user_code=" in ok:
                break
    code = None
    m = re.search(r"user_code=([A-Z0-9]{4}-[A-Z0-9]{4})", url or "")
    if m:
        code = m.group(1)
    else:
        m = CODE_RE.search(clean)
        code = m.group(1) if m else None
    return url, code


def _redact(text: str) -> str:
    text = URL_RE.sub("[link]", ANSI_RE.sub("", text))
    return CODE_RE.sub("[code]", text)


class SsoLogins:
    """One `octl login` per target at a time; state is in memory only."""

    def __init__(self, argv_for=None, now=time.time, *, env_for=None):
        self._lock = threading.Lock()
        self._runs: dict[str, dict] = {}
        self._argv_for = argv_for or (lambda t: [str(MCTL), "-t", t, "term", REMOTE_CMD])
        self._env_for = env_for or (lambda: dict(os.environ))
        self._now = now

    def start(self, target: str, known: list[str]) -> dict:
        if target not in known:
            raise SsoError(f"'{target}' is not a registered mctl target")
        with self._lock:
            run = self._runs.get(target)
            if run and run["state"] in ("starting", "waiting"):
                return self._public(run)
            run = {"target": target, "state": "starting", "url": None, "code": None,
                   "message": "Connecting\u2026", "started": self._now(), "ended": None,
                   "proc": None, "buf": ""}
            self._runs[target] = run
        env = dict(self._env_for(), MCTL_AUTOSTART="0")
        try:
            proc = subprocess.Popen(self._argv_for(target), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env=env, start_new_session=True)
        except OSError as e:
            self._finish(run, "failed", f"could not start mctl: {e}")
            return self._public(run)
        run["proc"] = proc
        threading.Thread(target=self._pump, args=(run,), daemon=True).start()
        threading.Thread(target=self._watchdog, args=(run,), daemon=True).start()
        return self._public(run)

    def cancel(self, target: str) -> dict:
        with self._lock:
            run = self._runs.get(target)
        if not run:
            raise SsoError(f"no SSO login for '{target}'")
        if run["state"] in ("starting", "waiting"):
            self._kill(run)
            self._finish(run, "cancelled", "Cancelled")
        return self._public(run)

    def status(self) -> dict:
        now = self._now()
        with self._lock:
            for t in [t for t, r in self._runs.items()
                      if r["ended"] and now - r["ended"] > KEEP_S]:
                del self._runs[t]
            return {t: self._public(r) for t, r in self._runs.items()}

    def _pump(self, run: dict) -> None:
        proc = run["proc"]
        fd = proc.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            with self._lock:
                run["buf"] = (run["buf"] + chunk.decode("utf-8", "replace"))[-TAIL_CHARS:]
                if run["state"] not in ("starting", "waiting"):
                    continue
                url, code = parse(run["buf"])
                if url and not run["url"]:
                    run.update(url=url, state="waiting",
                               message="Waiting for authorization in the browser\u2026")
                if code:
                    run["code"] = code
        rc = proc.wait()
        proc.stdout.close()
        if run["state"] in ("starting", "waiting"):
            if rc == 0:
                self._finish(run, "done", "Logged in")
            else:
                self._finish(run, "failed", self._failure(run, rc))

    def _failure(self, run: dict, rc: int) -> str:
        lines = [ln.strip() for ln in _redact(run["buf"]).splitlines() if ln.strip()]
        lines = [ln for ln in lines if not ln.startswith("Connection to ")]
        tail = lines[-1] if lines else ""
        if "stopped" in tail.lower() or "is stopped" in run["buf"]:
            return "VM is stopped \u2014 start it from the card first"
        return f"octl login exited {rc}" + (f": {tail[:200]}" if tail else "")

    def _watchdog(self, run: dict) -> None:
        deadline = run["started"] + TIMEOUT_S
        while run["state"] in ("starting", "waiting"):
            if self._now() >= deadline:
                self._kill(run)
                self._finish(run, "failed", "Timed out after 10 minutes")
                return
            time.sleep(1)

    def _kill(self, run: dict) -> None:
        proc = run.get("proc")
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    def _finish(self, run: dict, state: str, message: str) -> None:
        with self._lock:
            if run["state"] in ("starting", "waiting"):
                run.update(state=state, message=message, ended=self._now())
                if state != "waiting":
                    run["buf"] = ""

    @staticmethod
    def _public(run: dict) -> dict:
        active = run["state"] in ("starting", "waiting")
        return {"target": run["target"], "state": run["state"], "message": run["message"],
                "url": run["url"] if active else None,
                "code": run["code"] if active else None,
                "started": run["started"], "ended": run["ended"]}
