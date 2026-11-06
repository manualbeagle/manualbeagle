"""What every adapter is: metadata and tests from YAML, finding from YAML rules or a Python class."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..http import PoliteClient
from ..models import Candidate, DocKind, Query


class Expect(BaseModel):
    """What a test case's result must look like. A result in the top eight must meet every field that is set.

    Attributes:
        kind: the document kind.
        url_contains: a case-insensitive substring of the URL.
        url_regex: a regex the URL must match (case-insensitive).
        title_contains: a substring of the title, ignoring case and separators.
        text_contains: text the downloaded PDF must contain; checked only in live runs with --check-text.
        min_results: the fewest results the adapter must return.
        link_only: whether the result must (True) or must not (False) be link-only.
    """

    kind: DocKind | None = None
    url_contains: str | None = None
    url_regex: str | None = None
    title_contains: str | None = None
    text_contains: str | None = None  # checked only in live runs, against the downloaded PDF
    min_results: int = 1
    link_only: bool | None = None


class TestCase(BaseModel):
    """A real model an adapter must find, and what it should find.

    Attributes:
        model: the model as a person would type it.
        year: the model year, for vehicles.
        note: what the product is, for readers.
        expect: what the result must look like.
        skip: why the case can't pass today (with a date); a skipped case isn't run.
    """

    model: str
    year: int | None = None
    note: str = ""
    expect: Expect = Field(default_factory=Expect)
    skip: str | None = None  # reason, when a known model can't currently pass


class FieldSpec(BaseModel):
    """Where each field of a candidate comes from in one search result.

    JSONPath for json steps, a selector spec for html steps, a group name for regex steps; a literal starts
    with "=" (html, regex) or lacks "$" (json). Only `url` is required.
    """

    url: str
    title: str | None = None
    kind: str | None = None
    language: str | None = None
    models: str | None = None
    page_url: str | None = None
    published: str | None = None


class SearchStep(BaseModel):
    """One request and how to read candidates out of its response.

    `url`, `params`, `json` and `form` are templates ({model}, {model_q}, {brand}, ...); `results` selects
    each result (JSONPath, CSS selector or regex, by `format`); `fields` maps them to candidate fields.
    `keep_url`, `drop_url`, `keep_title` and `drop_title` filter results by regex (`keep_url` and `keep_title` are templates
    too); `base_url` resolves relative links; `url_template` builds the final URL from the `url` field. With
    `for_each_variant`, the step stops at the first model variant that finds something.
    """

    url: str
    method: Literal["GET", "POST"] = "GET"
    params: dict[str, Any] | None = None
    json_body: Any = Field(default=None, alias="json")
    form: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    format: Literal["json", "html", "regex"] = "json"
    results: str  # JSONPath (json), CSS selector (html) or a regex with named groups (regex)
    fields: FieldSpec
    base_url: str | None = None
    url_template: str | None = None  # build the final url from a field: "https://x/{url}"
    keep_url: str | None = None  # regex the url must match
    drop_url: str | None = None
    keep_title: str | None = None
    drop_title: str | None = None  # regex: drop results whose title matches
    for_each_variant: bool = True  # stop at the first variant with results

    model_config = {"populate_by_name": True}


class AdapterSpec(BaseModel):
    """An adapter's YAML file: the brand, where its documents live, how to search, and its test cases.

    `status` is works, partial, link, blocked or untested; `python` names a "module:Class" under
    manualbeagle.adapters.py that does the searching instead of `search`; `model` holds `strip_suffix`
    regexes; `kinds` maps a site's labels to DocKind; `manual_page` is a page a person can open; `options` is
    passed to a Python adapter as is.
    """

    id: str
    brand: str
    aliases: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    status: Literal["works", "partial", "blocked", "link", "untested"] = "untested"
    notes: str = ""
    categories: list[str] = Field(default_factory=list)
    vehicle: bool = False
    python: str | None = None  # "module:Class" under manualbeagle.adapters.py
    model: dict[str, Any] = Field(default_factory=dict)  # strip_suffix: [regex], upper: bool
    search: list[SearchStep] = Field(default_factory=list)
    kinds: dict[str, DocKind] = Field(default_factory=dict)  # label (case-insensitive substring) -> kind
    kind_default: DocKind | None = None
    link_only: bool = False
    manual_page: str | None = None  # a page a person can open: "https://maker/support?q={model}"
    options: dict[str, Any] = Field(default_factory=dict)  # free-form, for Python adapters
    # The maker's manuals cover a range and don't name each model (Reolink's Wireless Camera manual), so sync
    # trusts this adapter's match when a PDF's text doesn't name the model.
    family_manuals: bool = False
    tests: list[TestCase] = Field(default_factory=list)


@dataclass
class Context:
    """What an adapter gets for one lookup: the polite HTTP client and a log of notes."""

    http: PoliteClient
    log: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        """Add a line to the lookup's log (shown by verbose output)."""
        self.log.append(msg)


