from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from invite_finder import db
from invite_finder.api import deps
from invite_finder.api.app import app
from invite_finder.config import Settings
from invite_finder.conversation import ConversationDeps
from invite_finder.runner import RunManager
from invite_finder.store import commerce_store, event_store, people_store


class FakeLinq:
    """Records outbound messages instead of sending them. No test may reach
    the real Linq API."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_to_chat(self, chat_id: str, text: str) -> dict:
        self.sent.append((chat_id, text))
        return {}

    def send_message(self, to, text: str) -> dict:
        self.sent.append((",".join(to), text))
        return {}

    @property
    def last(self) -> str:
        return self.sent[-1][1] if self.sent else ""


@pytest.fixture()
def ctx(tmp_path):
    """A client wired to an isolated DB, a stub Linq, and known secrets."""
    db_path = str(tmp_path / "webhooks.db")
    settings = Settings(
        invite_db_path=db_path,
        invite_offline=True,
        linq_webhook_secret="",
        stripe_webhook_secret="",
        stripe_payment_link="https://buy.stripe.com/test",
    )
    conn = db.connect(db_path)
    linq = FakeLinq()
    conversation_deps = ConversationDeps(
        settings=settings,
        conn_factory=lambda: db.connect(db_path),
        run_manager=RunManager(lambda: db.connect(db_path)),
        build_client=lambda *a, **k: None,
        linq=linq,
    )

    app.dependency_overrides[deps.get_settings] = lambda: settings
    app.dependency_overrides[deps.get_conn] = lambda: conn
    app.dependency_overrides[deps.get_conversation_deps] = lambda: conversation_deps

    yield {
        "client": TestClient(app),
        "conn": conn,
        "linq": linq,
        "settings": settings,
        "db_path": db_path,
    }

    app.dependency_overrides.clear()
    conn.close()


def linq_event(text: str, *, handle="+14155551234", chat_id="chat-1", event_type="message.received"):
    return {
        "api_version": "v3",
        "event_type": event_type,
        "event_id": "evt-1",
        "data": {
            "chat": {"id": chat_id},
            "sender_handle": {"handle": handle},
            "parts": [{"type": "text", "value": text}],
        },
    }


def seed_event(conn, slug="ai-night", name="AI Night") -> int:
    event_id = event_store.upsert_event(
        conn,
        luma_slug=slug,
        source_url=f"https://lu.ma/{slug}",
        name=name,
        guest_count=300,
        show_guest_list=0,
        categories_json="[]",
        ingest_source="luma_api",
        ingest_warnings_json="[]",
    )
    for n in ("Ada Lovelace", "Grace Hopper"):
        person_id = people_store.upsert_person(conn, name=n)
        people_store.link_person_to_event(
            conn, event_id=event_id, person_id=person_id,
            relation="attendee", is_confirmed=True,
        )
    return event_id


# --- Linq inbound ------------------------------------------------------------


def test_unknown_sender_gets_a_welcome(ctx) -> None:
    response = ctx["client"].post("/api/webhooks/linq", json=linq_event("hi"))
    assert response.status_code == 200
    assert "Luma event link" in ctx["linq"].last


def test_known_event_link_returns_the_free_snapshot_and_stages_an_order(ctx) -> None:
    seed_event(ctx["conn"])
    response = ctx["client"].post(
        "/api/webhooks/linq", json=linq_event("check out https://lu.ma/ai-night")
    )
    assert response.status_code == 200
    reply = ctx["linq"].last
    assert "AI Night" in reply
    assert "UNLOCK" in reply

    # The snapshot is free, but it stages a pending order so UNLOCK knows the
    # size of the room it is pricing.
    customer_id = commerce_store.upsert_customer(ctx["conn"], handle="+14155551234")
    order = commerce_store.latest_pending_order(ctx["conn"], customer_id)
    assert order is not None
    assert order["person_count"] == 2


def test_pasted_guest_list_is_priced_and_persisted_before_payment(ctx) -> None:
    text = "Ada Lovelace — Analytical Engines\nGrace Hopper — UNIVAC\nKatherine Johnson — NASA"
    response = ctx["client"].post("/api/webhooks/linq", json=linq_event(text))
    assert response.status_code == 200

    reply = ctx["linq"].last
    assert "3 names" in reply
    assert "$0.30" in reply  # basic: 3 x $0.10
    assert "$3.00" in reply  # full:  3 x $1.00

    # Names are stored immediately. Resolution costs money and waits for
    # payment, but losing the customer's paste would be worse.
    names = {
        r["name"]
        for r in ctx["conn"].execute("SELECT name FROM people").fetchall()
    }
    assert names == {"Ada Lovelace", "Grace Hopper", "Katherine Johnson"}
    assert ctx["conn"].execute(
        "SELECT COUNT(*) n FROM people WHERE linkedin_url IS NOT NULL"
    ).fetchone()["n"] == 0


def test_unlock_prices_the_chosen_tier_and_returns_a_payment_link(ctx) -> None:
    client = ctx["client"]
    client.post(
        "/api/webhooks/linq",
        json=linq_event("Ada Lovelace\nGrace Hopper\nKatherine Johnson"),
    )
    client.post("/api/webhooks/linq", json=linq_event("FULL"))

    reply = ctx["linq"].last
    assert "$3.00" in reply
    assert "buy.stripe.com/test" in reply

    customer_id = commerce_store.upsert_customer(ctx["conn"], handle="+14155551234")
    order = commerce_store.latest_pending_order(ctx["conn"], customer_id)
    assert order["tier"] == "full"
    assert order["amount_cents"] == 300
    # The order id must ride on the link, or the webhook cannot attribute payment.
    assert f"client_reference_id={order['id']}" in reply


def test_stop_suppresses_the_handle_and_later_messages_are_dropped(ctx) -> None:
    client = ctx["client"]
    client.post("/api/webhooks/linq", json=linq_event("STOP"))
    assert "won't hear from me again" in ctx["linq"].last
    assert commerce_store.is_suppressed(ctx["conn"], channel="sms", address="+14155551234")

    sent_before = len(ctx["linq"].sent)
    response = ctx["client"].post("/api/webhooks/linq", json=linq_event("hello?"))
    assert response.json()["ignored"] == "suppressed"
    assert len(ctx["linq"].sent) == sent_before, "a suppressed handle must get no reply"


def test_reaction_is_recorded_without_replying(ctx) -> None:
    response = ctx["client"].post(
        "/api/webhooks/linq", json=linq_event("👍", event_type="reaction.added")
    )
    assert response.json()["recorded"] == "reaction"
    assert ctx["linq"].sent == [], "a tapback must not start a conversation"


def test_uninteresting_events_are_accepted_not_retried(ctx) -> None:
    for payload in (
        {"event_type": "message.delivered", "data": {}},
        {"event_type": "message.received", "data": {"parts": []}},
        {"not": "a webhook"},
    ):
        response = ctx["client"].post("/api/webhooks/linq", json=payload)
        # 200, so Linq stops retrying something that can only fail again.
        assert response.status_code == 200
        assert response.json()["ok"] is True


def test_linq_signature_is_enforced_when_a_secret_is_set(ctx) -> None:
    secret_raw = b"super-secret-key"
    ctx["settings"] = Settings(
        invite_db_path=ctx["db_path"],
        invite_offline=True,
        linq_webhook_secret="whsec_" + base64.b64encode(secret_raw).decode(),
    )
    app.dependency_overrides[deps.get_settings] = lambda: ctx["settings"]

    body = json.dumps(linq_event("hi")).encode()
    headers = {"webhook-id": "msg_1", "webhook-timestamp": "1700000000"}

    bad = ctx["client"].post(
        "/api/webhooks/linq",
        content=body,
        headers={**headers, "webhook-signature": "v1,not-the-signature",
                 "content-type": "application/json"},
    )
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "bad_signature"

    signed = f"{headers['webhook-id']}.{headers['webhook-timestamp']}.".encode() + body
    signature = base64.b64encode(
        hmac.new(secret_raw, signed, hashlib.sha256).digest()
    ).decode()
    good = ctx["client"].post(
        "/api/webhooks/linq",
        content=body,
        headers={**headers, "webhook-signature": f"v1,{signature}",
                 "content-type": "application/json"},
    )
    assert good.status_code == 200


# --- Stripe ------------------------------------------------------------------


def checkout_event(order_id: int, session_id: str = "cs_test_1") -> dict:
    return {
        "type": "checkout.session.completed",
        "data": {"object": {"id": session_id, "client_reference_id": str(order_id)}},
    }


def make_paid_order(conn) -> tuple[int, int, int]:
    event_id = seed_event(conn)
    customer_id = commerce_store.upsert_customer(conn, handle="+14155551234", chat_id="chat-1")
    order_id = commerce_store.create_order(
        conn, customer_id=customer_id, event_id=event_id, tier="full", person_count=2
    )
    return event_id, customer_id, order_id


def test_checkout_completed_settles_the_order_and_grants_access(ctx) -> None:
    event_id, customer_id, order_id = make_paid_order(ctx["conn"])

    response = ctx["client"].post("/api/webhooks/stripe", json=checkout_event(order_id))
    assert response.status_code == 200
    assert response.json()["fulfilling"] is True

    order = commerce_store.get_order(ctx["conn"], order_id)
    assert order["status"] in {"paid", "delivered"}
    assert order["stripe_session_id"] == "cs_test_1"
    assert commerce_store.has_entitlement(
        ctx["conn"], customer_id=customer_id, event_id=event_id, tier="full"
    )
    assert commerce_store.revenue_cents(ctx["conn"]) == 200


def test_replayed_stripe_webhook_does_not_double_count_or_refulfil(ctx) -> None:
    _, _, order_id = make_paid_order(ctx["conn"])

    first = ctx["client"].post("/api/webhooks/stripe", json=checkout_event(order_id))
    second = ctx["client"].post("/api/webhooks/stripe", json=checkout_event(order_id))

    assert first.json().get("fulfilling") is True
    assert second.json()["ignored"] == "already_settled"
    assert commerce_store.revenue_cents(ctx["conn"]) == 200


def test_non_checkout_stripe_events_are_ignored(ctx) -> None:
    response = ctx["client"].post(
        "/api/webhooks/stripe",
        json={"type": "payment_intent.created", "data": {"object": {}}},
    )
    assert response.status_code == 200
    assert response.json()["ignored"] == "uninteresting_event"


def test_payment_for_a_missing_order_is_recorded_not_swallowed(ctx) -> None:
    """The failure this guards: the orders row is gone (ephemeral disk, stale
    link), so there is nothing to fulfil — but the customer has still paid."""
    response = ctx["client"].post(
        "/api/webhooks/stripe",
        json={
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": "cs_missing", "client_reference_id": "4242",
                "amount_total": 400, "customer_details": {"email": "a@b.com"},
            }},
        },
    )
    body = response.json()
    assert response.status_code == 200          # 200, or Stripe retries forever
    assert body["needs_manual_fulfilment"] is True
    assert body["reason"] == "order_missing"

    orphans = commerce_store.list_unresolved_orphans(ctx["conn"])
    assert len(orphans) == 1
    assert orphans[0]["amount_cents"] == 400
    assert orphans[0]["claimed_order_id"] == "4242"


def test_payment_with_no_reference_is_also_captured(ctx) -> None:
    """Someone paid the raw Payment Link without going through the bot. Still
    money; previously this returned a bare 200 and vanished."""
    response = ctx["client"].post(
        "/api/webhooks/stripe",
        json={"type": "checkout.session.completed",
              "data": {"object": {"id": "cs_raw", "amount_total": 1000}}},
    )
    assert response.json()["reason"] == "no_reference"
    assert commerce_store.unresolved_orphan_cents(ctx["conn"]) == 1000


def test_orphan_payer_is_texted_when_stripe_gives_a_known_phone(ctx) -> None:
    commerce_store.upsert_customer(ctx["conn"], handle="+14155551234", chat_id="chat-1")
    response = ctx["client"].post(
        "/api/webhooks/stripe",
        json={
            "type": "checkout.session.completed",
            "data": {"object": {
                "id": "cs_known", "client_reference_id": "777", "amount_total": 300,
                "customer_details": {"phone": "+14155551234"},
            }},
        },
    )
    assert response.json()["notified"] is True
    assert "$3.00" in ctx["linq"].last
    assert "no extra charge" in ctx["linq"].last


def test_retried_orphan_does_not_apologise_twice(ctx) -> None:
    commerce_store.upsert_customer(ctx["conn"], handle="+14155551234", chat_id="chat-1")
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_retry", "client_reference_id": "888", "amount_total": 300,
            "customer_details": {"phone": "+14155551234"},
        }},
    }
    ctx["client"].post("/api/webhooks/stripe", json=event)
    sent_after_first = len(ctx["linq"].sent)
    ctx["client"].post("/api/webhooks/stripe", json=event)

    assert len(ctx["linq"].sent) == sent_after_first, "one payment, one apology"
    assert len(commerce_store.list_unresolved_orphans(ctx["conn"])) == 1


def test_health_reports_unfulfilled_money(ctx) -> None:
    """Money owed has to be visible without opening the database."""
    before = ctx["client"].get("/api/health").json()
    assert before["status"] == "ok"
    assert before["unfulfilled_payments_cents"] == 0

    ctx["client"].post(
        "/api/webhooks/stripe",
        json={"type": "checkout.session.completed",
              "data": {"object": {"id": "cs_h", "amount_total": 250}}},
    )
    after = ctx["client"].get("/api/health").json()
    assert after["status"] == "degraded"
    assert after["unfulfilled_payments_cents"] == 250
    assert after["unfulfilled_payments_count"] == 1


def test_stripe_signature_is_enforced_when_a_secret_is_set(ctx) -> None:
    ctx["settings"] = Settings(
        invite_db_path=ctx["db_path"], invite_offline=True,
        stripe_webhook_secret="whsec_test",
    )
    app.dependency_overrides[deps.get_settings] = lambda: ctx["settings"]

    body = json.dumps(checkout_event(1)).encode()
    bad = ctx["client"].post(
        "/api/webhooks/stripe",
        content=body,
        headers={"stripe-signature": "t=123,v1=wrong", "content-type": "application/json"},
    )
    assert bad.status_code == 401

    expected = hmac.new(b"whsec_test", b"123." + body, hashlib.sha256).hexdigest()
    good = ctx["client"].post(
        "/api/webhooks/stripe",
        content=body,
        headers={"stripe-signature": f"t=123,v1={expected}",
                 "content-type": "application/json"},
    )
    # Signature verifies; order 1 doesn't exist, so it settles nothing.
    assert good.status_code == 200
