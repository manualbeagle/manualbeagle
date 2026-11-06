"""Epson (US): printers, scanners, projectors.

Site: two steps. epson.com/search/?text=<model> links support pages shaped /Support/.../Epson-<MODEL>/s/SPT_<id>;
the one whose slug is exactly the model is the product's. Its "Manuals and Warranty" panel links PDFs on
files.support.epson.com, each in a row with its title ("User's Guide", "Start Here", "Quick Guide and Warranty").

Known gaps: the US site only; a model whose support slug differs from its name (a series page) isn't matched.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from ...models import Candidate, Query
from ..base import Adapter, Context

SEARCH = "https://epson.com/search/"


class Epson(Adapter):
    """epson.com's search, then the product's support page; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Search epson.com for the model's support page, then read its manuals."""
        for variant in self.variants(q):
            r = ctx.http.get(SEARCH, params={"text": variant})
            if not r.ok:
                continue
            slug = re.sub(r"[^A-Za-z0-9]+", "-", variant).strip("-").lower()
            links = re.findall(r'href="(/Support/[^"]+?/s/SPT_[A-Za-z0-9]+)"', r.text)
            # "Epson-XP-6000", or with a series name in front: "Epson-WorkForce-WF-2850"
            exact = [h for h in links if re.search(rf"/Epson-(?:[A-Za-z]+-)*{re.escape(slug)}/s/", h, re.I)]
            for href in dict.fromkeys(exact):
                page = urljoin("https://epson.com", href)
                r2 = ctx.http.get(page)
                if r2.ok:
                    out = list(self.parse(r2.text, page, variant))
                    if out:
                        return out
        return []

    def parse(self, html: str, page: str, model: str) -> Iterable[Candidate]:
        """The PDFs on files.support.epson.com that a support page links, titled from their row."""
        tree = LexborHTMLParser(html)
        seen = set()
        for a in tree.css("a"):
            href = a.attributes.get("href") or ""
            if "files.support.epson.com" not in href or not href.lower().endswith(".pdf") or href in seen:
                continue
            seen.add(href)
            row = a.parent
            for _ in range(3):
                if row is not None and len(row.text(strip=True)) < 15:
                    row = row.parent
            text = " ".join((row.text(separator=" ", strip=True) if row is not None else "").split())
            title = re.split(r"\s+(?:HTML|PDF)\b", text)[0].strip() or href.rsplit("/", 1)[-1]
            spanish = "spanish" in text.lower() or "español" in text.lower()
            yield self.candidate(
                url=href,
                title=f"Epson {model} {title[:90]}",
                kind=self.kind_for(None, title, href),
                language="es" if spanish else "en",
                models=[model],
                page_url=page,
            )

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep the search page's support links, or the PDF rows of a support page."""
        if "/search/" in url:
            links = re.findall(r'href="/Support/[^"]+?/s/SPT_[A-Za-z0-9]+"', body)
            return "\n".join(f"<a {h}>x</a>" for h in dict.fromkeys(links))
        tree = LexborHTMLParser(body)
        rows = []
        for a in tree.css("a"):
            href = a.attributes.get("href") or ""
            if "files.support.epson.com" in href and href.lower().endswith(".pdf"):
                row = a.parent
                for _ in range(3):
                    if row is not None and len(row.text(strip=True)) < 15:
                        row = row.parent
                rows.append(row.html if row is not None else a.html or "")
        return "<div>" + "\n".join(rows) + "</div>"
