"""
search_client.py
-----------------
Thin wrapper around Google's Custom Search JSON API. This is what supplies
"live web data" -- the actual grounding evidence Claude reasons over in the
verification stage, instead of relying on its own (possibly stale) memory.
"""

from __future__ import annotations

from urllib.parse import urlparse

import requests

from src.config import settings
from src.models import Evidence

_ENDPOINT = "https://www.googleapis.com/customsearch/v1"


class SearchError(Exception):
    pass


def web_search(query: str, num_results: int | None = None) -> list[Evidence]:
    """Run a live Google search and return parsed Evidence objects.

    Returns an empty list (rather than raising) on a zero-result search --
    that's a legitimate outcome, not a system error. Raises SearchError
    only for actual API/network failures, since the caller needs to
    distinguish "no evidence found" from "search is broken."
    """
    if not settings.GOOGLE_API_KEY or not settings.GOOGLE_CSE_ID:
        raise SearchError("Google Search API is not configured.")

    params = {
        "key": settings.GOOGLE_API_KEY,
        "cx": settings.GOOGLE_CSE_ID,
        "q": query,
        "num": min(num_results or settings.SEARCH_RESULTS_PER_CLAIM, 10),
    }

    try:
        resp = requests.get(_ENDPOINT, params=params, timeout=settings.REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise SearchError(f"Network error while searching: {e}") from e

    if resp.status_code == 429:
        raise SearchError("Google Search API rate limit / daily quota exceeded.")
    if resp.status_code == 403:
        raise SearchError("Google Search API key invalid or quota exhausted.")
    if not resp.ok:
        raise SearchError(f"Google Search API returned HTTP {resp.status_code}.")

    data = resp.json()
    items = data.get("items", [])

    results: list[Evidence] = []
    for item in items:
        link = item.get("link", "")
        domain = urlparse(link).netloc.replace("www.", "")
        results.append(
            Evidence(
                title=item.get("title", "").strip(),
                url=link,
                snippet=item.get("snippet", "").strip(),
                source_domain=domain,
            )
        )
    return results
