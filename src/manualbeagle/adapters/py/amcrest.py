"""Amcrest: security cameras, video doorbells, NVRs.

Site: support.amcrest.com, a Zendesk help centre. Its public article search
(/api/v2/help_center/articles/search.json?query=<model> manual) returns each article's title, labels and body. The
documents are articles titled "User Manual (IP8M-T2499E)", "Quick Start Guide (AD410)", "Technical
Specifications (...)" and so on, whose body links the PDF on Google Drive (drive.google.com/file/d/<id>/view);
that link is turned into Drive's direct download (drive.google.com/uc?export=download&id=<id>). Only articles that
name the model (in the title or a label) and whose titles read as documents are kept.

Known gaps: www.amcrest.com's product pages answer 403 (bot protection) and aren't used; Amcrest files manuals
under the model without its colour letter (IP8M-T2499E for the -EW and -EB), handled by stripping it; a Drive file
too large for Drive's virus scan would need a confirmation page, which isn't followed.
Last verified: 2026-11-03.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from ...models import Candidate, DocKind, Query
from ...normalize import compact
from ..base import Adapter, Context

SEARCH = "https://support.amcrest.com/api/v2/help_center/articles/search.json"
DRIVE = "https://drive.google.com/uc?export=download&id={id}"
DOC_TITLE = re.compile(r"manual|guide|specification|data ?sheet|diagram", re.I)
DRIVE_FILE = re.compile(r"https://drive\.google\.com/file/d/([A-Za-z0-9_-]{10,})")
PDF_LINK = re.compile(r'href="(https?://[^"]+?\.pdf)"', re.I)


def names_model(article: dict, variants: list[str]) -> bool:
    """Whether an article's title or labels contain one of the model variants."""
    texts = [article.get("title") or "", *(article.get("label_names") or [])]
    wanted = [compact(v) for v in variants if len(compact(v)) >= 4]
    return any(w in compact(t) for t in texts for w in wanted)


class Amcrest(Adapter):
    """Amcrest's help-centre article search; see the module docstring."""

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Search articles for '<model> manual' and return the Google Drive files and PDFs they link."""
        variants = self.variants(q)
        for variant in variants:
            r = ctx.http.get(SEARCH, params={"query": f"{variant} manual", "per_page": "25"})
            ctx.note(f"amcrest: {r.url} -> {r.status}")
            if not r.ok or not r.text.strip().startswith("{"):
                continue
            out: list[Candidate] = []
            for a in r.json().get("results") or []:
                title = (a.get("title") or "").strip()
                if not DOC_TITLE.search(title) or not names_model(a, variants):
                    continue
                body = a.get("body") or ""
                links = [DRIVE.format(id=m.group(1)) for m in DRIVE_FILE.finditer(body)]
                links += [m.group(1) for m in PDF_LINK.finditer(body)]
                for url in dict.fromkeys(links):
                    out.append(
                        self.candidate(
                            url=url,
                            title=f"Amcrest {title}",
                            kind=self.kind_for(None, title, "") or DocKind.other,
                            models=[x for x in a.get("label_names") or [] if re.search(r"\d", x)],
                            page_url=a.get("html_url"),
                        )
                    )
            if out:
                return out
        return []

    def trim_fixture(self, url: str, body: str) -> str:
        """Keep each article's title, address, labels and only its document links."""
        try:
            data = json.loads(body)
        except ValueError:
            return body[:2000]
        keep = []
        for a in data.get("results") or []:
            links = DRIVE_FILE.findall(a.get("body") or "")
            pdfs = PDF_LINK.findall(a.get("body") or "")
            short = " ".join(f'<a href="https://drive.google.com/file/d/{i}/view">' for i in links)
            short += " ".join(f'<a href="{p}">' for p in pdfs)
            keep.append(
                {
                    "title": a.get("title"),
                    "html_url": a.get("html_url"),
                    "label_names": a.get("label_names"),
                    "body": short,
                }
            )
        return json.dumps({"results": keep})
