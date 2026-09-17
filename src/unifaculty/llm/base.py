"""Provider interface + shared retry logic for LLM calls."""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from unifaculty.logs import get_logger, log_event

log = get_logger("llm")


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    finish_reason: str | None = None
    attempts: int = 1
    raw: dict[str, Any] = field(default_factory=dict)


class LLMError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False,
                 retry_after: float | None = None, hint: str | None = None):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after
        self.hint = hint


def error_from_response(resp: httpx.Response, provider: str) -> LLMError:
    status = resp.status_code
    try:
        body = resp.json()
        message = (body.get("error", {}) or {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        message = message or resp.text[:400]
    except ValueError:
        message = resp.text[:400]
    retry_after = None
    if "retry-after" in resp.headers:
        try:
            retry_after = float(resp.headers["retry-after"])
        except ValueError:
            retry_after = None
    hint = None
    lowered = str(message).lower()
    if status in (401, 403) and ("location" in lowered or "region" in lowered or "country" in lowered):
        hint = (f"{provider} refused the request for your location. Provider availability differs by country — "
                "try another provider, or `--provider ollama` to run a local model.")
    elif status in (401, 403):
        hint = f"Check the API key for {provider} (see .env.example)."
    elif status == 404:
        hint = "The model name may be wrong or retired — set llm.model in config/settings.yaml."
    elif status == 429:
        hint = "Rate limited — lower llm.requests_per_minute or wait; free tiers have daily caps."
    retryable = status == 429 or status >= 500
    return LLMError(f"{provider} HTTP {status}: {message}", status=status, retryable=retryable,
                    retry_after=retry_after, hint=hint)


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, model: str, timeout: float = 90, max_retries: int = 4,
                 sleep: Callable[[float], None] = time.sleep):
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep

    @abstractmethod
    def _complete(self, system: str, user: str, temperature: float, max_output_tokens: int) -> LLMResult:
        """One HTTP round-trip. Raise LLMError on failure."""

    def complete_json(self, system: str, user: str, temperature: float = 0.0,
                      max_output_tokens: int = 2048) -> LLMResult:
        attempt = 0
        while True:
            attempt += 1
            start = time.perf_counter()
            try:
                result = self._complete(system, user, temperature, max_output_tokens)
                result.latency_ms = round((time.perf_counter() - start) * 1000, 1)
                result.attempts = attempt
                return result
            except httpx.TimeoutException as exc:
                err = LLMError(f"{self.name} timeout after {self.timeout}s: {exc}", retryable=True)
            except httpx.TransportError as exc:
                err = LLMError(f"{self.name} network error: {exc}", retryable=True)
            except LLMError as exc:
                err = exc
            if not err.retryable or attempt > self.max_retries:
                log_event(log, logging.ERROR, "llm.failed", provider=self.name, model=self.model, attempt=attempt,
                          status=err.status, error=str(err)[:400], hint=err.hint)
                raise err
            wait = err.retry_after if err.retry_after is not None else min(60.0, (2 ** attempt) + random.uniform(0, 1))
            log_event(log, logging.WARNING, "llm.retry", provider=self.name, model=self.model, attempt=attempt,
                      status=err.status, wait_seconds=round(wait, 1), error=str(err)[:200], hint=err.hint)
            self._sleep(wait)

    def close(self) -> None:  # pragma: no cover - overridden where needed
        pass
