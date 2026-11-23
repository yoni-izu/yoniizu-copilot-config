import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import domain_watch  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now


class DomainWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clock = Clock()
        self.published = []
        self.watcher = domain_watch.Watcher(
            self.root, self.published.append, quiet=0.75, clock=self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def test_baseline_and_batched_database_and_wal_changes(self):
        db = self.root / "tasks.db"
        wal = self.root / "tasks.db-wal"
        db.write_bytes(b"one")
        wal.write_bytes(b"wal")
        self.watcher.tick()
        self.assertEqual(self.published, [])

        os.utime(db, ns=(100, 100))
        os.utime(wal, ns=(200, 200))
        self.clock.now += 0.5
        self.watcher.tick()
        self.assertEqual(self.published, [])

        self.clock.now += 0.8
        self.watcher.tick()
        self.assertEqual(self.published, [["tasks"]])

    def test_changes_across_domains_are_coalesced(self):
        (self.root / "prs.db").write_bytes(b"")
        self.watcher.tick()
        os.utime(self.root / "prs.db", ns=(100, 100))
        (self.root / "team.db").write_bytes(b"")
        self.watcher.tick()
        self.clock.now += 0.8
        self.watcher.tick()
        self.assertEqual(self.published, [["prs", "team"]])

    def test_untracked_database_is_ignored(self):
        self.watcher.tick()
        (self.root / "pldm.db").write_bytes(b"private to another surface")
        self.clock.now += 1
        self.watcher.tick()
        self.assertEqual(self.published, [])

    def test_configure_disables_filters_and_rebaselines(self):
        for name in ("tasks.db", "prs.db"):
            (self.root / name).write_bytes(b"")
        self.watcher.tick()
        self.watcher.configure(domains=["prs"])
        os.utime(self.root / "tasks.db", ns=(100, 100))
        os.utime(self.root / "prs.db", ns=(100, 100))
        self.watcher.tick()
        self.clock.now += 1
        self.watcher.tick()
        self.assertEqual(self.published, [["prs"]])

        self.watcher.configure(enabled=False)
        os.utime(self.root / "prs.db", ns=(200, 200))
        self.clock.now += 1
        self.watcher.tick()
        self.assertEqual(self.published, [["prs"]])

        # Re-enabling starts from a fresh baseline: changes made while off are not replayed.
        self.watcher.configure(enabled=True, interval=5, quiet=0)
        self.watcher.tick()
        self.assertEqual((self.watcher.interval, self.published), (5, [["prs"]]))
        os.utime(self.root / "tasks.db", ns=(300, 300))
        self.watcher.tick()
        self.assertEqual(self.published, [["prs"], ["tasks"]])


if __name__ == "__main__":
    unittest.main()
