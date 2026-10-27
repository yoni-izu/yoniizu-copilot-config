"""Focused mctl tests: all transports and workspace tools are isolated stubs."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import unittest
import uuid


MCTL = Path(__file__).resolve().parents[3] / "bin" / "mctl"


class WorkspaceMctlTests(unittest.TestCase):
    def setUp(self):
        self.scratch = Path(__file__).parent / (".workspace-mctl-test-" + uuid.uuid4().hex)
        self.scratch.mkdir()
        self.addCleanup(shutil.rmtree, self.scratch)
        self.events = self.scratch / "events"
        self.events.touch()
        self.home = self.scratch / "home"
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home), EVENTS=str(self.events),
                        DELEGATION_DB=str(self.home / "tasks/delegation.db"),
                        PLDM_DB=str(self.home / "tasks/pldm.db"))
        self.env.pop("BASH_ENV", None)

    def shell(self, code):
        script = f"source <(sed '$d' {shlex.quote(str(MCTL))})\n" + code
        return subprocess.run(
            ["bash", "-c", script], env=self.env, text=True, capture_output=True,
            timeout=15, check=False,
        )

    def runtime_fixture(self, body):
        runtime = self.home / ".copilot/skills/workspace-manager/workspace_runtime.py"
        runtime.parent.mkdir(parents=True)
        runtime.write_text(body)

    def test_independent_path_and_json_are_shell_safe(self):
        self.runtime_fixture("import json,sys\nprint(json.dumps(json.loads(sys.argv[1])))\n")
        path = "/independent/slot ' ; $(touch BAD) \n/fw"
        mirror = {"source_id": "WS-L-source", "source_path": "/src/'$(false)", "state": "syncing"}
        args = ["mirror", path, "WS-R-stable", "fb/release-12", json.dumps(mirror)]
        result = self.shell("""
rlogin() { bash -s <<< "$*"; }
rlogin_cred() { echo forbidden-retry >&2; return 99; }
find_ws() { return 98; }
map_remote() { return 97; }
cmd_workspace """ + shlex.join(args))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "action": "mirror", "path": path, "id": "WS-R-stable", "rev": "fb/release-12",
            "mirror": mirror,
        })
        self.assertFalse((Path.cwd() / "BAD").exists())

    def test_clear_mirror_transports_explicit_null(self):
        self.runtime_fixture("import json,sys\nprint(json.dumps(json.loads(sys.argv[1])))\n")
        result = self.shell("""
