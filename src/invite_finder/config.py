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
        )
