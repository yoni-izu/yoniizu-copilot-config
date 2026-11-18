import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portal  # noqa: E402

PR = "https://github.com/Elmnt-Internal/ip.mng.sw.fw/pull/42"
JOB = "https://github.com/Elmnt-Internal/ip.mng.sw.fw/actions/runs/111/job/222"


class DevopsTestCase(unittest.TestCase):
    def setUp(self):
        portal._DEVOPS_CACHE.clear()

    def tearDown(self):
        portal._DEVOPS_CACHE.clear()


class PrStageTest(DevopsTestCase):
    def stage(self, rows, rc=0):
        with mock.patch.object(portal, "_octl", return_value=(rc, json.dumps(rows), "")) as m:
            return portal.pr_stage(PR), m

    def test_not_in_version(self):
        self.assertEqual(self.stage([])[0]["stage"], "not-in-version")

    def test_pending_passed_failed(self):
        self.assertEqual(self.stage([{"Version": "1", "IntegrationStatus": "Running"}])[0]["stage"],
                         "integration-pending")
        portal._DEVOPS_CACHE.clear()
        self.assertEqual(self.stage([{"Version": "1", "IntegrationStatus": "Failed"},
                                     {"Version": "2", "IntegrationStatus": "Passed"}])[0]["stage"],
                         "integration-passed")
        portal._DEVOPS_CACHE.clear()
        self.assertEqual(self.stage([{"version": "1", "integration_status": "Failed"}])[0]["stage"],
                         "integration-failed")

    def test_in_cd_overrides_and_dict_form(self):
        res, octl = self.stage({"versions": [{"version": "3", "integration_status": "Passed",
                                              "cd_manifests": ["prod"]}]})
        self.assertTrue(res["ok"])
        self.assertEqual(res["stage"], "in-cd")
        self.assertEqual(res["versions"][0]["cd_manifests"], ["prod"])
        args = octl.call_args[0][0]
        self.assertEqual(args[:4], ["artifacts", "versions", "--pr", PR])
        self.assertIn("json", args)

    def test_errors(self):
        with mock.patch.object(portal, "_octl", return_value=(2, "", "boom")):
            res = portal.pr_stage(PR)
        self.assertFalse(res["ok"])
        self.assertIn("boom", res["error"])
        portal._DEVOPS_CACHE.clear()
        with mock.patch.object(portal, "_octl", return_value=(0, "not json", "")):
            self.assertEqual(portal.pr_stage(PR)["error"], "unparseable octl output")

    def test_bad_url(self):
        for bad in ("", "https://github.com/o/r/issues/1", PR + "/files", "x; rm -rf /"):
            with self.assertRaises(ValueError):
                portal.pr_stage(bad)

    def test_cached(self):
        with mock.patch.object(portal, "_octl", return_value=(0, "[]", "")) as m:
            portal.pr_stage(PR)
            portal.pr_stage(PR)
        self.assertEqual(m.call_count, 1)


class SherlockTest(DevopsTestCase):
    def test_json(self):
        out = json.dumps({"category": "infra", "owner": "platform", "nested": {"x": 1}})
        with mock.patch.object(portal, "_octl", return_value=(0, out, "")) as m:
            res = portal.sherlock(JOB)
        self.assertTrue(res["ok"])
        self.assertEqual(res["classification"], {"category": "infra", "owner": "platform"})
        self.assertEqual(m.call_args[0][0], ["workflow", "sherlock", JOB])
        self.assertIn("var-job_link=", res["links"]["job_lookup"])
        self.assertIn("actions%2Fruns%2F111%2Fattempts%2F1", res["links"]["runner_details"])

    def test_text(self):
        out = "Category: Runner lost\nCategory: dup\nReason:   OOM killed\nnoise line"
        with mock.patch.object(portal, "_octl", return_value=(0, out, "")):
            res = portal.sherlock(JOB)
        self.assertEqual(res["classification"], {"category": "Runner lost", "reason": "OOM killed"})

    def test_failure_and_cache(self):
        self.assertIsNone(portal.sherlock_cached(JOB))
        with mock.patch.object(portal, "_octl", return_value=(1, "", "denied")) as m:
            res = portal.sherlock(JOB)
            portal.sherlock(JOB)
        self.assertEqual(m.call_count, 1)
        self.assertFalse(res["ok"])
        self.assertIn("denied", res["error"])
        self.assertIs(portal.sherlock_cached(JOB), res)

    def test_bad_url(self):
        with self.assertRaises(ValueError):
            portal.sherlock("https://github.com/o/r/actions/runs/1")


class GrafanaHealthTest(DevopsTestCase):
    def _resp(self, body):
        r = mock.MagicMock()
        r.__enter__.return_value.read.return_value = body
        return r

    def test_ok_and_cache(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=self._resp(b'{"database":"ok","version":"11.2"}')) as m:
            res = portal.grafana_health()
            portal.grafana_health()
        self.assertEqual(m.call_count, 1)
        self.assertTrue(res["ok"])
        self.assertEqual(res["version"], "11.2")

    def test_down(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("refused")):
            res = portal.grafana_health()
        self.assertFalse(res["ok"])
        self.assertIn("refused", res["error"])


class DevopsAiSectionTest(unittest.TestCase):
    def test_section(self):
        self.assertIn("devops", portal.AI_SECTIONS)
        data = portal.ai_section_data("devops", False)
        self.assertEqual(data["octl_manifest"], portal.OCTL_MANIFEST)
        self.assertIn("ci_build_overview", data["grafana"])
        self.assertTrue(data["grafana"]["ec2_cost"]["url"].startswith(portal.GRAFANA_URL))

    def test_links(self):
        for item in portal.grafana_links().values():
            self.assertTrue(item["url"].startswith("https://"))


if __name__ == "__main__":
    unittest.main()
