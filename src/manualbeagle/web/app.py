"""The web UI's server: a small JSON API under /api and one static page, with Starlette.

`manualbeagle serve --web` runs it on 127.0.0.1:8765. Every endpoint calls the same code as the CLI: `pipeline.find`,
`Library`, `batch.look_up`, `sync.plan_item`, `sync.attach` and `newitem` (a scanned appliance into Homebox). All fetching goes through one shared
`PoliteClient`, so the UI keeps the same per-site turns and delays as the command line.

Who may use it: on a loopback address, the person at this computer. The Host header must be a loopback name
(which stops DNS rebinding), and a request that changes something must be JSON from this page's own origin (which
stops other web sites from posting to it). On any other address a secret token is required as well: the server
prints a link carrying it, and the page keeps it in a same-site cookie. Behind a TLS reverse proxy
(`--public-url`), the proxy's origin may send changes too, and an https one makes the cookie Secure.

Nameplate photos (`POST /api/nameplate`) are the one request body that isn't JSON: a JPEG or PNG, checked by its
first bytes, which a page on another site can't send without a CORS preflight this server never answers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import secrets
import sqlite3
import subprocess
import threading
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import __version__, settings
from .. import maintenance as mt
from ..adapters import Registry
from ..ask import AskError, make_model
from ..ask import ask as ask_manuals
from ..batch import harvest_args, look_up, parse_rows, row_item
from ..batch import summary as batch_summary
from ..config import Config, env_overrides
from ..config import build as build_config
from ..http import FetchError, PoliteClient
from ..library import Library, ocr_available
from ..models import KIND_LABELS, Candidate, DocKind, Query
from ..nameplate import (
    IMAGE_TYPES,
    MAX_IMAGE,
    brand_names,
    image_type,
    ocr_image,
    read_plate,
    strip_metadata,
    too_big,
)
from ..nameplate import ocr_available as plate_ocr_available
from ..newitem import NewItem, same_item, sync_args
from ..newitem import add as add_item
from ..newitem import preview as preview_item
from ..normalize import normalise
from ..pipeline import find
from ..search import make_engine
from ..sinks.homebox import Homebox, HomeboxError
from ..sync import Plan, attach, homebox, plan_item
from ..sync import summary as sync_summary
from ..vision import TIMEOUT as VISION_TIMEOUT
from ..vision import VisionError, make_vision
from .jobs import Busy, Job, Jobs

STATIC = Path(__file__).parent / "static"
LOOPBACK = ("127.0.0.1", "localhost", "::1", "[::1]")
COOKIE = "manualbeagle_token"
SHA = re.compile(r"^[0-9a-f]{64}$")
ENTITY_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")  # a Homebox id (a UUID)
# Search snippets mark each match with these, so the page can highlight them without trusting the text.
MARKS = ("\x02", "\x03")
MAX_ROWS = 2000
MAX_PAGES = 20
COOKIE_AGE = 365 * 24 * 3600  # a year, so a phone's installed app stays signed in
NAMEPLATE = "/api/nameplate"
OCR_TURN = threading.Lock()
# What anyone may load without the token: the page's own code and the web app manifest, which browsers fetch
# without cookies. None of it holds data.
PUBLIC = ("/static/", "/manifest.webmanifest", "/sw.js")

CSP = (
    "default-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


class ApiError(Exception):
    """A request the API refuses, with the HTTP status and the message the page shows."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class State:
    """What every request shares: the config, the adapters, the polite clients, the jobs and the token.

    `http` serves lookups, batch and sync. `fetch_http` serves the page's Fetch button, whose URL comes from the
    browser rather than from ranking, so it refuses manual mirrors on every redirect hop.
    `file_data` is config.toml as read, the base the Settings page's changes go over; `own_clients` says the
    clients were made here from `cfg`, so a change of settings makes new ones (`apply_settings`).
    """

    def __init__(
        self,
        cfg: Config,
        reg: Registry,
        http: PoliteClient | Any,
        fetch_http: PoliteClient | Any,
        token: str,
        origins: list[str] | None = None,
        vision_http: PoliteClient | Any = None,
        file_data: dict | None = None,
        own_clients: bool = False,
    ) -> None:
        self.cfg = cfg
        self.reg = reg
        self.http = http
        self.fetch_http = fetch_http
        self.vision_http = vision_http or http
        self.file_data = file_data if file_data is not None else cfg.model_dump(mode="json")
        self.own_clients = own_clients
        self.settings_lock = threading.Lock()
        self.jobs = Jobs()
        self.token = token
        # Origins besides the server's own that may send changes: a reverse proxy's public https:// address.
        self.origins = set(origins or [])
        self.secure_cookie = any(o.startswith("https://") for o in self.origins)
        self.brand_names = brand_names(name for a in reg for name in a.names())

    def vision(self):
        """The optional vision model the `[ai]` table sets up, or None (also when it names an unknown one)."""
        try:
            return make_vision(self.cfg.ai, self.vision_http)
        except VisionError:
            return None

    @contextmanager
    def library(self) -> Iterator[Library]:
        """A Library for this thread (SQLite connections stay in the thread that opened them)."""
        lib = Library(self.cfg.library, self.cfg.db_path)
        try:
            yield lib
        finally:
            lib.close()

    def engine(self):
        """The configured web-search engine, or None when it's turned off."""
        return make_engine(self.cfg.search.engine, url=self.cfg.search.url)

    def apply_settings(self, cfg: Config) -> None:
        """Use `cfg` from now on: new clients for its delay and private addresses, the old ones closed.

        Called only while no job runs, so nothing is using the old clients. Clients passed in from outside
        (tests) are kept.
        """
        if self.own_clients:
            old = (self.http, self.fetch_http, self.vision_http)
            self.http, self.fetch_http, self.vision_http = _clients(cfg, self.reg)
            for c in old:
                c.close()
        self.cfg = cfg


def _clients(cfg: Config, reg: Registry) -> tuple[PoliteClient, PoliteClient, PoliteClient]:
    """The three clients a server uses: lookups, the Fetch button (refuses manual mirrors), the `[ai]` model."""
    return (
        cfg.client(),
        cfg.client(refuse=reg.download_refusal),
        cfg.client(extra_private=[cfg.ollama_url()], timeout=VISION_TIMEOUT),
    )


def _state(request: Request) -> State:
    return request.app.state.mb


