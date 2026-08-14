from __future__ import annotations

import json

import pytest
import requests

from invite_finder import db
from invite_finder.brightdata import BrightDataClient
from invite_finder.cache import CacheMiss, CachingSession, derive_kind, fingerprint, normalize_url
from invite_finder.config import Settings


def make_settings() -> Settings:
    return Settings(
        brightdata_api_key="test-key",
        brightdata_serp_zone="zone-a",
        brightdata_unlocker_zone="zone-b",
    )


def _fake_send(body: str, call_count: dict[str, int]):
    """Builds a `session.send` replacement so CachingSession's own request()
    override still runs (and can decide to short-circuit on a cache hit); only
    a genuine cache miss reaches this fake network boundary."""

    def send(request, **kwargs):
        call_count["n"] += 1
        response = requests.Response()
        response.status_code = 200
        response.headers["content-type"] = "application/json"
        response._content = json.dumps({"body": body}).encode("utf-8")
        response.url = request.url
        response.request = request
        return response

    return send


def test_fingerprint_ignores_zone_and_tracking_params() -> None:
    payload_a = {"zone": "serp_api1", "url": "https://example.com/x?utm_source=foo&b=2&a=1"}
    payload_b = {"zone": "serp_api2", "url": "https://example.com/x?a=1&b=2"}
    assert fingerprint(payload_a) == fingerprint(payload_b)


def test_normalize_url_strips_tracking_and_sorts_params() -> None:
    normalized = normalize_url("https://EXAMPLE.com/x/?b=2&a=1&trk=abc&utm_campaign=y")
    assert normalized == "https://example.com/x?a=1&b=2"


def test_derive_kind_matches_expected_buckets() -> None:
    assert derive_kind("https://api.lu.ma/url?url=foo") == "luma_api"
    assert derive_kind("https://luma.com/vla-night-panel") == "luma_page"
    assert derive_kind("https://www.google.com/search?q=x") == "serp"
    assert derive_kind("https://www.linkedin.com/in/foo") == "linkedin_profile"
    assert derive_kind("https://example.com/other") == "page"


def test_second_identical_unlock_url_call_hits_cache_not_network() -> None:
    conn = db.connect(":memory:")
    settings = make_settings()
    call_count = {"n": 0}

    session = CachingSession(conn)
    session.send = _fake_send("# hello world", call_count)  # type: ignore[method-assign]
    client = BrightDataClient(settings, session=session)

    first = client.unlock_url("https://example.com/page-one", data_format="markdown")
    second = client.unlock_url("https://example.com/page-one", data_format="markdown")

    assert first == second == "# hello world"
    assert call_count["n"] == 1
    assert session.last_was_cache_hit is True


def test_zone_rename_does_not_invalidate_cache() -> None:
    conn = db.connect(":memory:")
    call_count = {"n": 0}

    session = CachingSession(conn)
    session.send = _fake_send("same body", call_count)  # type: ignore[method-assign]

    client_a = BrightDataClient(make_settings(), session=session)
    client_a.unlock_url("https://example.com/page-one", data_format="markdown")

    settings_b = Settings(
        brightdata_api_key="test-key",
        brightdata_serp_zone="zone-a",
        brightdata_unlocker_zone="a-totally-renamed-zone",
    )
    client_b = BrightDataClient(settings_b, session=session)
    client_b.unlock_url("https://example.com/page-one", data_format="markdown")

    assert call_count["n"] == 1
    assert session.last_was_cache_hit is True


def test_tracking_params_do_not_bust_cache() -> None:
    conn = db.connect(":memory:")
    call_count = {"n": 0}

    session = CachingSession(conn)
    session.send = _fake_send("tracked body", call_count)  # type: ignore[method-assign]
    client = BrightDataClient(make_settings(), session=session)

    client.unlock_url("https://example.com/page?utm_source=newsletter", data_format="markdown")
    client.unlock_url("https://example.com/page", data_format="markdown")

    assert call_count["n"] == 1


def test_offline_mode_raises_cache_miss_when_uncached() -> None:
    conn = db.connect(":memory:")
    settings = make_settings()
    session = CachingSession(conn, offline=True)
    client = BrightDataClient(settings, session=session)

    with pytest.raises(CacheMiss):
        client.unlock_url("https://example.com/never-fetched")


def test_offline_mode_serves_previously_cached_response() -> None:
    conn = db.connect(":memory:")
    settings = make_settings()
    call_count = {"n": 0}

    online_session = CachingSession(conn)
    online_session.send = _fake_send("cached body", call_count)  # type: ignore[method-assign]
    online_client = BrightDataClient(settings, session=online_session)
    online_client.unlock_url("https://example.com/warm")

    offline_session = CachingSession(conn, offline=True)
    offline_client = BrightDataClient(settings, session=offline_session)
    result = offline_client.unlock_url("https://example.com/warm")

    assert result == "cached body"
    assert call_count["n"] == 1


def test_force_refresh_bypasses_cache_but_still_writes() -> None:
    conn = db.connect(":memory:")
    settings = make_settings()
    call_count = {"n": 0}

    session = CachingSession(conn, force_refresh=True)
    session.send = _fake_send("refreshed body", call_count)  # type: ignore[method-assign]
    client = BrightDataClient(settings, session=session)

    client.unlock_url("https://example.com/page-one", data_format="markdown")
    client.unlock_url("https://example.com/page-one", data_format="markdown")

    assert call_count["n"] == 2
