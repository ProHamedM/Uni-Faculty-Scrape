# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

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
