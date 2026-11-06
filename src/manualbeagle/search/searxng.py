"""SearXNG through its JSON API. The API has to be turned on in SearXNG's settings.yml (search.formats: json).

SearXNG asks upstream engines (Google, Bing, Brave...) for every query, and they suspend it when it asks too often.
So searches are spaced out (`min_interval`, default 4 s), and when every engine is suspended the search raises
SearchUnavailable rather than looking like "nothing found".
"""

from __future__ import annotations

import threading
import time

from ..http import FetchError, PoliteClient
from . import Hit, SearchEngine


class SearchUnavailable(FetchError):
    """The search engine answered, but its upstream engines are suspended or failing."""


class SearXNG(SearchEngine):
    """A SearXNG instance's JSON API, with searches at least `min_interval` seconds apart."""

    name = "searxng"

    def __init__(self, base_url: str, min_interval: float = 4.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def search(self, query: str, http: PoliteClient, limit: int = 20) -> list[Hit]:
        """Search through SearXNG. Raises SearchUnavailable when its upstream engines can't answer."""
        with self._lock:
            gap = self.min_interval - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            try:
                r = http.get(
                    f"{self.base_url}/search",
                    params={"q": query, "format": "json", "categories": "general", "language": "en"},
                    check_challenge=False,
                )
            finally:
                self._last = time.monotonic()
        if r.status == 403:
            raise FetchError("SearXNG refused format=json: turn on the JSON API in its settings.yml")
        if r.status == 429:
            raise SearchUnavailable("SearXNG's limiter refused the query (HTTP 429)")
        if not r.ok:
            raise FetchError(f"SearXNG answered HTTP {r.status}")
        data = r.json()
        results = data.get("results", [])
        down = data.get("unresponsive_engines") or []
        if not results and down:
            why = "; ".join(f"{e[0]}: {e[1]}" for e in down if isinstance(e, list) and len(e) == 2)
            raise SearchUnavailable(f"no engine answered ({why})")
        hits = [
            Hit(
                url=x.get("url", ""),
                title=x.get("title", ""),
                snippet=x.get("content", "") or "",
                engine=",".join(x.get("engines", []) or [x.get("engine", "")]),
            )
            for x in results
            if x.get("url")
        ]
        return hits[:limit]
