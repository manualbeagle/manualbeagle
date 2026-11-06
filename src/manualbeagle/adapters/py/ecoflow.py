"""EcoFlow (US): portable power stations, solar generators, batteries, coolers.

Site: www.ecoflow.com/us/support/download. The download centre's own script reads
api-a.ecoflow.com/website/cms/downloadInfoForWeb/downloadInfo/v2?title=<name> (headers x-appid: 1 for the US
site, systemName: website_user), which lists PDFs ("MANUAL") and online manuals ("ONLINE", pages on
manuals.ecoflow.com). The title search is a substring match, so "DELTA 2" also returns DELTA 2 Max documents; each
title names its products before the first "-" or "_" ("EcoFlow DELTA 2 Max & EcoFlow E2000-Troubleshooting"), and
only documents whose product names equal the model are kept. An online manual's page carries its PDF
(`pdfAddress`), which is fetched for at most two online manuals.

Known gaps: EcoFlow's models are product names (DELTA 2, TRAIL Plus 300 DC, RIVER 2), not the codes on the label
(EFD330 and so on), which aren't searchable; regional variants named differently (RIVER 2 230) don't match.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from ...models import Candidate, DocKind, Query
from ...normalize import compact
from ..base import Adapter, Context

API = "https://api-a.ecoflow.com/website/cms/downloadInfoForWeb/downloadInfo/v2"
HEADERS = {"x-appid": "1", "systemName": "website_user", "Accept-Language": "en-US,en;q=0.9"}
PAGE = "https://www.ecoflow.com/us/support/download/index"
MAX_ONLINE = 2
PDF_RE = r'"title":"(?P<title>[^"]*)","pdfAddress":"(?P<url>https?://[^"]+)"'


def products(title: str) -> list[str]:
    """The product names a document title starts with."""
    head = re.split(r"[-_]", title, maxsplit=1)[0]
    names = []
    for part in head.split("&"):
        name = re.sub(r"^\s*EcoFlow\s+", "", part.strip(), flags=re.I)
        name = re.sub(r"\s+Portable Power Station\s*$", "", name, flags=re.I)
        if name:
            names.append(name.strip())
    return names


def language(title: str, lang: str | None) -> str | None:
    """'multi' for a title marking several languages, else the API's language code (default en)."""
    m = re.search(r"\(([A-Z+]+)\)", title)
    if m and "+" in m.group(1) or "multilingual" in title.lower():
        return "multi"
    return (lang or "en").split("_")[0] or None


class EcoFlow(Adapter):
    """EcoFlow's US download-centre API; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Search the document API by product name; keep PDFs and follow up to two online manuals."""
        for variant in self.variants(q):
            r = ctx.http.get(API, params={"page": "1", "size": "50", "title": variant}, headers=HEADERS)
            ctx.note(f"ecoflow: {r.url} -> {r.status}")
            if not r.ok or not r.text.strip().startswith("{"):
                continue
            rows = ((r.json().get("data") or {}).get("content")) or []
            want = compact(variant)
            out: list[Candidate] = []
            online = 0
            for x in rows:
                title = (x.get("title") or "").strip()
                names = products(title)
                if want not in (compact(n) for n in names):
                    continue
                url = x.get("fileUrl") or ""
                classify = x.get("classify")
                kind = DocKind.other if x.get("subType") == 2 else self.kind_for(None, title, url)
                if classify == "MANUAL" and url:
                    out.append(self.doc(url, title, kind, language(title, x.get("lang")), names, None))
                elif classify == "ONLINE" and url and online < MAX_ONLINE:
                    online += 1
                    out += self.online(url, title, kind, names, x.get("lang"), ctx)
            if out:
                return out
        return []

    def online(self, url: str, title: str, kind, names, lang, ctx: Context) -> list[Candidate]:
        """The PDFs an online manual's page links, or the page itself as a link-only candidate."""
        r = ctx.http.get(url)
        ctx.note(f"ecoflow: {r.url} -> {r.status}")
        found = []
        if r.ok:
            for m in re.finditer(PDF_RE, r.text):
                pdf_title = f"EcoFlow {m.group('title')}"
                found.append(self.doc(m.group("url"), pdf_title, kind, language(pdf_title, lang), names, url))
        if not found:  # an HTML manual only: offer the page for a person to open
            found.append(self.doc(url, title, kind, language(title, lang), names, url, link_only=True))
        return found

    def doc(self, url, title, kind, lang, names, page_url, link_only=False) -> Candidate:
        """A candidate titled with the brand, on the download centre's page unless another is given."""
        return self.candidate(
            url=url,
            title=title if title.lower().startswith("ecoflow") else f"EcoFlow {title}",
            kind=kind,
            language=lang,
            models=names,
            page_url=page_url or PAGE,
            link_only=link_only,
        )

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep the API rows' document fields, or an online manual's PDF entries."""
        if "manuals.ecoflow.com" in url:
            return "\n".join(m.group(0) for m in re.finditer(PDF_RE, body)) or body[:2000]
        try:
            data = json.loads(body)
        except ValueError:
            return body[:2000]
        keep = ("title", "fileUrl", "classify", "lang", "type", "subType")
        rows = ((data.get("data") or {}).get("content")) or []
        return json.dumps(
            {"code": data.get("code"), "data": {"content": [{k: x.get(k) for k in keep} for x in rows]}}
        )
