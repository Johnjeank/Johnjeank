"""
vinted_client.py – Thin async wrapper around the vinted-scraper library.

Usage
-----
    from vinted_client import VintedClient
    client = VintedClient(session_cookie, search_url)
    items = await client.search()   # list[VintedItem]

The search URL is parsed into query-param dict so users can paste a full
browser URL straight into SEARCH_URL.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse, parse_qs

from vinted_scraper import VintedScraper

logger = logging.getLogger(__name__)


def _parse_search_url(url: str) -> tuple[str, dict]:
    """
    Split a full Vinted search URL into (base_url, params_dict).

    e.g. "https://www.vinted.fr/catalog?search_text=nike&price_to=80"
      → ("https://www.vinted.fr", {"search_text": "nike", "price_to": "80"})
    """
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    # parse_qs returns lists; keep only first value for each key
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    # vinted-scraper always wants order_by=newest_first unless already set
    params.setdefault("order", "newest_first")
    return base, params


class VintedClient:
    def __init__(self, session_cookie: str, search_url: str) -> None:
        base_url, self._params = _parse_search_url(search_url)
        self._scraper = VintedScraper(base_url)
        # Inject the session cookie so Vinted returns real results
        self._scraper.agent.session.cookies.set(
            "_vinted_fr_session", session_cookie
        )

    async def search(self) -> list:
        """
        Run the blocking vinted-scraper call in a thread and return items.

        Returns an empty list on error so the monitor loop keeps running.
        """
        try:
            items = await asyncio.to_thread(
                self._scraper.search, self._params
            )
            return items or []
        except Exception as exc:
            logger.error("Vinted search failed: %s", exc)
            return []
