"""Mitsubishi Electric heating and cooling (US): MyLinkDrive, the maker's document library.

Site: www.mylinkdrive.com. Two JSON calls: /api/v0/product/search?searchText=<model> gives product slugs, then
/api/v0/product?slugName=<slug> lists documents by section (Operation, Installation, Parts, Service,
Submittals...). Each document's `srcUrl` is served through /viewPdf?srcUrl=<storage base>/<srcUrl>.

Known gaps: HVAC only (not Mitsubishi Electric's other divisions, not Mitsubishi Motors); Spanish and English
copies are both listed, and the ranking prefers English.
Last verified: 2026-11-03.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote

from ...models import Candidate, DocKind, Query
from ..base import Adapter, Context

API = "https://www.mylinkdrive.com/api/v0"
STORE = "http://enter.mehvac.com.s3.amazonaws.com/DAMRoot/"
VIEW = "https://www.mylinkdrive.com/viewPdf?srcUrl="

SECTIONS = {
    "Operation": DocKind.owners_manual,
    "Installation": DocKind.install_guide,
    "Parts": DocKind.parts_list,
    "Service": DocKind.service_manual,
    "Submittals": DocKind.spec_sheet,
    "Warranty": DocKind.warranty,
}
SKIP_SECTIONS = {"Engineering", "Marketing", "Compatibility", "Videos", "Images"}


class MitsubishiElectric(Adapter):
    """MyLinkDrive's product API; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Search products by model, exact match first, and read the first three's documents."""
        for variant in self.variants(q):
            r = ctx.http.get(f"{API}/product/search", params={"searchText": variant})
            if not r.ok:
                continue
            products = r.json().get("products") or []
            # Exact model first; a search for MSZ-FH12NA can also return MSZ-FH12NA2.
            products.sort(key=lambda p: (p.get("name", "").upper() != variant.upper(), p.get("name", "")))
            out: list[Candidate] = []
            for p in products[:3]:
                out += self.documents(p["slugName"], p.get("name", ""), ctx)
            if out:
                return out
        return []

    def documents(self, slug: str, name: str, ctx: Context) -> list[Candidate]:
        """The PDFs a product lists, with the kind taken from the section they're in."""
        r = ctx.http.get(f"{API}/product", params={"slugName": slug})
        if not r.ok:
            return []
        details = r.json().get("productDetails") or {}
        out = []
        for section, block in (details.get("contextListing") or {}).items():
            if section in SKIP_SECTIONS:
                continue
            for d in (block or {}).get("data") or []:
                src = d.get("srcUrl") or ""
                if not src.lower().endswith(".pdf"):
                    continue
                title = d.get("name") or src.rsplit("\\", 1)[-1]
                lang = "es" if ("spanish" in title.lower() or "-es_" in src.lower()) else "en"
                out.append(
                    self.candidate(
                        url=VIEW + STORE + quote(src, safe="/\\"),
                        title=f"Mitsubishi Electric {name}: {title}",
                        kind=SECTIONS.get(section) or self.kind_for(None, title, src),
                        language=lang,
                        models=[name],
                        page_url=f"https://www.mylinkdrive.com/USA/Products/{slug}",
                    )
                )
        return out
