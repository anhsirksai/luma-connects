from __future__ import annotations

import pytest

from invite_finder import db, stripe_links
from invite_finder.store import commerce_store, event_store


def make_conn():
    return db.connect(":memory:")


def make_event(conn, slug: str = "test-event") -> int:
    return event_store.upsert_event(
        conn,
        luma_slug=slug,
        source_url=f"https://luma.com/{slug}",
        name="Test Event",
        guest_count=10,
        show_guest_list=0,
        categories_json="[]",
        ingest_source="manual",
        ingest_warnings_json="[]",
    )


def test_quote_is_per_person_and_rejects_unknown_tiers() -> None:
    assert commerce_store.quote_cents("basic", 300) == 3000
    assert commerce_store.quote_cents("full", 300) == 30000
    assert commerce_store.quote_cents("basic", 0) == 0
    with pytest.raises(ValueError):
        commerce_store.quote_cents("platinum", 10)


def test_customer_upsert_is_idempotent_and_refreshes_chat() -> None:
    conn = make_conn()
    first = commerce_store.upsert_customer(conn, handle="+14155551234", chat_id="c1")
    second = commerce_store.upsert_customer(conn, handle="+14155551234", chat_id="c2")
    assert first == second
    assert commerce_store.get_customer(conn, first)["chat_id"] == "c2"

    # A later message without a chat id must not wipe the one we have.
    commerce_store.upsert_customer(conn, handle="+14155551234")
    assert commerce_store.get_customer(conn, first)["chat_id"] == "c2"


def test_paying_an_order_grants_entitlement_exactly_once() -> None:
    conn = make_conn()
    event_id = make_event(conn)
    customer_id = commerce_store.upsert_customer(conn, handle="+1", chat_id="c1")
    order_id = commerce_store.create_order(
        conn, customer_id=customer_id, event_id=event_id, tier="full", person_count=3
    )

    assert commerce_store.get_order(conn, order_id)["amount_cents"] == 300
    assert not commerce_store.has_entitlement(
        conn, customer_id=customer_id, event_id=event_id, tier="full"
    )

    assert commerce_store.settle_order(conn, order_id, stripe_session_id="cs_1") == "paid"
    assert commerce_store.has_entitlement(
        conn, customer_id=customer_id, event_id=event_id, tier="full"
    )
    assert commerce_store.revenue_cents(conn) == 300

    # Stripe retries webhooks. A replay must not double-count revenue.
    assert commerce_store.settle_order(conn, order_id, stripe_session_id="cs_1") == "replay"
    assert commerce_store.revenue_cents(conn) == 300


def test_settle_distinguishes_a_replay_from_a_missing_order() -> None:
    """The distinction that stops us swallowing a real payment: a replay is
    benign, a missing order means someone paid and got nothing."""
    conn = make_conn()
    assert commerce_store.settle_order(conn, 9999) == "unknown"
    assert commerce_store.settle_order(conn, None) == "unknown"


def test_orphan_payments_are_recorded_once_per_session() -> None:
    conn = make_conn()
    first = commerce_store.record_orphan_payment(
        conn, stripe_session_id="cs_9", claimed_order_id="41", amount_cents=400,
        email="a@b.com", phone="+14155551234", reason="order_missing",
    )
    # Stripe retries: the same session must not multiply into several rows,
    # or the operator sees phantom money and the payer gets several apologies.
    second = commerce_store.record_orphan_payment(
        conn, stripe_session_id="cs_9", claimed_order_id="41", amount_cents=400,
        email="a@b.com", phone="+14155551234", reason="order_missing",
    )
    assert first == second
    assert len(commerce_store.list_unresolved_orphans(conn)) == 1
    assert commerce_store.unresolved_orphan_cents(conn) == 400


def test_find_customer_by_handle_routes_an_orphan_back_to_its_thread() -> None:
    conn = make_conn()
    commerce_store.upsert_customer(conn, handle="+14155551234", chat_id="chat-7")
    found = commerce_store.find_customer_by_handle(conn, "+14155551234")
    assert found is not None and found["chat_id"] == "chat-7"
    assert commerce_store.find_customer_by_handle(conn, "+19999999999") is None
    assert commerce_store.find_customer_by_handle(conn, "") is None


def test_entitlement_tiers_are_ordered_not_just_matched() -> None:
    conn = make_conn()
    event_id = make_event(conn)
    customer_id = commerce_store.upsert_customer(conn, handle="+1")

    # Nobody pays for the free tier, so everybody has it.
    assert commerce_store.has_entitlement(
        conn, customer_id=customer_id, event_id=event_id, tier="snapshot"
    )
    assert not commerce_store.has_entitlement(
        conn, customer_id=customer_id, event_id=event_id, tier="basic"
    )

    commerce_store.grant_entitlement(
        conn, customer_id=customer_id, event_id=event_id, tier="full"
    )
    # Buying FULL includes everything BASIC covers.
    assert commerce_store.has_entitlement(
        conn, customer_id=customer_id, event_id=event_id, tier="basic"
    )
    assert commerce_store.best_tier(conn, customer_id=customer_id, event_id=event_id) == "full"


def test_suppression_is_case_insensitive_and_per_channel() -> None:
    conn = make_conn()
    commerce_store.suppress(conn, channel="email", address="  Person@Example.COM ")
    assert commerce_store.is_suppressed(
        conn, channel="email", address="person@example.com"
    )
    assert not commerce_store.is_suppressed(
        conn, channel="sms", address="person@example.com"
    )
    # Re-suppressing must not raise on the unique index.
    commerce_store.suppress(conn, channel="email", address="person@example.com")


def test_cost_ledger_separates_spend_from_cache_hits() -> None:
    conn = make_conn()
    commerce_store.record_service_purchase(
        conn, capability="contact_enrich", provider="pdl", channel="perflo",
        status="ok", charged_cents=12,
    )
    commerce_store.record_service_purchase(
        conn, capability="contact_enrich", provider="cache", channel="cache",
        status="hit", charged_cents=0, cache_hit=True,
    )
    # The cache hit served the same data for nothing; only real money counts.
    assert commerce_store.spend_cents(conn) == 12


def test_payment_link_carries_the_order_and_survives_existing_params() -> None:
    link = stripe_links.payment_link_for_order("https://buy.stripe.com/abc", 42)
    assert link == "https://buy.stripe.com/abc?client_reference_id=42"

    with_params = stripe_links.payment_link_for_order(
        "https://buy.stripe.com/abc?locale=en", 7
    )
    assert "locale=en" in with_params
    assert "client_reference_id=7" in with_params

    # Re-linking an order must not stack duplicate references.
    relinked = stripe_links.payment_link_for_order(with_params, 9)
    assert relinked.count("client_reference_id") == 1
    assert "client_reference_id=9" in relinked


def test_payment_link_requires_configuration() -> None:
    with pytest.raises(stripe_links.StripeError):
        stripe_links.payment_link_for_order("", 1)
