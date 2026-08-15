"""Per-person enrichment: posts, X, and contact data.

Gated by tier. The Basic tier buys nothing beyond what resolution already
found; the Full tier buys posts, X and contact data. Enrichment is never run
speculatively — every call here spends money, so the caller must hold a paid
entitlement.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from invite_finder.providers.base import BudgetExceeded, Capability
from invite_finder.providers.router import ProviderRouter
from invite_finder.resolve import _first_str
from invite_finder.store import people_store

# Which capabilities each paid tier is allowed to buy.
CAPABILITIES_BY_TIER: dict[str, tuple[str, ...]] = {
    "snapshot": (),
    "basic": (Capability.X_PROFILE,),
    "full": (
        Capability.X_PROFILE,
        Capability.LINKEDIN_POSTS,
        Capability.CONTACT_ENRICH,
        Capability.EMAIL_VERIFY,
    ),
}


@dataclass
class EnrichmentOutcome:
    people_enriched: int = 0
    emails_found: int = 0
    phones_found: int = 0
    budget_exhausted: bool = False
    notes: list[str] = field(default_factory=list)


def _post_themes(records: list[dict[str, Any]], *, limit: int = 5) -> list[str]:
    """Reduce a post feed to what the person actually talks about. We keep
    themes, not post bodies — the product answer is "what are they into", and
    storing less of someone's content is the right default."""
    themes: list[str] = []
    for record in records[: limit * 3]:
        text = _first_str(record, "text", "content", "post_text", "commentary")
        if not text:
            continue
        condensed = " ".join(text.split())[:180]
        if condensed and condensed not in themes:
            themes.append(condensed)
        if len(themes) >= limit:
            break
    return themes


def enrich_person(
    conn: sqlite3.Connection,
    router: ProviderRouter,
    person: sqlite3.Row,
    *,
    tier: str,
) -> dict[str, Any]:
    """Buy every capability the tier allows for one person, and merge the
    result. Returns the fields written."""
    capabilities = CAPABILITIES_BY_TIER.get(tier, ())
    if not capabilities:
        return {}

    person_id = int(person["id"])
    name = person["name"] or ""
    company = person["company"] or ""
    linkedin_url = person["linkedin_url"] or ""

    updates: dict[str, Any] = {}
    enrichment: dict[str, Any] = {}

    if Capability.X_PROFILE in capabilities and not person["x_url"]:
        result = router.fetch(
            Capability.X_PROFILE,
            {"query": f"{name} {company}".strip()},
            person_id=person_id,
        )
        record = result.first
        if record:
            x_url = _first_str(record, "url", "profile_url", "link")
            handle = _first_str(record, "username", "handle", "screen_name")
            if not x_url and handle:
                x_url = f"https://x.com/{handle.lstrip('@')}"
            if x_url:
                updates["x_url"] = x_url
            bio = _first_str(record, "description", "bio")
            if bio:
                enrichment["x_bio"] = bio

    if Capability.LINKEDIN_POSTS in capabilities and linkedin_url:
        result = router.fetch(
            Capability.LINKEDIN_POSTS,
            {"profileUrl": linkedin_url, "maxPosts": 10},
            person_id=person_id,
        )
        themes = _post_themes(result.records)
        if themes:
            enrichment["post_themes"] = themes

    if Capability.CONTACT_ENRICH in capabilities and not (person["email"] and person["phone"]):
        payload: dict[str, Any] = {"name": name}
        if company:
            payload["company"] = company
        if linkedin_url:
            payload["linkedin_url"] = linkedin_url

        result = router.fetch(Capability.CONTACT_ENRICH, payload, person_id=person_id)
        record = result.first
        if record:
            email = _first_str(record, "email", "work_email", "personal_email")
            phone = _first_str(record, "phone", "phone_number", "mobile_phone")
            if email and not person["email"]:
                if _email_is_deliverable(router, email, person_id, capabilities):
                    updates["email"] = email
                else:
                    enrichment["email_unverified"] = email
            if phone and not person["phone"]:
                updates["phone"] = phone

    if enrichment:
        updates["enrichment"] = enrichment
    if updates:
        people_store.upsert_person(
            conn, linkedin_url=linkedin_url or None, name=name or None, **updates
        )
    return updates


def _email_is_deliverable(
    router: ProviderRouter,
    email: str,
    person_id: int,
    capabilities: tuple[str, ...],
) -> bool:
    """Verify before storing. Selling an address that bounces is worse than
    selling no address, and verification costs a fraction of the enrichment."""
    if Capability.EMAIL_VERIFY not in capabilities:
        return True
    result = router.fetch(
        Capability.EMAIL_VERIFY, {"email": email}, person_id=person_id
    )
    record = result.first
    if record is None:
        # No verifier configured or reachable — keep the address rather than
        # discard a paid-for field on a missing optional check.
        return True
    status = (_first_str(record, "status", "result", "deliverability") or "").lower()
    if not status:
        return True
    return status in {"valid", "deliverable", "ok", "accept_all", "risky"}


def enrich_event_people(
    conn: sqlite3.Connection,
    router: ProviderRouter,
    *,
    event_id: int,
    tier: str,
    limit: int = 50,
) -> EnrichmentOutcome:
    """Enrich the people attached to an event, best-first.

    Stops cleanly when the budget runs out rather than failing the job: a
    partially enriched room is deliverable, a crashed run is not.
    """
    outcome = EnrichmentOutcome()
    if not CAPABILITIES_BY_TIER.get(tier):
        return outcome

    rows = conn.execute(
        """
        SELECT p.* FROM event_people ep
        JOIN people p ON p.id = ep.person_id
        WHERE ep.event_id = ?
        ORDER BY ep.is_confirmed DESC, ep.relevance_score DESC NULLS LAST
        LIMIT ?
        """,
        (event_id, limit),
    ).fetchall()

    for row in rows:
        try:
            updates = enrich_person(conn, router, row, tier=tier)
        except BudgetExceeded as exc:
            outcome.budget_exhausted = True
            outcome.notes.append(str(exc))
            break
        if updates:
            outcome.people_enriched += 1
            if updates.get("email"):
                outcome.emails_found += 1
            if updates.get("phone"):
                outcome.phones_found += 1

    return outcome
