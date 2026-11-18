#!/usr/bin/env python3
"""Browser terminal for the portal: a stdlib WebSocket bridged to a local pty.

The page ``/term`` asks ``POST /api/term/open`` for a one-time token, then
connects ``/ws/term?token=...``. The token names a pre-validated *spec* (argv,
title); nothing the socket sends can change what is executed, only feed it
keystrokes and resize it.

Specs:
* ``shell``: ``mctl -t <target> term "<tmux new -A -s portal || bash -l>"``. A
  closed window only detaches tmux, so reopening resumes the same session.
* ``log``: a job log through ``vd.log_view``: ``tail`` on the machine only
  (``tail -F`` for a live job, the last lines of a finished one or of its
  timestamped archived run). Nothing is downloaded or read from the mirror.

Wire format: client -> server text frames ``{"t":"i","d":"..."}`` (input) and
``{"t":"r","c":cols,"r":rows}`` (resize). Server -> client binary frames carry
raw pty output; a final text frame ``{"t":"x","code":N,"msg":...}`` reports the
exit before the close frame.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import secrets
import select
import signal
import socket
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path
from typing import Callable

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TOKEN_TTL = 120.0
MAX_SESSIONS = 8
MAX_FRAME = 1 << 20
MCTL = Path(os.path.expanduser("~/bin/mctl"))
TMUX_SESSION = "portal"
SHELL_REMOTE = (f"command -v tmux >/dev/null 2>&1 && exec tmux new -A -s {TMUX_SESSION} "
                "|| exec bash -l")

_LOCK = threading.Lock()
_TOKENS: dict[str, dict] = {}
_ACTIVE: dict[str, dict] = {}


class TermError(ValueError):
    """Bad terminal request (HTTP 400/403/409)."""


# ----------------------------------------------------------------- specs

def shell_spec(target: str, allowed: list[str]) -> dict:
    if target not in allowed:
        raise TermError(f"'{target}' is not a registered mctl target")
    return {"argv": [str(MCTL), "-t", target, "term", SHELL_REMOTE],
            "title": f"{target} \u00b7 tmux {TMUX_SESSION}", "kind": "shell",
            "target": target}


def bmc_spec(target: str, bmcs: dict[str, str]) -> dict:
    """Plain ssh to the target's registered BMC via `mctl bmc` (no tmux there)."""
    if target not in bmcs:
        raise TermError(f"'{target}' has no registered BMC")
    return {"argv": [str(MCTL), "-t", target, "bmc"],
            "title": f"{target} \u00b7 BMC {bmcs[target]}", "kind": "bmc",
            "target": target}


def provision_spec(target: str, known: list[str]) -> dict:
    """`mctl provision` on a registered target, live in a terminal window."""
    if target not in known:
        raise TermError(f"'{target}' is not a registered mctl target")
    return {"argv": [str(MCTL), "-t", target, "provision"],
            "title": f"{target} \u00b7 provision", "kind": "provision",
            "target": target}


def log_spec(view: dict) -> dict:
    if view.get("error"):
        raise TermError(view["error"])
    if view.get("mode") == "remote":
        return {"argv": [str(MCTL), "-t", view["target"], "term",
                         "bash -lc " + _sh_quote(view["command"])],
                "title": view.get("title") or "log", "kind": "log",
                "target": view["target"]}
    if view.get("mode") == "local":
        # A local-runner job (e.g. fw flash): its log lives on this machine.
        if not isinstance(view.get("command"), str) or not view["command"]:
            raise TermError("no way to show this log on its machine")
        return {"argv": ["bash", "-c", view["command"]],
                "title": view.get("title") or "log", "kind": "log",
                "target": view.get("target") or ""}
    raise TermError("no way to show this log on its machine")


def _sh_quote(text: str) -> str:
    return "'" + text.replace("'", "'\"'\"'") + "'"


def issue_token(spec: dict, now: float | None = None) -> str:
    now = time.time() if now is None else now
    with _LOCK:
        for tok, item in list(_TOKENS.items()):
            if item["expires"] < now:
                _TOKENS.pop(tok, None)
        if len(_ACTIVE) >= MAX_SESSIONS:
            raise TermError(f"{MAX_SESSIONS} terminals are already open; close one first")
        token = secrets.token_urlsafe(24)
        _TOKENS[token] = {"spec": spec, "expires": now + TOKEN_TTL}
    return token


def take_token(token: str, now: float | None = None) -> dict | None:
    now = time.time() if now is None else now
    with _LOCK:
        item = _TOKENS.pop(token or "", None)
    if not item or item["expires"] < now:
        return None
    return item["spec"]


def active() -> list[dict]:
    with _LOCK:
        return [{"id": k, "title": v["title"], "kind": v["kind"], "target": v["target"],
                 "since": v["since"]} for k, v in _ACTIVE.items()]


# ------------------------------------------------------------- websocket

def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def encode_frame(payload: bytes, opcode: int = 0x2) -> bytes:
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 1 << 16:
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    return head + payload


