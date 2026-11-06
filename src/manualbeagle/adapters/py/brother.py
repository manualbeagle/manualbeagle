"""Brother (US): printers, label makers.

Site: two steps. www.brother-usa.com/product-support/<MODEL> (model without dashes) links the product's support
pages with Brother's internal product key (`prod=hll2460dw_us_as`). support.brother.com/g/b/manualtop.aspx?c=us&
lang=en&prod=<key> then lists the manuals, each a PDF on download.brother.com with its title in the same row.

Known gaps: the full user's guide is often an online HTML guide, not a PDF, so the quick setup guide may be the best
PDF; generic guides shared by every model (BRAdmin, driver and licensing documents) are left out.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from selectolax.lexbor import LexborHTMLParser

from ...models import Candidate, Query
from ..base import Adapter, Context

PRODUCT = "https://www.brother-usa.com/product-support/{model}"
MANUALS = "https://support.brother.com/g/b/manualtop.aspx?c=us&lang=en&prod={key}"
GENERIC = re.compile(
    r"BRAdmin|Mass Deployment|Print Support App|Universal Printer Driver|Open Source|Channels Supported|"
    r"Security Information|License",
    re.I,
)


class Brother(Adapter):
    """brother-usa.com's product page, then support.brother.com's manuals page; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Find the product key on the product page, then read the manuals page's PDFs."""
        for variant in self.variants(q):
            code = re.sub(r"[^A-Z0-9]", "", variant.upper())
            r = ctx.http.get(PRODUCT.format(model=code))
            if not r.ok:
                continue
            m = re.search(r"manualtop\.aspx\?[^\"']*?prod=([a-z0-9_]+)", r.text)
            if not m:
                continue
            key = m.group(1)
            page = MANUALS.format(key=key)
            r2 = ctx.http.get(page)
            if not r2.ok:
                continue
            out = list(self.parse(r2.text, page, code))
            if out:
                return out
        return []

    def parse(self, html: str, page: str, code: str) -> Iterable[Candidate]:
        """The PDFs listed on a support.brother.com manuals page, leaving out generic documents."""
        tree = LexborHTMLParser(html)
        seen = set()
        for a in tree.css("a"):
            href = a.attributes.get("href") or ""
            if not href.lower().endswith(".pdf") or href in seen:
                continue
            seen.add(href)
            row = a.parent.parent if a.parent and a.parent.parent else a
            text = " ".join(row.text(separator=" ", strip=True).split())
            title = re.split(r"\s{1,}\d{2}/\d{2}/\d{4}", text)[0].strip() or href.rsplit("/", 1)[-1]
            if GENERIC.search(title):
                continue
            yield self.candidate(
                url=href,
                title=f"Brother {code} {title[:100]}",
                kind=self.kind_for(None, title, href),
                language="en" if "_eng_" in href or "/cv_" in href or "_e.pdf" in href else None,
                models=[code],
                page_url=page,
            )

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep the manuals link from the product page, or the PDF rows of the manuals page."""
        if "brother-usa.com" in url:
            m = re.search(r"manualtop\.aspx\?[^\"']*?prod=[a-z0-9_]+", body)
            return f'<a href="https://support.brother.com/g/b/{m.group(0)}">manuals</a>' if m else body[:500]
        tree = LexborHTMLParser(body)
        rows = []
        for a in tree.css("a"):
            if (a.attributes.get("href") or "").lower().endswith(".pdf"):
                row = a.parent.parent if a.parent and a.parent.parent else a
                rows.append(row.html or "")
        return "<div>" + "\n".join(rows) + "</div>"
