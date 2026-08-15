"""Inbound webhooks: Linq (messages) and Stripe (payments).

Both routes are `async def` on purpose. FastAPI runs sync routes in a worker
thread, where `asyncio.create_task()` raises "no running event loop" — and both
of these schedule background work. See CLAUDE.md and `create_event`, which is
async for exactly the same reason.

Both also answer 200 on input they will never accept. A 4xx makes the sender
retry a message that can only fail again.
"""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from invite_finder import stripe_links
from invite_finder.api.deps import (
    build_client_for_run,
    get_conn,
    get_conversation_deps,
    get_settings,
)
from invite_finder.config import Settings
from invite_finder.conversation import ConversationDeps, deliver_paid_order, handle_inbound
from invite_finder.linq import parse_inbound, verify_signature
from invite_finder.store import commerce_store

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _ok(**extra: object) -> JSONResponse:
    return JSONResponse(status_code=200, content={"ok": True, **extra})


@router.post("/linq")
async def linq_webhook(
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
    deps: ConversationDeps = Depends(get_conversation_deps),
) -> JSONResponse:
    body = await request.body()

    if not verify_signature(
        secret=settings.linq_webhook_secret,
        body=body,
        headers=dict(request.headers),
    ):
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "bad_signature",
                    "message": "Linq webhook signature did not verify.",
                    "detail": None,
                }
            },
        )

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed body is not retryable
        return _ok(ignored="unparseable")

    if not isinstance(payload, dict):
        return _ok(ignored="not_an_object")

    message = parse_inbound(payload)
    if message is None:
        return _ok(ignored="uninteresting_event")

    # A reaction is a signal, not a request: record it as a preference vote and
    # say nothing back, so a tapback doesn't start a conversation.
    if message.event_type == "reaction.added":
        commerce_store.upsert_customer(
            conn, handle=message.handle, chat_id=message.chat_id
        )
        return _ok(recorded="reaction")

    if commerce_store.is_suppressed(conn, channel="sms", address=message.handle):
        return _ok(ignored="suppressed")

    reply = await handle_inbound(
        deps,
        conn,
        handle=message.handle,
        chat_id=message.chat_id,
        text=message.text,
    )
    if reply:
        deps.linq.send_to_chat(message.chat_id, reply)
    return _ok(replied=bool(reply))


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
    deps: ConversationDeps = Depends(get_conversation_deps),
) -> JSONResponse:
    body = await request.body()

    if not stripe_links.verify_signature(
        secret=settings.stripe_webhook_secret,
        body=body,
        signature_header=request.headers.get("stripe-signature", ""),
    ):
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "code": "bad_signature",
                    "message": "Stripe webhook signature did not verify.",
                    "detail": None,
                }
            },
        )

    try:
        payload = stripe_links.loads(body)
    except stripe_links.StripeError:
        return _ok(ignored="unparseable")

    checkout = stripe_links.parse_checkout_completed(payload)
    if checkout is None:
        return _ok(ignored="uninteresting_event")

    # Stripe retries webhooks; settle_order is the idempotency guard, so
    # fulfilment only ever runs once per order.
    outcome = commerce_store.settle_order(
        conn, checkout.order_id, stripe_session_id=checkout.session_id
    )

    if outcome == "replay":
        return _ok(ignored="already_settled")

    if outcome == "unknown":
        # Money moved and we have nothing to fulfil against. Never return a
        # bare 200 here: record it, try to reach the payer, and make it loud.
        return await _handle_orphan_payment(deps, conn, checkout, body)

    assert checkout.order_id is not None  # settle_order only returns "paid" with one
    asyncio.create_task(deliver_paid_order(deps, checkout.order_id))
    return _ok(order_id=checkout.order_id, fulfilling=True)


async def _handle_orphan_payment(
    deps: ConversationDeps,
    conn: sqlite3.Connection,
    checkout: stripe_links.CheckoutCompleted,
    body: bytes,
) -> JSONResponse:
    """A completed checkout with no matching order.

    Three ways to get here: the order row is gone (an ephemeral filesystem
    wiped it between checkout and callback), someone paid the raw Payment Link
    without going through the bot, or a stale link was reused. In all three a
    real person is out of pocket, so the payment is persisted first and the
    customer told second.
    """
    reason = (
        "no_reference" if checkout.order_id is None else "order_missing"
    )
    orphan_id = commerce_store.record_orphan_payment(
        conn,
        stripe_session_id=checkout.session_id,
        claimed_order_id=checkout.claimed_reference,
        amount_cents=checkout.amount_cents,
        email=checkout.email,
        phone=checkout.phone,
        reason=reason,
        raw_json=body.decode("utf-8", errors="replace")[:20000],
    )

    amount = f"${(checkout.amount_cents or 0) / 100:.2f}"
    print(
        f"invite-api: UNFULFILLED PAYMENT {amount} session={checkout.session_id} "
        f"reason={reason} ref={checkout.claimed_reference} "
        f"email={checkout.email} phone={checkout.phone} orphan_id={orphan_id}",
        flush=True,
    )

    # If Stripe collected a phone we already know, we can apologise in-thread.
    # Guard on the stored flag, not just on the row being new: Stripe retries,
    # and one payment should produce exactly one apology.
    existing = commerce_store.get_orphan_payment(conn, orphan_id)
    already_notified = bool(existing["notified"]) if existing is not None else False

    notified = already_notified
    customer = (
        commerce_store.find_customer_by_handle(conn, checkout.phone)
        if checkout.phone and not already_notified
        else None
    )
    if customer is not None and customer["chat_id"]:
        try:
            deps.linq.send_to_chat(
                str(customer["chat_id"]),
                f"I received your {amount} payment but lost the list it was for — "
                f"that's on me. Paste the names again and I'll run it straight "
                f"away at no extra charge.",
            )
            commerce_store.mark_orphan_notified(conn, orphan_id)
            notified = True
        except Exception as exc:  # noqa: BLE001 - never fail the webhook on this
            print(f"invite-api: could not notify orphan payer: {exc}", flush=True)

    # 200 so Stripe stops retrying: the payment is recorded, and retrying
    # cannot make an order that no longer exists reappear.
    return _ok(
        orphan_payment_id=orphan_id,
        reason=reason,
        notified=notified,
        needs_manual_fulfilment=True,
    )
