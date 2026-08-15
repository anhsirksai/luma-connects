"""The agent's conversational surface.

One entry point, `handle_inbound`, decides what an incoming text means and what
to do about it. Everything it can do is one of four things:

  a Luma URL        -> run the free pipeline, reply with the Room Snapshot
  a pasted list     -> price it, reply with a payment link
  "unlock" / "yes"  -> create an order, reply with a payment link
  "stop"            -> suppress the handle and confirm

Long work never blocks the reply: the webhook answers immediately and the
result is delivered into the thread when it lands.
"""

from __future__ import annotations

import asyncio
import functools
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from invite_finder.config import Settings
from invite_finder.linq import LinqClient
from invite_finder.luma import parse_luma_slug
from invite_finder.pipeline import compute_snapshot_for_event
from invite_finder.providers.base import BudgetExceeded
from invite_finder.providers.router import ProviderRouter
from invite_finder.resolve import (
    GuestEntry,
    looks_like_guest_list,
    parse_guest_list,
    resolve_person,
)
from invite_finder.runner import RunManager
from invite_finder.snapshot import RoomSnapshot
from invite_finder.store import commerce_store, event_store, people_store, run_store
from invite_finder.stripe_links import payment_link_for_order

LUMA_URL = re.compile(r"https?://(?:www\.)?(?:lu\.ma|luma\.com)/[\w\-/]+", re.IGNORECASE)
STOP_WORDS = {"stop", "unsubscribe", "quit", "cancel", "opt out", "optout"}
UNLOCK_WORDS = {"unlock", "full", "yes", "buy", "upgrade", "pay", "y"}
HELP_WORDS = {"help", "hi", "hello", "start", "?"}

WELCOME = (
    "Hey — send me a Luma event link and I'll tell you who's in the room: "
    "fields, roles and seniority, free.\n\n"
    "Already have the guest list? Paste it and I'll resolve every name to a "
    "LinkedIn and X profile, and rank who's worth your time.\n\n"
    "Reply STOP to opt out."
)


@dataclass
class ConversationDeps:
    """Everything the state machine needs, injected so tests can stub it."""

    settings: Settings
    conn_factory: Callable[[], sqlite3.Connection]
    run_manager: RunManager
    build_client: Callable[..., Any]
    linq: LinqClient


def format_snapshot(snapshot: RoomSnapshot, event_name: str) -> str:
    """Render a Room Snapshot for a text message: two sections, top three bars.
    A phone screen is the constraint, not the data."""
    lines = [f"📍 {event_name}", ""]
    for section in snapshot.sections:
        if section.id not in {"fields", "role_types"}:
            continue
        bars = [b for b in section.bars if b.percentage > 0][:3]
        if not bars:
            continue
        lines.append(f"{section.title}:")
        lines.extend(f"  {b.percentage}% {b.label}" for b in bars)
        lines.append("")

    basis = snapshot.basis
    if basis.registered_count:
        lines.append(f"{basis.registered_count} registered.")
    lines.append(
        f"Based on {basis.classified_people} profiles "
        f"({basis.confirmed_people} confirmed, {basis.inferred_people} inferred)."
    )
    return "\n".join(lines).strip()


def _tier_pitch(person_count: int) -> str:
    basic = commerce_store.quote_cents("basic", person_count) / 100
    full = commerce_store.quote_cents("full", person_count) / 100
    return (
        f"I can work {person_count} names.\n\n"
        f"BASIC — ${basic:.2f}: LinkedIn + X profile, headline and company for each.\n"
        f"FULL — ${full:.2f}: adds email, phone, bio, what they post about, and a "
        f"ranked list of who to meet.\n\n"
        f"Reply BASIC or FULL."
    )


async def handle_inbound(
    deps: ConversationDeps,
    conn: sqlite3.Connection,
    *,
    handle: str,
    chat_id: str,
    text: str,
) -> str:
    """Interpret one inbound message and return the immediate reply.

    Anything slow is scheduled as a background task that texts the customer
    when it finishes — the caller is a webhook and must return fast.
    """
    customer_id = commerce_store.upsert_customer(conn, handle=handle, chat_id=chat_id)
    cleaned = (text or "").strip()
    lowered = cleaned.lower()

    if not cleaned:
        return WELCOME

    if lowered in STOP_WORDS:
        commerce_store.suppress(
            conn, channel="sms", address=handle, reason="customer replied STOP"
        )
        return "Done — you won't hear from me again. Reply START to resume."

    if lowered in HELP_WORDS:
        return WELCOME

    url_match = LUMA_URL.search(cleaned)
    if url_match:
        return _handle_luma_url(deps, conn, url_match.group(0), chat_id)

    if lowered in {"basic", "full"} or lowered in UNLOCK_WORDS:
        return _handle_unlock(deps, conn, customer_id, lowered)

    if looks_like_guest_list(cleaned):
        return _handle_guest_list(conn, customer_id, cleaned)

    return (
        "I didn't catch a Luma link or a guest list in that. Send me a "
        "lu.ma URL to see the room, or paste a list of names."
    )


