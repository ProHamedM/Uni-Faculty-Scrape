"""End-to-end: fictional university served from memory, mock LLM, real crawler/guards/logging/CSV."""

import csv
import io
import json
import tempfile
import unittest
from collections import defaultdict
from datetime import date
from pathlib import Path

from tests.helpers import FIXTURES, FakeClock, FakeFetcher, fixture_text, html_route
from unifaculty.config import Settings, load_profile, load_university
from unifaculty.llm.mock import MockProvider
from unifaculty.logs import close_logging, setup_logging
from unifaculty.pipeline import run_university, write_summary
from unifaculty.ratelimit import HostRateLimiter, RequestsPerMinute
from unifaculty.urls import host_of

WWW = "https://www.example-university.edu"
LAB = "https://lab.example-university.edu"


def routes():
    return {
        f"{WWW}/robots.txt": (200, {"content-type": "text/plain"}, fixture_text("site/robots.txt")),
        f"{WWW}/cs/people": html_route("people.html"),
        f"{WWW}/cs/people/jane-example": html_route("jane.html"),
        f"{WWW}/cs/people/john-sample": html_route("john.html"),
        f"{WWW}/cs/people/mara-offfield": html_route("mara.html"),
        f"{WWW}/jobs/phd-robot-learning": html_route("job.html"),
        f"{WWW}/private/salary-list": html_route("jane.html"),   # must never be requested
        f"{LAB}/team": (403, {"content-type": "text/html"}, fixture_text("site/challenge.html")),
        f"{LAB}/projects": html_route("jane.html"),               # must never be requested
    }


class OfflinePipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        cls.clock = FakeClock()
        cls.fetcher = FakeFetcher(routes(), clock=cls.clock)
        cls.request_times = defaultdict(list)
        original_get = cls.fetcher.get

        def timed_get(url):
            cls.request_times[host_of(url)].append(cls.clock.now)
            return original_get(url)

        cls.fetcher.get = timed_get
        settings = Settings(output_dir=str(tmp / "output"), runs_dir=str(tmp / "runs"), cache_dir=str(tmp / "cache"))
        settings.llm.provider = "mock"
        settings.crawl.min_text_chars = 150
        cls.uni = load_university(FIXTURES / "universities" / "example.yaml")
        cls.profile = load_profile(FIXTURES / "profile.yaml")
        cls.ctx = setup_logging(Path(settings.runs_dir), "offline", quiet=True, trace=True, debug_dump=True,
                                console_stream=io.StringIO())
        provider = MockProvider(sleep=cls.clock.sleep)
        limiter = HostRateLimiter(clock=cls.clock.time, sleep=cls.clock.sleep, jitter=0)
        rpm = RequestsPerMinute(1000, clock=cls.clock.time, sleep=cls.clock.sleep)
        cls.result = run_university(cls.uni, cls.profile, settings, cls.ctx, cls.fetcher, provider, limiter=limiter,
                                    rpm=rpm, today=date(2026, 9, 17), output_dir=Path(settings.output_dir))
        write_summary(cls.ctx, [cls.result], cls.profile, settings, provider, "fake")
        close_logging()
        cls.rows = list(csv.DictReader(io.StringIO(
            (Path(settings.output_dir) / "example-university" / "latest.csv").read_text(encoding="utf-8-sig"))))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_only_the_funded_on_field_professor_is_kept(self):
        self.assertEqual([r["name"] for r in self.rows], ["Jane Example"])
        jane = self.rows[0]
        self.assertEqual(jane["email"], "jane.example@example-university.edu")
        self.assertEqual(jane["funding_status"], "full")
        self.assertIn("phd", jane["degree_levels"])
        self.assertIn("https://github.com/jane-example", jane["github"])
        self.assertIn(f"{WWW}/jobs/phd-robot-learning", jane["source_urls"])
        self.assertIn(f"{WWW}/cs/people/jane-example", jane["source_urls"])

    def test_rejections_are_explained(self):
        rejected = list(csv.DictReader(io.StringIO((self.ctx.run_dir / "rejected.csv").read_text(encoding="utf-8-sig"))))
        by_url = {r["url"]: r["reasons"] for r in rejected}
        self.assertIn("no_funding", by_url[f"{WWW}/cs/people/john-sample"])
        self.assertIn("off_field", by_url[f"{WWW}/cs/people/mara-offfield"])

    def test_robots_disallowed_pages_are_never_requested(self):
        self.assertNotIn(f"{WWW}/private/salary-list", self.fetcher.requested)
        self.assertNotIn(f"{WWW}/cs/people?print=1", self.fetcher.requested)

    def test_blocked_host_is_stopped_not_retried(self):
        self.assertIn(f"{LAB}/team", self.fetcher.requested)
        self.assertNotIn(f"{LAB}/projects", self.fetcher.requested)
        self.assertEqual(self.fetcher.requested.count(f"{LAB}/team"), 1)
        self.assertIn("lab.example-university.edu", self.result.blocked_hosts)

    def test_scope_and_file_types(self):
        self.assertFalse(any("other-university.edu" in u for u in self.fetcher.requested))
        self.assertFalse(any(u.endswith(".pdf") for u in self.fetcher.requested))
        self.assertFalse(any("linkedin.com" in u or "github.com" in u for u in self.fetcher.requested))

    def test_robots_fetched_once_per_host(self):
        self.assertEqual(self.fetcher.requested.count(f"{WWW}/robots.txt"), 1)
        self.assertEqual(self.fetcher.requested.count(f"{LAB}/robots.txt"), 1)

    def test_politeness_delays(self):
        for host, times in self.request_times.items():
            gaps = [b - a for a, b in zip(times, times[1:])]
            minimum = 3.0 if host.startswith("www.") else 2.0   # www has Crawl-delay: 3
            for gap in gaps:
                self.assertGreaterEqual(gap + 1e-6, minimum, f"{host} gaps {gaps}")

    def test_logs_trace_the_run(self):
        events = [json.loads(line) for line in (self.ctx.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        names = {e["event"] for e in events}
        for expected in ("robots.loaded", "fetch.end", "parse.end", "prefilter.decision", "llm.classify.end",
                         "guard.host_stopped", "robots.disallowed", "candidate.kept", "university.end"):
            self.assertIn(expected, names)
        self.assertTrue(all(e["run_id"] == self.ctx.run_id for e in events))
        llm_files = list((self.ctx.run_dir / "llm").glob("*.json"))
        self.assertGreaterEqual(len(llm_files), 3)
        sample = json.loads(llm_files[0].read_text(encoding="utf-8"))
        self.assertIn("user_message", sample)          # --trace stores full prompts
        self.assertTrue(any((self.ctx.run_dir / "pages" / "example-university").glob("*.html")))

    def test_summary(self):
        summary = json.loads((self.ctx.run_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["totals"]["kept"], 1)
        stats = summary["universities"][0]["stats"]
        self.assertGreaterEqual(stats["counters"]["robots.disallowed"], 2)
        self.assertIn("403", stats["http_status"])


if __name__ == "__main__":
    unittest.main()
