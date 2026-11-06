"""LG (US).

Site: www.lg.com/us/support. Each model's support page (/us/support/product/lg-<MODEL>) is a Next.js page whose
embedded __NEXT_DATA__ JSON carries `manualData.manualList`. PDF entries have a GSFS file id (`fileName`) that
downloads from gscs-b2c.lge.com/downloadFile?fileId=<id>.

Known gaps: only the US site; HTML "online manuals" are skipped (no PDF); a model LG files under a different
customer model code (some window ACs) needs the code from the rating plate.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from ...models import Candidate, DocKind, Query
from ..base import Adapter, Context

PAGE = "https://www.lg.com/us/support/product/lg-{model}"
FILE = "https://gscs-b2c.lge.com/downloadFile?fileId={id}"

TYPES = {
    "MANUAL": DocKind.owners_manual,
    "USER": DocKind.owners_manual,
    "SPECIFICATION": DocKind.spec_sheet,
    "INSTALLATION": DocKind.install_guide,
    "QUICK": DocKind.quick_start,
    "ENERGY": DocKind.energy_guide,
    "WARRANTY": DocKind.warranty,
}

LANGS = {"english": "en", "spanish": "es", "french": "fr"}


def manual_list(html: str) -> tuple[dict, list[dict]]:
    """The support page's manual data and its list of manuals, from the page's __NEXT_DATA__ JSON."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return {}, []
    data = json.loads(m.group(1))
    md = data.get("props", {}).get("pageProps", {}).get("manualData") or {}
    return md, md.get("manualList") or []


class LG(Adapter):
    """LG's US support page and its embedded data; see the module docstring."""

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep only the manual list; see `trim`."""
        return trim(body)

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Read the model's support page and return each PDF in its manual list, on LG's file service."""
        for variant in self.variants(q):
            r = ctx.http.get(PAGE.format(model=variant.replace("/", "")))
            ctx.note(f"lg: {r.url} -> {r.status}")
            if not r.ok:
                continue
            md, rows = manual_list(r.text)
            out = []
            for x in rows:
                if (x.get("fileType") or "").upper() != "PDF" or not x.get("fileName"):
                    continue
                name = x.get("originalFileName") or ""
                lang_label = (x.get("fileNamePrint") or "").strip()
                lang = LANGS.get(lang_label.lower().split(",")[0]) if lang_label else None
                kind = TYPES.get((x.get("manualType") or "").upper())
                label = {DocKind.owners_manual: "Owner's Manual"}.get(
                    kind, (x.get("manualType") or "").title()
                )
                out.append(
                    self.candidate(
                        url=FILE.format(id=x["fileName"]),
                        title=f"LG {md.get('customerModelCode') or variant} {label} ({lang_label or '?'}) {name}",
                        kind=kind or self.kind_for(None, name),
                        language=lang,
                        models=[m for m in (md.get("customerModelCode"), md.get("csSalesCode")) if m],
                        page_url=r.url,
                        published=x.get("releaseDate"),
                        size_bytes=_size(x.get("fileSize")),
                    )
                )
            if out:
                return out
        return []


KEEP = ("manualType", "fileType", "fileName", "originalFileName", "fileSize", "fileNamePrint", "releaseDate")


def trim(body: str) -> str:
    """A support page cut down to its manual list, in the shape `manual_list` reads."""
    md, rows = manual_list(body)
    if not md:
        return body[:2000]
    small = {
        "customerModelCode": md.get("customerModelCode"),
        "csSalesCode": md.get("csSalesCode"),
        "manualList": [{k: x.get(k) for k in KEEP} for x in rows],
    }
    data = json.dumps({"props": {"pageProps": {"manualData": small}}})
    return f'<html><script id="__NEXT_DATA__" type="application/json">{data}</script></html>'


def _size(s: str | None) -> int | None:
    m = re.match(r"(\d+)\s*K", s or "")
    return int(m.group(1)) * 1024 if m else None
