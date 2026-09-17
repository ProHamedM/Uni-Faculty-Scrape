"""Guard rail for the repository itself: no key-shaped strings may be committed.

If this test fails, do NOT just edit the string: if it was a real key, revoke it at the provider first.
Fake keys for tests must be assembled at runtime (tests/helpers.py: fake_google_key, fake_token).
"""

import unittest

from tests.helpers import ROOT, fake_google_key, fake_token
from unifaculty.secretscan import scan_env_file, scan_repo, scan_text


class RepositoryHasNoSecretsTest(unittest.TestCase):
    def test_repository_is_clean(self):
        findings = scan_repo(ROOT)
        self.assertEqual([], [str(f) for f in findings],
                         "Key-shaped strings found. Revoke real keys at the provider, then remove them from the repo.")


class ScannerSelfTest(unittest.TestCase):
    def test_detects_runtime_built_google_key(self):
        self.assertEqual(scan_text(f"GEMINI_API_KEY={fake_google_key()}")[0].kind, "Google API key")

    def test_detects_openrouter_key(self):
        self.assertTrue(scan_text("sk-or-v1-" + "0a" * 32))

    def test_ignores_ordinary_code(self):
        self.assertEqual(scan_text('os.environ.get("GEMINI_API_KEY")\nkey = fake_token(26)'), [])

    def test_env_example_values_must_be_empty(self):
        self.assertEqual(scan_env_file("GEMINI_API_KEY=\nOPENROUTER_API_KEY=", ".env.example"), [])
        self.assertTrue(scan_env_file(f"GEMINI_API_KEY={fake_token(12)}", ".env.example"))

    def test_finding_never_prints_the_secret(self):
        key = fake_google_key()
        self.assertNotIn(key, str(scan_text(key, "x.py")[0]))


if __name__ == "__main__":
    unittest.main()
