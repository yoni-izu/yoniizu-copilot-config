"""VM power memory / auto-start and `mctl provision`.

Local fixtures only: octl is a stub, recipes are throwaway local ones, and no
machine is contacted (ssh would fail fast against the fake addresses).
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vd

IID = "i-012e7b37b8bfe283b"
MCTL = Path.home() / "bin" / "mctl"
ISO = "%Y-%m-%dT%H:%M:%SZ"


def lease_tag(offset: int) -> str:
    return "lease_expires_at=" + time.strftime(ISO, time.gmtime(time.time() + offset))


class VdPowerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="vd-power-"))
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "targets").write_text(f"vm vm me {IID}\ndsk61 remote me dsk61\n")
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in (("DB", self.root / "delegation.db"), ("JOBLOG", self.root / "joblog.jsonl"),
                            ("JOBSTATE", self.root / "jobstate.json"), ("VMSTATE", self.root / "vmstate.json"),
                            ("MCTL_DIR", self.root), ("TARGETS", self.root / "targets"),
                            ("MCTL", self.root / "mctl"), ("PROJECTS", self.root / "projects"),
                            ("POWER_DIR", self.root / "power"), ("POWER_LOG", self.root / "power.log"),
                            ("PROVISION_STATE", self.root / "provision")):
            stack.enter_context(patch.object(vd, name, value))
        self.run = stack.enter_context(patch.object(
            vd.subprocess, "run", side_effect=AssertionError("external commands forbidden")))
        (self.root / "power").mkdir()

    def test_power_view_started_and_predicted_stop(self):
        now = 1_000_000.0
        (self.root / "power" / IID).write_text(f"started={int(now) - 600}\nexpires={int(now) + 1200}\nby=auto\n")
        v = vd.power_view({"id": IID, "state": "running"}, now)
        self.assertEqual((v["started_by"], v["stop_ts"], v["predicted_stopped"]), ("auto", int(now) + 1200, False))
        self.assertTrue(v["started_ago_h"] and v["stop_in_h"])

    def test_cached_running_past_lease_is_predicted_stopped(self):
        now = 1_000_000.0
        (self.root / "power" / IID).write_text(f"started={int(now) - 4000}\nexpires={int(now) - 60}\n")
        v = vd.power_view({"id": IID, "state": "running"}, now)
        self.assertTrue(v["predicted_stopped"])
        self.assertEqual(v["stop_in_h"], "")
        self.assertFalse(vd.power_view({"id": IID, "state": "stopped"}, now)["predicted_stopped"])

    def test_power_view_falls_back_to_octl_lease_tag(self):
        v = vd.power_view({"id": "i-0000000000000000a", "state": "running", "lease": "1970-01-12T13:46:40Z"},
                          1_000_000.0 - 100)
        self.assertEqual(v["stop_ts"], 1_000_000)

    def test_power_events_newest_first_and_bounded(self):
        lines = [json.dumps({"ts": i, "event": "renew"}) for i in range(20)] + ["not json"]
        (self.root / "power.log").write_text("\n".join(lines) + "\n")
        ev = vd.power_events(limit=3)
        self.assertEqual([e["ts"] for e in ev], [19, 18, 17])

    def test_provision_status(self):
        self.assertIsNone(vd.provision_status("vm")["ok"])
        (self.root / "provision").mkdir()
        (self.root / "provision" / "vm.state").write_text(
            "dirs\tok\t100\t0\t\n"
            "copilot\tinstalled\t200\t6\ttodo: login\n"
            "rtl\tfail\t300\t9\tinstall exited 1\n")
        st = vd.provision_status("vm")
        self.assertFalse(st["ok"])
        self.assertEqual((st["failed"], st["todo"]), (["rtl"], ["copilot"]))
        self.assertEqual(len(st["recipes"]), 3)

    def test_keepalive_only_vm_targets_with_confirmed_running_jobs(self):
        rows = [{"target": "vm", "status": "running", "stale": False},
                {"target": "dsk61", "status": "running", "stale": False}]
        calls = []
        self.run.side_effect = lambda cmd, **_: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")
        with patch.object(vd, "jobs", return_value=rows):
            self.assertEqual(vd.keepalive(), {"vm": "ok"})
        self.assertEqual(calls, [[str(self.root / "mctl"), "-t", "vm", "lease", "renew", "--if-below",
                                  vd.KEEPALIVE_BELOW]])
        with patch.object(vd, "jobs", return_value=[{"target": "vm", "status": "running", "stale": True}]):
            self.assertEqual(vd.keepalive(), {})


@unittest.skipUnless(MCTL.exists(), "mctl not installed")
class MctlPowerProvisionTests(unittest.TestCase):
    """The real mctl against a throwaway HOME, a stub octl and local recipes."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="mctl-pp-"))
        self.addCleanup(shutil.rmtree, self.root)
        home, bin_ = self.root / "home", self.root / "bin"
        self.mdir = home / ".mctl"
        self.mdir.mkdir(parents=True)
        bin_.mkdir()
        octl = bin_ / "octl"
        octl.write_text("#!/bin/bash\n"
                        "if [[ \"$1 $2\" == 'vm list' ]]; then cat \"$OCTL_JSON\";"
                        " else echo \"OCTL $*\" >> \"$OCTL_CALLS\"; fi\n")
        octl.chmod(0o755)
        self.calls = self.root / "calls"
        self.set_vm("stopped", -900, "192.0.2.1")
        (self.mdir / "targets").write_text(f"vm vm me {IID}\ndsk61 remote me 192.0.2.9\n")
        (self.mdir / "dsk61.home").write_text("/home/me\n")
        self.prov = self.root / "prov"
        (self.prov / "recipes").mkdir(parents=True)
        (self.prov / "lists").mkdir()
        self.env = dict(os.environ, HOME=str(home), PATH=f"{bin_}:{os.environ['PATH']}",
                        OCTL_JSON=str(self.root / "list.json"), OCTL_CALLS=str(self.calls),
                        MCTL_TARGET="", MCTL_PROVISION_DIR=str(self.prov), MCTL_WAKE_TIMEOUT="1",
                        NO_COLOR="1")
        self.env.pop("VM_IP", None)

    def set_vm(self, state, lease_offset, ip):
        (self.root / "list.json").write_text(json.dumps({"aws": [
            {"name": "me-personal-instance", "instance_id": IID, "state": state, "private_ip": ip,
             "instance_type": "m7i.48xlarge", "tags": ["owner=me", lease_tag(lease_offset)]}]}))

    def mctl(self, *args):
        return subprocess.run([str(MCTL), *args], env=self.env, capture_output=True, text=True, timeout=90)

    def octl_calls(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def recipe(self, name, body, **meta):
        head = "".join(f"# {k}: {v}\n" for k, v in {"desc": name, "where": "local", **meta}.items())
        (self.prov / "recipes" / f"{name}.sh").write_text(head + body)

    # ---- power / lease -------------------------------------------------
    def test_lease_status_reads_octl_once_then_remembers(self):
        self.set_vm("running", 1200, "192.0.2.1")
        r = self.mctl("-t", "vm", "lease")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("predicted", r.stdout)
        self.assertIn("expires=", (self.mdir / "power" / IID).read_text())
        os.unlink(self.root / "list.json")          # a second status needs no octl
        self.assertEqual(self.mctl("-t", "vm", "lease").returncode, 0)

    def test_passive_commands_never_start_a_stopped_vm(self):
        self.mctl("-t", "vm", "ip")
        self.mctl("-t", "vm", "jobs-probe")
        self.assertFalse([c for c in self.octl_calls() if "start" in c])

    def test_active_command_auto_starts_with_short_lease(self):
        r = self.mctl("-t", "vm", "sh", "true")      # ssh never answers → wake times out
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(f"OCTL vm start {IID} --lease 30m -w", self.octl_calls())
        mem = (self.mdir / "power" / IID).read_text()
        self.assertIn("by=auto", mem)
        ev = [json.loads(l) for l in (self.mdir / "power.log").read_text().splitlines()]
        self.assertEqual(ev[-1]["event"], "auto-start")

    def test_autostart_can_be_disabled(self):
        self.env["MCTL_AUTOSTART"] = "0"
        self.mctl("-t", "vm", "sh", "true")
        self.assertFalse([c for c in self.octl_calls() if "start" in c])

    def test_lease_renew_if_below_skips_a_fresh_lease(self):
        (self.mdir / "power").mkdir()
        (self.mdir / "power" / IID).write_text(f"expires={int(time.time()) + 1700}\nstate=running\n")
        self.assertEqual(self.mctl("-t", "vm", "lease", "renew", "--if-below", "10m").returncode, 0)
        self.assertEqual(self.octl_calls(), [])
        (self.mdir / "power" / IID).write_text(f"expires={int(time.time()) + 300}\nstate=running\n")
        self.assertEqual(self.mctl("-t", "vm", "lease", "renew", "--if-below", "10m").returncode, 0)
        self.assertEqual(self.octl_calls(), [f"OCTL vm lease renew {IID} --lease 30m"])

    # ---- provision -----------------------------------------------------
    def test_dry_run_expands_includes_dedupes_and_changes_nothing(self):
        self.recipe("a", "check() { true; }\ninstall() { exit 9; }\n")
        self.recipe("b", "check() { false; }\ninstall() { touch \"$HOME/b\"; }\n")
        (self.prov / "lists" / "base.list").write_text("# base\na\n")
        (self.prov / "lists" / "machine.list").write_text("include base\nb   # trailing comment\na\n")
        r = self.mctl("-t", "dsk61", "provision", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("a b", r.stderr)
        self.assertIn("a: already in place", r.stderr)
        self.assertIn("b: missing", r.stderr)
        self.assertFalse((self.root / "home" / "b").exists())
        self.assertFalse((self.mdir / "provision" / "dsk61.state").exists())

    def test_run_installs_records_and_blocks_dependents(self):
        self.recipe("ok1", "check() { [ -f \"$HOME/ok1\" ]; }\ninstall() { touch \"$HOME/ok1\"; }\n"
                    "todo() { echo \"log in on $MCTL_DEST\"; }\n")
        self.recipe("bad", "check() { false; }\ninstall() { echo boom; false; }\n")
        self.recipe("dep", "check() { false; }\ninstall() { true; }\n", needs="bad")
        (self.prov / "lists" / "machine.list").write_text("ok1\nbad\ndep\n")
        r = self.mctl("-t", "dsk61", "provision")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ok1: installed", r.stderr)
        self.assertIn("manual step — ok1: log in on me@192.0.2.9", r.stderr)
        self.assertIn("dep: blocked", r.stderr)
        rows = {l.split("\t")[0]: l.split("\t") for l in
                (self.mdir / "provision" / "dsk61.state").read_text().splitlines()}
        self.assertEqual({k: v[1] for k, v in rows.items()}, {"ok1": "installed", "bad": "fail", "dep": "blocked"})
        self.assertTrue((self.root / "home" / "ok1").exists())
        # second run: ok1 is skipped, --skip drops the failing pair
        r = self.mctl("-t", "dsk61", "provision", "--skip", "bad,dep")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ok1: already in place", r.stderr)
        st = self.mctl("-t", "dsk61", "provision", "--status")
        self.assertRegex(st.stdout, r"ok1\s+ok\s")

    def test_only_and_unknown_recipe_and_list(self):
        self.recipe("x", "check() { true; }\ninstall() { true; }\n")
        (self.prov / "lists" / "machine.list").write_text("x\n")
        self.assertNotEqual(self.mctl("-t", "dsk61", "provision", "--only", "nope").returncode, 0)
        self.assertNotEqual(self.mctl("-t", "dsk61", "provision", "--list", "nope").returncode, 0)
        self.assertNotEqual(self.mctl("-t", "dsk61", "provision", "--only", "../x").returncode, 0)
        r = self.mctl("-t", "dsk61", "provision", "--only", "x")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_list_file_path_and_sibling_recipes(self):
        custom = self.root / "mine"
        (custom / "recipes").mkdir(parents=True)
        (custom / "recipes" / "own.sh").write_text("# where: local\ncheck() { true; }\ninstall() { true; }\n")
        (custom / "lists").mkdir()
        (custom / "lists" / "my.list").write_text("own\n")
        r = self.mctl("-t", "dsk61", "provision", "--list", str(custom / "lists" / "my.list"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("own: already in place", r.stderr)


class ShippedRecipesTests(unittest.TestCase):
    """The tracked default lists only name recipes that exist and parse."""

    PROV = HERE / "provision"

    def test_default_lists_resolve(self):
        names = set()
        for lst in ("vm", "machine"):
            for line in (self.PROV / "lists" / f"{lst}.list").read_text().splitlines():
                w = line.split("#")[0].split()
                if w and w[0] != "include":
                    names.add(w[0])
        for n in names:
            f = self.PROV / "recipes" / f"{n}.sh"
            self.assertTrue(f.exists(), n)
            self.assertEqual(subprocess.run(["bash", "-n", str(f)]).returncode, 0, n)
            text = f.read_text()
            self.assertIn("check()", text, n)
            self.assertIn("install()", text, n)


if __name__ == "__main__":
    unittest.main()
