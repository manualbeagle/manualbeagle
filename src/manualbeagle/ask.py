"""Ask your manuals: a question answered from the library's own pages, citing each page it used.

Retrieval, then (optionally) generation:

1. **Retrieval** is the per-page full-text index (`Library.search`, the same search as `manualbeagle search`).
   The question's words, less the little ones ("how", "do", "the"), are searched as `"a" OR "b" OR "c"`, so
   the pages that hold the most of them, and the rarest of them, come first (bm25). A brand, model or document
   picked by the person narrows the search; so does a brand or model the question names that the library has.
   The best few pages are kept (`PAGES`), at most `PER_DOCUMENT` from one document, each cut to the part around
   the words it matched (`PAGE_CHARS`), so the prompt stays short enough for a small local model.
2. **Generation** is the `[ai]` model of the config, the same set-up as the rating-plate reader (`vision.py`):
   Ollama on your own network, or Claude through Anthropic's API. It is told to answer only from the numbered
   pages, to cite each one it uses as `[n]`, and to say `NOT IN MANUALS` when they don't answer the question;
   that answer becomes "The manuals don't say." No AI is needed: with no model set up (or with `use_model`
   off), the answer is the best pages with their snippets.

The text that goes to the model is the question, and the excerpts of the chosen pages with their brand, model,
title and page number; nothing else from the library. With Claude it leaves your network (see docs/privacy.md).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import AIConfig, Config, read_secret
from .http import FetchError
from .library import Library
from .vision import DEFAULT_MODELS, TIMEOUT
from .vision import _error as _api_error

PAGES = 4  # pages given to the model: a small model's context is short
MAX_PAGES = 8
PER_DOCUMENT = 3  # so one long manual doesn't crowd out a second one that also matches
PAGE_CHARS = 1500  # of each page's text, around the words it matched
CANDIDATES = 30  # search hits looked at before choosing PAGES of them
ANSWER_CHARS = 2000
NOT_FOUND = "NOT IN MANUALS"
DONT_SAY = "The manuals don't say."

SYSTEM = (
    "You answer questions about a household's appliances, tools and vehicles, using only the numbered manual "
    "pages you are given. Rules:\n"
    "- Use only what those pages say. Never add facts, steps or numbers from elsewhere.\n"
    "- After each sentence or step, cite the page it came from as [n], using the page's number, such as [2].\n"
    "- Keep it short: a few sentences, or a short list of steps.\n"
    f"- If the pages don't answer the question, reply with exactly {NOT_FOUND} and nothing else."
)

# Words that say little about what a page is about: left out of the search, so the pages that hold the
# question's real words ("clean", "filter", "E5") rank first.
_STOP = """
    a about after again all also am an and any are as at be been before being both but by can could did do does
    doing done during each either else ever every few for from get gets getting got had has have having he her
    here hers him his how i if in into is it its itself just me might mine more most much must my myself need
    needs no nor of off on once only or other our ours ourselves out over own please really same say says
    shall she should so some such tell than that the their theirs them then there these they this those though
    through to too under until up upon us very want was way we were what whats when where which while who whom
    whose why will with would yes yet you your yours yourself manual manuals know
"""
STOP_WORDS = frozenset(_STOP.split())
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-/]*[A-Za-z0-9]|[A-Za-z0-9]")


class AskError(Exception):
    """A model that couldn't be asked or gave no usable answer, with the message to show."""


@dataclass
class Source:
    """One page given to the model (or, with no model, one of the best pages), numbered as the prompt numbers it."""

    n: int
    sha256: str
    page: int
    brand: str
    model: str
    title: str
    kind: str | None
    snip: str  # FTS5's snippet, with `marks` around the matched words
    text: str = field(default="", repr=False)  # the excerpt the model was given
    cited: bool = False

    @property
    def label(self) -> str:
        """The page as a person reads it: "LG WM5700HVA · Owner's Manual · p. 34"."""
        return f"{self.brand} {self.model} · {self.title or self.kind or 'Document'} · p. {self.page}"


