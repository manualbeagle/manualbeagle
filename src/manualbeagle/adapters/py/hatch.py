"""Hatch: sound machines, sunrise alarms and night lights (Rest, Rest+, Restore, Grow...).

Site: help.hatch.co, a Zendesk help centre with one category per product ("Restore 2", "Rest+ 2nd gen"). Three
public API calls: the category list (to find the product's category by name), an article search limited to that
category (query "manual"), and the "Quick Start Guides for all Hatch Products" article, whose links are named by
product. Only links to PDFs are kept: *.pdf (Contentful, S3), Zendesk article attachments, and Dropbox shares
(turned into direct downloads with dl=1).

Known gaps: Hatch's model names are product names (Restore 2, Rest+ 2nd gen), matched exactly, with "+" kept
apart from the plain name; some products now have only HTML guides (the Restore 2's manual article no longer links
a PDF), so only the quick start guide is found for them; the quick start guide index is read by its article id.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from html import unescape

from ...models import Candidate, Query
from ..base import Adapter, Context

API = "https://help.hatch.co/api/v2/help_center"
CATEGORIES = API + "/en-us/categories.json"
SEARCH = API + "/articles/search.json"
QSG_ARTICLE = API + "/en-us/articles/14668645947031.json"
ANCHOR = re.compile(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def norm(name: str) -> str:
    """'Rest+ 2nd Gen' -> 'restplus2ndgen'; keeps Rest+ apart from Rest."""
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("+", "plus"))


def pdf_link(href: str) -> str | None:
    """The direct link for a PDF, article attachment or Dropbox PDF (dl=1), or None for other links."""
    href = unescape(href).strip()
    path = href.split("?", 1)[0].lower()
    if "dropbox.com" in href and path.endswith(".pdf"):
        return re.sub(r"([?&])dl=0", r"\1dl=1", href) if "dl=0" in href else href + "&dl=1"
    if path.endswith(".pdf") or "/article_attachments/" in path:
        return href
    return None


def anchors(body: str) -> list[tuple[str, str]]:
    """(href, link text) for every anchor in an article body."""
    return [
        (m.group(1), re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip())
        for m in ANCHOR.finditer(body or "")
    ]


class Hatch(Adapter):
    """Hatch's help centre: product categories and their articles; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Find the product's category by name, read its articles' PDFs, and add its quick start guide."""
        want = norm(q.model)
        out: list[Candidate] = []
        r = ctx.http.get(CATEGORIES, params={"per_page": "100"})
        ctx.note(f"hatch: {r.url} -> {r.status}")
        cats = (r.json().get("categories") or []) if r.ok and r.text.strip().startswith("{") else []
        cat = next((c for c in cats if norm(c.get("name") or "") == want), None)
        if cat:
            s = ctx.http.get(SEARCH, params={"query": "manual", "category": str(cat["id"]), "per_page": "25"})
            ctx.note(f"hatch: {s.url} -> {s.status}")
            results = (s.json().get("results") or []) if s.ok and s.text.strip().startswith("{") else []
            for a in results:
                for href, text in anchors(a.get("body")):
                    url = pdf_link(href)
                    if url:
                        title = f"Hatch {cat['name'].strip()}: {text or a.get('title')}"
                        out.append(self.doc(url, title, a.get("html_url"), cat["name"].strip()))
        g = ctx.http.get(QSG_ARTICLE)
        ctx.note(f"hatch: {g.url} -> {g.status}")
        if g.ok and g.text.strip().startswith("{"):
            article = g.json().get("article") or {}
            for href, text in anchors(article.get("body")):
                url = pdf_link(href)
                if url and norm(text) == want:
                    out.append(
                        self.doc(url, f"Hatch {text} Quick Start Guide", article.get("html_url"), text)
                    )
        return out

    def doc(self, url: str, title: str, page: str | None, name: str) -> Candidate:
        """A candidate for one of the product's documents."""
        return self.candidate(
            url=url,
            title=title,
            kind=self.kind_for(None, title, url),
            language="en",
            models=[name],
            page_url=page,
        )

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep categories' ids and names, or articles' titles, addresses and PDF links."""
        try:
            data = json.loads(body)
        except ValueError:
            return body[:2000]

        def links_only(html: str) -> str:
            return " ".join(f'<a href="{h}">{t}</a>' for h, t in anchors(html) if pdf_link(h))

        if "categories.json" in url:
            return json.dumps(
                {"categories": [{"id": c["id"], "name": c["name"]} for c in data.get("categories", [])]}
            )
        if "article" in data:
            a = data["article"]
            return json.dumps({"article": {"html_url": a.get("html_url"), "body": links_only(a.get("body"))}})
        keep = [
            {"title": a.get("title"), "html_url": a.get("html_url"), "body": links_only(a.get("body"))}
            for a in data.get("results") or []
        ]
        return json.dumps({"results": keep})
