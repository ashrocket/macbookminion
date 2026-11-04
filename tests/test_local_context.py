import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/macbook-minion/scripts"))
import audit
import control
import fleet
import minion


class LocalContextTests(unittest.TestCase):
    def test_stop_reconciles_actual_coordinator_even_when_launchagent_exists(self):
        import launch_agent
        for managed in (False, True):
            with self.subTest(managed=managed), tempfile.TemporaryDirectory() as temp:
                config = {"host": "test-worker", "_state_dir": temp}
                with patch.object(launch_agent, "stop_if_installed", return_value=managed), patch.object(control, "service_info", return_value={"pid": 43210, "instance": "foreground-owner"}) as info, patch.object(control.os, "kill") as kill:
                    result = control.stop_service(config)
                info.assert_called_once_with(config)
                kill.assert_called_once_with(43210, control.signal.SIGTERM)
                self.assertEqual(result["state"], "stop_requested")

    def test_stop_cannot_claim_success_while_unknown_coordinator_holds_lock(self):
        import fcntl
        import launch_agent
        with tempfile.TemporaryDirectory() as temp:
            config = {"host": "test-worker", "_state_dir": temp}
            with (Path(temp) / "service.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(launch_agent, "stop_if_installed", return_value=True), patch.object(control, "service_info", return_value=None):
                    with self.assertRaisesRegex(OSError, "ownership"):
                        control.stop_service(config)

    def test_doctor_reports_missing_local_user_and_fails(self):
        import pwd
        output = io.StringIO()
        with patch.object(pwd, "getpwuid", side_effect=KeyError("private")), contextlib.redirect_stdout(output):
            code = minion.main(["doctor"])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(report["local_context"]["error_code"], "local_context_unavailable")
        self.assertFalse(report["live_connection_tested"])
        self.assertNotIn("private", output.getvalue())

    def test_ssh_uid_failure_is_classified_locally_without_raw_output(self):
        import local_context
        failure = subprocess.CompletedProcess(["ssh"], 255, "", "No user exists for uid 501\n")
        with patch.object(minion.subprocess, "run", return_value=failure), patch.object(local_context, "identity_available", return_value=False):
            with self.assertRaises(local_context.LocalContextError):
                minion.execute(["/usr/bin/ssh", "-G", "localhost"])

    def test_remote_text_cannot_impersonate_local_context_failure(self):
        import local_context
        failure = subprocess.CompletedProcess(["ssh"], 255, "", "No user exists for uid 501\n")
        with patch.object(minion.subprocess, "run", return_value=failure), patch.object(local_context, "identity_available", return_value=True):
            with self.assertRaises(minion.MinionError):
                minion.execute(["ssh", "host"])

    def test_broken_context_does_not_spawn_coordinator(self):
        import local_context
        config = {"host": "test-worker"}
        with patch.object(control, "service_info", return_value=None), patch.object(local_context, "require_healthy", side_effect=local_context.LocalContextError()), patch.object(control.subprocess, "Popen") as spawn:
            with self.assertRaises(local_context.LocalContextError):
                control.ensure_service(config)
        spawn.assert_not_called()

    def test_local_failure_keeps_worker_reachability_unknown_and_queue_saved(self):
        import local_context
        with tempfile.TemporaryDirectory() as temp:
            config = {"host": "test-worker", "min_free_gib": 25, "_state_dir": temp}
            transport = Mock()
            transport.remote.side_effect = local_context.LocalContextError()
            coordinator = control.Coordinator(config, transport)
            with patch.object(coordinator.store, "claim") as claim:
                self.assertFalse(coordinator.tick())
            claim.assert_not_called()
            worker = coordinator.store.state("worker")
            self.assertIsNone(worker["online"])
            self.assertEqual(worker["error_code"], "local_context_unavailable")
            self.assertEqual(coordinator.store.events()[-1]["kind"], "controller.context_unavailable")
            observation = fleet.observe(config, transport)
            self.assertIsNone(observation["online"])
            self.assertEqual(observation["error_code"], "local_context_unavailable")

    def test_early_coordinator_exit_is_reported_without_waiting_or_log_leak(self):
        import local_context
        with tempfile.TemporaryDirectory() as temp:
            config = {"host": "test-worker", "_config_path": str(Path(temp) / "config.json"), "_state_dir": temp}
            child = Mock()
            child.poll.return_value = 7
            child.returncode = 7
            with patch.object(local_context, "require_healthy"), patch.object(control, "service_info", return_value=None), patch.object(control.subprocess, "Popen", return_value=child), patch.object(control.time, "sleep") as sleep:
                with self.assertRaisesRegex(OSError, "exited.*7"):
                    control.ensure_service(config)
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
