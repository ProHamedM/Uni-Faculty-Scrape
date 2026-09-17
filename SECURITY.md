# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's private vulnerability reporting ("Report a vulnerability" under the repository's *Security* tab). Include steps to reproduce and the affected version. You can expect an acknowledgement within a week.

## In scope

* Leaks of API keys or other secrets (logs, cache, CSVs, error messages).
* Ways to make the crawler ignore robots.txt, scope, delays or stop-on-block rules through configuration or crafted web content.
* Prompt injection that causes unverified data (names, emails, funding claims) to reach the CSV.
* CSV/formula injection, path traversal via config or page content, unsafe YAML loading.

## Design notes

* API keys are read from the environment or `.env`, sent only in request headers, and redacted from every log sink (`logs.redact`).
* YAML is loaded with `yaml.safe_load`; configs are validated with pydantic.
* Page text is passed to the LLM as untrusted data; outputs are validated and verified against the page before use.
* CSV cells starting with `=`, `+`, `-`, `@` are neutralized.
* Run folders may contain raw page HTML (`--debug-dump`) and full prompts (`--trace`). Treat `runs/` as private.
