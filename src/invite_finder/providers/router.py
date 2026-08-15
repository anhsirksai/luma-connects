"""Capability routing across channels, with one result cache and one budget.

The router is the only thing the pipeline talks to. It owns three jobs that
must not be duplicated per-channel:

1. **Result cache.** Keyed on (capability, input), *above* the HTTP layer. A
   fallback from Perflo to Apify must not pay twice for the same answer, and a
   re-run of the same event must not pay at all.
2. **Budget ceiling.** A hard cents cap per process, independent of the Perflo
   mandate, so a loop bug cannot outspend the revenue it is serving.
3. **Cost ledger.** Every attempt lands in `service_purchases` — hits, misses
   and failures — which is what makes margin auditable.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Protocol

import requests

from invite_finder.cache import expires_at_for, fingerprint
from invite_finder.config import Settings
from invite_finder.providers.apify import ApifyChannel
from invite_finder.providers.base import (
    CACHE_KIND_BY_CAPABILITY,
    BudgetExceeded,
    Channel,
    ProviderError,
    ProviderResult,
)
from invite_finder.providers.perflo import PerfloChannel
from invite_finder.store import cache_store, commerce_store


class Reporter(Protocol):
    def emit(
        self, type_: str, message: str, data: dict[str, Any] | None = None
    ) -> None: ...


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    return datetime.now(timezone.utc) > expiry


class ProviderRouter:
    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        *,
        channels: list[Channel] | None = None,
        session: requests.Session | None = None,
        reporter: Reporter | None = None,
        order_id: int | None = None,
    ) -> None:
        self.conn = conn
        self.settings = settings
        self.reporter = reporter
        self.order_id = order_id
        if channels is None:
            # Perflo first: it brokers a wider catalogue and bills the mandate.
            # Apify is the same data reachable without waiting on KYC, so it
            # backs Perflo up rather than replacing it.
            channels = [
                PerfloChannel(settings, session=session),
                ApifyChannel(settings, session=session),
            ]
        self.channels = channels

    # --- cache ---------------------------------------------------------------

    @staticmethod
    def cache_key(capability: str, payload: dict[str, Any]) -> str:
        return fingerprint({"capability": capability, "input": payload})

    def _cached(self, capability: str, payload: dict[str, Any]) -> list[dict] | None:
        row = cache_store.get(self.conn, self.cache_key(capability, payload))
        if row is None:
            return None
        # Offline serves stale rather than nothing, matching CachingSession.
        if _is_expired(row["expires_at"]) and not self.settings.invite_offline:
            return None
        try:
            records = json.loads(row["body"])
        except json.JSONDecodeError:
            return None
        return records if isinstance(records, list) else None

    def _store(
        self, capability: str, payload: dict[str, Any], records: list[dict]
    ) -> None:
        kind = CACHE_KIND_BY_CAPABILITY.get(capability, "page")
        cache_store.put(
            self.conn,
            fingerprint=self.cache_key(capability, payload),
            kind=kind,
            url=f"provider://{capability}",
            request_json=json.dumps(
                {"capability": capability, "input": payload}, sort_keys=True
            ),
            status_code=200,
            content_type="application/json",
            body=json.dumps(records),
            expires_at=expires_at_for(kind),
        )

    # --- budget --------------------------------------------------------------

    def spent_cents(self) -> int:
        return commerce_store.spend_cents(self.conn)

    def remaining_cents(self) -> int:
        return max(0, self.settings.enrichment_budget_cents - self.spent_cents())

    # --- main entry point ----------------------------------------------------

    def fetch(
        self,
        capability: str,
        payload: dict[str, Any],
        *,
        person_id: int | None = None,
    ) -> ProviderResult:
        """Serve a capability from cache, else from the first channel that can.

        Never raises for an upstream failure — an empty result lets the caller
        degrade one field instead of failing a paid job. It does raise
        BudgetExceeded, because quietly overspending is not a degradation.
        """
        cached = self._cached(capability, payload)
        if cached is not None:
            commerce_store.record_service_purchase(
                self.conn,
                capability=capability,
                provider="cache",
                channel="cache",
                status="hit",
                charged_cents=0,
                cache_hit=True,
                person_id=person_id,
                order_id=self.order_id,
            )
            return ProviderResult(
                capability=capability,
                records=cached,
                provider="cache",
                channel="cache",
                charged_cents=0,
                cache_hit=True,
            )

        if self.settings.invite_offline:
            # Offline demo mode: skip the field rather than pretend to buy it.
            self._emit("log", f"{capability}: skipped (offline, no cached result)")
            return ProviderResult(capability=capability, channel="offline")

        if self.remaining_cents() <= 0:
            raise BudgetExceeded(
                f"Enrichment budget of {self.settings.enrichment_budget_cents}c is spent; "
                f"refusing to buy {capability}."
            )

        supported = [c for c in self.channels if c.supports(capability)]
        if not supported:
            self._emit("log", f"{capability}: no channel configured")
            return ProviderResult(capability=capability, channel="none")

        last_error: str | None = None
        for channel in supported:
            try:
                result = channel.fetch(capability, payload)
            except ProviderError as exc:
                last_error = str(exc)
                commerce_store.record_service_purchase(
                    self.conn,
                    capability=capability,
                    provider=getattr(channel, "name", "unknown"),
                    channel=getattr(channel, "channel", "unknown"),
                    status="failed",
                    charged_cents=0,
                    person_id=person_id,
                    order_id=self.order_id,
                    error=last_error[:500],
                )
                self._emit(
                    "log",
                    f"{capability}: {getattr(channel, 'name', '?')} failed, trying next",
                    {"error": last_error[:200]},
                )
                continue

            if result.records:
                self._store(capability, payload, result.records)
            commerce_store.record_service_purchase(
                self.conn,
                capability=capability,
                provider=result.provider or getattr(channel, "name", "unknown"),
                channel=result.channel or getattr(channel, "channel", "unknown"),
                status="ok",
                quote_cents_=result.quote_cents,
                charged_cents=result.charged_cents,
                purchase_id=result.purchase_id,
                cache_hit=result.cache_hit,
                person_id=person_id,
                order_id=self.order_id,
            )
            return result

        self._emit("log", f"{capability}: every channel failed", {"error": last_error})
        return ProviderResult(capability=capability, channel="exhausted")

    def _emit(
        self, type_: str, message: str, data: dict[str, Any] | None = None
    ) -> None:
        if self.reporter is not None:
            self.reporter.emit(type_, message, data)
