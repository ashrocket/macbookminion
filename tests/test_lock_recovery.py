"""Exercise recovery in real Bash against disposable worker directories only."""
from pathlib import Path
import os
import select
import shlex
import subprocess
import tempfile
import unittest


REMOTE = Path(__file__).resolve().parents[1] / "skills/macbook-minion/scripts/remote.sh"


class LockRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "worker space"
        (self.root / "jobs").mkdir(parents=True)
        (self.root / "snapshots/s-test").mkdir(parents=True)
        (self.root / "snapshots/s-test.ready").touch()

    def tearDown(self):
        self.temp.cleanup()

    def script(self, command, boot=200, extra=""):
        script = REMOTE.read_text() + "\nroot=" + shlex.quote(str(self.root)) + "\n"
        script += "boot_epoch() { printf '%s\\n' " + shlex.quote(str(boot)) + "; }\n"
        script += "utc_epoch() { printf '100\\n'; }\n"
        script += "modified_epoch() { printf '200\\n'; }\n"
        script += "sync() { :; }\n" + extra + "\n"
        return script + command + "\n"

    def shell(self, command, boot=200, extra=""):
        return subprocess.run(["/bin/bash"], input=self.script(command, boot, extra), text=True,
                              capture_output=True, timeout=5)

    def retained(self):
        job = self.root / "jobs/j-old"
        job.mkdir()
        for name, value in {"owner": "previous/session", "finished_at": "2026-10-25T00:37:24Z",
                            "exit_code": "1", "retain_lock": "", "worker_events.tsv": ""}.items():
            (job / name).write_text(value + "\n")
        (job / "output").mkdir()
        (job / "output/result.txt").write_text("preserved result")
        (self.root / "active").mkdir()
        (self.root / "active/job").write_text("j-old\n")
        return job

    def recover(self, boot=200, owner="previous/session", extra=""):
        return self.shell('remote_main "$root" recover_lock j-old ' + shlex.quote(owner) +
                          ' recovery/session', boot=boot, extra=extra)

    def test_inspect_is_read_only_and_requires_later_boot(self):
        job = self.retained()
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        for boot, eligible in ((99, "no"), (100, "no"), (200, "yes"), ("unknown", "no")):
            result = self.shell('remote_main "$root" lock_inspect j-old', boot=boot)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("reboot_proves_processes_ended=" + eligible, result.stdout)
            self.assertIn("owner=previous/session", result.stdout)
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")))
        self.assertFalse((job / "lock_recovery_intent").exists())

    def test_requires_later_reboot_and_exact_owner(self):
        job = self.retained()
        for boot, owner in ((99, "previous/session"), (100, "previous/session"),
                            ("unknown", "previous/session"), (200, "wrong/session")):
            result = self.recover(boot, owner)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((self.root / "active/job").exists())
            self.assertFalse((job / "lock_recovered").exists())
            self.assertFalse((self.root / "recovery-in-progress").exists())

    def test_recovery_archives_lock_and_guard_preserving_job_artifacts(self):
        job = self.retained()
        result = self.recover()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("artifacts=preserved", result.stdout)
        self.assertFalse((self.root / "active").exists())
        self.assertEqual((job / "recovered-lock/job").read_text(), "j-old\n")
        self.assertEqual((job / "exit_code").read_text(), "1\n")
        self.assertTrue((job / "retain_lock").exists())
        self.assertEqual((job / "output/result.txt").read_text(), "preserved result")
        self.assertIn("recovery/session", (job / "lock_recovered").read_text())
        self.assertIn("recovery/session", (job / "lock_recovery_intent").read_text())
        self.assertTrue((job / "recovered-recovery-guard/claim-200").is_dir())
        self.assertNotEqual(self.recover().returncode, 0)
        self.assertNotEqual(self.recover(boot=300).returncode, 0)
        status = self.shell('remote_main "$root" status j-old')
        self.assertIn("lock_recovered=yes", status.stdout)
        self.assertNotIn("lock_retained=yes", status.stdout)

    def test_unfinished_job_is_not_recoverable(self):
        job = self.retained()
        (job / "exit_code").unlink()
        self.assertNotEqual(self.recover().returncode, 0)
        self.assertTrue((self.root / "active/job").exists())

    def test_extra_lock_contents_refuse_recovery(self):
        self.retained()
        (self.root / "active/unknown").write_text("preserve me")
        self.assertNotEqual(self.recover().returncode, 0)
        self.assertEqual((self.root / "active/unknown").read_text(), "preserve me")

    def test_symlink_evidence_refuses_inspection_and_recovery(self):
        job = self.retained()
        (job / "exit_code").unlink()
        (job / "exit_code").symlink_to(job / "owner")
        self.assertNotEqual(self.recover().returncode, 0)
        self.assertNotEqual(self.shell('remote_main "$root" lock_inspect j-old').returncode, 0)
        self.assertTrue((self.root / "active/job").exists())

    def test_empty_guard_blocks_dispatch_and_requires_reboot_for_resume(self):
        job = self.retained()
        guard = self.root / "recovery-in-progress"
        guard.mkdir()
        (guard / "job").touch()
        self.assertEqual(self.shell("active_job").stdout, "pending-recovery\n")
        self.assertNotEqual(self.recover().returncode, 0)
        result = self.recover(boot=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((job / "recovered-lock/job").exists())

    def test_guard_for_another_job_refuses_resume(self):
        self.retained()
        guard = self.root / "recovery-in-progress"
        guard.mkdir()
        (guard / "job").write_text("j-other\n")
        self.assertNotEqual(self.recover(boot=300).returncode, 0)
        self.assertTrue((self.root / "active/job").exists())

    def test_current_boot_claim_refuses_second_recovery(self):
        self.retained()
        guard = self.root / "recovery-in-progress"
        (guard / "claim-300").mkdir(parents=True)
        (guard / "job").write_text("j-old\n")
        result = self.recover(boot=300)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already claimed", result.stderr)
        self.assertTrue((self.root / "active/job").exists())

    def test_concurrent_resume_has_one_claim_and_preserves_dispatch_barrier(self):
        self.retained()
        guard = self.root / "recovery-in-progress"
        guard.mkdir()
        (guard / "job").write_text("j-old\n")
        gate = self.root / "test-gate"
        os.mkfifo(gate)
        extra = '''sync_count=0
        sync() {
          sync_count=$((sync_count+1))
          if [ "$sync_count" -eq 1 ]; then
            printf 'ready\\n'
            read -r barrier < "$root/test-gate"
          fi
        }'''
        script = self.script('remote_main "$root" recover_lock j-old previous/session recovery/session',
                             boot=300, extra=extra)
        # One Bash process pauses after its durable intent while a second
        # process attempts the same recovery in the same fake boot.
        runner = self.root / "test-runner.sh"
        runner.write_text(script)
        first = subprocess.Popen(["/bin/bash", str(runner)], text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(select.select([first.stdout], [], [], 5)[0], "Recovery did not reach its intent")
            self.assertEqual(first.stdout.readline(), "ready\n")
            second = self.recover(boot=300)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already claimed", second.stderr)
            self.assertTrue((self.root / "active/job").exists())
            blocked = self.shell('remote_main "$root" prepare_job j-new s-test 0 next/session')
            self.assertNotEqual(blocked.returncode, 0)
            gate.write_text("continue\n")
            stdout, stderr = first.communicate(timeout=5)
            self.assertEqual(first.returncode, 0, stderr)
            self.assertIn("lock_recovered=yes", stdout)
        finally:
            if first.poll() is None:
                first.kill()
            first.communicate(timeout=5)

    def test_interrupted_recovery_blocks_dispatch_until_later_boot(self):
        for stage in ("intent", "moved", "receipt"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as isolated:
                original = self.root
                self.root = Path(isolated)
                try:
                    (self.root / "jobs").mkdir()
                    (self.root / "snapshots/s-test").mkdir(parents=True)
                    (self.root / "snapshots/s-test.ready").touch()
                    job = self.retained()
                    if stage == "intent":
                        extra = "sync() { exit 71; }"
                    elif stage == "moved":
                        extra = 'mv() { /bin/mv "$@"; exit 72; }'
                    else:
                        extra = 'sync_count=0; sync() { sync_count=$((sync_count+1)); [ "$sync_count" -ne 2 ] || exit 73; }'
                    self.assertNotEqual(self.recover(extra=extra).returncode, 0)
                    self.assertTrue((job / "lock_recovery_intent").exists())
                    self.assertEqual(self.shell("active_job").stdout, "j-old\n")
                    for action in ("prepare_job j-new s-test 0 next/session", "verify_job j-old", "launch j-old"):
                        blocked = self.shell('remote_main "$root" ' + action)
                        self.assertNotEqual(blocked.returncode, 0)
                        self.assertIn("recovery must finish", blocked.stderr)
                    self.assertFalse((self.root / "jobs/j-new").exists())
                    self.assertNotEqual(self.recover().returncode, 0)
                    resumed = self.recover(boot=300)
                    self.assertEqual(resumed.returncode, 0, resumed.stderr)
                    self.assertFalse((self.root / "recovery-in-progress").exists())
                    self.assertTrue((job / "recovered-lock/job").exists())
                finally:
                    self.root = original

    def test_guard_created_during_reservation_blocks_dispatch(self):
        extra = '''mkdir() {
          /bin/mkdir "$@"
          if [ "$1" = "$root/active" ]; then /bin/mkdir "$root/recovery-in-progress"; fi
        }'''
        result = self.shell('remote_main "$root" prepare_job j-new s-test 0 next/session', extra=extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "jobs/j-new").exists())
        self.assertTrue((self.root / "recovery-in-progress").exists())


if __name__ == "__main__":
    unittest.main()