class Adapter:
    """Base class. YAML-only adapters use YamlAdapter; Python adapters subclass this and override find()."""

    def __init__(self, spec: AdapterSpec) -> None:
        self.spec = spec

    @property
    def id(self) -> str:
        """The adapter's id: its YAML file name."""
        return self.spec.id

    @property
    def brand(self) -> str:
        """The brand name it answers to."""
        return self.spec.brand

    def names(self) -> list[str]:
        """The brand and its aliases."""
        return [self.spec.brand, *self.spec.aliases]

    def strip_rules(self) -> list[str]:
        """The adapter's `model.strip_suffix` regexes, used to make model variants."""
        return list(self.spec.model.get("strip_suffix", []))

    def variants(self, q: Query) -> list[str]:
        """The query's model variants, most specific first (the model itself when there are none)."""
        return q.variants or [q.model]

    def find(self, q: Query, ctx: Context) -> Iterable[Candidate]:
        """Search the maker's site for the query and return candidates. Fetch only through `ctx.http`."""
        raise NotImplementedError

    def trim_fixture(self, url: str, body: str) -> str:
        """Cut a recorded response down to what find() reads, so fixtures stay small. Default: keep it all."""
        return body

    # helpers for subclasses
    def kind_for(self, label: str | None, title: str = "", url: str = "") -> DocKind | None:
        """The document kind for a site's label or a title.

        Tries the adapter's `kinds` map (exact label, then substring) on the label and then the title, then
        guesses from the words of the label, title and URL, then falls back to `kind_default`.
        """
        for text in (label, title):
            if not text:
                continue
            low = text.lower()
            for k, v in self.spec.kinds.items():
                if k.lower() == low:
                    return v
            for k, v in self.spec.kinds.items():
                if k.lower() in low:
                    return v
        guessed = guess_kind(" ".join(x for x in (label, title, url) if x))
        return guessed or self.spec.kind_default

    def candidate(self, **kw: Any) -> Candidate:
        """A Candidate with `source` set to this adapter and `link_only` from its spec, unless given."""
        kw.setdefault("source", f"adapter:{self.id}")
        kw.setdefault("link_only", self.spec.link_only)
        return Candidate(**kw)


_KIND_WORDS: list[tuple[str, DocKind]] = [
    (r"quick[\s_-]*(start|reference|setup)|getting started|qsg\b", DocKind.quick_start),
    (r"install(ation|ing)?|mounting", DocKind.install_guide),
    (r"\bparts?[\s_-]*(list|breakdown|diagram|catalog)|exploded|repair parts|\bIPL\b", DocKind.parts_list),
    (r"\bspec(ification)?s?\b|data ?sheet|submittal|product data", DocKind.spec_sheet),
    (r"service[\s_-]*manual|technical service|tech sheet|wiring diagram", DocKind.service_manual),
    (r"warrant(y|ies)", DocKind.warranty),
    (r"maintenance", DocKind.maintenance_guide),
    (r"navigation|multimedia|audio system|entune|infotainment", DocKind.navigation_manual),
    (r"energy guide|energyguide", DocKind.energy_guide),
    (r"owner'?s?|user'?s?|operati(on|ng)|instruction|use (and|&) care|manual", DocKind.owners_manual),
]


def guess_kind(text: str) -> DocKind | None:
    """Guess a document's kind from words in its label, title or URL; None when nothing matches."""
    for pat, kind in _KIND_WORDS:
        if re.search(pat, text, re.I):
            return kind
    return None
