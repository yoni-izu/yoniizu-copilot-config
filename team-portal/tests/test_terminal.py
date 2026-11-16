"""Browser terminal: WebSocket framing, one-time tokens and a real pty session."""
import json
import os
import socket
import struct
import sys
import threading
import unittest
from pathlib import Path

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

    def test_shell_spec_only_for_configured_targets(self):
        with self.assertRaises(terminal.TermError):
            terminal.shell_spec("dsk61", ["vm"])
        spec = terminal.shell_spec("vm", ["vm"])
        self.assertEqual(spec["argv"][1:4], ["-t", "vm", "term"])
        self.assertIn("tmux new -A -s portal", spec["argv"][-1])

    def test_log_spec_quotes_remote_script_and_passes_errors(self):
        spec = terminal.log_spec({"mode": "remote", "target": "vm", "title": "x",
                                  "command": "echo 'a'; tail b.log"})
        self.assertEqual(spec["argv"][-1], "bash -lc 'echo '\"'\"'a'\"'\"'; tail b.log'")
        with self.assertRaises(terminal.TermError):
            terminal.log_spec({"error": "gone"})
        # Logs are only ever shown on their machine; no local viewer.
        with self.assertRaises(terminal.TermError):
            terminal.log_spec({"mode": "local", "argv": ["less", "/x"], "title": "y"})


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


if __name__ == "__main__":
    unittest.main()
