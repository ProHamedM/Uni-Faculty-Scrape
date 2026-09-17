# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's private vulnerability reporting ("Report a vulnerability" under the repository's *Security* tab). Include steps to reproduce and the affected version. You can expect an acknowledgement within a week.

## In scope

* Leaks of API keys or other secrets (logs, cache, CSVs, error messages).
* Ways to make the crawler ignore robots.txt, scope, delays or stop-on-block rules through configuration or crafted web content.
* Prompt injection that causes unverified data (names, emails, funding claims) to reach the CSV.
* CSV/formula injection, path traversal via config or page content, unsafe YAML loading.

## If you leaked an API key

1. **Revoke or regenerate the key at the provider immediately** — Google AI Studio, OpenRouter, Anthropic console. Once a key has been pushed to a public repository, treat it as compromised, even if you delete the commit a minute later: forks, clones, caches and scanners may already have it.
2. Check the provider's usage page for activity you don't recognize.
3. Put the new key in `.env` (git-ignored) or a session environment variable — never in code, tests or YAML.
4. Remove the old value from the repository. Rewriting history (`git filter-repo`) is optional and only useful after the key is revoked.
5. Close the GitHub secret-scanning alert as revoked.

Test values that were never real keys: replace them with values built at runtime (`tests/helpers.py`) and close the alert with the reason for test data.

## Preventing leaks

* `.env`, `.env.*` (except `.env.example`), `config/settings.yaml` and `config/profile.yaml` are git-ignored.
* Config files that contain a key-shaped value or a credential field (`api_key`, `token`, `secret`, `password`) are refused at load time.
* `unifaculty doctor` fails if `.env` is tracked or any file git would commit contains a key-shaped string.
* `tests/test_no_secrets.py` scans the repository in every test run; CI also runs gitleaks on the commits of every push and pull request.
* `pre-commit install` runs gitleaks before every local commit.
* Enable GitHub push protection for the repository so known key formats are rejected at push time.

## Design notes

* API keys are read from the environment or `.env`, sent only in request headers, and redacted from every log sink (`logs.redact`).
* YAML is loaded with `yaml.safe_load`; configs are validated with pydantic.
* Page text is passed to the LLM as untrusted data; outputs are validated and verified against the page before use.
* CSV cells starting with `=`, `+`, `-`, `@` are neutralized.
* Run folders may contain raw page HTML (`--debug-dump`) and full prompts (`--trace`). Treat `runs/` as private.
