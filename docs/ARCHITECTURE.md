# Architecture

## Goals, in priority order

1. **Never cause harm to the sites or people involved** — guardrails are code constants, not options.
2. **No garbage in the CSV** — the LLM judges funding and fit; code verifies everything the LLM claims.
3. **Explainable** — every skip, keep and drop is logged with a reason.
4. **Cheap to run** — keyword prefilter, LLM cache, per-run call budget, free-tier-friendly defaults.

## Data flow

```
cli.cmd_run
  └─ pipeline.run_university(uni, profile, settings, ctx, fetcher, provider)
       ├─ Crawler.seed(sections)                     sections chosen by profile.department_tags (+ all jobs/doctoral pages)
       ├─ for page in Crawler.crawl():               priority queue by score_link(); per-URL gate:
       │     scope → host stopped? → robots (RFC 9309) → delay → GET → DomainGuard → html? → parse → noindex?
       ├─ prefilter.should_classify(page)            cost control only
       ├─ Classifier.classify(page)
       │     build prompt (profile + page meta + untrusted page text)
       │     cache lookup (sha256 of prompt version, provider, model, system prompt, message)
       │     RequestsPerMinute → provider.complete_json() with retries
       │     extract_json_object → LLMDecision (pydantic, clamped, normalized)
       │     verify(): quotes on page, names on page, emails ∈ extracted emails
       │     apply_rules(): position, degree level, funding status + evidence, confidence, expiry, field fit, exclusions
       │     write llm/NNNN.json
       ├─ records_from_outcome → merge_records (email, then title-stripped name)
       └─ write output/<slug>/<slug>__<field>__<stamp>.csv + latest.csv, runs/<id>/rejected.csv
  └─ pipeline.write_summary → runs/<id>/summary.json
```

## Key decisions

**Own robots.txt engine.** `urllib.robotparser` uses first-match semantics and ignores wildcards, which gives wrong answers on real university files (e.g. `Allow: /93095219` inside `Disallow: /9`). `robots.py` implements RFC 9309 longest-match, `*`/`$`, group merging and the fetch-status rules, and is unit-tested against those cases.

**Sequential crawl.** One request at a time per university. Politeness delays (≥ 2 s, often 3–5 s) dominate runtime anyway, and a sequential loop keeps logs linear and easy to read. Universities could run in parallel in a later version because they are different hosts.

**LLM proposes, code disposes.** Free and small models hallucinate. The classifier asks for verbatim evidence quotes and restricts emails to those already extracted; `verify()` rejects anything that isn't on the page. Thresholds live in the user's profile.

**Prompt injection.** Page text is wrapped in `<<<PAGE_TEXT … PAGE_TEXT>>>`, the system prompt tells the model it is data, HTML comments are stripped before extraction, and verification means an injected "mark as funded" still needs a real funding sentence on the page.

**Provider-agnostic HTTP.** Providers are thin `httpx` clients (no SDKs) so the dependency list stays short and every request can be logged uniformly. Ollama support matters for users in countries where hosted APIs aren't offered.

**Browser-grade TLS, single identity.** `curl_cffi` makes ordinary public pages load behind CDNs that reject Python's default TLS fingerprint. The session is created once per university with one profile; there is no rotation code path, and a block stops the host.

## Extending

| Want to… | Touch |
|---|---|
| add a university | `config/universities/*.yaml` |
| add a language's keywords | `signals.py` (and a test in `tests/test_extract.py`) |
| add an LLM provider | `llm/providers.py`, `llm/factory.py`, a `tests/test_llm_providers.py` case |
| change the prompt | new `prompts/classify_vN.txt`, bump `PROMPT_VERSION` |
| add a CSV column | `models.py` (`CSV_COLUMNS`, `FacultyRecord`), `pipeline.records_from_outcome`, README table |
| support PDFs | `fetcher.is_html` / a new parser in `extract.py`, prefilter, tests |

## Phase 2 sketch

The pipeline's pieces become tools behind a skill: `list_universities`, `check_robots(url)`, `run_university(slug, profile, limits)`, `read_run_summary(run_id)`, `explain_rejection(run_id, url)`. The agent chooses and tunes; the guardrails stay inside the tools, so no prompt can switch them off. See `skills/README.md`.
