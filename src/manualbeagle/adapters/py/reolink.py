"""Reolink: security cameras, doorbells, NVRs.

Site: reolink.com and support.reolink.com. The site's search bar calls
apis.reolink.com/v2/official-site-data-cache/common/navigate/search-bar/product/list?keyword=<model>, which gives
each product's name and key (D340W -> d340w, TrackFlex Floodlight -> trackflex-floodlight-wifi). The product's
support page, support.reolink.com/c/<key>/, embeds its "User Manual" block as JSON with a `downloadUrl` (a PDF on
reolink.com). Exact name matches are tried first, then up to two products whose names start with the model.

Known gaps: Reolink's user manuals are shared by product family (Reolink_Wireless_Camera_User_Manual.pdf covers
the D340W and the E1 Outdoor Pro), so they don't name the model; some products, such as the RLC-810A, have no
support page (Not Found) and fall back to web search; quick start guides on the download centre aren't read.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from urllib.parse import unquote, urlparse

from ...models import Candidate, Query
from ...normalize import compact
from ..base import Adapter, Context

SEARCH = "https://apis.reolink.com/v2/official-site-data-cache/common/navigate/search-bar/product/list"
SUPPORT = "https://support.reolink.com/c/{key}/"
DOC_RE = r'"title":"(?P<title>[^"]{1,120})","downloadUrl":"(?P<url>https?://[^"]+?\.pdf[^"]*)"'
MAX_PRODUCTS = 2


def pick(items: list[dict], model: str) -> list[dict]:
    """The products named exactly as the model, then those whose name starts with it; at most two."""
    want = compact(model)
    exact = [x for x in items if compact(x.get("name") or "") == want]
    starts = [x for x in items if x not in exact and compact(x.get("name") or "").startswith(want)]
    return (exact + starts)[:MAX_PRODUCTS]


class Reolink(Adapter):
    """Reolink's product search, then each product's support page; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Search products by model and read the matching products' support pages."""
        for variant in self.variants(q):
            r = ctx.http.get(SEARCH, params={"lang": "en", "country": "US", "keyword": variant, "limit": "8"})
            ctx.note(f"reolink: {r.url} -> {r.status}")
            if not r.ok or not r.text.strip().startswith("{"):
                continue
            out: list[Candidate] = []
            for item in pick(r.json().get("items") or [], variant):
                if item.get("key"):
                    out += self.documents(item["key"], item.get("name") or item["key"], ctx)
            if out:
                return out
        return []

    def documents(self, key: str, name: str, ctx: Context) -> list[Candidate]:
        """The PDFs a product's support page lists."""
        page = SUPPORT.format(key=key)
        r = ctx.http.get(page)
        ctx.note(f"reolink: {r.url} -> {r.status}")
        if not r.ok:
            return []
        out = []
        seen: set[str] = set()
        for m in re.finditer(DOC_RE, r.text):
            url = m.group("url")
            if url in seen:
                continue
            seen.add(url)
            file = unquote(urlparse(url).path.rsplit("/", 1)[-1]).removesuffix(".pdf").replace("_", " ")
            title = m.group("title")
            out.append(
                self.candidate(
                    url=url,
                    title=f"Reolink {name} {title} ({file})",
                    kind=self.kind_for(None, title, url),
                    language="multi" if "multiple-languages" in url else None,
                    models=[name],
                    page_url=page,
                )
            )
        return out

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep each product's name and key, or a support page's document entries."""
        if "support.reolink.com" in url:
            return "\n".join(m.group(0) for m in re.finditer(DOC_RE, body)) or body[:2000]
        try:
            data = json.loads(body)
        except ValueError:
            return body[:2000]
        items = [{"name": x.get("name"), "key": x.get("key")} for x in data.get("items") or []]
        return json.dumps({"items": items})
