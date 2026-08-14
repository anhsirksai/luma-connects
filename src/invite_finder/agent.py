from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from agents import Agent, RunContextWrapper, function_tool

from invite_finder.event import extract_event_page_context
from invite_finder.models import ProfileSearchReport
from invite_finder.protocols import WebDataClient


LINKEDIN_PROFILE_RE = re.compile(r"https?://([a-z]{2,3}\.)?linkedin\.com/in/[^/?#\s]+")
SAN_FRANCISCO_ALIASES = (
    "San Francisco",
    "San Francisco Bay Area",
    "SF Bay Area",
    "Bay Area",
)


@dataclass
class LeadFinderContext:
    brightdata: WebDataClient
    city: str
    max_profiles: int = 20
    max_serp_queries: int = 5
    max_serp_results_per_query: int = 10
    max_page_fetches: int = 8
    serp_queries_used: list[str] = field(default_factory=list)
    fetched_pages: list[str] = field(default_factory=list)


def normalize_linkedin_profile_url(url: str) -> str | None:
    match = LINKEDIN_PROFILE_RE.search(url)
    if not match:
        return None
    parsed = urlsplit(match.group(0))
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), path, "", ""))


def city_search_terms(city: str) -> list[str]:
    city = city.strip()
    if not city:
        return []

    terms = [city]
    if "san francisco" in city.lower() or "bay area" in city.lower():
        terms.extend(SAN_FRANCISCO_ALIASES)

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(term)
    return deduped


def google_quote(value: str) -> str:
    cleaned = value.strip().replace('"', "")
    if not cleaned:
        return ""
    if " " in cleaned or "-" in cleaned:
        return f'"{cleaned}"'
    return cleaned


def google_or_group(values: list[str]) -> str:
    quoted = [google_quote(value) for value in values if google_quote(value)]
    if not quoted:
        return ""
    if len(quoted) == 1:
        return quoted[0]
    return f"({' OR '.join(quoted)})"


def query_mentions_city(query: str, city: str) -> bool:
    lowered = query.lower()
    return any(term.lower() in lowered for term in city_search_terms(city))


def ensure_linkedin_city_query(query: str, city: str) -> str:
    normalized_query = query.strip()
    if "site:linkedin.com/in" not in normalized_query:
        normalized_query = f"site:linkedin.com/in/ {normalized_query}"
    if not query_mentions_city(normalized_query, city):
        city_group = google_or_group(city_search_terms(city))
        normalized_query = f"{normalized_query} {city_group}".strip()
    return normalized_query


def build_seed_search_queries(city: str, event_url: str) -> list[str]:
    """Return broad-to-specific SERP queries for the first search pass."""
    city_group = google_or_group(city_search_terms(city))
    lowered_url = event_url.lower()

    if "vla" in lowered_url or "robot" in lowered_url:
        return [
            (
                'site:linkedin.com/in/ {city} '
                '(robotics OR "robot learning" OR "embodied AI" OR "AI robotics")'
            ).format(city=city_group),
            (
                'site:linkedin.com/in/ {city} '
                '(founder OR engineer OR researcher OR investor) '
                '(robotics OR "embodied AI" OR "robot learning")'
            ).format(city=city_group),
            (
                'site:linkedin.com/in/ {city} '
                '("robot learning" OR "imitation learning" OR "reinforcement learning")'
            ).format(city=city_group),
            (
                'site:linkedin.com/in/ {city} '
                '("computer vision" OR multimodal OR "foundation models") '
                '(robotics OR AI)'
            ).format(city=city_group),
            (
                'site:linkedin.com/in/ {city} '
                '("Physical Intelligence" OR "Figure AI" OR Covariant OR '
                'NVIDIA OR "Google DeepMind")'
            ).format(city=city_group),
            (
                'site:linkedin.com/in/ {city} '
                '("vision-language-action" OR "vision language action" OR VLA)'
            ).format(city=city_group),
        ]

    return [
        (
            'site:linkedin.com/in/ {city} '
            '(founder OR engineer OR researcher OR investor OR operator)'
        ).format(city=city_group),
        (
            'site:linkedin.com/in/ {city} '
            '("AI" OR startup OR technology OR community)'
        ).format(city=city_group),
        (
            'site:linkedin.com/in/ {city} '
            '(speaker OR panelist OR conference OR meetup)'
        ).format(city=city_group),
    ]


TOPIC_STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "event", "join",
    "panel", "night", "discussion", "fireside", "your", "will", "have",
    "about", "into", "over", "more", "than", "what", "when", "where", "come",
}


def _extract_topic_terms(
    event_name: str, categories: list[str], description: str | None
) -> list[str]:
    """Best-effort topic keywords for SERP seeding, derived from the event's
    own metadata rather than a hardcoded per-URL branch."""
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        key = term.strip().lower()
        if len(key) < 4 or key in TOPIC_STOPWORDS or key in seen:
            return
        seen.add(key)
        terms.append(term.strip())

    for category in categories:
        add(category)

    cleaned_name = re.sub(r"[\[\]()]", " ", event_name)
    for word in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", cleaned_name):
        add(word)

    if description:
        for word in re.findall(r"[A-Za-z][A-Za-z\-]{3,}", description)[:20]:
            add(word)

    return terms


