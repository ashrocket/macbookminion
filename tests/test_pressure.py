import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/macbook-minion/scripts"))
import pressure


class PressureTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.primary = dict(observed_at=self.now, logical_cpus=8, load1=1,
                            memory_gib=16, memory_pressure="normal", free_gib=100,
                            arch="arm64", session_state=dict(observed_at=self.now,
                            context_used=10, context_limit=100, subscription_remaining_fraction=0.8))
        self.worker = dict(observed_at=self.now, logical_cpus=8, load1=0,
                           memory_gib=16, memory_pressure="normal", free_gib=100,
                           arch="x86_64", id="intel", online=True, active_job="", queued_jobs=0)
        self.task = dict(kind="test", portable=True, primary_only=False,
                         min_memory_gib=2, min_free_gib=1, estimated_seconds=30)

    def route(self, **kwargs):
        return pressure.route(kwargs.get("primary", self.primary), kwargs.get("workers", [self.worker]),
                              kwargs.get("task", self.task), now=self.now)

    def test_light_normal_stays_local(self):
        result = self.route()
        self.assertEqual(result["decision"], "local")
        self.assertIsNone(result["worker_id"])
        self.assertFalse(result["return_hints"]["compact"])

    def test_primary_load_boundary_and_heavy_boundary(self):
        self.primary["load1"] = 5.999
        self.assertEqual(self.route()["decision"], "local")
        self.primary["load1"] = 6
        self.assertEqual(self.route()["decision"], "offload")
        self.primary["load1"] = 1
        self.task["estimated_seconds"] = 60
        self.assertEqual(self.route()["decision"], "local")
        self.task["estimated_seconds"] = 60.001
        self.assertEqual(self.route()["decision"], "offload")

    def test_primary_disk_boundary(self):
        self.primary["free_gib"] = 20
        self.assertEqual(self.route()["decision"], "local")
        self.primary["free_gib"] = 19.999
        self.assertEqual(self.route()["decision"], "offload")

    def test_warning_and_critical_memory_offload(self):
        for level in ("warn", "warning", "critical"):
            with self.subTest(level=level):
                self.primary["memory_pressure"] = level
                self.assertEqual(self.route()["decision"], "offload")
                self.assertEqual(self.route(workers=[])["decision"], "defer")

    def test_token_boundaries_have_explicit_compact_hints(self):
        session = self.primary["session_state"]
        session["context_used"] = 75
        result = self.route()
        self.assertEqual(result["decision"], "offload")
        self.assertTrue(result["return_hints"]["compact"])
        self.assertTrue(result["return_hints"]["deterministic_reduction"])
        self.assertFalse(result["return_hints"]["subscription_capacity_extended"])
        self.assertEqual(result["return_hints"]["model_execution"], "primary")
        session["context_used"] = 74.999
        session["subscription_remaining_fraction"] = 0.2001
        self.assertEqual(self.route()["decision"], "local")
        session["subscription_remaining_fraction"] = 0.2
        self.assertEqual(self.route()["decision"], "offload")

    def test_session_budget_is_never_assumed_low(self):
        for state in (None, {}, [], {"observed_at": 699}, {"observed_at": 1006},
                      dict(observed_at=1000, context_used=True, context_limit=100),
                      dict(observed_at=1000, context_used=1, context_limit=float("nan")),
                      dict(observed_at=1000, context_used=101, context_limit=100),
                      dict(observed_at=1000, context_used=1, context_limit=100,
                           subscription_remaining_fraction=False)):
            with self.subTest(state=state):
                self.primary["session_state"] = state
                result = self.route()
                self.assertTrue(result["return_hints"]["compact"])
                self.assertIsNone(result["pressure"]["session"]["pressure"])
                json.dumps(result, allow_nan=False)

    def test_token_pressure_without_worker_can_run_local_with_reduction(self):
        self.primary["session_state"]["context_used"] = 90
        result = self.route(workers=[])
        self.assertEqual(result["decision"], "local")
        self.assertTrue(result["return_hints"]["compact"])

    def test_unknown_host_fails_local_closed_and_can_offload(self):
        for key in ("load1", "logical_cpus", "free_gib", "memory_gib", "memory_pressure"):
            for value in (None, True, -1, float("nan"), float("inf"), "bad", {}, []):
                with self.subTest(key=key, value=value):
                    host = {**self.primary, key: value}
                    self.assertEqual(self.route(primary=host)["decision"], "offload")
                    result = self.route(primary=host, workers=[])
                    self.assertEqual(result["decision"], "defer")
                    json.dumps(result, allow_nan=False)

    def test_nonportable_or_primary_only_never_offloads(self):
        for modifier in ({"portable": False}, {"primary_only": True}):
            task = {**self.task, **modifier, "estimated_seconds": 1000}
            self.assertEqual(self.route(task=task)["decision"], "local")
            host = {**self.primary, "load1": 8}
            self.assertEqual(self.route(primary=host, task=task)["decision"], "defer")

    def test_worker_staleness_and_future_boundaries(self):
        self.primary["load1"] = 8
        for observed, expected in ((700, "offload"), (699.999, "defer"),
                                   (1005, "offload"), (1005.001, "defer")):
            with self.subTest(observed=observed):
                self.worker["observed_at"] = observed
                self.assertEqual(self.route()["decision"], expected)

    def test_primary_staleness_and_future_are_unsafe(self):
        for observed in (None, True, -1, 699, 1006, float("nan")):
            self.primary["observed_at"] = observed
            self.assertEqual(self.route(workers=[])["decision"], "defer")

    def test_worker_bad_state_cannot_receive_job(self):
        self.primary["load1"] = 8
        cases = [("online", False), ("online", 1), ("active_job", "j-running"),
                 ("active_job", None), ("queued_jobs", 1), ("queued_jobs", True),
                 ("queued_jobs", -1), ("queued_jobs", float("nan")), ("arch", None),
                 ("memory_pressure", "critical"), ("memory_pressure", "warn"), ("memory_pressure", "warning"),
                 ("logical_cpus", 0), ("logical_cpus", 8.0), ("load1", -1),
                 ("free_gib", float("inf")), ("memory_gib", True), ("id", "")]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                self.assertEqual(self.route(workers=[{**self.worker, key: value}])["decision"], "defer")

    def test_reserves_and_saturation_boundaries(self):
        self.primary["load1"] = 8
        self.worker.update(memory_gib=4, free_gib=30, load1=7.199)
        self.assertEqual(self.route()["decision"], "offload")
        for patch_values in ({"memory_gib": 3.999}, {"free_gib": 29.999}, {"load1": 7.2}):
            self.assertEqual(self.route(workers=[{**self.worker, **patch_values}])["decision"], "defer")

    def test_configured_worker_disk_floor_and_task_minimum(self):
        self.primary["load1"] = 8
        self.worker.update(min_free_gib=40, free_gib=45)
        self.assertEqual(self.route()["decision"], "offload")
        self.worker["free_gib"] = 44.999
        self.assertEqual(self.route()["decision"], "defer")
        self.worker["free_gib"] = 100
        self.task["min_free_gib"] = 101
        self.assertEqual(self.route()["decision"], "defer")
        for floor in (None, True, -1, float("nan")):
            self.worker["min_free_gib"] = floor
            self.assertEqual(self.route()["decision"], "defer")

    def test_required_arch_checks_primary_and_worker(self):
        self.task["required_arch"] = "x86_64"
        self.assertEqual(self.route()["decision"], "offload")
        self.task["required_arch"] = "arm64"
        self.task["estimated_seconds"] = 90
        self.assertEqual(self.route()["decision"], "local")
        self.task["required_arch"] = "unsupported"
        self.assertEqual(self.route()["decision"], "defer")

    def test_selection_is_stable_and_ranks_idle_capacity(self):
        self.task["estimated_seconds"] = 90
        a = {**self.worker, "id": "a", "logical_cpus": 10}
        b = {**self.worker, "id": "b", "logical_cpus": 10}
        for workers in ([self.worker, b, a], [a, b, self.worker]):
            self.assertEqual(self.route(workers=workers)["worker_id"], "a")
        a["active_job"] = "busy"
        self.assertEqual(self.route(workers=[a, b])["worker_id"], "b")
        a["active_job"] = ""
        a["load1"] = 2
        self.assertEqual(self.route(workers=[a, b])["worker_id"], "b")

    def test_duplicate_worker_ids_are_rejected(self):
        self.primary["load1"] = 8
        result = self.route(workers=[self.worker, self.worker.copy()])
        self.assertEqual(result["decision"], "defer")
        self.assertTrue(all(not w["eligible"] for w in result["pressure"]["workers"]))

    def test_malformed_task_and_top_level_schema_defer(self):
        tasks = [None, [], {}, {**self.task, "kind": "shell"}, {**self.task, "portable": 1},
                 {**self.task, "primary_only": "false"}, {**self.task, "required_arch": []}]
        for key in ("min_memory_gib", "min_free_gib", "estimated_seconds"):
            tasks.extend({**self.task, key: value} for value in (-1, True, float("nan"), float("inf"), "2"))
        for task in tasks:
            with self.subTest(task=task):
                self.assertEqual(self.route(task=task)["decision"], "defer")
        self.assertEqual(self.route(workers={})["decision"], "defer")
        self.assertEqual(self.route(primary=[])["decision"], "defer")
        self.assertEqual(pressure.route(self.primary, [], self.task, now=True)["decision"], "defer")

    def test_route_does_not_mutate_observations(self):
        original = copy.deepcopy((self.primary, self.worker, self.task))
        self.route()
        self.assertEqual((self.primary, self.worker, self.task), original)

    @patch.object(pressure.platform, "system", return_value="Darwin")
    @patch.object(pressure, "_sysctl")
    @patch.object(pressure.shutil, "disk_usage")
    @patch.object(pressure.os, "getloadavg", return_value=(2, 1, 1))
    @patch.object(pressure.os, "cpu_count", return_value=8)
    def test_sampler_reads_mac_metrics_and_preserves_session_age(self, cpu, load, disk, sysctl, system):
        disk.return_value.free = 40 * 1024 ** 3
        sysctl.side_effect = [str(16 * 1024 ** 3), "2"]
        session = dict(observed_at=100, context_used=5, context_limit=10)
        result = pressure.sample_primary(session, now=1000)
        self.assertEqual(result["memory_gib"], 16)
        self.assertEqual(result["memory_pressure"], "warn")
        self.assertEqual(result["free_gib"], 40)
        self.assertEqual(result["session_state"]["observed_at"], 100)
        self.assertEqual(result["sampling_errors"], [])
        self.assertEqual([c.args[0] for c in sysctl.call_args_list],
                         ["hw.memsize", "kern.memorystatus_vm_pressure_level"])

    @patch.object(pressure.platform, "system", return_value="Darwin")
    @patch.object(pressure, "_sysctl", return_value=None)
    @patch.object(pressure.shutil, "disk_usage", side_effect=OSError)
    @patch.object(pressure.os, "getloadavg", side_effect=OSError)
    @patch.object(pressure.os, "cpu_count", return_value=None)
    def test_sampler_missing_metrics_are_explicit_unknown(self, *mocks):
        result = pressure.sample_primary(now=1000)
        self.assertEqual(len(result["sampling_errors"]), 5)
        self.assertEqual(self.route(primary=result, workers=[])["decision"], "defer")

    def test_sysctl_failure_and_timeout_are_unknown(self):
        with patch.object(pressure.subprocess, "run", side_effect=OSError):
            self.assertIsNone(pressure._sysctl("hw.memsize"))
        with patch.object(pressure.subprocess, "run", side_effect=pressure.subprocess.TimeoutExpired("sysctl", 2)):
            self.assertIsNone(pressure._sysctl("hw.memsize"))


if __name__ == "__main__":
    unittest.main()
