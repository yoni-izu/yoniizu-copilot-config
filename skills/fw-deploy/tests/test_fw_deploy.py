import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fw_deploy.py"
SPEC = importlib.util.spec_from_file_location("fw_deploy", SCRIPT)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


MCTL = r"""#!/usr/bin/env python3
import os, subprocess, sys
from pathlib import Path
assert os.environ.get("MCTL_AUTOSTART") == "0"
if os.environ.get("TEST_UNREACHABLE"):
    print("target unreachable", file=sys.stderr)
    sys.exit(7)
args = sys.argv[1:]
assert args[:1] == ["-t"]
if args[2] == "dest":
    print("test@machine")
elif args[2] == "sh":
    if "base64.encode" in args[3]:
        mode = os.environ.get("TEST_STREAM", "")
        if mode == "corrupt":
            print("AA==")
            sys.exit(0)
        if mode == "noise":
            print("not base64!")
            sys.exit(0)
        if mode == "failed":
            print("AA==")
            sys.exit(9)
    sys.exit(subprocess.run(["bash", "-c", args[3]], cwd=args[4]).returncode)
else:
    raise SystemExit("unexpected mctl operation")
"""

RSYNC = r"""#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
args = sys.argv[1:]
assert all(flag in args for flag in (
    "--no-owner", "--no-group", "--ignore-existing", "--protect-args"))
files = args[args.index("--") + 1:]
destination = Path(files[-1].split(":", 1)[1])
if os.environ.get("TEST_RSYNC_FAILED"):
    raise SystemExit(23)
for source in files[:-1]:
    path = destination / Path(source).name
    if not path.exists():
        shutil.copyfile(source, path)
if os.environ.get("TEST_RSYNC_CORRUPT"):
    (destination / Path(files[0]).name).write_bytes(b"corrupt")
"""

