"""Stripe Payment Link construction and webhook verification.

The hackathon rules allow exactly one Payment Link, priced "customer chooses
price", because the organizers track revenue through that single link. So the
order is carried on the URL as `?client_reference_id=<order_id>` and read back
off `checkout.session.completed` — never mint a link per transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class StripeError(RuntimeError):
    """Raised when a Stripe webhook cannot be trusted."""


def payment_link_for_order(payment_link: str, order_id: int) -> str:
    """Append the order reference to the configured Payment Link, preserving
    any query params it already carries."""
    if not payment_link:
        raise StripeError("STRIPE_PAYMENT_LINK is not configured")
    parts = urlsplit(payment_link)
    params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k != "client_reference_id"
    ]
    params.append(("client_reference_id", str(order_id)))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment)
    )


def verify_signature(*, secret: str, body: bytes, signature_header: str) -> bool:
    """Stripe's scheme: `t=<ts>,v1=<hex>` where the signed payload is
    f"{timestamp}.{body}" HMAC-SHA256'd with the endpoint secret.

    An empty secret disables verification, for local tunnelled development only.
    """
    if not secret:
        return True
    if not signature_header:
        return False

    timestamp = ""
    candidates: list[str] = []
    for entry in signature_header.split(","):
        key, _, value = entry.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            candidates.append(value)
    if not timestamp or not candidates:
        return False

    signed = f"{timestamp}.".encode("utf-8") + body
    expected = hmac.new(
        secret.encode("utf-8"), signed, hashlib.sha256
    ).hexdigest()
    return any(hmac.compare_digest(c, expected) for c in candidates)


@dataclass(frozen=True)
class CheckoutCompleted:
    """A completed checkout, whether or not we can attribute it.

    `order_id` is None when the reference is missing or unparseable — someone
    paid the raw Payment Link, or reused a stale one. That is still money, so
    the caller must handle it rather than treat it as a non-event.
    """

    session_id: str | None
    order_id: int | None
    claimed_reference: str | None
    amount_cents: int | None
    email: str | None
    phone: str | None


def parse_checkout_completed(payload: dict[str, Any]) -> CheckoutCompleted | None:
    """Parse a checkout.session.completed event. Returns None only for other
    event types — never for a completed checkout we cannot attribute."""
    if payload.get("type") != "checkout.session.completed":
        return None

    data = payload.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    if not isinstance(obj, dict):
        return None

    reference = obj.get("client_reference_id")
    order_id: int | None = None
    if reference is not None:
        try:
            order_id = int(str(reference))
        except ValueError:
            order_id = None

    details = obj.get("customer_details")
    details = details if isinstance(details, dict) else {}

    amount = obj.get("amount_total")
    session_id = obj.get("id")

    return CheckoutCompleted(
        session_id=str(session_id) if session_id else None,
        order_id=order_id,
        claimed_reference=str(reference) if reference is not None else None,
        amount_cents=int(amount) if isinstance(amount, int) else None,
        email=(details.get("email") or None),
        phone=(details.get("phone") or None),
    )


def loads(body: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StripeError("Stripe webhook body was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise StripeError("Stripe webhook body was not a JSON object")
    return parsed