class Guard(BaseHTTPMiddleware):
    """Checks the token (when one is set) and the origin of changes, and adds security headers."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Refuse what this server shouldn't answer; add headers to what it does."""
        st = _state(request)
        path = request.url.path
        if st.token and path != "/healthz" and not path.startswith(PUBLIC):  # /healthz says only "ok"
            given = request.query_params.get("token")
            if given is not None and path == "/":
                if not secrets.compare_digest(given, st.token):
                    return _sign_in("✗ wrong token: open the link the server printed, or paste its token")
                r = RedirectResponse("/", 303)
                r.set_cookie(
                    COOKIE,
                    st.token,
                    max_age=COOKIE_AGE,
                    httponly=True,
                    samesite="strict",
                    secure=st.secure_cookie or request.url.scheme == "https",
                    path="/",
                )
                return r
            sent = request.cookies.get(COOKIE) or request.headers.get("authorization", "").removeprefix(
                "Bearer "
            )
            if not sent or not secrets.compare_digest(sent, st.token):
                if path == "/":
                    return _sign_in(
                        "This server needs its token: open the link it printed, or paste the token."
                    )
                return PlainTextResponse("✗ this server needs its token: open the link it printed", 401)
        if request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            own = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin is not None and origin != own and origin not in st.origins:
                return JSONResponse({"error": "requests from other sites are refused"}, 403)
            ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != "application/json" and not (path == NAMEPLATE and ctype in IMAGE_TYPES):
                return JSONResponse({"error": "send JSON (Content-Type: application/json)"}, 415)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers.setdefault("Content-Security-Policy", CSP)
            response.headers.setdefault("X-Frame-Options", "DENY")
        return response


def _sign_in(message: str) -> Response:
    """The page a browser without the token gets at /: a form to paste the token into.

    A phone's installed web app may not share the browser's cookies (iOS keeps them apart), so the link the
    server printed can't always be opened inside it; pasting the token there signs the app in.
    """
    from html import escape

    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Manual Beagle: sign in</title><link rel="stylesheet" href="/static/style.css"></head>'
        '<body><main class="signin"><h1>Manual Beagle</h1>'
        f'<p role="alert">{escape(message)}</p>'
        '<form method="get" action="/" class="row-form">'
        '<label class="grow">Token <input name="token" type="password" autocomplete="current-password" required>'
        '</label><button type="submit" class="primary">Sign in</button></form></main></body></html>'
    )
    return Response(
        page,
        401,
        media_type="text/html; charset=utf-8",
        headers={"Content-Security-Policy": CSP, "X-Frame-Options": "DENY", "Cache-Control": "no-store"},
    )


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise ApiError(400, "the request body isn't valid JSON") from None
    if not isinstance(body, dict):
        raise ApiError(400, "send a JSON object")
    return body


def _text(body: dict, key: str, required: bool = False, limit: int = 200) -> str:
    value = body.get(key)
    value = "" if value is None else str(value).strip()
    if required and not value:
        raise ApiError(400, f"{key} is required")
    if len(value) > limit:
        raise ApiError(400, f"{key} is too long")
    return value


def _number(value: Any, default: float, low: float, high: float, name: str) -> float:
    if value in (None, ""):
        return default
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ApiError(400, f"{name} must be a number") from None
    if not low <= n <= high:
        raise ApiError(400, f"{name} must be between {low:g} and {high:g}")
    return n


def _sha(request: Request) -> str:
    sha = request.path_params["sha"]
    if not SHA.match(sha):
        raise ApiError(404, "no such document")
    return sha


# Pages


async def index(request: Request) -> Response:
    """The single page."""
    return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")


async def healthz(request: Request) -> Response:
    """GET /healthz: "ok" while the server runs. It needs no token and tells nothing else, for monitoring."""
    return PlainTextResponse("ok")


async def manifest(request: Request) -> Response:
    """The web app manifest, which lets a phone install the page as an app (over HTTPS)."""
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


async def service_worker(request: Request) -> Response:
    """The service worker, served from / so that its scope is the whole site."""
    return FileResponse(
        STATIC / "sw.js", media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "no-cache"}
    )


# The API


async def api_status(request: Request) -> Response:
    """GET /api/status: the version, the library's size, and which optional parts are set up."""
    st = _state(request)

    def work() -> dict:
        with st.library() as lib:
            docs = lib.documents()
        vision = st.vision()
        return {
            "version": __version__,
            "documents": len(docs),
            "pages": sum(d["pages"] or 0 for d in docs),
            "ocr": ocr_available(),
            "plate_ocr": plate_ocr_available(),
            "vision": vision.label if vision else None,
            "vision_leaves_network": bool(vision and vision.leaves_network),
            "search": st.cfg.search.engine != "none",
            "homebox": bool(st.cfg.homebox.url),
            "home_assistant": bool(st.cfg.home_assistant.url),
            "paperless": bool(st.cfg.paperless.url),
            "adapters": len(list(st.reg)),
            "kinds": [{"value": k.value, "label": KIND_LABELS[k]} for k in DocKind],
        }

    return JSONResponse(await run_in_threadpool(work))


async def api_find(request: Request) -> Response:
    """POST /api/find {brand, model, year?, kind?, search: auto|always|never}: the ranked candidates.

    Answers with `FindResult` as JSON plus `in_library`, which maps each candidate URL already downloaded to its
    document's sha256. Saves nothing.
    """
    st = _state(request)
    body = await _json(request)
    brand, model = _text(body, "brand", required=True), _text(body, "model", required=True)
    year = int(_number(body.get("year"), 0, 1900, 2100, "year")) or None
    kind = _text(body, "kind")
    if kind and kind not in DocKind.__members__:
        raise ApiError(400, f"unknown kind {kind!r}")
    search = _text(body, "search") or "auto"
    if search not in ("auto", "always", "never"):
        raise ApiError(400, "search must be auto, always or never")

    def work() -> dict:
        engine = None if search == "never" else st.engine()
        q = Query(brand=brand, model=model, year=year, kind=DocKind(kind) if kind else None)
        res = find(q, st.reg, st.http, engine, always_search=search == "always")
        with st.library() as lib:
            have = {d["source_url"]: d["sha256"] for d in lib.documents()}
        out = res.model_dump(mode="json")
        out["in_library"] = {c.url: have[c.url] for c in res.candidates if c.url in have}
        return out

    return JSONResponse(await run_in_threadpool(work))


async def api_fetch(request: Request) -> Response:
    """POST /api/fetch {candidate, brand, model}: download one candidate into the library and check its text.

    Refuses a link-only candidate, and any URL that `Registry.download_refusal` says is for a person to open
    (a manual mirror), since the candidate comes back from the browser.
    """
    st = _state(request)
    body = await _json(request)
    brand, model = _text(body, "brand", required=True), _text(body, "model", required=True)
    raw = body.get("candidate")
    if not isinstance(raw, dict):
        raise ApiError(400, "candidate is required")
    try:
        c = Candidate.model_validate(raw)
    except ValueError as e:
        raise ApiError(400, f"not a candidate: {e}") from None
    refusal = st.reg.download_refusal(c.url)
    if refusal:
        raise ApiError(422, f"not downloaded: {refusal}")

    def work() -> dict:
        with st.library() as lib:
            doc = lib.fetch(c, normalise(Query(brand=brand, model=model)), st.fetch_http)
        if getattr(st, "paperless_auto", None):
            st.paperless_auto.wake()  # [paperless] auto: file it soon
        return {
            "sha256": doc.sha256,
            "title": doc.title,
            "brand": doc.brand,
            "model": doc.model,
            "pages": doc.page_count,
            "ocr": doc.ocr,
            "check_score": doc.check_score,
            "check_reasons": doc.check_reasons or [],
        }

    try:
        return JSONResponse(await run_in_threadpool(work))
    except FetchError as e:
        raise ApiError(422, str(e)) from None


