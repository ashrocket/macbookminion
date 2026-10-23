import importlib.util
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skills/macbook-minion/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("minion", SCRIPTS / "minion.py")
minion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(minion)


class ControllerTests(unittest.TestCase):
    def test_host_and_id_reject_options_and_shell_input(self):
        for value in ("-oProxyCommand=evil", "u@host;id", "x y", "u@$(id)", "u@host\n"):
            with self.assertRaises(minion.MinionError):
                minion.host_value(value)
        for value in ("../oops", "x/y", "x;id", "-x"):
            with self.assertRaises(minion.MinionError):
                minion.identifier(value)
        self.assertEqual(minion.host_value("minion@old-mac.local"), "minion@old-mac.local")

    def test_ssh_strict_host_checks_no_forwarding(self):
        args = minion.ssh_base({"identity": "/tmp/key with spaces"})
        for option in ("StrictHostKeyChecking=yes", "ForwardAgent=no", "BatchMode=yes", "ClearAllForwardings=yes"):
            self.assertIn(option, args)
        self.assertEqual(args[-1], "/tmp/key with spaces")

    def test_inventory_filters_credentials_dependencies_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            (source / "good file.txt").write_text("hello")
            (source / ".env.local").write_text("fake-test-secret")
            (source / "key.pem").write_text("fake-test-key")
            (source / "node_modules").mkdir()
            (source / "node_modules/dep.js").write_text("dependency")
            (source / "link").symlink_to(source / "good file.txt")
            _, files, skipped, total = minion.inventory(source)
            self.assertEqual([name for name, _ in files], ["good file.txt"])
            self.assertEqual(total, 5)
            self.assertEqual(set(skipped), {".env.local", "key.pem", "node_modules", "link"})

    def test_verify_rejects_changed_extra_and_traversal_files(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp)
            (dest / "result.txt").write_text("good")
            manifest = minion.manifest_text([("result.txt", minion.digest(dest / "result.txt"))])
            self.assertEqual(minion.verify_download(dest, manifest), 1)
            (dest / "result.txt").write_text("bad")
            with self.assertRaises(minion.MinionError):
                minion.verify_download(dest, manifest)
            with self.assertRaises(minion.MinionError):
                minion.verify_download(dest, "0" * 64 + "  ./../escape\n")
            (dest / "result.txt").write_text("good")
            (dest / "extra").write_text("unexpected")
            with self.assertRaises(minion.MinionError):
                minion.verify_download(dest, manifest)

    def test_existing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(minion.MinionError):
                minion.fetch({"host": "fake"}, "jobs", "j-1", Path(temp))

    def test_sensitive_source_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / ".ssh"
            source.mkdir()
            (source / "config").write_text("test")
            with self.assertRaises(minion.MinionError):
                minion.inventory(source)


