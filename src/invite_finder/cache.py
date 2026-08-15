from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from invite_finder.store import cache_store

TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"trk", "fbclid", "gclid", "mc_cid", "mc_eid"}

# Credentials that ride in a query string (Apify accepts ?token=). They must
# never reach the fingerprint or the stored url column: rotating a token would
# otherwise invalidate the whole cache and re-bill every lookup, and the secret
# would sit in plaintext in the DB.
SECRET_QUERY_PARAMS = {"token", "api_key", "apikey", "access_token"}

# Request-body keys that vary per call without changing the response. `zone`
# is Bright Data's account routing; `mandate_id` and `max_price` are Perflo's
# per-purchase authorization. Leave them in the fingerprint and every call is
# a cache miss — and on a pay-per-call provider every miss is a charge.
VOLATILE_PAYLOAD_KEYS = {"zone", "mandate_id", "max_price"}

TTL_BY_KIND: dict[str, timedelta | None] = {
    "luma_api": timedelta(hours=6),
    "luma_page": timedelta(hours=24),
    "serp": timedelta(days=30),
    "linkedin_profile": timedelta(days=30),
    "page": timedelta(days=7),
    # Paid channel-B kinds. Contact data changes slowly and costs the most, so
    # it gets the longest life; posts are the point of being fresh.
    "linkedin_search": timedelta(days=30),
    "linkedin_posts": timedelta(days=3),
    "x_profile": timedelta(days=7),
    "contact_enrich": timedelta(days=90),
    "email_verify": timedelta(days=90),
}


class CacheMiss(RuntimeError):
    """Raised in offline mode when a request has no cached response."""


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
        and not any(k.lower().startswith(p) for p in TRACKING_PARAM_PREFIXES)
    ]
    query_pairs.sort()
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))


def derive_kind(url: str) -> str:
    normalized = normalize_url(url).lower()
    if "api.lu.ma/url" in normalized:
        return "luma_api"
    if "lu.ma/" in normalized or "luma.com/" in normalized:
        return "luma_page"
    if "google.com/search" in normalized:
        return "serp"
    if "linkedin.com/in/" in normalized:
        return "linkedin_profile"
    return "page"


def strip_secret_params(url: str) -> str:
    """Remove credential-bearing query params from a URL."""
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in SECRET_QUERY_PARAMS
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def canonical_payload(
    payload: dict[str, Any], *, endpoint: str | None = None
) -> dict[str, Any]:
    canonical = {k: v for k, v in payload.items() if k not in VOLATILE_PAYLOAD_KEYS}
    if "url" in canonical and isinstance(canonical["url"], str):
        # Bright Data shape: the target is inside the body, so the body alone
        # identifies the request.
        canonical["url"] = normalize_url(canonical["url"])
    elif endpoint:
        # Channel-B shape: the body is the provider's input document and the
        # target lives in the endpoint (which actor, which Perflo route). Fold
        # it in, or two different actors called with identical input would
        # collide on one fingerprint and serve each other's cached answer.
        canonical["__endpoint"] = normalize_url(strip_secret_params(endpoint))
    return canonical


def fingerprint(payload: dict[str, Any], *, endpoint: str | None = None) -> str:
    canonical = canonical_payload(payload, endpoint=endpoint)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def expires_at_for(kind: str, *, now: datetime | None = None) -> str | None:
    ttl = TTL_BY_KIND.get(kind)
    if ttl is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now + ttl).isoformat()


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    return datetime.now(timezone.utc) > expiry


def _build_response(row: sqlite3.Row, *, from_cache: bool, stale: bool = False) -> requests.Response:
    response = requests.Response()
    response.status_code = row["status_code"]
    response.headers["content-type"] = row["content_type"] or "application/json"
    response._content = row["body"].encode("utf-8")
    response.url = row["url"]
    response.request = requests.PreparedRequest()
    response.request.method = "POST"
    response.request.url = row["url"]
    # Custom markers read by callers that want to distinguish cache hits.
    response.headers["x-invite-finder-cache"] = "stale" if stale else "hit" if from_cache else "miss"
    return response


class CachingSession(requests.Session):
    """A requests.Session that transparently caches Bright Data POST requests.

    Every call to BrightDataClient._post_request goes through session.post(...),
    which requests.Session implements by calling self.request("POST", url, ...).
    Overriding request() here catches every Bright Data call without touching
    brightdata.py.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        offline: bool = False,
        force_refresh: bool = False,
    ) -> None:
        super().__init__()
        self.conn = conn
        self.offline = offline
        self.force_refresh = force_refresh
        self.last_was_cache_hit: bool | None = None
        self.last_kind: str | None = None
        self.last_url: str | None = None

    def request(  # type: ignore[override]
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        cache_kind: str | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        # cache_kind is ours, not requests' — it must never reach super().
        if method.upper() != "POST" or not isinstance(json, dict):
            return super().request(method, url, json=json, **kwargs)

        payload = json
        # Bright Data carries its target inside the body; channel-B providers
        # carry it in the endpoint. Only the latter needs the endpoint folded
        # into the cache key.
        body_url = payload.get("url")
        endpoint = None if isinstance(body_url, str) else url
        target_url = body_url if isinstance(body_url, str) else strip_secret_params(url)
        kind = cache_kind or derive_kind(str(target_url))
        fp = fingerprint(payload, endpoint=endpoint)
        self.last_kind = kind
        self.last_url = str(target_url)

        if not self.force_refresh:
            cached = cache_store.get(self.conn, fp)
            if cached is not None:
                stale = _is_expired(cached["expires_at"])
                if not stale or self.offline:
                    self.last_was_cache_hit = True
                    return _build_response(cached, from_cache=True, stale=stale)

        if self.offline:
            self.last_was_cache_hit = False
            raise CacheMiss(
                f"No cached response for {kind} request to {target_url} "
                f"(fingerprint={fp[:12]}...) while running offline."
            )

        response = super().request(method, url, json=json, **kwargs)
        self.last_was_cache_hit = False
        cache_store.put(
            self.conn,
            fingerprint=fp,
            kind=kind,
            url=str(target_url),
            request_json=json_module_dumps(canonical_payload(payload, endpoint=endpoint)),
            status_code=response.status_code,
            content_type=response.headers.get("content-type"),
            body=response.text,
            expires_at=expires_at_for(kind),
        )
        return response


def json_module_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def seed_from_fixture(
    conn: sqlite3.Connection,
    *,
    payload: dict[str, Any],
    body: str,
    status_code: int = 200,
    content_type: str = "application/json",
) -> str:
    """Seed the cache for a request the real pipeline will make, keyed by the
    exact fingerprint the real code computes. Used by offline dev tooling so
    fixtures never drift from the live fingerprinting logic."""
    fp = fingerprint(payload)
    kind = derive_kind(str(payload.get("url", "")))
    cache_store.seed_raw(
        conn,
        fingerprint=fp,
        kind=kind,
        url=str(payload.get("url", "")),
        body=body,
        status_code=status_code,
        content_type=content_type,
        request_json=json_module_dumps(canonical_payload(payload)),
        expires_at=expires_at_for(kind),
    )
    return fp