async def api_documents(request: Request) -> Response:
    """GET /api/documents: every document in the library (without its local path)."""
    st = _state(request)

    def work() -> list[dict]:
        with st.library() as lib:
            return [{k: v for k, v in d.items() if k != "path"} for d in lib.documents()]

    return JSONResponse(await run_in_threadpool(work))


async def api_document(request: Request) -> Response:
    """GET /api/documents/{sha}: one document's details."""
    st, sha = _state(request), _sha(request)

    def work() -> dict | None:
        with st.library() as lib:
            return lib.document(sha)

    doc = await run_in_threadpool(work)
    if doc is None:
        raise ApiError(404, "no such document")
    doc.pop("path")
    return JSONResponse(doc)


async def api_pages(request: Request) -> Response:
    """GET /api/documents/{sha}/pages?start=1&count=1: the text of up to 20 pages."""
    st, sha = _state(request), _sha(request)
    start = int(_number(request.query_params.get("start"), 1, 1, 100_000, "start"))
    count = int(_number(request.query_params.get("count"), 1, 1, MAX_PAGES, "count"))

    def work() -> dict | None:
        with st.library() as lib:
            doc = lib.document(sha)
            if doc is None:
                return None
            pages = lib.read_pages(sha, list(range(start, start + count)))
        return {"sha256": sha, "page_count": doc["pages"], "pages": {str(k): v for k, v in pages.items()}}

    out = await run_in_threadpool(work)
    if out is None:
        raise ApiError(404, "no such document")
    return JSONResponse(out)


async def api_pdf(request: Request) -> Response:
    """GET /api/documents/{sha}/pdf: the PDF itself, to read in the browser. Only files inside the library."""
    st, sha = _state(request), _sha(request)

    def work() -> dict | None:
        with st.library() as lib:
            return lib.document(sha)

    doc = await run_in_threadpool(work)
    root = st.cfg.library.resolve()
    path = Path(doc["path"]).resolve() if doc else None
    if path is None or not path.is_file() or not path.is_relative_to(root):
        raise ApiError(404, "no such document")
    name = re.sub(r"[^\w .-]+", "_", f"{doc['brand'] or ''} {doc['model'] or sha[:10]}").strip() or sha[:10]
    return FileResponse(
        path, media_type="application/pdf", filename=f"{name}.pdf", content_disposition_type="inline"
    )


def _quoted(query: str) -> str:
    """Each word of a query as an FTS5 string, for text typed without search syntax (hyphens, colons)."""
    return " ".join('"' + w.replace('"', '""') + '"' for w in query.split())


async def api_search(request: Request) -> Response:
    """GET /api/search?q=&brand=&model=&limit=: full-text search of every page, best first.

    Each hit's `snip` marks matched words with the control characters STX and ETX (`MARKS`). Text that isn't valid FTS5 syntax is searched
    again as plain words, and `plain` says so.
    """
    st = _state(request)
    q = (request.query_params.get("q") or "").strip()
    if not q:
        raise ApiError(400, "q is required")
    brand = request.query_params.get("brand") or None
    model = request.query_params.get("model") or None
    limit = int(_number(request.query_params.get("limit"), 30, 1, 200, "limit"))

    def work() -> dict:
        with st.library() as lib:
            try:
                rows = lib.search(q, brand=brand, model=model, limit=limit, marks=MARKS)
                plain = False
            except sqlite3.OperationalError:
                rows = lib.search(_quoted(q), brand=brand, model=model, limit=limit, marks=MARKS)
                plain = True
        return {"query": q, "plain": plain, "rows": rows}

    try:
        return JSONResponse(await run_in_threadpool(work))
    except sqlite3.OperationalError as e:
        raise ApiError(400, f"can't search for that: {e}") from None


async def api_ask(request: Request) -> Response:
    """POST /api/ask {question, brand?, model?, document?, pages?, use_model?}: answer from the library's pages.

    Retrieval runs on the per-page index; the `[ai]` model (if any, and unless `use_model` is false) writes a
    short answer citing pages as [n]. The reply is `Answer.to_dict()`: `status`, `headline` (a glyph and words),
    `answer`, and `sources` with each page's document, number, label, snippet (matches between `MARKS`) and
    whether it was cited. Read-only.
    """
    st = _state(request)
    body = await _json(request)
    question = _text(body, "question", required=True, limit=500)
    brand = _text(body, "brand") or None
    model_number = _text(body, "model") or None
    document = _text(body, "document") or None
    if document and not SHA.match(document):
        raise ApiError(400, "document must be a sha256")
    pages = int(_number(body.get("pages"), 4, 1, 8, "pages"))
    use_model = body.get("use_model", True) is not False

    def work() -> dict:
        try:
            llm = make_model(st.cfg.ai, st.vision_http) if use_model else None
        except AskError as e:
            raise ApiError(409, str(e)) from None
        with st.library() as lib:
            a = ask_manuals(lib, question, llm, brand, model_number, document, pages, marks=MARKS)
        return a.to_dict()

    return JSONResponse(await run_in_threadpool(work))


async def api_homebox(request: Request) -> Response:
    """GET /api/homebox: whether Homebox is set up and answering, and its version."""
    st = _state(request)
    if not st.cfg.homebox.url:
        return JSONResponse({"configured": False, "ok": False, "error": "no [homebox] url in the config"})

    def work() -> dict:
        try:
            hb = homebox(st.cfg)
        except HomeboxError as e:
            return {"configured": False, "ok": False, "url": st.cfg.homebox.url, "error": str(e)}
        try:
            version = hb.status().get("build", {}).get("version", "?")
            return {"configured": True, "ok": True, "url": st.cfg.homebox.url, "version": version}
        except (HomeboxError, httpx.HTTPError, OSError) as e:
            return {"configured": True, "ok": False, "url": st.cfg.homebox.url, "error": str(e)}
        finally:
            hb.close()

    return JSONResponse(await run_in_threadpool(work))


def _with_homebox(st: State, work):
    """Call `work(hb)` with a Homebox client and close it after. HomeboxError and network errors become 502s."""
    try:
        hb = homebox(st.cfg)
    except HomeboxError as e:
        raise ApiError(502, f"Homebox: {e}") from None
    try:
        return work(hb)
    except HomeboxError as e:
        raise ApiError(502, f"Homebox: {e}") from None
    except (httpx.HTTPError, OSError) as e:
        raise ApiError(502, f"Homebox didn't answer: {e}") from None
    finally:
        hb.close()


