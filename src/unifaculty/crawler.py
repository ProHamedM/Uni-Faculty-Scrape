"""Scoped, polite, priority-ordered crawl of one university.

Every URL goes through the same gate, in this order, and every outcome is logged:

1. scheme/extension check     -> skip.non_html_url
2. allowed_domains scope      -> skip.out_of_scope
3. host already stopped       -> skip.host_stopped
4. robots.txt (RFC 9309)      -> robots.disallowed
5. politeness delay           -> ratelimit.wait
6. GET                        -> fetch.end
7. DomainGuard verdict        -> retry once / skip / stop host
8. content-type, noindex      -> skip.non_html / skip.noindex
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from unifaculty.compliance import MAX_HONORED_CRAWL_DELAY, DomainGuard, effective_delay
from unifaculty.config import CrawlSettings, UniversityConfig
from unifaculty.extract import ParsedPage, parse_html
from unifaculty.fetcher import FetchResult, Fetcher
from unifaculty.logs import TRACE, get_logger, log_event
from unifaculty.ratelimit import HostRateLimiter
from unifaculty.robots import RobotsCache
from unifaculty.signals import score_link
from unifaculty.tracing import Stats, span
from unifaculty.urls import host_in_scope, host_of, looks_like_html, normalize_url

log = get_logger("crawler")


@dataclass
class CrawledPage:
    url: str
    final_url: str
    depth: int
    section: str
    parsed: ParsedPage
    fetched_at: str
    http_status: int


class Crawler:
    def __init__(self, uni: UniversityConfig, fetcher: Fetcher, settings: CrawlSettings, stats: Stats,
                 field_terms: list[str], limiter: HostRateLimiter | None = None,
                 guard: DomainGuard | None = None, pages_dir: Path | None = None):
        self.uni = uni
        self.fetcher = fetcher
        self.settings = settings
        self.stats = stats
        self.field_terms = field_terms
        self.limiter = limiter or HostRateLimiter()
        self.guard = guard or DomainGuard()
        self.pages_dir = pages_dir
        self.robots = RobotsCache(self._fetch_robots_text)
        self._seen: set[str] = set()
        self._counter = itertools.count()
        self._frontier: list[tuple[int, int, int, str, str, str]] = []  # (-score, depth, n, url, section, via)
        self.pages_fetched = 0

    # ------------------------------------------------------------------ robots
    def _fetch_robots_text(self, robots_url: str) -> tuple[int, str, str | None]:
        host = host_of(robots_url)
        self.limiter.wait(host, effective_delay(self.uni.min_delay_seconds, None))
        with span("robots.fetch", log, url=robots_url) as s:
            result = self.fetcher.get(robots_url)
            s.set(http_status=result.status, bytes=result.bytes, error=result.error)
        self.stats.inc("requests.robots")
        self.stats.status_codes[str(result.status)] += 1
        return result.status, result.text if 200 <= result.status < 300 else "", result.error

    # ------------------------------------------------------------------ frontier
    def _push(self, url: str, depth: int, section: str, anchor: str = "", via: str = "") -> None:
        if url in self._seen:
            return
        self._seen.add(url)
        score = 100 if depth == 0 else score_link(url, anchor, self.field_terms)
        heapq.heappush(self._frontier, (-score, depth, next(self._counter), url, section, via))
        log_event(log, TRACE, "frontier.push", url=url, depth=depth, score=score, anchor=anchor[:80], via=via)

    def seed(self, sections: list) -> None:
        for section in sections:
            for seed in section.seeds:
                url = normalize_url(seed)
                if url:
                    self._push(url, 0, section.name)
        log_event(log, logging.INFO, "crawl.seeded", university=self.uni.slug,
                  sections=[s.name for s in sections], seeds=len(self._frontier),
                  max_pages=self.uni.max_pages, max_depth=self.uni.max_depth)

    # ------------------------------------------------------------------ main loop
    def crawl(self) -> Iterator[CrawledPage]:
        while self._frontier and self.pages_fetched < self.uni.max_pages:
            neg_score, depth, _, url, section, via = heapq.heappop(self._frontier)
            page = self._visit(url, depth, section, via, -neg_score)
            if page is not None:
                yield page
        reason = "max_pages reached" if self.pages_fetched >= self.uni.max_pages else "frontier empty"
        log_event(log, logging.INFO, "crawl.finished", reason=reason, pages_fetched=self.pages_fetched,
                  frontier_left=len(self._frontier), stopped_hosts=dict(self.guard_stopped()))
        if self.pages_fetched == 0:
            closed = {p.origin: p.group_label for p in self.robots._policies.values() if p.mode == "disallow_all"}
            log_event(log, logging.WARNING, "crawl.nothing_fetched", closed_by_robots=closed or None,
                      stopped_hosts=self.guard_stopped() or None,
                      hint="If the reasons are network errors (timeouts, proxy/TLS errors), check your connection "
                           "or VPN and run `unifaculty robots <seed-url> -v`; if robots.txt really disallows, "
                           "this university can't be crawled.")

    def guard_stopped(self) -> dict[str, str]:
        return {h: s.stopped for h, s in self.guard.hosts.items() if s.stopped}

    def _skip(self, event: str, level: int = logging.DEBUG, **fields) -> None:
        self.stats.inc(event)
        log_event(log, level, event, **fields)

    def _visit(self, url: str, depth: int, section: str, via: str, score: int) -> CrawledPage | None:
        host = host_of(url)
        if not looks_like_html(url):
            self._skip("skip.non_html_url", TRACE, url=url)
            return None
        if not host_in_scope(host, self.uni.allowed_domains):
            self._skip("skip.out_of_scope", TRACE, url=url, host=host)
            return None
        stopped = self.guard.is_stopped(host)
        if stopped:
            self._skip("skip.host_stopped", TRACE, url=url, reason=stopped)
            return None
        decision = self.robots.check(url)
        if not decision.allowed:
            self._skip("robots.disallowed", logging.DEBUG, url=url, rule=decision.rule, reason=decision.reason)
            return None

        crawl_delay = self.robots.crawl_delay(url)
        if crawl_delay and crawl_delay > MAX_HONORED_CRAWL_DELAY:
            self.guard.stop(host, f"Crawl-delay {crawl_delay}s is longer than this tool waits ({MAX_HONORED_CRAWL_DELAY}s)")
            self._skip("skip.host_stopped", logging.INFO, url=url, reason="crawl-delay too long")
            return None
        delay = effective_delay(self.uni.min_delay_seconds, crawl_delay)

        result: FetchResult | None = None
        for attempt in range(3):
            self.limiter.wait(host, delay)
            with span("fetch", log, url=url, depth=depth, score=score, section=section, attempt=attempt) as s:
                result = self.fetcher.get(url)
                s.set(http_status=result.status, final_url=result.final_url if result.final_url != url else None,
                      bytes=result.bytes, content_type=result.content_type, truncated=result.truncated or None,
                      redirects=result.redirects or None, error=result.error)
                if result.status == 0 or result.status >= 400:
                    s.promote(logging.WARNING if result.status in (0, 401, 403, 429) or result.status >= 500 else logging.INFO)
            self.pages_fetched += 1
            self.stats.inc("requests.pages")
            self.stats.status_codes[str(result.status)] += 1
            self.stats.timing("fetch", result.elapsed_ms)
            self.stats.inc("bytes", result.bytes)

            verdict = self.guard.assess(host, result.status, result.headers, result.text, attempt)
            if verdict.action == "retry" and self.pages_fetched < self.uni.max_pages:
                self.stats.inc("fetch.retries")
                self.limiter.pause(host, verdict.wait_seconds, verdict.reason)
                continue
            if verdict.action == "stop_host":
                self.stats.blocked_hosts[host] = verdict.reason
                return None
            if verdict.action in ("skip_url", "retry"):
                self._skip("skip.http_error", logging.INFO, url=url, http_status=result.status, reason=verdict.reason)
                return None
            break
        assert result is not None

        final = normalize_url(result.final_url) or url
        if final != url:
            if not host_in_scope(host_of(final), self.uni.allowed_domains):
                self._skip("skip.redirect_out_of_scope", logging.INFO, url=url, final_url=final)
                return None
            if not self.robots.check(final).allowed:
                self._skip("robots.disallowed_after_redirect", logging.INFO, url=url, final_url=final)
                return None
            self._seen.add(final)

        if not result.is_html:
            self._skip("skip.non_html", logging.DEBUG, url=final, content_type=result.content_type)
            return None

        self._dump(final, result)
        with span("parse", log, url=final) as s:
            parsed = parse_html(result.text, final, self.field_terms)
            parsed.meta_robots |= result.x_robots_tag
            s.set(kind=parsed.kind, title=parsed.title[:120], text_chars=len(parsed.text), links=len(parsed.links),
                  emails=len(parsed.emails), profile_links=sorted(parsed.profile_links), meta_robots=sorted(parsed.meta_robots) or None,
                  signals=parsed.signals.as_dict())
        self.stats.inc(f"pages.kind.{parsed.kind}")

        if not parsed.nofollow and depth < self.uni.max_depth:
            added = 0
            for link in parsed.links:
                if host_in_scope(host_of(link.url), self.uni.allowed_domains) and looks_like_html(link.url):
                    before = len(self._seen)
                    self._push(link.url, depth + 1, section, link.anchor, via=final)
                    added += len(self._seen) - before
            log_event(log, TRACE, "frontier.links_added", url=final, added=added, frontier=len(self._frontier))
        elif parsed.nofollow:
            self._skip("skip.nofollow_links", logging.DEBUG, url=final)

        if parsed.noindex:
            self._skip("skip.noindex", logging.DEBUG, url=final, meta_robots=sorted(parsed.meta_robots))
            return None

        return CrawledPage(url=url, final_url=final, depth=depth, section=section, parsed=parsed,
                           fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           http_status=result.status)

    def _dump(self, url: str, result: FetchResult) -> None:
        if not self.pages_dir:
            return
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".html"
        (self.pages_dir / name).write_text(result.text, encoding="utf-8")
        with (self.pages_dir / "index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"file": name, "url": url, "status": result.status, "bytes": result.bytes}) + "\n")
