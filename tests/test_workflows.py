from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/macbook-minion/scripts"))
import workflows
import minion


class WorkflowTests(unittest.TestCase):
    def test_certificate_bundle_rejects_secret_and_malformed_material(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bundle"
            for content in ("private data", "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----", "-----BEGIN CERTIFICATE-----\ninvalid\n-----END CERTIFICATE-----"):
                path.write_text(content)
                with self.assertRaises((ValueError, workflows.ssl.SSLError)):
                    workflows.certificate_bundle(path)

    def test_large_output_reserve_uses_each_workers_own_floor(self):
        args = SimpleNamespace(config=Path("unused"), session_state=None, worker=None)
        rows = [{"id": "a", "min_free_gib": 25}, {"id": "b", "min_free_gib": 50}]
        with patch.object(workflows.fleet, "overview", return_value={"workers": rows}), \
             patch.object(workflows.pressure, "sample_primary", return_value={}), \
             patch.object(workflows.pressure, "route", return_value={}) as route:
            workflows.placement(args, {}, reserve=20)
            self.assertEqual([w["min_free_gib"] + 5 for w in route.call_args.args[1]], [45, 70])

    def test_parser_routes_new_actions(self):
        args = minion.parser().parse_args(["--worker", "intel", "research", "plan.json",
                    "--owner", "p/session", "--title", "Research", "--description", "Collect evidence"])
        self.assertEqual(args.worker, "intel")
        self.assertEqual(args.plan, Path("plan.json"))

    def test_local_or_defer_never_transfers_or_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            script = source / "job.sh"
            script.write_text("true\n")
            args = SimpleNamespace(owner="p/session", title="Test", description="Portable test")
            for choice in ("local", "defer"):
                with patch.object(workflows, "placement", return_value={"decision": choice}), \
                     patch.object(minion, "put") as put, patch.object(workflows.control, "submit") as submit:
                    result = workflows.submit(args, source, script, {})
                    self.assertFalse(result["submitted"])
                    put.assert_not_called()
                    submit.assert_not_called()

    def test_offload_keeps_selected_worker_snapshot_and_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            script = source / "job.sh"
            script.write_text("true\n")
            args = SimpleNamespace(config=source/"config.json", owner="p/session", title="Test", description="Test")
            decision = {"decision": "offload", "worker_id": "intel", "reasons": ["disk pressure"]}
            config = {"host": "test", "min_free_gib": 25}
            with patch.object(workflows, "placement", return_value=decision), \
                 patch.object(minion, "load_config", return_value=config), \
                 patch.object(minion, "put", return_value={"snapshot": "s-test"}) as put, \
                 patch.object(workflows.control, "submit", return_value={"job": "j-test", "state": "queued"}) as submit:
                result = workflows.submit(args, source, script, {})
                self.assertEqual(result["worker"], "intel")
                self.assertFalse(result["verified_complete"])
                self.assertTrue(result["submitted"])
                self.assertEqual(submit.call_args.args[1], "s-test")
                self.assertIs(put.call_args.args[0], config)


if __name__ == "__main__":
    unittest.main()