def _photo_path(st: State, sha: str) -> Path | None:
    """A kept nameplate photo's file, or None for no photo. 404 for a photo that isn't in the library."""
    if not sha:
        return None
    if not SHA.match(sha):
        raise ApiError(404, "no such photo")
    with st.library() as lib:
        row = lib.photo(sha)
    path = Path(row["path"]).resolve() if row else None
    if path is None or not path.is_file() or not path.is_relative_to(st.cfg.library.resolve()):
        raise ApiError(404, "no such photo")
    return path


def _entity_id(body: dict, key: str) -> str:
    value = _text(body, key, limit=64)
    if value and not ENTITY_ID.match(value):
        raise ApiError(400, f"{key} isn't a Homebox id")
    return value


async def api_new_item_preview(request: Request) -> Response:
    """POST /api/homebox/new-item/preview {brand, model, serial?, photo?, titles?}: what adding it would do.

    Reads Homebox and writes nothing. Answers with a suggested `name` and `kind` (from the photo's OCR text and
    `titles`, the documents Find returned), Homebox's `locations` to pick from, and `matches`: items that already
    have this maker and model number, so the page can offer to use one instead of making a duplicate.
    """
    st = _state(request)
    body = await _json(request)
    brand, model = _text(body, "brand", required=True), _text(body, "model", required=True)
    serial = _text(body, "serial", limit=80)
    titles = body.get("titles") or []
    if not isinstance(titles, list) or len(titles) > 50:
        raise ApiError(400, "titles must be a list of at most 50 strings")
    texts = [str(t)[:300] for t in titles]
    sha = _text(body, "photo", limit=64)

    def work() -> dict:
        if sha:
            _photo_path(st, sha)
            with st.library() as lib:
                texts.append((lib.photo(sha) or {}).get("ocr_text") or "")
        return _with_homebox(st, lambda hb: preview_item(hb, brand, model, serial, texts, st.reg))

    out = await run_in_threadpool(work)
    out["attach_links"] = st.cfg.homebox.attach_links
    return JSONResponse(out)


async def api_new_item(request: Request) -> Response:
    """POST /api/homebox/new-item {confirm: true, name, brand, model, serial?, location_id?, existing_id?, photo?}.

    Starts a job that writes to Homebox what the person confirmed (`newitem.add`): creates the item (or, with
    `existing_id`, adds to that item, which must have the same maker and model), sets its serial, attaches the
    plate photo, then finds and attaches its manual as `sync homebox` does. Each step is a row of the job.
    Refused without `confirm: true`, so nothing is written by a request that skipped the page's confirm step.
    """
    st = _state(request)
    body = await _json(request)
    if body.get("confirm") is not True:
        raise ApiError(400, "confirm first: send confirm: true")
    brand, model = _text(body, "brand", required=True), _text(body, "model", required=True)
    existing = _entity_id(body, "existing_id")
    new = NewItem(
        name=_text(body, "name", required=not existing, limit=255),
        brand=brand,
        model=model,
        serial=_text(body, "serial", limit=80),
        location_id=_entity_id(body, "location_id"),
        existing_id=existing,
    )
    sha = _text(body, "photo", limit=64)
    new.photo = await run_in_threadpool(_photo_path, st, sha)
    if existing:

        def check(hb: Homebox) -> None:
            if not same_item(hb.item(existing), brand, model, st.reg):
                raise ApiError(409, "that Homebox item has another maker or model number")

        await run_in_threadpool(_with_homebox, st, check)
    if st.jobs.running():
        raise ApiError(409, "another job is still running; wait for it or cancel it")

    def write(job: Job):
        hb = homebox(st.cfg)
        try:
            with st.library() as lib:

                def plan(item):
                    return plan_item(item, st.cfg, st.reg, st.http, lib, sync_args())

                for row in add_item(hb, new, plan, st.cfg.homebox.attach_links):
                    if row.get("url", "").startswith("http") and row["step"] == "item":
                        job.extra["item_url"] = row["url"]
                    yield row
        finally:
            hb.close()

    def summarise(job: Job) -> str:
        if job.state == "failed" or job.error:
            return ""
        manual = next((r for r in job.rows if r["step"] == "manual"), None)
        glyph = {"ok": "✓", "skip": "–", "bad": "✗"}
        head = f"✓ {'added to' if not existing else 'updated in'} Homebox: {new.name or 'the item'}"
        return head + (f"; manual: {glyph[manual['state']]} {manual['text']}" if manual else "")

    total = 2 + bool(new.photo) + bool(new.serial and not existing)
    try:
        job = st.jobs.start("add", total, write, summarise)
    except Busy as e:
        raise ApiError(409, str(e)) from None
    return JSONResponse(job.public(), 202)


# Settings


def _settings_out(st: State) -> dict:
    """What the Settings page shows: each setting, where its value comes from, and whether the keys are set."""
    cfg = st.cfg
    return {
        "fields": settings.describe(
            st.file_data,
            settings.read_overrides(cfg.home),
            cfg.model_dump(mode="json"),
            env_overrides(),
        ),
        "saved_in": str(settings.overrides_path(cfg.home)),
        "keys": {
            "homebox": bool(cfg.homebox.url) and bool(cfg.homebox.token()),
            "home_assistant": bool(cfg.home_assistant.url) and bool(cfg.home_assistant.token()),
            "paperless": bool(cfg.paperless.url) and bool(cfg.paperless.token()),
            "held_back": {
                t: getattr(cfg, t).key_held_back(getattr(cfg, t).url)
                for t in ("homebox", "home_assistant", "paperless")
            },
            "anthropic": bool(os.environ.get(cfg.ai.api_key_env or "ANTHROPIC_API_KEY")),
        },
    }


async def api_settings(request: Request) -> Response:
    """GET /api/settings: the settings and their sources. POST {changes: {key: value | null}}: save and use them.

    A null takes a change away. Saving waits for no job to be running, since the clients a job uses are
    replaced. Settings that could run a program, read a secret or move the library can't be changed here
    (`settings.py`).
    """
    st = _state(request)
    if request.method == "GET":
        return JSONResponse(await run_in_threadpool(_settings_out, st))
    body = await _json(request)
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        raise ApiError(400, "send {changes: {setting: value}}")

    def work() -> dict:
        with st.settings_lock:
            if st.jobs.running():
                raise ApiError(409, "a batch or sync is running; save the settings when it has finished")
            home = st.cfg.home
            new = settings.changed(settings.read_overrides(home), changes)  # checks every value
            try:
                cfg = build_config(st.file_data, new)  # the whole configuration, before anything is written
            except ValueError as e:
                raise ApiError(400, f"those settings don't work together: {e}") from None
            settings.write(home, new)
            st.apply_settings(cfg)
            return _settings_out(st)

    try:
        return JSONResponse(await run_in_threadpool(work))
    except ValueError as e:
        raise ApiError(400, str(e)) from None


