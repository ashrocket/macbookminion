"""Physical identity guards exercise actual shell parsing without SSH."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/macbook-minion/scripts"))
import fleet
import minion


class WorkerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="minion-identity-")
        self.root = Path(self.temp.name)
        self.uuid = "test-physical-worker"
        self.config = {"host": "stable-alias", "min_free_gib": 25,
                       "worker_uid": hashlib.sha256(self.uuid.encode()).hexdigest()}
        self.sysctl = self.root / "fake sysctl"
        self.sysctl.write_text("#!/bin/bash\nprintf '%s\\n' '" + self.uuid + "'\n")
        self.sysctl.chmod(0o700)
        self.ioreg = self.root / "fake ioreg"
        self.ioreg.write_text("#!/bin/bash\nexit 1\n")
        self.ioreg.chmod(0o700)
        self.rsync = self.root / "fake rsync"
        self.args_file = self.root / "rsync-args.json"
        self.rsync.write_text("#!" + sys.executable + "\nimport json,sys\n"
                              "open(" + repr(str(self.args_file)) + ", 'w').write(json.dumps(sys.argv[1:]))\n")
        self.rsync.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def shell_fixture(self, text):
        return text.replace("raw=$(sysctl ", "raw=$(" + shlex.quote(str(self.sysctl)) + " ").replace(
            "/usr/sbin/ioreg", shlex.quote(str(self.ioreg))).replace(
            "/usr/bin/rsync", shlex.quote(str(self.rsync)))

    def test_remote_identity_is_checked_before_init_creates_any_directory(self):
        for uid, success in (("b" * 64, False), (self.config["worker_uid"], True)):
            config = {**self.config, "worker_uid": uid}
            with patch.object(minion, "execute", return_value="") as execute:
                minion.remote(config, "init")
            payload = self.shell_fixture(execute.call_args.kwargs["input"])
            destination = self.root / ("worker-" + str(success))
            payload = payload.replace('"$HOME/.macbookminion"', shlex.quote(str(destination)))
            result = subprocess.run(["/bin/bash"], input=payload, text=True, capture_output=True)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            self.assertEqual(destination.exists(), success)
            if not success:
                self.assertIn("operation refused", result.stderr)

    def test_rsync_guard_and_server_share_connection_and_preserve_arguments(self):
        arguments = ["--server", "--sender", "-logDtpre.iLsfxC", ".", "path with 'quotes' and spaces"]
        for uid, success in (("b" * 64, False), (self.config["worker_uid"], True)):
            with patch.object(minion, "execute", return_value="") as execute:
                minion.rsync({**self.config, "worker_uid": uid}, "/tmp/source", "stable-alias:.macbookminion/s-1/")
            argv = execute.call_args.args[0]
            command = next(arg.split("=", 1)[1] for arg in argv if arg.startswith("--rsync-path="))
            # A remote login shell parses this exactly as rsync's SSH command.
            # Replace only the absolute tool paths with controlled test fixtures.
            words = shlex.split(command)
            words[2] = self.shell_fixture(words[2])
            result = subprocess.run(["/bin/bash", "-c", shlex.join(words) + " " + shlex.join(arguments)],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode == 0, success, result.stderr)
            self.assertEqual(self.args_file.exists(), success)
            if success:
                self.assertEqual(json.loads(self.args_file.read_text()), arguments)

    def test_missing_uuid_fails_before_remote_action(self):
        self.sysctl.write_text("#!/bin/bash\nexit 1\n")
        payload = self.shell_fixture(minion.identity_guard(self.config)) + "printf 'BAD ACTION'\n"
        result = subprocess.run(["/bin/bash"], input=payload, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("BAD ACTION", result.stdout)

    def test_ioreg_fallback_matches_probe_and_guard_digest_without_uuid_disclosure(self):
        self.sysctl.write_text("#!/bin/bash\nexit 1\n")
        fixture = '|   "IOPlatformUUID" = "' + self.uuid + '"\n'
        self.ioreg.write_text("#!/bin/bash\nprintf '%s' " + shlex.quote(fixture) + "\n")
        script = self.shell_fixture(minion.SCRIPTS.joinpath("remote.sh").read_text())
        # Override unrelated inventory only; exercise the real probe identity path.
        script += "\nroot=/nonexistent-minion-test\nprobe\n"
        probe = subprocess.run(["/bin/bash"], input=script, text=True, capture_output=True)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertEqual(minion.key_values(probe.stdout)["worker_uid"], self.config["worker_uid"])
        guard = self.shell_fixture(minion.identity_guard(self.config)) + "printf 'verified\\n'\n"
        guarded = subprocess.run(["/bin/bash"], input=guard, text=True, capture_output=True)
        self.assertEqual(guarded.returncode, 0, guarded.stderr)
        self.assertEqual(guarded.stdout, "verified\n")
        self.assertNotIn(self.uuid, probe.stdout + probe.stderr + guarded.stdout + guarded.stderr)

    def test_empty_sysctl_uses_fallback_and_missing_platform_uuid_refuses(self):
        self.sysctl.write_text("#!/bin/bash\nexit 0\n")
        self.ioreg.write_text("#!/bin/bash\nprintf '%s\\n' 'unrelated properties'\n")
        script = self.shell_fixture(minion.identity_guard(self.config)) + "printf 'BAD ACTION'\n"
        result = subprocess.run(["/bin/bash"], input=script, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("BAD ACTION", result.stdout)

    def test_invalid_saved_identity_never_starts_transport(self):
        for uid in (None, "", True, "a" * 63, "$(touch bad)", [], {}):
            config = {**self.config, "worker_uid": uid}
            with self.subTest(uid=uid), patch.object(minion, "execute") as execute:
                with self.assertRaises(minion.MinionError):
                    minion.remote(config, "init")
                with self.assertRaises(minion.MinionError):
                    minion.rsync(config, "/tmp/source", "stable-alias:.macbookminion/")
                execute.assert_not_called()

    def test_legacy_transport_remains_compatible(self):
        config = {"host": "legacy-alias", "min_free_gib": 25}
        self.assertEqual(minion.identity_guard(config), "")
        with patch.object(minion, "execute", return_value="") as execute:
            minion.rsync(config, "/tmp/source", "legacy-alias:.macbookminion/")
        self.assertFalse(any(arg.startswith("--rsync-path=") for arg in execute.call_args.args[0]))

    def test_observation_rejects_wrong_or_missing_physical_identity(self):
        class Transport:
            key_values = staticmethod(minion.key_values)
        transport = Transport()
        for observed in ("", "b" * 64, self.config["worker_uid"]):
            response = "os=Darwin\nworker_uid=" + observed + "\nactive_job=\nlogical_cpus=8\n"
            with patch.object(transport, "remote", create=True, return_value=response):
                row = fleet.observe(self.config, transport)
            self.assertEqual(row["online"], observed == self.config["worker_uid"])
            if observed != self.config["worker_uid"]:
                self.assertIsNone(row["logical_cpus"])
                self.assertIsNone(row["active_job"])


if __name__ == "__main__":
    unittest.main()
