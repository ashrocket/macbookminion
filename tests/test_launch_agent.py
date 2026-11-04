import fcntl
import importlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/macbook-minion/scripts'))


class LaunchAgentTests(unittest.TestCase):
    def setUp(self):
        self.agent = importlib.import_module('launch_agent')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.config = {'host': 'paired-worker', '_worker_id': 'default',
                       '_config_path': str(self.home / 'config.json'),
                       '_state_dir': str(self.home / 'state')}
        self.config_file = Path(self.config['_config_path'])
        self.config_file.write_text('{}')
        Path(self.config['_state_dir']).mkdir(mode=0o700)
        self.loaded = False
        self.calls = []
        self.addCleanup(patch.stopall)
        patch.object(self.agent.Path, 'home', return_value=self.home).start()
        patch.object(self.agent.os, 'getuid', return_value=501).start()
        self.preflight = patch.object(self.agent.local_context, 'require_healthy').start()
        self.run = patch.object(self.agent.subprocess, 'run', side_effect=self.launchctl).start()

    def launchctl(self, args, **kwargs):
        self.calls.append(args)
        if args[1] == 'print':
            return subprocess.CompletedProcess(args, 0 if self.loaded else 113,
                stdout='\tpath = ' + str(self.agent.agent_path(self.config)) + '\n', stderr='private stderr')
        if args[1] == 'bootstrap':
            self.loaded = True
        if args[1] == 'bootout':
            self.loaded = False
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')

    def test_install_pins_configuration_and_safe_lifecycle(self):
        result = self.agent.install(self.config)
        path = self.agent.agent_path(self.config)
        self.assertEqual(result['state'], 'installed')
        data = plistlib.loads(path.read_bytes())
        self.assertEqual(data['ProgramArguments'], self.agent.control.service_command(self.config))
        self.assertTrue(data['RunAtLoad'])
        self.assertEqual(data['KeepAlive'], {'SuccessfulExit': False})
        self.assertEqual(data['ThrottleInterval'], 30)
        self.assertEqual(data['LimitLoadToSessionType'], 'Aqua')
        self.assertNotIn('EnvironmentVariables', data)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.preflight.called)
        self.assertEqual([a[1] for a in self.calls], ['print', 'bootstrap', 'kickstart'])
        self.assertNotIn('-k', sum(self.calls, []))
        self.assertEqual(self.calls[1][2], 'gui/501')
        for call in self.run.call_args_list:
            self.assertEqual(call.kwargs['timeout'], 10)
            self.assertNotIn('shell', call.kwargs)

    def test_start_after_stop_bootstraps_again_and_preserves_files(self):
        self.agent.install(self.config)
        saved = self.agent.agent_path(self.config).read_bytes()
        self.assertTrue(self.agent.stop_if_installed(self.config))
        self.assertFalse(self.loaded)
        self.assertEqual(self.agent.agent_path(self.config).read_bytes(), saved)
        self.assertEqual(self.config_file.read_text(), '{}')
        self.calls.clear()
        self.assertTrue(self.agent.start_if_installed(self.config))
        self.assertEqual([a[1] for a in self.calls], ['print', 'bootstrap', 'kickstart'])

    def test_absent_install_is_opt_in(self):
        self.assertFalse(self.agent.start_if_installed(self.config))
        self.assertFalse(self.agent.stop_if_installed(self.config))
        self.assertEqual(self.calls, [])
        self.assertFalse((self.home / 'Library').exists())

    def test_unhealthy_context_prevents_writes_and_launchctl(self):
        self.preflight.side_effect = OSError('unhealthy context')
        with self.assertRaises(OSError):
            self.agent.install(self.config)
        self.assertFalse((self.home / 'Library').exists())
        self.assertEqual(self.calls, [])

    def test_unhealthy_context_prevents_installed_start_and_stop(self):
        self.agent.install(self.config)
        self.calls.clear()
        self.preflight.side_effect = OSError('unhealthy context')
        for method in (self.agent.start_if_installed, self.agent.stop_if_installed):
            with self.assertRaises(OSError):
                method(self.config)
        self.assertEqual(self.calls, [])

    def test_drifted_plist_is_never_replaced_or_stopped(self):
        self.agent.install(self.config)
        path = self.agent.agent_path(self.config)
        path.write_bytes(b'unrelated data')
        self.calls.clear()
        for method in (self.agent.install, self.agent.start_if_installed, self.agent.stop_if_installed):
            with self.assertRaisesRegex(OSError, 'match'):
                method(self.config)
        self.assertEqual(path.read_bytes(), b'unrelated data')
        self.assertEqual(self.calls, [])

    def test_label_is_stable_per_host_and_config(self):
        path = self.agent.agent_path(self.config)
        self.assertEqual(path, self.agent.agent_path(dict(self.config)))
        self.assertNotEqual(path, self.agent.agent_path({**self.config, 'host': 'another-worker'}))
        self.assertNotEqual(path, self.agent.agent_path({**self.config, '_config_path': '/another/config.json'}))

    def test_live_legacy_coordinator_prevents_install(self):
        with (Path(self.config['_state_dir']) / 'service.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(OSError, 'coordinator'):
                self.agent.install(self.config)
        self.assertFalse(self.agent.agent_path(self.config).exists())
        self.assertFalse(any(args[1] in ('bootstrap', 'kickstart') for args in self.calls))

    def test_loaded_service_from_other_path_is_not_mutated(self):
        self.agent.install(self.config)
        self.calls.clear()
        self.run.side_effect = None
        self.run.return_value = subprocess.CompletedProcess([], 0, stdout='path = /other.plist\n', stderr='')
        with self.assertRaisesRegex(OSError, 'match'):
            self.agent.stop_if_installed(self.config)
        self.assertEqual(self.run.call_args.args[0][1], 'print')

    def test_launchctl_failure_is_sanitized(self):
        self.run.side_effect = None
        self.run.return_value = subprocess.CompletedProcess([], 5, stdout='secret output', stderr='secret stderr')
        with self.assertRaises(OSError) as raised:
            self.agent.install(self.config)
        self.assertNotIn('secret', str(raised.exception))
        self.assertLess(len(str(raised.exception)), 300)

    def test_live_legacy_coordinator_prevents_start_when_agent_unloaded(self):
        self.agent.install(self.config)
        self.agent.stop_if_installed(self.config)
        self.calls.clear()
        with (Path(self.config['_state_dir']) / 'service.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(OSError, 'coordinator'):
                self.agent.start_if_installed(self.config)
        self.assertEqual([args[1] for args in self.calls], ['print'])

    def test_repeated_install_does_not_overwrite_or_bootstrap_loaded_service(self):
        self.agent.install(self.config)
        path = self.agent.agent_path(self.config)
        before = path.stat().st_mtime_ns
        self.calls.clear()
        self.agent.install(self.config)
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertEqual([args[1] for args in self.calls], ['print', 'kickstart'])

    def test_timeout_is_sanitized(self):
        self.run.side_effect = subprocess.TimeoutExpired('secret command', 10, stderr='secret stderr')
        with self.assertRaises(OSError) as raised:
            self.agent.install(self.config)
        self.assertNotIn('secret', str(raised.exception))
        self.assertFalse(self.agent.agent_path(self.config).exists())

    def test_installed_interpreter_survives_cli_python_changes(self):
        self.agent.install(self.config)
        data = self.agent.agent_path(self.config).read_bytes()
        with patch.object(self.agent.control.sys, 'executable', '/different/python3'):
            self.assertTrue(self.agent.start_if_installed(self.config))
            self.assertTrue(self.agent.stop_if_installed(self.config))
            self.agent.install(self.config)
        self.assertEqual(self.agent.agent_path(self.config).read_bytes(), data)

    def test_safe_working_directory_and_umask(self):
        self.agent.install(self.config)
        data = plistlib.loads(self.agent.agent_path(self.config).read_bytes())
        self.assertEqual(data['WorkingDirectory'], self.config['_state_dir'])
        self.assertEqual(data['Umask'], 0o077)
        self.assertEqual(data['StandardOutPath'], '/dev/null')
        self.assertEqual(data['StandardErrorPath'], '/dev/null')

    def test_corrupt_identity_metadata_refuses_lifecycle(self):
        self.agent.install(self.config)
        self.agent._metadata_path(self.config).write_text('{}')
        self.calls.clear()
        with self.assertRaisesRegex(OSError, 'metadata'):
            self.agent.stop_if_installed(self.config)
        self.assertEqual(self.calls, [])

    def test_concurrent_lifecycle_is_refused(self):
        self.agent.install(self.config)
        self.calls.clear()
        with self.agent._lifecycle(self.config):
            with self.assertRaisesRegex(OSError, 'operation'):
                self.agent.stop_if_installed(self.config)
        self.assertEqual(self.calls, [])

    def test_plist_drift_during_inspection_prevents_mutation(self):
        self.agent.install(self.config)
        self.agent.stop_if_installed(self.config)
        self.calls.clear()
        original = self.launchctl
        def drift(args, **kwargs):
            result = original(args, **kwargs)
            if args[1] == 'print':
                self.agent.agent_path(self.config).write_text('changed concurrently')
            return result
        self.run.side_effect = drift
        with self.assertRaisesRegex(OSError, 'match'):
            self.agent.start_if_installed(self.config)
        self.assertEqual([args[1] for args in self.calls], ['print'])

    def test_private_runtime_log_has_bounded_retention(self):
        stream = self.agent.RuntimeLog(self.config)
        stream.write('x' * (self.agent.LOG_LIMIT + 100))
        stream.write('next line\n')
        path = Path(self.config['_state_dir']) / 'controller.log'
        backup = path.with_suffix('.log.1')
        self.assertLessEqual(path.stat().st_size, self.agent.LOG_LIMIT)
        self.assertLessEqual(backup.stat().st_size, self.agent.LOG_LIMIT)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.read_text(), 'next line\n')

    def test_oversized_legacy_logs_are_bounded_on_write(self):
        path = Path(self.config['_state_dir']) / 'controller.log'
        backup = path.with_suffix('.log.1')
        for item in (path, backup):
            item.write_bytes(b'x' * (self.agent.LOG_LIMIT + 100))
            item.chmod(0o600)
        path.write_bytes(b'current')
        self.agent.RuntimeLog(self.config).write('')
        self.assertLessEqual(path.stat().st_size, self.agent.LOG_LIMIT)
        self.assertLessEqual(backup.stat().st_size, self.agent.LOG_LIMIT)

    def test_metadata_interpreter_change_does_not_authorize_plist_drift(self):
        self.agent.install(self.config)
        path = self.agent._metadata_path(self.config)
        data = json.loads(path.read_bytes())
        data['arguments'][0] = '/bin/sh'
        path.write_text(json.dumps(data, sort_keys=True, separators=(',', ':')) + '\n')
        self.calls.clear()
        with self.assertRaisesRegex(OSError, 'match'):
            self.agent.start_if_installed(self.config)
        self.assertEqual(self.calls, [])

    def test_configure_runtime_checks_health_before_writes(self):
        self.preflight.side_effect = OSError('unhealthy')
        with self.assertRaises(OSError):
            self.agent.configure_runtime(self.config)
        self.assertFalse((Path(self.config['_state_dir']) / 'controller.log').exists())

    def test_runtime_log_refuses_symlink(self):
        path = Path(self.config['_state_dir']) / 'controller.log'
        path.symlink_to(self.config_file)
        with self.assertRaises(OSError):
            self.agent.RuntimeLog(self.config).write('test')
        self.assertEqual(self.config_file.read_text(), '{}')

    def test_symlink_plist_is_refused(self):
        path = self.agent.agent_path(self.config)
        path.parent.mkdir(parents=True)
        path.symlink_to(self.config_file)
        with self.assertRaises(OSError):
            self.agent.install(self.config)
        self.assertEqual(self.config_file.read_text(), '{}')


if __name__ == '__main__':
    unittest.main()