def _recv_exact(sock_file, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock_file.read(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def read_frame(sock_file) -> tuple[int, bytes]:
    """One (possibly fragmented) client message: (opcode, payload)."""
    message, first_op = b"", None
    while True:
        b1, b2 = _recv_exact(sock_file, 2)
        fin, opcode = b1 & 0x80, b1 & 0x0F
        masked, n = b2 & 0x80, b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", _recv_exact(sock_file, 2))[0]
        elif n == 127:
            n = struct.unpack("!Q", _recv_exact(sock_file, 8))[0]
        if n > MAX_FRAME:
            raise ConnectionError("frame too large")
        if not masked:
            raise ConnectionError("client frames must be masked")
        mask = _recv_exact(sock_file, 4)
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(_recv_exact(sock_file, n)))
        if opcode >= 0x8:                       # control frames are never fragmented
            return opcode, data
        if first_op is None:
            first_op = opcode
        message += data
        if len(message) > MAX_FRAME:
            raise ConnectionError("message too large")
        if fin:
            return first_op, message


def handshake(handler, expected_origin: str) -> bool:
    """Complete the RFC 6455 upgrade on a BaseHTTPRequestHandler."""
    h = handler.headers
    origin = h.get("Origin")
    if origin and origin != expected_origin:
        handler.send_error(403, "cross-origin terminal refused")
        return False
    if (h.get("Upgrade", "").lower() != "websocket"
            or "upgrade" not in h.get("Connection", "").lower()
            or not h.get("Sec-WebSocket-Key")):
        handler.send_error(400, "websocket upgrade required")
        return False
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", accept_key(h["Sec-WebSocket-Key"]))
    handler.end_headers()
    handler.wfile.flush()
    return True


# ------------------------------------------------------------------ pty

def set_size(fd: int, cols: int, rows: int) -> None:
    cols = max(10, min(int(cols), 500))
    rows = max(3, min(int(rows), 200))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def spawn(argv: list[str], env: dict | None = None, cols: int = 120,
          rows: int = 32) -> tuple[subprocess.Popen, int]:
    """Start argv on a fresh pty as its controlling terminal (``setsid -c``)."""
    master, slave = os.openpty()
    set_size(slave, cols, rows)
    full_env = dict(env or os.environ)
    full_env.update(TERM="xterm-256color", COLORTERM="truecolor", LESSSECURE="1",
                    LESSCHARSET="utf-8")
    try:
        proc = subprocess.Popen(["setsid", "-w", "-c", *argv], stdin=slave, stdout=slave,
                                stderr=slave, env=full_env, close_fds=True,
                                cwd=os.path.expanduser("~"))
    finally:
        os.close(slave)
    return proc, master


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    for sig, wait in ((signal.SIGHUP, 2), (signal.SIGTERM, 2), (signal.SIGKILL, 2)):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                return
        try:
            proc.wait(wait)
            return
        except subprocess.TimeoutExpired:
            continue


def serve(sock: socket.socket, spec: dict, env: dict | None = None,
          on_exit: Callable[[dict, int | None], None] | None = None) -> int | None:
    """Bridge one upgraded socket to the spec's process until either side ends."""
    proc, master = spawn(spec["argv"], env)
    sid = secrets.token_hex(6)
    with _LOCK:
        _ACTIVE[sid] = {"title": spec.get("title", ""), "kind": spec.get("kind", ""),
                        "target": spec.get("target", ""), "since": time.time()}
    send_lock = threading.Lock()
    closed = threading.Event()

    def send(data: bytes, opcode: int = 0x2) -> None:
        if closed.is_set():
            return
        with send_lock:
            try:
                sock.sendall(encode_frame(data, opcode))
            except OSError:
                closed.set()

    def pump_output() -> None:
        while not closed.is_set():
            try:
                ready, _, _ = select.select([master], [], [], 0.5)
            except (OSError, ValueError):
                break
            if not ready:
                if proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(master, 65536)
            except OSError:
                break                      # EIO: the pty slave side is gone
            if not data:
                break
            send(data)
        rc = proc.poll()
        if rc is None and not closed.is_set():
            try:
                rc = proc.wait(2)
            except subprocess.TimeoutExpired:
                rc = None
        if rc in (0, None):
            msg = "end of log" if spec.get("kind") == "log" else "session ended"
        else:
            msg = f"exited with code {rc}"
        send(json.dumps({"t": "x", "code": rc, "msg": msg}).encode(), 0x1)
        send(struct.pack("!H", 1000), 0x8)
        closed.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    pump = threading.Thread(target=pump_output, daemon=True, name="term-out")
    pump.start()
    reader = sock.makefile("rb")
    try:
        while not closed.is_set():
            try:
                opcode, data = read_frame(reader)
            except (ConnectionError, OSError, ValueError):
                break
            if opcode == 0x8:
                break
            if opcode == 0x9:
                send(data, 0xA)
                continue
            if opcode not in (0x1, 0x2):
                continue
            try:
                msg = json.loads(data.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("t") == "i" and isinstance(msg.get("d"), str):
                try:
                    os.write(master, msg["d"].encode("utf-8"))
                except OSError:
                    break
            elif msg.get("t") == "r":
                try:
                    set_size(master, msg.get("c", 120), msg.get("r", 32))
                except (OSError, TypeError, ValueError):
                    pass
    finally:
        closed.set()
        _stop(proc)
        pump.join(3)
        try:
            os.close(master)
        except OSError:
            pass
        with _LOCK:
            _ACTIVE.pop(sid, None)
        if on_exit:
            try:
                on_exit(spec, proc.poll())
            except Exception:                               # noqa: BLE001
                pass
    return proc.poll()
