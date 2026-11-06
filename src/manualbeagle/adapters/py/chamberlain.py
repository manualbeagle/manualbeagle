"""Chamberlain (US): garage door openers, gate operators, myQ accessories.

Site: www.chamberlain.com. Its search page (/search?q=<model>) carries the Coveo organisation and an anonymous,
short-lived search token (data-coveo-search-token) that the page's own script uses; the adapter does the same:
one GET for the token, then one POST to https://<org>.org.coveo.com/rest/search/v2 limited to documents
(aq @commonsource==document). Results are PDFs on cgi.widen.net (Chamberlain's asset library) titled like
"Chamberlain B980 Owner's Manual - Spanish" or "Chamberlain B353, ..., B970, ... Installation Manual - English".

Known gaps: the token's own filter leaves out discontinued products, so older openers (and some English manuals,
such as the B970 owner's manual) aren't found and fall back to web search; LiftMaster and Merlin, Chamberlain
Group's other brands, have their own sites and aren't covered here.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from ...models import Candidate, Query
from ...normalize import compact
from ..base import Adapter, Context

PAGE = "https://www.chamberlain.com/search"
SEARCH = "https://{org}.org.coveo.com/rest/search/v2"
TOKEN_RE = r'data-coveo-search-token="(?P<token>[^"]+)"'
ORG_RE = r'data-coveo-organization-id="(?P<org>[a-z0-9]+)"'
LANGS = {"english": "en", "spanish": "es", "french": "fr"}


def language(title: str, raw: dict) -> str | None:
    """The document's language from its title (' - English') or Coveo's field; 'multi' for several."""
    m = re.search(r" - ([A-Za-z, ]+?)(?: \(|$)", title)
    words = re.findall(r"[a-z]+", m.group(1).lower()) if m else [w.lower() for w in raw.get("language") or []]
    langs = [LANGS[w] for w in words if w in LANGS]
    if len(langs) == 1:
        return langs[0]
    return "multi" if langs else None


def models_in(title: str) -> list[str]:
    """'Chamberlain B353, B353C, B970 Installation Manual' -> ['B353', 'B353C', 'B970']."""
    return re.findall(r"\b[A-Z]{1,4}\d{2,5}[A-Z0-9-]*\b", title)


class Chamberlain(Adapter):
    """Chamberlain's own Coveo search, limited to documents; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Read the search page's Coveo token, then search documents and keep PDFs naming the model."""
        for variant in self.variants(q):
            page = ctx.http.get(PAGE, params={"q": variant})
            ctx.note(f"chamberlain: {page.url} -> {page.status}")
            tok, org = re.search(TOKEN_RE, page.text or ""), re.search(ORG_RE, page.text or "")
            if not page.ok or not tok or not org:
                continue
            r = ctx.http.request(
                "POST",
                SEARCH.format(org=org.group("org")),
                json_body={"q": variant, "aq": "@commonsource==document", "numberOfResults": 20},
                headers={"Authorization": f"Bearer {tok.group('token')}"},
            )
            ctx.note(f"chamberlain: coveo search -> {r.status}")
            if not r.ok or not r.text.strip().startswith("{"):
                continue
            want = compact(variant)
            out = []
            for x in r.json().get("results") or []:
                url, title, raw = x.get("clickUri") or "", (x.get("title") or "").strip(), x.get("raw") or {}
                if ".pdf" not in url.lower():
                    continue
                models = models_in(title)
                if models and want not in (compact(m) for m in models) and want not in compact(title):
                    continue
                out.append(
                    self.candidate(
                        url=url,
                        title=title,
                        kind=self.kind_for(None, title, url),
                        language=language(title, raw),
                        models=models,
                        page_url=f"{PAGE}?q={variant}",
                    )
                )
            if out:
                return out
        return []

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep the Coveo organisation (not the short-lived token), or results' titles, URLs and languages."""
        if "chamberlain.com/search" in url:
            org = re.search(ORG_RE, body)
            # The token is short-lived and only sent in a header, which replay doesn't match on.
            return f'<div data-coveo-search-token="TOKEN" data-coveo-organization-id="{org.group("org") if org else ""}">'
        try:
            data = json.loads(body)
        except ValueError:
            return body[:2000]
        keep = [
            {
                "title": x.get("title"),
                "clickUri": x.get("clickUri"),
                "raw": {"language": (x.get("raw") or {}).get("language")},
            }
            for x in data.get("results") or []
        ]
        return json.dumps({"totalCount": data.get("totalCount"), "results": keep})