async def api_ollama_models(request: Request) -> Response:
    """POST {url}: the models an Ollama at `url` has (its /api/tags), so the page can offer them as choices."""
    st = _state(request)
    body = await _json(request)
    try:
        url = settings.check("ai.url", body.get("url") or st.cfg.ai.url or "http://localhost:11434")
    except ValueError as e:
        raise ApiError(400, str(e)) from None

    def work() -> dict:
        client = st.cfg.client(extra_private=[url], timeout=15)
        try:
            r = client.get(url + "/api/tags")
            if not r.ok:
                return {"url": url, "ok": False, "error": f"HTTP {r.status}", "models": []}
            data = r.json()
        except (FetchError, httpx.HTTPError, OSError, ValueError) as e:
            return {"url": url, "ok": False, "error": str(e), "models": []}
        finally:
            client.close()
        models = data.get("models", []) if isinstance(data, dict) else []
        names = [m.get("name", "") for m in models if isinstance(m, dict)]
        return {"url": url, "ok": True, "models": sorted(n for n in names if n)}

    return JSONResponse(await run_in_threadpool(work))


# Nameplate photos


def _photo_out(row: dict) -> dict:
    """A photo's row for the page: without its local path or OCR text."""
    return {k: v for k, v in row.items() if k not in ("path", "ocr_text")}


async def _image_body(request: Request) -> tuple[bytes, str]:
    """The request's image: at most MAX_IMAGE bytes, JPEG or PNG by its first bytes, matching its Content-Type."""
    declared = request.headers.get("content-type", "").split(";")[0].strip().lower()
    over_cap = ApiError(413, f"the photo is over {MAX_IMAGE // 1_000_000} MB")
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_IMAGE:
        raise over_cap
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_IMAGE:
            raise over_cap
    data = bytes(buf)
    if not data:
        raise ApiError(400, "send the photo as the request body")
    kind = image_type(data)
    if kind is None:
        raise ApiError(415, "send a JPEG or PNG photo")
    if kind != declared:
        raise ApiError(415, f"the photo is {kind}, not {declared}")
    if refusal := too_big(data):
        raise ApiError(413, refusal)
    return data, kind


async def api_nameplate(request: Request) -> Response:
    """POST /api/nameplate (body: a JPEG or PNG): read a rating plate, keep the photo, suggest brand and model.

    The photo's metadata (location, device) is removed before it's kept in the library. Answers with `photo`
    (its sha256 and fields), `reading` (`nameplate.Reading`: brands, models and serials, best first, and the OCR
    text), `ocr` (false when Tesseract isn't installed, so nothing could be read) and `vision` (the optional
    vision model's name, or null). Nothing is looked up: the person confirms the brand and model first.
    """
    st = _state(request)
    data, kind = await _image_body(request)
    data = strip_metadata(data)

    def work() -> dict:
        have_ocr = plate_ocr_available()
        try:
            with OCR_TURN:  # one photo at a time: Tesseract takes a core and some memory for each
                text = ocr_image(data) if have_ocr else ""
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
            raise ApiError(500, f"couldn't read the photo: {e}") from None
        reading = read_plate(text, st.brand_names)
        with st.library() as lib:
            row = lib.add_photo(data, kind, text)
        vision = st.vision()
        return {
            "photo": _photo_out(row),
            "reading": reading.as_dict(),
            "ocr": have_ocr,
            "vision": vision.label if vision else None,
        }

    return JSONResponse(await run_in_threadpool(work))


def _photo_sha(request: Request) -> str:
    sha = request.path_params["sha"]
    if not SHA.match(sha):
        raise ApiError(404, "no such photo")
    return sha


async def api_photos(request: Request) -> Response:
    """GET /api/photos: every nameplate photo, newest first, with the brand and model confirmed for it."""
    st = _state(request)

    def work() -> list[dict]:
        with st.library() as lib:
            return lib.photos()

    return JSONResponse(await run_in_threadpool(work))


async def api_photo(request: Request) -> Response:
    """GET /api/photos/{sha}: one photo's fields and OCR text. POST {brand, model, serial?}: confirm them."""
    st, sha = _state(request), _photo_sha(request)
    if request.method == "POST":
        body = await _json(request)
        brand, model = _text(body, "brand", required=True), _text(body, "model", required=True)
        serial = _text(body, "serial", limit=80)

        def work() -> dict | None:
            with st.library() as lib:
                return lib.photo(sha) if lib.label_photo(sha, brand, model, serial) else None

    else:

        def work() -> dict | None:
            with st.library() as lib:
                return lib.photo(sha)

    row = await run_in_threadpool(work)
    if row is None:
        raise ApiError(404, "no such photo")
    row.pop("path")
    return JSONResponse(row)


async def api_photo_image(request: Request) -> Response:
    """GET /api/photos/{sha}/image: the photo itself. Only files inside the library."""
    st, sha = _state(request), _photo_sha(request)

    def work() -> dict | None:
        with st.library() as lib:
            return lib.photo(sha)

    row = await run_in_threadpool(work)
    path = Path(row["path"]).resolve() if row else None
    if row is None or path is None or not path.is_file() or not path.is_relative_to(st.cfg.library.resolve()):
        raise ApiError(404, "no such photo")
    return FileResponse(
        path, media_type=row["media_type"], headers={"Cache-Control": "private, max-age=86400"}
    )


async def api_photo_vision(request: Request) -> Response:
    """POST /api/photos/{sha}/vision {}: ask the optional vision model (`[ai]`) to read a kept photo.

    Answers with {model, brands, models, serials}, each a list of suggestions. 409 when no model is set up.
    """
    st, sha = _state(request), _photo_sha(request)
    await _json(request)
    try:
        vision = make_vision(st.cfg.ai, st.vision_http)
    except VisionError as e:
        raise ApiError(409, str(e)) from None
    if vision is None:
        raise ApiError(409, "no vision model is set up: see [ai] in the config")

    def work() -> dict | None:
        with st.library() as lib:
            row = lib.photo(sha)
        if row is None:
            return None
        found = vision.read(Path(row["path"]).read_bytes(), row["media_type"])
        return {"model": vision.label, **{k: [asdict(s) for s in v] for k, v in found.items()}}

    try:
        out = await run_in_threadpool(work)
    except VisionError as e:
        raise ApiError(502, str(e)) from None
    if out is None:
        raise ApiError(404, "no such photo")
    return JSONResponse(out)


async def api_photo_delete(request: Request) -> Response:
    """POST /api/photos/{sha}/delete {}: remove a photo from the library."""
    st, sha = _state(request), _photo_sha(request)
    await _json(request)

    def work() -> bool:
        with st.library() as lib:
            return lib.delete_photo(sha)

    if not await run_in_threadpool(work):
        raise ApiError(404, "no such photo")
    return JSONResponse({"deleted": sha})


def _plan_row(p: Plan) -> dict:
    """A sync or batch plan for the page: the library document's sha256 instead of its local path."""
    row = asdict(p)
    row["document"] = Path(p.file).stem if p.file else ""
    del row["file"]
    return row


