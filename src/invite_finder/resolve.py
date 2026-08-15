"""Turn a pasted guest list into resolved people.

This is the paid path, and it is the opposite shape from `agent.py`'s discovery:
that one trawls a city and a topic to *infer* who might attend, this one takes
names the customer already holds and resolves each to a real profile.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from invite_finder.providers.base import Capability
from invite_finder.providers.router import ProviderRouter
from invite_finder.store import people_store

# "Name — Company", "Name - Company", "Name, Company", "Name (Company)" and
# "Name @ Company" all appear in guest lists people actually paste.
SEPARATORS = re.compile(r"\s+[—–-]\s+|\s*[|,]\s*|\s+@\s+")
PARENTHETICAL = re.compile(r"^(?P<name>[^(]+)\((?P<company>[^)]+)\)\s*$")
# A name line has letters and at most a handful of words.
NAME_LIKE = re.compile(r"^[\w.'’\-]+(?:\s+[\w.'’\-]+){0,4}$", re.UNICODE)


@dataclass(frozen=True)
class GuestEntry:
    name: str
    company: str | None = None


def parse_guest_list(text: str, *, limit: int = 500) -> list[GuestEntry]:
    """Parse a pasted guest list into name/company pairs.

    Deliberately forgiving about format and strict about what counts as a name,
    because the alternative — resolving junk lines — spends real money per row.
    """
    entries: list[GuestEntry] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("-•*0123456789. \t")
        if not line or len(line) > 200:
            continue
        if line.lower().startswith(("http://", "https://")):
            continue

        company: str | None = None
        match = PARENTHETICAL.match(line)
        if match:
            name = match.group("name").strip()
            company = match.group("company").strip()
        else:
            pieces = [p.strip() for p in SEPARATORS.split(line) if p.strip()]
            name = pieces[0] if pieces else ""
            company = pieces[1] if len(pieces) > 1 else None

        if not name or not NAME_LIKE.match(name):
            continue
        # A single word is more often a header ("Guests", "Confirmed") than a
        # person we can resolve.
        if len(name.split()) < 2:
            continue

        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        entries.append(GuestEntry(name=name, company=company or None))
        if len(entries) >= limit:
            break

    return entries


def looks_like_guest_list(text: str, *, minimum: int = 3) -> bool:
    return len(parse_guest_list(text, limit=minimum)) >= minimum


def _split_name(name: str) -> tuple[str, str]:
    parts = name.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _first_str(record: dict[str, Any], *keys: str) -> str | None:
    """Providers disagree on field names for the same thing, so probe."""
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def profile_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize one provider record into our people columns."""
    return {
        "linkedin_url": _first_str(
            record, "linkedin_url", "profile_url", "profileUrl", "url", "link"
        ),
        "name": _first_str(record, "name", "full_name", "fullName"),
        "headline": _first_str(record, "headline", "title", "job_title", "occupation"),
        "company": _first_str(
            record, "company", "company_name", "current_company", "companyName"
        ),
        "location_text": _first_str(record, "location", "location_text", "city"),
        "bio_short": _first_str(record, "summary", "about", "bio"),
        "avatar_url": _first_str(record, "avatar", "photo_url", "profile_picture"),
    }


def resolve_person(
    router: ProviderRouter,
    entry: GuestEntry,
    *,
    location: str | None = None,
    person_id: int | None = None,
) -> dict[str, Any] | None:
    """Resolve one name to a profile. Returns None when nothing matched —
    a miss is normal and must not fail the surrounding job."""
    first, last = _split_name(entry.name)
    payload: dict[str, Any] = {
        "firstname": first,
        "lastname": last,
        "max_profiles": 1,
    }
    if entry.company:
        payload["current_job_title"] = entry.company
    if location:
        payload["location"] = location

    result = router.fetch(
        Capability.LINKEDIN_BY_NAME, payload, person_id=person_id
    )
    record = result.first
    if record is None:
        return None

    profile = profile_from_record(record)
    # Keep the customer's own labels when the provider has nothing better —
    # they came off a real guest list and are often more accurate.
    profile["name"] = profile["name"] or entry.name
    profile["company"] = profile["company"] or entry.company
    return profile


def resolve_guest_list(
    conn: sqlite3.Connection,
    router: ProviderRouter,
    entries: list[GuestEntry],
    *,
    event_id: int,
    run_id: int | None = None,
    location: str | None = None,
) -> int:
    """Resolve every entry and link it to the event as a confirmed attendee.

    These are confirmed in the sense that matters: the customer supplied the
    list. That is the whole difference between this path and SERP inference.
    """
    resolved = 0
    for entry in entries:
        profile = resolve_person(router, entry, location=location)
        if profile is None:
            # Still record the person from the customer's own list, so an
            # unresolved guest is visible rather than silently dropped.
            person_id = people_store.upsert_person(
                conn, name=entry.name, company=entry.company
            )
        else:
            person_id = people_store.upsert_person(conn, **profile)
            resolved += 1

        people_store.link_person_to_event(
            conn,
            event_id=event_id,
            person_id=person_id,
            relation="attendee",
            is_confirmed=True,
            evidence=["Supplied by customer from the event guest list"],
            run_id=run_id,
        )
    return resolved
