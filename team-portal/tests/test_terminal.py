"""Browser terminal: WebSocket framing, one-time tokens and a real pty session."""
import json
import os
import select
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import terminal  # noqa: E402


def client_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    mask = os.urandom(4)
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    else:
        head += bytes([0x80 | 126]) + struct.pack("!H", n)
    return head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload))


def server_frames(data: bytes):
    out, i = [], 0
    while i + 2 <= len(data):
        op, n = data[i] & 0x0F, data[i + 1] & 0x7F
        i += 2
        if n == 126:
            n = struct.unpack("!H", data[i:i + 2])[0]
            i += 2
        elif n == 127:
            n = struct.unpack("!Q", data[i:i + 8])[0]
            i += 8
        out.append((op, data[i:i + n]))
        i += n
    return out


class FramingTests(unittest.TestCase):
    def test_accept_key_matches_rfc_example(self):
        self.assertEqual(terminal.accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
                         "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_masked_frames_round_trip_including_extended_length(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        big = b"x" * 300
        a.sendall(client_frame(b"hi") + client_frame(big, 0x2))
        f = b.makefile("rb")
        self.assertEqual(terminal.read_frame(f), (0x1, b"hi"))
        self.assertEqual(terminal.read_frame(f), (0x2, big))

    def test_unmasked_client_frame_is_rejected(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        a.sendall(bytes([0x81, 2]) + b"hi")
        with self.assertRaises(ConnectionError):
            terminal.read_frame(b.makefile("rb"))

    def test_server_frame_lengths(self):
        for n in (5, 200, 70000):
            frames = server_frames(terminal.encode_frame(b"a" * n))
            self.assertEqual(frames, [(0x2, b"a" * n)])


class TokenTests(unittest.TestCase):
    def test_token_is_single_use_and_expires(self):
        spec = {"argv": ["true"], "title": "t", "kind": "shell", "target": "vm"}
        tok = terminal.issue_token(spec, now=1000.0)
        self.assertEqual(terminal.take_token(tok, now=1001.0), spec)
        self.assertIsNone(terminal.take_token(tok, now=1001.0))
        tok = terminal.issue_token(spec, now=1000.0)
        self.assertIsNone(terminal.take_token(tok, now=1000.0 + terminal.TOKEN_TTL + 1))

    def test_shell_spec_only_for_registered_targets(self):
        with self.assertRaises(terminal.TermError):
            terminal.shell_spec("dsk61", ["vm"])
        spec = terminal.shell_spec("vm", ["vm"])
        self.assertEqual(spec["argv"][1:4], ["-t", "vm", "term"])
        self.assertIn("tmux new -A -s portal", spec["argv"][-1])

    def test_provision_spec_only_for_registered_targets(self):
        with self.assertRaises(terminal.TermError):
            terminal.provision_spec("evil;rm", ["vm"])
        spec = terminal.provision_spec("dsk61", ["vm", "dsk61"])
        self.assertEqual(spec["argv"][1:], ["-t", "dsk61", "provision"])
        self.assertEqual(spec["kind"], "provision")

    def test_bmc_spec_only_for_targets_with_a_bmc(self):
        with self.assertRaises(terminal.TermError):
            terminal.bmc_spec("vm", {"dsk61": "bmc-sa-dsk61-csr"})
        spec = terminal.bmc_spec("dsk61", {"dsk61": "bmc-sa-dsk61-csr"})
        self.assertEqual(spec["argv"][1:], ["-t", "dsk61", "bmc"])
        self.assertEqual(spec["kind"], "bmc")
        self.assertIn("bmc-sa-dsk61-csr", spec["title"])

    def test_log_spec_quotes_remote_script_and_passes_errors(self):
        spec = terminal.log_spec({"mode": "remote", "target": "vm", "title": "x",
                                  "command": "echo 'a'; tail b.log"})
        self.assertEqual(spec["argv"][-1], "bash -lc 'echo '\"'\"'a'\"'\"'; tail b.log'")
        with self.assertRaises(terminal.TermError):
            terminal.log_spec({"error": "gone"})
        # Local-runner jobs (fw flash) run here, so their log is viewed here;
        # the view must be a vd-built command, never a client argv.
        with self.assertRaises(terminal.TermError):
            terminal.log_spec({"mode": "local", "argv": ["less", "/x"], "title": "y"})
        spec = terminal.log_spec({"mode": "local", "command": "tail -n 5 /x", "title": "y"})
        self.assertEqual(spec["argv"], ["bash", "-c", "tail -n 5 /x"])


class WorkspaceSpecTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.capture = self.root / "capture"
        self.env = dict(os.environ, HOME=str(self.root), CAPTURE=str(self.capture),
                        PATH=f"{self.bin}:{os.environ['PATH']}")
        tmux = self.bin / "tmux"
        tmux.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@" > "$CAPTURE"\n')
        tmux.chmod(0o755)
        self.local_id = "WS-L-" + "a" * 32
        self.remote_id = "WS-R-" + "b" * 32

    def test_local_directory_and_session_are_passed_without_shell_interpretation(self):
        directory = self.root / "work dir '$(touch PWNED);#"
        directory.mkdir()
        record = {"id": self.local_id, "path": str(directory), "label": "local"}
        spec = terminal.workspace_spec(record, [])
        result = subprocess.run(spec["argv"], cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.capture.read_text().splitlines(),
                         [str(directory), "new-session", "-A", "-s", "portal-ws-" + self.local_id,
                          "-c", str(directory)])
        self.assertFalse((self.root / "PWNED").exists())
        other = terminal.workspace_spec({**record, "id": "WS-L-" + "c" * 32}, [])
        self.assertNotEqual(spec["argv"][-1], other["argv"][-1])

    def test_remote_home_and_absolute_paths_are_quoted_and_never_start_a_vm(self):
        inner_bash = self.bin / "bash"
        inner_bash.write_text('#!/bin/bash\nexec /bin/bash -c "$2" "$3" "$4" "$5"\n')
        inner_bash.chmod(0o755)
        directory = self.root / "remote dir '$(touch PWNED);#"
        directory.mkdir()
        for path in ("~/" + directory.name, str(directory)):
            with self.subTest(path=path):
                spec = terminal.workspace_spec(
                    {"id": self.remote_id, "path": path, "target": "desk"}, ["desk"])
                self.assertEqual(spec["argv"][4:12],
                                 ["env", "-u", "VM_IP", "MCTL_AUTOSTART=0",
                                  str(terminal.MCTL), "-t", "desk", "term"])
                result = subprocess.run(["/bin/bash", "-c", spec["argv"][-1]],
                                        cwd=self.root, env=self.env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.capture.read_text().splitlines(),
                                 [str(directory), "new-session", "-A", "-s",
                                  "portal-ws-" + self.remote_id, "-c", str(directory)])
                self.assertFalse((self.root / "PWNED").exists())

    def test_invalid_records_and_missing_tmux_fail_explicitly(self):
        for record in (
                {"id": "slot", "path": str(self.root)},
                {"id": self.local_id, "path": str(self.root / "missing")},
                {"id": self.remote_id, "path": "relative", "target": "desk"},
                {"id": self.remote_id, "path": "~/valid", "target": "unknown"},
                {"id": self.local_id, "path": "/tmp/\0bad"}):
            with self.subTest(record=record), self.assertRaises(terminal.TermError):
                terminal.workspace_spec(record, ["desk"])
        empty_bin = self.root / "empty-bin"
        empty_bin.mkdir()
        (empty_bin / "bash").symlink_to("/bin/bash")
        spec = terminal.workspace_spec({"id": self.local_id, "path": str(self.root)}, [])
        result = subprocess.run(spec["argv"], env={**self.env, "PATH": str(empty_bin)},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("tmux is required", result.stderr)
        self.assertIn("terminal failed", result.stderr)

    def test_remote_connection_failure_stays_visible_and_does_not_wake_vm(self):
        mctl = self.bin / "mctl"
        mctl.write_text('#!/bin/sh\nprintf "%s\\n" "VM_IP=${VM_IP-unset}" '
                        '"MCTL_AUTOSTART=$MCTL_AUTOSTART" "$1" "$2" "$3" > "$CAPTURE"\n'
                        'echo "VM is stopped" >&2\nexit 23\n')
        mctl.chmod(0o755)
        with patch.object(terminal, "MCTL", mctl):
            spec = terminal.workspace_spec(
                {"id": self.remote_id, "path": "~/workspace", "target": "vm"}, ["vm"])
        proc, master = terminal.spawn(
            spec["argv"], {**self.env, "VM_IP": "stale", "MCTL_AUTOSTART": "1"})
        self.addCleanup(os.close, master)
        self.addCleanup(terminal._stop, proc)
        output = b""
        deadline = time.monotonic() + 8
        while b"Press Enter to close." not in output and time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                output += os.read(master, 65536)
            if proc.poll() is not None:
                break
        self.assertIn(b"VM is stopped", output)
        self.assertIn(b"failed (status 23)", output)
        self.assertIn(b"Press Enter to close.", output)
        self.assertIsNone(proc.poll())
        self.assertEqual(self.capture.read_text().splitlines(),
                         ["VM_IP=unset", "MCTL_AUTOSTART=0", "-t", "vm", "term"])
        os.write(master, b"\r")
        self.assertEqual(proc.wait(timeout=8), 23)


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class WorkspaceTmuxIntegrationTests(unittest.TestCase):
    def test_isolated_sessions_start_at_root_and_reattach_to_existing_directory(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        bin_ = root / "bin"
        home = root / "home"
        first = root / "workspace one"
        second = root / "workspace two"
        moved = root / "elsewhere"
        for directory in (bin_, home, first, second, moved):
            directory.mkdir()
        tmux = shutil.which("tmux")
        socket_path = root / "isolated-tmux.sock"
        wrapper = bin_ / "tmux"
        wrapper.write_text('#!/bin/sh\nexec "$REAL_TMUX" -S "$TMUX_SOCKET" -f /dev/null "$@"\n')
        wrapper.chmod(0o755)
        env = dict(os.environ, HOME=str(home), PATH=f"{bin_}:{os.environ['PATH']}",
                   REAL_TMUX=tmux, TMUX_SOCKET=str(socket_path), TMUX="stale,1,1", SHELL="/bin/bash")
        control = [tmux, "-S", str(socket_path), "-f", "/dev/null"]
        clients = []

        def stop_server():
            subprocess.run([*control, "kill-server"], env=env, capture_output=True)

        def stop_clients():
            for proc, master in clients:
                terminal._stop(proc)
                os.close(master)

        self.addCleanup(stop_server)
        self.addCleanup(stop_clients)

        def query(*args):
            return subprocess.run([*control, *args], env=env, capture_output=True, text=True)

        def wait_for(predicate, proc, master):
            deadline = time.monotonic() + 8
            output = b""
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0)[0]:
                    output += os.read(master, 65536)
                if predicate():
                    return
                if proc.poll() is not None:
                    self.fail(f"terminal exited early ({proc.returncode}): {output!r}")
                time.sleep(.05)
            self.fail(f"tmux did not reach expected state: {output!r}")

        def open_workspace(ident, directory):
            spec = terminal.workspace_spec({"id": ident, "path": str(directory)}, [])
            proc, master = terminal.spawn(spec["argv"], env)
            clients.append((proc, master))
            name = "portal-ws-" + ident
            wait_for(lambda: bool(query("list-clients", "-t", name).stdout.strip()), proc, master)
            return proc, master, name

        def current_path(name):
            return query("display-message", "-p", "-t", name + ":0.0",
                         "#{pane_current_path}").stdout.strip()

        def detach(proc, master):
            os.write(master, b"\x02d")
            self.assertEqual(proc.wait(timeout=8), 0)

        first_id = "WS-L-" + "1" * 32
        proc, master, first_name = open_workspace(first_id, first)
        wait_for(lambda: current_path(first_name) == str(first), proc, master)
        os.write(master, ("cd " + shlex.quote(str(moved)) + "\r").encode())
        wait_for(lambda: current_path(first_name) == str(moved), proc, master)
        terminal._stop(proc)
        self.assertEqual(query("has-session", "-t", first_name).returncode, 0)

        proc, master, _ = open_workspace(first_id, first)
        self.assertEqual(current_path(first_name), str(moved))
        detach(proc, master)

        proc, master, second_name = open_workspace("WS-L-" + "2" * 32, second)
        wait_for(lambda: current_path(second_name) == str(second), proc, master)
        self.assertEqual(current_path(first_name), str(moved))
        detach(proc, master)


class SessionTests(unittest.TestCase):
    def test_pty_session_echoes_input_resizes_and_reports_exit(self):
        server, client = socket.socketpair()
        self.addCleanup(client.close)
        spec = {"argv": ["bash", "--norc", "-c",
                         "test -t 0 && echo TTY; read line; echo got:$line; "
                         "stty size; exit 3"],
                "title": "test", "kind": "shell", "target": "local"}
        ended = []
        t = threading.Thread(target=lambda: ended.append(
            terminal.serve(server, spec, dict(os.environ))), daemon=True)
        t.start()
        client.sendall(client_frame(json.dumps({"t": "r", "c": 101, "r": 33}).encode()))
        client.sendall(client_frame(json.dumps({"t": "i", "d": "hello\r"}).encode()))
        client.settimeout(10)
        data = b""
        while True:
            try:
                chunk = client.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
        t.join(10)
        frames = server_frames(data)
        text = b"".join(p for op, p in frames if op == 0x2).decode("utf-8", "replace")
        self.assertIn("TTY", text)
        self.assertIn("got:hello", text)
        self.assertIn("33 101", text)
        exits = [json.loads(p) for op, p in frames if op == 0x1]
        self.assertEqual(exits[-1]["code"], 3)
        self.assertEqual(frames[-1][0], 0x8)
        self.assertEqual(ended, [3])
        self.assertEqual(terminal.active(), [])



class WindowsTerminalTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_shell_spec_session_names(self):
        spec = terminal.shell_spec("vm", ["vm"], "portal-3")
        self.assertIn("tmux new -A -s portal-3", spec["argv"][-1])
        self.assertIn("-s portal ", terminal.shell_spec("vm", ["vm"], "")["argv"][-1])
        for bad in ("portal-123", "other", "portal;rm"):
            with self.assertRaises(terminal.TermError):
                terminal.shell_spec("vm", ["vm"], bad)

    def test_uart_spec_only_for_listed_consoles(self):
        consoles = [{"id": "bmc2200", "label": "OAM 0"}]
        spec = terminal.uart_spec("srv12", "bmc2200", consoles)
        self.assertEqual(spec["argv"][1:], ["-t", "srv12", "uart", "attach", "bmc2200"])
        self.assertEqual(spec["kind"], "uart")
        self.assertIn("OAM 0", spec["title"])
        for bad in ("bmc2201", "-x", "a b"):
            with self.assertRaises(terminal.TermError):
                terminal.uart_spec("srv12", bad, consoles)

    def test_issue_token_respects_limit(self):
        spec = {"argv": ["true"], "title": "t", "kind": "shell", "target": "vm"}
        self.addCleanup(terminal._ACTIVE.clear)
        terminal._ACTIVE.update({"a": {}, "b": {}})
        with self.assertRaises(terminal.TermError):
            terminal.issue_token(spec, now=5000.0, limit=2)
        terminal.take_token(terminal.issue_token(spec, now=5000.0, limit=3), now=5000.0)

    def test_grid_rows_layouts(self):
        self.assertEqual(terminal.grid_rows(1, "grid"), [[0]])
        self.assertEqual(terminal.grid_rows(4, "grid"), [[0, 1], [2, 3]])
        self.assertEqual(terminal.grid_rows(8, "grid"), [[0, 1, 2], [3, 4, 5], [6, 7]])
        self.assertEqual(terminal.grid_rows(3, "rows"), [[0], [1], [2]])
        self.assertEqual(terminal.grid_rows(3, "columns"), [[0, 1, 2]])

    def test_wt_args_builds_rows_then_columns(self):
        panes = [(f"p{i}", ["cmd", str(i)]) for i in range(4)]
        args = terminal.wt_args(panes, "grid", "portal")
        self.assertEqual(args[:3], ["-w", "portal", "new-tab"])
        joined = " ".join(args)
        self.assertEqual(joined.count("split-pane -H"), 1)
        self.assertEqual(joined.count("split-pane -V"), 2)
        self.assertIn("focus-pane -t 0 ; split-pane -V -s 0.500", joined)
        self.assertIn("focus-pane -t 1 ; split-pane -V -s 0.500", joined)
        self.assertEqual(args[-4:], [";", "focus-pane", "-t", "0"])
        self.assertEqual(terminal.wt_args(panes[:1], "grid", "w")[:3], ["-w", "w", "new-tab"])
        self.assertNotIn("split-pane", terminal.wt_args(panes[:1], "grid", "w"))
        self.assertEqual(" ".join(terminal.wt_args(panes[:3], "rows", "w")).count("-s 0.667"), 1)

    def test_title_strips_wt_separators(self):
        self.assertEqual(terminal._title('a;b"c\nd'), "a b c d")
        self.assertEqual(len(terminal._title("x" * 200)), 80)
        args = terminal.wt_args([("evil; new-tab calc", ["c"])], "grid", "w")
        self.assertEqual(args.count(";"), 0)

    def test_wt_launch_writes_one_time_specs(self):
        calls = []

        class Out:
            returncode, stdout, stderr = 0, b"", b""

        def run(argv, **kw):
            calls.append(argv)
            return Out()
        specs = [{"argv": ["/bin/echo", "a;b"], "title": "one"}, {"argv": ["/bin/true"], "title": "two"}]
        res = terminal.wt_launch(specs, "grid", "portal", "", {"SSH_AUTH_SOCK": "/s", "EVIL": "x"},
                                 folder=self.folder, wt="/x/wt.exe", run=run, now=100.0)
        self.assertEqual(res["panes"], 2)
        argv = calls[0]
        self.assertEqual(argv[0], "/x/wt.exe")
        self.assertNotIn("a;b", argv)
        self.assertNotIn("-d", argv)
        files = sorted(self.folder.glob("*.json"))
        self.assertEqual(len(files), 2)
        data = [json.loads(f.read_text()) for f in files]
        self.assertTrue(all(d["env"] == {"SSH_AUTH_SOCK": "/s"} for d in data))
        self.assertTrue(all(oct(f.stat().st_mode)[-3:] == "600" for f in files))
        ids = [a for a in argv if terminal.LAUNCH_ID_RE.match(a)]
        self.assertEqual(sorted(ids), sorted(f.stem for f in files))

    def test_wt_launch_distro_and_failure_cleanup(self):
        class Out:
            returncode, stdout, stderr = 1, b"", b"boom"
        seen = []
        with self.assertRaisesRegex(terminal.TermError, "boom"):
            terminal.wt_launch([{"argv": ["x"], "title": "t"}], "grid", "portal", "Ubuntu-24.04",
                               folder=self.folder, wt="/x/wt.exe",
                               run=lambda a, **k: seen.append(a) or Out(), now=1.0)
        self.assertIn("Ubuntu-24.04", seen[0])
        self.assertEqual(list(self.folder.glob("*.json")), [])
        for kw in ({"layout": "diag"}, {"window": "a b"}, {"distro": "-x"}):
            args = dict(layout="grid", window="portal", distro="")
            args.update(kw)
            with self.assertRaises(terminal.TermError):
                terminal.wt_launch([{"argv": ["x"]}], folder=self.folder, wt="/x/wt.exe",
                                   run=lambda a, **k: Out(), **args)
        with self.assertRaises(terminal.TermError):
            terminal.wt_launch([{"argv": ["x"]}] * (terminal.WT_MAX_PANES + 1), folder=self.folder,
                               wt="/x/wt.exe", run=lambda a, **k: Out())

    def test_run_launch_is_one_time_and_expires(self):
        lid = terminal._write_launch({"argv": ["/bin/true"], "title": "t"}, {}, self.folder, 100.0)
        import builtins
        from unittest import mock
        with mock.patch.object(builtins, "input", side_effect=EOFError), \
                mock.patch("sys.stdout"):
            self.assertEqual(terminal.run_launch(lid, self.folder, now=100.0 + terminal.LAUNCH_TTL + 1), 1)
            self.assertFalse((self.folder / f"{lid}.json").exists())
            self.assertEqual(terminal.run_launch(lid, self.folder, now=100.0), 1)
            self.assertEqual(terminal.run_launch("../etc", self.folder, now=100.0), 1)
        lid = terminal._write_launch({"argv": ["/bin/true"], "title": "t"}, {"SSH_AUTH_SOCK": "/s"},
                                     self.folder, 100.0)
        with mock.patch("os.execvpe") as ex, mock.patch("os.chdir"), mock.patch("sys.stdout"), \
                mock.patch.dict("os.environ", {"PATH": "/usr/bin:/bin"}):
            terminal.run_launch(lid, self.folder, now=101.0)
        ex.assert_called_once()
        self.assertEqual(ex.call_args[0][1], ["/bin/true"])
        self.assertEqual(ex.call_args[0][2]["SSH_AUTH_SOCK"], "/s")
        home = os.path.expanduser("~")
        self.assertEqual(ex.call_args[0][2]["PATH"],
                         f"{home}/.local/bin:{home}/bin:/usr/bin:/bin")
        self.assertFalse((self.folder / f"{lid}.json").exists())

if __name__ == "__main__":
    unittest.main()
