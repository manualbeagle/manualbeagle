"""Levoit: air purifiers, humidifiers, fans, vacuums.

Site: levoit.com/pages/user-manuals lists every product as a card, the product's name in its heading and a
download link to its manual on levoit.com/cdn/shop/files/. The page holds all cards at once (its pagination is
done in the browser), so one request finds any model. A card counts when its name contains the model with
spaces, hyphens and marks such as "®" ignored between characters ("Classic300S" matches "Classic 300S"), but not
followed by more of a model number ("Core 300S" doesn't match "Core 300S-P").

Known gaps: products are named, not numbered (LAP-C201S-AUSR is "Core 200S"), so a lookup by the rating plate's
model code finds nothing unless the code is in the name; the page is about 2 MB.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from html import unescape

from ...models import Candidate, Query
from ..base import Adapter, Context

PAGE = "https://levoit.com/pages/user-manuals"
CARD = re.compile(
    r">([^<]{3,160})</a>\s*</h3>\s*<div class=\"dtc-manual-product-card__actions\">\s*<a\s+"
    r"class=\"dtc-manual-product-card__download\"\s+href=\"([^\"]+?\.pdf)",
    re.S,
)


def model_pattern(model: str) -> re.Pattern[str]:
    """'Classic300S' -> matches 'Classic 300S' and 'Classic300S', not 'Classic 300S-P'."""
    chars = [re.escape(c) for c in re.sub(r"[^A-Za-z0-9]", "", model)]
    return re.compile(r"(?<![A-Za-z0-9])" + r"[\W_]{0,2}".join(chars) + r"(?!-?[A-Za-z0-9])", re.I)


class Levoit(Adapter):
    """Levoit's user-manuals page; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Read the user-manuals page and return the manual of every card whose name matches the model."""
        want = model_pattern(q.model)
        r = ctx.http.get(PAGE)
        ctx.note(f"levoit: {r.url} -> {r.status}")
        if not r.ok:
            return []
        out: dict[str, Candidate] = {}
        for name, href in CARD.findall(r.text):
            name = re.sub(r"\s+", " ", unescape(name)).strip()
            if not want.search(name):
                continue
            url = "https:" + href if href.startswith("//") else href
            url = url.split("?", 1)[0]
            title = name if name.lower().startswith("levoit") else f"Levoit {name}"
            out.setdefault(
                url,
                self.candidate(
                    url=url,
                    title=f"{title} user manual",
                    kind=self.kind_for("manual", title, url),
                    language="en",
                    models=[q.model],
                    page_url=PAGE,
                ),
            )
        return list(out.values())

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep only the cards, in the shape find() reads."""
        return "\n".join(
            f'>{n}</a></h3><div class="dtc-manual-product-card__actions"><a '
            f'class="dtc-manual-product-card__download" href="{h}"'
            for n, h in CARD.findall(body)
        )