def build_seed_search_queries_for_event(
    *,
    city: str,
    event_name: str,
    categories: list[str] | None = None,
    description: str | None = None,
) -> list[str]:
    """Broad-to-specific SERP queries derived from an event's own name,
    categories, and description, instead of a hardcoded per-URL topic branch."""
    city_group = google_or_group(city_search_terms(city))
    topic_terms = _extract_topic_terms(event_name, categories or [], description)
    topic_group = google_or_group(topic_terms[:6])

    queries: list[str] = []
    if topic_group:
        queries.append(f"site:linkedin.com/in/ {city_group} {topic_group}".strip())
        queries.append(
            f"site:linkedin.com/in/ {city_group} "
            f"(founder OR engineer OR researcher OR investor) {topic_group}".strip()
        )
    else:
        queries.append(
            f"site:linkedin.com/in/ {city_group} "
            "(founder OR engineer OR researcher OR investor OR operator)"
        )
    queries.append(
        f'site:linkedin.com/in/ {city_group} ("AI" OR startup OR technology OR community)'
    )
    queries.append(
        f"site:linkedin.com/in/ {city_group} (speaker OR panelist OR conference OR meetup)"
    )
    return queries


def dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for result in results:
        profile_url = normalize_linkedin_profile_url(str(result.get("link", "")))
        if not profile_url or profile_url in seen:
            continue
        seen.add(profile_url)
        deduped.append({**result, "link": profile_url})
    return deduped


@function_tool
async def fetch_event_context(
    wrapper: RunContextWrapper[LeadFinderContext],
    event_url: str,
) -> dict[str, str]:
    """Fetch and summarize public event page text with Bright Data Web Unlocker."""
    page = wrapper.context.brightdata.unlock_url(event_url, data_format="markdown")
    context = extract_event_page_context(event_url, page)
    return {
        "url": context.url,
        "title": context.title,
        "description": context.description,
        "text": context.text,
    }


@function_tool
async def search_linkedin_profiles(
    wrapper: RunContextWrapper[LeadFinderContext],
    query: str,
    limit: int = 0,
    start: int = 0,
) -> dict[str, Any]:
    """Search Google SERP for public LinkedIn profiles using Bright Data SERP API."""
    context = wrapper.context
    if len(context.serp_queries_used) >= context.max_serp_queries:
        return {
            "error": "SERP query limit reached for this run.",
            "max_serp_queries": context.max_serp_queries,
        }

    normalized_query = ensure_linkedin_city_query(query, context.city)
    result_limit = limit or context.max_serp_results_per_query
    result_limit = max(1, min(result_limit, 100))
    start = max(0, start)

    context.serp_queries_used.append(normalized_query)
    results = context.brightdata.google_search(
        normalized_query,
        limit=result_limit,
        start=start,
    )
    records = [
        {
            "title": result.title,
            "link": result.link,
            "description": result.description,
            "rank": result.rank,
            "source_query": result.source_query,
        }
        for result in results
    ]
    return {
        "query": normalized_query,
        "limit": result_limit,
        "start": start,
        "results": dedupe_results(records),
    }


@function_tool
async def fetch_public_profile_page(
    wrapper: RunContextWrapper[LeadFinderContext],
    linkedin_url: str,
) -> dict[str, Any]:
    """Fetch a public LinkedIn profile page through Bright Data Web Unlocker."""
    context = wrapper.context
    profile_url = normalize_linkedin_profile_url(linkedin_url)
    if not profile_url:
        return {"error": "Only public linkedin.com/in profile URLs are allowed."}
    if len(context.fetched_pages) >= context.max_page_fetches:
        return {
            "error": "Profile page fetch limit reached for this run.",
            "max_page_fetches": context.max_page_fetches,
        }

    context.fetched_pages.append(profile_url)
    page = context.brightdata.unlock_url(profile_url, data_format="markdown")
    return {
        "url": profile_url,
        "content": page[:8000],
    }


def build_profile_finder_agent(model: str) -> Agent[LeadFinderContext]:
    return Agent[LeadFinderContext](
        name="LinkedIn event invite researcher",
        model=model,
        output_type=ProfileSearchReport,
        tools=[
            fetch_event_context,
            search_linkedin_profiles,
            fetch_public_profile_page,
        ],
        instructions=(
            "You find public LinkedIn profiles that may be interested in a specific "
            "event. Use the tools in this order: fetch the event context, search "
            "with broad public LinkedIn queries first, then narrow only after you "
            "have some usable results. Do not spend the whole search budget on "
            "exact niche phrases. Use city aliases such as Bay Area when relevant. "
            "When the user asks for many profiles, request larger SERP result "
            "limits per query instead of repeatedly using the default first page. "
            "Fetch public profile pages only when the SERP snippet is promising "
            "and more evidence is needed. "
            "Prefer people with explicit public evidence that matches the event "
            "topic, role, company, prior talks, projects, or community involvement. "
            "Do not infer sensitive traits. Do not claim certainty. Do not include "
            "private contact information. Do not suggest logging in, connecting, "
            "messaging, or bypassing account controls. Return a concise structured "
            "report with source queries and caveats."
        ),
    )
