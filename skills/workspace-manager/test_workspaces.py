import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import wm
import workspace_runtime as runtime


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.store = self.base / "workspaces.db"
        self.patch = patch.object(wm, "STORE", self.store)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        legacy = patch.object(wm.workspace_store, "LEGACY", self.base / "workspaces.json")
        legacy.start()
        self.addCleanup(legacy.stop)
        self.local = self.base / "one"
        self.local.mkdir()
        wm.save({"slots": {}, "tracked": {"one": {
            "path": str(self.local), "label": "one", "rev": "main"}}, "groups": []})

    def test_migration_and_remote_collision_have_stable_unique_ids(self):
        first = wm.record("one")["id"]
        self.assertTrue(first.startswith("WS-L-"))
        self.assertEqual(first, wm.record("one")["id"])
        with patch.object(wm, "machine_targets", return_value=["vm"]):
            ok, _, slot = wm.track(str(self.local), target="vm")
        self.assertTrue(ok)
        remote = wm.record(slot)
        self.assertTrue(remote["id"].startswith("WS-R-"))
        self.assertNotEqual(first, remote["id"])
        self.assertEqual(wm.normalize_slot(first), "one")
        with self.assertRaisesRegex(ValueError, "through mctl"):
            wm.slot_path(slot)
        self.assertIsNone(next(r for r in wm.roster() if r["slot"] == slot)["exists"])

    def test_clean_guard_and_no_task_displacement(self):
        with patch.object(wm, "workspace_request", return_value={"clean": False, "reasons": ["dirty"]}):
            self.assertFalse(wm.occupy_task("one", "T-1")[0])
        with patch.object(wm, "workspace_request", return_value={"clean": True}):
            self.assertTrue(wm.occupy_task("one", "T-1")[0])
            self.assertFalse(wm.occupy_task("one", "T-2", force=True)[0])
        self.assertEqual(wm.get_holder("one")["task_id"], "T-1")
        self.assertTrue(any(e["outcome"] == "rejected" for e in wm.action_log("one")))

    def test_cached_roster_uses_saved_readings_without_filesystem_probe(self):
        data = wm.load()
        is_dir = Path.is_dir
        def guard(path):
            if path == self.local:
                raise AssertionError("live workspace directory probe")
            return is_dir(path)
        with patch.object(wm, "status_read", return_value={"exists": False}) as read, \
                patch.object(Path, "is_dir", autospec=True, side_effect=guard), \
                patch.object(subprocess, "run", side_effect=AssertionError("live command")):
            rows = wm.roster(data, cached_only=True)
        self.assertFalse(rows[0]["exists"])
        self.assertEqual(rows[0]["status"], {"exists": False})
        read.assert_called_once_with(data["tracked"]["one"]["id"])

    def test_failed_probe_preserves_success_timestamp_and_mirror(self):
        wm.status_write("one", {"checked": "2026-01-01", "mirror": {"state": "active"}})
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 180)):
            with self.assertRaises(ValueError):
                wm.workspace_request("one")
        reading = wm.status_read(wm.record("one")["id"])
        self.assertEqual(reading["checked"], "2026-01-01")
        self.assertEqual(reading["mirror"]["state"], "active")
        self.assertIn("error", reading)

    def test_mirror_lifecycle_and_unknown_machine(self):
        with patch.object(wm, "machine_targets", return_value=["vm"]):
            rec = wm.mirror_state("vm", "/remote/one", wm.record("one")["id"],
                                  str(self.local), "active")
            self.assertFalse(wm.untrack(rec["slot"])[0])
            wm.mirror_state("vm", "/remote/one", wm.record("one")["id"],
                            str(self.local), "released")
            self.assertTrue(wm.untrack(rec["slot"])[0])
            self.assertFalse(wm.track("/remote/two", target="unknown")[0])

    def test_occupied_sync_rejected_without_running_command(self):
        with patch.object(wm, "workspace_request", return_value={"clean": True}):
            self.assertTrue(wm.occupy_task("one", "T-1")[0])
        with patch.object(subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "occupied"):
                wm.workspace_request("one", "sync")
        run.assert_not_called()

    def test_failed_free_retains_task_holder(self):
        with patch.object(wm, "workspace_request", return_value={"clean": True}):
            wm.occupy_task("one", "T-1")
        with patch.object(wm, "workspace_request", side_effect=ValueError("local-only commits")):
            with self.assertRaises(ValueError):
                wm.release("one")
        self.assertEqual(wm.get_holder("one")["task_id"], "T-1")

    def test_nonzero_json_error_preserves_machine_mirror_failure(self):
        wm.status_write("one", {"checked": "2026-01-01", "mirror": {"state": "active"}})
        response = subprocess.CompletedProcess([], 1, json.dumps({
            "ok": False, "error": "sync failed", "mirror": {"state": "failed"}}), "")
        with patch.object(subprocess, "run", return_value=response):
            with self.assertRaisesRegex(ValueError, "sync failed"):
                wm.workspace_request("one")
        reading = wm.status_read(wm.record("one")["id"])
        self.assertEqual(reading["checked"], "2026-01-01")
        self.assertEqual(reading["mirror"]["state"], "failed")

    def test_empty_roster_does_not_resurrect_untracked_workspaces(self):
        self.assertTrue(wm.untrack("one")[0])
        self.assertEqual(wm.roster(), [])

    def test_failed_legacy_migration_keeps_task_context(self):
        tasks = self.base / "tasks.json"
        original = {"tasks": [{"id": "T-1", "workspace": "one", "status": "in-progress"}]}
        tasks.write_text(json.dumps(original))
        with patch.object(wm.task_store, "LEGACY", tasks), \
                patch.object(wm.task_store, "DB_PATH", self.base / "tasks.db"), \
                patch.object(wm, "workspace_request", return_value={"clean": False, "reasons": ["dirty"]}):
            with self.assertRaisesRegex(ValueError, "without stripping"):
                wm.migrate_from_tasks()
        self.assertEqual(json.loads(tasks.read_text()), original)

    def test_task_title_reader_uses_owner_database(self):
        with patch.object(wm.task_store, "LEGACY", self.base / "absent-tasks.json"), \
                patch.object(wm.task_store, "DB_PATH", self.base / "tasks.db"):
            with wm.task_store.transaction() as data:
                data["tasks"].append({"id": "T-1", "title": "database title"})
            self.assertEqual(wm._resolve_task_title("T-1"), "database title")
            self.assertEqual(wm._resolve_task_title("missing"), "")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.slot = self.home / "slot"
        self.root = self.slot / "fw"
        self.repo = self.root / "repo"
        self.manifests = self.root / ".repo/manifests"
        self.manifests.mkdir(parents=True)
        self.repo.mkdir()
        self.bin = self.home / "bin"
        self.bin.mkdir()
        (self.bin / "wip").symlink_to("/home/yoniizu/bin/wip")
        octl = self.bin / "octl"
        octl.write_text("#!/bin/sh\nprintf 'sync\\n' >> \"$HOME/octl-calls\"\n")
        octl.chmod(0o755)
        self.env = patch.dict(os.environ, {
            "HOME": str(self.home), "PATH": str(self.bin) + ":" + os.environ["PATH"]})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.init_repo(self.repo, self.home / "origin.git")
        manifest = self.manifests / "test.xml"
        manifest.write_text('<manifest>\n<default revision="main"/>\n'
                            '<project name="demo" path="repo"/>\n</manifest>\n')
        self.init_repo(self.manifests, self.home / "manifest-origin.git")
        (self.root / ".repo/metadata.json").write_text(json.dumps({
            "manifest": "test.xml", "project": "test", "manifest_revision": "main"}))
        self.request = {"id": "WS-L-" + "a" * 32, "path": str(self.slot), "rev": "main"}

    def git(self, path, *args):
        return subprocess.run(["git", "-C", str(path), *args], check=True,
                              text=True, capture_output=True).stdout.strip()

    def init_repo(self, repo, origin):
        self.git(repo, "init", "-b", "main")
        self.git(repo, "config", "user.email", "tests@example.invalid")
        self.git(repo, "config", "user.name", "Workspace tests")
        (repo / "file").write_text("baseline\n")
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-m", "baseline")
        self.git(self.home, "init", "--bare", str(origin))
        self.git(repo, "remote", "add", "origin", str(origin))
        self.git(repo, "push", "-u", "origin", "main")

    def execute(self, action, **extra):
        return runtime.execute(dict(self.request, action=action, **extra))

    def dev(self):
        self.git(self.repo, "checkout", "-b", "dev/test")
        (self.repo / "file").write_text("development\n")
        self.git(self.repo, "commit", "-am", "development")
        self.git(self.repo, "push", "-u", "origin", "dev/test")

    def test_status_dirty_and_independent_sync_guard(self):
        self.assertTrue(self.execute("status")["status"]["clean"])
        self.dev()
        self.assertFalse(self.execute("status")["status"]["clean"])
        result = self.execute("sync")
        self.assertFalse(result["ok"])
        self.assertIn("expected main", result["error"])
        self.assertFalse((self.home / "octl-calls").exists())
        (self.repo / "untracked").write_text("keep me")
        self.assertFalse(self.execute("free")["ok"])
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "dev/test")

    def test_mirror_sync_preserves_dev_then_free_clears_relation(self):
        self.dev()
        source = {"source_id": "WS-L-" + "b" * 32, "state": "active"}
        self.assertTrue(self.execute("mirror", mirror=source)["ok"])
        result = self.execute("sync")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "dev/test")
        self.assertEqual(result["status"]["mirror"]["source_id"], source["source_id"])
        released = self.execute("free")
        self.assertTrue(released["ok"], released)
        self.assertTrue(released["status"]["clean"])
        self.assertIsNone(released["status"]["mirror"])
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "main")
        self.assertIn("free", [row["action"] for row in released["log"]])

    def test_free_emits_ordered_progress_on_stderr_only(self):
        (self.bin / "octl").write_text("#!/bin/sh\nprintf 'Fetching manifests\\n'\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = self.execute("free")
        self.assertTrue(result["ok"], result)
        events = [json.loads(line[len("WS_PROGRESS:"):]) for line in err.getvalue().splitlines()
                  if line.startswith("WS_PROGRESS:")]
        order = list(dict.fromkeys(e["step"] for e in events))
        self.assertEqual(order, ["inspect", "return", "sync", "update", "verify"])
        returned = [e for e in events if e["step"] == "return" and e.get("done") == 1]
        self.assertEqual((returned[-1]["total"], returned[-1]["current"]), (1, "repo"))
        self.assertIn("Fetching manifests", [e.get("detail") for e in events])

    def test_local_only_commits_and_failed_free_keep_mirror(self):
        self.dev()
        self.execute("mirror", mirror={"source_id": "WS-L-" + "b" * 32, "state": "active"})
        (self.repo / "file").write_text("not pushed\n")
        self.git(self.repo, "commit", "-am", "local only")
        result = self.execute("free")
        self.assertFalse(result["ok"])
        self.assertIn("local-only", result["error"])
        self.assertIsNotNone(self.execute("status")["status"]["mirror"])

    def test_missing_and_revision_mismatch_fail_explicitly(self):
        (self.repo / "file").write_text("dirty\n")
        result = self.execute("status")
        self.assertFalse(result["status"]["clean"])
        self.assertTrue(any("changes" in s for s in result["status"]["reasons"]))
        self.request["rev"] = "wrong"
        self.assertIn("differs from manifest", self.execute("status")["error"])
        self.request["path"] = str(self.home / "missing")
        result = self.execute("status")
        self.assertFalse(result["status"]["exists"])

    def test_machine_identity_cannot_be_overwritten(self):
        self.execute("status")
        other = runtime.execute(dict(self.request, action="status", id="WS-R-" + "c" * 32))
        self.assertFalse(other["ok"])
        self.assertEqual(other["workspace_id"], self.request["id"])

    def test_mirror_sync_fetches_development_and_baseline_updates(self):
        self.dev()
        self.execute("mirror", mirror={"source_id": "WS-L-" + "b" * 32, "state": "active"})
        updater = self.home / "updater"
        self.git(self.home, "clone", "--branch", "dev/test",
                 str(self.home / "origin.git"), str(updater))
        self.git(updater, "config", "user.email", "tests@example.invalid")
        self.git(updater, "config", "user.name", "Workspace tests")
        (updater / "file").write_text("next remote development\n")
        self.git(updater, "commit", "-am", "remote development update")
        dev_head = self.git(updater, "rev-parse", "HEAD")
        self.git(updater, "push", "origin", "dev/test")
        self.git(updater, "checkout", "main")
        (updater / "baseline-update").write_text("next baseline\n")
        self.git(updater, "add", ".")
        self.git(updater, "commit", "-m", "remote baseline update")
        main_head = self.git(updater, "rev-parse", "HEAD")
        self.git(updater, "push", "origin", "main")
        result = self.execute("sync")
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), dev_head)
        self.assertEqual(self.git(self.repo, "rev-parse", "main"), main_head)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "dev/test")

    def test_manifest_project_pin_is_clean_and_preserved(self):
        self.git(self.repo, "tag", "pin-v1")
        self.git(self.repo, "push", "origin", "pin-v1")
        manifest = self.manifests / "test.xml"
        manifest.write_text('<manifest>\n<default revision="main"/>\n'
                            '<project name="demo" path="repo" revision="pin-v1"/>\n</manifest>\n')
        self.git(self.manifests, "commit", "-am", "pin repository")
        self.git(self.manifests, "push", "origin", "main")
        self.git(self.repo, "checkout", "--detach", "pin-v1")
        self.assertTrue(self.execute("status")["status"]["clean"])
        result = self.execute("sync")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["status"]["clean"])
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"),
                         self.git(self.repo, "rev-parse", "pin-v1"))

    def test_failed_sync_keeps_relation_and_records_error(self):
        self.dev()
        self.execute("mirror", mirror={"source_id": "WS-L-" + "b" * 32, "state": "active"})
        (self.bin / "octl").write_text("#!/bin/sh\necho 'sync failure' >&2\nexit 1\n")
        result = self.execute("free")
        self.assertFalse(result["ok"])
        self.assertIn("sync failure", result["error"])
        self.assertEqual(result["mirror"]["state"], "failed")
        self.assertEqual(result["log"][-1]["outcome"], "error")
        self.assertIsNotNone(self.execute("status")["status"]["mirror"])

    def test_cannot_steal_or_clear_mirror_without_free(self):
        source_id = "WS-L-" + "b" * 32
        self.assertTrue(self.execute("mirror", mirror={"source_id": source_id, "state": "active"})["ok"])
        other = self.execute("mirror", mirror={"source_id": "WS-L-" + "c" * 32, "state": "syncing"})
        self.assertFalse(other["ok"])
        self.assertIn("free it", other["error"])
        self.assertFalse(self.execute("mirror", mirror=None)["ok"])
        self.assertEqual(self.execute("status")["status"]["mirror"]["source_id"], source_id)

    def test_missing_or_extra_repositories_prevent_partial_sync(self):
        extra = self.root / "extra"
        extra.mkdir()
        self.init_repo(extra, self.home / "extra-origin.git")
        result = self.execute("sync")
        self.assertFalse(result["ok"])
        self.assertIn("not declared", result["error"])
        self.assertFalse((self.home / "octl-calls").exists())

    def test_missing_manifest_repo_prevents_partial_sync(self):
        manifest = self.manifests / "test.xml"
        manifest.write_text('<manifest>\n<default revision="main"/>\n'
                            '<project name="demo" path="repo"/>\n'
                            '<project name="missing" path="missing"/>\n</manifest>\n')
        self.git(self.manifests, "commit", "-am", "declare missing repository")
        self.git(self.manifests, "push", "origin", "main")
        result = self.execute("sync")
        self.assertFalse(result["ok"])
        self.assertIn("coverage mismatch", result["error"])
        self.assertFalse((self.home / "octl-calls").exists())

    def test_status_preserves_existing_wip_branch_plan(self):
        state = self.slot / ".wip/state"
        state.parent.mkdir()
        original = b"# WIP_INFO\nuser-planned-checkout=keep-this\n"
        state.write_bytes(original)
        self.assertTrue(self.execute("status")["ok"])
        self.assertEqual(state.read_bytes(), original)


