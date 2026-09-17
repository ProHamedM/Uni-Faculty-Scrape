import unittest

from tests.helpers import fixture_text
from unifaculty.extract import classify_profile_link, decode_cfemail, parse_html
from unifaculty.signals import score_link, text_signals
from unifaculty.urls import host_in_scope, normalize_url


class UrlTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_url("../people/./jane?utm_source=x&id=3#top", "https://WWW.Example.edu:443/cs/a/"),
                         "https://www.example.edu/cs/people/jane?id=3")
        self.assertIsNone(normalize_url("mailto:a@b.edu"))
        self.assertIsNone(normalize_url("javascript:void(0)"))

    def test_scope(self):
        self.assertTrue(host_in_scope("www.fb12.uni-frankfurt.de", ["uni-frankfurt.de"]))
        self.assertFalse(host_in_scope("uni-frankfurt.de.evil.com", ["uni-frankfurt.de"]))
        self.assertFalse(host_in_scope("notuni-frankfurt.de", ["uni-frankfurt.de"]))


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.page = parse_html(fixture_text("site/jane.html"), "https://www.example-university.edu/cs/people/jane-example",
                               ["model compression", "robot learning"])

    def test_emails_including_obfuscated(self):
        self.assertEqual(self.page.emails[0], "jane.example@example-university.edu")
        self.assertIn("office@example-university.edu", self.page.emails)

    def test_profile_links(self):
        self.assertEqual(self.page.profile_links["github"], ["https://github.com/jane-example"])
        self.assertIn("linkedin", self.page.profile_links)
        self.assertIn("google_scholar", self.page.profile_links)

    def test_kind_and_signals(self):
        self.assertEqual(self.page.kind, "profile")
        self.assertIn("fully funded", self.page.signals.funding_terms)
        self.assertIn("model compression", self.page.signals.field_terms)

    def test_html_comments_are_not_page_text(self):
        self.assertNotIn("Ignore previous instructions", self.page.text)

    def test_listing_detection(self):
        page = parse_html(fixture_text("site/people.html"), "https://www.example-university.edu/cs/people")
        self.assertEqual(page.kind, "listing")

    def test_position_detection(self):
        page = parse_html(fixture_text("site/job.html"), "https://www.example-university.edu/jobs/phd-robot-learning")
        self.assertEqual(page.kind, "position")

    def test_cfemail(self):
        # "a@b.de" XOR-encoded with key 0x42
        key = 0x42
        encoded = f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in "a@b.de")
        self.assertEqual(decode_cfemail(encoded), "a@b.de")

    def test_meta_robots(self):
        page = parse_html('<html><head><meta name="robots" content="noindex, nofollow"></head><body>x</body></html>',
                          "https://x.edu/")
        self.assertTrue(page.noindex and page.nofollow)

    def test_profile_link_classifier_rejects_non_profiles(self):
        self.assertIsNone(classify_profile_link("https://github.com/features/actions"))
        self.assertIsNone(classify_profile_link("https://www.linkedin.com/company/example"))
        self.assertEqual(classify_profile_link("https://orcid.org/0000-0002-1825-0097"), "orcid")

    def test_german_signals(self):
        s = text_signals("Wir suchen eine*n Wissenschaftliche Mitarbeiterin (Doktorandin), Vergütung nach E13 TV-G-U.")
        self.assertTrue(s.has_position)
        self.assertTrue(s.has_funding)

    def test_link_scoring_prefers_jobs_and_people(self):
        jobs = score_link("https://x.edu/jobs/phd", "Open PhD positions", [])
        news = score_link("https://x.edu/news/2026/party", "Summer party", [])
        self.assertGreater(jobs, news)


if __name__ == "__main__":
    unittest.main()
