import contextlib
import io
import os
import unittest

from tests.helpers import ROOT
from unifaculty.cli import EXIT_CONFIG, EXIT_OK, main
from unifaculty.config import load_profile, load_settings, load_universities


@contextlib.contextmanager
def in_repo_root():
    old = os.getcwd()
    os.chdir(ROOT)
    try:
        yield
    finally:
        os.chdir(old)


class ShippedConfigTests(unittest.TestCase):
    def test_seed_universities_validate(self):
        unis = load_universities(ROOT / "config" / "universities")
        self.assertEqual(sorted(unis), ["goethe-frankfurt", "stanford", "univie"])
        for uni in unis.values():
            self.assertGreaterEqual(uni.min_delay_seconds, 2.0)
            self.assertTrue(uni.seeds_verified)

    def test_example_profile_and_settings_validate(self):
        profile = load_profile(ROOT / "config" / "profile.example.yaml")
        self.assertIn("phd", profile.target_degree_levels)
        settings = load_settings(ROOT / "config" / "settings.example.yaml")
        self.assertEqual(settings.crawl.fetcher, "curl_cffi")

    def test_frankfurt_uses_crawl_delay_5(self):
        unis = load_universities(ROOT / "config" / "universities")
        self.assertEqual(unis["goethe-frankfurt"].min_delay_seconds, 5)


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with in_repo_root(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_universities_lists_seeds(self):
        code, out, _ = self.run_cli("universities")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("fb12.uni-frankfurt.de", out)

    def test_unknown_university_is_config_error(self):
        code, _, err = self.run_cli("run", "-u", "nowhere", "--profile", "config/profile.example.yaml", "--llm", "mock")
        self.assertEqual(code, EXIT_CONFIG)
        self.assertIn("unknown university", err)

    def test_missing_profile_is_config_error(self):
        code, _, err = self.run_cli("run", "-u", "stanford", "--profile", "does/not/exist.yaml", "--llm", "mock")
        self.assertEqual(code, EXIT_CONFIG)
        self.assertIn("not found", err)


if __name__ == "__main__":
    unittest.main()
