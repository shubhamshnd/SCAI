"""Web search tool (design.md Section 4, item 1) — general fallback for anything not covered by
a dedicated API (regulation changes, news, one-off supplier lookups), and the fallback path for
price categories with no dedicated fetcher (scai/tools/pricing.py).

Uses Tavily (https://tavily.com) — built for LLM agents, returns clean structured snippets
instead of raw HTML, has a free tier. Needs TAVILY_API_KEY in .env.
"""

from __future__ import annotations

import os

import requests

import scai.config  # noqa: F401 - import loads .env, which is where TAVILY_API_KEY lives

_TAVILY_URL = "https://api.tavily.com/search"


class ToolUnavailableError(Exception):
    pass


def search(query: str, max_results: int = 5) -> list[dict]:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise ToolUnavailableError(
            "No TAVILY_API_KEY set in .env — get a free key at https://tavily.com and add it "
            "to enable web search."
        )

    resp = requests.post(
        _TAVILY_URL,
        json={"api_key": api_key, "query": query, "max_results": max_results, "search_depth": "basic"},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    return [
        {"title": r["title"], "url": r["url"], "snippet": r["content"]}
        for r in payload.get("results", [])
    ]
