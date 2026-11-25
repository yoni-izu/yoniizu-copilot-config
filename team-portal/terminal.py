#!/usr/bin/env python3
"""Browser terminal for the portal: a stdlib WebSocket bridged to a local pty.

The page ``/term`` asks ``POST /api/term/open`` for a one-time token, then
connects ``/ws/term?token=...``. The token names a pre-validated *spec* (argv,
title); nothing the socket sends can change what is executed, only feed it
keystrokes and resize it.

Specs:
* ``shell``: ``mctl -t <target> term "<tmux new -A -s portal || bash -l>"``. A
  closed window only detaches tmux, so reopening resumes the same session.
  ``session`` picks another tmux session (``portal-2``...) for a second shell.
* ``workspace``: a separate persistent tmux session for a tracked workspace,
  started in its root locally or on its registered machine without waking a VM.
* ``uart``: ``pilot -t <target> uart attach <id>``; the console list is pilot's.
* ``log``: a job log through ``vd.log_view``: ``tail`` on the machine only
  (``tail -F`` for a live job, the last lines of a finished one or of its
  timestamped archived run). Nothing is downloaded or read from the mirror.

Windows Terminal: ``wt_launch`` writes each pane's spec to a one-time 0600 file
and opens ``wt.exe ... wsl.exe --exec python3 terminal.py --run <id>`` panes, so
no argv crosses the Windows command-line quoting; the runner execs the spec.

Wire format: client -> server text frames ``{"t":"i","d":"..."}`` (input) and
``{"t":"r","c":cols,"r":rows}`` (resize). Server -> client binary frames carry
raw pty output; a final text frame ``{"t":"x","code":N,"msg":...}`` reports the
exit before the close frame.
"""
from __future__ import annotations

import base64
import fcntl
import glob
import math
import re
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
PILOT = Path(os.path.expanduser("~/bin/pilot"))
TMUX_SESSION = "portal"
SESSION_RE = re.compile(r"^portal(-[0-9]{1,2})?$")
WORKSPACE_ID_RE = re.compile(r"^WS-[LR]-[0-9a-f]{32}$")
CONSOLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
SHELL_REMOTE = (f"command -v tmux >/dev/null 2>&1 && exec tmux new -A -s {TMUX_SESSION} "
                "|| exec bash -l")
WORKSPACE_SHELL = """\
cd -- "$1" || { printf 'Workspace directory is unavailable: %s\\n' "$1" >&2; exit 1; }
command -v tmux >/dev/null 2>&1 || {
  printf 'tmux is required; install it or provision this machine\\n' >&2
  exit 1
}
unset TMUX
exec tmux new-session -A -s "$2" -c "$PWD"
"""
WORKSPACE_HOLD = """\
if "$@"; then exit 0; else status=$?; fi
printf 'Portal workspace terminal failed (status %s).\\n' "$status" >&2
if [ -t 0 ]; then
  printf 'Press Enter to close. '
  IFS= read -r _ || :
fi
exit "$status"
"""
WT_LAYOUTS = ("grid", "rows", "columns")
WT_MAX_PANES = 16
LAUNCH_TTL = 120.0
WT_GLOB = "/mnt/c/Users/*/AppData/Local/Microsoft/WindowsApps/wt.exe"
WSL_EXE = "/mnt/c/WINDOWS/system32/wsl.exe"

_LOCK = threading.Lock()
_TOKENS: dict[str, dict] = {}
_ACTIVE: dict[str, dict] = {}


class TermError(ValueError):
    """Bad terminal request (HTTP 400/403/409)."""


# ----------------------------------------------------------------- specs

def shell_spec(target: str, allowed: list[str], session: str = TMUX_SESSION) -> dict:
    if target not in allowed:
        raise TermError(f"'{target}' is not a registered mctl target")
    session = session or TMUX_SESSION
    if not SESSION_RE.match(session):
        raise TermError("session must be portal or portal-<n>")
    remote = SHELL_REMOTE.replace(f"-s {TMUX_SESSION} ", f"-s {session} ")
    return {"argv": [str(MCTL), "-t", target, "term", remote],
            "title": f"{target} \u00b7 tmux {session}", "kind": "shell",
            "target": target}


