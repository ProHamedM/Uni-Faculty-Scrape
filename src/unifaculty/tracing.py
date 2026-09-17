"""Lightweight spans: nested, timed, and written to every log sink.

    with span("fetch", url=url) as s:
        result = fetcher.get(url)
        s.set(status=result.status, bytes=result.bytes)

logs ``fetch.start`` (TRACE) and ``fetch.end`` (DEBUG by default) with
``elapsed_ms``, the span id and its parent span id; an exception logs
``fetch.error`` with the traceback and re-raises.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from unifaculty.logs import TRACE, current_span, get_logger, log_event, parent_span

_log = get_logger("trace")


@dataclass
class SpanHandle:
    name: str
    span_id: str
    fields: dict[str, Any] = field(default_factory=dict)
    level: int = logging.DEBUG

    def set(self, **fields: Any) -> None:
        self.fields.update(fields)

    def promote(self, level: int) -> None:
        """Log the span's end at a higher level (e.g. WARNING when something went wrong)."""
        self.level = max(self.level, level)


@contextmanager
def span(name: str, logger: logging.Logger | None = None, level: int = logging.DEBUG,
         **fields: Any) -> Iterator[SpanHandle]:
    logger = logger or _log
    span_id = secrets.token_hex(6)
    handle = SpanHandle(name=name, span_id=span_id, fields=dict(fields), level=level)
    outer = current_span.get()
    token_span = current_span.set(span_id)
    token_parent = parent_span.set(outer)
    start = time.perf_counter()
    log_event(logger, TRACE, f"{name}.start", **fields)
    try:
        yield handle
    except Exception as exc:
        elapsed = round((time.perf_counter() - start) * 1000, 1)
        log_event(logger, logging.ERROR, f"{name}.error", elapsed_ms=elapsed,
                  error=type(exc).__name__, message=str(exc)[:500], exc_info=True, **handle.fields)
        raise
    else:
        elapsed = round((time.perf_counter() - start) * 1000, 1)
        log_event(logger, handle.level, f"{name}.end", elapsed_ms=elapsed, **handle.fields)
    finally:
        current_span.reset(token_span)
        parent_span.reset(token_parent)


class Stats:
    """Counters and timings that end up in summary.json."""

    def __init__(self) -> None:
        self.counters: Counter[str] = Counter()
        self.status_codes: Counter[str] = Counter()
        self.reasons: Counter[str] = Counter()
        self.timings_ms: dict[str, list[float]] = defaultdict(list)
        self.tokens: Counter[str] = Counter()
        self.blocked_hosts: dict[str, str] = {}

    def inc(self, key: str, amount: int = 1) -> None:
        self.counters[key] += amount

    def timing(self, key: str, ms: float) -> None:
        self.timings_ms[key].append(ms)

    def to_dict(self) -> dict[str, Any]:
        def summarize(values: list[float]) -> dict[str, float]:
            if not values:
                return {}
            ordered = sorted(values)
            p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
            return {"count": len(values), "total_ms": round(sum(values), 1),
                    "avg_ms": round(sum(values) / len(values), 1), "p95_ms": round(p95, 1),
                    "max_ms": round(ordered[-1], 1)}

        return {
            "counters": dict(sorted(self.counters.items())),
            "http_status": dict(sorted(self.status_codes.items())),
            "reject_reasons": dict(self.reasons.most_common()),
            "timings": {k: summarize(v) for k, v in sorted(self.timings_ms.items())},
            "llm_tokens": dict(self.tokens),
            "blocked_hosts": dict(self.blocked_hosts),
        }
