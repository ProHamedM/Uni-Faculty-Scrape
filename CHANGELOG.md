# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.1] — 2026-09-17

### Security
- Removed the Google-API-key-shaped test literal that triggered a GitHub secret-scanning alert (it was never a real key). Tests now build key-shaped values at runtime (`tests/helpers.py`).
- New `unifaculty.secretscan`: `tests/test_no_secrets.py` fails if a key-shaped string or a committed `.env` file is in the repository.
- Config files containing a key-shaped value or a credential field are refused at load time.
- `unifaculty doctor` checks that `.env` is not tracked, scans files git would commit, reports where the key came from (never any part of it), and warns about placeholder keys.
- CI: gitleaks job on every push and pull request. Added `.pre-commit-config.yaml` (gitleaks, detect-private-key, ruff, repository secret scan).
- README / SECURITY.md / CONTRIBUTING.md: how to keep keys safe and what to do after a leak.

### Changed
- Quick start and log recipes are Windows (PowerShell) first, with macOS/Linux equivalents.
- `.env` and YAML files saved with a UTF-8 byte-order mark (Windows Notepad) now load correctly.
- Console logging never crashes on characters a legacy Windows code page can't encode.
- CI runs Windows first in the matrix.

## [0.1.0] — 2026-09-17

### Added
- Phase 1 core: scoped priority crawler, RFC 9309 robots.txt engine, politeness floor, stop-on-block guard.
- curl_cffi fetcher (one consistent browser profile) and an honest httpx fallback.
- Extraction of page text, emails (incl. bracket-obfuscated and Cloudflare-protected), academic profile links, English/German position and funding signals.
- LLM classifier with providers for Gemini, OpenRouter / Ollama / OpenAI-compatible endpoints, Anthropic, and an offline mock.
- Code-side verification: evidence quotes, names and emails must be on the page; rules for expiry, degree level, funding status and field fit.
- Per-university CSV with merging, Excel-friendly encoding and formula-injection protection.
- Run folders with `run.log`, `events.jsonl`, per-call LLM logs, `rejected.csv`, `summary.json`; `--trace`, `--debug-dump`, secret redaction.
- CLI: `run`, `robots`, `doctor`, `universities`, `summary`.
- Seed configs for Stanford, Goethe University Frankfurt and the University of Vienna.
- Offline test suite (82 tests), CI workflow, contribution and acceptable-use docs.
