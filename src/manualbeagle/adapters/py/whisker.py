"""Whisker (Litter-Robot, Feeder-Robot).

Site: www.litter-robot.com. Each robot's manual article, /support/article/<slug>-manual/ (litter-robot-5-manual,
litter-robot-4-manual), links its PDFs in every language under /manual_pdf/ (absolute on some pages, relative on
others). The slug is the model in lower case with hyphens; a Pro shares its model's manual, so "Litter-Robot 5
Pro" also tries litter-robot-5.

Known gaps: the model has to be Whisker's product name (Litter-Robot 4, not LR4); accessory installation guides
aren't read.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urljoin

from ...models import Candidate, DocKind, Query
from ..base import Adapter, Context

ARTICLE = "https://www.litter-robot.com/support/article/{slug}-manual/"
PDF_RE = re.compile(
    r'href="(?P<url>(?:https?://(?:www\.)?litter-robot\.com)?/manual_pdf/(?P<file>[^"/]+?)\.pdf)"', re.I
)
LANG_WORDS = {
    "English": "en",
    "Espanol": "es",
    "Francais": "fr",
    "Deutsch": "de",
    "Italiano": "it",
    "Korean": "ko",
    "Portugues": "pt",
    "norsk": "no",
    "svenska": "sv",
    "suomi": "fi",
    "Dansk": "da",
    "Nederlands": "nl",
    "Elliniko": "el",
}


def slugs(model: str) -> list[str]:
    """The manual article's slug for a model, and without '-pro' (a Pro uses its model's manual)."""
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    out = [slug]
    base = re.sub(r"-pro$", "", slug)
    if base != slug:
        out.append(base)
    return out


def language(file: str) -> str | None:
    """The language code from a manual's file name (Litter-Robot-5-Elliniko-encheiridio -> el); English if none."""
    for word, code in LANG_WORDS.items():
        if word.lower() in file.lower():
            return code
    return "en"


class Whisker(Adapter):
    """The manual articles on litter-robot.com; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Read the model's manual article and return the PDFs it links."""
        for slug in slugs(q.model):
            page = ARTICLE.format(slug=slug)
            r = ctx.http.get(page)
            ctx.note(f"whisker: {r.url} -> {r.status}")
            if not r.ok:
                continue
            out: dict[str, Candidate] = {}
            for m in PDF_RE.finditer(r.text):
                url = urljoin("https://litter-robot.com/", m.group("url"))
                if url not in out:
                    file = m.group("file")
                    out[url] = self.candidate(
                        url=url,
                        title=f"Whisker {file.replace('-', ' ')}",
                        kind=DocKind.owners_manual,
                        language=language(file),
                        models=[slug.replace("-", " ")],
                        page_url=page,
                    )
            if out:
                return list(out.values())
        return []

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep only the PDF links."""
        return "\n".join(m.group(0) for m in PDF_RE.finditer(body)) or body[:2000]
