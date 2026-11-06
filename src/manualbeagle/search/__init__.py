"""Web search plugins: the generic fallback when no adapter knows a brand, or an adapter finds nothing."""

from __future__ import annotations

from dataclasses import dataclass

from ..http import PoliteClient


@dataclass
class Hit:
    """One web-search result."""

    url: str
    title: str = ""
    snippet: str = ""
    engine: str = ""


class SearchEngine:
    """Base class for web-search plugins. Subclasses set `name` and implement `search`."""

    name = "none"

    def search(self, query: str, http: PoliteClient, limit: int = 20) -> list[Hit]:
        """Search the web for `query` through `http` and return at most `limit` hits."""
        raise NotImplementedError


def make_engine(kind: str | None, **opts: str) -> SearchEngine | None:
    """The configured search engine, or None for 'none'. Raises ValueError for an unknown engine."""
    if not kind or kind == "none":
        return None
    if kind == "searxng":
        from .searxng import SearXNG

        return SearXNG(opts.get("url") or "http://localhost:8080")
    raise ValueError(f"unknown search engine {kind!r}")
