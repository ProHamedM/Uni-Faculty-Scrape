import unittest

from tests.helpers import FakeClock, fixture_text
from unifaculty.compliance import MIN_DELAY_FLOOR, DomainGuard, detect_challenge, effective_delay
from unifaculty.config import UniversityConfig
from unifaculty.ratelimit import HostRateLimiter


class GuardTests(unittest.TestCase):
    def test_403_stops_host_for_good(self):
        g = DomainGuard()
        v = g.assess("lab.example.edu", 403, {}, "Forbidden", attempt=0)
        self.assertEqual(v.action, "stop_host")
        self.assertTrue(g.is_stopped("lab.example.edu"))

    def test_challenge_page_stops_host_even_with_200(self):
        g = DomainGuard()
        v = g.assess("x.example.edu", 200, {}, fixture_text("site/challenge.html"), attempt=0)
        self.assertEqual(v.action, "stop_host")
        self.assertIn("challenge", v.reason)

    def test_cf_mitigated_header(self):
        self.assertIsNotNone(detect_challenge(403, {"cf-mitigated": "challenge"}, ""))

    def test_normal_page_mentioning_captcha_word_is_fine(self):
        body = "<html><body>" + ("<p>Research on robust vision models.</p>" * 3000) + "Are you a robot? No.</body></html>"
        self.assertIsNone(detect_challenge(200, {}, body))

    def test_429_waits_once_then_stops(self):
        g = DomainGuard()
        first = g.assess("h", 429, {"retry-after": "7"}, "", attempt=0)
        self.assertEqual(first.action, "retry")
        self.assertEqual(first.wait_seconds, 7.0)
        second = g.assess("h", 429, {}, "", attempt=1)
        self.assertEqual(second.action, "stop_host")

    def test_retry_after_is_capped(self):
        v = DomainGuard().assess("h", 429, {"retry-after": "99999"}, "", attempt=0)
        self.assertLessEqual(v.wait_seconds, 120.0)

    def test_5xx_retries_then_skips_then_stops(self):
        g = DomainGuard()
        self.assertEqual(g.assess("h", 503, {}, "", 0).action, "retry")
        self.assertEqual(g.assess("h", 503, {}, "", 1).action, "retry")
        self.assertEqual(g.assess("h", 503, {}, "", 2).action, "skip_url")
        g.assess("h", 500, {}, "", 2)
        self.assertEqual(g.assess("h", 500, {}, "", 2).action, "stop_host")

    def test_404_skips_url_only(self):
        g = DomainGuard()
        self.assertEqual(g.assess("h", 404, {}, "", 0).action, "skip_url")
        self.assertIsNone(g.is_stopped("h"))


class PolitenessTests(unittest.TestCase):
    def test_effective_delay_never_below_floor(self):
        self.assertEqual(effective_delay(0.1, None), MIN_DELAY_FLOOR)
        self.assertEqual(effective_delay(2, 5), 5)

    def test_config_cannot_go_below_floor(self):
        uni = UniversityConfig(slug="x-uni", name="X", country="XX", allowed_domains=["x.edu"],
                               sections=[{"name": "s", "seeds": ["https://www.x.edu/"]}], min_delay_seconds=0.2)
        self.assertEqual(uni.min_delay_seconds, MIN_DELAY_FLOOR)

    def test_seed_outside_allowed_domains_is_rejected(self):
        with self.assertRaises(ValueError):
            UniversityConfig(slug="x-uni", name="X", country="XX", allowed_domains=["x.edu"],
                             sections=[{"name": "s", "seeds": ["https://evil.example.com/"]}])

    def test_limiter_spaces_requests_per_host(self):
        clock = FakeClock()
        limiter = HostRateLimiter(clock=clock.time, sleep=clock.sleep, jitter=0)
        limiter.wait("a", 3)
        limiter.wait("a", 3)
        limiter.wait("b", 3)   # other host: no wait
        self.assertEqual(clock.sleeps, [3.0])


if __name__ == "__main__":
    unittest.main()
