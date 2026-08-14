from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from invite_finder.agent import normalize_linkedin_profile_url
from invite_finder.event import extract_event_page_context
from invite_finder.luma_models import LumaEvent, LumaPerson, LumaVenue
from invite_finder.protocols import WebDataClient

LUMA_HOSTS = {"lu.ma", "www.lu.ma", "luma.com", "www.luma.com"}
REJECTED_PATH_PREFIXES = ("u/", "calendar/", "discover")


class LumaUrlError(ValueError):
    """Raised when a URL does not point to a single public Luma event."""


class LumaParseError(RuntimeError):
    """Raised when a Luma response could not be parsed by any ingestion tier."""


def parse_luma_slug(url: str) -> str:
    candidate = url.strip()
    if not candidate:
        raise LumaUrlError("Empty URL.")
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parts = urlsplit(candidate)
    host = parts.netloc.lower()
    if host not in LUMA_HOSTS:
        raise LumaUrlError(f"Not a Luma URL: {url}")

    path = parts.path.strip("/")
    if not path:
        raise LumaUrlError(f"No event slug in URL: {url}")
    if path == "discover" or path.startswith(REJECTED_PATH_PREFIXES):
        raise LumaUrlError(f"URL does not point to a single event: {url}")

    return path.split("/")[0]


def linkedin_url_from_handle(handle: str | None) -> tuple[str | None, str | None]:
    """Returns (linkedin_url, linkedin_company_url). Exactly one is non-None
    (unless the handle is empty/unparseable, in which case both are None)."""
    if not handle:
        return None, None

    handle = handle.strip()
    if handle.startswith(("http://", "https://")):
        candidate = handle
    elif handle.lstrip("/").startswith("company/"):
        company_path = handle.lstrip("/")
        return None, f"https://www.linkedin.com/{company_path}"
    elif handle.lstrip("/").startswith("in/"):
        candidate = f"https://www.linkedin.com/{handle.lstrip('/')}"
    else:
        candidate = f"https://www.linkedin.com/in/{handle.lstrip('/')}"

    normalized = normalize_linkedin_profile_url(candidate)
    return normalized, None


def speaker_names_from_description(description: str | None) -> list[str]:
    """Best-effort, low-confidence hints only -- never treat as confirmed
    attendees. Looks for a 'Speakers:' style line and splits the names."""
    if not description:
        return []
    match = re.search(r"speakers?:\s*(.+)", description, re.IGNORECASE)
    if not match:
        return []
    tail = re.split(r"[.\n]", match.group(1), maxsplit=1)[0]
    names = [n.strip() for n in re.split(r",| and ", tail) if n.strip()]
    return names


def _parse_json_body(body: str) -> dict | None:
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _extract_plain_text(value: object) -> str | None:
    """Best-effort plain text extraction. Luma's description/description_mirror
    fields are a plain string for some events, and a rich-text document object
    (Lexical/ProseMirror-style {"type": "doc", "content": [...]}) for others.
    Walks any "content" lists collecting "text" leaves; never raises."""
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None

    parts: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str):
                parts.append(text)
            for child in node.get("content") or []:
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    joined = " ".join(p.strip() for p in parts if p.strip())
    return joined or None


def _extract_category_names(categories: object) -> list[str]:
    """Categories are a list of plain strings for some events, and a list of
    category objects ({"api_id", "name"/"title", "tint_color", ...}) for
    others. Extracts a display name from either shape."""
    names: list[str] = []
    for item in categories or []:
        if isinstance(item, str) and item:
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("title") or item.get("label")
            if isinstance(name, str) and name:
                names.append(name)
    return names


