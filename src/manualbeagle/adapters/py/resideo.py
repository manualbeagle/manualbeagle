r"""Resideo / Honeywell Home (US): thermostats, sensors, water and air products.

Site: www.honeywellhome.com, a Shopify store. Its predictive search (/search/suggest.json, told to look in variant
SKUs and descriptions too) turns a model such as RCHT9610WF into product handles; each product page
(/products/<handle>) lists its documents as resource cards linking PDFs on digitalassets.resideo.com. The page
writes those links with backslashes (damroot/Original\10015\33-00478.pdf), which the server only accepts as
forward slashes, so they are rewritten.

Known gaps: products Honeywell Home no longer sells (older RTH and TH models) often have no product page, so the
web search runs; Resideo's professional catalogue (customer.resideo.com) isn't searched; a search can also match
an accessory that names the model in its description, which ranks below the product's own documents because its
SKUs don't name the model.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ...models import Candidate, Query
from ..base import Adapter, Context

SITE = "https://www.honeywellhome.com"
SUGGEST = SITE + "/search/suggest.json"
MAX_PRODUCTS = 3
LANGS = {"english": "en", "spanish": "es", "french": "fr"}


def suggest_params(model: str) -> dict[str, str]:
    """The store's predictive-search parameters, looking in titles, SKUs and descriptions."""
    return {
        "q": model,
        "resources[type]": "product",
        "resources[limit]": "6",
        "resources[options][fields]": "title,variants.sku,variants.title,body",
    }


def product_skus(page: str, handle: str) -> list[str]:
    """The product's own variant SKUs, from the store's analytics block (the page also lists other products)."""
    m = re.search(r'"handle":"' + re.escape(handle) + r'","variants":\[(.*?)\]', page, re.S)
    if not m:
        return []
    return sorted({s.replace("\\/", "/") for s in re.findall(r'"sku":"([^"]+)"', m.group(1))})


def language(title: str) -> str | None:
    """The language from a title's trailing '(English)'; 'multi' for several, None when not given."""
    m = re.search(r"\(([^)]*)\)\s*$", title)
    if not m:
        return None
    langs = [LANGS[w] for w in re.findall(r"[a-z]+", m.group(1).lower()) if w in LANGS]
    if len(langs) == 1:
        return langs[0]
    return "multi" if langs else None


class Resideo(Adapter):
    """Honeywell Home's store search, then each product page's document cards; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Search the store for the model and read the documents of the first three products."""
        for variant in self.variants(q):
            r = ctx.http.get(SUGGEST, params=suggest_params(variant))
            ctx.note(f"resideo: {r.url} -> {r.status}")
            if not r.ok or not r.text.strip().startswith("{"):
                continue
            products = ((r.json().get("resources") or {}).get("results") or {}).get("products") or []
            out: list[Candidate] = []
            for p in products[:MAX_PRODUCTS]:
                if p.get("handle"):
                    out += self.documents(p["handle"], p.get("title") or "", ctx)
            if out:
                return out
        return []

    def documents(self, handle: str, name: str, ctx: Context) -> list[Candidate]:
        """The PDFs on a product page's resource cards, with the product's SKUs as its models."""
        page_url = f"{SITE}/products/{handle}"
        r = ctx.http.get(page_url)
        ctx.note(f"resideo: {r.url} -> {r.status}")
        if not r.ok:
            return []
        skus = product_skus(r.text, handle)
        out: list[Candidate] = []
        seen: set[str] = set()
        for a in HTMLParser(r.text).css(".resource-card a"):
            href = (a.attributes.get("href") or "").strip().replace("\\", "/")
            if ".pdf" not in href.lower() or href in seen:
                continue
            seen.add(href)
            h = a.css_first(".resource-heading")
            title = h.text(separator=" ", strip=True) if h else ""
            title = re.sub(r"^link\s+|\s+pdf$", "", title.strip()).strip()
            out.append(
                self.candidate(
                    url=href,
                    title=f"Honeywell Home {name}: {title}" if name else title,
                    kind=self.kind_for(None, title, href),
                    language=language(title),
                    models=skus,
                    page_url=page_url,
                )
            )
        return out

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep products' handles and titles, or a product page's cards and SKU block."""
        if "suggest.json" in url:
            try:
                data = json.loads(body)
            except ValueError:
                return body[:2000]
            products = ((data.get("resources") or {}).get("results") or {}).get("products") or []
            keep = [{"handle": p.get("handle"), "title": p.get("title")} for p in products]
            return json.dumps({"resources": {"results": {"products": keep}}})
        handle = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        cards = [a.html or "" for a in HTMLParser(body).css(".resource-card a")]
        cards = [
            '<div class="resource-card">' + re.sub(r"<svg.*?</svg>", "", c, flags=re.S) + "</div>"
            for c in cards
        ]
        m = re.search(r'"handle":"' + re.escape(handle) + r'","variants":\[(.*?)\]', body, re.S)
        meta = m.group(0) if m else ""
        return "<html><body><script>" + meta + "</script>\n" + "\n".join(cards) + "\n</body></html>"
