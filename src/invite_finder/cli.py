from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
from typing import Any

from agents import Runner
from agents.exceptions import MaxTurnsExceeded

from invite_finder.agent import (
    LeadFinderContext,
    build_profile_finder_agent,
    build_seed_search_queries,
)
from invite_finder.brightdata import BrightDataClient
from invite_finder.config import Settings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find public LinkedIn profiles for an event and city."
    )
    parser.add_argument("--event-url", required=True, help="Public event URL.")
    parser.add_argument("--city", required=True, help='Target city, e.g. "San Francisco".')
    parser.add_argument("--max-profiles", type=int, default=20)
    parser.add_argument("--max-serp-queries", type=int, default=5)
    parser.add_argument(
        "--serp-results-per-query",
        type=int,
        help=(
            "Google results to request per SERP query, up to 100. Defaults to "
            "a value derived from --max-profiles and --max-serp-queries."
        ),
    )
    parser.add_argument("--max-page-fetches", type=int, default=8)
    parser.add_argument(
        "--max-agent-turns",
        type=int,
        help=(
            "Maximum OpenAI Agents SDK loop turns. Defaults to a value derived "
            "from the SERP and page-fetch budgets."
        ),
    )
    parser.add_argument("--output", help="Optional path to write JSON report.")
    return parser.parse_args()


def _jsonable(value: Any) -> str:
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json(indent=2)
    return json.dumps(value, indent=2, default=str)


def _default_max_agent_turns(args: argparse.Namespace) -> int:
    return max(20, args.max_serp_queries + args.max_page_fetches + 8)


def _default_serp_results_per_query(args: argparse.Namespace) -> int:
    queries = max(1, args.max_serp_queries)
    needed_per_query = math.ceil(args.max_profiles / queries)
    return max(10, min(100, needed_per_query * 2))


async def run(args: argparse.Namespace) -> str:
    settings = Settings.from_env()
    brightdata = BrightDataClient(settings)
    serp_results_per_query = (
        args.serp_results_per_query or _default_serp_results_per_query(args)
    )
    serp_results_per_query = max(1, min(serp_results_per_query, 100))
    context = LeadFinderContext(
        brightdata=brightdata,
        city=args.city,
        max_profiles=args.max_profiles,
        max_serp_queries=args.max_serp_queries,
        max_serp_results_per_query=serp_results_per_query,
        max_page_fetches=args.max_page_fetches,
    )
    agent = build_profile_finder_agent(settings.openai_agent_model)
    seed_queries = build_seed_search_queries(args.city, args.event_url)
    seed_query_text = "\n".join(f"- {query}" for query in seed_queries)

    prompt = (
        f'Find up to {args.max_profiles} public LinkedIn profiles in "{args.city}" '
        f"who may be interested in this event: {args.event_url}. "
        "Use Bright Data SERP and Web Unlocker tools. Start with these broad-to-"
        "specific SERP queries before inventing narrower exact-match queries:\n"
        f"{seed_query_text}\n"
        f"For each SERP query, request up to {serp_results_per_query} results. "
        "Use public SERP snippets as evidence when profile page fetch budget is "
        "lower than the requested candidate count. "
        "If the broad queries have no usable results, report that clearly instead "
        "of fabricating candidates. Return the structured report."
    )
    max_turns = args.max_agent_turns or _default_max_agent_turns(args)
    try:
        result = await Runner.run(agent, prompt, context=context, max_turns=max_turns)
    except MaxTurnsExceeded as exc:
        payload = {
            "event_url": args.event_url,
            "city": args.city,
            "candidates": [],
            "caveats": [
                (
                    f"The agent exceeded max_agent_turns={max_turns} before it "
                    "could finish the report."
                ),
                (
                    "Increase --max-agent-turns, lower --max-serp-queries or "
                    "--max-page-fetches, or narrow the city/event criteria."
                ),
                f"SDK error: {exc}",
            ],
        }
        return json.dumps(payload, indent=2)
    return _jsonable(result.final_output)


def main() -> None:
    args = _parse_args()
    output = asyncio.run(run(args))

    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n", encoding="utf-8")
        print(f"Wrote {path}")
        return

    print(output)


if __name__ == "__main__":
    main()
