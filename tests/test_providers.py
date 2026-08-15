from __future__ import annotations

import json

import pytest
import requests

from invite_finder import cache, db
from invite_finder.cache import CachingSession
from invite_finder.config import Settings
from invite_finder.providers.apify import ApifyChannel
from invite_finder.providers.base import (
    BudgetExceeded,
    Capability,
    ProviderError,
    ProviderResult,
)
from invite_finder.providers.router import ProviderRouter
from invite_finder.store import commerce_store
from invite_finder.resolve import GuestEntry, looks_like_guest_list, parse_guest_list


def make_conn():
    return db.connect(":memory:")


def make_settings(**overrides) -> Settings:
    base = {
        "apify_token": "test-token",
        "enrichment_budget_cents": 500,
        "invite_offline": False,
    }
    base.update(overrides)
    return Settings(**base)


class StubChannel:
    """A channel that records calls, so tests can prove the router did or did
    not reach a provider."""

    def __init__(self, name="stub", records=None, fail=False, cost=7):
        self.name = name
        self.channel = f"{name}_channel"
        self.calls = 0
        self._records = records if records is not None else [{"name": "Ada"}]
        self._fail = fail
        self._cost = cost

    def supports(self, capability: str) -> bool:
        return True

    def fetch(self, capability: str, payload: dict) -> ProviderResult:
        self.calls += 1
        if self._fail:
            raise ProviderError(f"{self.name} is down")
        return ProviderResult(
            capability=capability,
            records=self._records,
            provider=self.name,
            channel=self.channel,
            charged_cents=self._cost,
        )


# --- cache fingerprinting ----------------------------------------------------


def test_endpoint_folds_into_the_cache_key_for_channel_b() -> None:
    """Two actors called with identical input must not collide. Without the
    endpoint in the key they would serve each other's answers."""
    payload = {"firstname": "Ada", "lastname": "Lovelace"}
    a = cache.fingerprint(payload, endpoint="https://api.apify.com/v2/acts/actor-a/run")
    b = cache.fingerprint(payload, endpoint="https://api.apify.com/v2/acts/actor-b/run")
    assert a != b


def test_bright_data_fingerprints_are_unchanged_by_the_endpoint_support() -> None:
    """Bright Data carries its target in the body, so adding endpoint folding
    must not invalidate the existing cache (or every seeded fixture breaks)."""
    payload = {"zone": "z1", "url": "https://lu.ma/x", "format": "json"}
    assert cache.fingerprint(payload) == cache.fingerprint(
        payload, endpoint="https://api.brightdata.com/request"
    )


def test_volatile_keys_do_not_bust_the_cache() -> None:
    """mandate_id and max_price change on every Perflo purchase. If they
    reached the fingerprint, every call would miss — and every miss is a
    charge."""
    base = {"target": {"service_id": "svc_1"}, "input": {"email": "a@b.com"}}
    first = cache.fingerprint(
        {**base, "mandate_id": "m1", "max_price": {"amount": "0.05"}},
        endpoint="https://api.perflo.ai/v1/purchases",
    )
    second = cache.fingerprint(
        {**base, "mandate_id": "m2", "max_price": {"amount": "0.25"}},
        endpoint="https://api.perflo.ai/v1/purchases",
    )
    assert first == second


def test_secret_query_params_never_reach_the_cache_key_or_url() -> None:
    with_token = "https://api.apify.com/v2/acts/a/run?token=SECRET&x=1"
    stripped = cache.strip_secret_params(with_token)
    assert "SECRET" not in stripped
    assert "x=1" in stripped
    # A rotated token must not invalidate the cache.
    rotated = "https://api.apify.com/v2/acts/a/run?token=OTHER&x=1"
    assert cache.fingerprint({"a": 1}, endpoint=with_token) == cache.fingerprint(
        {"a": 1}, endpoint=rotated
    )


# --- router ------------------------------------------------------------------


def test_router_serves_the_second_call_from_cache_and_charges_nothing() -> None:
    """The margin guarantee: repeating an enrichment must not reach a paid
    provider a second time."""
    conn = make_conn()
    channel = StubChannel(cost=25)
    router = ProviderRouter(conn, make_settings(), channels=[channel])

    first = router.fetch(Capability.CONTACT_ENRICH, {"name": "Ada"})
    assert first.records and channel.calls == 1
    assert commerce_store.spend_cents(conn) == 25

    second = router.fetch(Capability.CONTACT_ENRICH, {"name": "Ada"})
    assert second.records == first.records
    assert second.cache_hit is True
    assert channel.calls == 1, "second call must not reach the provider"
    assert commerce_store.spend_cents(conn) == 25, "a cache hit must cost nothing"


def test_router_distinguishes_capabilities_with_identical_payloads() -> None:
    conn = make_conn()
    channel = StubChannel()
    router = ProviderRouter(conn, make_settings(), channels=[channel])

    router.fetch(Capability.CONTACT_ENRICH, {"name": "Ada"})
    router.fetch(Capability.X_PROFILE, {"name": "Ada"})
    assert channel.calls == 2


def test_router_falls_back_to_the_next_channel_and_pays_once() -> None:
    conn = make_conn()
    broken = StubChannel(name="perflo", fail=True)
    working = StubChannel(name="apify", cost=5)
    router = ProviderRouter(conn, make_settings(), channels=[broken, working])

    result = router.fetch(Capability.LINKEDIN_BY_NAME, {"firstname": "Ada"})
    assert result.records
    assert result.provider == "apify"
    assert broken.calls == 1 and working.calls == 1
    assert commerce_store.spend_cents(conn) == 5

    # The failure is on the ledger, so a silent fallback is still auditable.
    statuses = [
        r["status"]
        for r in conn.execute("SELECT status FROM service_purchases ORDER BY id").fetchall()
    ]
    assert statuses == ["failed", "ok"]


