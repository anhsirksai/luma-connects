from __future__ import annotations

from typing import Protocol

from invite_finder.brightdata import SerpResult


class WebDataClient(Protocol):
    """Structural type shared by BrightDataClient and ObservedBrightDataClient,
    so LeadFinderContext.brightdata can hold either without a hard dependency
    on the concrete Bright Data implementation."""

    def unlock_url(
        self,
        url: str,
        *,
        data_format: str | None = "markdown",
        response_format: str = "raw",
        country: str | None = None,
    ) -> str: ...

    def google_search(
        self,
        query: str,
        *,
        limit: int = 10,
        start: int = 0,
        language: str = "en",
        country: str | None = None,
    ) -> list[SerpResult]: ...
