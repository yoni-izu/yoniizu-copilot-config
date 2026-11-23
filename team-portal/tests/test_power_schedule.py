import copy
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from power_schedule import PowerSchedule
import portal_settings


class PowerScheduleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "power-schedule.json"
        self.now = 1000
        self.cfg = copy.deepcopy(portal_settings.DEFAULTS["machines"])
        self.observed = {"age": 900, "machines": [
            {"id": "i-one", "state": "running", "stop_ts": 1100}]}
        self.operation_lock = threading.Lock()
        self.changed = Mock()
        self.probe = Mock(side_effect=self.confirm_stopped)
        self.schedule = self.make_schedule()

    def make_schedule(self):
        return PowerSchedule(self.path, lambda: copy.deepcopy(self.observed),
                             self.probe, lambda: self.cfg, self.operation_lock,
                             self.changed, clock=lambda: self.now)

    def confirm_stopped(self):
        self.observed = {"age": self.now, "machines": [
            {**m, "state": "stopped", "stop_ts": None} for m in self.observed["machines"]]}
        return {"machines": self.observed["machines"]}

    def test_waits_until_deadline_and_coalesces_power_checks(self):
        self.observed["machines"].append({"id": "i-two", "state": "running", "stop_ts": 1100})
        self.assertEqual(self.schedule.step(), 60)
        self.now = 1129
        self.assertEqual(self.schedule.step(), 1)
        self.probe.assert_not_called()
        self.now = 1130
        self.schedule.step()
        self.probe.assert_called_once()
        self.assertEqual(self.schedule.snapshot()["instances"], {})

    def test_renewal_and_removal_cancel_old_deadline_without_querying(self):
        self.schedule.step()
        self.observed["machines"][0]["stop_ts"] = 1500
        self.now = 1130
        self.schedule.step()
        self.probe.assert_not_called()
        self.assertEqual(self.schedule.snapshot()["instances"]["i-one"]["due"], 1530)
        self.observed["machines"] = []
        self.schedule.step()
        self.assertEqual(self.schedule.snapshot()["instances"], {})
        self.probe.assert_not_called()

    def test_contention_does_not_consume_attempts_and_rechecks_renewal(self):
        self.schedule.step()
        self.now = 1130
        self.operation_lock.acquire()
        self.assertEqual(self.schedule.step(), 30)
        self.assertEqual(self.schedule.snapshot()["instances"]["i-one"]["attempts"], 0)
        self.operation_lock.release()
        self.observed["machines"][0]["stop_ts"] = 1500
        self.schedule.step()
        self.probe.assert_not_called()

    def test_failure_retries_are_bounded_and_survive_restart(self):
        self.cfg["power_retry_limit"] = 1
        self.probe.side_effect = None
        self.probe.return_value = {"error": "SSO unavailable"}
        self.now = 1130
        self.schedule.step()
        self.assertIn("SSO unavailable", self.schedule.snapshot()["instances"]["i-one"]["error"])
        self.schedule = self.make_schedule()
        self.now = 1159
        self.schedule.step()
        self.assertEqual(self.probe.call_count, 1)
        self.now = 1160
        self.schedule.step()
        self.assertIn("exhausted", self.schedule.snapshot()["instances"]["i-one"]["error"])
        self.schedule = self.make_schedule()
        self.now = 2000
        self.schedule.step()
        self.assertEqual(self.probe.call_count, 2)
        self.observed["age"] = 2000  # Successful explicit Check VM status.
        self.schedule.step()
        self.assertEqual(self.probe.call_count, 3)

    def test_delayed_stopping_keeps_budget_across_updated_observations(self):
        self.cfg["power_retry_limit"] = 1

        def stopping():
            self.observed["age"] = self.now
            self.observed["machines"][0].update(state="stopping", stop_ts=None)
            return {"machines": self.observed["machines"]}

        self.probe.side_effect = stopping
        self.now = 1130
        self.schedule.step()
        self.now = 1160
        self.schedule.step()
        row = self.schedule.snapshot()["instances"]["i-one"]
        self.assertEqual(row["attempts"], 2)
        self.assertIn("exhausted", row["error"])
        self.now = 1190
        self.schedule.step()
        self.assertEqual(self.probe.call_count, 2)

    def test_pending_transition_settles_to_running_without_waiting_for_lease_end(self):
        self.observed["machines"][0]["state"] = "pending"

        def running():
            self.observed["age"] = self.now
            self.observed["machines"][0]["state"] = "running"
            return {"machines": self.observed["machines"]}

        self.probe.side_effect = running
        self.schedule.step()
        self.now = 1030
        self.schedule.step()
        self.probe.assert_called_once()
        row = self.schedule.snapshot()["instances"]["i-one"]
        self.assertEqual(row["due"], 1130)
        self.assertEqual(row["attempts"], 0)

    def test_settings_disable_and_reconfigure_without_extra_queries(self):
        self.schedule.step()
        self.cfg["power_checks"] = False
        self.now = 1130
        self.schedule.step()
        self.probe.assert_not_called()
        self.cfg.update(power_checks=True, power_grace_s=60)
        self.schedule.step()
        self.probe.assert_not_called()
        self.now = 1160
        self.schedule.step()
        self.probe.assert_called_once()

    def test_missing_deadlines_do_not_create_queries(self):
        self.observed["machines"] = [
            {"id": "i-one", "state": "running", "stop_ts": None},
            {"id": "i-two", "state": "running", "stop_ts": float("nan")},
            {"id": "i-three", "state": "stopped", "stop_ts": 800}]
        self.schedule.step()
        self.assertEqual(self.schedule.snapshot()["instances"], {})
        self.probe.assert_not_called()

    def test_changed_retry_spacing_applies_to_existing_attempt(self):
        self.probe.side_effect = None
        self.probe.return_value = {"error": "offline"}
        self.now = 1130
        self.schedule.step()
        self.cfg["power_retry_s"] = 10
        self.now = 1139
        self.assertEqual(self.schedule.step(), 1)
        self.assertEqual(self.probe.call_count, 1)
        self.now = 1140
        self.schedule.step()
        self.assertEqual(self.probe.call_count, 2)

    def test_exhausted_sequence_is_rearmed_by_new_lease_not_local_sweeps(self):
        self.cfg["power_retry_limit"] = 0
        self.probe.side_effect = None
        self.probe.return_value = {"error": "offline"}
        self.now = 1130
        self.schedule.step()
        self.now = 1200
        self.schedule.step()
        self.assertEqual(self.probe.call_count, 1)
        self.observed["machines"][0]["stop_ts"] = 1300
        self.schedule.step()
        self.assertEqual(self.schedule.snapshot()["instances"]["i-one"]["attempts"], 0)
        self.now = 1330
        self.schedule.step()
        self.assertEqual(self.probe.call_count, 2)

    def test_external_exception_is_visible_and_durable(self):
        self.probe.side_effect = RuntimeError("offline")
        self.now = 1130
        with patch("sys.stderr"):
            self.schedule.step()
        self.assertIn("offline", self.make_schedule().snapshot()["instances"]["i-one"]["error"])

    def test_no_query_if_attempt_cannot_be_persisted(self):
        self.now = 1130
        with patch.object(self.schedule, "_save", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.schedule.step()
        self.probe.assert_not_called()

    def test_rejects_corrupt_bookkeeping_instead_of_replaying(self):
        self.path.write_text('{"schema":1,"rows":{"i-one":{}}}')
        with self.assertRaisesRegex(ValueError, "Invalid power scheduler"):
            self.make_schedule()


class PortalPowerIntegrationTests(unittest.TestCase):
    def test_owner_observation_adapter_uses_cached_power_only(self):
        import portal
        with patch.object(portal.vd, "machines", return_value={
                "age": 1000, "machines": [{"id": "i-one", "state": "running"}]}) as cached, \
                patch.object(portal.vd, "power_view", return_value={"stop_ts": 1100}) as power:
            payload = portal.power_observations()
        cached.assert_called_once_with()
        power.assert_called_once()
        self.assertEqual(payload["machines"][0]["stop_ts"], 1100)

    def test_payload_surfaces_scheduler_failures_without_machine_changes(self):
        import portal
        scheduler = Mock()
        scheduler.snapshot.return_value = {"error": "disk full", "instances": {}}
        with patch.object(portal, "POWER_SCHEDULE", scheduler), \
                patch.object(portal.vd, "payload", return_value={"machines": []}), \
                patch.object(portal.vd, "targets", return_value=[]), \
                patch.object(portal, "live_payload", return_value={"links": {}}):
            payload = portal.vmlog_payload()
        self.assertEqual(payload["power_schedule"]["error"], "disk full")
        scheduler.wake.set.assert_called_once()