def workspace_spec(workspace: dict, allowed: list[str]) -> dict:
    """Build a terminal from an owner-resolved workspace, not a client path."""
    ident = workspace.get("id")
    path = workspace.get("path")
    target = workspace.get("target") or ""
    if not isinstance(ident, str) or not WORKSPACE_ID_RE.fullmatch(ident):
        raise TermError("invalid workspace id")
    if not isinstance(path, str) or not path or "\0" in path:
        raise TermError("workspace has no valid directory")
    session = f"portal-ws-{ident}"
    if target:
        if target not in allowed:
            raise TermError(f"'{target}' is not a registered mctl target")
        if path == "~":
            directory = '"$HOME"'
        elif path.startswith("~/"):
            directory = '"$HOME"/' + _sh_quote(path[2:])
        elif path.startswith("/"):
            directory = _sh_quote(path)
        else:
            raise TermError("remote workspace directory must be absolute or home-relative")
        remote = ("bash -lc " + _sh_quote(WORKSPACE_SHELL) + " workspace-terminal "
                  + directory + " " + _sh_quote(session))
        command = ["env", "-u", "VM_IP", "MCTL_AUTOSTART=0",
                   str(MCTL), "-t", target, "term", remote]
    else:
        path = os.path.expanduser(path)
        if not os.path.isabs(path) or not os.path.isdir(path):
            raise TermError(f"local workspace directory is unavailable: {path}")
        command = ["bash", "-c", WORKSPACE_SHELL, "workspace-shell", path, session]
    title = workspace.get("label") or workspace.get("slot") or ident
    return {"argv": ["bash", "-c", WORKSPACE_HOLD, "workspace-terminal", *command],
            "title": f"{title} \u00b7 workspace terminal",
            "kind": "workspace", "target": target}


def uart_spec(target: str, console: str, consoles: list[dict]) -> dict:
    """An interactive UART console through `pilot uart attach` (pilot owns the ports)."""
    if not CONSOLE_RE.match(console or ""):
        raise TermError("invalid console id")
    item = next((c for c in consoles if c.get("id") == console), None)
    if item is None:
        raise TermError(f"'{console}' is not a registered UART console of {target}")
    return {"argv": [str(PILOT), "-t", target, "uart", "attach", console],
            "title": f"{target} \u00b7 {item.get('label') or console}", "kind": "uart",
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


def issue_token(spec: dict, now: float | None = None, limit: int | None = None) -> str:
    now = time.time() if now is None else now
    limit = MAX_SESSIONS if limit is None else limit
    with _LOCK:
        for tok, item in list(_TOKENS.items()):
            if item["expires"] < now:
                _TOKENS.pop(tok, None)
        if len(_ACTIVE) >= limit:
            raise TermError(f"{limit} browser terminals are already open; close one first")
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


# ------------------------------------------------------- Windows Terminal

WINDOW_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
DISTRO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LAUNCH_ID_RE = re.compile(r"^[0-9a-f]{32}$")
RUNNER = Path(__file__).resolve()


def launch_dir() -> Path:
    import sys
    sys.path.insert(0, str(RUNNER.parent.parent / "lib"))
    from private_paths import private_root  # noqa: PLC0415
    return private_root() / "team-portal" / "state" / "term-launch"


def wt_path() -> str | None:
    found = sorted(glob.glob(WT_GLOB))
    return found[0] if found else None


def grid_rows(n: int, layout: str) -> list[list[int]]:
    """Pane indices row by row (row-major, like the browser grid)."""
    if layout == "rows":
        cols = 1
    elif layout == "columns":
        cols = n
    else:
        cols = max(1, math.ceil(math.sqrt(n)))
    return [list(range(i, min(i + cols, n))) for i in range(0, n, cols)]


def _title(text: str) -> str:
    # wt splits sub-commands on ';' even inside quotes; titles are display-only.
    return re.sub(r'[;"\r\n]', " ", text or "terminal")[:80]


def wt_args(panes: list[tuple[str, list[str]]], layout: str, window: str) -> list[str]:
    """wt.exe arguments for one tab holding ``panes`` [(title, commandline argv)].

    Rows are split first (-H), then every row is split into columns (-V); each
    split gives the new pane the fraction that keeps the panes equal. Pane ids
    are creation order, which ``focus-pane -t`` relies on.
    """
    rows = grid_rows(len(panes), layout)

    def pane(i: int) -> list[str]:
        title, cmd = panes[i]
        return ["--title", _title(title), "--suppressApplicationTitle", *cmd]

    args = ["-w", window, "new-tab", *pane(rows[0][0])]
    for r in range(1, len(rows)):
        left = len(rows) - r
        args += [";", "split-pane", "-H", "-s", f"{left / (left + 1):.3f}", *pane(rows[r][0])]
    for r, row in enumerate(rows):
        if len(row) < 2:
            continue
        args += [";", "focus-pane", "-t", str(r)]
        for c in range(1, len(row)):
            left = len(row) - c
            args += [";", "split-pane", "-V", "-s", f"{left / (left + 1):.3f}", *pane(row[c])]
    if len(panes) > 1:
        args += [";", "focus-pane", "-t", "0"]
    return args


def _write_launch(spec: dict, env_extra: dict, folder: Path, now: float) -> str:
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for old in folder.glob("*.json"):
        try:
            if old.stat().st_mtime < now - LAUNCH_TTL:
                old.unlink()
        except OSError:
            pass
    lid = secrets.token_hex(16)
    path = folder / f"{lid}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"argv": spec["argv"], "title": spec.get("title", ""), "env": env_extra,
                   "expires": now + LAUNCH_TTL}, fh)
    return lid


