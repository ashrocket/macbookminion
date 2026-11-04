"""Local Bash fixtures for the full-history worker overview wire protocol."""
import base64
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest


REMOTE = Path(__file__).resolve().parents[1] / "skills/macbook-minion/scripts/remote.sh"
NOW = "2026-11-04T12:34:56Z"


class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "worker space"
        (self.root / "jobs").mkdir(parents=True)
        (self.root / "snapshots").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def shell(self, action="overview", extra="", timeout=10):
        script = REMOTE.read_text() + "\n"
        script += "date() { printf '%s\\n' " + shlex.quote(NOW) + "; }\n"
        script += extra + "\nremote_main " + shlex.quote(str(self.root)) + " " + action + "\n"
        return subprocess.run(["/bin/bash"], input=script, text=True, capture_output=True, timeout=timeout)

    def job(self, name, files):
        path = self.root / "jobs" / name
        path.mkdir()
        for field, value in files.items():
            (path / field).write_bytes(value if isinstance(value, bytes) else value.encode())
        return path

    def encoded(self, value):
        return base64.b64encode(value.encode()).decode()

    def test_missing_and_empty_scalars_keep_original_defaults_and_record_order(self):
        self.job("j-missing", {})
        self.job("j-empty", {"owner": "", "snapshot": "\n\n", "worker_events.tsv": ""})
        result = self.shell()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         "active_job=\nworker_at=" + NOW + "\n\n"
                         "job=j-empty\nowner=\nsnapshot=\ncreated_at=\nstarted_at=\nfinished_at=\n"
                         "worker_events_b64=\nstate=staged_or_interrupted\n\n"
                         "job=j-missing\nowner=unknown\nsnapshot=\ncreated_at=\nstarted_at=\nfinished_at=\n"
                         "state=staged_or_interrupted\n\n")

    def test_finished_job_preserves_metadata_events_and_scalar_whitespace(self):
        metadata = '{"id":"j-finished","description":"' + "wide value " * 100 + '"}\n'
        events = NOW + "\taccepted\t\n" + NOW + "\tnote\t" + self.encoded("café\nnext line") + "\n"
        self.job("j-finished", {"owner": "  session\\owner\t \n\n", "snapshot": "s-first\nsecond\n\n",
                               "created_at": NOW, "started_at": NOW + "\n", "finished_at": NOW + "\n\n",
                               "metadata.json": metadata, "worker_events.tsv": events,
                               "exit_code": "1\n\n", "launched": "", "retain_lock": ""})
        result = self.shell("status j-finished")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         "job=j-finished\nowner=  session\\owner\t \nsnapshot=s-first\nsecond\n"
                         "created_at=" + NOW + "\nstarted_at=" + NOW + "\nfinished_at=" + NOW + "\n"
                         "launched=yes\nmetadata_b64=" + self.encoded(metadata) + "\n"
                         "worker_events_b64=" + self.encoded(events) + "\n"
                         "lock_retained=yes\nstate=finished\nexit_code=1\n")

    def test_running_interrupted_and_recovered_states_remain_distinct(self):
        self.job("j-running", {"pid": "123\n", "process_start": "saved process\n"})
        self.job("j-interrupted", {"pid": "456\n", "process_start": "old process\n"})
        self.job("j-recovered", {"exit_code": "1\n", "retain_lock": "", "lock_recovered": NOW})
        result = self.shell(extra="ps() { printf 'saved process\\n'; }")
        self.assertEqual(result.returncode, 0, result.stderr)
        blocks = result.stdout.strip().split("\n\n")
        self.assertTrue(blocks[1].endswith("state=interrupted"))
        self.assertIn("lock_recovered=yes\nstate=finished\nexit_code=1", blocks[2])
        self.assertNotIn("lock_retained", blocks[2])
        self.assertTrue(blocks[3].endswith("state=running\npid=123"))

    def test_invalid_pid_and_symlink_job_still_fail(self):
        self.job("j-invalid", {"pid": "123; false\n", "process_start": "saved\n"})
        result = self.shell("status j-invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid saved PID", result.stderr)
        (self.root / "jobs/j-symlink").symlink_to(self.root / "jobs/j-invalid", target_is_directory=True)
        result = self.shell("status j-symlink")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe or missing directory", result.stderr)

    def test_scalar_nuls_are_removed_without_losing_following_bytes(self):
        self.job("j-nuls", {"owner": b"\x00session\x00/owner\x00\n\n",
                            "snapshot": b"s-first\n\x00second\n\x00\n",
                            "created_at": b"\x00\x00", "started_at": b"start\x00end",
                            "finished_at": b"finished\x00\n", "exit_code": b"1\x002\x00\n"})
        result = self.shell("status j-nuls")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout,
                         "job=j-nuls\nowner=session/owner\nsnapshot=s-first\nsecond\n"
                         "created_at=\nstarted_at=startend\nfinished_at=finished\n"
                         "state=finished\nexit_code=12\n")

    def test_nul_inside_malformed_pid_does_not_accept_numeric_prefix(self):
        self.job("j-nul-pid", {"pid": b"123\x00junk\n", "process_start": "saved process\n"})
        result = self.shell("status j-nul-pid", extra="ps() { printf 'must not inspect this PID\\n' >&2; exit 94; }")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid saved PID", result.stderr)
        self.assertNotIn("must not inspect", result.stderr)

    def test_recovery_reservation_stays_in_overview_header(self):
        (self.root / "recovery-in-progress").mkdir()
        result = self.shell()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "active_job=pending-recovery\nworker_at=" + NOW + "\n\n")
        (self.root / "recovery-in-progress/job").write_text("j-held\n")
        result = self.shell()
        self.assertTrue(result.stdout.startswith("active_job=j-held\n"))

    def test_large_overview_keeps_all_history_without_scalar_cat_or_tr_processes(self):
        count = 100
        metadata = '{"description":"' + "complete metadata " * 100 + '"}\n'
        events = "".join(NOW + "\tnote\t" + self.encoded("event " + str(i)) + "\n" for i in range(20))
        for i in range(count):
            self.job("j-%03d" % i, {"owner": "project/session-%d\n" % i, "snapshot": "s-old\n",
                                    "created_at": NOW, "started_at": NOW, "finished_at": NOW,
                                    "exit_code": "1\n", "retain_lock": "", "metadata.json": metadata,
                                    "worker_events.tsv": events})
        # The overview header may read its one active marker. Scalar job reads
        # and base64 newline removal must not invoke these external utilities.
        extra = '''cat() {
          if [ "$1" = "$root/active/job" ]; then command cat "$@"; else
            printf 'unexpected scalar cat\\n' >&2; exit 91
          fi
        }
        tr() { printf 'unexpected tr\\n' >&2; exit 92; }'''
        started = time.monotonic()
        result = self.shell(extra=extra, timeout=10)
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        blocks = result.stdout.strip().split("\n\n")
        self.assertEqual(len(blocks), count + 1)
        for i, block in enumerate(blocks[1:]):
            self.assertTrue(block.startswith("job=j-%03d\nowner=project/session-%d\n" % (i, i)))
            self.assertIn("metadata_b64=" + self.encoded(metadata) + "\n", block)
            self.assertIn("worker_events_b64=" + self.encoded(events) + "\n", block)
            self.assertTrue(block.endswith("lock_retained=yes\nstate=finished\nexit_code=1"))
        self.assertLess(elapsed, 10)


if __name__ == "__main__":
    unittest.main()