def test_router_returns_empty_rather_than_raising_when_all_channels_fail() -> None:
    conn = make_conn()
    router = ProviderRouter(
        conn, make_settings(), channels=[StubChannel(name="a", fail=True)]
    )
    result = router.fetch(Capability.X_PROFILE, {"name": "Ada"})
    assert not result.records
    assert result.channel == "exhausted"


def test_router_refuses_to_spend_past_the_budget() -> None:
    conn = make_conn()
    channel = StubChannel(cost=400)
    router = ProviderRouter(conn, make_settings(enrichment_budget_cents=500), channels=[channel])

    router.fetch(Capability.CONTACT_ENRICH, {"name": "one"})
    assert router.remaining_cents() == 100

    router.fetch(Capability.CONTACT_ENRICH, {"name": "two"})
    assert router.remaining_cents() == 0

    with pytest.raises(BudgetExceeded):
        router.fetch(Capability.CONTACT_ENRICH, {"name": "three"})
    assert channel.calls == 2, "the over-budget call must not reach the provider"


def test_router_skips_paid_calls_entirely_when_offline() -> None:
    conn = make_conn()
    channel = StubChannel()
    router = ProviderRouter(conn, make_settings(invite_offline=True), channels=[channel])
    result = router.fetch(Capability.CONTACT_ENRICH, {"name": "Ada"})
    assert not result.records
    assert channel.calls == 0


# --- apify channel -----------------------------------------------------------


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession(requests.Session):
    def __init__(self, response):
        super().__init__()
        self._response = response
        self.last_url = None
        self.last_kwargs = None

    def post(self, url, **kwargs):  # type: ignore[override]
        self.last_url = url
        self.last_kwargs = kwargs
        return self._response


def test_apify_channel_reports_per_result_cost() -> None:
    session = FakeSession(FakeResponse([{"name": "Ada"}] * 200))
    channel = ApifyChannel(make_settings(), session=session)

    result = channel.fetch(Capability.LINKEDIN_BY_NAME, {"firstname": "Ada"})
    assert len(result.records) == 200
    # 200 results at $5.00/1k = $1.00.
    assert result.charged_cents == 100
    assert session.last_kwargs["cache_kind"] == "linkedin_search"


def test_apify_channel_charges_nothing_on_a_cache_hit() -> None:
    session = FakeSession(
        FakeResponse([{"name": "Ada"}], headers={"x-invite-finder-cache": "hit"})
    )
    channel = ApifyChannel(make_settings(), session=session)
    result = channel.fetch(Capability.LINKEDIN_BY_NAME, {"firstname": "Ada"})
    assert result.cache_hit is True
    assert result.charged_cents == 0


def test_apify_channel_is_disabled_without_a_token() -> None:
    channel = ApifyChannel(make_settings(apify_token=""))
    assert not channel.supports(Capability.LINKEDIN_BY_NAME)
    with pytest.raises(ProviderError):
        channel.fetch(Capability.LINKEDIN_BY_NAME, {})


def test_apify_channel_surfaces_http_failures_as_provider_errors() -> None:
    session = FakeSession(FakeResponse({"error": "nope"}, status_code=500))
    channel = ApifyChannel(make_settings(), session=session)
    with pytest.raises(ProviderError):
        channel.fetch(Capability.LINKEDIN_BY_NAME, {"firstname": "Ada"})


def test_caching_session_accepts_cache_kind_without_leaking_it_upstream() -> None:
    """cache_kind is ours; passing it to requests would raise."""
    conn = make_conn()
    session = CachingSession(conn, offline=True)
    with pytest.raises(cache.CacheMiss):
        session.post(
            "https://api.apify.com/v2/acts/a/run-sync-get-dataset-items?token=t",
            json={"firstname": "Ada"},
            cache_kind="linkedin_search",
        )


# --- guest list parsing ------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Ada Lovelace — Analytical Engines", GuestEntry("Ada Lovelace", "Analytical Engines")),
        ("Ada Lovelace (Analytical Engines)", GuestEntry("Ada Lovelace", "Analytical Engines")),
        ("Ada Lovelace, Analytical Engines", GuestEntry("Ada Lovelace", "Analytical Engines")),
        ("Ada Lovelace @ Analytical Engines", GuestEntry("Ada Lovelace", "Analytical Engines")),
        ("Ada Lovelace", GuestEntry("Ada Lovelace", None)),
    ],
)
def test_guest_list_parses_the_formats_people_actually_paste(line, expected) -> None:
    assert parse_guest_list(line) == [expected]


def test_guest_list_rejects_junk_that_would_cost_money_to_resolve() -> None:
    text = "\n".join(
        [
            "Guests",                      # single word header
            "https://lu.ma/some-event",    # a URL
            "",                            # blank
            "Ada Lovelace",                # real
            "1. Grace Hopper",             # numbered, real
            "ada lovelace",                # duplicate, different case
        ]
    )
    assert parse_guest_list(text) == [
        GuestEntry("Ada Lovelace", None),
        GuestEntry("Grace Hopper", None),
    ]


def test_looks_like_guest_list_needs_several_names() -> None:
    assert not looks_like_guest_list("Ada Lovelace")
    assert looks_like_guest_list("Ada Lovelace\nGrace Hopper\nKatherine Johnson")
