"""Main-VM switching: instance-bound targets in vd.py and `mctl vm`.

Local fixtures only: octl is a stub, no machine is probed or powered.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import delegation_db
import vd

OLD, NEW = "i-0ebef8d9fb0fc8d84", "i-012e7b37b8bfe283b"
OCTL_JSON = {"aws": [
    {"name": "me-personal-instance", "instance_id": OLD, "state": "stopped",
     "private_ip": "10.0.0.1", "instance_type": "m7i.48xlarge",
     "tags": ["owner=me", "description=old"]},
    {"name": "me-personal-instance", "instance_id": NEW, "state": "running",
     "private_ip": "10.0.0.2", "instance_type": "m7i.48xlarge",
     "tags": ["owner=me", "description=new"]},
]}
REGISTRY = f"""# name kind user host
vm-0ebef8   vm      -        {OLD}
vm          vm      me       {NEW}
dsk61       remote  me       dsk61
"""


class VdMainTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="vd-main-"))
        self.addCleanup(shutil.rmtree, self.root)
        self.targets = self.root / "targets"
        self.targets.write_text(REGISTRY)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in (("DB", self.root / "delegation.db"), ("JOBLOG", self.root / "joblog.jsonl"),
                            ("JOBSTATE", self.root / "jobstate.json"), ("VMSTATE", self.root / "vmstate.json"),
                            ("MCTL_DIR", self.root), ("TARGETS", self.targets),
                            ("MCTL", self.root / "mctl"), ("PROJECTS", self.root / "projects")):
            stack.enter_context(patch.object(vd, name, value))
        self.run = stack.enter_context(patch.object(
            vd.subprocess, "run", side_effect=AssertionError("external commands forbidden")))

    def octl_ok(self):
        self.run.side_effect = None
        self.run.return_value = subprocess.CompletedProcess(
            [], 0, "INF Validating SSO credentials\n" + json.dumps(OCTL_JSON) + "\nA new release is available\n", "")

    def test_bindings_ignore_comments_remote_and_unbound(self):
        self.targets.write_text(REGISTRY + "legacy vm - -\n# vm vm - i-00000000\n")
        self.assertEqual(vd.target_bindings(), {"vm-0ebef8": OLD, "vm": NEW})
        self.assertEqual(vd.main_instance(), NEW)

    def test_machines_match_by_instance_not_shared_name_or_ip(self):
        (self.root / "vm.ip").write_text("10.0.0.1\n")   # stale cache from the old VM
        self.octl_ok()
        ms = vd.machines(force=True)["machines"]
        self.assertEqual([(m["id"], m["target"], m["main"]) for m in ms],
                         [(NEW, "vm", True), (OLD, "vm-0ebef8", False)])

    def test_payload_reflects_registry_switch_without_requery(self):
        self.octl_ok()
        vd.machines(force=True)
        self.targets.write_text(f"vm vm - {OLD}\nvm-012e7b vm me {NEW}\n")
        self.run.side_effect = AssertionError("payload must not re-query octl")
        p = vd.payload()
        self.assertEqual(p["main_instance"], OLD)
        self.assertEqual(p["machines"][0]["id"], OLD)
        self.assertTrue(p["machines"][0]["main"])
        state = {t["name"]: t for t in p["target_state"]}
        self.assertTrue(state["vm"]["main"])
        self.assertEqual(state["vm-012e7b"]["instance"], NEW)

    def test_unbound_legacy_target_still_matches_by_cached_ip(self):
        self.targets.write_text("vm vm - -\n")
        (self.root / "vm.ip").write_text("10.0.0.2\n")
        self.octl_ok()
        ms = {m["id"]: m for m in vd.machines(force=True)["machines"]}
        self.assertEqual(ms[NEW]["target"], "vm")
        self.assertFalse(ms[NEW]["main"])
        self.assertTrue(vd.has_instance("vm"))

    def test_has_instance_uses_binding(self):
        self.assertTrue(vd.has_instance("vm-0ebef8"))
        self.assertFalse(vd.has_instance("dsk61"))

    def test_power_goes_through_mctl(self):
        self.assertIn("error", vd.power("start", "i-xyz"))
        self.assertIn("error", vd.power("reboot", OLD))
        calls = []

        def fake(cmd, **_):
            calls.append(cmd)
            if cmd[0] == "octl":
                return subprocess.CompletedProcess(cmd, 0, json.dumps(OCTL_JSON), "")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.run.side_effect = fake
        self.assertTrue(vd.power("start", OLD).get("ok"))
        self.assertEqual(calls[0], [str(self.root / "mctl"), "vm", "start", OLD])

    def test_power_failure_strips_ansi(self):
        self.run.side_effect = lambda cmd, **_: subprocess.CompletedProcess(
            cmd, 1, "", "\x1b[31m✖\x1b[0m mctl: start failed\n")
        self.assertEqual(vd.power("start", OLD), {"error": "✖ mctl: start failed"})

    def test_set_main_validates_and_calls_mctl(self):
        self.assertIn("error", vd.set_main("i-xyz"))
        self.assertIn("error", vd.set_main(OLD, "bad user;"))

        def fake(cmd, **_):
            self.targets.write_text(f"vm vm - {OLD}\nvm-012e7b vm me {NEW}\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.run.side_effect = fake
        self.assertEqual(vd.set_main(OLD), {"ok": True, "main": OLD})
        self.assertEqual(self.run.call_args.args[0], [str(self.root / "mctl"), "vm", "main", OLD])

    def test_set_main_reports_failure_when_registry_unchanged(self):
        self.run.side_effect = lambda cmd, **_: subprocess.CompletedProcess(
            cmd, 1, "", "\x1b[31m✖\x1b[0m mctl: instance i-0ebef8d9fb0fc8d84 is not one of your VMs\n")
        res = vd.set_main(OLD)
        self.assertIn("not one of your VMs", res["error"])
        self.assertEqual(vd.main_instance(), NEW)

    def test_target_add_remove_use_mctl_and_preserve_history(self):
        state = {"jobs": [], "targets": ["desk-long-name"], "machines": []}

        def fake(cmd, **_):
            if cmd[2] == "add":
                self.targets.write_text(REGISTRY + "desk-long-name remote test.user desktop.example\n")
            else:
                self.targets.write_text(REGISTRY)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.run.side_effect = fake
        with patch.object(vd, "payload", return_value=state), patch.object(vd, "jobs", return_value=[]):
            res = vd.portal_target({"action": "add", "name": "desk-long-name", "kind": "remote",
                                    "address": "desktop.example", "login": "test.user"})
            self.assertIn("registered", res["message"])
            self.assertEqual(self.run.call_args.args[0],
                             [str(vd.MCTL), "target", "add", "desk-long-name", "test.user@desktop.example"])
            vd.portal_target({"action": "remove", "name": "desk-long-name"})
            self.assertEqual(self.run.call_args.args[0], [str(vd.MCTL), "target", "rm", "desk-long-name"])
            self.assertEqual(self.targets.read_text(), REGISTRY)

    def test_target_vm_add_is_explicitly_instance_bound(self):
        iid = "i-12345678"

        def fake(cmd, **_):
            self.targets.write_text(REGISTRY + f"new-vm vm me {iid}\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.run.side_effect = fake
        with patch.object(vd, "payload", return_value={"jobs": [], "targets": []}):
            vd.portal_target({"action": "add", "name": "new-vm", "kind": "vm",
                              "address": iid, "login": "me"})
        self.assertEqual(self.run.call_args.args[0],
                         [str(vd.MCTL), "target", "add", "new-vm", "--vm", "me", iid])

    def test_target_invalid_inputs_never_run_a_command(self):
        valid = {"action": "add", "name": "new", "kind": "remote",
                 "address": "desktop.example", "login": "me"}
        invalid = [{}, {"action": "oops", "name": "new"}, valid | {"name": "vm"},
                   valid | {"name": "-bad"}, valid | {"name": "bad.*"},
                   valid | {"login": "me;cmd"}, valid | {"address": "--help"},
                   valid | {"address": "x\nvm vm - -"}, valid | {"kind": "bad"},
                   valid | {"kind": "vm", "address": NEW}, valid | {"kind": "vm", "address": "bad"},
                   valid | {"extra": True}, valid | {"name": 1}, valid | {"address": None},
                   {"action": "remove", "name": "vm"}, {"action": "remove", "name": "unknown"},
                   {"action": "remove", "name": "dsk61", "extra": True}]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValueError):
                vd.portal_target(data)
        self.run.assert_not_called()

    def test_target_bmc_add_set_clear_use_mctl(self):
        def fake(cmd, **_):
            if cmd[2] == "add":
                self.targets.write_text(REGISTRY + "desk remote me desk.example bmc-desk\n")
            elif cmd[2] == "bmc":
                self.targets.write_text(REGISTRY if cmd[4] == "-" else REGISTRY.replace(
                    "dsk61       remote  me       dsk61", "dsk61 remote me dsk61 " + cmd[4]))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.run.side_effect = fake
        with patch.object(vd, "payload", return_value={"jobs": []}):
            vd.portal_target({"action": "add", "name": "desk", "kind": "remote", "address": "desk.example",
                              "login": "me", "bmc": "bmc-desk"})
            self.assertEqual(self.run.call_args.args[0],
                             [str(vd.MCTL), "target", "add", "desk", "me@desk.example", "--bmc", "bmc-desk"])
            self.assertEqual(vd.target_bmcs(), {"desk": "bmc-desk"})
            self.targets.write_text(REGISTRY)
            res = vd.portal_target({"action": "bmc", "name": "dsk61", "bmc": "root@bmc-sa-dsk61-csr"})
            self.assertEqual(self.run.call_args.args[0],
                             [str(vd.MCTL), "target", "bmc", "dsk61", "root@bmc-sa-dsk61-csr"])
            self.assertIn("BMC set to root@bmc-sa-dsk61-csr", res["message"])
            res = vd.portal_target({"action": "bmc", "name": "dsk61", "bmc": ""})
            self.assertEqual(self.run.call_args.args[0][-1], "-")
            self.assertIn("cleared", res["message"])
            self.run.side_effect = lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, "", "")
            self.assertIn("did not persist",
                          vd.portal_target({"action": "bmc", "name": "dsk61", "bmc": "bmc-x"})["error"])

    def test_target_bmc_invalid_inputs_never_run_a_command(self):
        add = {"action": "add", "name": "new", "kind": "remote", "address": "desktop.example", "login": "me"}
        for data in [add | {"bmc": "x;rm"}, add | {"bmc": "-oProxyCommand=x"}, add | {"bmc": 3},
                     add | {"kind": "vm", "address": "i-12345678", "bmc": "bmc-x"},
                     {"action": "bmc", "name": "vm", "bmc": "bmc-x"},
                     {"action": "bmc", "name": "unknown", "bmc": "bmc-x"},
                     {"action": "bmc", "name": "dsk61"}, {"action": "bmc", "name": "dsk61", "bmc": "a b"},
                     {"action": "bmc", "name": "dsk61", "bmc": None}]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                vd.portal_target(data)
        self.run.assert_not_called()

    def test_target_remove_refuses_running_and_unverified_jobs(self):
        for status in ("running", "unknown"):
            with patch.object(vd, "jobs", return_value=[{"target": "dsk61", "status": status}]):
                with self.assertRaisesRegex(ValueError, "running or unverified"):
                    vd.portal_target({"action": "remove", "name": "dsk61"})
        self.run.assert_not_called()

    def test_target_errors_and_unchanged_registry_are_explicit(self):
        data = {"action": "add", "name": "new", "kind": "remote", "address": "desktop", "login": "me"}
        self.run.side_effect = lambda *a, **k: subprocess.CompletedProcess([], 1, "", "refused")
        self.assertEqual(vd.portal_target(data), {"error": "refused"})
        self.run.side_effect = lambda *a, **k: subprocess.CompletedProcess([], 0, "", "")
        self.assertIn("did not persist", vd.portal_target(data)["error"])
        self.run.side_effect = subprocess.TimeoutExpired("mctl", 15)
        self.assertIn("timed out", vd.portal_target(data)["error"])
        self.run.side_effect = OSError("missing mctl")
        self.assertEqual(vd.portal_target(data), {"error": "missing mctl"})


MCTL = Path.home() / "bin" / "mctl"


@unittest.skipUnless(MCTL.exists(), "mctl not installed")
class MctlVmTests(unittest.TestCase):
    """Runs the real mctl against a throwaway HOME and a stub octl."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mctl-vm-"))
        self.addCleanup(shutil.rmtree, self.root)
        home, bin_ = self.root / "home", self.root / "bin"
        (home / ".mctl").mkdir(parents=True)
        bin_.mkdir()
        octl = bin_ / "octl"
        octl.write_text("#!/bin/bash\n"
                        "if [[ \"$1 $2\" == 'vm list' ]]; then echo 'INF noise'; cat \"$OCTL_JSON\";"
                        " echo 'A new release'; else echo \"OCTL $*\" >> \"$OCTL_CALLS\"; fi\n")
        octl.chmod(0o755)
        (self.root / "list.json").write_text(json.dumps(OCTL_JSON))
        self.reg = home / ".mctl" / "targets"
        self.reg.write_text(f"vm      vm      -       {OLD}\ndsk61 remote me dsk61\n")
        self.env = dict(os.environ, HOME=str(home), PATH=f"{bin_}:{os.environ['PATH']}",
                        OCTL_JSON=str(self.root / "list.json"), OCTL_CALLS=str(self.root / "calls"),
                        MCTL_TARGET="")
        self.env.pop("VM_IP", None)

    def mctl(self, *args):
        return subprocess.run([str(MCTL), *args], env=self.env, capture_output=True, text=True, timeout=60)

    def registry(self):
        return {c[0]: c[1:] for c in (l.split() for l in self.reg.read_text().splitlines()) if c}

    def test_switch_keeps_previous_and_round_trips(self):
        (self.reg.parent / "vm.ip").write_text("10.0.0.1\n")
        r = self.mctl("vm", "main", NEW, "--user", "me")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.registry()["vm"], ["vm", "me", NEW])
        self.assertEqual(self.registry()["vm-0ebef8"], ["vm", "-", OLD])
        self.assertFalse((self.reg.parent / "vm.ip").exists())
        self.assertEqual(self.mctl("vm", "main").stdout.strip(), NEW)
        self.assertIn(OLD, self.mctl("vm", "list").stdout)

        r = self.mctl("vm", "main", OLD)    # login comes from the kept target
        self.assertEqual(r.returncode, 0, r.stderr)
        reg = self.registry()
        self.assertEqual(reg["vm"], ["vm", "-", OLD])
        self.assertEqual(reg["vm-012e7b"], ["vm", "me", NEW])
        self.assertNotIn("vm-0ebef8", reg)
        self.assertIn("dsk61", reg)

    def test_ip_and_power_use_the_bound_instance(self):
        self.assertEqual(self.mctl("vm", "main", NEW, "--user", "me").returncode, 0)
        self.assertEqual(self.mctl("-t", "vm", "ip").stdout.strip(), "10.0.0.2")
        r = self.mctl("-t", "vm-0ebef8", "start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.root / "calls").read_text().strip(), f"OCTL vm start {OLD} --lease 30m")

    def test_unbound_target_refuses_ambiguous_running_vms(self):
        both = json.loads(json.dumps(OCTL_JSON))
        both["aws"][0]["state"] = "running"
        (self.root / "list.json").write_text(json.dumps(both))
        self.reg.write_text("vm vm - -\n")
        r = self.mctl("-t", "vm", "ip")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not bound", r.stderr)
        self.assertNotEqual(self.mctl("-t", "vm", "start").returncode, 0)

    def test_rejects_unknown_or_malformed_instance(self):
        self.assertNotEqual(self.mctl("vm", "main", "i-deadbeef00").returncode, 0)
        self.assertNotEqual(self.mctl("vm", "main", "xyz").returncode, 0)
        self.assertEqual(self.registry()["vm"], ["vm", "-", OLD])

    def test_add_long_names_and_logins_remain_separate_columns(self):
        r = self.mctl("target", "add", "build-desktop-long", "long.login.for.testing@desktop.example")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.registry()["build-desktop-long"],
                         ["remote", "long.login.for.testing", "desktop.example"])
        r = self.mctl("target", "rm", "build-desktop-long")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("build-desktop-long", self.registry())
        self.assertIn("vm", self.registry())
        self.assertFalse((self.root / "calls").exists(), "registry changes must not call octl")


if __name__ == "__main__":
    unittest.main()
