"""Perflo channel — pay-per-call services bought against an agent mandate.

Four steps, per Perflo's service-purchase flow:

    GET  /v1/services?capability=…   discover who can do this
    POST /v1/purchase-quotes         what will it cost
    POST /v1/purchases               buy it (agent path: mandate, no confirmation)
    GET  /v1/purchases/{id}          poll until it leaves queued|running|settling

The mandate is the point: it carries a hard spending cap the agent cannot talk
its way past, so an autonomous loop can buy data without being able to drain
the account.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import requests

from invite_finder.config import Settings
from invite_finder.providers.base import (
    CACHE_KIND_BY_CAPABILITY,
    Capability,
    ProviderError,
    ProviderResult,
)

# What to search the marketplace for, per capability.
SERVICE_QUERY_BY_CAPABILITY: dict[str, str] = {
    Capability.LINKEDIN_BY_NAME: "linkedin profile search",
    Capability.LINKEDIN_POSTS: "linkedin profile posts",
    Capability.X_PROFILE: "x twitter profile",
    Capability.CONTACT_ENRICH: "email phone enrichment",
    Capability.EMAIL_VERIFY: "email verification",
}

# Ceiling per individual call, in USD. A quote above this is refused rather
# than paid — it usually means the router matched the wrong service.
MAX_PRICE_USD_BY_CAPABILITY: dict[str, str] = {
    Capability.LINKEDIN_BY_NAME: "0.05",
    Capability.LINKEDIN_POSTS: "0.05",
    Capability.X_PROFILE: "0.05",
    Capability.CONTACT_ENRICH: "0.25",
    Capability.EMAIL_VERIFY: "0.02",
}

TERMINAL_POLL_STATES = {"queued", "running", "settling"}
RESULT_KEYS = ("result", "output", "data", "items", "records")


def _to_cents(amount: Any) -> int | None:
    if amount is None:
        return None
    try:
        return int(round(float(str(amount)) * 100))
    except (TypeError, ValueError):
        return None


class PerfloChannel:
    name = "perflo"
    channel = "perflo_marketplace"

    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or requests.Session()
        # Service discovery is stable for the life of a process and costs
        # nothing, so resolve each capability at most once.
        self._service_ids: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.settings.perflo_agent_token and self.settings.perflo_mandate_id)

    def supports(self, capability: str) -> bool:
        return self.enabled and capability in SERVICE_QUERY_BY_CAPABILITY

    # --- plumbing ------------------------------------------------------------

    @property
    def _base(self) -> str:
        return self.settings.perflo_api_base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.perflo_agent_token}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self.session.get(
            f"{self._base}{path}", headers=self._headers, params=params, timeout=30
        )
        if response.status_code >= 400:
            raise ProviderError(
                f"perflo GET {path} failed with {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError(f"perflo GET {path} returned invalid JSON") from exc

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        cache_kind: str | None = None,
    ) -> Any:
        headers = dict(self._headers)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        kwargs: dict[str, Any] = {
            "headers": headers,
            "json": payload,
            "params": params,
            "timeout": 60,
        }
        if cache_kind:
            kwargs["cache_kind"] = cache_kind
        try:
            response = self.session.post(f"{self._base}{path}", **kwargs)
        except TypeError:
            kwargs.pop("cache_kind", None)
            response = self.session.post(f"{self._base}{path}", **kwargs)

        if response.status_code >= 400:
            raise ProviderError(
                f"perflo POST {path} failed with {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError(f"perflo POST {path} returned invalid JSON") from exc

    # --- flow ----------------------------------------------------------------

    def _service_id(self, capability: str) -> str:
        cached = self._service_ids.get(capability)
        if cached:
            return cached

        query = SERVICE_QUERY_BY_CAPABILITY[capability]
        data = self._get(
            "/v1/services",
            {"mandate_id": self.settings.perflo_mandate_id, "q": query, "limit": 5},
        )
        services = data.get("services") or data.get("data") or data if isinstance(data, (dict, list)) else []
        if isinstance(services, dict):
            services = services.get("items") or []
        for service in services if isinstance(services, list) else []:
            if not isinstance(service, dict):
                continue
            service_id = service.get("id") or service.get("service_id")
            if service_id:
                self._service_ids[capability] = str(service_id)
                return str(service_id)

        raise ProviderError(f"perflo has no service matching {capability!r}")

    def _quote(self, service_id: str) -> int | None:
        """Ask what a call costs before making it. Returns cents, or None when
        Perflo declines to quote (in which case max_price still bounds us)."""
        try:
            data = self._post(
                "/v1/purchase-quotes",
                {"target": {"kind": "service", "service_id": service_id}},
                params={"mandate_id": self.settings.perflo_mandate_id},
            )
        except ProviderError:
            return None
        price = data.get("price") if isinstance(data, dict) else None
        if isinstance(price, dict):
            return _to_cents(price.get("amount"))
        return _to_cents(data.get("observed_price") if isinstance(data, dict) else None)

    @staticmethod
    def _extract_records(purchase: dict[str, Any]) -> list[dict[str, Any]]:
        """Find the service's output inside the settled purchase. The envelope
        is Perflo's; the payload shape is the individual service's, so probe
        the usual carrier keys rather than assuming one."""
        candidates: list[Any] = []
        for key in RESULT_KEYS:
            if key in purchase:
                candidates.append(purchase[key])
        result = purchase.get("result")
        if isinstance(result, dict):
            for key in RESULT_KEYS:
                if key in result:
                    candidates.append(result[key])

        for candidate in candidates:
            if isinstance(candidate, list):
                records = [item for item in candidate if isinstance(item, dict)]
                if records:
                    return records
            elif isinstance(candidate, dict):
                return [candidate]
        return []

    def _poll(self, purchase_id: str, *, attempts: int = 30, delay: float = 1.0) -> dict[str, Any]:
        # Blocking sleep, matching the rest of the pipeline's sync-inside-async
        # style (the Bright Data path blocks the loop the same way).
        for _ in range(attempts):
            purchase = self._get(f"/v1/purchases/{purchase_id}")
            if not isinstance(purchase, dict):
                raise ProviderError("perflo purchase response was not an object")
            status = str(purchase.get("status") or "").lower()
            if status not in TERMINAL_POLL_STATES:
                return purchase
            time.sleep(delay)
        raise ProviderError(f"perflo purchase {purchase_id} did not settle in time")

    def fetch(self, capability: str, payload: dict[str, Any]) -> ProviderResult:
        if not self.enabled:
            raise ProviderError("PERFLO_AGENT_TOKEN / PERFLO_MANDATE_ID are not configured")
        if capability not in SERVICE_QUERY_BY_CAPABILITY:
            raise ProviderError(f"perflo does not serve {capability}")

        service_id = self._service_id(capability)
        quote = self._quote(service_id)

        body = {
            "target": {"kind": "service", "service_id": service_id},
            "input": payload,
            "max_price": {
                "amount": MAX_PRICE_USD_BY_CAPABILITY.get(capability, "0.10"),
                "currency": "USD",
            },
            "mandate_id": self.settings.perflo_mandate_id,
        }
        operation = self._post(
            "/v1/purchases",
            body,
            idempotency_key=uuid.uuid4().hex,
            cache_kind=CACHE_KIND_BY_CAPABILITY.get(capability),
        )
        purchase_id = (
            operation.get("resource_id") or operation.get("id")
            if isinstance(operation, dict)
            else None
        )
        if not purchase_id:
            raise ProviderError("perflo purchase did not return a resource id")

        purchase = self._poll(str(purchase_id))
        status = str(purchase.get("status") or "").lower()
        if status in {"failed", "denied", "canceled", "cancelled"}:
            raise ProviderError(
                f"perflo purchase {purchase_id} ended {status}: "
                f"{purchase.get('error') or purchase.get('denial_code') or ''}"
            )

        charged = purchase.get("charged") or purchase.get("amount")
        charged_cents = (
            _to_cents(charged.get("amount")) if isinstance(charged, dict) else _to_cents(charged)
        )

        return ProviderResult(
            capability=capability,
            records=self._extract_records(purchase),
            provider=str(purchase.get("service_id") or service_id),
            channel=self.channel,
            charged_cents=charged_cents if charged_cents is not None else (quote or 0),
            quote_cents=quote,
            purchase_id=str(purchase_id),
        )
