import os
from pathlib import Path
import subprocess
import tempfile
import unittest


BIN = Path(__file__).resolve().parent
REPO = BIN.parent


class CliInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.prefix = self.home / "bin"
        self.env = dict(os.environ, HOME=str(self.home), PREFIX=str(self.prefix))
        for key in ("MCTL_DEFAULT_USER", "BMC", "BMC_HOST", "PWF", "BMC_PW_FILE"):
            self.env.pop(key, None)

    def run_command(self, command, *args, env=None):
        return subprocess.run(
            [str(command), *args], env=env or self.env, text=True,
            capture_output=True, timeout=15,
        )

    def setting(self, name, value):
        root = self.home / ".copilot-private" / "cli"
        root.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(value)

    def test_link_install_and_idempotence(self):
        for _ in range(2):
            result = self.run_command(BIN / "install.sh")
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.prefix / "mctl").resolve(), BIN / "mctl")
        self.assertEqual((self.prefix / "completions/wip.bash").resolve(),
                         BIN / "completions/wip.bash")
        for name in ("mctl", "vmctl"):
            result = self.run_command(self.prefix / name, "--help")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("mctl", result.stdout)

    def test_copy_install_includes_shim_dependency(self):
        result = self.run_command(BIN / "install.sh", "--copy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.prefix / "mctl").is_symlink())
        self.assertTrue((self.prefix / "cli-local-config.sh").is_file())
        result = self.run_command(self.prefix / "vmctl", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_command(BIN / "install.sh", "--copy")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_conflict_does_not_replace_or_install_anything(self):
        self.prefix.mkdir()
        original = self.prefix / "mctl"
        original.write_text("do not overwrite")
        result = self.run_command(BIN / "install.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertEqual(original.read_text(), "do not overwrite")
        self.assertFalse((self.prefix / "wip").exists())

    def test_remote_user_private_setting_and_override(self):
        self.setting("default-remote-user", "private-user\n")
        result = self.run_command(BIN / "mctl", "-t", "test-host", "dest")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "private-user@test-host")
        env = dict(self.env, MCTL_DEFAULT_USER="override")
        result = self.run_command(BIN / "mctl", "-t", "test-host", "dest", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "override@test-host")
        self.setting("default-remote-user", "")
        result = self.run_command(BIN / "mctl", "-t", "test-host", "dest")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("empty CLI setting", result.stderr)

    def test_setup_machine_uses_configured_user(self):
        fakebin = self.home / "fakebin"
        fakebin.mkdir()
        ssh = fakebin / "ssh"
        ssh.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 42\n')
        ssh.chmod(0o755)
        self.setting("default-remote-user", "private-user\n")
        env = dict(self.env, PATH=f"{fakebin}:{self.env['PATH']}")
        result = self.run_command(BIN / "setup_machine.sh", "test-host", env=env)
        self.assertEqual(result.returncode, 42)
        self.assertIn("private-user@test-host", result.stdout)
        result = self.run_command(BIN / "setup_machine.sh", "test-host", "explicit", env=env)
        self.assertEqual(result.returncode, 42)
        self.assertIn("explicit@test-host", result.stdout)

    def test_lab_clients_require_or_use_private_host(self):
        result = self.run_command(BIN / "lsctl", "start", "run")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("configure lab-bmc-host", result.stderr)
        result = self.run_command(BIN / "bmcjob", "list")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("configure lab-bmc-host", result.stderr)

        fakebin = self.home / "fakebin"
        fakebin.mkdir()
        sshpass = fakebin / "sshpass"
        sshpass.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        sshpass.chmod(0o755)
        password = self.home / "password"
        password.write_text("fixture\n")
        self.setting("lab-bmc-host", "private-host\n")
        env = dict(self.env, PATH=f"{fakebin}:{self.env['PATH']}", BMC_PW_FILE=str(password))
        result = self.run_command(BIN / "bmcjob", "list", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("root@private-host", result.stdout)
        result = self.run_command(BIN / "bmcjob", "-H", "explicit-host", "list", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("root@explicit-host", result.stdout)

    def test_fw_deploy_launcher_uses_repository_implementation(self):
        (self.home / ".copilot").symlink_to(REPO, target_is_directory=True)
        result = self.run_command(BIN / "fw-deploy", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
