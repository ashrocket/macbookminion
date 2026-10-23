from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/macbook-minion/scripts"))
import audit
import control
import dashboard
import minion
import test_minion as worker_fixture


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"host": "test-worker", "_state_dir": self.temp.name}
        self.store = audit.store_for(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_concurrent_writers_preserve_linked_audit(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda n: self.store.event("sender", "controller", "test", str(n)), range(40)))
        self.assertEqual(self.store.verify()["events"], 40)
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        with self.store.db() as db:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM events WHERE seq=1")
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE events SET payload='{}' WHERE seq=1")

    def test_manual_tampering_is_detected(self):
        self.store.event("sender", "controller", "test", "original")
        with self.store.db() as db:
            db.execute("DROP TRIGGER immutable_events_update")
            db.execute("UPDATE events SET payload='{}' WHERE seq=1")
        with self.assertRaisesRegex(ValueError, "chain mismatch"):
            self.store.verify()

    def test_transport_audits_requests_and_replies_without_raw_output(self):
        private = "token=DO_NOT_STORE_THIS stdout and shell contents"
        with patch.object(minion, "execute", return_value=private):
            self.assertEqual(minion.remote(self.config, "status", "j-test"), private)
        events = self.store.events()
        self.assertEqual([e["kind"] for e in events], ["rpc.send", "rpc.reply"])
        self.assertEqual(events[0]["request_id"], events[1]["request_id"])
        self.assertEqual(events[1]["source"], "worker")
        self.assertNotIn("DO_NOT_STORE_THIS", json.dumps(events))
        with patch.object(minion, "execute", side_effect=minion.MinionError(private)):
            with self.assertRaises(minion.MinionError):
                minion.remote(self.config, "launch", "j-test")
        self.assertEqual(self.store.events()[-1]["kind"], "rpc.error")
        self.assertNotIn("DO_NOT_STORE_THIS", json.dumps(self.store.events()))

    def test_worker_events_are_deduplicated(self):
        for _ in range(3):
            self.store.event("worker", "controller", "started", "Started", event_key="j-1:started")
        self.assertEqual(self.store.verify()["events"], 1)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.f = worker_fixture.WorkerTests()
        self.f.setUp()
        self.t = worker_fixture.minion
        self.config = self.f.config
        self.config["_state_dir"] = str(self.f.base / "controller")
        self.store = audit.store_for(self.config)
        self.coordinator = control.Coordinator(self.config, self.t)
        self.snapshot = self.t.put(self.config, self.f.source())["snapshot"]

    def tearDown(self):
        self.f.tearDown()

    def submit(self, name, script):
        path = self.f.base / (name + ".sh")
        path.write_text(script)
        return control.submit(self.config, self.snapshot, path, 1, "test/queue", title=name,
                              description="Readable purpose for " + name, transport=self.t, start=False)["job"]

    def finish(self, job):
        self.f.wait_finished(job)
        self.coordinator.sync()
        return self.store.job(job)

    def test_fifo_execution_progress_descriptions_and_result(self):
        first = self.submit("First job", 'minion_note "Reading input"\ncat input.txt > "$MINION_OUTPUT_DIR/result.txt"\nsleep 0.3\nminion_note "Done"\n')
        second = self.submit("Second job", 'printf "Second job ran\\n"\n')
        self.coordinator.tick()
        self.assertEqual((self.f.root / "active/job").read_text().strip(), first)
        self.assertEqual(self.store.job(second)["state"], "queued")
        self.assertEqual(self.finish(first)["state"], "succeeded")
        self.coordinator.tick()
        self.assertEqual(self.finish(second)["state"], "succeeded")
        metadata = json.loads((self.f.root / "jobs" / first / "metadata.json").read_text())
        self.assertEqual(metadata["description"], "Readable purpose for First job")
        notes = [e["summary"] for e in self.store.events(first) if e["kind"] == "worker.note"]
        self.assertEqual(notes, ["Reading input", "Done"])
        count = self.store.verify()["events"]
        self.coordinator.sync()
        self.assertEqual(self.store.verify()["events"], count)

    def test_restart_after_lost_launch_reply_does_not_repeat_job(self):
        job = self.submit("Lost reply", 'printf "one\\n" >> "$MINION_OUTPUT_DIR/count.txt"\nsleep 0.2\n')
        real = self.t.remote
        launches = []
        def lost(config, action, *args, **kwargs):
            result = real(config, action, *args, **kwargs)
            if action == "launch":
                launches.append(args[0])
                raise self.t.MinionError("Simulated lost SSH reply after launch")
            return result
        with patch.object(self.t, "remote", side_effect=lost):
            self.coordinator.tick()
        self.assertEqual(self.store.job(job)["state"], "uncertain")
        restarted = control.Coordinator(self.config, self.t)
        restarted.tick()
        self.assertEqual(self.finish(job)["state"], "succeeded")
        self.assertEqual(launches, [job])
        self.assertEqual((self.f.root / "jobs" / job / "output/count.txt").read_text(), "one\n")

    def test_interrupted_upload_resumes_same_job_before_launch(self):
        job = self.submit("Recover staging", 'echo done\n')
        upload = self.t.upload_file
        def interrupt(config, local, suffix):
            if local.name == "runner.sh":
                raise self.t.MinionError("Simulated transfer loss")
            return upload(config, local, suffix)
        with patch.object(self.t, "upload_file", side_effect=interrupt):
            self.coordinator.tick()
        self.assertFalse((self.f.root / "jobs" / job / "launched").exists())
        control.Coordinator(self.config, self.t).tick()
        self.assertEqual(self.finish(job)["state"], "succeeded")
        self.assertEqual(len(list((self.f.root / "jobs").iterdir())), 1)

    def test_offline_does_not_dispatch_or_mark_job_failed(self):
        job = self.submit("Offline wait", 'echo done\n')
        with patch.object(self.t, "remote", side_effect=self.t.MinionError("offline")):
            self.assertFalse(self.coordinator.tick())
        self.assertEqual(self.store.job(job)["state"], "queued")
        self.assertFalse(self.store.state("worker")["online"])
        self.assertFalse((self.f.root / "active").exists())

    def test_cancel_and_changed_spool_are_not_dispatched(self):
        cancelled = self.submit("Cancel me", 'echo no\n')
        self.store.cancel(cancelled)
        job = self.submit("Changed script", 'echo expected\n')
        (self.store.root / "spool" / job / "command.sh").write_text("echo tampered\n")
        self.coordinator.tick()
        self.assertEqual(self.store.job(job)["state"], "attention")
        self.assertEqual(self.store.job(cancelled)["state"], "cancelled")
        self.assertFalse((self.f.root / "active").exists())

    def test_pending_queue_survives_new_store_instance(self):
        job = self.submit("Saved job", 'echo persisted\n')
        reopened = audit.Store(self.store.root)
        self.assertEqual(reopened.job(job)["state"], "queued")
        self.assertEqual(reopened.job(job)["metadata"]["title"], "Saved job")
        self.assertTrue(reopened.verify()["valid"])

    def test_repeated_staging_failure_stops_after_three_attempts(self):
        job = self.submit("Broken transfer", 'echo done\n')
        with patch.object(self.t, "upload_file", side_effect=self.t.MinionError("offline")) as upload:
            for _ in range(6):
                self.coordinator.tick()
        self.assertEqual(upload.call_count, 3)
        self.assertEqual(self.store.job(job)["state"], "attention")
        self.assertFalse((self.f.root / "jobs" / job / "launched").exists())

    def test_retained_remote_lock_blocks_next_job(self):
        first = self.submit("Cleanup needs review", 'touch "$(dirname "$MINION_OUTPUT_DIR")/retain_lock"\n')
        second = self.submit("Wait for cleanup", 'echo later\n')
        self.coordinator.tick()
        deadline = time.monotonic() + 5
        while not (self.f.root / "jobs" / first / "exit_code").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.coordinator.sync()
        self.assertEqual(self.store.job(first)["state"], "attention")
        self.coordinator.tick()
        self.assertEqual(self.store.job(second)["state"], "queued")
        self.assertEqual((self.f.root / "active/job").read_text().strip(), first)

    def test_launch_without_confirmed_process_holds_queue_for_review(self):
        first = self.submit("Interrupted launch", 'echo must not repeat\n')
        second = self.submit("Waiting job", 'echo later\n')
        self.store.claim()
        self.t.remote(self.config, "prepare_job", first, self.snapshot, 1, "test/queue")
        (self.f.root / "jobs" / first / "launched").mkdir()
        with patch.object(self.t, "upload_file") as upload:
            self.coordinator.tick()
        upload.assert_not_called()
        self.assertEqual(self.store.job(first)["state"], "attention")
        self.assertIn("will not be launched again", self.store.job(first)["error"])
        self.assertEqual(self.store.job(second)["state"], "queued")

    def test_concurrent_submissions_and_claims_are_unique_and_ordered(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            jobs = list(pool.map(lambda n: self.submit("Sender " + str(n), 'echo saved\n'), range(12)))
        self.assertEqual(len(set(jobs)), 12)
        expected = [row["id"] for row in self.store.jobs()]
        with ThreadPoolExecutor(max_workers=6) as pool:
            claimed = list(pool.map(lambda _: self.store.claim()["id"], range(12)))
        self.assertEqual(set(claimed), set(jobs))
        dispatched = [e["job"] for e in self.store.events() if e["kind"] == "job.dispatching"]
        self.assertEqual(dispatched, expected)
        self.assertIsNone(self.store.claim())
        self.assertTrue(self.store.verify()["valid"])


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"host": "fake-worker", "_state_dir": self.temp.name}
        self.server = dashboard.make_server(self.config, "test-private-token", "test-instance", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, **kwargs):
        return urllib.request.urlopen(urllib.request.Request(self.base + path, **kwargs))

    def test_private_read_only_local_endpoint_and_csp(self):
        with self.request("/test-private-token/") as response:
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            self.assertIn("MacBook Minion", response.read().decode())
        with self.request("/test-private-token/api/health") as response:
            self.assertEqual(json.load(response)["instance"], "test-instance")
        for path, kwargs, status in [("/api/state", {}, 404),
            ("/test-private-token/api/state", {"headers": {"Host": "evil.example"}}, 403),
            ("/test-private-token/api/state", {"headers": {"Origin": "https://evil.example"}}, 403),
            ("/test-private-token/api/state", {"method": "POST", "data": b"{}"}, 405)]:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request(path, **kwargs)
            self.assertEqual(raised.exception.code, status)
            raised.exception.close()


if __name__ == "__main__":
    unittest.main()