@dataclass
class Answer:
    """What `ask` returns.

    `status` is one of:
    - `answered`: the model answered from the pages; `answer` cites them as [n], and `Source.cited` marks those.
    - `not_found`: the model said the pages don't answer it; `answer` is "The manuals don't say."
    - `pages_only`: no model is set up (or it wasn't asked); `sources` are the best pages.
    - `model_error`: the model couldn't be asked (`error` says why); `sources` are the best pages.
    - `no_pages`: no page of the library matches the question.
    """

    question: str
    status: str
    answer: str = ""
    sources: list[Source] = field(default_factory=list)
    terms: list[str] = field(default_factory=list)  # the words searched for, to highlight in the Reader
    scope: dict[str, str] = field(default_factory=dict)  # brand, model or document the search was narrowed to
    model: str | None = None  # the model's label, "ollama gemma3", when one was asked
    leaves_network: bool = False  # the pages' text went to a service outside your network
    error: str = ""

    @property
    def headline(self) -> str:
        """The status as a glyph and words, so it never rests on colour: "✓ answered from 2 of 4 pages"."""
        n = len(self.sources)
        if self.status == "answered":
            cited = sum(s.cited for s in self.sources)
            if not cited:
                return f"▲ answered, but the model cited none of the {n} pages it read"
            return f"✓ answered from {cited} of {n} pages"
        if self.status == "not_found":
            return f"– the manuals don't say (read {n} pages)"
        if self.status == "pages_only":
            return "▲ the best pages (no AI model wrote an answer)"
        if self.status == "model_error":
            return f"✗ the model couldn't answer ({self.error}): the best pages"
        return "– no page of the library matches"

    def to_dict(self) -> dict[str, Any]:
        """As JSON for the web API and the MCP tool (excerpts left out: they're the pages' own text)."""
        out = asdict(self)
        for s, src in zip(out["sources"], self.sources, strict=True):
            s.pop("text")
            s["label"] = src.label
        out["headline"] = self.headline
        return out


# Retrieval


def keywords(question: str) -> list[str]:
    """The words of a question worth searching for: no little words, no repeats, in order."""
    out: list[str] = []
    for w in WORD.findall(question):
        lw = w.lower()
        if lw in STOP_WORDS or (len(lw) < 2 and not lw.isdigit()) or lw in out:
            continue
        out.append(lw)
    return out


def fts_query(terms: list[str]) -> str:
    """The terms as an FTS5 query: each a quoted string (so no word is read as syntax), joined by OR."""
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def named_scope(question: str, docs: list[dict]) -> dict[str, str]:
    """The brand and model the question names, out of those in the library: {"brand": ..., "model": ...}.

    A model counts when one of the question's words is that model number (hyphens and case aside), or holds
    it when it is five characters or more. A brand counts when its whole name is in the question, and only when
    one brand is named. A model is kept only if one brand's documents have it.
    """
    low = question.lower()
    words = {_squash(w) for w in WORD.findall(question)} - {""}
    joined = _squash(question)
    brands = {
        d["brand"] for d in docs if d["brand"] and re.search(rf"\b{re.escape(d['brand'].lower())}\b", low)
    }
    models = set()
    for d in docs:
        m = _squash(d["model"] or "")
        if len(m) >= 3 and (m in words or (len(m) >= 5 and m in joined)):
            models.add((d["brand"], d["model"]))
    if brands and len(brands) == 1:
        models = {bm for bm in models if bm[0] in brands}
    out: dict[str, str] = {}
    if len(models) == 1:
        out["brand"], out["model"] = next(iter(models))
    elif len(brands) == 1:
        out["brand"] = next(iter(brands))
    return out


def _scope_words(scope: dict[str, str]) -> set[str]:
    """The brand's and model's own words, which every page of their manuals matches anyway."""
    return {w.lower() for v in scope.values() for w in WORD.findall(v)} | {_squash(v) for v in scope.values()}


