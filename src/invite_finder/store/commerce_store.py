"""Customers, orders, entitlements, suppressions and the paid-call cost ledger.

One file per table group, every function taking `conn` first, matching the rest
of `store/`. Money is always integer cents — never floats.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

# Per-person price of each paid tier, in cents.
TIER_RATES_CENTS: dict[str, int] = {
    "basic": 10,
    "full": 100,
}
TIER_ORDER = ["snapshot", "basic", "full"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def quote_cents(tier: str, person_count: int) -> int:
    """Price a room. Unknown tiers raise rather than silently costing nothing."""
    if tier not in TIER_RATES_CENTS:
        raise ValueError(f"Unknown paid tier: {tier}")
    return TIER_RATES_CENTS[tier] * max(0, person_count)


# --- customers ---------------------------------------------------------------


def upsert_customer(
    conn: sqlite3.Connection, *, handle: str, chat_id: str | None = None
) -> int:
    """Identify a customer by the handle they message from, refreshing the chat
    thread we should reply into."""
    now = now_iso()
    existing = conn.execute(
        "SELECT * FROM customers WHERE handle = ?", (handle,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE customers SET chat_id=?, last_seen_at=? WHERE id=?",
            (chat_id or existing["chat_id"], now, existing["id"]),
        )
        conn.commit()
        return int(existing["id"])

    cursor = conn.execute(
        """
        INSERT INTO customers (handle, chat_id, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?)
        """,
        (handle, chat_id, now, now),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_customer(conn: sqlite3.Connection, customer_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()


# --- orders ------------------------------------------------------------------


def create_order(
    conn: sqlite3.Connection,
    *,
    customer_id: int,
    event_id: int | None,
    tier: str,
    person_count: int,
) -> int:
    amount = quote_cents(tier, person_count)
    cursor = conn.execute(
        """
        INSERT INTO orders
          (customer_id, event_id, tier, person_count, amount_cents, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (customer_id, event_id, tier, person_count, amount, now_iso()),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_order(conn: sqlite3.Connection, order_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def settle_order(
    conn: sqlite3.Connection, order_id: int | None, *, stripe_session_id: str | None = None
) -> str:
    """Settle an order and grant the matching entitlement.

    Returns one of:
      "paid"    — settled now; fulfil it
      "replay"  — already settled; Stripe is retrying, ignore safely
      "unknown" — no such order, but money still moved

    The distinction between "replay" and "unknown" is the whole point. Both
    mean "do not fulfil from this callback", but only one of them is benign:
    "unknown" is a customer who has paid and has nothing to show for it, and
    it must never be silently swallowed.
    """
    if order_id is None:
        return "unknown"
    order = get_order(conn, order_id)
    if order is None:
        return "unknown"
    if order["status"] != "pending":
        return "replay"

    conn.execute(
        "UPDATE orders SET status='paid', paid_at=?, stripe_session_id=? WHERE id=?",
        (now_iso(), stripe_session_id, order_id),
    )
    if order["event_id"] is not None:
        grant_entitlement(
            conn,
            customer_id=int(order["customer_id"]),
            event_id=int(order["event_id"]),
            tier=str(order["tier"]),
            order_id=order_id,
        )
    conn.commit()
    return "paid"


def mark_order_delivered(conn: sqlite3.Connection, order_id: int) -> None:
    conn.execute(
        "UPDATE orders SET status='delivered', delivered_at=? WHERE id=?",
        (now_iso(), order_id),
    )
    conn.commit()


def latest_pending_order(
    conn: sqlite3.Connection, customer_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM orders WHERE customer_id=? AND status='pending'
        ORDER BY id DESC LIMIT 1
        """,
        (customer_id,),
    ).fetchone()


def revenue_cents(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM orders "
        "WHERE status IN ('paid', 'delivered')"
    ).fetchone()
    return int(row["total"])


# --- orphan payments ---------------------------------------------------------


def record_orphan_payment(
    conn: sqlite3.Connection,
    *,
    stripe_session_id: str | None,
    claimed_order_id: str | None,
    amount_cents: int | None,
    email: str | None,
    phone: str | None,
    reason: str,
    raw_json: str | None = None,
) -> int:
    """Persist money we received but cannot attribute to an order.

    Idempotent on the Stripe session id, because Stripe retries: a retried
    orphan must not multiply into several rows and several apology texts.
    """
    existing = None
    if stripe_session_id:
        existing = conn.execute(
            "SELECT id FROM orphan_payments WHERE stripe_session_id = ?",
            (stripe_session_id,),
        ).fetchone()
    if existing is not None:
        return int(existing["id"])

    cursor = conn.execute(
        """
        INSERT INTO orphan_payments
          (stripe_session_id, claimed_order_id, amount_cents, email, phone,
           reason, raw_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stripe_session_id, claimed_order_id, amount_cents, email, phone,
            reason, raw_json, now_iso(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def get_orphan_payment(
    conn: sqlite3.Connection, orphan_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM orphan_payments WHERE id = ?", (orphan_id,)
    ).fetchone()


def mark_orphan_notified(conn: sqlite3.Connection, orphan_id: int) -> None:
    conn.execute(
        "UPDATE orphan_payments SET notified=1 WHERE id=?", (orphan_id,)
    )
    conn.commit()


def list_unresolved_orphans(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM orphan_payments WHERE resolved_at IS NULL "
            "ORDER BY created_at DESC"
        ).fetchall()
    )


def unresolved_orphan_cents(conn: sqlite3.Connection) -> int:
    """Money taken that nobody has been given anything for. Should be 0."""
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total FROM orphan_payments "
        "WHERE resolved_at IS NULL"
    ).fetchone()
    return int(row["total"])


def find_customer_by_handle(
    conn: sqlite3.Connection, handle: str
) -> sqlite3.Row | None:
    """Look up a customer by messaging handle, so a payment carrying a phone
    number can still be routed back to the right thread."""
    if not handle:
        return None
    normalized = handle.strip()
    return conn.execute(
        "SELECT * FROM customers WHERE handle = ? ORDER BY id DESC LIMIT 1",
        (normalized,),
    ).fetchone()


# --- entitlements ------------------------------------------------------------


def grant_entitlement(
    conn: sqlite3.Connection,
    *,
    customer_id: int,
    event_id: int,
    tier: str,
    order_id: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO entitlements (customer_id, event_id, tier, order_id, granted_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(customer_id, event_id, tier) DO NOTHING
        """,
        (customer_id, event_id, tier, order_id, now_iso()),
    )
    conn.commit()


def best_tier(conn: sqlite3.Connection, *, customer_id: int, event_id: int) -> str:
    """The highest tier this customer has paid for on this event.

    Everyone has 'snapshot' — the free tier is what makes the paid one worth
    buying, so it is never gated.
    """
    rows = conn.execute(
        "SELECT tier FROM entitlements WHERE customer_id=? AND event_id=?",
        (customer_id, event_id),
    ).fetchall()
    tiers = {str(r["tier"]) for r in rows}
    for tier in reversed(TIER_ORDER):
        if tier in tiers:
            return tier
    return "snapshot"


def has_entitlement(
    conn: sqlite3.Connection, *, customer_id: int, event_id: int, tier: str
) -> bool:
    held = best_tier(conn, customer_id=customer_id, event_id=event_id)
    return TIER_ORDER.index(held) >= TIER_ORDER.index(tier)


# --- suppressions ------------------------------------------------------------


def suppress(
    conn: sqlite3.Connection, *, channel: str, address: str, reason: str | None = None
) -> None:
    conn.execute(
        """
        INSERT INTO suppressions (channel, address, reason, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(channel, address) DO NOTHING
        """,
        (channel, address.strip().lower(), reason, now_iso()),
    )
    conn.commit()


def is_suppressed(conn: sqlite3.Connection, *, channel: str, address: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM suppressions WHERE channel=? AND address=?",
        (channel, address.strip().lower()),
    ).fetchone()
    return row is not None


# --- cost ledger -------------------------------------------------------------


def record_service_purchase(
    conn: sqlite3.Connection,
    *,
    capability: str,
    provider: str,
    channel: str,
    status: str,
    quote_cents_: int | None = None,
    charged_cents: int | None = None,
    purchase_id: str | None = None,
    cache_hit: bool = False,
    person_id: int | None = None,
    order_id: int | None = None,
    error: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO service_purchases
          (capability, provider, channel, quote_cents, charged_cents, purchase_id,
           status, cache_hit, person_id, order_id, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            capability, provider, channel, quote_cents_, charged_cents, purchase_id,
            status, int(cache_hit), person_id, order_id, error, now_iso(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def spend_cents(conn: sqlite3.Connection, *, order_id: int | None = None) -> int:
    """Total actually charged by providers. Cache hits cost nothing and are
    recorded with charged_cents = 0, so this is real money only."""
    if order_id is None:
        row = conn.execute(
            "SELECT COALESCE(SUM(charged_cents), 0) AS total FROM service_purchases"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(charged_cents), 0) AS total FROM service_purchases "
            "WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    return int(row["total"])
