"""Shared vocabulary for the pay-per-call channel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class Capability:
    """What we want done, independent of who does it.

    The router maps a capability to whichever channel can serve it, so callers
    never name a vendor and swapping vendors touches one table.
    """

    LINKEDIN_BY_NAME = "linkedin_by_name"
    LINKEDIN_POSTS = "linkedin_posts"
    X_PROFILE = "x_profile"
    CONTACT_ENRICH = "contact_enrich"
    EMAIL_VERIFY = "email_verify"

    ALL = (
        LINKEDIN_BY_NAME,
        LINKEDIN_POSTS,
        X_PROFILE,
        CONTACT_ENRICH,
        EMAIL_VERIFY,
    )


# Cache kind (and therefore TTL, see cache.TTL_BY_KIND) per capability.
CACHE_KIND_BY_CAPABILITY: dict[str, str] = {
    Capability.LINKEDIN_BY_NAME: "linkedin_search",
    Capability.LINKEDIN_POSTS: "linkedin_posts",
    Capability.X_PROFILE: "x_profile",
    Capability.CONTACT_ENRICH: "contact_enrich",
    Capability.EMAIL_VERIFY: "email_verify",
}


class ProviderError(RuntimeError):
    """A channel could not serve a capability. The router catches this and
    tries the next channel, so it must never be raised for programmer error."""


class BudgetExceeded(RuntimeError):
    """The run has spent its ceiling. Raised instead of quietly continuing —
    a data product that overspends its own revenue is worse than one that
    degrades a tier."""


@dataclass
class ProviderResult:
    """One capability's answer, plus what it cost to get."""

    capability: str
    records: list[dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    channel: str = ""
    charged_cents: int = 0
    quote_cents: int | None = None
    purchase_id: str | None = None
    cache_hit: bool = False

    @property
    def first(self) -> dict[str, Any] | None:
        return self.records[0] if self.records else None

    def __bool__(self) -> bool:
        return bool(self.records)


class Channel(Protocol):
    """A source that can serve one or more capabilities."""

    name: str

    def supports(self, capability: str) -> bool: ...

    def fetch(self, capability: str, payload: dict[str, Any]) -> ProviderResult: ...