FINDMNT = r"""#!/usr/bin/env python3
import os
print(os.environ.get("TEST_FSTYPE", "nfs4"))
"""


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / "flash_images"
        self.image = self.home / "image with ' quotes.bin"
        self.image.write_bytes(bytes(range(256)) * 1024 + b"\x00\x00")
        self.notes = self.home / "notes.md"
        self.notes.write_text("Baseline build ID unknown.\nFeature commit: test fixture.\n")
        self.tools = self.home / "bin"
        self.tools.mkdir()
        for name, content in (("mctl", MCTL), ("rsync", RSYNC), ("findmnt", FINDMNT)):
            path = self.tools / name
            path.write_text(content)
            path.chmod(0o755)
        env = {
            "PATH": str(self.tools) + os.pathsep + os.environ["PATH"],
            "HOME": str(self.home),
        }
        self.environ = patch.dict(os.environ, env)
        self.environ.start()
        self.addCleanup(self.environ.stop)

    def call(self, *args, status=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            actual = deploy.main(["--root", str(self.root), *map(str, args)])
        self.assertEqual(actual, status, stderr.getvalue())
        return stdout.getvalue().strip(), stderr.getvalue()

    def save(self, *extra, command="import", topic="diagnostic"):
        output, _ = self.call(
            command, self.image, "--project", "iridium", "--topic", topic,
            "--platform", "arm", "--variant", "asic", "--notes", self.notes, *extra)
        return Path(output)

    def machine(self):
        return ["--target", "lab", "--workspace", str(self.home)]

    def test_import_identity_notes_naming_and_default_root(self):
        directory = self.save()
        self.assertEqual(directory.parent.parent, self.root / "iridium")
        self.assertRegex(directory.parent.name, r"^\d{8}_\d{6}_diagnostic$")
        self.assertEqual(deploy.parser().parse_args(["list"]).root, "~/flash_images")
        self.assertEqual((directory / self.image.name).read_bytes(), self.image.read_bytes())
        notes = (directory / "CHANGE_NOTES.md").read_text()
        self.assertIn(self.notes.read_text().strip(), notes)
        self.assertIn("Project: iridium", notes)
        self.call("verify", directory)
        output, _ = self.call("list", "--project", "iridium")
        self.assertIn(str(directory), output)

    def test_list_json_reports_identity_and_flags_tampered_bundle(self):
        good = self.save(topic="good")
        bad = self.save(topic="bad")
        (bad / "CHANGE_NOTES.md").write_text("tampered")
        output, _ = self.call("list", "--json")
        data = json.loads(output)
        self.assertEqual(data["root"], str(self.root))
        by_dir = {item["directory"]: item for item in data["images"]}
        item = by_dir[str(good)]
        self.assertTrue(item["valid"])
        self.assertEqual(item["project"], "iridium")
        self.assertEqual(item["variant"], "asic")
        self.assertEqual(item["path"], str(good / self.image.name))
        self.assertEqual(item["size"], self.image.stat().st_size)
        self.assertEqual(len(item["sha256"]), 64)
        self.assertFalse(by_dir[str(bad)]["valid"])
        self.assertIn("error", by_dir[str(bad)])
        output, _ = self.call("list", "--json", "--project", "atlantic2")
        self.assertEqual(json.loads(output)["images"], [])

    def test_import_without_variant_and_copy_locally(self):
        output, _ = self.call(
            "import", self.image, "--project", "atlantic2", "--topic", "test",
            "--platform", "host", "--notes", self.notes)
        directory = Path(output)
        self.assertEqual(directory.parent, self.root / "atlantic2")
        destination = self.home / "anywhere" / "new directory"
        self.call("copy", directory, "--destination", destination)
        self.call("verify", directory, "--destination", destination)
        self.assertEqual(len(list(destination.iterdir())), 3)

    def test_collision_preserves_existing_bundle(self):
        now = deploy.datetime.now(deploy.timezone.utc)
        with patch.object(deploy, "datetime") as clock:
            clock.now.return_value = now
            directory = self.save()
            self.call("import", self.image, "--project", "iridium", "--topic", "diagnostic",
                      "--platform", "arm", "--notes", self.notes, status=1)
        self.call("verify", directory)

    def test_empty_and_missing_source_never_publish(self):
        for content in (b"", None):
            if content is None:
                self.image.unlink()
            else:
                self.image.write_bytes(content)
            self.call("import", self.image, "--project", "iridium", "--topic", "invalid",
                      "--platform", "arm", "--notes", self.notes, status=1)
        self.assertEqual(list(self.root.glob("**/deployment.json")), [])
        self.assertEqual(list((self.root / "iridium").iterdir()), [])

    def test_local_copy_conflict_and_idempotence(self):
        directory = self.save()
        destination = self.home / "copy"
        self.call("copy", directory, "--destination", destination)
        self.call("copy", directory, "--destination", destination)
        (destination / "CHANGE_NOTES.md").write_text("user-owned notes")
        _, error = self.call("copy", directory, "--destination", destination, status=1)
        self.assertIn("mismatch", error)
        self.assertEqual((destination / "CHANGE_NOTES.md").read_text(), "user-owned notes")

    def test_tampering_and_symlink_are_rejected(self):
        directory = self.save()
        notes = directory / "CHANGE_NOTES.md"
        notes.write_text("tampered")
        self.call("verify", directory, status=1)
        self.call("copy", directory, "--destination", self.home / "copy", status=1)
        notes.unlink()
        notes.symlink_to(self.notes)
        self.call("verify", directory, status=1)

    def test_remote_binary_pull_preserves_nuls_and_source_time(self):
        directory = self.save(*self.machine(), command="pull")
        image = directory / self.image.name
        self.assertEqual(image.read_bytes(), self.image.read_bytes())
        self.assertEqual(image.stat().st_mtime_ns, self.image.stat().st_mtime_ns)
        self.call("verify", directory)

    def test_failed_or_corrupt_remote_pull_is_not_published(self):
        for mode in ("corrupt", "noise", "failed"):
            with self.subTest(mode=mode), patch.dict(os.environ, {"TEST_STREAM": mode}):
                self.call("pull", self.image, *self.machine(),
                          "--project", "iridium", "--topic", mode, "--platform", "arm",
                          "--notes", self.notes, status=1)
        self.assertEqual(list(self.root.glob("**/deployment.json")), [])

    def test_remote_copy_and_cross_machine_verify(self):
        directory = self.save()
        destination = self.home / "machine images" / "quote's directory"
        self.call("copy", directory, *self.machine(), "--destination", destination)
        self.call("verify", directory, "--target", "second", "--workspace", self.home,
                  "--destination", destination)
        self.call("copy", directory, *self.machine(), "--destination", destination)
        self.assertEqual((destination / self.image.name).read_bytes(), self.image.read_bytes())

    def test_remote_conflict_preserves_destination(self):
        directory = self.save()
        destination = self.home / "remote"
        destination.mkdir()
        (destination / self.image.name).write_bytes(b"original")
        self.call("copy", directory, *self.machine(), "--destination", destination, status=1)
        self.assertEqual((destination / self.image.name).read_bytes(), b"original")
        self.assertFalse((destination / "CHANGE_NOTES.md").exists())

    def test_rsync_error_or_corruption_is_failure(self):
        directory = self.save()
        for flag in ("TEST_RSYNC_FAILED", "TEST_RSYNC_CORRUPT"):
            with self.subTest(flag=flag), patch.dict(os.environ, {flag: "1"}):
                self.call("copy", directory, *self.machine(),
                          "--destination", self.home / flag, status=1)

    def test_nfs_mirrors_preserve_hierarchy_only_when_requested(self):
        directory = self.save()
        mirror = self.home / "nfs"
        self.assertFalse(mirror.exists())
        self.call("mirror", directory, "--nfs-root", mirror)
        expected = mirror / directory.relative_to(self.root)
        self.call("verify", directory, "--destination", expected)
        remote_root = self.home / "remote nfs"
        self.call("mirror", directory, *self.machine(), "--nfs-root", remote_root)
        self.call("verify", directory, *self.machine(),
                  "--destination", remote_root / directory.relative_to(self.root))

    def test_non_nfs_mirror_and_unreachable_target_fail_without_losing_local(self):
        directory = self.save()
        with patch.dict(os.environ, {"TEST_FSTYPE": "ext4"}):
            self.call("mirror", directory, "--nfs-root", self.home / "mirror", status=1)
            self.call("mirror", directory, *self.machine(),
                      "--nfs-root", self.home / "mirror", status=1)
        with patch.dict(os.environ, {"TEST_UNREACHABLE": "1"}):
            self.call("copy", directory, *self.machine(),
                      "--destination", self.home / "offline", status=1)
        self.call("verify", directory)

    def test_required_remote_arguments_and_device_destination_rejected(self):
        directory = self.save()
        self.call("copy", directory, "--target", "lab",
                  "--destination", self.home / "remote", status=1)
        self.call("verify", directory, "--target", "lab", status=1)
        for destination in ("/", "/dev/null", "relative/path"):
            self.call("copy", directory, "--destination", destination, status=1)
            self.call("copy", directory, *self.machine(),
                      "--destination", destination, status=1)

    def test_manifest_path_escape_is_rejected(self):
        directory = self.save()
        path = directory / "deployment.json"
        metadata = json.loads(path.read_text())
        metadata["image"] = "../outside.bin"
        path.write_text(json.dumps(metadata))
        self.call("verify", directory, status=1)

    def test_project_symlink_and_device_directory_alias_rejected(self):
        self.root.mkdir()
        (self.root / "iridium").symlink_to(self.home, target_is_directory=True)
        self.call("import", self.image, "--project", "iridium", "--topic", "test",
                  "--platform", "arm", "--notes", self.notes, status=1)
        (self.root / "iridium").unlink()
        directory = self.save()
        alias = self.home / "device-alias"
        alias.symlink_to("/dev", target_is_directory=True)
        self.call("copy", directory, "--destination", alias, status=1)
        self.call("copy", directory, *self.machine(), "--destination", alias, status=1)

    def test_empty_notes_missing_tool_and_cli_help(self):
        self.notes.write_text("")
        self.call("import", self.image, "--project", "iridium", "--topic", "test",
                  "--platform", "arm", "--notes", self.notes, status=1)
        result = subprocess.run(["python3", str(SCRIPT), "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("mirror", result.stdout)
        with patch.object(deploy.subprocess, "run", side_effect=FileNotFoundError("mctl")):
            self.notes.write_text("Baseline unknown")
            self.call("pull", self.image, *self.machine(), "--project", "iridium",
                      "--topic", "test", "--platform", "arm", "--notes", self.notes, status=1)


if __name__ == "__main__":
    unittest.main()
