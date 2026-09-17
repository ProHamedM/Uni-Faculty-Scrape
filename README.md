# Uni Faculty Scraper

**Find professors who have an open, funded PhD / postdoc position in *your* field — and skip the dead ends.**

A polite crawler reads a university's department pages, doctoral-school pages and job portals. An LLM then keeps only the pages where a named professor is recruiting **right now**, the position is **funded** (fully or partially), and the research actually **fits your profile**. Every claim the LLM makes is checked against the page before it reaches your CSV.

Built for students everywhere — especially those who can't afford to spend weeks on dead-end funding leads.

[![CI](https://github.com/ProHamedM/Uni-Faculty-Scrape/actions/workflows/ci.yml/badge.svg)](https://github.com/ProHamedM/Uni-Faculty-Scrape/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-Apache--2.0-green)

> **Status: Phase 1 (v0.1.1).** Core crawler, compliance guardrails, LLM filter, CSV output and logging are implemented and covered by an offline test suite. Live validation on the three seed universities is the next step — see [Roadmap](#roadmap).

---

## How it works

```
 config/universities/*.yaml          config/profile.yaml
 (seeds + allowed domains)           (your fields, degree level, thresholds)
            │                                    │
            ▼                                    │
 ┌─────────────────────┐   robots.txt (RFC 9309), 2s+ per host, stop on block
 │  Crawler            │── curl_cffi (one consistent browser profile) ───────► university pages
 │  scoped · priority  │
 └─────────┬───────────┘
           │ parsed pages (text, emails, profile links, EN/DE signals)
           ▼
 ┌─────────────────────┐
 │  Prefilter          │  cheap keyword gate — only decides what is worth an LLM call
 └─────────┬───────────┘
           ▼
 ┌─────────────────────┐   Gemini · OpenRouter · Anthropic · Ollama (local)
 │  LLM classifier     │── "is there an open, funded position that fits this profile?"
 └─────────┬───────────┘
           ▼
 ┌─────────────────────┐   quotes must appear on the page · names must appear on the page
 │  Verify + rules     │   emails must be on the page · expired / off-field / unfunded → dropped
 └─────────┬───────────┘
           ▼
 output/<university>/latest.csv        runs/<run_id>/  (run.log, events.jsonl, llm/, rejected.csv, summary.json)
```

## What the tool will never do

These are enforced in code (`src/unifaculty/compliance.py`, `robots.py`) and have **no switch to turn them off**:

| Never | How it's enforced |
|---|---|
| Ignore robots.txt | RFC 9309 longest-match with `*` / `$`. robots.txt unreachable (5xx, network error) = host fully disallowed. |
| Push through a block | HTTP 401/403/407/451 or a CAPTCHA/challenge page stops that host for the run. A second 429 does too. |
| Rotate IPs, proxies, headers or fingerprints | One client, one profile, all run. No proxy-rotation feature exists. |
| Crawl fast | ≥ 2 s between requests to a host, one at a time; `Crawl-delay` honored (raised, never lowered). |
| Log in, submit forms, pass paywalls | GET requests only; no credentials are ever sent to universities. |
| Leave the university | Only hosts under the config's `allowed_domains` are fetched; LinkedIn/GitHub links are recorded, never visited. |
| Guess or enrich contact details | Emails must be printed on the official page; nothing is looked up elsewhere. |
| Publish scraped data | CSVs stay on your machine; `output/` and `runs/` are git-ignored. |

Why `curl_cffi` then? Many university CDNs reject non-browser TLS stacks even on ordinary public pages. A browser-grade client makes those pages load; it is **not** used to get past a deliberate block. Read [ACCEPTABLE_USE.md](ACCEPTABLE_USE.md) before running the tool.

## Quick start

### Windows (PowerShell)

```powershell
git clone https://github.com/ProHamedM/Uni-Faculty-Scrape.git
cd Uni-Faculty-Scrape

python -m venv .venv
.\.venv\Scripts\Activate.ps1          # prompt shows (.venv); cmd.exe: .venv\Scripts\activate.bat
python -m pip install -e ".[dev]"

Copy-Item config\profile.example.yaml config\profile.yaml   # then edit it: YOUR research interests
Copy-Item .env.example .env                                 # then edit it: ONE LLM key (or use Ollama)
notepad .env

unifaculty doctor                                           # checks Python, modules, key, configs, secret hygiene
unifaculty universities                                     # what's configured
unifaculty run -u univie --llm mock --max-pages 20 -v       # dry run: real crawl, fake LLM, no key needed
unifaculty run -u univie -v                                 # the real thing
```

If activation fails with *"running scripts is disabled on this system"*, allow local scripts for your user once, then activate again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

`Copy-Item` (or its alias `cp`) needs both a **source** and a **destination**: `cp config\profile.yaml` alone copies nothing.

### macOS / Linux

```bash
git clone https://github.com/ProHamedM/Uni-Faculty-Scrape.git
cd Uni-Faculty-Scrape
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e ".[dev]"
cp config/profile.example.yaml config/profile.yaml
cp .env.example .env
unifaculty doctor
unifaculty run -u univie --llm mock --max-pages 20 -v
```

Results land in `output\<university>\latest.csv`; everything that happened is in `runs\<run_id>\`.

## Keeping your API key safe

Your key is a password that spends your quota (or money). The repo is public, so one careless `git add .` publishes it.

**Do**

* Put the key **only** in `.env` in the project folder. It is git-ignored; confirm with `git check-ignore -v .env` (it should print the `.gitignore` line).
* Or keep it out of files entirely for one session: `$env:GEMINI_API_KEY = "…"` (PowerShell) / `export GEMINI_API_KEY=…` (bash).
* Run `unifaculty doctor` before committing: it fails if `.env` is tracked or a key-shaped string sits in any file git would commit.
* Install the pre-commit hooks once, so gitleaks blocks a commit that contains a key:
  ```powershell
  python -m pip install pre-commit
  pre-commit install
  ```
* Turn on GitHub **push protection** (repository *Settings → Code security*), so GitHub rejects a push that contains a known key format.
* Limit the damage a leaked key can do: restrict a Gemini key to the Generative Language API in Google Cloud, and set a credit limit on OpenRouter keys.

**Don't**

* Paste keys into code, tests, `config/*.yaml` (the tool refuses to load a config containing one), notebooks, screenshots, issues or shared run logs.
* Write "fake" keys that look real in tests — build them at runtime instead (see `tests/helpers.py`). Key-shaped placeholders trigger secret-scanning alerts and train people to paste keys into code.

**If a real key was committed or pushed**

1. **Revoke / regenerate it at the provider first** (Google AI Studio, OpenRouter, Anthropic console). Deleting the commit is not enough: forks, clones and caches keep it.
2. Put the new key in `.env`, never in the repo.
3. Optionally scrub history (`git filter-repo`) — only after revoking.
4. Close the GitHub alert as revoked. For test data that was never a real key, close it with the reason for test data.

The test suite contains `tests/test_no_secrets.py`, and CI runs gitleaks on every push and pull request.

## Choosing an LLM provider

| Provider | `--llm` | Key (`.env`) | Default model | Notes |
|---|---|---|---|---|
| Google Gemini | `gemini` | `GEMINI_API_KEY` | `gemini-3.5-flash-lite` | Free tier via AI Studio; [not offered in every country](https://ai.google.dev/gemini-api/docs/available-regions). |
| OpenRouter | `openrouter` | `OPENROUTER_API_KEY` | `openrouter/free` | Free models have per-minute and daily caps. |
| Anthropic | `anthropic` | `ANTHROPIC_API_KEY` | `claude-haiku-4-5-20251001` | Paid API. |
| Ollama (local) | `ollama` | — | `llama3.1:8b` | Runs on your machine; no key, no region limits. Use any instruct model you've pulled. |
| Any OpenAI-compatible | `openai_compat` | `OPENAI_API_KEY` | set `llm.model` | Set `llm.base_url` too. |
| Mock | `mock` | — | — | Offline dry run for testing the pipeline and logs. Not real filtering. |

Model names change; override with `--model` or `llm.model` in `config/settings.yaml` (copy it from `config\settings.example.yaml`). Keys never go in that file — see [Keeping your API key safe](#keeping-your-api-key-safe). Please don't wire in unofficial "free API" wrappers that reuse someone's web-app session — they break the provider's terms, can get accounts banned, and leak session cookies to third-party code.

## The CSV

One row per professor per university (a professor found on a profile page *and* a job ad is merged into one row).

| Column | Meaning |
|---|---|
| `name`, `title_role`, `department` | Supervisor or contact named on the page |
| `research_interests`, `matched_topics` | Topics, and which of them matched your profile |
| `degree_levels`, `position_title`, `deadline`, `start_date` | The opening |
| `funding_status`, `funding_source` | `full` / `partial` (or `unclear` if you allow it), plus grant/contract when stated |
| `email` | Only if printed on the page |
| `linkedin`, `github`, `google_scholar`, `orcid`, `researchgate`, `dblp`, `semantic_scholar`, `x`, `bluesky` | Only links the page itself contains |
| `field_match_score`, `funding_confidence`, `rationale` | The LLM's scores (0–1) and one-line reasoning |
| `funding_evidence`, `position_evidence` | Verbatim quotes, verified to exist on the page |
| `source_urls`, `last_checked_utc`, `llm_provider`, `llm_model`, `run_id` | Provenance |

The file is UTF-8 with BOM (umlauts open correctly in Excel) and neutralizes spreadsheet formula injection.

## Logging, tracing and debugging

Every run writes a folder you can grep, `jq` or send in a bug report (API keys are redacted everywhere):

```
runs/20260917T140512Z-univie-3f9a/
├── run.log        human-readable, DEBUG (TRACE with --trace), with span ids
├── events.jsonl   one JSON event per line: robots.*, fetch.*, parse.*, prefilter.*, llm.*, guard.*, candidate.*
├── llm/           one file per LLM call: decision, verification, tokens, latency (full prompt + reply with --trace)
├── pages/         raw HTML of every fetched page (--debug-dump)
├── rejected.csv   every candidate page that was dropped, and why
└── summary.json   counts, HTTP status histogram, timings (avg/p95), token use, stopped hosts
```

| Flag | Effect |
|---|---|
| `-v` / `-vv` | Console at DEBUG / TRACE (every robots check, link, wait) |
| `-q` | Warnings and errors only |
| `--trace` | Store full prompts and raw LLM replies; TRACE level in log files |
| `--debug-dump` | Save raw HTML so you can see exactly what the parser saw |
| `--no-cache` | Ignore cached LLM answers (cache lives in `.cache/llm/`) |

Handy commands (PowerShell):

```powershell
$run = (Get-ChildItem runs | Sort-Object LastWriteTime | Select-Object -Last 1).FullName   # newest run
unifaculty summary $run                                            # totals, status codes, top reject reasons
unifaculty robots https://informatik.univie.ac.at/ueber-uns/subeinheiten    # what robots.txt says, and why

$events = Get-Content "$run\events.jsonl" -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json }
$events | Where-Object { $_.level -in 'WARNING','ERROR' } | Format-Table ts, event, url, reason, hint -Wrap
$events | Where-Object event -eq 'llm.classify.end' | Format-Table keep, field_score, funding, url -AutoSize
```

On macOS/Linux the same with `jq`: see [docs/LOGGING.md](docs/LOGGING.md).

More in [docs/LOGGING.md](docs/LOGGING.md).

## Configuration

* `config/profile.yaml` — your target fields, degree levels, keywords (EN/DE), excluded topics, thresholds, and which department tags to crawl.
* `config/settings.yaml` — provider, model, rate limits, LLM call budget, fetcher, prefilter mode. Every key is optional ([example](config/settings.example.yaml)).
* `config/universities/<slug>.yaml` — `allowed_domains`, seed `sections` (department / jobs / doctoral_school), page and depth caps, delay. See [CONTRIBUTING.md](CONTRIBUTING.md#adding-a-university).

### Seed universities (checked 2026-09-17)

| Slug | Where it starts | robots.txt notes |
|---|---|---|
| `stanford` | CS faculty by research area (AI, Robotics) | Open for faculty pages. US funding is mostly program-level, so expect "looking for students" profile pages more than job ads. |
| `goethe-frankfurt` | FB 12 Stellenportal, Promotion page | `fb12.uni-frankfurt.de` sets `Crawl-delay: 5`. `www.uni-frankfurt.de` disallows almost all numeric page ids. `informatik.uni-frankfurt.de` returned HTTP 500 for robots.txt → skipped per RFC 9309. |
| `univie` | CS research groups, Doctoral School (DoCS), job portal | Open; PhD roles are usually "University assistant predoctoral" contracts. |

## Project layout

```
src/unifaculty/
  cli.py          run · robots · doctor · universities · summary
  pipeline.py     crawl → prefilter → classify → verify → merge → CSV
  crawler.py      scoped priority crawl, per-URL gate (scope, robots, delay, guard)
  robots.py       RFC 9309 parser/matcher and per-origin cache
  compliance.py   hard guardrails: delay floor, stop statuses, challenge detection
  fetcher.py      curl_cffi (default) and httpx fetchers
  extract.py      text, links, emails (incl. obfuscated / Cloudflare), profile links, page kind
  signals.py      English + German position/funding/field keywords, link scoring
  classify.py     prompt, JSON parsing, evidence verification, keep/drop rules, LLM call log, cache
  llm/            gemini · openai-compatible (OpenRouter, Ollama) · anthropic · mock
  logs.py         run folder, console/text/JSONL sinks, redaction
  secretscan.py   finds key-shaped strings in the repo and configs (doctor, tests, config loading)
  tracing.py      nested timed spans, run statistics
config/           settings, profile and university examples
tests/            offline suite: fictional university, fake fetcher, mock LLM, mocked HTTP APIs
skills/           Phase 2: agent skill(s)
docs/             architecture and logging details
```

## Development

```powershell
python -m pip install -e ".[dev]"
pre-commit install        # gitleaks + ruff before every commit
pytest                    # or: python -m unittest discover -s tests -t .
ruff check src tests
```

The whole suite runs offline in about a second: no network, no API keys. It includes a scan of the repository for committed secrets.

## Roadmap

**Phase 1 — core logic (this release).** Crawler, guardrails, LLM filter with verification, CSV, logging. Next: live validation runs on Stanford, Goethe Frankfurt and Uni Vienna, then tuning prompts, keywords and thresholds from `rejected.csv`.

**Phase 1.x.** PDF job ads, sitemap-assisted discovery, per-university parsers for structured directories, a small local web viewer for the CSVs.

**Phase 2 — agent skill.** Package the pipeline as a uniform skill an LLM can run through an agent harness: choosing seeds, reading run logs, adjusting settings, and explaining results — with the same guardrails underneath. See [skills/README.md](skills/README.md).

## Contributing

Adding a university is the most useful contribution and takes one YAML file — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License and disclaimer

Apache License 2.0 — see [LICENSE](LICENSE).

This tool reads public web pages. You are responsible for how you use it: respect each site's terms, local law (including data-protection rules such as the GDPR when handling names and emails of people in the EU), and the people behind the addresses you collect — write to professors individually and thoughtfully, never in bulk. Nothing here is legal advice.
