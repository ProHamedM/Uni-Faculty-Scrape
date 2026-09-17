"""HTTP fetchers. GET only, redirects capped, bodies size-capped.

``CurlCffiFetcher`` (default) uses curl_cffi's Chrome profile so ordinary
public pages load on CDNs that reject non-browser TLS stacks. It keeps ONE
identity for the whole run: no header, fingerprint, proxy or IP rotation.
Blocks are handled by :class:`unifaculty.compliance.DomainGuard`, which stops
the host instead of retrying differently.

``HttpxFetcher`` is a dependency-light fallback that identifies itself
honestly as ``UniFacultyScraper``.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Protocol

from unifaculty import PRODUCT_TOKEN, REPO_URL, __version__

HTML_TYPES = ("text/html", "application/xhtml+xml")


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    bytes: int = 0
    elapsed_ms: float = 0.0
    truncated: bool = False
    error: str | None = None
    redirects: int = 0

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    @property
    def is_html(self) -> bool:
        ct = self.content_type
        return (not ct and self.text.lstrip()[:15].lower().startswith(("<!doctype html", "<html"))) or ct in HTML_TYPES

    @property
    def x_robots_tag(self) -> set[str]:
        value = self.headers.get("x-robots-tag", "")
        return {v.strip().lower() for v in re.split(r"[,:]", value) if v.strip()}


class Fetcher(Protocol):
    name: str

    def get(self, url: str) -> FetchResult: ...

    def close(self) -> None: ...


def _decode(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    candidates = [match.group(1)] if match else []
    sniff = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:4096], re.I)
    if sniff:
        candidates.append(sniff.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8", "cp1252"]
    for enc in candidates:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def accept_language(languages: list[str]) -> str:
    parts, q = [], 1.0
    for lang in languages or ["en"]:
        parts.append(lang if q == 1.0 else f"{lang};q={q:.1f}")
        q = max(0.1, q - 0.2)
    if "en" not in (languages or []):
        parts.append("en;q=0.5")
    return ",".join(parts)


class CurlCffiFetcher:
    name = "curl_cffi"

    def __init__(self, impersonate: str = "chrome", timeout: float = 25, max_bytes: int = 3_000_000,
                 languages: list[str] | None = None):
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RuntimeError(
                "curl_cffi is not installed. Run `pip install -e .` (or `pip install curl_cffi`), "
                "or use `--fetcher httpx`."
            ) from exc
        self._requests = curl_requests
        self.impersonate = impersonate
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.session = curl_requests.Session(impersonate=impersonate)
        self.session.headers.update({"Accept-Language": accept_language(languages or ["en"])})

    def get(self, url: str) -> FetchResult:
        start = time.perf_counter()
        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True, max_redirects=5, stream=True)
            chunks, total, truncated = [], 0, False
            try:
                for chunk in resp.iter_content():
                    total += len(chunk)
                    if total > self.max_bytes:
                        chunks.append(chunk[: max(0, self.max_bytes - (total - len(chunk)))])
                        truncated = True
                        break
                    chunks.append(chunk)
            finally:
                resp.close()
            body = b"".join(chunks)
            headers = {k.lower(): v for k, v in resp.headers.items()}
            history = getattr(resp, "history", None) or []
            return FetchResult(url=url, final_url=str(resp.url), status=resp.status_code, headers=headers,
                               text=_decode(body, headers.get("content-type", "")), bytes=len(body),
                               elapsed_ms=round((time.perf_counter() - start) * 1000, 1), truncated=truncated,
                               redirects=len(history))
        except Exception as exc:  # network errors, timeouts, TLS errors
            return FetchResult(url=url, final_url=url, status=0, error=f"{type(exc).__name__}: {exc}"[:500],
                               elapsed_ms=round((time.perf_counter() - start) * 1000, 1))

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:  # pragma: no cover
            pass


class HttpxFetcher:
    name = "httpx"

    def __init__(self, timeout: float = 25, max_bytes: int = 3_000_000, languages: list[str] | None = None):
        import httpx

        self.max_bytes = max_bytes
        self.client = httpx.Client(
            follow_redirects=True, max_redirects=5, timeout=timeout,
            headers={
                "User-Agent": f"Mozilla/5.0 (compatible; {PRODUCT_TOKEN}/{__version__}; +{REPO_URL})",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Language": accept_language(languages or ["en"]),
            },
        )

    def get(self, url: str) -> FetchResult:
        start = time.perf_counter()
        try:
            with self.client.stream("GET", url) as resp:
                chunks, total, truncated = [], 0, False
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > self.max_bytes:
                        chunks.append(chunk[: max(0, self.max_bytes - (total - len(chunk)))])
                        truncated = True
                        break
                    chunks.append(chunk)
                body = b"".join(chunks)
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return FetchResult(url=url, final_url=str(resp.url), status=resp.status_code, headers=headers,
                                   text=_decode(body, headers.get("content-type", "")), bytes=len(body),
                                   elapsed_ms=round((time.perf_counter() - start) * 1000, 1),
                                   truncated=truncated, redirects=len(resp.history))
        except Exception as exc:
            return FetchResult(url=url, final_url=url, status=0, error=f"{type(exc).__name__}: {exc}"[:500],
                               elapsed_ms=round((time.perf_counter() - start) * 1000, 1))

    def close(self) -> None:
        self.client.close()


def build_fetcher(kind: str, impersonate: str, timeout: float, max_bytes: int, languages: list[str]) -> Fetcher:
    if kind == "httpx":
        return HttpxFetcher(timeout=timeout, max_bytes=max_bytes, languages=languages)
    return CurlCffiFetcher(impersonate=impersonate, timeout=timeout, max_bytes=max_bytes, languages=languages)