def _is_csv(text: str, fmt: str) -> bool:
    if fmt in ("csv", "yaml"):
        return fmt == "csv"
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return "," in first and not first.startswith(("-", "#"))


async def api_batch(request: Request) -> Response:
    """POST /api/batch {text, format: auto|csv|yaml, fetch, min_score}: start a batch job over a list.

    Without `fetch`, each row is `batch.look_up` (nothing downloaded). With it, each row is `sync.plan_item`
    (the best PDF whose text names the model goes into the library, else the maker's link).
    """
    st = _state(request)
    body = await _json(request)
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ApiError(400, "paste a list, or choose a file")
    fmt = _text(body, "format") or "auto"
    if fmt not in ("auto", "csv", "yaml"):
        raise ApiError(400, "format must be auto, csv or yaml")
    fetch = bool(body.get("fetch"))
    min_score = _number(body.get("min_score"), 60, 0, 300, "min_score")
    try:
        rows = parse_rows(text, _is_csv(text, fmt))
    except Exception as e:  # yaml.YAMLError, csv.Error, ValueError: all mean "can't read this list"
        raise ApiError(400, f"can't read the list: {e}") from None
    if not rows:
        raise ApiError(400, "the list has no rows")
    if len(rows) > MAX_ROWS:
        raise ApiError(400, f"the list has {len(rows)} rows; the web UI takes at most {MAX_ROWS}")

    def look(job: Job):
        engine = st.engine()
        for row in rows:
            yield look_up(row, st.reg, st.http, engine, min_score)

    def harvest(job: Job):
        args = harvest_args(min_score)
        with st.library() as lib:
            for n, row in enumerate(rows):
                p = plan_item(row_item(n, row), st.cfg, st.reg, st.http, lib, args)
                job.data.setdefault("plans", []).append(p)
                yield _plan_row(p)

    def summarise(job: Job) -> str:
        if fetch:
            from ..batch import coverage

            return coverage(job.data.get("plans", []))
        return batch_summary(job.rows)

    try:
        job = st.jobs.start("batch", len(rows), harvest if fetch else look, summarise)
    except Busy as e:
        raise ApiError(409, str(e)) from None
    job.extra["fetch"] = fetch
    return JSONResponse(job.public(), 202)


async def api_sync(request: Request) -> Response:
    """POST /api/sync {item?, force?, links_only?, allow_search_links?, min_score?}: start a sync dry run.

    Plans every Homebox item as `sync homebox --dry-run` does, and writes nothing to Homebox. The page then
    shows the plans, and POST /api/jobs/{id}/attach writes them.
    """
    st = _state(request)
    body = await _json(request)
    args = argparse.Namespace(
        min_score=_number(body.get("min_score"), 60, 0, 300, "min_score"),
        links_only=bool(body.get("links_only")),
        force=bool(body.get("force")),
        allow_search_links=bool(body.get("allow_search_links")),
    )
    only = _text(body, "item").lower()

    def items() -> list:
        hb = homebox(st.cfg)
        try:
            return hb.items()
        finally:
            hb.close()

    try:
        found = await run_in_threadpool(items)
    except HomeboxError as e:
        raise ApiError(502, f"Homebox: {e}") from None
    except (httpx.HTTPError, OSError) as e:
        raise ApiError(502, f"Homebox didn't answer: {e}") from None
    found = [i for i in found if only in i.name.lower()]
    if st.jobs.running():
        raise ApiError(409, "another job is still running; wait for it or cancel it")

    def plan(job: Job):
        with st.library() as lib:
            for item in found:
                p = plan_item(item, st.cfg, st.reg, st.http, lib, args)
                job.data.setdefault("plans", []).append(p)
                yield _plan_row(p)

    try:
        job = st.jobs.start(
            "sync", len(found), plan, lambda j: sync_summary(j.data.get("plans", []), dry_run=True)
        )
    except Busy as e:
        raise ApiError(409, str(e)) from None
    return JSONResponse(job.public(), 202)


async def api_attach(request: Request) -> Response:
    """POST /api/jobs/{id}/attach {}: write a finished sync dry run's uploads and links to Homebox."""
    st = _state(request)
    await _json(request)
    dry = st.jobs.get(request.path_params["id"])
    if dry is None or dry.kind != "sync":
        raise ApiError(404, "no such sync dry run")
    if dry.state != "done":
        raise ApiError(409, "the dry run hasn't finished")
    if dry.extra.get("attached"):
        raise ApiError(409, "these plans were attached already; run a new dry run")
    links = st.cfg.homebox.attach_links  # off: upload PDFs only, as `sync homebox` does
    plans = [p for p in dry.data.get("plans", []) if p.action == "upload" or (p.action == "link" and links)]
    if not plans:
        raise ApiError(400, "nothing to attach")

    def write(job: Job):
        hb = homebox(st.cfg)
        try:
            for p in plans:
                row = _plan_row(p)
                try:
                    attach(hb, p)
                    row["done"] = True
                except HomeboxError as e:
                    row["error"] = str(e)
                yield row
        finally:
            hb.close()

    def summarise(job: Job) -> str:
        done = sum(1 for r in job.rows if r.get("done"))
        failed = len(job.rows) - done
        return f"✓ {done} attached to Homebox" + (f", ✗ {failed} refused" if failed else "")

    try:
        job = st.jobs.start("attach", len(plans), write, summarise)
    except Busy as e:
        raise ApiError(409, str(e)) from None
    dry.extra["attached"] = job.id
    job.extra["dry_run"] = dry.id
    return JSONResponse(job.public(), 202)


# Maintenance suggestions: read from the manuals, approved here one at a time, filed in Homebox.

MAINTENANCE_TURN = threading.Lock()  # one approval at a time, so a double click can't file an entry twice


def _suggestion_id(request: Request) -> int:
    sid = request.path_params["id"]
    if not sid.isdigit() or len(sid) > 12:
        raise ApiError(404, "no such suggestion")
    return int(sid)


async def api_maintenance(request: Request) -> Response:
    """GET /api/maintenance?status=new|filed|dismissed|all: the suggestions, the counts, and what's set up."""
    st = _state(request)
    status = request.query_params.get("status", "new")
    if status not in (*mt.STATUSES, "all"):
        raise ApiError(400, "status must be new, filed, dismissed or all")

    def work() -> dict:
        with st.library() as lib:
            store = mt.Store(lib)
            return {
                "suggestions": [s.public() for s in store.list(None if status == "all" else status)],
                "counts": store.counts(),
                "ai": mt.model_label(st.cfg.ai) or None,
                "homebox": bool(st.cfg.homebox.url),
            }

    return JSONResponse(await run_in_threadpool(work))


