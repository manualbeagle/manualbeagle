"""Samsung (US).

Site: www.samsung.com/us/support. The support page's own script reads /us/api/support/product/detail/<MODEL>.json,
whose `manualsAndDownloads` groups documents by kind (UserManual, QuickStartGuide, InstallationGuide, Legal...)
and then by language; each entry has a `downloadUrl` on downloadcenter.samsung.com. That path is disallowed in
robots.txt, which doesn't apply to a lookup (see CLAUDE.md, "robots.txt doesn't apply to lookups").

Known gaps: the US site only; the API wants Samsung's full model code (QN65QN85AAFXZA, HW-Q800A/ZA), so a short
name like "QN85A" finds nothing and falls back to web search.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from urllib.parse import quote

from ...models import Candidate, DocKind, Query
from ..base import Adapter, Context

DETAIL = "https://www.samsung.com/us/api/support/product/detail/{model}.json"

SECTIONS = {
    "UserManual": DocKind.owners_manual,
    "manuals": None,  # a merge of the others: kind comes from each entry's own type
    "QuickStartGuide": DocKind.quick_start,
    "InstallationGuide": DocKind.install_guide,
    "FlashManual": DocKind.owners_manual,
    "Legal": DocKind.warranty,
}
TYPES = {
    "user manual": DocKind.owners_manual,
    "quick guide": DocKind.quick_start,
    "installation guide": DocKind.install_guide,
    "warranty": DocKind.warranty,
    "legal": DocKind.warranty,
}
LANGS = {"EN": "en", "ES": "es", "FR": "fr", "MULTI": "multi"}
KEEP = ("fileName", "type", "languageCode", "fileSize", "version", "stdFileExtension", "downloadUrl")


def entries(data: dict) -> Iterable[tuple[str, dict]]:
    """(section, entry) for every document in the product detail's manuals-and-downloads sections."""
    md = data.get("manualsAndDownloads") or {}
    for section in SECTIONS:
        block = md.get(section)
        if isinstance(block, dict):
            for rows in block.values():
                if isinstance(rows, list):
                    for x in rows:
                        yield section, x
        elif isinstance(block, list):
            for x in block:
                yield section, x


class Samsung(Adapter):
    """Samsung's US product-detail JSON; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Read the model's product detail and return each PDF in its manuals and downloads."""
        for variant in self.variants(q):
            r = ctx.http.get(DETAIL.format(model=quote(variant, safe="")))
            ctx.note(f"samsung: {r.url} -> {r.status}")
            if not r.ok or not r.text.strip().startswith(("{", "[")):
                continue
            data = r.json()
            data = data[0] if isinstance(data, list) and data else data
            if not isinstance(data, dict) or not data.get("modelCode"):
                continue
            out = []
            for section, x in entries(data):
                url = x.get("downloadUrl")
                if not url or (x.get("stdFileExtension") or "").lower() != "pdf":
                    continue
                kind = TYPES.get((x.get("type") or "").lower()) or SECTIONS.get(section)
                lang = LANGS.get((x.get("languageCode") or "").upper())
                out.append(
                    self.candidate(
                        url=url,
                        title=f"Samsung {data.get('modelCode')} {x.get('fileName') or ''} ({x.get('type') or section}, "
                        f"{x.get('languageCode') or '?'})",
                        kind=kind or self.kind_for(None, x.get("fileName") or "", url),
                        language=lang,
                        models=[data.get("modelCode"), data.get("modelName") or ""],
                        page_url=f"https://www.samsung.com/us/support/downloads/?model={quote(variant, safe='')}",
                        published=x.get("version"),
                    )
                )
            if out:
                return out
        return []

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep the model code and name and the documents' fields."""
        try:
            data = json.loads(body)
        except ValueError:
            return body[:2000]
        data = data[0] if isinstance(data, list) and data else data
        if not isinstance(data, dict):
            return body[:2000]
        md = {}
        for section in SECTIONS:
            block = (data.get("manualsAndDownloads") or {}).get(section)
            if isinstance(block, dict):
                md[section] = {
                    lang: [{k: x.get(k) for k in KEEP} for x in rows]
                    for lang, rows in block.items()
                    if isinstance(rows, list)
                }
        return json.dumps(
            {
                "modelCode": data.get("modelCode"),
                "modelName": data.get("modelName"),
                "manualsAndDownloads": md,
            }
        )