class ProgressStreamTests(unittest.TestCase):
    def test_progress_events_stream_and_stdout_stays_json(self):
        script = ("import sys,json;"
                  "sys.stderr.write('WS_PROGRESS:'+json.dumps({'step':'return','done':1})+'\\n');"
                  "sys.stderr.write('WS_PROGRESS:not json\\n');"
                  "sys.stderr.write('plain warning\\n');"
                  "print(json.dumps({'ok':True}))")
        seen = []
        result = wm._run_with_progress([sys.executable, "-c", script], 30, seen.append)
        self.assertEqual(seen, [{"step": "return", "done": 1}])
        self.assertEqual(json.loads(result.stdout), {"ok": True})
        self.assertEqual(result.stderr, "plain warning\n")

    def test_streaming_timeout_raises(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            wm._run_with_progress([sys.executable, "-c", "import time; time.sleep(5)"],
                                  0.3, lambda e: None)


class GithubAuthTests(unittest.TestCase):
    def setUp(self):
        runtime.github_https_config.cache_clear()
        self.addCleanup(runtime.github_https_config.cache_clear)

    def test_logged_in_gh_rewrites_ssh_remotes_to_https(self):
        ok = subprocess.CompletedProcess([], 0)
        with patch.object(runtime.shutil, "which", return_value="/usr/bin/gh"), \
                patch.object(runtime.subprocess, "run", return_value=ok):
            env = runtime.github_https_config()
        pairs = [(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"])
                 for i in range(int(env["GIT_CONFIG_COUNT"]))]
        self.assertIn(("url.https://github.com/.insteadOf", "git@github.com:"), pairs)
        self.assertIn(("url.https://github.com/.insteadOf", "ssh://git@github.com/"), pairs)
        self.assertEqual(pairs[-1], ("credential.https://github.com.helper",
                                     "!/usr/bin/gh auth git-credential"))

    def test_missing_or_logged_out_gh_keeps_ssh(self):
        with patch.object(runtime.shutil, "which", return_value=None):
            self.assertEqual(runtime.github_https_config(), {})
        runtime.github_https_config.cache_clear()
        with patch.object(runtime.shutil, "which", return_value="/usr/bin/gh"), \
                patch.object(runtime.subprocess, "run",
                             return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(runtime.github_https_config(), {})


if __name__ == "__main__":
    unittest.main()
