import unittest

from tests.helpers import fixture_text  # noqa: F401  (sets sys.path)
from unifaculty.robots import RobotsCache, RobotsPolicy, parse_robots

FRANKFURT_LIKE = """
User-agent: MJ12bot
Disallow: /

User-agent: *
Allow: /93095219
Allow: /*?page=
Disallow: /admin/
Disallow: /*&*
Disallow: /1
Disallow: /6
Disallow: /9
"""


def policy(text: str, origin: str = "https://www.example.edu") -> RobotsPolicy:
    groups, sitemaps = parse_robots(text)
    return RobotsPolicy(origin, groups, sitemaps, status=200)


class RobotsMatchingTests(unittest.TestCase):
    def test_longest_match_wins_over_order(self):
        p = policy(FRANKFURT_LIKE)
        self.assertTrue(p.check("https://www.example.edu/93095219/Faculty_page").allowed)
        d = p.check("https://www.example.edu/65709683/Aktuelle_Ausschreibungen")
        self.assertFalse(d.allowed)
        self.assertEqual(d.rule, "Disallow: /6")

    def test_wildcards_and_query(self):
        p = policy(FRANKFURT_LIKE)
        self.assertFalse(p.check("https://www.example.edu/news?x=1&y=2").allowed)
        self.assertTrue(p.check("https://www.example.edu/news?page=2").allowed)

    def test_allow_wins_on_equal_length(self):
        p = policy("User-agent: *\nDisallow: /folder\nAllow: /folder\n")
        self.assertTrue(p.check("https://www.example.edu/folder/page").allowed)

    def test_dollar_anchor(self):
        p = policy("User-agent: *\nDisallow: /*.php$\n")
        self.assertFalse(p.check("https://www.example.edu/index.php").allowed)
        self.assertTrue(p.check("https://www.example.edu/index.php?lang=en").allowed)

    def test_named_group_takes_precedence_and_groups_merge(self):
        text = ("User-agent: *\nDisallow: /\n\n"
                "User-agent: UniFacultyScraper\nDisallow: /internal/\n\n"
                "User-agent: unifacultyscraper\nCrawl-delay: 7\n")
        p = policy(text)
        self.assertEqual(p.group_label, "UniFacultyScraper")
        self.assertTrue(p.check("https://www.example.edu/people").allowed)
        self.assertFalse(p.check("https://www.example.edu/internal/x").allowed)
        self.assertEqual(p.crawl_delay, 7)

    def test_multiple_agents_share_a_group(self):
        text = "User-agent: foo\nUser-agent: *\nDisallow: /private/\n"
        self.assertFalse(policy(text).check("https://www.example.edu/private/a").allowed)

    def test_empty_disallow_allows_everything(self):
        p = policy("User-agent: *\nDisallow:\n")
        self.assertTrue(p.check("https://www.example.edu/anything").allowed)

    def test_percent_encoding_is_normalized(self):
        p = policy("User-agent: *\nDisallow: /~jane/\n")
        self.assertFalse(p.check("https://www.example.edu/%7Ejane/cv").allowed)

    def test_crawl_delay_parsed(self):
        self.assertEqual(policy("User-agent: *\nCrawl-delay: 5\n").crawl_delay, 5.0)


class RobotsFetchOutcomeTests(unittest.TestCase):
    def cache(self, status: int, text: str = "", error: str | None = None) -> RobotsCache:
        return RobotsCache(lambda url: (status, text, error))

    def test_404_allows_all(self):
        self.assertTrue(self.cache(404).check("https://a.example.edu/x").allowed)

    def test_500_disallows_all(self):
        d = self.cache(500).check("https://informatik.example.edu/")
        self.assertFalse(d.allowed)
        self.assertIn("unreachable", d.reason)

    def test_network_error_disallows_all(self):
        self.assertFalse(self.cache(0, error="timeout").check("https://a.example.edu/x").allowed)

    def test_403_disallows_all(self):
        self.assertFalse(self.cache(403).check("https://a.example.edu/x").allowed)

    def test_fetched_once_per_origin(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return 200, "User-agent: *\nDisallow: /p/\n", None

        cache = RobotsCache(fetch)
        cache.check("https://a.example.edu/p/1")
        cache.check("https://a.example.edu/q/2")
        cache.check("https://b.example.edu/p/1")
        self.assertEqual(calls, ["https://a.example.edu/robots.txt", "https://b.example.edu/robots.txt"])


if __name__ == "__main__":
    unittest.main()
