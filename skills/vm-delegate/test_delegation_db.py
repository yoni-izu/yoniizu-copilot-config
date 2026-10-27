"""Local fixtures only: no machine probes, VM controls or live migration."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest
from unittest.mock import patch
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import delegation_db
import vd


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.root = HERE / (".delegation-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.log = self.root / "joblog.jsonl"
        self.state = self.root / "jobstate.json"
        self.vms = self.root / "vmstate.json"
        self.path = self.root / "delegation.db"
        self.store = delegation_db.Store(self.path, self.log, self.state, self.vms)
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, value in (("DB", self.path), ("JOBLOG", self.log),
                            ("JOBSTATE", self.state), ("VMSTATE", self.vms),
                            ("MCTL_DIR", self.root), ("TARGETS", self.root / "targets"),
                            ("PROJECTS", self.root / "projects")):
            stack.enter_context(patch.object(vd, name, value))
        stack.enter_context(patch.object(vd.subprocess, "run",
                                         side_effect=AssertionError("external commands forbidden")))

    def dispatch(self, ident="id-1", target="vm", tag="tag", **extra):
        return dict(id=ident, target=target, tag=tag, kind="build",
                    started="2026-10-25T12:00:00", **extra)

    def append(self, *rows):
        with self.log.open("a") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")

    def audit_count(self):
        with sqlite3.connect(self.path) as c:
            return c.execute("SELECT count(*) FROM audit").fetchone()[0]

    def test_plain_dictionary_cannot_bypass_snapshot_conflict_checks(self):
        state = self.store.read("jobstate")
        with self.assertRaisesRegex(ValueError, "snapshot"):
            self.store.write("jobstate", dict(state))
        self.assertEqual(self.store.read("jobstate"), state)

    def test_backed_import_replay_preserves_full_payload_and_never_rewrites(self):
        dispatch = self.dispatch(unknown={"a": [1]})
        self.append(dispatch)
        jobstate = {"jobs": {"id-1": {"status": "running", "checked": "old",
                                     "new_field": [1]}},
                    "checked": {"vm": "old"}, "attempted": {"vm": "error-time"},
                    "errors": {"vm": "offline"}, "unknown_root": {"x": 1}}
        vmstate = {"machines": [{"id": "i-123", "extra": True}], "checked": "old",
                   "error": "", "unknown": "retained"}
        self.state.write_text(json.dumps(jobstate))
        self.vms.write_text(json.dumps(vmstate))
        originals = [p.read_bytes() for p in (self.log, self.state, self.vms)]
        expected = {"joblog": [dispatch], "jobstate": jobstate, "vmstate": vmstate}
        self.assertEqual(self.store.export(), expected)
        count = self.audit_count()
        self.assertEqual(self.store.export(), expected)
        self.assertEqual(self.audit_count(), count)
        self.assertEqual(len(list((self.root / "migration-backups/delegation").glob("*.bak"))), 2)
        state = vd._read_state()
        state["errors"]["vm"] = "still offline"
        vd._write_state(state)
        self.assertEqual([p.read_bytes() for p in (self.log, self.state, self.vms)], originals)
        self.state.write_text("corrupt after import")
        self.assertEqual(vd._read_state()["errors"]["vm"], "still offline")

    def test_ingestion_dedup_rotation_and_conflicting_identity(self):
        first = self.dispatch()
        self.append(first, first)
        self.assertEqual(self.store.history(), [first])
        self.log.write_text(json.dumps(self.dispatch("id-2")) + "\n")
        self.assertEqual(len(self.store.history()), 2)
        self.append(self.dispatch("id-3"), self.dispatch(cmd="conflicting"))
        count = self.audit_count()
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.store.history()
        self.assertEqual(self.audit_count(), count)
        self.log.unlink()
        self.assertEqual(len(self.store.history()), 2)

    def test_malformed_import_and_log_fail_explicitly_and_roll_back(self):
        self.state.write_text('{"jobs":[]}')
        with self.assertRaises(ValueError):
            self.store.history()
        self.assertEqual(self.audit_count(), 0)
        self.state.write_text('{"jobs":{}}')
        self.append(self.dispatch())
        with self.log.open("a") as f:
            f.write('{"partial":')
        with self.assertRaisesRegex(ValueError, "joblog.jsonl:2"):
            self.store.history()
        self.assertEqual(self.audit_count(), 0)
        self.log.write_text(json.dumps(self.dispatch()) + "\n")
        self.assertEqual(len(self.store.history()), 1)

    def test_scoped_updates_merge_and_old_snapshot_does_not_erase_target(self):
        self.append(self.dispatch(), self.dispatch("id-2", "lab"))
        self.store.history()
        a, b = vd._read_state(), vd._read_state()
        a.update(last_attempt="2026-10-25T12:01:00")
        a["attempted"]["vm"] = "2026-10-25T12:01:00"
        a["jobs"]["id-1"] = {"status": "running", "checked": "2026-10-25T12:01:00"}
        b.update(last_attempt="2026-10-25T12:02:00")
        b["attempted"]["lab"] = "2026-10-25T12:02:00"
        b["errors"]["lab"] = "offline"
        vd._write_state(b)
        vd._write_state(a)
        result = vd._read_state()
        self.assertEqual(result["errors"], {"lab": "offline"})
        self.assertEqual(result["jobs"]["id-1"]["status"], "running")
        self.assertEqual(set(result["attempted"]), {"vm", "lab"})
        self.assertEqual(result["last_attempt"], "2026-10-25T12:02:00")

    def test_older_probe_cannot_replace_newer_reading_or_error(self):
        self.append(self.dispatch())
        self.store.history()
        a, b = vd._read_state(), vd._read_state()
        for st, stamp, status in ((a, "2026-10-25T12:01:00", "running"),
                                  (b, "2026-10-25T12:02:00", "passed")):
            st["jobs"]["id-1"] = {"status": status, "checked": stamp}
            st["attempted"]["vm"] = stamp
            st["checked"] = {"vm": stamp}
            st["errors"]["vm"] = "new error" if st is b else "old error"
            st.update(last_attempt=stamp, last_refresh=stamp)
        vd._write_state(b)
        vd._write_state(a)
        result = vd._read_state()
        self.assertEqual(result["jobs"]["id-1"]["status"], "passed")
        self.assertEqual(result["errors"]["vm"], "new error")

    def test_failed_probe_keeps_freshness_and_records_error_attempt(self):
        self.append(self.dispatch())
        self.state.write_text(json.dumps({
            "jobs": {"id-1": {"status": "running", "checked": "2026-10-25T12:00:00"}},
            "checked": {"vm": "2026-10-25T12:00:00"},
            "attempted": {"vm": "2026-10-25T12:00:00"},
            "last_refresh": "2026-10-25T12:00:00"}))
        with patch.object(vd, "targets", return_value=["vm"]), \
                patch.object(vd, "probe", return_value=({}, "offline")):
            result = vd.refresh("vm", sync=False)
        self.assertEqual(result["errors"], {"vm": "offline"})
        state = vd._read_state()
        self.assertEqual(state["checked"]["vm"], "2026-10-25T12:00:00")
        self.assertEqual(state["jobs"]["id-1"]["checked"], "2026-10-25T12:00:00")
        self.assertEqual(state["last_refresh"], "2026-10-25T12:00:00")
        self.assertGreater(state["attempted"]["vm"], state["checked"]["vm"])

    def test_adoption_never_appends_producer_and_is_target_tag_specific(self):
        row = dict(state="DONE", rc="0", pid=7, lines=1, errors=0,
                   last_line="done", cmd="build", mtime=1792929600)
        self.log.write_text("")
        with patch.object(vd, "targets", return_value=["vm", "lab"]), \
                patch.object(vd, "probe", return_value=({("a", "build"): row,
                                                        ("b", "build"): row}, "")):
            vd.refresh(sync=False)
            vd.refresh(sync=False)
        self.assertEqual(self.log.read_text(), "")
        self.assertEqual(len(self.store.history()), 4)
        self.assertEqual(len(vd._read_state()["jobs"]), 4)

    def test_concurrent_ingestion_and_adoption_is_idempotent(self):
        self.append(self.dispatch())
        with ThreadPoolExecutor(max_workers=8) as workers:
            list(workers.map(lambda _: self.store.history(), range(20)))
            list(workers.map(lambda _: self.store.adopt(self.dispatch("adopted")), range(20)))
        self.assertEqual(len(self.store.history()), 2)

    def test_conflicting_writer_rolls_back_adoption_and_audit(self):
        state = vd._read_state()
        other = vd._read_state()
        state["errors"]["vm"] = "old"
        other["errors"]["vm"] = "new"
        vd._write_state(other)
        count = self.audit_count()
        with self.assertRaisesRegex(ValueError, "concurrent"):
            vd._write_state(state, [self.dispatch("not-committed")])
        self.assertEqual(self.audit_count(), count)
        self.assertEqual(self.store.history(), [])

    def test_machine_failure_preserves_cached_check_and_unknown_fields(self):
        self.vms.write_text(json.dumps({"machines": [{"id": "i-1"}],
                                       "checked": "2026-10-01", "unknown": 9}))
        with patch.object(vd.subprocess, "run", side_effect=OSError("offline")):
            result = vd.machines(force=True)
        self.assertEqual(result["checked"], "2026-10-01")
        self.assertEqual(result["machines"], [{"id": "i-1"}])
        self.assertEqual(result["unknown"], 9)
        self.assertEqual(result["error"], "offline")
        self.assertEqual(result["error_at"], result["last_attempt"])
        self.assertEqual(vd.machines(), result)

    def test_future_version_rejected(self):
        vd._read_state()
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE meta SET value='999' WHERE key='delegation_version'")
        with self.assertRaisesRegex(ValueError, "version"):
            vd._read_state()


if __name__ == "__main__":
    unittest.main()