def _handle_luma_url(
    deps: ConversationDeps, conn: sqlite3.Connection, url: str, chat_id: str
) -> str:
    slug = parse_luma_slug(url)
    existing = event_store.get_by_slug(conn, slug)
    if existing is not None:
        event_id = int(existing["id"])
        snapshot = compute_snapshot_for_event(conn, event_id)
        counts = people_store.count_event_people(conn, event_id)
        _open_pending_order(conn, chat_id, event_id, counts["total"])
        return (
            f"{format_snapshot(snapshot, existing['name'])}\n\n"
            f"Want the {counts['total']} people themselves — profiles, contacts, "
            f"who to meet? Reply UNLOCK."
        )

    active = run_store.latest_active_run_for_slug(conn, slug)
    if active is not None:
        return "Already working on that one — I'll text you the moment it's ready."

    run_id = run_store.create_run(conn, event_id=None, input_url=url)
    task = deps.run_manager.start(
        run_id,
        luma_url=url,
        build_client=functools.partial(deps.build_client, force_refresh=False),
        agent_model=deps.settings.openai_agent_model,
    )
    # Deliver the snapshot when the run lands, without holding the webhook.
    asyncio.create_task(_deliver_snapshot_when_ready(deps, task, run_id, chat_id))
    return "On it — reading the event and working out who's in the room. ~60s."


async def _deliver_snapshot_when_ready(
    deps: ConversationDeps, task: asyncio.Task, run_id: int, chat_id: str
) -> None:
    try:
        await task
    except Exception:  # noqa: BLE001 - the run records its own failure
        pass

    conn = deps.conn_factory()
    try:
        run = run_store.get_run(conn, run_id)
        if run is None or run["event_id"] is None:
            deps.linq.send_to_chat(
                chat_id,
                "I couldn't read that event — Luma may have it private. "
                "Try another link, or paste the guest list directly.",
            )
            return

        event_id = int(run["event_id"])
        event_row = event_store.get_by_id(conn, event_id)
        snapshot = compute_snapshot_for_event(conn, event_id)
        counts = people_store.count_event_people(conn, event_id)
        _open_pending_order(conn, chat_id, event_id, counts["total"])
        deps.linq.send_to_chat(
            chat_id,
            f"{format_snapshot(snapshot, event_row['name'])}\n\n"
            f"Want the {counts['total']} people themselves? Reply UNLOCK.",
        )
    finally:
        conn.close()


def _open_pending_order(
    conn: sqlite3.Connection, chat_id: str, event_id: int, person_count: int
) -> None:
    """Stage an unpriced order against this event so a later UNLOCK knows what
    it is buying. Nothing is charged until the customer picks a tier."""
    if person_count <= 0:
        return
    row = conn.execute(
        "SELECT id FROM customers WHERE chat_id = ? ORDER BY id DESC LIMIT 1",
        (chat_id,),
    ).fetchone()
    if row is None:
        return
    customer_id = int(row["id"])
    existing = commerce_store.latest_pending_order(conn, customer_id)
    if existing is not None and existing["event_id"] == event_id:
        return
    commerce_store.create_order(
        conn,
        customer_id=customer_id,
        event_id=event_id,
        tier="basic",
        person_count=person_count,
    )


def _handle_guest_list(
    conn: sqlite3.Connection, customer_id: int, text: str
) -> str:
    entries = parse_guest_list(text)
    if not entries:
        return "I couldn't find any names in that. One per line works best."

    # Persist the list right away, unresolved. Resolution costs money and the
    # customer hasn't paid yet — but losing their paste would be worse, and
    # storing bare names costs nothing.
    event_id = event_store.upsert_event(
        conn,
        luma_slug=f"pasted-{customer_id}-{int(datetime.now(timezone.utc).timestamp())}",
        source_url="customer://pasted-guest-list",
        name="Pasted guest list",
        guest_count=len(entries),
        show_guest_list=0,
        categories_json="[]",
        ingest_source="manual",
        ingest_warnings_json="[]",
    )
    for entry in entries:
        person_id = people_store.upsert_person(
            conn, name=entry.name, company=entry.company
        )
        people_store.link_person_to_event(
            conn,
            event_id=event_id,
            person_id=person_id,
            relation="attendee",
            is_confirmed=True,
            evidence=["Supplied by customer from the event guest list"],
        )

    commerce_store.create_order(
        conn,
        customer_id=customer_id,
        event_id=event_id,
        tier="basic",
        person_count=len(entries),
    )
    return _tier_pitch(len(entries))


