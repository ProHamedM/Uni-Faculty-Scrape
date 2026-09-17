"""robots.txt per RFC 9309 — parsed and enforced, with no way to switch it off.

Why not ``urllib.robotparser``? It uses first-match instead of the RFC's
longest-match rule and does not implement ``*`` / ``$`` wildcards, so it
gets real university files (e.g. an ``Allow: /93095219`` inside a blanket
``Disallow: /9``) wrong.

Fetch outcomes (RFC 9309, 2.3.1):

* 2xx               -> parse and obey
* 404 / 410 / 4xx   -> "unavailable": everything allowed
* 401 / 403         -> treated as *disallow all* (stricter than the RFC: a
                       server refusing robots.txt is not inviting a crawler)
* 429, 5xx, network -> "unreachable": everything disallowed for this run
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import unquote

from unifaculty import PRODUCT_TOKEN
from unifaculty.logs import TRACE, get_logger, log_event
from unifaculty.urls import origin_of, path_and_query

log = get_logger("robots")

MAX_ROBOTS_BYTES = 500 * 1024  # RFC 9309: parse at least 500 KiB


@dataclass
class Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]

    @property
    def specificity(self) -> int:
        return len(self.pattern.encode("utf-8"))


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None


def _normalize_path(path: str) -> str:
    # Decode percent-escapes so "/%7Ejane" and "/~jane" compare equal; keep "/" semantics.
    try:
        return unquote(path, errors="strict")
    except UnicodeDecodeError:  # pragma: no cover - malformed escapes
        return path


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = "".join(".*" if ch == "*" else re.escape(ch) for ch in _normalize_path(body))
    return re.compile(regex + ("$" if anchored else ""), re.DOTALL)


def parse_robots(text: str) -> tuple[list[Group], list[str]]:
    """Return (groups, sitemaps). Groups for the same agent are merged by the matcher."""
    groups: list[Group] = []
    sitemaps: list[str] = []
    current: Group | None = None
    last_was_agent = False
    for raw in text[:MAX_ROBOTS_BYTES].splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if current is None or not last_was_agent:
                current = Group()
                groups.append(current)
            current.agents.append(value.lower())
            last_was_agent = True
            continue
        if key == "sitemap":
            if value:
                sitemaps.append(value)
            continue
        last_was_agent = False
        if current is None:
            continue  # rules before any user-agent line are ignored
        if key in ("allow", "disallow"):
            if not value:
                continue  # "Disallow:" with no path matches nothing
            if not value.startswith("/") and not value.startswith("*"):
                value = "/" + value
            current.rules.append(Rule(allow=(key == "allow"), pattern=value, regex=_compile(value)))
        elif key == "crawl-delay":
            try:
                delay = float(value)
                if delay >= 0:
                    current.crawl_delay = delay
            except ValueError:
                pass
    return groups, sitemaps


@dataclass
class Decision:
    allowed: bool
    rule: str | None          # the matching line, e.g. "Disallow: /9"
    group: str                # which user-agent group applied
    reason: str


class RobotsPolicy:
    """Rules for one origin."""

    def __init__(self, origin: str, groups: list[Group], sitemaps: list[str], mode: str = "parsed",
                 status: int | None = None, product_token: str = PRODUCT_TOKEN):
        self.origin = origin
        self.mode = mode          # parsed | allow_all | disallow_all
        self.status = status
        self.sitemaps = sitemaps
        token = product_token.lower()
        named = [g for g in groups if any(a.split("/")[0].strip() == token for a in g.agents)]
        star = [g for g in groups if "*" in g.agents]
        chosen = named or star
        self.group_label = product_token if named else ("*" if star else "none")
        self.rules: list[Rule] = [r for g in chosen for r in g.rules]
        delays = [g.crawl_delay for g in chosen if g.crawl_delay is not None]
        self.crawl_delay: float | None = max(delays) if delays else None

    @classmethod
    def allow_all(cls, origin: str, status: int | None, reason: str) -> "RobotsPolicy":
        policy = cls(origin, [], [], mode="allow_all", status=status)
        policy.group_label = reason
        return policy

    @classmethod
    def disallow_all(cls, origin: str, status: int | None, reason: str) -> "RobotsPolicy":
        policy = cls(origin, [], [], mode="disallow_all", status=status)
        policy.group_label = reason
        return policy

    def check(self, url: str) -> Decision:
        target = path_and_query(url)
        if target == "/robots.txt":
            return Decision(True, None, self.group_label, "robots.txt itself")
        if self.mode == "allow_all":
            return Decision(True, None, self.group_label, f"robots.txt unavailable (HTTP {self.status}) — allowed")
        if self.mode == "disallow_all":
            return Decision(False, None, self.group_label, f"robots.txt unreachable (HTTP {self.status}) — disallowed")
        normalized = _normalize_path(target)
        best: Rule | None = None
        for rule in self.rules:
            if rule.regex.match(normalized):
                if (best is None or rule.specificity > best.specificity
                        or (rule.specificity == best.specificity and rule.allow and not best.allow)):
                    best = rule
        if best is None:
            return Decision(True, None, self.group_label, "no matching rule")
        line = f"{'Allow' if best.allow else 'Disallow'}: {best.pattern}"
        return Decision(best.allow, line, self.group_label, "longest match")


FetchText = Callable[[str], tuple[int, str, str | None]]
"""(url) -> (status, text, error). Status 0 means a network error."""


class RobotsCache:
    """Fetches robots.txt once per origin per run and answers allow/deny questions."""

    def __init__(self, fetch_text: FetchText):
        self._fetch_text = fetch_text
        self._policies: dict[str, RobotsPolicy] = {}

    def policy_for(self, url: str) -> RobotsPolicy:
        origin = origin_of(url)
        if origin in self._policies:
            return self._policies[origin]
        robots_url = origin + "/robots.txt"
        status, text, error = self._fetch_text(robots_url)
        if status == 0:
            policy = RobotsPolicy.disallow_all(origin, status, f"network error: {error}")
            level = logging.WARNING
        elif 200 <= status < 300:
            groups, sitemaps = parse_robots(text or "")
            policy = RobotsPolicy(origin, groups, sitemaps, status=status)
            level = logging.INFO
        elif status in (401, 403):
            policy = RobotsPolicy.disallow_all(origin, status, "robots.txt refused (401/403)")
            level = logging.WARNING
        elif status == 429 or status >= 500:
            policy = RobotsPolicy.disallow_all(origin, status, "robots.txt unreachable (429/5xx)")
            level = logging.WARNING
        else:
            policy = RobotsPolicy.allow_all(origin, status, "robots.txt not found")
            level = logging.INFO
        self._policies[origin] = policy
        log_event(log, level, "robots.loaded", origin=origin, http_status=status, mode=policy.mode,
                  group=policy.group_label, rules=len(policy.rules), crawl_delay=policy.crawl_delay,
                  sitemaps=len(policy.sitemaps), error=error)
        if policy.mode == "disallow_all":
            log_event(log, logging.WARNING, "robots.host_closed", origin=origin,
                      hint="RFC 9309: unreachable robots.txt means complete disallow; this host is skipped")
        return policy

    def check(self, url: str) -> Decision:
        policy = self.policy_for(url)
        decision = policy.check(url)
        log_event(log, TRACE, "robots.check", url=url, allowed=decision.allowed, rule=decision.rule,
                  group=decision.group, reason=decision.reason)
        return decision

    def crawl_delay(self, url: str) -> float | None:
        return self.policy_for(url).crawl_delay
