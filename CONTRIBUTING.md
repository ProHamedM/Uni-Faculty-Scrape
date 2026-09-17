# Contributing

Thanks for helping other students find funded positions. The most valuable contributions, in order:

1. **Add or fix a university config** (one YAML file).
2. **Report bad filter decisions** — a funded position that was dropped, or garbage that was kept — with the run's `events.jsonl` lines and `llm/` file.
3. Improve keywords for more languages (`signals.py`), extraction, prompts, docs.

Please read [ACCEPTABLE_USE.md](ACCEPTABLE_USE.md) first. Contributions that weaken the guardrails (robots.txt, delays, stop-on-block, scope) or add evasion features will not be merged.

## Development setup

```bash
git clone https://github.com/ProHamedM/Uni-Faculty-Scrape.git
cd Uni-Faculty-Scrape
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The test suite is fully offline: a fictional university (`tests/fixtures/site/`) served by a fake fetcher, a mock LLM, and mocked HTTP for the provider clients. Keep it that way — tests must never touch the network or need API keys.

## Adding a university

1. **Pick entry points, not the homepage.** Good seeds: a department's faculty list by research area, research-group overview, doctoral-school calls/funding pages, and the job portal filtered to the faculty.
2. **Check robots.txt for every host** you plan to use:
   ```bash
   unifaculty robots https://www.example.edu/department/people
   ```
   If the path is disallowed, choose another entry point or leave that university out. Never work around it.
3. **Create `config/universities/<slug>.yaml`:**
   ```yaml
   slug: example-uni                 # lowercase, digits, hyphens
   name: Example University
   country: XX                       # ISO code
   languages: [de, en]               # sets Accept-Language
   allowed_domains:                  # hosts (and their subdomains) the crawler may fetch
     - cs.example.edu
     - jobs.example.edu
   max_pages: 250                    # ≤ 3000
   max_depth: 3                      # ≤ 6
   min_delay_seconds: 3              # ≥ 2; use the site's Crawl-delay if higher
   seeds_verified: "2026-09-17"      # date you checked the seeds
   notes: >
     robots.txt findings, how positions are advertised here, local job titles.
   sections:
     - name: Computer Science — faculty
       kind: department              # department | jobs | doctoral_school | group
       tags: [cs, ai]                # matched against profile.department_tags
       seeds:
         - https://cs.example.edu/people/faculty
     - name: Job portal
       kind: jobs                    # jobs and doctoral_school sections are always crawled
       tags: [jobs]
       seeds:
         - https://jobs.example.edu/search?faculty=cs
   ```
4. **Dry run and read the logs:**
   ```bash
   unifaculty run -u example-uni --llm mock --max-pages 30 -vv --debug-dump
   unifaculty summary runs/<run_id>
   ```
   Check `robots.disallowed`, `skip.*` and `fetch.end` events; open `runs/<id>/pages/` to see what was parsed.
5. **Real run with your own key**, then look at `rejected.csv` for positions that should have been kept.
6. Open a PR with the YAML, the robots.txt notes, and the `summary.json` totals (no CSVs, no personal data).

## Coding guidelines

* Python ≥ 3.10, type hints, `ruff check src tests` clean, line length 120.
* **Log it.** New behavior should emit structured events with `log_event(log, level, "area.event", key=value)` or run inside `span("name", log, ...)`. Use WARNING for anything a user needs to act on, and add a `hint=`.
* Never log secrets. Keys go in headers, not URLs; `logs.redact()` is a safety net, not a license.
* Keep compliance constants in `compliance.py`. Config may make the crawler slower or narrower, never faster or broader.
* LLM output is untrusted. Anything that ends up in the CSV must be verifiable against the page (see `classify.verify`).
* Prompt changes: add a new file `src/unifaculty/prompts/classify_vN.txt` and bump `PROMPT_VERSION` (it's part of the cache key).
* Add a test for every bug fix. Fixtures use fictional people and `example-university.edu` only.

## Commit and PR conventions

* Small, focused PRs. Conventional prefixes help: `feat:`, `fix:`, `docs:`, `config:`, `test:`, `refactor:`.
* Describe how you tested (unit tests, dry run, real run on which university).
* Fill in the PR template checklist.

## Reporting issues

Use the issue templates. For crawl or filter problems attach the relevant `events.jsonl` lines (`jq` examples in [docs/LOGGING.md](docs/LOGGING.md)) and the matching `llm/*.json` file. Remove any personal data you don't need to show the problem.

## Conduct

Be kind and patient — many contributors are students writing their first open-source PR, often in their second or third language. Harassment or discrimination of any kind isn't tolerated; maintainers may remove comments or contributors that break this.
