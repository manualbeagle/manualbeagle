"""The web UI's Home Assistant and Paperless-ngx endpoints, and the background filing of new manuals.

Home Assistant (read only):
- GET  /api/homeassistant          whether it's set up and answering, and its version
- GET  /api/homeassistant/devices  every device and what an import would do with it (`plan_import`)
- POST /api/homeassistant/import {device_ids: [...]}   add those devices to Homebox as new items. The server reads
  Home Assistant and Homebox again and adds only devices that are still "new", so the browser sends ids, never
  names or models. The page asks before it sends this, for one item or for all after the review.

Paperless-ngx:
- GET  /api/paperless              whether it's set up, its version, and how many manuals were sent or not yet
- POST /api/paperless/send {sha256} or {all: true}   a job that files one manual, or every one not sent yet

With `[paperless] auto` on, `run()` starts `PaperlessAuto`, a thread that files new manuals a few minutes after they
reach the library (sooner after the page's Fetch button), whatever downloaded them.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..sinks.homebox import HomeboxError
from ..sinks.paperless import (
    Ledger,
    PaperlessError,
    check_queued,
    file_document,
    sweep,
)
from ..sinks.paperless import from_config as paperless_from_config
from ..sources.homeassistant import (
    HomeAssistantError,
    add_to_homebox,
    plan_import,
    registry_brands,
)
from ..sources.homeassistant import from_config as ha_from_config
from ..sources.homeassistant import summary as ha_summary
from ..sync import homebox
from .jobs import Busy, Job

MAX_IMPORT = 500
AUTO_EVERY = 300  # seconds between the background filer's looks at the library


def _app():
    from . import app  # the app module imports this one inside create_app, so import it late

    return app


# Home Assistant


async def api_homeassistant(request: Request) -> Response:
    """GET /api/homeassistant: whether Home Assistant is set up and answering, and its version."""
    st = _app()._state(request)
    url = st.cfg.home_assistant.url
    if not url:
        return JSONResponse(
            {"configured": False, "ok": False, "error": "no [home_assistant] url in the config"}
        )

    def work() -> dict:
        try:
            version = ha_from_config(st.cfg).status()
            return {"configured": True, "ok": True, "url": url, "version": version}
        except HomeAssistantError as e:
            return {
                "configured": bool(st.cfg.home_assistant.token()),
                "ok": False,
                "url": url,
                "error": str(e),
            }

    return JSONResponse(await run_in_threadpool(work))


def _offers(st: Any) -> tuple[list, bool, str]:
    """Read Home Assistant's devices and Homebox's items; plan the import. Raises ApiError when HA fails.

    Returns the offers, whether Homebox answered, and Homebox's error when it didn't.
    """
    app = _app()
    try:
        devices = ha_from_config(st.cfg).devices()
    except HomeAssistantError as e:
        raise app.ApiError(502, f"Home Assistant: {e}") from None
    items, hb_ok, hb_error = [], False, ""
    try:
        hb = homebox(st.cfg)
        try:
            items, hb_ok = hb.items(), True
        finally:
            hb.close()
    except (HomeboxError, httpx.HTTPError, OSError) as e:
        hb_error = str(e)
    known = registry_brands(st.reg)
    return plan_import(devices, items, st.cfg.home_assistant.skip_integrations, known), hb_ok, hb_error


async def api_homeassistant_devices(request: Request) -> Response:
    """GET /api/homeassistant/devices: every device, and whether an import would add it, it's in Homebox, or skip it."""
    st = _app()._state(request)
    offers, hb_ok, hb_error = await run_in_threadpool(_offers, st)
    return JSONResponse(
        {
            "offers": [o.as_dict() for o in offers],
            "summary": ha_summary(offers),
            "homebox": hb_ok,
            "homebox_error": hb_error,
        }
    )


