"""Makers whose support site is a Zendesk help centre that links manuals from its articles (SPAN, ...).

Site: the help centre's public article search, <base>/api/v2/help_center/articles/search.json?query=<query>,
which returns each article's title, address and body HTML. Every link in those bodies to a document is a
candidate when its link text names the model as a whole word, or matches the adapter's `also` regex (for a
document that covers every model, such as SPAN's "Panel Owner's Manual"). A document link is a PDF, a Zendesk
article attachment, a Google Drive file (turned into Drive's direct download) or a Dropbox PDF (dl=1).

Options (in the adapter's YAML `options`): `base` (the help centre's root URL, required), `query` (default
"{model} manual"), `also` (a regex for link texts to keep whatever model they name), `locale` (default en-us).

Known gaps: a manual that's an HTML article rather than a linked file isn't found; a Drive file too large for
Drive's virus scan needs a confirmation page, which isn't followed.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from html import unescape

from ...models import Candidate, Query
from ..base import Adapter, Context

ANCHOR = re.compile(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
DRIVE_FILE = re.compile(r"https://drive\.google\.com/file/d/([A-Za-z0-9_-]{10,})")
DRIVE = "https://drive.google.com/uc?export=download&id={id}"


def document_url(href: str) -> str | None:
    """The direct download for a link to a document, or None for any other link."""
    href = unescape(href).strip()
    m = DRIVE_FILE.match(href)
    if m:
        return DRIVE.format(id=m.group(1))
    path = href.split("?", 1)[0].lower()
    if "dropbox.com" in href and path.endswith(".pdf"):
        return (
            re.sub(r"([?&])dl=0", r"\1dl=1", href)
            if "dl=0" in href
            else href + ("&" if "?" in href else "?") + "dl=1"
        )
    if path.endswith(".pdf") or "/article_attachments/" in path:
        return href
    return None


def links(body: str) -> list[tuple[str, str]]:
    """(href, link text) for every anchor in an article body."""
    return [
        (h, re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", t))).strip())
        for h, t in ANCHOR.findall(body or "")
    ]


class Zendesk(Adapter):
    """A maker's Zendesk help centre, configured by `options`; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Return the documents linked from articles whose link text names the model (or matches `also`)."""
        opts = self.spec.options
        base = str(opts["base"]).rstrip("/")
        query = str(opts.get("query") or "{model} manual").format(model=q.model)
        also = re.compile(opts["also"], re.I) if opts.get("also") else None
        want = re.compile(rf"(?<![A-Za-z0-9]){re.escape(q.model)}(?![A-Za-z0-9])", re.I)
        r = ctx.http.get(
            base + "/api/v2/help_center/articles/search.json",
            params={"query": query, "locale": opts.get("locale") or "en-us", "per_page": "25"},
        )
        ctx.note(f"{self.id}: {r.url} -> {r.status}")
        if not r.ok or not r.text.lstrip().startswith("{"):
            return []
        out: dict[str, Candidate] = {}
        for article in r.json().get("results") or []:
            for href, text in links(article.get("body")):
                url = document_url(href)
                if not url or url in out or not (want.search(text) or (also and also.search(text))):
                    continue
                title = text if self.spec.brand.lower() in text.lower() else f"{self.spec.brand} {text}"
                out[url] = self.candidate(
                    url=url,
                    title=title,
                    kind=self.kind_for(None, title, url),
                    language="en",
                    models=[q.model] if want.search(text) else [],
                    page_url=article.get("html_url"),
                )
        return list(out.values())

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep each article's title, address and only its document links."""
        try:
            data = json.loads(body)
        except ValueError:
            return body[:2000]
        keep = [
            {
                "title": a.get("title"),
                "html_url": a.get("html_url"),
                "body": " ".join(
                    f'<a href="{h}">{t}</a>' for h, t in links(a.get("body")) if document_url(h)
                ),
            }
            for a in data.get("results") or []
        ]
        return json.dumps({"results": keep})
