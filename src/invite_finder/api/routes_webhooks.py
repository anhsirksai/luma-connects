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

    parsed = stripe_links.parse_checkout_completed(payload)
    if parsed is None:
        return _ok(ignored="uninteresting_event")

    order_id, session_id = parsed
    # Stripe retries webhooks; mark_order_paid is the idempotency guard, so
    # fulfilment only ever runs once per order.
    newly_paid = commerce_store.mark_order_paid(
        conn, order_id, stripe_session_id=session_id
    )
    if not newly_paid:
        return _ok(ignored="already_settled")

    asyncio.create_task(deliver_paid_order(deps, order_id))
    return _ok(order_id=order_id, fulfilling=True)
