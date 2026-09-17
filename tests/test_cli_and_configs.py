import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT, fake_google_key
from unifaculty.cli import EXIT_CONFIG, EXIT_OK, main
from unifaculty.config import ConfigError, load_dotenv, load_profile, load_settings, load_universities


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


class SecretHandlingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("GEMINI_API_KEY", None)

    def test_key_pasted_into_settings_is_refused(self):
        path = self.dir / "settings.yaml"
        path.write_text(f"llm:\n  provider: gemini\n  model: {fake_google_key()}\n", encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_settings(path)
        self.assertIn("Google API key", str(ctx.exception))
        self.assertNotIn(fake_google_key(), str(ctx.exception))

    def test_credential_field_in_settings_is_refused(self):
        path = self.dir / "settings.yaml"
        path.write_text("llm:\n  provider: openrouter\n  api_key: something\n", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_settings(path)

    def test_token_limits_are_not_mistaken_for_credentials(self):
        path = self.dir / "settings.yaml"
        path.write_text("llm:\n  max_output_tokens: 1024\n", encoding="utf-8")
        self.assertEqual(load_settings(path).llm.max_output_tokens, 1024)

    def test_dotenv_saved_with_bom_by_windows_notepad(self):
        os.environ.pop("GEMINI_API_KEY", None)
        path = self.dir / ".env"
        path.write_bytes("﻿GEMINI_API_KEY=abc123\r\n# comment\r\n".encode("utf-8"))
        self.assertEqual(load_dotenv(path), ["GEMINI_API_KEY"])
        self.assertEqual(os.environ["GEMINI_API_KEY"], "abc123")


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