def parse_luma_api_payload(slug: str, source_url: str, payload: dict) -> LumaEvent:
    data = payload.get("data", payload)
    event = data.get("event") or {}
    geo = event.get("geo_address_info") or {}
    coord = event.get("coordinate") or {}

    venue = LumaVenue(
        name=geo.get("address") or geo.get("short_address"),
        address=geo.get("full_address") or geo.get("address"),
        city=geo.get("city"),
        region=geo.get("region"),
        country=geo.get("country"),
        latitude=coord.get("latitude") if coord else event.get("geo_latitude"),
        longitude=coord.get("longitude") if coord else event.get("geo_longitude"),
    )

    people: list[LumaPerson] = []
    for host in data.get("hosts") or []:
        li_url, li_company = linkedin_url_from_handle(host.get("linkedin_handle"))
        people.append(
            LumaPerson(
                luma_user_api_id=host.get("api_id"),
                name=host.get("name"),
                avatar_url=host.get("avatar_url"),
                linkedin_url=li_url,
                linkedin_company_url=li_company,
                relation="host",
            )
        )

    for guest in data.get("featured_guests") or []:
        li_url, li_company = linkedin_url_from_handle(guest.get("linkedin_handle"))
        people.append(
            LumaPerson(
                luma_user_api_id=guest.get("api_id"),
                name=guest.get("name"),
                bio_short=_extract_plain_text(guest.get("bio_short")),
                avatar_url=guest.get("avatar_url"),
                linkedin_url=li_url,
                linkedin_company_url=li_company,
                relation="featured_guest",
            )
        )

    warnings: list[str] = []
    if not data.get("hosts") and not data.get("featured_guests"):
        warnings.append(
            "Luma API returned no hosts or featured guests for this event; the "
            "room snapshot will rely entirely on SERP-inferred people."
        )
    if not event.get("name"):
        warnings.append("Luma API payload had no event name.")

    location_type = "offline" if geo.get("city") else None

    return LumaEvent(
        slug=slug,
        source_url=source_url,
        luma_api_id=event.get("api_id"),
        name=event.get("name") or slug,
        description=(
            _extract_plain_text(data.get("description_mirror"))
            or _extract_plain_text(event.get("description_short"))
        ),
        cover_url=event.get("cover_url"),
        start_at=event.get("start_at"),
        end_at=event.get("end_at"),
        timezone=event.get("timezone"),
        location_type=location_type,
        venue=venue,
        guest_count=data.get("guest_count"),
        show_guest_list=bool(event.get("show_guest_list")),
        categories=_extract_category_names(data.get("categories")),
        people=people,
        ingest_source="luma_api",
        warnings=warnings,
        raw=data,
    )


def parse_luma_jsonld(slug: str, source_url: str, html: str) -> LumaEvent:
    soup = BeautifulSoup(html, "html.parser")
    event_data: dict | None = None

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict) and "Event" in str(candidate.get("@type", "")):
                event_data = candidate
                break
        if event_data:
            break

    if not event_data:
        raise LumaParseError("No schema.org Event JSON-LD found on the page.")

    location = event_data.get("location")
    address = location.get("address") if isinstance(location, dict) else None
    if isinstance(address, dict):
        venue = LumaVenue(
            name=location.get("name") if isinstance(location, dict) else None,
            address=address.get("streetAddress"),
            city=address.get("addressLocality"),
            region=address.get("addressRegion"),
            country=address.get("addressCountry"),
        )
    else:
        venue = LumaVenue(name=location.get("name") if isinstance(location, dict) else None)

    image = event_data.get("image")
    if isinstance(image, list):
        image = image[0] if image else None

    return LumaEvent(
        slug=slug,
        source_url=source_url,
        name=event_data.get("name") or slug,
        description=event_data.get("description"),
        cover_url=image,
        start_at=event_data.get("startDate"),
        end_at=event_data.get("endDate"),
        venue=venue,
        ingest_source="luma_jsonld",
        warnings=[
            "Guest count and guest list are not available from JSON-LD; only "
            "event metadata (title, date, venue, cover) was recovered.",
        ],
        raw=event_data,
    )


def fetch_luma_event(
    client: WebDataClient, url: str, *, allow_fallbacks: bool = True
) -> LumaEvent:
    slug = parse_luma_slug(url)
    source_url = f"https://luma.com/{slug}"
    api_url = f"https://api.lu.ma/url?url={slug}"

    api_error: str | None = None
    try:
        body = client.unlock_url(api_url, data_format=None)
        payload = _parse_json_body(body)
        if payload is not None:
            return parse_luma_api_payload(slug, source_url, payload)
        api_error = "api.lu.ma response did not parse as JSON."
    except Exception as exc:  # noqa: BLE001 - deliberately fall through a tier
        api_error = str(exc)

    if not allow_fallbacks:
        raise LumaParseError(f"Luma API tier failed and fallbacks are disabled: {api_error}")

    jsonld_error: str | None = None
    try:
        html = client.unlock_url(source_url, data_format=None)
        event = parse_luma_jsonld(slug, source_url, html)
        event.warnings.insert(0, f"Fell back from Luma API tier: {api_error}")
        return event
    except Exception as exc:  # noqa: BLE001
        jsonld_error = str(exc)

    markdown = client.unlock_url(source_url, data_format="markdown")
    context = extract_event_page_context(source_url, markdown)
    return LumaEvent(
        slug=slug,
        source_url=source_url,
        name=context.title or slug,
        description=context.description or (context.text[:500] if context.text else None),
        ingest_source="luma_markdown",
        warnings=[
            f"Fell back from Luma API tier: {api_error}",
            f"Fell back from JSON-LD tier: {jsonld_error}",
            "Only a title/description could be recovered; no guest count, "
            "venue, or guest list is available for this event.",
        ],
        raw=None,
    )
