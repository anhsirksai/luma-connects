from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when required runtime configuration is missing."""


def load_dotenv_if_available() -> None:
    """Loads a .env file from the current working directory upward, if one
    exists and python-dotenv is installed. Never overrides variables already
    present in the environment. Called by Settings.from_env() so every entry
    point (CLI, API server) picks up .env without having to remember to."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    brightdata_api_key: str = ""
    brightdata_serp_zone: str = ""
    brightdata_unlocker_zone: str = ""
    brightdata_request_endpoint: str = "https://api.brightdata.com/request"
    brightdata_timeout_seconds: int = 60
    brightdata_country: str = "us"
    openai_agent_model: str = "gpt-5.5"
    invite_db_path: str = "data/private/invite_finder.db"
    invite_offline: bool = False
    invite_cors_origins: str = "http://localhost:3000"
    # Messaging front door (Linq). Empty means the webhook route still parses
    # and stores, but no reply is delivered — which is what tests exercise.
    linq_api_key: str = ""
    linq_api_base_url: str = "https://api.linqapp.com/api/partner/v3"
    linq_webhook_secret: str = ""
    # Money in. One Payment Link is reused for every transaction; the order is
    # carried on it as ?client_reference_id=<order_id>.
    stripe_payment_link: str = ""
    stripe_webhook_secret: str = ""
    # Money out / channel B. Perflo brokers pay-per-call services; Apify is the
    # same catalogue reachable directly with a token and no KYC.
    perflo_api_base_url: str = "https://api.perflo.ai"
    perflo_agent_token: str = ""
    perflo_mandate_id: str = ""
    apify_token: str = ""
    apify_api_base_url: str = "https://api.apify.com/v2"
    # Hard ceiling on what one run may spend, independent of the Perflo
    # mandate, so a bug cannot drain the budget even if the mandate is generous.
    enrichment_budget_cents: int = 500
    # Operator auth. Setting ADMIN_PHONE turns on the passcode gate for the
    # data routes; the code is texted to this number over Linq. Empty means
    # the API is OPEN — fine on localhost, never on a public URL.
    admin_phone: str = ""
    admin_session_ttl_hours: int = 24
    public_base_url: str = "http://localhost:8000"

    @classmethod
    def from_env(cls, offline: bool | None = None) -> "Settings":
        load_dotenv_if_available()
        is_offline = _bool_env("INVITE_OFFLINE", False) if offline is None else offline

        if is_offline:
            brightdata_api_key = os.getenv("BRIGHTDATA_API_KEY", "")
            brightdata_serp_zone = os.getenv("BRIGHTDATA_SERP_ZONE", "")
            brightdata_unlocker_zone = os.getenv("BRIGHTDATA_UNLOCKER_ZONE", "")
        else:
            brightdata_api_key = _required_env("BRIGHTDATA_API_KEY")
            brightdata_serp_zone = _required_env("BRIGHTDATA_SERP_ZONE")
            brightdata_unlocker_zone = _required_env("BRIGHTDATA_UNLOCKER_ZONE")

        return cls(
            brightdata_api_key=brightdata_api_key,
            brightdata_serp_zone=brightdata_serp_zone,
            brightdata_unlocker_zone=brightdata_unlocker_zone,
            brightdata_request_endpoint=os.getenv(
                "BRIGHTDATA_REQUEST_ENDPOINT",
                "https://api.brightdata.com/request",
            ),
            brightdata_timeout_seconds=_int_env("BRIGHTDATA_TIMEOUT_SECONDS", 60),
            brightdata_country=os.getenv("BRIGHTDATA_COUNTRY", "us"),
            openai_agent_model=os.getenv("OPENAI_AGENT_MODEL", "gpt-5.5"),
            invite_db_path=os.getenv("INVITE_DB_PATH", "data/private/invite_finder.db"),
            invite_offline=is_offline,
            invite_cors_origins=os.getenv("INVITE_CORS_ORIGINS", "http://localhost:3000"),
            linq_api_key=os.getenv("LINQ_API_KEY", ""),
            linq_api_base_url=os.getenv(
                "LINQ_API_BASE_URL", "https://api.linqapp.com/api/partner/v3"
            ),
            linq_webhook_secret=os.getenv("LINQ_WEBHOOK_SECRET", ""),
            stripe_payment_link=os.getenv("STRIPE_PAYMENT_LINK", ""),
            stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
            perflo_api_base_url=os.getenv("PERFLO_API_BASE_URL", "https://api.perflo.ai"),
            perflo_agent_token=os.getenv("PERFLO_AGENT_TOKEN", ""),
            perflo_mandate_id=os.getenv("PERFLO_MANDATE_ID", ""),
            apify_token=os.getenv("APIFY_TOKEN", ""),
            apify_api_base_url=os.getenv("APIFY_API_BASE_URL", "https://api.apify.com/v2"),
            enrichment_budget_cents=_int_env("ENRICHMENT_BUDGET_CENTS", 500),
            admin_phone=os.getenv("ADMIN_PHONE", ""),
            admin_session_ttl_hours=_int_env("ADMIN_SESSION_TTL_HOURS", 24),
            # RENDER_EXTERNAL_URL is injected by Render with the service's real
            # public URL. Falling back to it means the deployed app knows its
            # own address without anyone hardcoding a guess that has to be
            # corrected after the first deploy.
            public_base_url=(
                os.getenv("PUBLIC_BASE_URL")
                or os.getenv("RENDER_EXTERNAL_URL")
                or "http://localhost:8000"
            ),
        )
