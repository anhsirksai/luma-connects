from __future__ import annotations

import json
from pathlib import Path

import pytest

from invite_finder.luma import (
    LumaParseError,
    LumaUrlError,
    fetch_luma_event,
    linkedin_url_from_handle,
    parse_luma_api_payload,
    parse_luma_jsonld,
    parse_luma_slug,
    speaker_names_from_description,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "luma_api_vla_night_panel.json"


def load_fixture_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def test_parse_luma_slug_accepts_common_forms() -> None:
    assert parse_luma_slug("https://luma.com/vla-night-panel") == "vla-night-panel"
    assert parse_luma_slug("https://lu.ma/vla-night-panel") == "vla-night-panel"
    assert parse_luma_slug("lu.ma/vla-night-panel") == "vla-night-panel"
    assert parse_luma_slug("https://luma.com/vla-night-panel?tk=abc123") == "vla-night-panel"


def test_parse_luma_slug_rejects_non_event_paths() -> None:
    with pytest.raises(LumaUrlError):
        parse_luma_slug("https://luma.com/u/someuser")
    with pytest.raises(LumaUrlError):
        parse_luma_slug("https://luma.com/discover")
    with pytest.raises(LumaUrlError):
        parse_luma_slug("https://example.com/vla-night-panel")


def test_linkedin_url_from_handle_splits_person_and_company() -> None:
    person_url, person_company = linkedin_url_from_handle("/in/karim-baba-130547289")
    assert person_url == "https://www.linkedin.com/in/karim-baba-130547289"
    assert person_company is None

    company_url, company_company = linkedin_url_from_handle("/company/bright-data")
    assert company_url is None
    assert company_company == "https://www.linkedin.com/company/bright-data"

    assert linkedin_url_from_handle(None) == (None, None)


def test_parse_luma_api_payload_extracts_full_fidelity_event() -> None:
    payload = load_fixture_payload()
    event = parse_luma_api_payload(
        "vla-night-panel", "https://luma.com/vla-night-panel", payload
    )

    assert event.ingest_source == "luma_api"
    assert event.guest_count == 552
    assert event.venue.city == "San Francisco"
    assert event.timezone == "America/Los_Angeles"
    assert len(event.people) == 12  # 2 hosts + 10 featured guests

    by_luma_id = {p.luma_user_api_id: p for p in event.people}
    bright_data_host = by_luma_id["usr-brightdataorg0001"]
    assert bright_data_host.linkedin_url is None
    assert bright_data_host.linkedin_company_url == "https://www.linkedin.com/company/bright-data"

    human_host = by_luma_id["usr-itsajchan0001"]
    assert human_host.linkedin_url == "https://www.linkedin.com/in/itsajchan"
    assert human_host.relation == "host"

    featured = [p for p in event.people if p.relation == "featured_guest"]
    assert len(featured) == 10
    assert all(p.linkedin_url for p in featured)

    assert event.warnings == []


def test_parse_luma_api_payload_warns_when_no_people() -> None:
    payload = {
        "data": {
            "event": {"api_id": "evt-x", "name": "Empty Event"},
            "guest_count": 10,
            "hosts": [],
            "featured_guests": [],
        }
    }
    event = parse_luma_api_payload("empty-event", "https://luma.com/empty-event", payload)
    assert event.people == []
    assert any("no hosts or featured guests" in w for w in event.warnings)


def test_parse_luma_api_payload_handles_rich_text_description() -> None:
    """Some events return description/description_mirror as a Lexical/
    ProseMirror-style rich-text doc object instead of a plain string. This
    must not raise -- it previously crashed with a pydantic ValidationError
    and silently degraded the whole event to the JSON-LD fallback tier."""
    payload = {
        "data": {
            "event": {"api_id": "evt-rich", "name": "Rich Text Event"},
            "description_mirror": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": "Join us for a deep dive."},
                            {"type": "text", "text": " Bring a laptop."},
                        ],
                    }
                ],
            },
            "hosts": [],
            "featured_guests": [],
        }
    }
    event = parse_luma_api_payload("rich-text-event", "https://luma.com/rich-text-event", payload)
    assert event.description == "Join us for a deep dive. Bring a laptop."