class WorkerTests(unittest.TestCase):
    """Real macOS Bash/rsync behavior in temp dirs; SSH transport is replaced locally."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="minion-test-")
        self.base = Path(self.temp.name)
        self.root = self.base / "worker space"
        self.config = {"host": "test-worker", "min_free_gib": 1, "identity": None}
        self.real_rsync = minion.rsync
        self.remote_patch = patch.object(minion, "remote", self.local_remote)
        self.rsync_patch = patch.object(minion, "rsync", self.local_rsync)
        self.remote_patch.start()
        self.rsync_patch.start()
        self.local_remote(self.config, "init")

    def tearDown(self):
        # Tests wait for every launched runner before removing their temp tree.
        self.remote_patch.stop()
        self.rsync_patch.stop()
        self.temp.cleanup()

    def local_remote(self, config, action, *args, timeout=45):
        payload = (SCRIPTS / "remote.sh").read_text()
        payload += "\nremote_main " + shlex.join([str(self.root), action, *map(str, args)]) + "\n"
        return minion.execute(["/bin/bash"], input=payload, timeout=timeout)

    def local_rsync(self, config, source, destination, *, file_list=None):
        prefix = "test-worker:.macbookminion/"
        if source.startswith(prefix):
            source = str(self.root) + "/" + source[len(prefix):]
        if destination.startswith(prefix):
            destination = str(self.root) + "/" + destination[len(prefix):]
        self.real_rsync(config, source, destination, file_list=file_list)

    def source(self):
        source = self.base / "source space"
        source.mkdir()
        (source / "input.txt").write_text("verified input\n")
        (source / ".env").write_text("not transferred")
        (source / "nested").mkdir()
        (source / "nested/has spaces.txt").write_text("nested")
        (source / "execute.sh").write_text("#!/bin/sh\nexit 0\n")
        (source / "execute.sh").chmod(0o755)
        return source

    def wait_finished(self, job):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = minion.key_values(self.local_remote(self.config, "status", job))
            if status.get("state") == "finished" and not (self.root / "active").exists():
                return status
            time.sleep(0.05)
        self.fail("Worker did not finish in time")

    def test_real_transfer_job_and_verified_fetch(self):
        uploaded = minion.put(self.config, self.source())
        snapshot = uploaded["snapshot"]
        self.assertTrue(uploaded["sha256_verified"])
        self.assertFalse((self.root / "snapshots" / snapshot / ".env").exists())
        self.assertEqual((self.root / "snapshots" / snapshot / "nested/has spaces.txt").read_text(), "nested")
        self.assertEqual((self.root / "snapshots" / snapshot / "execute.sh").stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / "snapshots" / snapshot / "input.txt").stat().st_mode & 0o777, 0o600)
        job = minion.run_job(self.config, snapshot, REPO / "examples/smoke-job.sh", 1, "test/session-1")["job"]
        status = self.wait_finished(job)
        self.assertEqual(status["exit_code"], "0")
        self.assertEqual(status["owner"], "test/session-1")
        result = minion.fetch(self.config, "jobs", job, self.base / "results")
        self.assertTrue(result["sha256_verified"])
        self.assertIn("verified input", (self.base / "results/output/result.txt").read_text())
        self.assertIn("Smoke job complete", self.local_remote(self.config, "logs", job))
        with self.assertRaises(minion.MinionError):
            self.local_remote(self.config, "launch", job)

    def test_failure_exit_code_is_preserved(self):
        snapshot = minion.put(self.config, self.source())["snapshot"]
        script = self.base / "fail.sh"
        script.write_text("printf 'expected failure\\n'\nexit 7\n")
        job = minion.run_job(self.config, snapshot, script, 1)["job"]
        self.assertEqual(self.wait_finished(job)["exit_code"], "7")
        result = minion.fetch(self.config, "jobs", job, self.base / "failed-results")
        self.assertTrue(result["sha256_verified"])

    def test_detached_job_excludes_second_job_and_early_fetch(self):
        snapshot = minion.put(self.config, self.source())["snapshot"]
        script = self.base / "wait.sh"
        gate = self.base / "release"
        script.write_text("while [ ! -f " + shlex.quote(str(gate)) + " ]; do sleep 0.05; done\n")
        job = minion.run_job(self.config, snapshot, script, 1)["job"]
        try:
            deadline = time.monotonic() + 4
            status = {}
            while time.monotonic() < deadline:
                status = minion.key_values(self.local_remote(self.config, "status", job))
                if status.get("state") == "running":
                    break
                time.sleep(0.05)
            self.assertEqual(status.get("state"), "running")
            with self.assertRaises(minion.MinionError):
                minion.run_job(self.config, snapshot, script, 1)
            with self.assertRaises(minion.MinionError):
                minion.fetch(self.config, "jobs", job, self.base / "early")
            with self.assertRaises(minion.MinionError):
                self.local_remote(self.config, "launch", job)
        finally:
            gate.touch()
            self.wait_finished(job)

    def test_low_space_rejected_before_snapshot_creation(self):
        with self.assertRaises(minion.MinionError):
            self.local_remote(self.config, "prepare_snapshot", "s-no-space", 10**15)
        self.assertFalse((self.root / "snapshots/s-no-space").exists())

    def test_corrupt_upload_is_not_marked_ready(self):
        self.local_remote(self.config, "prepare_snapshot", "s-bad", 0)
        (self.root / "snapshots/s-bad/file.txt").write_text("changed")
        (self.root / "snapshots/s-bad.sha256").write_text("0" * 64 + "  ./file.txt\n")
        with self.assertRaises(minion.MinionError):
            self.local_remote(self.config, "verify_snapshot", "s-bad")
        self.assertFalse((self.root / "snapshots/s-bad.ready").exists())

    def test_symlink_workspace_is_rejected(self):
        other = self.base / "other"
        other.mkdir()
        link = self.base / "link"
        link.symlink_to(other, target_is_directory=True)
        self.root = link
        with self.assertRaises(minion.MinionError):
            self.local_remote(self.config, "init")
        self.assertEqual(list(other.iterdir()), [])

    def test_dead_or_reused_pid_is_interrupted(self):
        directory = self.root / "jobs/j-interrupted"
        directory.mkdir()
        (directory / "pid").write_text(str(os.getpid()))
        (directory / "process_start").write_text("different start time")
        status = minion.key_values(self.local_remote(self.config, "status", "j-interrupted"))
        self.assertEqual(status["state"], "interrupted")

    def test_invalid_owner_cannot_create_a_job_or_lock(self):
        with self.assertRaises(minion.MinionError):
            minion.run_job(self.config, "s-valid", REPO / "examples/smoke-job.sh", 1, "bad\nstate=finished")
        self.assertFalse((self.root / "active").exists())
        self.assertEqual(list((self.root / "jobs").iterdir()), [])

    def test_uncertain_browser_cleanup_keeps_lock_after_exit(self):
        snapshot = minion.put(self.config, self.source())["snapshot"]
        script = self.base / "retained.sh"
        script.write_text('touch "$MINION_OUTPUT_DIR/../retain_lock"\nexit 9\n')
        job = minion.run_job(self.config, snapshot, script, 1)["job"]
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = minion.key_values(self.local_remote(self.config, "status", job))
            if status.get("state") == "finished":
                break
            time.sleep(0.05)
        self.assertEqual(status.get("exit_code"), "9")
        self.assertEqual(status.get("lock_retained"), "yes")
        self.assertEqual((self.root / "active/job").read_text().strip(), job)
        with self.assertRaises(minion.MinionError):
            minion.run_job(self.config, snapshot, script, 1)


if __name__ == "__main__":
    unittest.main()
