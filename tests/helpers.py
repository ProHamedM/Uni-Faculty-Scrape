"""Test doubles: an in-memory website, a fake clock, and config builders.

All fixture people, emails and domains are fictional (example-university.edu).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from unifaculty.fetcher import FetchResult  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += max(0.0, seconds)


class FakeFetcher:
    """Serves a dict of url -> (status, headers, body). Unknown URLs return 404."""

    name = "fake"

    def __init__(self, routes: dict[str, tuple[int, dict, str]], clock: FakeClock | None = None):
        self.routes = routes
        self.requested: list[str] = []
        self.clock = clock

    def get(self, url: str) -> FetchResult:
        self.requested.append(url)
        if self.clock:
            self.clock.now += 0.05
        status, headers, body = self.routes.get(url, (404, {"content-type": "text/html"}, "<html><body>Not found</body></html>"))
        headers = {k.lower(): v for k, v in headers.items()}
        return FetchResult(url=url, final_url=headers.pop("x-final-url", url), status=status, headers=headers,
                           text=body, bytes=len(body.encode("utf-8")), elapsed_ms=12.0)

    def close(self) -> None:
        pass


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def html_route(name: str, status: int = 200) -> tuple[int, dict, str]:
    return status, {"content-type": "text/html; charset=utf-8"}, fixture_text(f"site/{name}")