rlogin() { bash -s <<< "$*"; }
cmd_workspace mirror /remote/slot WS-R-stable 4 null
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)["mirror"])

    def test_transport_and_domain_errors_are_not_retried(self):
        self.runtime_fixture("print('not executed')\n")
        for command, expected, stdout, stderr in [
            ('printf \'{"ok":false,"error":"occupied"}\\n\'', 0, "occupied", ""),
            ("echo transport-details >&2; echo partial-output; return 23", 23,
             "partial-output", "transport-details"),
        ]:
            with self.subTest(command=command):
                result = self.shell("""
rlogin() { echo called >> "$EVENTS"; """ + command + """; }
rlogin_cred() { return 99; }
cmd_workspace free /remote/slot WS-R-stable 2
""")
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertIn(stdout, result.stdout)
                self.assertIn(stderr, result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), ["called", "called"])

    def test_invalid_requests_never_reach_transport(self):
        self.runtime_fixture("raise SystemExit('not executed')\n")
        for args in [
            "free relative WS-R-stable 0", "free /slot WS-L-source 0",
            "sync /slot WS-R-stable ''", "mirror /slot WS-R-stable main",
            "mirror /slot WS-R-stable 0 '[]'", "unknown /slot WS-R-stable 0",
            "free /slot WS-R-stable main null",
            "align /slot WS-R-stable main '{\"prs\":{}}'",
            "align /slot WS-R-stable main '{\"id\":\"WS-R-other\"}'",
        ]:
            with self.subTest(args=args):
                result = self.shell("""
rlogin() { echo unexpected >> "$EVENTS"; }
cmd_workspace """ + args)
                self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events.read_text(), "")

    def test_existing_remote_identity_is_adopted_before_any_mutation(self):
        result = self.shell("""
T_NAME=lab
workspace_identity() {
  printf '%s\\n' "$*" >> "$EVENTS"
  printf '{"id":"WS-R-cached","rev":"main"}\\n'
}
workspace_reconcile_identity() {
  printf 'reconcile:%s\\n' "$1" >> "$EVENTS"
  printf '{"id":"WS-R-existing","path":"/slot","rev":"main"}\\n'
}
cmd_workspace() {
  printf '%s\\n' "$*" >> "$EVENTS"
  [[ "$1" == status ]] || return 99
  if [[ "$3" == WS-R-cached ]]; then
    printf '{"ok":false,"error":"workspace identity mismatch","workspace_id":"WS-R-existing","path":"/slot"}\\n'
  else
    printf '{"ok":true,"status":{"id":"WS-R-existing","path":"/slot"}}\\n'
  fi
}
workspace_remote_identity /slot
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["id"], "WS-R-existing")
        self.assertEqual(self.events.read_text().splitlines(), [
            "/slot --target lab", "status /slot WS-R-cached main",
            "reconcile:/slot",
            "status /slot WS-R-existing main",
        ])
        self.assertEqual(json.loads(result.stdout)["rev"], "main")

    def test_align_options_are_transported_without_local_or_remote_gh(self):
        self.runtime_fixture("import json,sys\nprint(json.dumps(json.loads(sys.argv[1])))\n")
        prs = [{"owner": "org", "repo": "repo", "branch": "dev/review", "pr_number": 42}]
        result = self.shell("""
rlogin() { bash -s <<< "$*"; }
cmd_workspace """ + shlex.join(["align", "/independent/slot", "WS-R-stable",
                                "main", json.dumps({"prs": prs})]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "action": "align", "path": "/independent/slot", "id": "WS-R-stable",
            "rev": "main", "prs": prs,
        })

    def test_first_remote_registration_inherits_pinned_revision_and_preserves_existing(self):
        module = self.home / ".copilot/skills/workspace-manager/wm.py"
        module.parent.mkdir(parents=True)
        module.write_text("""
from contextlib import nullcontext
import json, os
from pathlib import Path
state = Path(os.environ["EVENTS"])
def store_lock():
    return nullcontext()
def load():
    return json.loads(state.read_text() or "{}")
def tracked(data):
    return data
def track(path, target, rev):
    data = load()
    data["slot"] = {"id":"WS-R-stable", "path":path, "target":target, "rev":rev}
    state.write_text(json.dumps(data))
    return True, "registered", "slot"
def identity(path, target):
    return dict(load()["slot"], slot="slot")
""")
        result = self.shell("""
T_NAME=lab
cmd_workspace() {
  printf '{"ok":true,"status":{"id":"%s","path":"%s","exists":true}}\\n' "$3" "$2"
}
workspace_remote_identity /remote/slot a1_power_on
workspace_remote_identity /remote/slot main
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["rev"] for row in rows], ["a1_power_on", "a1_power_on"])
        self.assertEqual(json.loads(self.events.read_text())["slot"]["rev"], "a1_power_on")

    def test_runtime_nonzero_exit_still_forwards_domain_json(self):
        self.runtime_fixture('print(\'{"ok":false,"error":"guard refused"}\')\nraise SystemExit(7)\n')
        result = self.shell("""
rlogin() { bash -s <<< "$*"; }
cmd_workspace free /remote/slot WS-R-stable a1_power_on
""")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(json.loads(result.stdout), {"ok": False, "error": "guard refused"})

    def mirror_stubs(self):
        saved = self.home / ".wip/saved/tag.wip"
        saved.parent.mkdir(parents=True)
        saved.write_text("stub snapshot")
        return """
T_NAME=lab; T_KIND=remote
find_ws() { printf '%s\\n' "$HOME"; }
map_remote() { printf '/remote%s\\n' "$1"; }
remote_home() { echo /remote/home; }
ws_tag() { echo tag; }
workspace_identity() {
  printf '{"id":"WS-L-source","path":"%s/slot","rev":"a1_power_on"}\\n' "$HOME"
}
workspace_remote_identity() {
  [[ "$2" == a1_power_on ]] || return 99
  printf '{"id":"WS-R-existing","path":"%s","rev":"main","exists":true,"safe":true,"clean":true}\\n' "$1"
}
workspace_slot_unoccupied() { [[ "${OCCUPIED:-0}" != 1 ]]; }
check_manifest_drift() { echo drift >> "$EVENTS"; }
wip() { :; }
snapshot_rows() { echo rows; }
push_snapshot_branches() { echo push >> "$EVENTS"; }
workspace_set_mirror() { printf 'remote:%s:%s:%s\\n' "$2" "$5" "$1" >> "$EVENTS"; }
workspace_mirror_state() { printf 'registry:%s\\n' "$4" >> "$EVENTS"; }
workspace_sync_slot() { echo "guarded-sync:$3" >> "$EVENTS"; }
sshdest() { echo fake-host; }
timeout() { echo copy >> "$EVENTS"; }
rlogin() {
  if [[ "$*" == *hard-reset* ]]; then
    echo align >> "$EVENTS"
    [[ "${FAIL_ALIGN:-0}" != 1 ]] || return 42
  fi
}
rlogin_cred() { echo forbidden-retry >> "$EVENTS"; return 99; }
verify_remote_branches() {
  echo verify >> "$EVENTS"
  [[ "${FAIL_VERIFY:-0}" != 1 ]] || die "verification failed"
}
"""

    def test_mirror_tracks_slot_identity_not_fw_and_activates_after_verification(self):
        result = self.shell(self.mirror_stubs() + "\ncmd_mirror --sync\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.events.read_text().splitlines()
        remote_path = "/remote" + str(self.home) + "/slot"
        self.assertIn(f"remote:WS-R-existing:syncing:{remote_path}", rows)
        self.assertLess(rows.index("guarded-sync:main"), rows.index("registry:syncing"))
        self.assertLess(rows.index("registry:syncing"), rows.index("align"))
        self.assertLess(rows.index("verify"), rows.index("registry:active"))
        self.assertNotIn("forbidden-retry", rows)

    def test_failed_alignment_or_verification_never_looks_active(self):
        stubs = self.mirror_stubs()
        for failure in ["FAIL_ALIGN=1", "FAIL_VERIFY=1"]:
            with self.subTest(failure=failure):
                self.events.write_text("")
                result = self.shell(stubs + "\n" + failure + "\ncmd_mirror\n")
                self.assertNotEqual(result.returncode, 0)
                rows = self.events.read_text().splitlines()
                self.assertIn("registry:failed", rows)
                self.assertNotIn("registry:active", rows)
                self.assertNotIn("mirror aligned", result.stderr)

    def test_occupied_target_is_rejected_before_push_or_remote_alignment(self):
        result = self.shell(self.mirror_stubs() + "\nOCCUPIED=1\ncmd_mirror\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events.read_text(), "")

    def test_guard_refuses_unsafe_independent_or_another_sources_slot(self):
        for status in [
            {"exists": False, "safe": True, "clean": True},
            {"exists": True, "safe": False, "clean": True},
            {"exists": True, "safe": True, "clean": False},
            {"exists": True, "safe": True, "clean": True,
             "mirror": {"source_id": "WS-L-other", "state": "active"}},
        ]:
            with self.subTest(status=status):
                result = self.shell("workspace_mirror_guard " + shlex.join([
                    json.dumps(status), "WS-L-source",
                ]))
                self.assertNotEqual(result.returncode, 0)

    def test_action_history_omits_commands_and_preserves_failure_exit(self):
        module = self.home / ".copilot/skills/workspace-manager/wm.py"
        module.parent.mkdir(parents=True)
        module.write_text("""
import json, os
def identity(path, target=""):
    return {"slot": "remote" if target else "local"}
def log_action(slot, action, outcome, detail):
    with open(os.environ["EVENTS"], "a") as stream:
        stream.write(json.dumps([slot, action, outcome, detail]) + "\\n")
""")
        for rc in [0, 23]:
            with self.subTest(rc=rc):
                self.events.write_text("")
                result = self.shell("""
T_NAME=lab
find_ws() { echo /local/slot/fw; }
map_remote() { echo /remote/slot/fw; }
rlogin_cred() { return """ + str(rc) + """; }
( cmd_sh 'echo secret-password-do-not-log' )
""")
                self.assertEqual(result.returncode, rc, result.stderr)
                rows = [json.loads(row) for row in self.events.read_text().splitlines()]
                self.assertEqual(rows, [
                    ["local", "mctl-sh", "ok" if rc == 0 else "error", "target=lab"],
                    ["remote", "mctl-sh", "ok" if rc == 0 else "error", "target=lab"],
                ])
                self.assertNotIn("secret-password", self.events.read_text())

    def release_stubs(self):
        return """
T_NAME=lab
find_ws() { echo /local/slot/fw; }
map_remote() { echo /remote/slot; }
workspace_identity() { echo '{"id":"WS-L-source","path":"/local/slot","rev":1}'; }
workspace_remote_identity() {
  printf '{"id":"WS-R-existing","path":"/remote/slot","rev":"main","mirror":{"source_id":"%s"}}\\n' "${OWNER:-WS-L-source}"
}
workspace_checked() {
  echo "$*" >> "$EVENTS"
  [[ "${FAIL_FREE:-0}" != 1 ]] || { echo occupied >&2; return 1; }
  echo '{"ok":true}'
}
workspace_mirror_state() { echo "registry:$4" >> "$EVENTS"; }
rlogin_cred() { echo forbidden-retry >> "$EVENTS"; return 99; }
"""

    def test_release_uses_guarded_slot_free_then_clears_registry(self):
        result = self.shell(self.release_stubs() + "\ncmd_release\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), [
            "free /remote/slot WS-R-existing main", "registry:released",
        ])
        self.assertIn("release complete", result.stderr)

    def test_failed_release_retains_relation_and_does_not_claim_success(self):
        result = self.shell(self.release_stubs() + "\nFAIL_FREE=1\ncmd_release\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("registry:released", self.events.read_text())
        self.assertNotIn("release complete", result.stderr)

    def test_release_refuses_another_source_without_free(self):
        result = self.shell(self.release_stubs() + "\nOWNER=WS-L-other\ncmd_release\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.events.read_text(), "")


if __name__ == "__main__":
    unittest.main()
