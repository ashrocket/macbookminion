"""Worker registry, ownership and sanitized observations; no live SSH."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/macbook-minion/scripts"
sys.path.insert(0, str(SCRIPTS))
import audit
import control
import fleet
import minion


class FakeTransport:
    def __init__(self, aliases=None, physical=None, offline=()):
        self.aliases = aliases or {}
        self.physical = physical or {}
        self.offline = offline
        self.calls = []

    key_values = staticmethod(minion.key_values)
    ssh_base = staticmethod(minion.ssh_base)

    def remote(self, config, action, *args, **kwargs):
        self.calls.append((config["host"], action))
        if config["host"] in self.offline:
            raise OSError("private hostname and credentials must not be emitted")
        uid = hashlib.sha256(self.physical.get(config["host"], config["host"]).encode()).hexdigest()
        return ("os=Darwin\narch=x86_64\nlogical_cpus=4\nload1=1.25\n"
                "memory_bytes=17179869184\nfree_kib=52428800\nmemory_pressure=warn\n"
                "active_job=j-active\nworker_uid=" + uid + "\ntool_python3=/private/name/python\n")

    def execute(self, args, **kwargs):
        return "hostname " + self.aliases.get(args[-1], args[-1]) + "\n"


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "config.json"
        self.key = self.root / "test_identity"
        self.key.write_text("test only")
        self.legacy = {"host": "old-mac", "identity": str(self.key), "min_free_gib": 25}
        self.path.write_text(json.dumps(self.legacy))

    def tearDown(self):
        self.temp.cleanup()

    def add(self, name="second", host="second-mac", transport=None):
        return fleet.add_worker(self.path, name, host, self.key, transport=transport or FakeTransport())

    def test_legacy_config_and_state_directory_are_preserved(self):
        config = minion.load_config(self.path)
        expected = "state-" + hashlib.sha256(b"old-mac").hexdigest()[:12]
        self.assertEqual(Path(config["_state_dir"]).name, expected)
        self.assertEqual(config["_worker_id"], "default")
        self.add()
        self.assertEqual(minion.load_config(self.path), config)
        saved = json.loads(self.path.read_text())
        for key, value in self.legacy.items():
            self.assertEqual(saved[key], value)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_explicit_selection_and_default_worker(self):
        self.add()
        second = minion.load_config(self.path, "second")
        self.assertEqual(second["host"], "second-mac")
        self.assertNotEqual(second["_state_dir"], minion.load_config(self.path)["_state_dir"])
        document = json.loads(self.path.read_text())
        document["default_worker"] = "second"
        self.path.write_text(json.dumps(document))
        self.assertEqual(minion.load_config(self.path)["_worker_id"], "second")
        self.assertEqual(minion.load_config(self.path, "default")["host"], "old-mac")
        with self.assertRaisesRegex(ValueError, "Unknown worker"):
            minion.load_config(self.path, "missing")

    def test_mapping_only_config(self):
        self.path.write_text(json.dumps({"default_worker": "one", "workers": {"one": self.legacy}}))
        self.assertEqual(minion.load_config(self.path)["_worker_id"], "one")

    def test_duplicate_hosts_aliases_and_physical_identity_rejected(self):
        for host, transport in (
            ("old-mac", FakeTransport()),
            ("alias", FakeTransport(aliases={"alias": "old-mac"})),
            ("new-address", FakeTransport(physical={"new-address": "old-mac"})),
        ):
            original = self.path.read_bytes()
            with self.assertRaisesRegex(ValueError, "already registered"):
                self.add(host=host, transport=transport)
            self.assertEqual(self.path.read_bytes(), original)

    def test_failed_probe_and_unknown_physical_identity_do_not_save(self):
        original = self.path.read_bytes()
        with self.assertRaises(OSError):
            self.add(transport=FakeTransport(offline={"second-mac"}))
        with self.assertRaises(OSError):
            self.add(transport=FakeTransport(offline={"old-mac"}))
        transport = FakeTransport()
        with patch.object(transport, "remote", return_value="os=Darwin\n"):
            with self.assertRaisesRegex(ValueError, "physical worker identity"):
                self.add(transport=transport)
        self.assertEqual(self.path.read_bytes(), original)

    def test_concurrent_additions_keep_both_workers(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda number: self.add("worker-" + str(number), "mac-" + str(number)), (1, 2)))
        self.assertTrue(all(result["configured"] for result in results))
        self.assertEqual(set(fleet.load_configs(self.path)), {"default", "worker-1", "worker-2"})

    def test_concurrent_duplicate_registration_accepts_only_once(self):
        def attempt(name):
            try:
                return self.add(name, "same-mac")["configured"]
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("first", "second")))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(fleet.load_configs(self.path)), 2)

    def test_initial_config_never_overwrites_existing(self):
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            fleet.save_initial(self.path, {"host": "replacement"})
        self.assertEqual(self.path.read_bytes(), original)

    def test_service_command_pins_worker_and_host(self):
        self.add()
        config = minion.load_config(self.path, "second")
        argv = control.service_command(config)
        parsed = minion.parser().parse_args(argv[2:])
        self.assertEqual(parsed.worker, "second")
        self.assertEqual(parsed.expected_host, "second-mac")
        self.assertEqual(parsed.action, "serve")
        with patch.object(minion, "remote") as remote, patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(minion.main(["--config", str(self.path), "--worker", "second",
                                          "--expected-host", "wrong-host", "serve"]), 1)
        remote.assert_not_called()

    def test_global_worker_cli_only_contacts_selected_worker(self):
        self.add()
        with patch.object(minion, "remote", return_value="os=Darwin\n") as remote, patch("sys.stdout", new_callable=io.StringIO):
            result = minion.main(["--config", str(self.path), "--worker", "second", "probe"])
        self.assertEqual(result, 0)
        self.assertEqual(remote.call_args.args[0]["host"], "second-mac")

    def test_known_snapshot_and_job_ids_cannot_move_workers(self):
        self.add()
        first = minion.load_config(self.path)
        second = minion.load_config(self.path, "second")
        store = audit.store_for(first)
        store.state("snapshot:s-existing", {"worker": "default"})
        store.enqueue({"id": "j-existing", "title": "existing", "description": "saved", "owner": "test/session",
                       "created_at": audit.now(), "snapshot": "s-existing", "script_sha256": "0" * 64})
        for item in ("s-existing", "j-existing"):
            fleet.assert_item_worker(first, item)
            with self.assertRaisesRegex(ValueError, "belongs to worker default"):
                fleet.assert_item_worker(second, item)
        with patch.object(minion, "remote") as remote, patch("sys.stderr", new_callable=io.StringIO):
            result = minion.main(["--config", str(self.path), "--worker", "second", "status", "j-existing"])
        self.assertEqual(result, 1)
        remote.assert_not_called()

    def test_offline_observation_omits_stale_capacity_and_sensitive_errors(self):
        config = minion.load_config(self.path)
        store = audit.store_for(config)
        store.state("worker", {"online": True, "active_job": "j-stale", "free_gib": 100})
        row = fleet.observe(config, FakeTransport(offline={"old-mac"}))
        self.assertFalse(row["online"])
        self.assertIsNone(row["active_job"])
        self.assertIsNone(row["free_gib"])
        self.assertEqual(row["memory_pressure"], "unknown")
        self.assertNotIn("private", json.dumps(row))
        self.assertIsInstance(row["observed_at"], float)

    def test_overview_sanitizes_and_counts_pending_jobs(self):
        self.add()
        store = audit.store_for(minion.load_config(self.path))
        store.enqueue({"id": "j-pending", "title": "queued", "description": "saved", "owner": "test/session",
                       "created_at": audit.now(), "snapshot": "s-existing", "script_sha256": "0" * 64})
        rows = fleet.overview(self.path, FakeTransport())["workers"]
        row = next(row for row in rows if row["worker"] == "default")
        self.assertEqual(row["logical_cpus"], 4)
        self.assertEqual(row["memory_gib"], 16)
        self.assertEqual(row["free_gib"], 50)
        self.assertEqual(row["memory_pressure"], "warn")
        self.assertEqual(row["pending_queue_count"], 1)
        self.assertEqual(row["active_job"], "j-active")
        self.assertNotIn("old-mac", json.dumps(rows))
        self.assertNotIn("/private", json.dumps(rows))
        self.assertEqual(len(rows), 2)

    def test_empty_active_job_is_known_idle_and_missing_is_unknown(self):
        config = minion.load_config(self.path)
        transport = FakeTransport()
        for response, expected in (("os=Darwin\nactive_job=\n", ""),
                                   ("os=Darwin\n", None),
                                   ("os=Darwin\nactive_job=malformed\n", None)):
            with self.subTest(response=response), patch.object(transport, "remote", return_value=response):
                self.assertEqual(fleet.observe(config, transport)["active_job"], expected)

    def test_untrusted_numbers_and_pressure_are_unknown(self):
        transport = FakeTransport()
        with patch.object(transport, "remote", return_value="os=Darwin\nload1=NaN\nfree_kib=-1\nlogical_cpus=4.5\nmemory_pressure=weird\n"):
            row = fleet.observe(minion.load_config(self.path), transport)
        self.assertIsNone(row["load1"])
        self.assertIsNone(row["free_gib"])
        self.assertIsNone(row["logical_cpus"])
        self.assertEqual(row["memory_pressure"], "unknown")

    def test_missing_optional_sysctl_keeps_probe_fields_separate(self):
        payload = (SCRIPTS / "remote.sh").read_text()
        payload += r"""
uname() { if [ "$1" = -s ]; then echo Darwin; else echo arm64; fi; }
sysctl() {
  case "$2" in
    hw.logicalcpu) echo 8 ;;
    hw.memsize) echo 17179869184 ;;
    vm.loadavg) echo '{ 1.25 2.0 3.0 }' ;;
    kern.memorystatus_vm_pressure_level) echo 1 ;;
    kern.hostuuid) echo test-uuid ;;
    *) return 1 ;;
  esac
}
root=/nonexistent-test-worker
probe
"""
        completed = subprocess.run(["/bin/bash"], input=payload, text=True, capture_output=True, check=True)
        info = minion.key_values(completed.stdout)
        self.assertEqual(info["logical_cpus"], "8")
        self.assertEqual(info["memory_bytes"], "17179869184")
        self.assertEqual(info["load1"], "1.25")
        self.assertEqual(info["memory_pressure"], "normal")
        self.assertEqual(info["worker_uid"], hashlib.sha256(b"test-uuid").hexdigest())

    def test_remote_probe_shell_parses(self):
        subprocess.run(["/bin/bash", "-n", str(SCRIPTS / "remote.sh")], check=True)


if __name__ == "__main__":
    unittest.main()