async def api_homeassistant_import(request: Request) -> Response:
    """POST /api/homeassistant/import {device_ids}: add those devices, if still new, to Homebox as items."""
    app = _app()
    st = app._state(request)
    body = await app._json(request)
    ids = body.get("device_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and len(i) <= 100 for i in ids):
        raise app.ApiError(400, "send {device_ids: [the devices to add]}")
    if len(ids) > MAX_IMPORT:
        raise app.ApiError(400, f"at most {MAX_IMPORT} devices at a time")
    wanted = set(ids)

    def work() -> dict:
        offers, hb_ok, hb_error = _offers(st)
        if not hb_ok:
            raise app.ApiError(502, f"Homebox: {hb_error}")
        chosen = [o for o in offers if o.device.id in wanted and o.action == "new"]
        hb = homebox(st.cfg)
        rows = []
        try:
            for o, item_id, error in add_to_homebox(hb, chosen):
                rows.append({**o.as_dict(), "done": not error, "item_id": item_id, "error": error})
        finally:
            hb.close()
        not_new = len(wanted) - len(chosen)
        made = sum(1 for r in rows if r["done"])
        return {
            "rows": rows,
            "summary": f"✓ {made} added to Homebox"
            + (f", ✗ {len(rows) - made} refused" if len(rows) > made else "")
            + (f", – {not_new} no longer new (skipped)" if not_new else ""),
        }

    return JSONResponse(await run_in_threadpool(work))


# Paperless-ngx


async def api_paperless(request: Request) -> Response:
    """GET /api/paperless: whether Paperless is set up and answering, and what was sent to it."""
    st = _app()._state(request)
    cfg = st.cfg
    if not cfg.paperless.url:
        return JSONResponse({"configured": False, "ok": False, "error": "no [paperless] url in the config"})

    def work() -> dict:
        out: dict[str, Any] = {"configured": True, "url": cfg.paperless.url, "auto": cfg.paperless.auto}
        try:
            pl = paperless_from_config(cfg)
        except PaperlessError as e:
            return {**out, "configured": False, "ok": False, "error": str(e)}
        try:
            with st.library() as lib:
                ledger = Ledger(lib.db)
                out["version"] = pl.status()
                check_queued(pl, ledger)
                out["counts"] = ledger.counts()
                out["unsent"] = len(ledger.unsent(lib.documents()))
            out["ok"] = True
        except PaperlessError as e:
            out.update(ok=False, error=str(e))
        finally:
            pl.close()
        return out

    return JSONResponse(await run_in_threadpool(work))


async def api_paperless_send(request: Request) -> Response:
    """POST /api/paperless/send {sha256} | {all: true}: a job filing one manual, or every one not sent yet."""
    app = _app()
    st = app._state(request)
    body = await app._json(request)
    sha = app._text(body, "sha256", limit=64)
    if not sha and body.get("all") is not True:
        raise app.ApiError(400, "send {sha256: a document} or {all: true}")
    if sha and not app.SHA.match(sha):
        raise app.ApiError(404, "no such document")
    try:
        paperless_from_config(st.cfg).close()
    except PaperlessError as e:
        raise app.ApiError(400, str(e)) from None
    with st.library() as lib:
        ledger = Ledger(lib.db)
        docs = lib.documents()
        todo = [d for d in docs if d["sha256"] == sha] if sha else ledger.unsent(docs)
    if sha and not todo:
        raise app.ApiError(404, "no such document")
    if not todo:
        raise app.ApiError(400, "every manual is in Paperless already")
    cfg = st.cfg

    def work(job: Job):
        pl = paperless_from_config(cfg)
        try:
            with st.library() as lib:
                ledger = Ledger(lib.db)
                for d in todo:
                    yield file_document(
                        pl, ledger, d, cfg.paperless.document_type, cfg.paperless.tags, 90 if sha else 0
                    ).as_dict()
        finally:
            pl.close()

    def summarise(job: Job) -> str:
        n = {s: sum(1 for r in job.rows if r["state"] == s) for s in ("filed", "queued", "exists", "failed")}
        return f"✓ {n['filed']} filed, … {n['queued']} queued, = {n['exists']} there already, ✗ {n['failed']} failed"

    try:
        job = st.jobs.start("paperless", len(todo), work, summarise)
    except Busy as e:
        raise app.ApiError(409, str(e)) from None
    return JSONResponse(job.public(), 202)


class PaperlessAuto:
    """Files new library manuals in Paperless in the background while `[paperless] auto` is on.

    It looks every `every` seconds, and at once after `wake()`. It waits while a job runs, so it never competes
    with a batch or sync for the library. The setting is read each time, so the Settings page turns it on or off.
    """

    def __init__(self, st: Any, every: float = AUTO_EVERY) -> None:
        self.st = st
        self.every = every
        self._wake = threading.Event()
        self.last: list = []
        self.error = ""

    def wake(self) -> None:
        """Look now (after a download)."""
        self._wake.set()

    def once(self) -> None:
        """One look: file what's new, if auto is on and no job runs."""
        cfg = self.st.cfg
        if not (cfg.paperless.auto and cfg.paperless.url) or self.st.jobs.running():
            return
        try:
            with self.st.library() as lib:
                self.last = sweep(cfg, lib)
            self.error = ""
        except (PaperlessError, OSError) as e:
            self.error = str(e)

    def loop(self) -> None:
        """Run forever (a daemon thread)."""
        while True:
            self._wake.wait(self.every)
            self._wake.clear()
            try:
                self.once()
            except Exception as e:  # never let the thread die; the next look tries again
                self.error = f"{type(e).__name__}: {e}"

    def start(self) -> PaperlessAuto:
        """Start the loop in a daemon thread."""
        threading.Thread(target=self.loop, name="manualbeagle-paperless-auto", daemon=True).start()
        return self


ROUTES = [
    Route("/api/homeassistant", api_homeassistant),
    Route("/api/homeassistant/devices", api_homeassistant_devices),
    Route("/api/homeassistant/import", api_homeassistant_import, methods=["POST"]),
    Route("/api/paperless", api_paperless),
    Route("/api/paperless/send", api_paperless_send, methods=["POST"]),
]
