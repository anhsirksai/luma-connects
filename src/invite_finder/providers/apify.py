"""Apify channel — pay-per-result actors reached directly with a token.

This is the no-KYC path. Perflo brokers the same catalogue (and bills it to an
agent mandate), but an Apify token works the minute you sign up, so the router
can run channel B before any banking setup clears.

Every call goes through `CachingSession`, which folds the endpoint into the
cache key — without that, two actors called with identical input would collide
on one fingerprint and serve each other's answers.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from invite_finder.config import Settings
from invite_finder.providers.base import (
    CACHE_KIND_BY_CAPABILITY,
    Capability,
    ProviderError,
    ProviderResult,
)

# Actor slugs use `~` in API paths. Pricing is per 1,000 results; we record the
# per-call cost so the ledger reflects real spend rather than a flat guess.
ACTORS: dict[str, dict[str, Any]] = {
    Capability.LINKEDIN_BY_NAME: {
        "actor": "apimaestro~linkedin-profile-search-scraper",
        "cost_per_1k_cents": 500,
    },
    Capability.LINKEDIN_POSTS: {
        "actor": "harvestapi~linkedin-profile-posts",
        "cost_per_1k_cents": 500,
    },
}


def _cost_cents(capability: str, record_count: int) -> int:
    """Round up: a fractional cent still spends money, and under-reporting
    spend is the failure mode that matters."""
    spec = ACTORS.get(capability)
    if not spec or record_count <= 0:
        return 0
    per_1k = int(spec["cost_per_1k_cents"])
    return -(-per_1k * record_count // 1000)


class ApifyChannel:
    name = "apify"
    channel = "apify_direct"

    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.apify_token)

    def supports(self, capability: str) -> bool:
        return self.enabled and capability in ACTORS

    def fetch(self, capability: str, payload: dict[str, Any]) -> ProviderResult:
        spec = ACTORS.get(capability)
        if spec is None:
            raise ProviderError(f"apify does not serve {capability}")
        if not self.enabled:
            raise ProviderError("APIFY_TOKEN is not configured")

        actor = spec["actor"]
        url = (
            f"{self.settings.apify_api_base_url.rstrip('/')}"
            f"/acts/{actor}/run-sync-get-dataset-items"
            f"?token={self.settings.apify_token}"
        )

        try:
            response = self.session.post(
                url,
                json=payload,
                timeout=120,
                cache_kind=CACHE_KIND_BY_CAPABILITY.get(capability),
            )
        except TypeError:
            # A plain requests.Session (tests, or a caller that bypassed the
            # caching layer) doesn't accept cache_kind.
            response = self.session.post(url, json=payload, timeout=120)

        if response.status_code >= 400:
            raise ProviderError(
                f"apify actor {actor} failed with {response.status_code}: "
                f"{response.text[:500]}"
            )

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError(f"apify actor {actor} returned invalid JSON") from exc

        records = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
        cache_hit = response.headers.get("x-invite-finder-cache") in {"hit", "stale"}

        return ProviderResult(
            capability=capability,
            records=records,
            provider=actor,
            channel=self.channel,
            # A cache hit is a call we did not make, so it costs nothing. This
            # is the number that proves the cache is earning its keep.
            charged_cents=0 if cache_hit else _cost_cents(capability, len(records)),
            cache_hit=cache_hit,
        )
