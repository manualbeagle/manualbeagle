"""The data that moves through the pipeline."""

from __future__ import annotations

from enum import StrEnum
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import BaseModel, Field, field_validator

# Query parameters that only follow the click: ad and search-engine click IDs, mailing-list IDs (and utm_*).
TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "gbraid",
    "wbraid",
    "dclid",
    "msclkid",
    "msockid",
    "srsltid",
    "yclid",
    "mc_cid",
    "mc_eid",
    "_ga",
    "_gl",
    "igshid",
    "ocid",
}
# For de-duplicating, also those that cache-bust or name the referrer (Shopify's search adds _pos, _psq, _psid,
# _ss; its CDN adds v).
_NOISE_PARAMS = TRACKING_PARAMS | {"v", "_pos", "_psq", "_psid", "_ss", "ref", "source"}


def _tracking(name: str) -> bool:
    return name.lower() in TRACKING_PARAMS or name.lower().startswith("utm_")


def strip_tracking(url: str) -> str:
    """The URL without tracking parameters (utm_*, click IDs such as Bing's msockid); otherwise unchanged."""
    u = urlparse(url)
    if not u.query:
        return url
    pairs = parse_qsl(u.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if not _tracking(k)]
    if len(kept) == len(pairs):
        return url
    return urlunparse(u._replace(query=urlencode(kept)))


class DocKind(StrEnum):
    """What a document is. Adapters map a site's own labels onto these values."""

    owners_manual = "owners_manual"
    install_guide = "install_guide"
    quick_start = "quick_start"
    parts_list = "parts_list"
    spec_sheet = "spec_sheet"
    service_manual = "service_manual"
    warranty = "warranty"
    maintenance_guide = "maintenance_guide"
    navigation_manual = "navigation_manual"
    energy_guide = "energy_guide"
    other = "other"


KIND_LABELS = {
    DocKind.owners_manual: "Owner's manual",
    DocKind.install_guide: "Installation guide",
    DocKind.quick_start: "Quick start",
    DocKind.parts_list: "Parts list",
    DocKind.spec_sheet: "Spec sheet",
    DocKind.service_manual: "Service manual",
    DocKind.warranty: "Warranty",
    DocKind.maintenance_guide: "Maintenance guide",
    DocKind.navigation_manual: "Navigation manual",
    DocKind.energy_guide: "Energy guide",
    DocKind.other: "Document",
}


class Query(BaseModel):
    """One lookup: what the user typed, plus what normalising made of it."""

    brand: str
    model: str
    year: int | None = None
    kind: DocKind | None = None  # preferred kind, if the user asked for one
    language: str = "en"
    region: str = "us"  # a country code; a URL for another country's site ranks lower

    canonical_brand: str | None = None
    variants: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    """A document that might be the right one."""

    url: str
    title: str = ""
    kind: DocKind | None = None
    language: str | None = None
    source: str = ""  # "adapter:lg", "search:searxng"
    page_url: str | None = None  # a page a person can open, when url isn't the PDF itself
    link_only: bool = False  # never download: the site's terms forbid it, or it needs a person
    models: list[str] = Field(default_factory=list)  # models the source says it covers
    published: str | None = None
    size_bytes: int | None = None
    snippet: str = ""

    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)

    @field_validator("url", "page_url")
    @classmethod
    def _no_tracking(cls, v: str | None) -> str | None:
        return strip_tracking(v) if v else v

    @property
    def host(self) -> str:
        """The URL's host name, lower case."""
        return (urlparse(self.url).hostname or "").lower()

    def key(self) -> str:
        """Identity for de-duplicating: the URL without its query string's tracking noise.

        The rest of the query stays: Google Drive's downloads all share the path /uc and differ only in `id`.
        """
        u = urlparse(self.url)
        query = sorted(
            (k, v) for k, v in parse_qsl(u.query) if not (k.lower() in _NOISE_PARAMS or _tracking(k))
        )
        base = f"{u.hostname}{u.path}".lower().rstrip("/")
        return base + ("?" + urlencode(query) if query else "")


class AdapterReport(BaseModel):
    """How one adapter's search went for one lookup: how many it found, or why it failed."""

    adapter: str
    ok: bool
    count: int = 0
    error: str | None = None
    blocked: bool = False


class FindResult(BaseModel):
    """Everything one lookup found: the normalised query, ranked candidates, and how each source did."""

    query: Query
    candidates: list[Candidate]
    adapters: list[AdapterReport] = Field(default_factory=list)
    searched: bool = False
    search_error: str | None = None

    @property
    def best(self) -> Candidate | None:
        """The top-ranked candidate, or None."""
        return self.candidates[0] if self.candidates else None
