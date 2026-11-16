"""Regression: a jobs-probe line truncated mid-character must not abort the probe."""
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import vd


class ProbeDecodeTest(unittest.TestCase):
    def test_truncated_multibyte_is_replaced_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "mctl"
            # 0xe2 0x80 is the first two bytes of an em dash cut by `cut -c`.
            fake.write_bytes(b"#!/bin/sh\nprintf 'ws1\\tbuild\\tdone\\t0\\t\\t1\\t5\\t0\\tocti\\tlast \\342\\200\\n'\n")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            with mock.patch.object(vd, "MCTL", fake), \
                    mock.patch.object(vd, "agent_env", return_value=(dict(os.environ), "")):
                rows, err, _ = vd._probe_once("vm")
        self.assertEqual(err, "")
        self.assertIn(("ws1", "build"), rows)
        self.assertTrue(rows[("ws1", "build")]["last_line"].startswith("last "))


if __name__ == "__main__":
    unittest.main()