def wt_launch(specs: list[dict], layout: str = "grid", window: str = "portal",
              distro: str = "", env_extra: dict | None = None, folder: Path | None = None,
              wt: str | None = None, run: Callable = subprocess.run,
              now: float | None = None) -> dict:
    """Open ``specs`` as panes of one new Windows Terminal tab on this PC."""
    if not 1 <= len(specs) <= WT_MAX_PANES:
        raise TermError(f"choose 1-{WT_MAX_PANES} panes")
    if layout not in WT_LAYOUTS:
        raise TermError("layout must be grid, rows or columns")
    if not WINDOW_RE.match(window or ""):
        raise TermError("invalid Windows Terminal window name")
    if distro and not DISTRO_RE.match(distro):
        raise TermError("invalid WSL distro name")
    wt = wt or wt_path()
    if not wt:
        raise TermError("Windows Terminal (wt.exe) was not found")
    now = time.time() if now is None else now
    folder = folder or launch_dir()
    env_extra = {k: v for k, v in (env_extra or {}).items()
                 if k in ("SSH_AUTH_SOCK",) and isinstance(v, str)}
    ids = [_write_launch(s, env_extra, folder, now) for s in specs]
    wsl = ["wsl.exe", *(["-d", distro] if distro else []), "--cd", "~", "--exec",
           "/usr/bin/python3", str(RUNNER), "--run"]
    args = wt_args([(s.get("title", ""), [*wsl, i]) for s, i in zip(specs, ids)], layout, window)
    try:
        out = run([wt, *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=20,
                  check=False, start_new_session=True)
    except (OSError, subprocess.TimeoutExpired) as e:
        out = None
        error = str(e)
    if out is None or out.returncode != 0:
        for i in ids:
            (folder / f"{i}.json").unlink(missing_ok=True)
        detail = error if out is None else (out.stderr or out.stdout or b"").decode(
            "utf-8", "replace").strip()[-200:] or f"exit {out.returncode}"
        raise TermError(f"Windows Terminal did not start: {detail}")
    return {"panes": len(specs), "window": window, "layout": layout}


def _user_path(path: str) -> str:
    """Prepend the user bin dirs: ``wsl.exe --exec`` skips the login profile."""
    home = os.path.expanduser("~")
    parts = [p for p in path.split(os.pathsep) if p]
    extra = [d for d in (f"{home}/.local/bin", f"{home}/bin") if d not in parts]
    return os.pathsep.join(extra + parts)


def run_launch(lid: str, folder: Path | None = None, now: float | None = None) -> int:
    """Pane entry point (``terminal.py --run <id>``): exec the one-time spec."""
    folder = folder or launch_dir()
    now = time.time() if now is None else now
    path = folder / f"{lid}.json"
    try:
        if not LAUNCH_ID_RE.match(lid or ""):
            raise TermError("bad launch id")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise TermError("launch expired or already used") from e
        finally:
            path.unlink(missing_ok=True)
        if data.get("expires", 0) < now or not data.get("argv"):
            raise TermError("launch expired")
        env = dict(os.environ, **(data.get("env") or {}))
        env["PATH"] = _user_path(env.get("PATH", ""))
        os.chdir(os.path.expanduser("~"))
        print(f"\033]0;{_title(data.get('title', ''))}\007", end="", flush=True)
        os.execvpe(data["argv"][0], data["argv"], env)
    except (TermError, OSError) as e:
        print(f"Portal terminal: {e}")
        try:
            input("Press Enter to close.")
        except EOFError:
            pass
        return 1
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--run":
        sys.exit(run_launch(sys.argv[2]))
    sys.exit("usage: terminal.py --run <launch-id>")