def test_parse_luma_api_payload_handles_object_categories() -> None:
    """Some events return categories as a list of category objects
    ({api_id, name, tint_color, ...}) instead of plain strings."""
    payload = {
        "data": {
            "event": {"api_id": "evt-cat", "name": "Categorized Event"},
            "categories": [
                {"api_id": "cat-ai", "name": "AI", "tint_color": "#dd7aa4"},
                {"api_id": "cat-startups", "title": "Startups", "tint_color": "#4a90d9"},
                "Plain String Category",
            ],
            "hosts": [],
            "featured_guests": [],
        }
    }
    event = parse_luma_api_payload("categorized-event", "https://luma.com/categorized-event", payload)
    assert event.categories == ["AI", "Startups", "Plain String Category"]


def test_parse_luma_api_payload_handles_rich_text_bio() -> None:
    payload = {
        "data": {
            "event": {"api_id": "evt-bio", "name": "Bio Event"},
            "hosts": [],
            "featured_guests": [
                {
                    "api_id": "usr-1",
                    "name": "Jamie Rivers",
                    "bio_short": {"type": "doc", "content": [{"type": "text", "text": "Investor."}]},
                }
            ],
        }
    }
    event = parse_luma_api_payload("bio-event", "https://luma.com/bio-event", payload)
    assert event.people[0].bio_short == "Investor."


def test_parse_luma_jsonld_fallback() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Event",
      "name": "From Great Demo to Signed Deal",
      "startDate": "2026-08-14T09:30:00-07:00",
      "endDate": "2026-08-14T11:00:00-07:00",
      "description": "The founder GTM playbook.",
      "image": "https://images.lumacdn.com/event-covers/gtm-playbook.jpg",
      "location": {
        "@type": "Place",
        "name": "Remote",
        "address": {
          "@type": "PostalAddress",
          "addressLocality": "San Francisco",
          "addressRegion": "CA",
          "addressCountry": "US"
        }
      }
    }
    </script>
    </head><body></body></html>
    """
    event = parse_luma_jsonld("gtm-playbook", "https://luma.com/gtm-playbook", html)
    assert event.ingest_source == "luma_jsonld"
    assert event.name == "From Great Demo to Signed Deal"
    assert event.venue.city == "San Francisco"
    assert event.guest_count is None
    assert event.people == []
    assert any("Guest count and guest list are not available" in w for w in event.warnings)


def test_parse_luma_jsonld_raises_on_garbage_html() -> None:
    with pytest.raises(LumaParseError):
        parse_luma_jsonld("garbage", "https://luma.com/garbage", "<html><body>nope</body></html>")


class _StubClient:
    """Minimal WebDataClient stub for exercising fetch_luma_event's tiers."""

    def __init__(self, responses: dict[str, str | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def unlock_url(self, url, *, data_format="markdown", response_format="raw", country=None):
        self.calls.append(url)
        result = self.responses.get(url)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise KeyError(f"No stubbed response for {url}")
        return result

    def google_search(self, *args, **kwargs):  # pragma: no cover - unused here
        raise NotImplementedError


def test_fetch_luma_event_uses_api_tier_when_available() -> None:
    payload_text = FIXTURE_PATH.read_text()
    client = _StubClient({"https://api.lu.ma/url?url=vla-night-panel": payload_text})

    event = fetch_luma_event(client, "https://luma.com/vla-night-panel")
    assert event.ingest_source == "luma_api"
    assert event.guest_count == 552
    assert client.calls == ["https://api.lu.ma/url?url=vla-night-panel"]


def test_fetch_luma_event_falls_through_to_markdown_when_all_tiers_fail() -> None:
    client = _StubClient(
        {
            "https://api.lu.ma/url?url=broken-event": "not json at all {{{",
            "https://luma.com/broken-event": "some html with no jsonld",
        }
    )
    # The second unlock_url call (JSON-LD tier) reuses the same URL as the
    # markdown tier in this stub, so give the same non-JSON-LD HTML both times
    # by making the stub return it regardless of data_format.

    event = fetch_luma_event(client, "https://luma.com/broken-event")
    assert event.ingest_source == "luma_markdown"
    assert event.guest_count is None
    assert event.people == []
    assert len(event.warnings) == 3


def test_speaker_names_from_description() -> None:
    text = "Join us for a fireside chat. Speakers: Karim Baba, Priya Natarajan and Daniel Osei. RSVP now."
    assert speaker_names_from_description(text) == [
        "Karim Baba",
        "Priya Natarajan",
        "Daniel Osei",
    ]
    assert speaker_names_from_description(None) == []
    assert speaker_names_from_description("No speaker info here.") == []
