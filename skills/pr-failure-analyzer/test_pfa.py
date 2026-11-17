import json
import tempfile
import unittest
from pathlib import Path

import pfa


class PfaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "pf.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_fingerprint_masks_volatile_tokens(self):
        a = pfa.fingerprint("error at /w/a/b.c:12 sha 1a2b3c4d5e 0xdead https://x/run/1")
        b = pfa.fingerprint("ERROR at /other/x.c:99 sha ffffeeee11 0xbeef https://y/run/7")
        self.assertEqual(a, b)
        self.assertNotEqual(a, pfa.fingerprint("link error: undefined symbol"))
        self.assertEqual(pfa.fingerprint("  "), "")

    def test_record_match_recent(self):
        pfa.record(repo="r1", pr=3, verdict="flake", signature="timeout 30s", path=self.db)
        pfa.record(repo="r2", verdict="fw-code", signature="undefined FOO", path=self.db)
        self.assertEqual([r["repo"] for r in pfa.match("timeout 45s", path=self.db)], ["r1"])
        self.assertEqual(len(pfa.recent(["r2"], path=self.db)), 1)
        self.assertEqual(len(pfa.recent(path=self.db)), 2)
        with self.assertRaises(ValueError):
            pfa.record(repo="r", verdict="bogus", path=self.db)
        with self.assertRaises(ValueError):
            pfa.record(repo="", verdict="flake", path=self.db)

    def test_parse_verdict(self):
        line = "PORTAL-TRIAGE: " + json.dumps({
            "verdict": "Runner-Env", "summary": "runner lost", "owner": "infra",
            "items": [{"repo": "r", "pr": 4, "check": "build", "signature": "lost",
                       "verdict": "nonsense"}, "bad"]})
        out = pfa.parse_verdict("## Verdict\ntext\n" + line + "\n")
        self.assertEqual((out["verdict"], out["owner"]), ("runner-env", "infra"))
        self.assertEqual(out["items"][0]["verdict"], "runner-env")
        self.assertEqual(len(out["items"]), 1)
        self.assertIsNone(pfa.parse_verdict("no machine line"))
        self.assertIsNone(pfa.parse_verdict("PORTAL-TRIAGE: {not json"))
        self.assertEqual(pfa.parse_verdict('PORTAL-TRIAGE: {"verdict":"x"}')["verdict"],
                         "unknown")

    def test_lesson_appends(self):
        md = Path(self.tmp.name) / "p.md"
        md.write_text("## Lessons\n")
        pfa.add_lesson("Runner OOM", "runner-env", "killed", "small runner", "re-run", path=md)
        self.assertIn("### Runner OOM", md.read_text())
        with self.assertRaises(ValueError):
            pfa.add_lesson("", "flake", "", "", "", path=md)


if __name__ == "__main__":
    unittest.main()
