# Acceptable Use

Uni Faculty Scraper exists to help students find **publicly advertised, funded academic positions** and contact the right professor once, personally. This page states what that means in practice and what the code does to keep it that way.

## Intended use

* Reading public university web pages — faculty directories, research-group pages, doctoral-school pages, job portals.
* Producing a private shortlist (CSV) of professors with open, funded positions matching *your* research interests.
* Writing to those professors individually, with a message tailored to their work.

## Not acceptable

* Bulk or automated emailing of the collected addresses, or adding them to mailing lists.
* Selling, publishing or sharing scraped datasets (the CSVs are for the person who ran the tool).
* Crawling sites that disallow it, or modifying the code to bypass robots.txt, rate limits, blocks, CAPTCHAs, logins or paywalls.
* Adding proxy/IP rotation, fingerprint rotation, CAPTCHA solving, credential stuffing or any other evasion technique. Pull requests that do this will be closed.
* Using the tool against non-academic sites, or to profile individuals beyond their public professional role.

## Guardrails built into the code

| Guardrail | Where | Detail |
|---|---|---|
| robots.txt always obeyed | `robots.py` | RFC 9309 matching (longest match, `*`, `$`), product token `UniFacultyScraper`, `*` group otherwise. 404 → allowed; 401/403 → disallowed; 429/5xx/network error → disallowed for the run. |
| Politeness floor | `compliance.py` | `MIN_DELAY_FLOOR = 2.0` s per host, one request at a time; `Crawl-delay` honored up to 120 s (longer → host skipped). Config can only slow it down. |
| Stop on block | `compliance.py` | 401/403/407/451, a CAPTCHA/challenge page, or a second 429 stops the host for the whole run. No retry with a different identity. |
| One identity | `fetcher.py` | A single curl_cffi session with one browser profile for the run; the httpx fetcher identifies itself as `UniFacultyScraper`. |
| GET only, no credentials | `fetcher.py` | No forms, no logins, no auth headers to universities. |
| Scope lock | `crawler.py`, `config.py` | Only hosts under `allowed_domains`; seeds outside scope fail config validation; off-scope redirects are dropped. |
| `noindex` / `nofollow` | `crawler.py` | `noindex` pages are not extracted; `nofollow` pages' links are not followed (meta tag and `X-Robots-Tag`). |
| Page caps | `config.py` | `max_pages` ≤ 3000 per university per run, `max_depth` ≤ 6. |
| Data minimization | `classify.py`, `extract.py` | Only emails present on the page; names and evidence quotes must appear on the page; profile links are recorded, never fetched. |
| Local output | `.gitignore` | `output/`, `runs/`, `.cache/`, `config/profile.yaml` and `.env` are never committed. |

## Personal data

Professors' names and work emails are personal data in many jurisdictions (for example under the EU GDPR for staff of Frankfurt or Vienna). Keep the CSVs private, delete them when your search is over, and use contact details only for a genuine, individual application. If you are a professor or university and want a page or domain excluded, open an issue or add a `Disallow` rule for `UniFacultyScraper` in robots.txt — the tool obeys it on the next run.

*This document describes project policy, not legal advice.*
