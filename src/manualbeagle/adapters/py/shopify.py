"""Makers that sell from their own Shopify store and link manuals in the product description (WEN, ...).

Site: the store's public product search, <store>/search/suggest.json?q=<model>&resources[type]=product, which
returns each matching product with its description HTML. A product counts when its title names the model as a
whole word; replacement parts ("[3410-HW] Hardware Bag for WEN 3410") are left out by `skip_title`. Every PDF
the description links on the store's file hosts is a candidate, titled by its link text or file name.

Options (in the adapter's YAML `options`): `store` (the store's base URL, required), `pdf_hosts` (hosts a PDF may
be on; default cdn.shopify.com), `skip_title` (a regex for product titles to leave out; default a title that
starts with "[", the usual way part listings are named).

Known gaps: a manual the description doesn't link (some stores put it in a theme block or a metafield) isn't
found; the search's own matching decides which products come back.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from html import unescape
from urllib.parse import unquote, urljoin, urlsplit

from ...models import Candidate, Query
from ..base import Adapter, Context

ANCHOR = re.compile(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
BARE_PDF = re.compile(r'https?://[^\s"\'<>]+?\.pdf', re.I)
# "Click here to view the product manual" -> "product manual"; "Download" -> "" (the file name is used instead)
CLICK_HERE = re.compile(
    r"^(click\s+)?(here\s*)?(to\s+)?(view|download|see|open)?\s*(the\s+|our\s+)?$|^click\s+here\s+to\s+(view|download|see|open)\s+(the\s+|our\s+)?",
    re.I,
)


def pdf_links(body: str, hosts: list[str]) -> list[tuple[str, str]]:
    """(url, link text) for every PDF in `body` on one of `hosts`, query strings dropped, in order, once each."""
    found: dict[str, str] = {}
    for href, text in ANCHOR.findall(body or ""):
        found.setdefault(unescape(href).split("?", 1)[0], _text(text))
    for href in BARE_PDF.findall(body or ""):
        found.setdefault(unescape(href).split("?", 1)[0], "")
    return [
        (u, t)
        for u, t in found.items()
        if u.lower().endswith(".pdf") and any(urlsplit(u).hostname == h for h in hosts)
    ]


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html))).strip()


def file_title(url: str) -> str:
    """'3410.manual.20200526.pdf' -> '3410 manual 20200526'."""
    name = unquote(url.rsplit("/", 1)[-1])[:-4]
    return re.sub(r"[._\s]+", " ", name.replace("-", " ")).strip()


class Shopify(Adapter):
    """A maker's own Shopify store, configured by `options`; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Return the PDFs linked in the descriptions of the store's products that name the model."""
        store = str(self.spec.options["store"]).rstrip("/")
        hosts = list(self.spec.options.get("pdf_hosts") or ["cdn.shopify.com"])
        skip = re.compile(self.spec.options.get("skip_title") or r"^\s*\[")
        want = re.compile(rf"(?<![A-Za-z0-9]){re.escape(q.model)}(?![A-Za-z0-9])", re.I)
        r = ctx.http.get(store + "/search/suggest.json", params={"q": q.model, "resources[type]": "product"})
        ctx.note(f"{self.id}: {r.url} -> {r.status}")
        if not r.ok or not r.text.lstrip().startswith("{"):
            return []
        products = ((r.json().get("resources") or {}).get("results") or {}).get("products") or []
        out: list[Candidate] = []
        seen: set[str] = set()
        for p in products:
            title = str(p.get("title") or "")
            if not want.search(title) or skip.search(title):
                continue
            page = urljoin(store + "/", str(p.get("url") or "").split("?", 1)[0])
            for url, text in pdf_links(str(p.get("body") or ""), hosts):
                if url in seen:
                    continue
                seen.add(url)
                label = CLICK_HERE.sub("", text).strip() or file_title(url)
                doc_title = f"{self.spec.brand} {q.model} {label}"
                out.append(
                    self.candidate(
                        url=url,
                        title=doc_title,
                        kind=self.kind_for(None, doc_title, url),
                        language="en",
                        models=[q.model],
                        page_url=page,
                    )
                )
        return out

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep each product's title, url and only the PDF links of its description."""
        try:
            data = json.loads(body)
        except ValueError:
            return body[:2000]
        hosts = list(self.spec.options.get("pdf_hosts") or ["cdn.shopify.com"])
        products = ((data.get("resources") or {}).get("results") or {}).get("products") or []
        keep = [
            {
                "title": p.get("title"),
                "url": p.get("url"),
                "body": " ".join(f'<a href="{u}">{t}</a>' for u, t in pdf_links(p.get("body") or "", hosts)),
            }
            for p in products
        ]
        return json.dumps({"resources": {"results": {"products": keep}}})