async def api_maintenance_suggest(request: Request) -> Response:
    """POST /api/maintenance/suggest {ai?}: start reading every document for maintenance tasks (a job).

    Matches documents to Homebox's items when Homebox is set up (reading only). With `ai`, the `[ai]` model is
    asked about the pages with an interval too. Writes only to the library's database, never to Homebox.
    """
    st = _state(request)
    body = await _json(request)
    label = mt.model_label(st.cfg.ai) if body.get("ai") else ""
    if body.get("ai") and not label:
        raise ApiError(400, "no model is set up: choose one on Settings ([ai] provider)")
    if st.jobs.running():
        raise ApiError(409, "another job is still running; wait for it or cancel it")

    def items() -> tuple[list | None, str]:
        if not st.cfg.homebox.url:
            return None, ""
        try:
            hb = homebox(st.cfg)
        except HomeboxError as e:
            return None, str(e)
        try:
            return hb.items(), ""
        except (HomeboxError, httpx.HTTPError, OSError) as e:
            return None, str(e)
        finally:
            hb.close()

    found_items, problem = await run_in_threadpool(items)

    def count() -> int:
        with st.library() as lib:
            return len(lib.documents())

    total = await run_in_threadpool(count)

    def work(job: Job):
        ask = None
        if label:

            def ask(prompt: str) -> str:
                return mt.ask_model(st.cfg.ai, st.vision_http, prompt)

        with st.library() as lib:
            for c in mt.suggest(lib, found_items, ask, label, cancelled=job.cancel.is_set):
                yield asdict(c)

    def summarise(job: Job) -> str:
        found = sum(r["found"] for r in job.rows)
        added = sum(r["added"] for r in job.rows)
        how = f"rules and {label}" if label else "rules only"
        return f"✓ {added} new suggestion{'' if added == 1 else 's'}; {found} found in {len(job.rows)} documents ({how})"

    try:
        job = st.jobs.start("maintenance", total, work, summarise)
    except Busy as e:
        raise ApiError(409, str(e)) from None
    if problem:
        job.extra["warning"] = f"Homebox: {problem}; suggestions are kept by brand and model until it answers"
    return JSONResponse(job.public(), 202)


def _date(value: Any) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        d = dt.date.fromisoformat(str(value))
    except ValueError:
        raise ApiError(400, "scheduled must be a date, YYYY-MM-DD") from None
    if not dt.date(2000, 1, 1) <= d <= dt.date(2100, 12, 31):
        raise ApiError(400, "scheduled must be a date between 2000 and 2100")
    return d


async def api_maintenance_approve(request: Request) -> Response:
    """POST /api/maintenance/{id}/approve {task?, scheduled?, item_id?}: file one suggestion in Homebox.

    The person's click: adds a maintenance entry to the Homebox item, scheduled for `scheduled` (default: one
    interval from today), named `task` (default: the suggestion's). `item_id` chooses the item when the
    suggestion has none (or another one). Homebox refusing leaves the suggestion waiting, with the reason.
    """
    st = _state(request)
    sid = _suggestion_id(request)
    body = await _json(request)
    task = _text(body, "task", limit=255)
    chosen = _entity_id(body, "item_id")
    scheduled = _date(body.get("scheduled"))

    def work() -> dict:
        with MAINTENANCE_TURN, st.library() as lib:
            store = mt.Store(lib)
            s = store.get(sid)
            if s is None:
                raise ApiError(404, "no such suggestion")
            if s.status != "new":
                raise ApiError(409, f"this suggestion was {s.status} already")
            item_id = chosen or s.item_id
            if not item_id:
                raise ApiError(400, "choose the Homebox item this task is for")
            try:
                hb = homebox(st.cfg)
            except HomeboxError as e:
                raise ApiError(400, str(e)) from None
            try:
                name = s.item_name if item_id == s.item_id else hb.entity(item_id).get("name", "")
                when = scheduled or s.first_date()
                entry = mt.file_in_homebox(hb, s, item_id, task or s.task, when)
            except HomeboxError as e:
                store.failed(sid, str(e))
                raise ApiError(502, f"Homebox refused: {e}") from None
            except (httpx.HTTPError, OSError) as e:
                store.failed(sid, str(e))
                raise ApiError(502, f"Homebox didn't answer: {e}") from None
            finally:
                hb.close()
            store.filed(sid, task or s.task, item_id, name, when.isoformat(), entry)
            done = store.get(sid)
            assert done is not None
            return done.public()

    return JSONResponse(await run_in_threadpool(work))


async def api_maintenance_decide(request: Request) -> Response:
    """POST /api/maintenance/{id}/dismiss {} or /reopen {}: dismiss a waiting suggestion, or undo that."""
    st = _state(request)
    sid = _suggestion_id(request)
    await _json(request)
    action = request.url.path.rsplit("/", 1)[1]

    def work() -> dict:
        with st.library() as lib:
            store = mt.Store(lib)
            ok = store.dismiss(sid) if action == "dismiss" else store.reopen(sid)
            s = store.get(sid)
            if s is None:
                raise ApiError(404, "no such suggestion")
            if not ok:
                raise ApiError(409, f"this suggestion is {s.status}")
            return s.public()

    return JSONResponse(await run_in_threadpool(work))


async def api_maintenance_items(request: Request) -> Response:
    """GET /api/maintenance/items: Homebox's items (id and name), to choose one for a suggestion without one."""
    st = _state(request)

    def work() -> list[dict]:
        try:
            hb = homebox(st.cfg)
        except HomeboxError as e:
            raise ApiError(400, str(e)) from None
        try:
            return sorted(
                (
                    {"id": e["id"], "name": e.get("name", "")}
                    for e in hb.summaries()
                    if not (e.get("entityType") or {}).get("isLocation")
                ),
                key=lambda e: e["name"].lower(),
            )
        except (HomeboxError, httpx.HTTPError, OSError) as e:
            raise ApiError(502, f"Homebox: {e}") from None
        finally:
            hb.close()

    return JSONResponse(await run_in_threadpool(work))


async def api_jobs(request: Request) -> Response:
    """GET /api/jobs: every job this server remembers, newest last, without their rows."""
    st = _state(request)
    return JSONResponse([{k: v for k, v in j.public().items() if k != "rows"} for j in st.jobs.all()])


async def api_job(request: Request) -> Response:
    """GET /api/jobs/{id}: one job with its rows so far."""
    job = _state(request).jobs.get(request.path_params["id"])
    if job is None:
        raise ApiError(404, "no such job")
    return JSONResponse(job.public())


async def api_cancel(request: Request) -> Response:
    """POST /api/jobs/{id}/cancel {}: stop a job after the row it's on."""
    await _json(request)
    job = _state(request).jobs.get(request.path_params["id"])
    if job is None:
        raise ApiError(404, "no such job")
    job.cancel.set()
    return JSONResponse(job.public())


async def _api_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ApiError)
    return JSONResponse({"error": str(exc)}, exc.status)


async def _not_found(request: Request, exc: Exception) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "not found"}, 404)
    return PlainTextResponse("Not found", 404)


