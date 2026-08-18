from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


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


# Hostnames that mean "this machine". ADMIN_AUTH=off is only honoured when the
# service's own base URL is one of these — a tunnel forwarding to localhost is
# still a public address, and is exactly the case the gate exists for.
LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def _is_local_url(url: str) -> bool:
    try:
        hostname = urlsplit(url).hostname
    except ValueError:
        return False
    return (hostname or "").lower() in LOCAL_HOSTNAMES


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
    # "auto" | "on" | "off". See admin_auth_enabled below; from_env() refuses
    # "off" anywhere that is not localhost.
    admin_auth_mode: str = "auto"
    admin_session_ttl_hours: int = 24
    public_base_url: str = "http://localhost:8000"

    @property
    def admin_auth_enabled(self) -> bool:
        """Whether the passcode gate is enforced on the data routes.

        "auto" is the original rule and the default: the gate is on exactly
        when there is a phone to text a code to. "off" is the local-development
        escape hatch — from_env() refuses to honour it anywhere non-local, so
        it cannot be deployed by accident. "on" is for a host that has a phone
        and wants the gate stated rather than inferred.
        """
        if self.admin_auth_mode == "off":
            return False
        if self.admin_auth_mode == "on":
            return True
        return bool(self.admin_phone)

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

        admin_phone = os.getenv("ADMIN_PHONE", "")
        admin_auth_mode = (os.getenv("ADMIN_AUTH") or "auto").strip().lower()
        if admin_auth_mode not in {"auto", "on", "off"}:
            raise ConfigError(
                f"ADMIN_AUTH must be one of auto, on, off (got {admin_auth_mode!r})."
            )

        # RENDER_EXTERNAL_URL is injected by Render with the service's real
        # public URL. Falling back to it means the deployed app knows its own
        # address without anyone hardcoding a guess that has to be corrected
        # after the first deploy.
        render_external_url = os.getenv("RENDER_EXTERNAL_URL")
        public_base_url = (
            os.getenv("PUBLIC_BASE_URL") or render_external_url or "http://localhost:8000"
        )

        if admin_auth_mode == "on" and not admin_phone:
            raise ConfigError(
                "ADMIN_AUTH=on requires ADMIN_PHONE. The gate would be on with no "
                "way to deliver a passcode, which locks the operator out of their "
                "own API."
            )

        # The whole value of the off switch is that it cannot travel. Two
        # independent checks, because either one alone has a hole: a deploy
        # could override PUBLIC_BASE_URL to localhost, and a cloudflared tunnel
        # is a public address with no RENDER_EXTERNAL_URL to give it away.
        if admin_auth_mode == "off":
            if render_external_url:
                raise ConfigError(
                    "ADMIN_AUTH=off is refused here: RENDER_EXTERNAL_URL is set "
                    f"({render_external_url}), so this is a public deployment. The "
                    "data routes serve names, LinkedIn URLs, emails and phone "
                    "numbers; they do not go out unauthenticated."
                )
            if not _is_local_url(public_base_url):
                raise ConfigError(
                    "ADMIN_AUTH=off is only allowed when the service is bound to "
                    f"localhost (PUBLIC_BASE_URL is {public_base_url}). Either drop "
                    "ADMIN_AUTH=off, or pass PUBLIC_BASE_URL=http://localhost:8000 "
                    "for a local-only run."
                )

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
            admin_phone=admin_phone,
            admin_auth_mode=admin_auth_mode,
            admin_session_ttl_hours=_int_env("ADMIN_SESSION_TTL_HOURS", 24),
            public_base_url=public_base_url,
        )