def _handle_unlock(
    deps: ConversationDeps, conn: sqlite3.Connection, customer_id: int, word: str
) -> str:
    order = commerce_store.latest_pending_order(conn, customer_id)
    if order is None:
        return (
            "Send me a Luma link or paste a guest list first, then I'll price it up."
        )

    tier = word if word in {"basic", "full"} else "full"
    person_count = int(order["person_count"])
    amount = commerce_store.quote_cents(tier, person_count)
    conn.execute(
        "UPDATE orders SET tier=?, amount_cents=? WHERE id=?",
        (tier, amount, int(order["id"])),
    )
    conn.commit()

    if not deps.settings.stripe_payment_link:
        return "Payments aren't configured yet — tell the operator to set STRIPE_PAYMENT_LINK."

    link = payment_link_for_order(deps.settings.stripe_payment_link, int(order["id"]))
    return (
        f"{tier.upper()} for {person_count} people — ${amount / 100:.2f}.\n\n"
        f"{link}\n\n"
        f"I'll start the moment it clears."
    )


async def deliver_paid_order(deps: ConversationDeps, order_id: int) -> None:
    """Fulfil a paid order: resolve the list, enrich it, text back the results.

    Called from the Stripe webhook, so it must own its own connection and never
    raise into the caller.
    """
    from invite_finder.enrich import enrich_event_people

    conn = deps.conn_factory()
    try:
        order = commerce_store.get_order(conn, order_id)
        if order is None:
            return
        customer = commerce_store.get_customer(conn, int(order["customer_id"]))
        chat_id = customer["chat_id"] if customer else None
        if not chat_id:
            return

        tier = str(order["tier"])
        event_id = order["event_id"]
        router = ProviderRouter(conn, deps.settings, order_id=order_id)

        if event_id is None:
            deps.linq.send_to_chat(
                chat_id,
                "Payment received — but I've lost track of which list this was for. "
                "Paste it again and I'll run it on the house.",
            )
            return

        # Resolve first: a pasted list is stored as bare names, and enrichment
        # keys off the LinkedIn URL that resolution supplies.
        _resolve_pending_people(conn, router, int(event_id))

        outcome = enrich_event_people(
            conn, router, event_id=int(event_id), tier=tier, limit=int(order["person_count"])
        )
        rows, _ = people_store.list_event_people(conn, int(event_id), limit=10)
        deps.linq.send_to_chat(chat_id, _format_dossiers(rows, tier))
        if outcome.budget_exhausted:
            deps.linq.send_to_chat(
                chat_id,
                "Heads up: I hit my spending cap partway through, so the tail of "
                "the list is thinner. Nothing extra was charged.",
            )
        commerce_store.mark_order_delivered(conn, order_id)
    finally:
        conn.close()


def _resolve_pending_people(
    conn: sqlite3.Connection, router: ProviderRouter, event_id: int
) -> int:
    """Resolve every person on this event that has a name but no profile yet.

    Budget exhaustion stops the loop rather than failing it — a partly resolved
    list still delivers.
    """
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.company FROM event_people ep
        JOIN people p ON p.id = ep.person_id
        WHERE ep.event_id = ? AND p.linkedin_url IS NULL AND p.name IS NOT NULL
        """,
        (event_id,),
    ).fetchall()

    resolved = 0
    for row in rows:
        entry = GuestEntry(name=str(row["name"]), company=row["company"])
        try:
            profile = resolve_person(router, entry, person_id=int(row["id"]))
        except BudgetExceeded:
            break
        if profile:
            people_store.upsert_person(conn, **profile)
            resolved += 1
    return resolved


def _format_dossiers(rows: list[sqlite3.Row], tier: str) -> str:
    if not rows:
        return "Payment received, but I couldn't resolve anyone on that list."

    blocks: list[str] = [f"Here's the room ({tier.upper()}):", ""]
    for row in rows:
        parts = [f"• {row['name'] or 'Unknown'}"]
        if row["headline"]:
            parts.append(f"  {row['headline']}")
        if row["linkedin_url"]:
            parts.append(f"  {row['linkedin_url']}")
        if row["x_url"]:
            parts.append(f"  {row['x_url']}")
        if tier == "full":
            if row["email"]:
                parts.append(f"  {row['email']}")
            if row["phone"]:
                parts.append(f"  {row['phone']}")
        blocks.append("\n".join(parts))
    return "\n".join(blocks)