def create_app(
    cfg: Config,
    http: PoliteClient | Any = None,
    token: str = "",
    hosts: list[str] | None = None,
    origins: list[str] | None = None,
    file_data: dict | None = None,
) -> Starlette:
    """The web app for `cfg`.

    `file_data` is config.toml as read (`config.read_file`), the base the Settings page's changes go over; without
    it, `cfg`'s own values are the base.

    `http` is the client every lookup and download uses (default: `cfg.client()`, plus one that refuses
    link-only sites for the Fetch button and one with a long timeout for the optional vision model; all closed
    when the app stops). A client passed in serves all three.
    `token`, when set, is required on every request. `hosts` are the Host header values to answer (default:
    the loopback names; "*" for any, which is only safe with a token). `origins` are other origins whose pages
    may send changes, such as a reverse proxy's `https://name`; an https one makes the token cookie Secure.
    """
    from .integrations import ROUTES as INTEGRATION_ROUTES

    reg = Registry(cfg.adapter_dirs)
    own_client = http is None
    client, fetch_client, vision_client = _clients(cfg, reg) if own_client else (http, http, http)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        yield
        st: State = app.state.mb
        if st.own_clients:
            for c in (st.http, st.fetch_http, st.vision_http):
                c.close()

    routes = [
        Route("/", index),
        Route("/healthz", healthz),
        Route("/manifest.webmanifest", manifest),
        Route("/sw.js", service_worker),
        Route("/api/status", api_status),
        Route("/api/find", api_find, methods=["POST"]),
        Route("/api/fetch", api_fetch, methods=["POST"]),
        Route("/api/documents", api_documents),
        Route("/api/documents/{sha}", api_document),
        Route("/api/documents/{sha}/pages", api_pages),
        Route("/api/documents/{sha}/pdf", api_pdf),
        Route("/api/search", api_search),
        Route("/api/ask", api_ask, methods=["POST"]),
        Route("/api/homebox", api_homebox),
        Route("/api/homebox/new-item/preview", api_new_item_preview, methods=["POST"]),
        Route("/api/homebox/new-item", api_new_item, methods=["POST"]),
        Route("/api/settings", api_settings, methods=["GET", "POST"]),
        Route("/api/settings/ollama-models", api_ollama_models, methods=["POST"]),
        Route(NAMEPLATE, api_nameplate, methods=["POST"]),
        Route("/api/photos", api_photos),
        Route("/api/photos/{sha}", api_photo, methods=["GET", "POST"]),
        Route("/api/photos/{sha}/image", api_photo_image),
        Route("/api/photos/{sha}/vision", api_photo_vision, methods=["POST"]),
        Route("/api/photos/{sha}/delete", api_photo_delete, methods=["POST"]),
        Route("/api/batch", api_batch, methods=["POST"]),
        Route("/api/sync", api_sync, methods=["POST"]),
        Route("/api/maintenance", api_maintenance),
        Route("/api/maintenance/suggest", api_maintenance_suggest, methods=["POST"]),
        Route("/api/maintenance/items", api_maintenance_items),
        Route("/api/maintenance/{id}/approve", api_maintenance_approve, methods=["POST"]),
        Route("/api/maintenance/{id}/dismiss", api_maintenance_decide, methods=["POST"]),
        Route("/api/maintenance/{id}/reopen", api_maintenance_decide, methods=["POST"]),
        Route("/api/jobs", api_jobs),
        Route("/api/jobs/{id}", api_job),
        Route("/api/jobs/{id}/attach", api_attach, methods=["POST"]),
        Route("/api/jobs/{id}/cancel", api_cancel, methods=["POST"]),
        *INTEGRATION_ROUTES,
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    app = Starlette(
        routes=routes,
        middleware=[
            Middleware(TrustedHostMiddleware, allowed_hosts=hosts or list(LOOPBACK), www_redirect=False),
            Middleware(Guard),
        ],
        exception_handlers={ApiError: _api_error, 404: _not_found},
        lifespan=lifespan,
    )
    app.state.mb = State(
        cfg,
        reg,
        client,
        fetch_client,
        token,
        origins,
        vision_client,
        file_data=file_data,
        own_clients=own_client,
    )
    return app


def public_origin(url: str) -> tuple[str, str]:
    """The origin (`https://name[:port]`) and host name of a public URL. Raises ValueError for anything else."""
    from urllib.parse import urlsplit

    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError(f"--public-url must be an http(s) URL with a host name, not {url!r}")
    return f"{u.scheme}://{u.netloc}", u.hostname


def run(
    cfg: Config,
    host: str = "127.0.0.1",
    port: int = 8765,
    token: bool = False,
    open_browser: bool = False,
    public_url: str = "",
    file_data: dict | None = None,
) -> int:
    """Serve the web UI until Ctrl+C (or the service stops). Returns the exit code.

    On a loopback address no token is needed unless `token`. On any other address, or behind a reverse proxy
    (`public_url`, the address people open), a token is always needed: it comes from MANUALBEAGLE_WEB_TOKEN, or
    a new random one. The printed link carries a random token, but not one from the environment, so a
    service's journal never holds it.
    """
    import sys

    import uvicorn

    loopback = host in LOOPBACK
    origins: list[str] = []
    hosts = list(LOOPBACK) if loopback else ["*"]
    if public_url:
        try:
            origin, name = public_origin(public_url)
        except ValueError as e:
            sys.stdout.write(f"✗ {e}\n")
            return 2
        origins.append(origin)
        if loopback:
            hosts.append(name)
    secret, from_env = "", False
    if token or not loopback or public_url:
        from_env = bool(os.environ.get("MANUALBEAGLE_WEB_TOKEN"))
        secret = os.environ.get("MANUALBEAGLE_WEB_TOKEN") or secrets.token_urlsafe(24)
    shown = f"[{host}]" if ":" in host and not host.startswith("[") else host
    base = origins[0] + "/" if origins else f"http://{shown}:{port}/"
    url = base + (f"?token={secret}" if secret and not from_env else "")
    app = create_app(cfg, token=secret, hosts=hosts, origins=origins, file_data=file_data)
    from .integrations import PaperlessAuto

    app.state.mb.paperless_auto = PaperlessAuto(app.state.mb).start()  # files new manuals while auto is on
    sys.stdout.write(f"Manual Beagle {__version__}: open {url}\n")
    if from_env:
        sys.stdout.write("  with ?token=<MANUALBEAGLE_WEB_TOKEN> the first time on each browser\n")
    if public_url:
        sys.stdout.write(f"  listening on {host}:{port} behind {origins[0]}\n")
    elif not loopback:
        sys.stdout.write(
            "▲ listening beyond this computer: anyone with the link can read your library and run lookups\n"
        )
        sys.stdout.write(
            "▲ plain HTTP: a phone needs HTTPS to install the app; see --public-url in docs/web.md\n"
        )
    sys.stdout.write("Ctrl+C stops it.\n")
    sys.stdout.flush()
    if open_browser:
        import webbrowser

        webbrowser.open(url if not from_env else base)
    uvicorn.run(app, host=host.strip("[]"), port=port, log_level="warning", access_log=False)
    return 0
