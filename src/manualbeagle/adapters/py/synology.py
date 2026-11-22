"""Synology.

Site: the download centre's API (api/support/findDownloadInfo) lists a product's documents by language.
Known gaps: English hardware documents only; DSM's own guides are left out.
Last verified: 2026-11-22.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from urllib.parse import unquote

from ...models import Candidate, Query
from ..base import Adapter, Context

API = "https://www.synology.com/api/support/findDownloadInfo"


def hardware_pdfs(body: str) -> Iterator[str]:
    """The English hardware PDFs in the API's answer, none for anything else."""
    try:
        groups = json.loads(body)["info"]["docs"]["detail"]
        files = [f for g in groups for i in g["items"] for f in i["files"]]
    except (ValueError, KeyError, TypeError):
        return
    for f in files:
        url = str(f.get("url") or "")
        if "/Document/Hardware/" in url and "/enu/" in url and url.lower().endswith(".pdf"):
            yield url


class Synology(Adapter):
    """Synology's download centre; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Return the first variant's English hardware documents."""
        for variant in self.variants(q):
            r = ctx.http.get(API, params={"lang": "en-us", "product": variant})
            ctx.note(f"{self.id}: {r.url} -> {r.status}")
            if not r.ok:
                continue
            out = []
            for url in dict.fromkeys(hardware_pdfs(r.text)):
                name = unquote(url.rsplit("/", 1)[-1])[:-4]
                title = "Synology " + name.replace("_", " ")
                kind = self.kind_for(url, title, url)
                out.append(self.candidate(url=url, title=title, kind=kind, language="en", models=[variant]))
            if out:
                return out
        return []

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep only the English hardware PDFs of the API's answer."""
        files = [{"url": u} for u in hardware_pdfs(body)]
        return json.dumps({"info": {"docs": {"detail": [{"items": [{"files": files}]}]}}})
