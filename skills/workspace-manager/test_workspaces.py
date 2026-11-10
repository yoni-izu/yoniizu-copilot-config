import contextlib
import fcntl
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

sys.path.insert(0, str(Path(__file__).parent.parent / "vm-delegate"))
import vd


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

    def test_metadata_editor_fields_persist_without_workspace_operations(self):
        wid = wm.record("one")["id"]
        ok, _, group = wm.add_group("Development")
        self.assertTrue(ok)
        with patch.object(wm, "workspace_request") as request:
            ok, msg = wm.set_slot_fields("one", {
                "label": "New label", "badge": "NEW", "group": group, "rev": "release"})
        self.assertTrue(ok, msg)
        request.assert_not_called()
        rec = wm.record("one")
        self.assertEqual(rec["id"], wid)
        self.assertEqual(rec["path"], str(self.local))
        for key, value in {"label": "New label", "badge": "NEW",
                           "group": group, "rev": "release"}.items():
            self.assertEqual(rec[key], value)
        self.assertTrue(wm.set_slot_fields("one", {"group": ""})[0])
        self.assertEqual(wm.record("one")["group"], "")

    def test_metadata_revision_guard_preserves_hold_and_rejects_unknown_group(self):
        with patch.object(wm, "workspace_request", return_value={"clean": True}):
            self.assertTrue(wm.occupy_task("one", "T-1")[0])
        self.assertFalse(wm.set_slot_fields("one", {"rev": "release", "label": "Lost"})[0])
        self.assertEqual(wm.record("one")["label"], "one")
        self.assertTrue(wm.set_slot_fields("one", {"label": "Held label", "badge": "HELD"})[0])
        self.assertEqual(wm.get_holder("one")["task_id"], "T-1")
        self.assertFalse(wm.set_slot_fields("one", {"group": "unknown"})[0])

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

    def test_remote_workspace_resolves_agent_for_both_subprocess_paths(self):
        with patch.object(wm, "machine_targets", return_value=["vm"]):
            ok, _, slot = wm.track("/remote/one", target="vm")
        self.assertTrue(ok)
        env = dict(os.environ, SSH_AUTH_SOCK="/working/agent")
        response = subprocess.CompletedProcess([], 0, json.dumps({
            "ok": True, "status": {"checked": "new", "clean": True}}), "")
        for action in ("status", "sync", "free", "align"):
            for streaming in (False, True):
                with self.subTest(action=action, streaming=streaming), \
                        patch.object(vd, "agent_env", return_value=(env, "")) as agent, \
                        patch.object(subprocess, "run", return_value=response) as run, \
                        patch.object(wm, "_run_with_progress", return_value=response) as stream:
                    wm.workspace_request(slot, action,
                                         progress=(lambda event: None) if streaming else None)
                agent.assert_called_once_with(recheck=True)
                invoked, unused = (stream, run) if streaming else (run, stream)
                self.assertEqual(invoked.call_args.kwargs["env"], env)
                unused.assert_not_called()

    def test_local_workspace_does_not_resolve_ssh_agent(self):
        response = subprocess.CompletedProcess([], 0, json.dumps({
            "ok": True, "status": {"checked": "new", "clean": True}}), "")
        with patch.object(vd, "agent_env") as agent, \
                patch.object(subprocess, "run", return_value=response):
            wm.workspace_request("one")
        agent.assert_not_called()

    def test_remote_auth_failure_keeps_reading_and_reports_missing_agent(self):
        with patch.object(wm, "machine_targets", return_value=["vm"]):
            ok, _, slot = wm.track("/remote/one", target="vm")
        self.assertTrue(ok)
        wm.status_write(slot, {"checked": "old", "mirror": {"state": "active"}})
        response = subprocess.CompletedProcess([], 255, "", "Permission denied (publickey).")
        with patch.object(vd, "agent_env", return_value=({}, "no ssh-agent with identities")), \
                patch.object(subprocess, "run", return_value=response) as run:
            with self.assertRaisesRegex(ValueError, "no ssh-agent with identities"):
                wm.workspace_request(slot)
        run.assert_called_once()
        reading = wm.status_read(wm.record(slot)["id"])
        self.assertEqual(reading["checked"], "old")
        self.assertEqual(reading["mirror"]["state"], "active")
        self.assertIn("Permission denied", reading["error"])

    def test_remote_key_file_auth_still_works_without_agent(self):
        with patch.object(wm, "machine_targets", return_value=["vm"]):
            ok, _, slot = wm.track("/remote/one", target="vm")
        self.assertTrue(ok)
        response = subprocess.CompletedProcess([], 0, json.dumps({
            "ok": True, "status": {"checked": "new", "clean": True}}), "")
        with patch.object(vd, "agent_env", return_value=({}, "no ssh-agent with identities")), \
                patch.object(subprocess, "run", return_value=response):
            self.assertTrue(wm.workspace_request(slot)["clean"])

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

    def test_force_sync_cannot_bypass_an_existing_hold(self):
        with patch.object(wm, "workspace_request", return_value={"clean": True}):
            self.assertTrue(wm.occupy_task("one", "T-1")[0])
        for mirror in (None, {"state": "active"}):
            wm.status_write("one", {"mirror": mirror})
            with patch.object(subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "occupied"):
                    wm.workspace_request("one", "sync", portal_sync={"approved": "a" * 64})
            run.assert_not_called()
        self.assertEqual(wm.get_holder("one")["task_id"], "T-1")

    def test_untrack_override_preserves_holds_readings_history_and_checkout(self):
        for holder in ({"type": "task", "task_id": "T-1"},
                       {"type": "adhoc", "label": "experiment"}):
            with self.subTest(holder=holder):
                d = wm.load()
                d["slots"]["one"] = {"holder": holder}
                wm.save(d)
                wid = wm.record("one")["id"]
                reading = {"mirror": {"state": "active"}, "checked": "old"}
                wm.status_write(wid, reading)
                marker = self.local / "keep.txt"
                marker.write_text("unchanged")
                before = wm.action_log("one")
                with patch.object(wm, "workspace_request") as request, \
                        patch.object(subprocess, "run") as run:
                    self.assertFalse(wm.untrack("one")[0])
                    ok, message = wm.untrack(wid, force=True)
                self.assertTrue(ok, message)
                request.assert_not_called()
                run.assert_not_called()
                self.assertEqual(wm.roster(), [])
                self.assertEqual(wm.get_holder("one"), holder)
                self.assertEqual(wm.status_read(wid), reading)
                self.assertEqual(marker.read_text(), "unchanged")
                events = wm.workspace_store.events(wid)
                self.assertEqual(events[:-1], before)
                self.assertIn("override requested", events[-1]["detail"])
                d = wm.load()
                d["tracked"]["one"] = {"id": wid, "path": str(self.local),
                                        "label": "one", "rev": "main"}
                wm.save(d)

    def test_untrack_override_does_not_bypass_operation_lock_or_validation(self):
        wid = wm.record("one")["id"]
        path = wm._state_path(wid, ".operation.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "operation already running"):
                wm.untrack("one", force=True)
        self.assertIn("one", wm.tracked())
        with self.assertRaisesRegex(ValueError, "not tracked"):
            wm.untrack("unknown", force=True)
        for force in ("true", 1, None):
            self.assertFalse(wm.untrack("one", force=force)[0])
        self.assertIn("one", wm.tracked())

    def test_untrack_mirror_requires_explicit_override(self):
        wid = wm.record("one")["id"]
        wm.status_write(wid, {"mirror": {"state": "failed"}})
        self.assertFalse(wm.untrack("one")[0])
        self.assertTrue(wm.untrack("one", force=True)[0])
        self.assertEqual(wm.status_read(wid)["mirror"]["state"], "failed")

    def test_failed_free_retains_task_holder(self):
        with patch.object(wm, "workspace_request", return_value={"clean": True}):
            wm.occupy_task("one", "T-1")
        with patch.object(wm, "workspace_request", side_effect=ValueError("local-only commits")):
            with self.assertRaises(ValueError):
                wm.release("one")
        self.assertEqual(wm.get_holder("one")["task_id"], "T-1")

    def test_release_drops_stale_holder_on_untracked_slot(self):
        d = wm.load()
        d["slots"]["main"] = {"holder": {"type": "task", "task_id": "T-102",
                                         "detail": "main", "since": "2026-08-05",
                                         "notes": []}}
        wm.save(d)
        with patch.object(wm, "workspace_request") as request:
            ok, holder = wm.release("main")
        request.assert_not_called()
        self.assertTrue(ok)
        self.assertEqual(holder["task_id"], "T-102")
        self.assertIsNone(wm.get_holder("main"))
        self.assertIsNone(wm.slot_of_task("T-102"))

    def test_release_task_clears_stale_untracked_occupancy(self):
        d = wm.load()
        d["slots"]["main"] = {"holder": {"type": "task", "task_id": "T-102",
                                         "detail": "main", "since": "2026-08-05",
                                         "notes": []}}
        wm.save(d)
        with patch.object(wm, "workspace_request") as request:
            self.assertEqual(wm.release_task("T-102"), "main")
        request.assert_not_called()
        self.assertIsNone(wm.slot_of_task("T-102"))

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

    def test_workspace_request_surfaces_approval_without_persisting_it_as_status(self):
        approval = {"token": "a" * 64, "repos": [], "blockers": []}
        response = subprocess.CompletedProcess([], 0, json.dumps({
            "ok": True, "status": {"clean": False, "path": str(self.local)},
            "approval_required": approval, "log": []}), "")
        with patch.object(subprocess, "run", return_value=response) as run:
            reading = wm.workspace_request("one", "free", portal_free={"approved": None})
        self.assertEqual(reading["approval_required"], approval)
        request = json.loads(run.call_args.args[0][-1])
        self.assertEqual(request["portal_free"], {"approved": None})
        self.assertNotIn("approval_required", wm.status_read(wm.record("one")["id"]))
        self.assertEqual(wm.action_log("one")[-1]["outcome"], "approval-required")

    def test_workspace_request_records_saved_stash_recovery_details(self):
        stash = {"path": "/repo", "branch": "dev/test", "oid": "b" * 40}
        response = subprocess.CompletedProcess([], 0, json.dumps({
            "ok": True, "status": {"clean": True, "path": str(self.local), "stashes": [stash]},
            "log": []}), "")
        with patch.object(subprocess, "run", return_value=response):
            wm.workspace_request("one", "free", portal_free={"approved": "a" * 64})
        event = wm.action_log("one")[-1]
        self.assertEqual(event["outcome"], "ok")
        self.assertIn(stash["oid"], event["detail"])
        self.assertIn("dev/test", event["detail"])

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

    def portal_free(self, approved=None):
        return self.execute("free", portal_free={"approved": approved})

    def portal_sync(self, approved=None):
        return self.execute("sync", portal_sync={"approved": approved})

    def test_portal_sync_stashes_work_and_returns_unexpected_branch_on_confirmation(self):
        self.dev()
        (self.repo / "file").write_text("staged\n")
        self.git(self.repo, "add", "file")
        (self.repo / "file").write_text("unstaged\n")
        (self.repo / "untracked").write_text("keep\n")
        first = self.portal_sync()
        self.assertTrue(first["ok"], first)
        self.assertIn("approval_required", first)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "dev/test")
        self.assertEqual(self.git(self.repo, "stash", "list"), "")
        self.assertFalse((self.home / "octl-calls").exists())
        result = self.portal_sync(first["approval_required"]["token"])
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["status"]["clean"])
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "main")
        stash = result["status"]["stashes"][0]
        self.git(self.repo, "checkout", "dev/test")
        self.git(self.repo, "stash", "apply", "--index", stash["oid"])
        self.assertEqual(self.git(self.repo, "show", ":file"), "staged")
        self.assertEqual((self.repo / "file").read_text(), "unstaged\n")
        self.assertEqual((self.repo / "untracked").read_text(), "keep\n")

    def test_portal_sync_reprompts_if_dirty_content_changes(self):
        (self.repo / "file").write_text("first\n")
        first = self.portal_sync()
        (self.repo / "file").write_text("second\n")
        result = self.portal_sync(first["approval_required"]["token"])
        self.assertNotEqual(first["approval_required"]["token"],
                            result["approval_required"]["token"])
        self.assertEqual(self.git(self.repo, "stash", "list"), "")
        self.assertFalse((self.home / "octl-calls").exists())

    def test_portal_sync_blocks_local_only_target_commits(self):
        (self.repo / "file").write_text("local commit\n")
        self.git(self.repo, "commit", "-am", "local main commit")
        first = self.portal_sync()
        self.assertTrue(first["approval_required"]["blockers"])
        result = self.portal_sync(first["approval_required"]["token"])
        self.assertFalse(result["ok"])
        self.assertEqual(self.git(self.repo, "stash", "list"), "")
        self.assertFalse((self.home / "octl-calls").exists())

    def test_portal_sync_clean_checkout_runs_normally_and_network_error_does_not_prompt(self):
        self.assertTrue(self.portal_sync()["status"]["clean"])
        self.git(self.repo, "remote", "set-url", "origin", str(self.home / "missing.git"))
        result = self.portal_sync()
        self.assertFalse(result["ok"])
        self.assertNotIn("approval_required", result)

    def test_portal_sync_mirror_preserves_normal_guards_and_rejects_force(self):
        self.dev()
        source = {"source_id": "WS-L-" + "b" * 32, "state": "active"}
        self.execute("mirror", mirror=source)
        result = self.portal_sync()
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "dev/test")
        self.assertEqual(result["status"]["mirror"], source)
        (self.repo / "untracked").write_text("keep")
        result = self.portal_sync()
        self.assertFalse(result["ok"])
        self.assertNotIn("approval_required", result)
        forced = self.portal_sync("a" * 64)
        self.assertFalse(forced["ok"])
        self.assertIn("free independent", forced["error"])
        self.assertEqual(self.git(self.repo, "stash", "list"), "")

    def test_portal_sync_options_are_strict_and_sync_only(self):
        for value in (None, {}, {"approved": True}, {"approved": "bad"},
                      {"approved": None, "extra": True}):
            with self.assertRaises(ValueError):
                self.execute("sync", portal_sync=value)
        with self.assertRaises(ValueError):
            self.execute("free", portal_sync={"approved": None})

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

    def test_portal_free_requires_approval_and_preserves_all_uncommitted_work(self):
        self.dev()
        (self.repo / "file").write_text("staged\n")
        self.git(self.repo, "add", "file")
        (self.repo / "file").write_text("unstaged\n")
        (self.repo / "untracked").write_text("untracked\n")
        first = self.portal_free()
        self.assertTrue(first["ok"], first)
        approval = first["approval_required"]
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "dev/test")
        self.assertEqual((self.repo / "file").read_text(), "unstaged\n")
        self.assertFalse((self.home / "octl-calls").exists())
        self.assertEqual(self.git(self.repo, "stash", "list"), "")

        result = self.portal_free(approval["token"])
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["status"]["clean"])
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "main")
        stash = result["status"]["stashes"][0]
        self.assertEqual(stash["branch"], "dev/test")
        self.assertIn(stash["oid"], self.git(self.repo, "stash", "list", "--format=%H"))
        self.assertIn(stash["oid"], result["log"][-1]["detail"])

        self.git(self.repo, "checkout", "dev/test")
        self.git(self.repo, "stash", "apply", "--index", stash["oid"])
        self.assertEqual(self.git(self.repo, "show", ":file"), "staged")
        self.assertEqual((self.repo / "file").read_text(), "unstaged\n")
        self.assertEqual((self.repo / "untracked").read_text(), "untracked\n")

    def test_portal_free_preserves_local_only_development_branch(self):
        self.git(self.repo, "checkout", "-b", "dev/local")
        (self.repo / "file").write_text("local commit\n")
        self.git(self.repo, "commit", "-am", "local commit")
        head = self.git(self.repo, "rev-parse", "HEAD")
        first = self.portal_free()
        self.assertFalse(first["approval_required"]["blockers"])
        result = self.portal_free(first["approval_required"]["token"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "main")
        self.assertEqual(self.git(self.repo, "rev-parse", "refs/heads/dev/local"), head)

    def test_portal_free_reprompts_when_worktree_changes_after_approval(self):
        (self.repo / "first").write_text("one")
        first = self.portal_free()
        (self.repo / "second").write_text("two")
        second = self.portal_free(first["approval_required"]["token"])
        self.assertTrue(second["ok"], second)
        self.assertNotEqual(second["approval_required"]["token"],
                            first["approval_required"]["token"])
        self.assertEqual(self.git(self.repo, "stash", "list"), "")
        self.assertFalse((self.home / "octl-calls").exists())

    def test_portal_free_reprompts_when_existing_dirty_file_content_changes(self):
        (self.repo / "file").write_text("first\n")
        first = self.portal_free()
        (self.repo / "file").write_text("other\n")
        second = self.portal_free(first["approval_required"]["token"])
        self.assertTrue(second["ok"], second)
        self.assertNotEqual(second["approval_required"]["token"],
                            first["approval_required"]["token"])
        self.assertEqual((self.repo / "file").read_text(), "other\n")
        self.assertEqual(self.git(self.repo, "stash", "list"), "")

    def test_portal_free_reprompts_when_staged_content_changes(self):
        (self.repo / "file").write_text("first\n")
        self.git(self.repo, "add", "file")
        first = self.portal_free()
        (self.repo / "file").write_text("other\n")
        self.git(self.repo, "add", "file")
        second = self.portal_free(first["approval_required"]["token"])
        self.assertTrue(second["ok"], second)
        self.assertNotEqual(second["approval_required"]["token"],
                            first["approval_required"]["token"])
        self.assertEqual(self.git(self.repo, "show", ":file"), "other")
        self.assertEqual(self.git(self.repo, "stash", "list"), "")

    def test_portal_free_blocks_unverified_or_local_target_commits_before_stashing(self):
        (self.repo / "file").write_text("local commit\n")
        self.git(self.repo, "commit", "-am", "local main commit")
        self.git(self.repo, "checkout", "-b", "dev/local")
        (self.repo / "untracked").write_text("keep")
        first = self.portal_free()
        self.assertTrue(first["approval_required"]["blockers"])
        result = self.portal_free(first["approval_required"]["token"])
        self.assertFalse(result["ok"])
        self.assertIn("target branch", result["error"])
        self.assertEqual((self.repo / "untracked").read_text(), "keep")
        self.assertEqual(self.git(self.repo, "stash", "list"), "")
        self.assertFalse((self.home / "octl-calls").exists())

    def test_portal_free_saves_manifest_changes_without_changing_repo_inventory(self):
        manifest = self.manifests / "test.xml"
        original = manifest.read_text()
        manifest.write_text(original + "<!-- local change -->\n")
        first = self.portal_free()
        result = self.portal_free(first["approval_required"]["token"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(manifest.read_text(), original)
        self.assertEqual(result["status"]["stashes"][0]["path"], str(self.manifests))
        self.assertTrue(result["status"]["clean"])

    def test_portal_free_returns_manifest_repository_and_preserves_its_branch(self):
        self.git(self.manifests, "checkout", "-b", "dev/manifest")
        original_head = self.git(self.manifests, "rev-parse", "HEAD")
        first = self.portal_free()
        result = self.portal_free(first["approval_required"]["token"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.git(self.manifests, "branch", "--show-current"), "main")
        self.assertEqual(self.git(self.manifests, "rev-parse", "dev/manifest"), original_head)
        self.assertTrue(result["status"]["clean"])

    def test_portal_free_preserves_pinned_manifest_revision(self):
        self.git(self.repo, "tag", "pin-v1")
        self.git(self.repo, "push", "origin", "pin-v1")
        manifest = self.manifests / "test.xml"
        manifest.write_text('<manifest>\n<default revision="main"/>\n'
                            '<project name="demo" path="repo" revision="pin-v1"/>\n</manifest>\n')
        self.git(self.manifests, "commit", "-am", "pin repository")
        self.git(self.manifests, "push", "origin", "main")
        self.git(self.repo, "checkout", "--detach", "pin-v1")
        (self.repo / "file").write_text("dirty pinned checkout\n")
        first = self.portal_free()
        self.assertEqual(first["approval_required"]["repos"][0]["revision"], "pin-v1")
        result = self.portal_free(first["approval_required"]["token"])
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["status"]["clean"])
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"),
                         self.git(self.repo, "rev-parse", "pin-v1"))

    def test_portal_free_stashes_changes_in_every_manifest_repository(self):
        other = self.root / "other"
        other.mkdir()
        self.init_repo(other, self.home / "other-origin.git")
        manifest = self.manifests / "test.xml"
        manifest.write_text('<manifest>\n<default revision="main"/>\n'
                            '<project name="demo" path="repo"/>\n'
                            '<project name="other" path="other"/>\n</manifest>\n')
        self.git(self.manifests, "commit", "-am", "add other repository")
        self.git(self.manifests, "push", "origin", "main")
        (self.repo / "file").write_text("first change\n")
        (other / "new").write_text("second change\n")
        first = self.portal_free()
        self.assertEqual(len(first["approval_required"]["repos"]), 2)
        result = self.portal_free(first["approval_required"]["token"])
        self.assertTrue(result["ok"], result)
        self.assertEqual({s["path"] for s in result["status"]["stashes"]},
                         {str(self.repo), str(other)})
        self.assertTrue(result["status"]["clean"])

    def test_portal_free_stops_if_manifest_changes_after_final_check(self):
        (self.repo / "file").write_text("keep this change\n")
        first = self.portal_free()
        original = runtime.manifest_repos
        calls = 0

        def changed_manifest(root, revision):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.manifests / "test.xml").write_text(
                    '<manifest>\n<default revision="main"/>\n'
                    '<project name="demo" path="repo"/>\n'
                    '<project name="other" path="other"/>\n</manifest>\n')
            return original(root, revision)

        with patch.object(runtime, "manifest_repos", side_effect=changed_manifest):
            result = self.portal_free(first["approval_required"]["token"])
        self.assertFalse(result["ok"])
        self.assertIn("manifest changed after approval", result["error"])
        self.assertEqual((self.repo / "file").read_text(), "keep this change\n")
        self.assertEqual(self.git(self.repo, "stash", "list"), "")
        self.assertFalse((self.home / "octl-calls").exists())

    def test_portal_free_refuses_detached_local_only_commit(self):
        self.git(self.repo, "checkout", "-b", "dev/local")
        (self.repo / "file").write_text("local commit\n")
        self.git(self.repo, "commit", "-am", "local commit")
        self.git(self.repo, "checkout", "--detach")
        first = self.portal_free()
        self.assertIn("detached HEAD", "; ".join(first["approval_required"]["blockers"]))
        result = self.portal_free(first["approval_required"]["token"])
        self.assertFalse(result["ok"])
        self.assertFalse((self.home / "octl-calls").exists())

    def test_portal_free_does_not_overwrite_ignored_files(self):
        (self.repo / ".gitignore").write_text("artifact\n")
        (self.repo / "artifact").write_text("baseline artifact\n")
        self.git(self.repo, "add", ".gitignore")
        self.git(self.repo, "add", "-f", "artifact")
        self.git(self.repo, "commit", "-m", "baseline artifact")
        self.git(self.repo, "push", "origin", "main")
        self.git(self.repo, "checkout", "-b", "dev/ignored")
        self.git(self.repo, "rm", "artifact")
        self.git(self.repo, "commit", "-m", "remove artifact")
        self.git(self.repo, "push", "-u", "origin", "dev/ignored")
        (self.repo / "artifact").write_text("keep ignored file\n")
        first = self.portal_free()
        result = self.portal_free(first["approval_required"]["token"])
        self.assertFalse(result["ok"])
        self.assertIn("would be overwritten", result["error"])
        self.assertEqual((self.repo / "artifact").read_text(), "keep ignored file\n")
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "dev/ignored")

    def test_failed_portal_free_reports_saved_stash_and_keeps_mirror(self):
        self.dev()
        self.execute("mirror", mirror={"source_id": "WS-L-" + "b" * 32, "state": "active"})
        (self.repo / "untracked").write_text("keep")
        (self.bin / "octl").write_text("#!/bin/sh\necho 'sync failure' >&2\nexit 1\n")
        first = self.portal_free()
        result = self.portal_free(first["approval_required"]["token"])
        self.assertFalse(result["ok"])
        self.assertIn("sync failure", result["error"])
        self.assertIn("Saved work:", result["error"])
        self.assertIn(self.git(self.repo, "rev-parse", "refs/stash"), result["error"])
        self.assertEqual(result["mirror"]["state"], "failed")

    def test_partial_stash_batch_failure_reports_stash_for_recovery(self):
        (self.repo / "file").write_text("keep this change\n")
        first = self.portal_free()
        original_run = runtime.run

        def fail_after_stash(args, cwd, timeout=180, on_line=None):
            output = original_run(args, cwd, timeout, on_line)
            if args[1:3] == ["forall", "ws"] and '"action": "stash"' in args[3]:
                raise RuntimeError("simulated failure after stashing")
            return output

        with patch.object(runtime, "run", side_effect=fail_after_stash):
            result = self.portal_free(first["approval_required"]["token"])
        self.assertFalse(result["ok"])
        self.assertIn("simulated failure after stashing", result["error"])
        self.assertIn(self.git(self.repo, "rev-parse", "refs/stash"), result["error"])
        self.assertIn("Saved work:", result["log"][-1]["detail"])
        self.assertFalse((self.home / "octl-calls").exists())

    def test_portal_free_options_are_strict_and_free_only(self):
        for option in (None, {}, {"approved": True}, {"approved": "invalid"},
                       {"approved": None, "extra": True}):
            with self.subTest(option=option):
                with self.assertRaises(ValueError):
                    self.execute("free", portal_free=option)
        with self.assertRaises(ValueError):
            self.execute("sync", portal_free={"approved": None})

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
    def test_streaming_subprocess_receives_selected_agent_environment(self):
        env = dict(os.environ, SSH_AUTH_SOCK="/working/agent")
        script = "import os; print(os.environ['SSH_AUTH_SOCK'])"
        result = wm._run_with_progress([sys.executable, "-c", script], 30,
                                      lambda event: None, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), env["SSH_AUTH_SOCK"])

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
