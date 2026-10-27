"""Exercise the import coordinator in a private synthetic HOME."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class MigrationTests(unittest.TestCase):
    def test_rehearsal_preserves_sources_and_replays_without_duplicates(self):
        with tempfile.TemporaryDirectory() as name:
            home = Path(name)
            tasks = home / "tasks"
            tasks.mkdir()
            fixtures = {
                tasks / "tasks.json": {"next_id": 2, "tasks": [
                    {"id": "T-001", "status": "todo", "notes": ["original"]}]},
                tasks / "workspaces.json": {
                    "version": 1, "tracked": {}, "groups": [],
                    "slots": {"one": {"holder": {"type": "task", "task_id": "T-001"}}}},
                tasks / "pldm-jobs.json": {"jobs": [{"id": 1, "opaque": "kept"}],
                                         "last_scan": None},
                home / ".mctl/jobstate.json": {"jobs": {}, "errors": {}},
                home / ".mctl/vmstate.json": {"machines": [], "checked": ""},
                tasks / "pr-monitor-cache.json": {"features": []},
            }
            for path, value in fixtures.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value))
            memoir = home / ".copilot/memories/pr-monitor-features.md"
            memoir.parent.mkdir(parents=True)
            memoir.write_text("# Features\n## [1] Example\n- **Description:** Original **text**\n")
            raid = home / "team-planner/raid.md"
            raid.parent.mkdir()
            raid.write_text("# RAID\n### R-001 | Risk\n- **Title:** Original **title**\n")
            log = home / ".mctl/joblog.jsonl"
            log.write_text(json.dumps({"id": "dispatch-1", "target": "lab"}) + "\n")
            originals = {p: p.read_bytes() for p in [*fixtures, memoir, raid, log]}
            environment = dict(os.environ, HOME=str(home))
            environment["TASKS_LEGACY_JSON"] = str(tasks / "tasks.json")
            environment["WORKSPACES_LEGACY_JSON"] = str(tasks / "workspaces.json")
            environment["PJQ_STORE"] = str(tasks / "pldm-jobs.json")
            script = Path(__file__).resolve().parents[1] / "migrate.py"
            manifest = home / "stage" / "migration-manifest.json"
            command = [sys.executable, str(script), "--database-dir", str(home / "stage"),
                       "--manifest", str(manifest)]
            for _ in range(2):
                result = subprocess.run(command, env=environment, text=True,
                                        capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertTrue(report["verified"])
                self.assertEqual(report["domains"]["tasks"], 1)
                self.assertEqual(report["domains"]["delegation"], 1)
                self.assertTrue(json.loads(manifest.read_text())["verified"])
            for path, original in originals.items():
                self.assertEqual(path.read_bytes(), original)
            self.assertFalse((tasks / "tasks.db").exists())


if __name__ == "__main__":
    unittest.main()