def excerpt(text: str, terms: list[str], size: int = PAGE_CHARS) -> str:
    """The part of a page around the search terms: the `size` characters that hold the most of them.

    A term matches a word that starts with it less a plural or verb ending ("cleaning" for "clean"), like the
    index's stemming. The whole page when it's short enough.
    """
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) <= size:
        return text
    stems = [re.sub(r"(ing|ed|es|s)$", "", t) or t for t in terms]
    hits = [m.start() for s in stems for m in re.finditer(rf"\b{re.escape(s)}", text, re.I)]
    if not hits:
        return text[:size].rstrip() + " …"
    best = max(hits, key=lambda h: sum(1 for x in hits if h - size // 4 <= x < h - size // 4 + size))
    start = max(0, min(best - size // 4, len(text) - size))
    cut = text[start : start + size].strip()
    return ("… " if start else "") + cut + (" …" if start + size < len(text) else "")


def retrieve(
    lib: Library,
    question: str,
    brand: str | None = None,
    model: str | None = None,
    document: str | None = None,
    pages: int = PAGES,
    marks: tuple[str, str] = ("[", "]"),
) -> tuple[list[Source], list[str], dict[str, str]]:
    """The best pages for a question: (sources, the words searched for, the scope searched).

    The scope is what the caller passed (a document by its sha256 or a unique prefix of it); failing that, the brand or model the question names (`named_scope`).
    A named scope that finds nothing is dropped and the whole library searched instead.
    """
    docs = lib.documents()
    if document and len(document) < 64:  # a prefix, as `manualbeagle docs` prints it
        found = [d["sha256"] for d in docs if d["sha256"].startswith(document.lower())]
        document = found[0] if len(found) == 1 else document
    given = {k: v for k, v in (("brand", brand), ("model", model), ("document", document)) if v}
    scope = given or named_scope(question, docs)
    terms = [t for t in keywords(question) if t not in _scope_words(scope)] or keywords(question)
    if not terms:
        return [], [], scope

    def search(sc: dict[str, str]) -> list[dict]:
        try:
            return lib.search(fts_query(terms), limit=CANDIDATES, marks=marks, **sc)
        except sqlite3.OperationalError:
            return []

    rows = search(scope)
    if not rows and scope and not given:
        scope = {}
        rows = search(scope)
    chosen: list[dict] = []
    per_doc: dict[str, int] = {}
    for r in rows:
        if per_doc.get(r["sha256"], 0) >= PER_DOCUMENT:
            continue
        per_doc[r["sha256"]] = per_doc.get(r["sha256"], 0) + 1
        chosen.append(r)
        if len(chosen) >= pages:
            break
    sources = []
    for i, r in enumerate(chosen, 1):
        text = lib.read_pages(r["sha256"], [r["page"]]).get(r["page"], "")
        sources.append(
            Source(
                n=i,
                sha256=r["sha256"],
                page=r["page"],
                brand=r["brand"] or "",
                model=r["model"] or "",
                title=r["title"] or "",
                kind=r["kind"],
                snip=" ".join((r["snip"] or "").split()),
                text=excerpt(text, terms),
            )
        )
    return sources, terms, scope


# Generation


def prompt(question: str, sources: list[Source]) -> str:
    """The user message: each page numbered, with what it is, then the question."""
    parts = ["Manual pages:"]
    for s in sources:
        parts.append(
            f"\n[{s.n}] {s.brand} {s.model}, {s.title or s.kind or 'manual'}, page {s.page}:\n{s.text}"
        )
    parts.append(f"\nQuestion: {question}")
    return "\n".join(parts)


CITES = re.compile(r"\[\s*(\d+(?:\s*[,;]\s*\d+)*)\s*\]")


def cite(text: str, sources: list[Source]) -> str:
    """The model's answer with its citations tidied: "[1, 2]" becomes "[1][2]", numbers with no page dropped.

    Marks each cited source `cited`.
    """
    by_n = {s.n: s for s in sources}

    def one(m: re.Match) -> str:
        out = ""
        for n in (int(x) for x in re.split(r"[,;]", m.group(1))):
            if n in by_n:
                by_n[n].cited = True
                out += f"[{n}]"
        return out

    return re.sub(r"[ \t]+([.,;:])", r"\1", CITES.sub(one, text)).strip()


class Model:
    """A text model: `complete()` sends the system prompt and one user message and returns the reply."""

    provider = ""

    def __init__(self, cfg: AIConfig, http: Any) -> None:
        self.cfg = cfg
        self.http = http
        self.model = cfg.model or DEFAULT_MODELS[self.provider]

    @property
    def label(self) -> str:
        """The provider and model: "ollama gemma3"."""
        return f"{self.provider} {self.model}"

    @property
    def leaves_network(self) -> bool:
        """Whether the pages' text goes to a service outside your own network."""
        return self.provider != "ollama"

    def complete(self, system: str, user: str) -> str:
        """Ask the model; raises AskError when it can't be asked or its reply isn't a message."""
        raise NotImplementedError


class Ollama(Model):
    """A model served by Ollama's chat API (`POST /api/chat`), with a low temperature and an 8k context."""

    provider = "ollama"

    def complete(self, system: str, user: str) -> str:
        """See `Model.complete`."""
        url = (self.cfg.url or "http://localhost:11434").rstrip("/") + "/api/chat"
        body = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 8192},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            r = self.http.post(url, json_body=body, check_challenge=False)
        except FetchError as e:
            raise AskError(f"Ollama didn't answer: {e}") from None
        if not r.ok:
            raise AskError(f"Ollama answered HTTP {r.status}: {_api_error(r)}")
        try:
            return r.json()["message"]["content"]
        except (ValueError, KeyError, TypeError):
            raise AskError("Ollama's answer wasn't a chat message") from None


class Anthropic(Model):
    """Claude, through Anthropic's Messages API (`POST /v1/messages`): low effort, server-side fallback on."""

    provider = "anthropic"
    url = "https://api.anthropic.com/v1/messages"

    def complete(self, system: str, user: str) -> str:
        """See `Model.complete`."""
        key = read_secret(self.cfg.api_key_env or "ANTHROPIC_API_KEY")
        if not key:
            raise AskError(f"no API key: set {self.cfg.api_key_env or 'ANTHROPIC_API_KEY'}")
        body = {
            "model": self.model,
            "max_tokens": 16000,
            "output_config": {"effort": "low"},
            "fallbacks": "default",
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "server-side-fallback-2026-07-01",
        }
        try:
            r = self.http.post(self.url, json_body=body, headers=headers, check_challenge=False)
        except FetchError as e:
            raise AskError(f"Anthropic's API didn't answer: {e}") from None
        if not r.ok:
            raise AskError(f"Anthropic's API answered HTTP {r.status}: {_api_error(r)}")
        try:
            data = r.json()
        except ValueError:
            raise AskError("Anthropic's answer wasn't JSON") from None
        if data.get("stop_reason") == "refusal":
            raise AskError("the model declined to answer")
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


PROVIDERS: dict[str, type[Model]] = {"ollama": Ollama, "anthropic": Anthropic}


def model_client(cfg: Config):
    """A PoliteClient for the model: a long timeout, and Ollama's address allowed even when it's private."""
    return cfg.client(extra_private=[cfg.ollama_url()], timeout=TIMEOUT)


def make_model(cfg: AIConfig, http: Any) -> Model | None:
    """The text model the `[ai]` table sets up, or None when it's off. Raises AskError for an unknown one."""
    if cfg.provider in ("", "none"):
        return None
    cls = PROVIDERS.get(cfg.provider)
    if cls is None:
        raise AskError(f"[ai] provider {cfg.provider!r} can't answer questions; use ollama or anthropic")
    return cls(cfg, http)


def ask(
    lib: Library,
    question: str,
    model: Model | None = None,
    brand: str | None = None,
    model_number: str | None = None,
    document: str | None = None,
    pages: int = PAGES,
    marks: tuple[str, str] = ("[", "]"),
) -> Answer:
    """Answer a question from the library's pages, citing each page used (see the module docstring).

    `model` is the text model (`make_model`), or None for the best pages only. `brand`, `model_number` and
    `document` (a sha256) narrow the search; `pages` is how many pages the model reads (1 to `MAX_PAGES`).
    """
    question = " ".join(question.split())
    pages = max(1, min(pages, MAX_PAGES))
    sources, terms, scope = retrieve(lib, question, brand, model_number, document, pages, marks)
    out = Answer(question=question, status="no_pages", sources=sources, terms=terms, scope=scope)
    if not sources:
        out.answer = DONT_SAY
        return out
    if model is None:
        out.status = "pages_only"
        return out
    out.model, out.leaves_network = model.label, model.leaves_network
    try:
        reply = model.complete(SYSTEM, prompt(question, sources)).strip()
    except AskError as e:
        out.status, out.error = "model_error", str(e)
        return out
    if not reply or NOT_FOUND in reply.upper():
        out.status, out.answer = "not_found", DONT_SAY
        return out
    out.status, out.answer = "answered", cite(reply[:ANSWER_CHARS], sources)
    return out
