"""mctl per-target BMC column: registry edits, listing and the `bmc` command.

Runs the real ~/bin/mctl with HOME pointed at a temp dir (MCTL_DIR is
$HOME/.mctl), and a fake ssh/sshpass on PATH, so nothing real is touched.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

MCTL = Path.home() / "bin" / "mctl"
REGISTRY = (
    "# mctl targets\n"
    "dsk61   remote  yoni yoni-host\n"
    "vm      vm      yoni i-012e7b37b8bfe283b\n"
)


class MctlBmc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        (self.home / ".mctl").mkdir()
        self.reg = self.home / ".mctl" / "targets"
        self.reg.write_text(REGISTRY)
        bin_ = self.home / "fakebin"
        bin_.mkdir()
        for tool in ("ssh", "sshpass"):
            f = bin_ / tool
            f.write_text('#!/bin/sh\necho "%s pw=${SSHPASS:-none} $*"\n' % tool)
            f.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), PATH=f"{bin_}:{os.environ['PATH']}")

    def tearDown(self):
        self.tmp.cleanup()

    def mctl(self, *args):
        return subprocess.run([str(MCTL), *args], env=self.env, capture_output=True, text=True, timeout=20)

    def test_set_list_and_clear(self):
        self.assertEqual(self.mctl("target", "bmc", "dsk61", "bmc-sa-dsk61-csr").returncode, 0)
        self.assertIn("dsk61\tremote\tyoni\tyoni-host\tbmc-sa-dsk61-csr", self.reg.read_text())
        self.assertIn("bmc-sa-dsk61-csr", self.mctl("targets").stdout)
        self.assertEqual(self.mctl("-t", "dsk61", "dest").stdout.strip(), "yoni@yoni-host")
        self.assertEqual(self.mctl("target", "bmc", "dsk61", "-").returncode, 0)
        self.assertIn("dsk61\tremote\tyoni\tyoni-host\n", self.reg.read_text())

    def test_rejects_vm_unknown_and_unsafe(self):
        for args in (("vm", "bmc-x"), ("nobody", "bmc-x"), ("dsk61", "x;rm"), ("dsk61", "-oProxyCommand=x")):
            r = self.mctl("target", "bmc", *args)
            self.assertNotEqual(r.returncode, 0, args)
        self.assertEqual(self.reg.read_text(), REGISTRY)
        self.assertNotEqual(self.mctl("target", "add", "v2", "--vm", "--bmc", "b").returncode, 0)

    def test_add_with_bmc(self):
        self.assertEqual(self.mctl("target", "add", "box", "u@h1", "--bmc", "admin@bmc-h1").returncode, 0)
        self.assertIn("box\tremote\tu\th1\tadmin@bmc-h1\n", self.reg.read_text())
        self.assertEqual(self.mctl("-t", "box", "dest").stdout.strip(), "u@h1")

    def test_bmc_command_uses_password_file_via_env(self):
        self.mctl("target", "bmc", "dsk61", "bmc-sa-dsk61-csr")
        r = self.mctl("-t", "dsk61", "bmc")
        self.assertIn("ssh pw=none -tt", r.stdout)          # no password file: plain ssh prompt
        self.assertIn("root@bmc-sa-dsk61-csr", r.stdout)
        (self.home / ".pilot").mkdir()
        (self.home / ".pilot" / "bmc.pw").write_text("s3cret\n")
        r = self.mctl("-t", "dsk61", "bmc")
        self.assertIn("sshpass pw=s3cret -e ssh -tt", r.stdout)
        self.assertNotIn("s3cret -e", r.stdout.split("pw=s3cret", 1)[1])  # not in argv
        self.assertNotEqual(self.mctl("-t", "vm", "bmc").returncode, 0)
        self.mctl("target", "bmc", "dsk61", "-")
        self.assertIn("has no BMC", self.mctl("-t", "dsk61", "bmc").stderr)


if __name__ == "__main__":
    unittest.main()
