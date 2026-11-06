"""Roborock: robot vacuums, wet-dry vacuums.

Site: support.roborock.com, a Zendesk help centre. Its public article search
(/api/v2/help_center/articles/search.json?query=<model> manual&locale=en-us) finds articles such as "Roborock S8
MaxV Ultra (water tank version) FCC User Manual"; the search leaves article bodies out, so each matching
article's attachment list (/api/v2/help_center/en-us/articles/<id>/attachments.json) gives the PDFs (user manual,
diagram). An article's product is its title before the version or edition ("S8 MaxV Ultra", "Qrevo Edge"), and
it must equal the model, so "Q Revo" doesn't pick up the Qrevo Edge; US (FCC) editions come first, then up to
three articles are read.

Known gaps: Roborock's model names are product names (S8 MaxV Ultra), and a model sold in several versions
(water tank, refill and drainage) returns each version's manual; the label codes (such as S8MaxVUltra-ODA or
RR-S8MU) aren't searched.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from ...models import Candidate, Query
from ...normalize import compact
from ..base import Adapter, Context

API = "https://support.roborock.com/api/v2/help_center"
SEARCH = API + "/articles/search.json"
ATTACHMENTS = API + "/en-us/articles/{id}/attachments.json"
DOC_TITLE = re.compile(r"manual|quick start|guide", re.I)
MAX_ARTICLES = 3


def product(title: str) -> str:
    """'Roborock S8 MaxV Ultra (water tank version) FCC User Manual' -> 'S8 MaxV Ultra'."""
    t = re.sub(r"^\s*Roborock\s+", "", title, flags=re.I)
    return re.split(r"\(|\b(?:FCC|CE|UKCA|User|Quick|Manual|Instruction)\b", t, maxsplit=1, flags=re.I)[
        0
    ].strip()


class Roborock(Adapter):
    """Roborock's help-centre search and article attachments; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Search '<model> manual', keep this product's articles (FCC editions first), and read their PDFs."""
        for variant in self.variants(q):
            r = ctx.http.get(
                SEARCH, params={"query": f"{variant} manual", "per_page": "25", "locale": "en-us"}
            )
            ctx.note(f"roborock: {r.url} -> {r.status}")
            if not r.ok or not r.text.strip().startswith("{"):
                continue
            want = compact(variant)
            articles = [
                a
                for a in r.json().get("results") or []
                if DOC_TITLE.search(a.get("title") or "") and compact(product(a.get("title") or "")) == want
            ]
            articles.sort(key=lambda a: "FCC" not in (a.get("title") or ""))
            out: list[Candidate] = []
            for a in articles[:MAX_ARTICLES]:
                out += self.attachments(a, ctx)
            if out:
                return out
        return []

    def attachments(self, article: dict, ctx: Context) -> list[Candidate]:
        """The PDF attachments of one article."""
        r = ctx.http.get(ATTACHMENTS.format(id=article["id"]))
        ctx.note(f"roborock: {r.url} -> {r.status}")
        if not r.ok or not r.text.strip().startswith("{"):
            return []
        title = re.sub(r"\s+", " ", article.get("title") or "").strip()
        out = []
        for x in r.json().get("article_attachments") or []:
            url, name = x.get("content_url"), x.get("display_file_name") or x.get("file_name") or ""
            if not url or x.get("content_type") != "application/pdf":
                continue
            label = name.removesuffix(".pdf")
            out.append(
                self.candidate(
                    url=url,
                    title=label,
                    kind=self.kind_for(None, label, ""),
                    language="en" if "FCC" in title else "multi",
                    models=[title],
                    page_url=article.get("html_url"),
                    size_bytes=x.get("size"),
                )
            )
        return out

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep articles' ids, titles and addresses, or attachments' URLs, names, types and sizes."""
        try:
            data = json.loads(body)
        except ValueError:
            return body[:2000]
        if "attachments.json" in url:
            keep = ("content_url", "display_file_name", "content_type", "size")
            rows = data.get("article_attachments") or []
            return json.dumps({"article_attachments": [{k: x.get(k) for k in keep} for x in rows]})
        keep = ("id", "title", "html_url")
        return json.dumps({"results": [{k: a.get(k) for k in keep} for a in data.get("results") or []]})
