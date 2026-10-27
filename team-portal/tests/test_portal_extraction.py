import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
TM_DIR = ROOT / "skills" / "task-manager"
PORTAL_DIR = ROOT / "team-portal"
for _p in (TM_DIR, PORTAL_DIR):
    sys.path.insert(0, str(_p))
import portal
import task_store
import tm


def run(code, env=None):
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env or os.environ, cwd=str(ROOT))


class ExtractionTests(unittest.TestCase):
    def test_no_circular_import(self):
        pre = f"import sys; sys.path[:0]=[{str(TM_DIR)!r},{str(PORTAL_DIR)!r}];"
        self.assertEqual(run(pre + "import tm").returncode, 0)
        self.assertEqual(run(pre + "import portal").returncode, 0)

    def test_portal_runtime_moved_out_of_tm(self):
        for name in ("cmd_serve", "render_dashboard_html", "start_workspace_job", "apply_task_edit",
                     "PID_FILE", "ALIGN_JOBS"):
            self.assertTrue(hasattr(portal, name), name)
            if name not in ("cmd_serve", "apply_task_edit"):
                self.assertFalse(hasattr(tm, name), name)

    def test_tm_task_api_present(self):
        for name in ("load", "save", "cmd_add", "cmd_add_review", "edit_task",
                     "edit_task_fields", "apply_task_edit", "cmd_edit", "cmd_serve", "cmd_dashboard", "STORE"):
            self.assertTrue(hasattr(tm, name), name)

    def test_defaults(self):
        self.assertEqual(portal.build_parser().parse_args(["serve"]).port, 8787)
        self.assertEqual(portal.PID_FILE.name, ".dashboard.pid")
        self.assertEqual(
            portal.PID_FILE.parent,
            Path.home() / ".copilot-private" / "team-portal" / "state")
        self.assertEqual(portal.ASSETS, PORTAL_DIR / "assets")

    def test_legacy_cli_delegates(self):
        with patch.object(tm, "_portal") as p:
            p.return_value.cmd_serve.return_value = 0
            p.return_value.cmd_dashboard.return_value = 0
            ns = argparse.Namespace(port=8787, stop=False, status=False, output=None)
            tm.cmd_serve(ns)
            tm.cmd_dashboard(ns)
            p.return_value.cmd_serve.assert_called_once_with(ns)
            p.return_value.cmd_dashboard.assert_called_once_with(ns)

    def test_legacy_loader_resolves_standalone_portal(self):
        self.assertIs(tm._portal(), portal)
        self.assertEqual(Path(portal.__file__).resolve(), PORTAL_DIR / "portal.py")

    def test_skill_is_instruction_only(self):
        skill = ROOT / "skills" / "portal-maintainer"
        self.assertEqual(sorted(p.name for p in skill.iterdir()), ["SKILL.md"])

    def test_legacy_help_parity(self):
        for cmd in ("serve", "dashboard"):
            a = run(f"import sys; sys.argv=['tm','{cmd}','--help']; sys.path.insert(0,{str(TM_DIR)!r}); import tm; tm.main()")
            b = run(f"import sys; sys.argv=['portal','{cmd}','--help']; sys.path[:0]=[{str(TM_DIR)!r},{str(PORTAL_DIR)!r}]; import portal; portal.main()")
            self.assertEqual(a.returncode, 0, a.stderr)
            self.assertEqual(b.returncode, 0, b.stderr)
            for flag in ("--port", "--stop", "--status") if cmd == "serve" else ("-o",):
                self.assertIn(flag, a.stdout)
                self.assertIn(flag, b.stdout)


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        for target, attr, val in ((task_store, "DB_PATH", t / "tasks.db"),
                                  (task_store, "LEGACY", t / "none.json"),
                                  (tm, "STORE", t / "tasks.json")):
            p = patch.object(target, attr, val)
            p.start()
            self.addCleanup(p.stop)
        wm = patch.object(tm.wm, "occupy_task")
        wm.start()
        self.addCleanup(wm.stop)

    def add(self, title):
        ns = argparse.Namespace(title=title, description="d", category="misc", priority=None,
                                complexity=None, cost=None, context="", jira=None,
                                workspace=None, no_workspace=True, backlog=True, cwd=None)
        self.assertEqual(tm.cmd_add(ns), 0)

    def test_distinct_ids_and_edit_preserves_fields(self):
        self.add("one")
        self.add("two")
        ids = [t["id"] for t in tm.load()["tasks"]]
        self.assertEqual(len(set(ids)), 2)
        ok, _ = tm.edit_task(ids[0], title="uno", description=None)
        self.assertTrue(ok)
        t = tm.load()["tasks"][0]
        self.assertEqual((t["title"], t["description"], t["category"]), ("uno", "d", "misc"))
        ok, _ = tm.edit_task(ids[0], title="", description=None)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
