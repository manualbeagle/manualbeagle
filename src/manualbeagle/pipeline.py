"""normalise -> adapter -> generic search -> verify and rank. Fetching is Library.fetch, in library.py."""

from __future__ import annotations

from urllib.parse import urlparse

from .adapters import Adapter, Context, Registry
from .http import Blocked, FetchError, PoliteClient
from .models import AdapterReport, Candidate, FindResult, Query
from .normalize import normalise
from .search import SearchEngine
from .verify import is_mirror, model_in, rank


def search_queries(q: Query, adapters: list[Adapter]) -> list[str]:
    """The web-search queries for a lookup, most useful first.

    Search engines suspend a SearXNG that asks too often, so search_candidates stops as soon as one query has
    found a PDF that names the model.
    """
    brand = q.canonical_brand or q.brand
    model = q.variants[0] if q.variants else q.model
    year = f"{q.year} " if q.year else ""
    out = [f'{year}{brand} "{model}" manual pdf']
    for a in adapters:
        for d in a.spec.domains[:1]:
            out.append(f"site:{d} {model} manual")
    if len(q.variants) > 1 and q.variants[1] != model:
        out.append(f"{year}{brand} {q.variants[1]} owner's manual")
    return out


def search_candidates(
    q: Query, engine: SearchEngine, http: PoliteClient, adapters: list[Adapter], max_queries: int = 2
) -> list[Candidate]:
    """Run up to `max_queries` web searches and turn their hits into candidates (non-PDFs are link-only)."""
    out: list[Candidate] = []
    variants = q.variants or [q.model]
    for text in search_queries(q, adapters)[:max_queries]:
        for h in engine.search(text, http):
            path = urlparse(h.url).path.lower()
            pdf = path.endswith(".pdf")
            out.append(
                Candidate(
                    url=h.url,
                    title=h.title,
                    snippet=h.snippet,
                    source=f"search:{engine.name}",
                    link_only=is_mirror(h.url) or not pdf,
                    page_url=None if pdf else h.url,
                )
            )
        if any(
            not c.link_only and max(model_in(c.title, variants), model_in(c.url, variants)) >= 2 for c in out
        ):
            break
    return out


def find(
    q: Query,
    registry: Registry,
    http: PoliteClient,
    engine: SearchEngine | None = None,
    always_search: bool = False,
    only_adapter: str | None = None,
) -> FindResult:
    """Look up manuals for a query: normalise, run the brand's adapters, search the web if needed, and rank.

    Adapters' errors are recorded in the result, not raised. The web search runs when no adapter found a
    document, or always with `always_search`. `only_adapter` runs one adapter by id whatever the brand.
    """
    adapters = [registry.get(only_adapter)] if only_adapter else registry.for_brand(q.brand)
    first = adapters[0] if adapters else None
    nq = normalise(
        q,
        first.strip_rules() if first else None,
        canonical=first.brand if first else None,
        vehicle=bool(first and first.spec.vehicle),
    )
    cands: list[Candidate] = []
    reports: list[AdapterReport] = []
    domains: list[str] = []
    for a in adapters:
        domains += a.spec.domains
        ctx = Context(http=http)
        try:
            found = list(a.find(nq, ctx))
            reports.append(AdapterReport(adapter=a.id, ok=True, count=len(found)))
            cands += found
        except Blocked as e:
            reports.append(AdapterReport(adapter=a.id, ok=False, error=str(e), blocked=True))
        except (FetchError, ValueError, KeyError, TypeError) as e:
            reports.append(AdapterReport(adapter=a.id, ok=False, error=f"{type(e).__name__}: {e}"))
        if a.spec.manual_page:
            from .adapters.yaml_adapter import fill, template_vars

            url = fill(a.spec.manual_page, template_vars(nq, nq.variants[0] if nq.variants else nq.model))
            cands.append(
                a.candidate(
                    url=url,
                    title=f"{a.brand} support page for {nq.model}",
                    link_only=True,
                    page_url=url,
                    source=f"page:{a.id}",
                )
            )

    result = FindResult(query=nq, candidates=[], adapters=reports)
    real = [c for c in cands if not c.source.startswith("page:")]
    if engine and (always_search or not real):
        result.searched = True
        try:
            cands += search_candidates(nq, engine, http, adapters)
        except FetchError as e:
            result.search_error = str(e)
    result.candidates = rank(cands, nq, domains)
    return result
