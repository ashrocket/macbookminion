import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/macbook-minion/scripts"))
import research_worker as research


class ResearchTests(unittest.TestCase):
    def run_plan(self, responses, **options):
        calls, sleeps, time = [], [], [0]
        def fetch(url, timeout):
            calls.append(url)
            return responses[url]
        def sleep(seconds):
            sleeps.append(seconds)
            time[0] += seconds
        report = research.crawl({"urls": ["https://public.example/start"], **options},
                                fetch=fetch, clock=lambda: time[0], sleep=sleep)
        return report, calls, sleeps

    def test_robots_rate_boundaries_and_same_origin_links(self):
        responses = {
            "https://public.example/robots.txt": (200, {}, b"User-agent: *\nDisallow: /private\nCrawl-delay: 4"),
            "https://public.example/start": (200, {"content-type": "text/html"},
                b'<a href="/private">private</a><a href="/next">next</a><a href="https://outside.example">out</a><script>hidden</script>'),
            "https://public.example/next": (200, {"content-type": "text/plain"}, b"Evidence")}
        report, calls, sleeps = self.run_plan(responses, max_depth=1)
        self.assertEqual(report["collected"], 2)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleeps, [4, 4])
        self.assertEqual(report["pages"][1]["outcome"], "robots_disallowed")
        self.assertNotIn("hidden", report["pages"][0]["text"])

    def test_robots_unavailable_fails_closed_and_no_redirect_follow(self):
        for status in (301, 403, 429, 500):
            report, calls, _ = self.run_plan({"https://public.example/robots.txt": (status, {}, b"")})
            self.assertEqual(report["collected"], 0)
            self.assertEqual(len(calls), 1)

    def test_access_limit_and_challenge_are_not_success(self):
        for status, body in ((403, b"Denied"), (429, b"Wait"), (503, b"Later"), (200, b"Verify you are human"), (302, b"")):
            report, calls, _ = self.run_plan({
                "https://public.example/robots.txt": (404, {}, b""),
                "https://public.example/start": (status, {"content-type": "text/html"}, body)})
            self.assertEqual(report["collected"], 0)
            self.assertEqual(len(calls), 2)

    def test_compressed_or_html_robots_cannot_allow_collection(self):
        for headers, body in (({"content-encoding": "gzip"}, b"compressed"),
                              ({"content-type": "text/html"}, b"Verify you are human"),
                              ({}, b"<html>challenge</html>")):
            report, calls, _ = self.run_plan({"https://public.example/robots.txt": (200, headers, body)})
            self.assertEqual(report["collected"], 0)
            self.assertEqual(len(calls), 1)

    def test_robots_retry_after_stops_even_successful_or_missing_policy(self):
        for status in (200, 404):
            report, calls, _ = self.run_plan({"https://public.example/robots.txt": (status, {"retry-after": "60"}, b"")})
            self.assertEqual(report["collected"], 0)
            self.assertEqual(len(calls), 1)

    def test_plan_bounds_and_credentials(self):
        for url in ("file:///tmp/a", "https://u:p@example.com/", "https://example.com:8888/", "https://example.com/\nx"):
            with self.assertRaises(ValueError):
                research.validate_plan({"urls": [url]})
        for options in ({"max_pages": 100}, {"delay_seconds": 0}, {"max_depth": True}, {"proxy": "x"}):
            with self.assertRaises(ValueError):
                research.validate_plan({"urls": ["https://example.com"], **options})

    def test_private_dns_is_rejected_before_connect(self):
        for address in ("127.0.0.1", "192.168.0.5", "169.254.169.254", "::1"):
            with patch.object(research.socket, "getaddrinfo", return_value=[(2, 1, 6, "", (address, 443))]), \
                    patch.object(research.socket, "create_connection") as connect:
                with self.assertRaises(ValueError):
                    research.request("https://public.example/")
                connect.assert_not_called()

    def test_deadline_respects_crawl_delay(self):
        report, calls, sleeps = self.run_plan({"https://public.example/robots.txt":
            (200, {}, b"User-agent: *\nCrawl-delay: 120")}, timeout_seconds=10)
        self.assertEqual(report["collected"], 0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(sleeps)


if __name__ == "__main__":
    unittest.main()
