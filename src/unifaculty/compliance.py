"""Hard guardrails. These are constants on purpose — config can make the tool
*more* careful, never less. See ACCEPTABLE_USE.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping

from unifaculty.logs import get_logger, log_event

log = get_logger("compliance")

#: Seconds between two requests to the same host. Config values below this are raised.
MIN_DELAY_FLOOR = 2.0
#: Longest Crawl-delay we still wait for before giving up on a host for this run.
MAX_HONORED_CRAWL_DELAY = 120.0
#: Hard ceiling on pages per university per run.
MAX_PAGES_CEILING = 3000
#: Longest Retry-After we wait for on a 429 (once).
MAX_RETRY_AFTER = 120.0
#: Consecutive 5xx responses before a host is dropped for the run.
MAX_CONSECUTIVE_SERVER_ERRORS = 5

#: HTTP statuses that mean "you are not welcome here" — stop the host, never work around it.
STOP_STATUSES = {401, 403, 407, 451}

# Markers of bot-challenge / CAPTCHA interstitials. Matching one stops the host.
_STRONG_CHALLENGE_MARKERS = (
    "cf-chl-", "challenge-platform", "cf_chl_opt", "_incapsula_resource", "px-captcha",
    "captcha-delivery.com", "verify you are human", "just a moment...", "ddos-guard",
    "attention required! | cloudflare", "are you a robot", "unusual traffic from your computer",
)

ALLOWED_METHODS = frozenset({"GET"})


def effective_delay(config_delay: float | None, crawl_delay: float | None) -> float:
    """The politeness delay for a host: the strictest of floor, config and robots Crawl-delay."""
    candidates = [MIN_DELAY_FLOOR]
    if config_delay:
        candidates.append(float(config_delay))
    if crawl_delay:
        candidates.append(float(crawl_delay))
    return max(candidates)


def detect_challenge(status: int, headers: Mapping[str, str], body: str) -> str | None:
    """Return a short reason if the response is a bot challenge / CAPTCHA page."""
    lowered_headers = {k.lower(): v for k, v in headers.items()}
    if lowered_headers.get("cf-mitigated", "").lower() == "challenge":
        return "cf-mitigated: challenge header"
    head = (body or "")[:40000].lower()
    for marker in _STRONG_CHALLENGE_MARKERS:
        if marker in head and (status in (403, 429, 503) or len(body or "") < 60000):
            return f"challenge marker '{marker}'"
    return None


def parse_retry_after(value: str | None, default: float = 30.0) -> float:
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        return default  # HTTP-date form: fall back to the default wait


@dataclass
class HostState:
    stopped: str | None = None
    rate_limited: int = 0
    consecutive_server_errors: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    action: str                 # "ok" | "retry" | "skip_url" | "stop_host"
    reason: str = ""
    wait_seconds: float = 0.0


class DomainGuard:
    """Decides what happens after each response. A stop is final for the run."""

    def __init__(self) -> None:
        self.hosts: dict[str, HostState] = {}

    def state(self, host: str) -> HostState:
        return self.hosts.setdefault(host, HostState())

    def is_stopped(self, host: str) -> str | None:
        return self.state(host).stopped

    def stop(self, host: str, reason: str) -> None:
        state = self.state(host)
        if not state.stopped:
            state.stopped = reason
            log_event(log, logging.WARNING, "guard.host_stopped", host=host, reason=reason,
                      policy="no retries with a different identity; host skipped for this run")

    def assess(self, host: str, status: int, headers: Mapping[str, str], body: str,
               attempt: int) -> Verdict:
        state = self.state(host)
        challenge = detect_challenge(status, headers, body)
        if challenge:
            self.stop(host, f"bot challenge / CAPTCHA ({challenge})")
            return Verdict("stop_host", state.stopped or challenge)
        if status in STOP_STATUSES:
            self.stop(host, f"HTTP {status}")
            return Verdict("stop_host", f"HTTP {status}")
        if status == 429:
            state.rate_limited += 1
            if state.rate_limited >= 2:
                self.stop(host, "HTTP 429 twice (rate limited)")
                return Verdict("stop_host", "HTTP 429 twice")
            wait = min(parse_retry_after(headers.get("retry-after") or headers.get("Retry-After")), MAX_RETRY_AFTER)
            return Verdict("retry", "HTTP 429 — waiting once as asked", wait_seconds=max(wait, MIN_DELAY_FLOOR))
        if status == 0 or status >= 500:
            state.consecutive_server_errors += 1
            if state.consecutive_server_errors >= MAX_CONSECUTIVE_SERVER_ERRORS:
                self.stop(host, f"{state.consecutive_server_errors} consecutive server/network errors")
                return Verdict("stop_host", "too many server errors")
            if attempt < 2:
                return Verdict("retry", f"HTTP {status} — transient, backing off", wait_seconds=10.0 * (attempt + 1))
            return Verdict("skip_url", f"HTTP {status} after retries")
        state.consecutive_server_errors = 0
        if 400 <= status < 500:
            return Verdict("skip_url", f"HTTP {status}")
        return Verdict("ok")
