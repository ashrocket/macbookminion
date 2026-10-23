import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/macbook-minion/scripts"))
from browser_plan import validate_plan
import browser_worker
import minion


class BrowserPlanTests(unittest.TestCase):
    def plan(self, *steps):
        return {"steps": [{"action": "open", "url": "https://example.org"}, *steps]}

    def test_rejects_credentials_and_non_web_schemes(self):
        for url in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "https://user:pass@example.org", "https://example.org:bad", "https://a\n.example.org"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_plan({"steps": [{"action": "open", "url": url}]})

    def test_rejects_unbounded_work_and_raw_browser_settings(self):
        for plan in ({"steps": []}, {**self.plan(), "timeout_seconds": 9999},
                     {**self.plan(), "profile": "/Users/test/profile"},
                     self.plan({"action": "evaluate", "script": "code"}),
                     self.plan({"action": "fill", "selector": "#x", "text": "a", "secret": True})):
            with self.assertRaises(ValueError):
                validate_plan(plan)

    def test_artifact_names_cannot_escape_or_overwrite(self):
        for name in ("../x", "foo/bar", "failure", "UPPER"):
            with self.assertRaises(ValueError):
                validate_plan(self.plan({"action": "capture", "name": name}))
        with self.assertRaises(ValueError):
            validate_plan(self.plan(*[{"action": "capture", "name": "same"}] * 2))

    def test_example_is_a_valid_bounded_plan(self):
        example = Path(__file__).resolve().parents[1] / "examples/browser-smoke.json"
        self.assertEqual(len(validate_plan(json.loads(example.read_text()))["steps"]), 7)

    def test_ambiguous_target_is_not_clicked(self):
        driver = browser_worker.Driver(1)
        with patch.object(driver, "command", return_value=[{}, {}]):
            with self.assertRaises(browser_worker.WebDriverError):
                driver.element("button")

    def test_password_and_upload_fields_are_rejected_before_typing(self):
        driver = browser_worker.Driver(1)
        for kind in ("password", "file", "hidden"):
            with patch.object(driver, "element", return_value="/element/test"), patch.object(driver, "command", return_value=kind) as call:
                with self.assertRaises(RuntimeError):
                    browser_worker.perform(driver, {"action": "fill", "selector": "input", "text": "test"}, Path("unused"))
                self.assertEqual(call.call_count, 1)

    def test_browser_submission_uses_shared_runner(self):
        info = "python3=/usr/local/bin/python3\nchrome_version=150.0.7871.125\ndriver_installed=yes\nactive_job=\n"
        captured = {}
        def put(config, source):
            captured["plan"] = json.loads(source.joinpath("plan.json").read_text())
            captured["files"] = set(p.name for p in source.iterdir())
            return {"snapshot": "s-browser"}
        with patch.object(minion, "remote", return_value=info), patch.object(minion, "put", side_effect=put), patch.object(minion, "run_job", return_value={"job": "j-browser"}) as run:
            result = minion.browser_job({}, "test/owner", plan=self.plan())
        self.assertEqual(run.call_args.args[1], "s-browser")
        self.assertEqual(run.call_args.args[4], "test/owner")
        self.assertEqual(result["job"], "j-browser")
        self.assertEqual(captured["files"], {"plan.json", "browser_worker.py", "browser_plan.py"})

    def test_busy_browser_does_not_download_or_submit(self):
        info = "python3=python3\nchrome_version=150.0.7871.125\nactive_job=j-other\n"
        with patch.object(minion, "remote", return_value=info), patch.object(minion, "put") as put:
            with self.assertRaises(minion.MinionError):
                minion.browser_job({}, "test/owner", setup=True)
            put.assert_not_called()

    def test_missing_driver_produces_failure_report_without_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "output"
            output.mkdir()
            with patch.dict("os.environ", {"MINION_JOB_ID": "j-test"}), patch.object(browser_worker.subprocess, "Popen") as launch:
                result = browser_worker.run(root, output, validate_plan(self.plan()))
            self.assertEqual(result, 1)
            launch.assert_not_called()
            self.assertFalse(json.loads(output.joinpath("report.json").read_text())["success"])


if __name__ == "__main__":
    unittest.main()
