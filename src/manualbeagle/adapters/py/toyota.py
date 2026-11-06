"""Toyota (US) vehicles.

Site: www.toyota.com/owners/warranty-owners-manuals. The page's script reads
/service/tcom/cs/manuals/downloads/<model>/<year>, JSON with ownerManuals, navigationManuals (multimedia),
warrantyGuides (warranty and maintenance), omotaOwnersManualOverTheAir and a driversExcerpts PDF link. PDFs are on
assets.sipb.toyota.com and delivery.tcom.assetscs.toyota.com.

The model is the vehicle's name ("Sienna", "bZ4X", "RAV4 Hybrid") and the year is required: `find Toyota "2022
Sienna"` or `--year 2022`. Toyota files hybrids as "<model> hv" (an all-hybrid Sienna from 2021 is "sienna hv"),
so that's tried when the plain name finds nothing.

Known gaps: Lexus isn't covered; one year can list several manuals (production-date splits such as "through
Sep. 2022 Prod."), and the title is the only way to tell them apart.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import quote

from ...models import Candidate, DocKind, Query
from ..base import Adapter, Context

API = "https://www.toyota.com/service/tcom/cs/manuals/downloads/{model}/{year}"
PAGE = "https://www.toyota.com/owners/warranty-owners-manuals/vehicle/{model}/{year}"

GROUPS = {
    "ownerManuals": DocKind.owners_manual,
    "omotaOwnersManualOverTheAir": DocKind.owners_manual,
    "navigationManuals": DocKind.navigation_manual,
    "warrantyGuides": DocKind.maintenance_guide,
}


def model_names(model: str) -> list[str]:
    """The names Toyota's API may use for a model, most likely first ('rav4 hybrid', 'rav4 hv', 'rav4')."""
    m = re.sub(r"\s+", " ", model.strip().lower())
    base = re.sub(r"\s*(hybrid|hv|prime|plug-in hybrid)\s*$", "", m)
    names = [m]
    if "hybrid" in m or m.endswith(" hv"):
        names.append(f"{base} hv")
    names += [base, f"{base} hv", base.replace(" ", "")]
    out: list[str] = []
    for n in names:
        if n and n not in out:
            out.append(n)
    return out


class Toyota(Adapter):
    """Toyota's owner's-manuals API; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Try each name for the model and year until one returns an owner's manual. Needs `q.year`."""
        if not q.year:
            ctx.note("toyota: a model year is needed (e.g. 2022 Sienna)")
            return []
        for name in model_names(q.model):
            r = ctx.http.get(API.format(model=quote(name, safe=""), year=q.year))
            ctx.note(f"toyota: {r.url} -> {r.status}")
            if not r.ok:
                continue
            data = (r.json() or {}).get("data") or {}
            out = list(self._candidates(data, q, name))
            if any(c.kind == DocKind.owners_manual for c in out):
                return out
        return []

    def _candidates(self, data: dict, q: Query, name: str) -> Iterable[Candidate]:
        page = PAGE.format(model=quote(name.replace(" ", ""), safe=""), year=q.year)
        for group, group_kind in GROUPS.items():
            for pub in data.get(group) or []:
                kind = group_kind
                for doc in pub.get("documents") or []:
                    if (doc.get("format") or "").upper() != "PDF" or not doc.get("contentLink"):
                        continue
                    title = doc.get("title") or pub.get("pubTitle") or ""
                    low = title.lower()
                    if "quick" in low:
                        kind = DocKind.quick_start
                    elif "pamphlet" in low or "consumer information" in low:
                        kind = DocKind.other
                    yield self.candidate(
                        url=doc["contentLink"],
                        title=title,
                        kind=kind,
                        language=pub.get("language"),
                        models=[f"{q.year} {q.model}"],
                        page_url=page,
                    )
        if isinstance(data.get("driversExcerpts"), str):
            yield self.candidate(
                url=data["driversExcerpts"],
                title=f"Toyota {q.year} {q.model} Driving Support Systems excerpt",
                kind=DocKind.other,
                language="en",
                page_url=page,
            )
