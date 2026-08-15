"""Linq Partner API client — the messaging front door.

Deliberately uses a plain `requests.Session`, not `CachingSession`: caching is
for *reads* we pay for. Replaying a cached "message sent" would silently drop
replies to customers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

import requests

from invite_finder.config import Settings


class LinqError(RuntimeError):
    """Raised when the Linq API returns an unsuccessful response."""


@dataclass(frozen=True)
class InboundMessage:
    """The parts of a `message.received` webhook we act on."""

    chat_id: str
    handle: str
    text: str
    event_type: str = "message.received"


def parse_inbound(payload: dict[str, Any]) -> InboundMessage | None:
    """Pull the fields we need out of a Linq webhook body.

    Shape (webhook version 2026-02-03):
        data.chat.id, data.sender_handle.handle, data.parts[].value

    Returns None for events we don't act on, or a malformed body — the route
    turns that into a 200 so Linq doesn't retry something we will never accept.
    """
    event_type = str(payload.get("event_type") or "")
    if event_type not in {"message.received", "reaction.added"}:
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    chat = data.get("chat") if isinstance(data.get("chat"), dict) else {}
    sender = (
        data.get("sender_handle")
        if isinstance(data.get("sender_handle"), dict)
        else {}
    )
    chat_id = str(chat.get("id") or "")
    handle = str(sender.get("handle") or "")
    if not chat_id or not handle:
        return None

    # A message can carry several parts (text, media, link). Join the text ones;
    # a photo-only message yields "" and is handled as an unrecognised input.
    parts = data.get("parts") if isinstance(data.get("parts"), list) else []
    text = " ".join(
        str(p.get("value") or "")
        for p in parts
        if isinstance(p, dict) and p.get("type") == "text"
    ).strip()

    return InboundMessage(
        chat_id=chat_id, handle=handle, text=text, event_type=event_type
    )


def verify_signature(
    *, secret: str, body: bytes, headers: dict[str, str]
) -> bool:
    """Standard Webhooks verification.

    signature = base64(HMAC-SHA256(secret, f"{id}.{timestamp}.{body}"))

    An empty secret means verification is disabled — acceptable for local
    tunnelled development, never in production. The caller decides.
    """
    if not secret:
        return True

    lowered = {k.lower(): v for k, v in headers.items()}
    msg_id = lowered.get("webhook-id", "")
    timestamp = lowered.get("webhook-timestamp", "")
    signature_header = lowered.get("webhook-signature", "")
    if not (msg_id and timestamp and signature_header):
        return False

    key = secret
    if key.startswith("whsec_"):
        key = key[len("whsec_"):]
    try:
        key_bytes = base64.b64decode(key)
    except Exception:  # noqa: BLE001 - a non-base64 secret is used verbatim
        key_bytes = key.encode("utf-8")

    signed = f"{msg_id}.{timestamp}.".encode("utf-8") + body
    expected = base64.b64encode(hmac.new(key_bytes, signed, hashlib.sha256).digest())

    # The header carries one or more space-separated "v1,<sig>" entries so a
    # secret can be rotated without dropping in-flight deliveries.
    for entry in signature_header.split(" "):
        _, _, candidate = entry.partition(",")
        if candidate and hmac.compare_digest(candidate.encode("utf-8"), expected):
            return True
    return False


class LinqClient:
    """Sends messages and manages webhook subscriptions."""

    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.linq_api_key)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.linq_api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.linq_api_base_url.rstrip('/')}{path}"
        response = self.session.post(
            url, headers=self._headers, json=payload, timeout=30
        )
        if response.status_code >= 400:
            raise LinqError(
                f"Linq {path} failed with {response.status_code}: {response.text[:500]}"
            )
        try:
            return response.json()
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _text_parts(text: str) -> list[dict[str, str]]:
        return [{"type": "text", "value": text}]

    def send_to_chat(self, chat_id: str, text: str) -> dict[str, Any]:
        """Reply inside an existing thread — the normal path, since every
        conversation starts with the customer texting us."""
        if not self.enabled:
            return {}
        return self._post(
            f"/chats/{chat_id}/messages/send",
            {"message": {"parts": self._text_parts(text)}},
        )

    def send_message(self, to: list[str], text: str) -> dict[str, Any]:
        """Start a new thread with one or more handles."""
        if not self.enabled:
            return {}
        return self._post(
            "/messages", {"to": to, "message": {"parts": self._text_parts(text)}}
        )

    def register_webhook(self, target_url: str, events: list[str]) -> dict[str, Any]:
        if not self.enabled:
            return {}
        return self._post(
            "/webhook-subscriptions",
            {"target_url": target_url, "subscribed_events": events},
        )
