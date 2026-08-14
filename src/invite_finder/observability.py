from __future__ import annotations

from typing import Any, Protocol

from invite_finder.brightdata import SerpResult
from invite_finder.protocols import WebDataClient


class RunReporter(Protocol):
    def emit(self, type_: str, message: str, data: dict[str, Any] | None = None) -> None: ...


class NullReporter:
    """No-op reporter used when progress streaming isn't needed (e.g. the CLI)."""

    def emit(self, type_: str, message: str, data: dict[str, Any] | None = None) -> None:
        return None


def _cache_status(client: WebDataClient) -> str | None:
    session = getattr(client, "session", None)
    hit = getattr(session, "last_was_cache_hit", None)
    if hit is None:
        return None
    return "cache_hit" if hit else "fetch"


class ObservedBrightDataClient:
    """Wraps a WebDataClient (BrightDataClient or similar) and emits run_events
    around each call, without requiring any change to brightdata.py or the
    three @function_tools in agent.py that hold a reference to this object."""

    def __init__(self, inner: WebDataClient, reporter: RunReporter | None = None) -> None:
        self.inner = inner
        self.reporter = reporter or NullReporter()

    def unlock_url(
        self,
        url: str,
        *,
        data_format: str | None = "markdown",
        response_format: str = "raw",
        country: str | None = None,
    ) -> str:
        result = self.inner.unlock_url(
            url, data_format=data_format, response_format=response_format, country=country
        )
        status = _cache_status(self.inner) or "fetch"
        self.reporter.emit(
            status,
            f"Fetched {url}" if status == "fetch" else f"Cache hit: {url}",
            {"url": url},
        )
        return result

    def google_search(
        self,
        query: str,
        *,
        limit: int = 10,
        start: int = 0,
        language: str = "en",
        country: str | None = None,
    ) -> list[SerpResult]:
        results = self.inner.google_search(
            query, limit=limit, start=start, language=language, country=country
        )
        status = _cache_status(self.inner) or "serp"
        self.reporter.emit(
            "serp" if status != "cache_hit" else "cache_hit",
            f"Searched: {query}" if status != "cache_hit" else f"Cache hit: {query}",
            {"query": query, "result_count": len(results)},
        )
        return results
