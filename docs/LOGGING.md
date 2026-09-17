# Logging, tracing and debugging

Every `unifaculty run` creates `runs/<run_id>/`. The run id is `<UTC timestamp>-<label>-<4 hex>`, e.g. `20260917T140512Z-univie-3f9a`, and appears on every log line.

## Sinks

| File | Level | Format | Use it for |
|---|---|---|---|
| console (stderr) | INFO (`-v` DEBUG, `-vv` TRACE, `-q` WARNING) | text | watching a run |
| `run.log` | DEBUG (TRACE with `--trace`) | text with `<span_id>` | reading a run top to bottom |
| `events.jsonl` | DEBUG (TRACE with `--trace`) | one JSON object per line | filtering with `jq`, bug reports |
| `llm/NNNN-<hash>.json` | every LLM decision | JSON | why a page was kept or dropped |
| `pages/<university>/*.html` + `index.jsonl` | `--debug-dump` only | raw HTML | what the parser actually saw |
| `rejected.csv` | — | CSV | every dropped candidate with reasons |
| `summary.json` | — | JSON | totals, status codes, timings, tokens, stopped hosts |

A text line looks like:

```
14:05:12.345 INFO    [univie] fetch.end elapsed_ms=812.4 url=https://informatik.univie.ac.at/... depth=1 http_status=200 bytes=48211 content_type=text/html
```

A JSONL event looks like:

```json
{"ts": "2026-09-17T11:05:12.345Z", "level": "INFO", "event": "llm.classify.end", "logger": "unifaculty.classify",
 "run_id": "20260917T110510Z-univie-3f9a", "university": "univie", "span_id": "a1b2c3d4e5f6", "parent_span_id": "0f9e8d7c6b5a",
 "elapsed_ms": 1432.1, "url": "https://...", "provider": "gemini", "model": "gemini-3.5-flash-lite", "cache": "miss",
 "input_tokens": 3120, "output_tokens": 240, "keep": false, "reasons": ["funding_unclear"], "funding": "unclear", "field_score": 0.82}
```

## Spans

Timed operations are spans: `university` → `robots.fetch` / `fetch` → `parse` → `llm.classify`. Each span logs `<name>.start` (TRACE) and `<name>.end` with `elapsed_ms`, or `<name>.error` with a traceback. `span_id` / `parent_span_id` let you rebuild the tree.

## Event reference

| Event | Level | Meaning |
|---|---|---|
| `run.start`, `run.done`, `run.university_done` | INFO | run lifecycle and per-university totals |
| `crawl.seeded`, `crawl.finished`, `crawl.nothing_fetched` | INFO / WARNING | crawl lifecycle; the last one explains why nothing was fetched |
| `robots.loaded` | INFO / WARNING | robots.txt status, mode (`parsed`, `allow_all`, `disallow_all`), rule count, crawl-delay |
| `robots.host_closed` | WARNING | robots.txt unreachable → host fully disallowed |
| `robots.check` | TRACE | allow/deny for one URL with the matching rule |
| `robots.disallowed` | DEBUG | URL skipped by robots.txt (`rule=` shows the line) |
| `ratelimit.wait`, `ratelimit.pause` | TRACE / INFO | politeness waits; pauses after 429/5xx |
| `fetch.end` | DEBUG (WARNING on 0/401/403/429/5xx) | one HTTP GET: status, bytes, content type, redirects, error |
| `guard.host_stopped` | WARNING | host stopped for the run (403, challenge page, repeated 429, server errors) |
| `skip.*` | TRACE–INFO | `non_html_url`, `out_of_scope`, `host_stopped`, `http_error`, `non_html`, `noindex`, `nofollow_links`, `redirect_out_of_scope` |
| `frontier.push`, `frontier.links_added` | TRACE | link discovery and priority scores |
| `parse.end` | DEBUG | page kind, title, text size, emails found, profile links, keyword signals |
| `prefilter.decision` | DEBUG | whether a page goes to the LLM, and why |
| `llm.classify.end` | INFO | decision, scores, verification results, cache hit/miss, tokens, latency |
| `llm.retry`, `llm.failed`, `llm.rpm_wait`, `llm.budget_exhausted` | WARNING / ERROR / DEBUG | provider trouble, with a `hint=` |
| `verify.hallucination_blocked` | WARNING | LLM named a person or email not on the page — removed |
| `candidate.kept`, `candidate.rejected` | INFO / DEBUG | final outcome per page |

## Recipes

### Windows (PowerShell)

```powershell
$run = (Get-ChildItem runs | Sort-Object LastWriteTime | Select-Object -Last 1).FullName   # newest run
$events = Get-Content "$run\events.jsonl" -Encoding UTF8 | ForEach-Object { $_ | ConvertFrom-Json }

# Everything that needs attention
$events | Where-Object { $_.level -in 'WARNING','ERROR' } | Format-Table ts, event, url, origin, reason, error, hint -Wrap

# Why was a specific page dropped?
$events | Where-Object url -eq 'https://...' | Format-List event, kind, classify, reason, keep, reasons, quotes_ok

# Slowest fetches
$events | Where-Object event -eq 'fetch.end' | Sort-Object elapsed_ms -Descending | Select-Object -First 10 elapsed_ms, http_status, url

# Which robots.txt rules blocked what
$events | Where-Object event -eq 'robots.disallowed' | Group-Object rule | Sort-Object Count -Descending | Format-Table Count, Name

# LLM decisions at a glance
$events | Where-Object event -eq 'llm.classify.end' | Format-Table keep, field_score, funding, funding_conf, cache, url -AutoSize

# Live tail of a running crawl (second terminal)
Get-Content "$run\run.log" -Wait -Tail 20

# Top reject reasons
unifaculty summary $run
```

`Import-Csv "$run\rejected.csv" | Out-GridView` opens the dropped candidates in a sortable window.

### macOS / Linux (jq)

```bash
RUN=$(ls -td runs/*/ | head -1)

jq -r 'select(.level=="WARNING" or .level=="ERROR") | [.ts, .event, .url // .origin // "", .reason // .error // "", .hint // ""] | @tsv' $RUN/events.jsonl
jq 'select(.url=="https://...") | {event, kind, classify, reason, keep, reasons, quotes_ok}' $RUN/events.jsonl
jq -r 'select(.event=="fetch.end") | [.elapsed_ms, .http_status, .url] | @tsv' $RUN/events.jsonl | sort -rn | head
jq -r 'select(.event=="robots.disallowed") | [.rule // .reason, .url] | @tsv' $RUN/events.jsonl | sort | uniq -c | sort -rn
jq -r 'select(.event=="llm.classify.end") | [.keep, .field_score, .funding, .funding_conf, .cache, .url] | @tsv' $RUN/events.jsonl
tail -f $RUN/run.log
unifaculty summary $RUN
```

## Secrets

`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` values, and common key patterns (`AIza…`, `sk-or-v1-…`, `sk-ant-…`, `Bearer …`), are replaced with `<redacted>` in every sink, including `llm/` files. Keys are only ever sent in request headers. Redaction is a safety net: still review a run folder before attaching it to an issue, and never share `.env`.

## Filing a bug

Attach `summary.json`, the relevant `events.jsonl` lines, and (for filter problems) the matching `llm/*.json`. Leave out CSVs and anything with personal data you don't need to show the problem.
