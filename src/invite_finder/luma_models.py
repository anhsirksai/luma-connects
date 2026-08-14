from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LumaVenue(BaseModel):
    name: str | None = None
    address: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class LumaPerson(BaseModel):
    luma_user_api_id: str | None = None
    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    bio_short: str | None = None
    avatar_url: str | None = None
    linkedin_url: str | None = None
    linkedin_company_url: str | None = None
    twitter_handle: str | None = None
    website: str | None = None
    relation: Literal["host", "featured_guest", "speaker"]


class LumaEvent(BaseModel):
    slug: str
    source_url: str
    luma_api_id: str | None = None
    name: str
    description: str | None = None
    cover_url: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = None
    location_type: str | None = None
    venue: LumaVenue = Field(default_factory=LumaVenue)
    guest_count: int | None = None
    show_guest_list: bool = False
    categories: list[str] = Field(default_factory=list)
    people: list[LumaPerson] = Field(default_factory=list)
    ingest_source: Literal["luma_api", "luma_jsonld", "luma_markdown", "manual"]
    warnings: list[str] = Field(default_factory=list)
    raw: dict | None = None
