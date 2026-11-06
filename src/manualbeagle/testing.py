"""The adapter test harness. Each adapter's YAML lists known models and what each should find.

Offline runs replay recorded responses (tests/fixtures/<adapter>/<model>.json). Live runs hit the real site,
politely, and can re-record those fixtures.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .adapters import Adapter, Context
from .adapters.base import TestCase
from .http import Blocked, Cassette, CassetteMiss, FetchError, PoliteClient
from .models import Candidate, Query
from .normalize import compact, normalise
from .verify import rank

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def slug(s: str) -> str:
    """A string as a lower-case, hyphenated file name."""
    return re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower() or "x"


def fixture_path(adapter_id: str, case: TestCase, root: Path = FIXTURES) -> Path:
    """Where a test case's recorded responses live: <root>/<adapter id>/<model slug>[-<year>].json."""
    name = slug(case.model) + (f"-{case.year}" if case.year else "")
    return root / adapter_id / f"{name}.json"


@dataclass
class CaseResult:
    """The result of one test case, with the top candidates for a failure's report."""

    adapter: str
    model: str
    status: str  # pass | fail | blocked | skip | error | no-fixture
    message: str = ""
    top: list[Candidate] = field(default_factory=list)

    year: int | None = None

    @property
    def glyph(self) -> str:
        """The status as a glyph, so output never relies on colour."""
        return GLYPHS[self.status]

    @property
    def broken(self) -> bool:
        """Whether this result means the adapter needs attention: it failed, errored or was blocked."""
        return self.status in BROKEN

    def as_dict(self) -> dict:
        """The result as plain JSON-ready data (`adapters test --json`), with the top three candidates."""
        return {
            "adapter": self.adapter,
            "model": self.model,
            "year": self.year,
            "status": self.status,
            "message": self.message,
            "top": [
                {
                    "score": round(c.score),
                    "kind": c.kind.value if c.kind else None,
                    "title": c.title,
                    "url": c.url,
                }
                for c in self.top[:3]
            ],
        }


GLYPHS = {"pass": "✓", "fail": "✗", "blocked": "✗", "skip": "–", "error": "!", "no-fixture": "?"}
BROKEN = frozenset({"fail", "error", "blocked"})


def matches(c: Candidate, case: TestCase) -> bool:
    """Whether a candidate meets every condition of the test case's `expect` that's checked offline."""
    e = case.expect
    if e.kind and c.kind != e.kind:
        return False
    if e.url_contains and e.url_contains.lower() not in c.url.lower():
        return False
    if e.url_regex and not re.search(e.url_regex, c.url, re.I):
        return False
    if e.title_contains and compact(e.title_contains) not in compact(c.title):
        return False
    return not (e.link_only is not None and c.link_only != e.link_only)


def run_case(
    adapter: Adapter,
    case: TestCase,
    mode: str = "replay",
    fixtures: Path = FIXTURES,
    delay: float = 1.5,
    check_text: bool = False,
) -> CaseResult:
    """Run one test case in `mode` "replay" (fixtures), "live" or "record" (live, saving fixtures).

    With `check_text` in a live run, download the matching PDF and check that it contains
    `expect.text_contains`.
    """
    r = _run_case(adapter, case, mode, fixtures, delay, check_text)
    r.year = case.year
    return r


def _run_case(
    adapter: Adapter, case: TestCase, mode: str, fixtures: Path, delay: float, check_text: bool
) -> CaseResult:
    """The body of run_case(), which adds the case's year to whatever this returns."""
    if case.skip:
        return CaseResult(adapter.id, case.model, "skip", case.skip)
    path = fixture_path(adapter.id, case, fixtures)
    if mode == "replay" and not path.exists():
        return CaseResult(
            adapter.id,
            case.model,
            "no-fixture",
            f"record it: manualbeagle adapters test {adapter.id} --record",
        )
    cassette = (
        Cassette(path, mode="replay" if mode == "replay" else "record")
        if mode in ("replay", "record")
        else None
    )
    if cassette and mode == "record":
        cassette.trim = adapter.trim_fixture
    http = PoliteClient(delay=delay, cassette=cassette)
    q = normalise(
        Query(brand=adapter.brand, model=case.model, year=case.year),
        adapter.strip_rules(),
        canonical=adapter.brand,
        vehicle=adapter.spec.vehicle,
    )
    try:
        found = list(adapter.find(q, Context(http=http)))
    except Blocked as e:
        return CaseResult(adapter.id, case.model, "blocked", str(e))
    except CassetteMiss as e:
        return CaseResult(adapter.id, case.model, "error", f"{e} (re-record the fixture)")
    except (FetchError, ValueError, KeyError, TypeError) as e:
        return CaseResult(adapter.id, case.model, "error", f"{type(e).__name__}: {e}")
    finally:
        if cassette and mode == "record":
            cassette.save()

    ranked = rank(found, q, adapter.spec.domains)
    if len(ranked) < case.expect.min_results:
        http.close()
        return CaseResult(
            adapter.id,
            case.model,
            "fail",
            f"{len(ranked)} results, wanted {case.expect.min_results}",
            ranked[:5],
        )
    hit = next((c for c in ranked[:8] if matches(c, case)), None)
    if hit is None:
        http.close()
        return CaseResult(
            adapter.id, case.model, "fail", "no result in the top 8 met the expectation", ranked[:5]
        )
    msg = f"{hit.kind.value if hit.kind else '?'}: {hit.title[:60]}"
    if check_text and case.expect.text_contains and not hit.link_only:
        from .library import pdf_pages

        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "x.pdf"
            try:
                r = http.download(hit.url, dest)
                if r.content[:5] != b"%PDF-":
                    return CaseResult(adapter.id, case.model, "fail", f"not a PDF: {hit.url}", ranked[:5])
                text = "\n".join(pdf_pages(dest))
            except FetchError as e:
                return CaseResult(adapter.id, case.model, "error", str(e), ranked[:5])
            if compact(case.expect.text_contains) not in compact(text):
                return CaseResult(
                    adapter.id,
                    case.model,
                    "fail",
                    f"PDF doesn't contain {case.expect.text_contains!r}",
                    ranked[:5],
                )
            msg += " (text checked)"
    http.close()
    return CaseResult(adapter.id, case.model, "pass", msg, ranked[:5])
