"""Paperless-ngx as a sink: file the library's manuals there, tagged with brand and model, without duplicates.

API used (all under /api, `Authorization: Token <token>`; a token comes from the user's profile in Paperless):
- GET  /api/documents/?checksum__iexact=<hash>   is this exact file there already? Paperless 3 stores the
                                                 original's SHA-256, Paperless 2 its MD5, so both are asked
- GET  /api/tags/?name__iexact=<name>           and POST /api/tags/ {name, matching_algorithm: 0}
- GET  /api/document_types/?name__iexact=<name> and POST /api/document_types/ {name, matching_algorithm: 0}
- POST /api/documents/post_document/            multipart: document, title, document_type, tags (repeated);
                                                returns the consumption task's id
- GET  /api/tasks/?task_id=<id>                 the task. API version 10 (Paperless 3) pages the answer and has
                                                 related_document_ids and result_data; 9 and older (Paperless 2)
                                                 give a list with related_document and result. Both are read.

Tags and the document type are made with matching "None" (0), so Paperless never puts them on other documents by
itself. A token whose user may not view documents (so it can't read the rest of Paperless) gets 403 for the checksum
check; then only the `paperless` table guards against sending a manual twice. Paperless 3 consumes a duplicate file unless PAPERLESS_CONSUMER_DELETE_DUPLICATES is set, which is why
Manual Beagle checks the checksum first and keeps a table of what it sent (`paperless` in the library's database).

Verified against Paperless-ngx 3.4.1's source (documents/filters.py, serialisers.py, consumer.py, views.py), 2026-11-03.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from .. import __version__
from ..models import KIND_LABELS, DocKind

MATCH_NONE = 0
SCHEMA = """
CREATE TABLE IF NOT EXISTS paperless (
    sha256 TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    state TEXT NOT NULL,
    task_id TEXT,
    document_id INTEGER,
    message TEXT,
    sent_at REAL
);
"""


class PaperlessError(Exception):
    """Paperless refused a request, didn't answer, or isn't configured."""


class CantLook(Exception):
    """This token may not list Paperless's documents, so it can't check for a copy there."""


@dataclass
class Filed:
    """What happened to one library document: filed, already there, queued, or failed."""

    sha256: str
    title: str
    state: str  # filed | exists | queued | failed
    message: str = ""
    document_id: int | None = None
    task_id: str = ""

    def as_dict(self) -> dict:
        """The result as JSON for the web UI."""
        return asdict(self)


class Paperless:
    """A client for Paperless-ngx's REST API, authenticated with a user's API token."""

    def __init__(
        self, url: str, token: str, timeout: float = 120, transport: httpx.BaseTransport | None = None
    ):
        if not url or not token:
            raise PaperlessError(
                "Paperless-ngx needs a URL and an API token (config [paperless] url, and token_env or token_file)"
            )
        self.base = url.strip().rstrip("/") + "/api"
        self.http = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Token {token}",
                "Accept": "application/json",
                "User-Agent": f"ManualBeagle/{__version__}",
            },
        )
        self._ids: dict[tuple[str, str], int] = {}
        self.version = ""
        self.cant_look = False  # set once the token turns out not to see documents (no "view documents")

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self.http.close()

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = self.http.request(method, self.base + path, **kw)
        except httpx.HTTPError as e:
            raise PaperlessError(f"Paperless didn't answer: {e}") from None
        self.version = r.headers.get("x-version", self.version)
        if r.status_code >= 400:
            raise PaperlessError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
        try:
            return r.json() if r.content else None
        except ValueError:
            raise PaperlessError(f"{method} {path}: the answer isn't JSON") from None

    def status(self) -> str:
        """Check the token with one small request; return Paperless's version (its X-Version header)."""
        self._req("GET", "/tags/", params={"page_size": 1})
        return self.version or "?"

    def find_file(self, path: Path) -> int | None:
        """The id of the document whose original is this exact file, or None.

        Raises CantLook when this token may not list documents (a user without "view documents", so it can't read
        the rest of Paperless); the caller then relies on its own record of what it sent.
        """
        if self.cant_look:
            raise CantLook()
        data = path.read_bytes()
        for digest in (hashlib.sha256(data).hexdigest(), hashlib.md5(data).hexdigest()):
            try:
                res = self._req("GET", "/documents/", params={"checksum__iexact": digest, "page_size": 1})
            except PaperlessError as e:
                if " HTTP 403 " in str(e):
                    self.cant_look = True
                    raise CantLook() from None
                raise
            found = (res or {}).get("results") or []
            if found:
                return int(found[0]["id"])
        return None

    def _named(self, kind: str, name: str) -> int:
        """The id of the tag or document type `name` (`kind` is "tags" or "document_types"), made when missing."""
        key = (kind, name.lower())
        if key not in self._ids:
            res = self._req("GET", f"/{kind}/", params={"name__iexact": name, "page_size": 1})
            found = (res or {}).get("results") or []
            if found:
                self._ids[key] = int(found[0]["id"])
            else:
                made = self._req("POST", f"/{kind}/", json={"name": name, "matching_algorithm": MATCH_NONE})
                self._ids[key] = int(made["id"])
        return self._ids[key]

    def tag(self, name: str) -> int:
        """The id of the tag `name`, made (matching none) when Paperless has none of that name."""
        return self._named("tags", name)

    def document_type(self, name: str) -> int:
        """The id of the document type `name`, made (matching none) when missing."""
        return self._named("document_types", name)

    def upload(self, path: Path, title: str, tags: list[int], document_type: int | None) -> str:
        """Send a PDF to be consumed; return the consumption task's id."""
        data: dict[str, Any] = {"title": title[:128], "tags": [str(t) for t in tags]}
        if document_type:
            data["document_type"] = str(document_type)
        with open(path, "rb") as f:
            res = self._req(
                "POST",
                "/documents/post_document/",
                files={"document": (path.name, f, "application/pdf")},
                data=data,
            )
        task = res.get("task_id") if isinstance(res, dict) else res
        if not task:
            raise PaperlessError(f"post_document gave no task id: {str(res)[:200]}")
        return str(task)

    def task(self, task_id: str) -> dict:
        """The consumption task: `status` (SUCCESS, FAILURE, PENDING, STARTED, REVOKED), `document`, `result`.

        `document` is the new document's id, `duplicate_of` the id of the one a duplicate matched, when known.
        """
        res = self._req("GET", "/tasks/", params={"task_id": task_id})
        rows = res.get("results", []) if isinstance(res, dict) else res or []
        if not rows:
            return {"status": "PENDING", "document": None, "result": "", "duplicate_of": None}
        t = rows[0]
        data = t.get("result_data") or {}
        ids = t.get("related_document_ids") or []
        dups = t.get("duplicate_documents") or []
        result = t.get("result") or data.get("reason") or data.get("error_message") or ""
        if not result and data.get("duplicate_of"):
            result = f"It is a duplicate of document #{data['duplicate_of']}"
        return {
            "status": str(t.get("status") or "PENDING").upper(),
            "document": t.get("related_document") or (ids[0] if ids else None) or data.get("document_id"),
            "result": str(result),
            "duplicate_of": data.get("duplicate_of")
            or (dups[0] if dups and isinstance(dups[0], int) else None),
        }


def title_for(brand: str, model: str, kind: str | None) -> str:
    """The title a manual gets in Paperless: 'Brand Model - Kind', such as 'LG WM4000HWA - Owner's manual'."""
    label = KIND_LABELS.get(DocKind(kind), "Manual") if kind in {k.value for k in DocKind} else "Manual"
    return f"{brand} {model} - {label}".strip()


class Ledger:
    """What Manual Beagle sent to Paperless, kept in the library's SQLite database (the `paperless` table)."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.db.executescript(SCHEMA)

    def get(self, sha: str) -> dict | None:
        """The row for a library document, or None."""
        cur = self.db.execute("SELECT * FROM paperless WHERE sha256=?", (sha,))
        row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row, strict=True)) if row else None

    def put(self, f: Filed, checksum: str) -> None:
        """Record what happened to a document."""
        self.db.execute(
            "INSERT OR REPLACE INTO paperless VALUES (?,?,?,?,?,?,?)",
            (f.sha256, checksum, f.state, f.task_id, f.document_id, f.message, time.time()),
        )
        self.db.commit()

    def unsent(self, documents: list[dict]) -> list[dict]:
        """The documents with no row, or that failed, or whose file changed since it was sent (OCR rewrites it).

        A file is hashed again only when it was modified after it was sent.
        """
        out = []
        for d in documents:
            row = self.get(d["sha256"])
            path = Path(d["path"])
            changed = (
                row is not None
                and path.exists()
                and path.stat().st_mtime > (row["sent_at"] or 0)
                and row["checksum"] != _sha256(path)
            )
            if row is None or row["state"] == "failed" or changed:
                out.append(d)
        return out

    def counts(self) -> dict[str, int]:
        """How many documents are in each state."""
        return dict(self.db.execute("SELECT state, count(*) FROM paperless GROUP BY state").fetchall())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_document(
    pl: Paperless,
    ledger: Ledger,
    doc: dict,
    document_type: str = "Manual",
    tags: list[str] | None = None,
    wait: float = 0,
) -> Filed:
    """File one library document (a row of `Library.documents()`) in Paperless, unless it's there already.

    Tags: the brand, the model and `tags`. Waits up to `wait` seconds for Paperless to consume it, polling its task;
    without waiting, the result is "queued" and `check_queued` finishes it later. Raises nothing for Paperless's
    refusals: they come back as "failed" with the message.
    """
    path = Path(doc["path"])
    title = title_for(doc.get("brand") or "", doc.get("model") or "", doc.get("kind"))
    f = Filed(sha256=doc["sha256"], title=title, state="failed")
    if not path.exists():
        f.message = f"the library's file is missing: {path}"
        return f
    checksum = _sha256(path)
    try:
        try:
            existing = pl.find_file(path)
            looked = True
        except CantLook:
            existing, looked = None, False
        if existing is not None:
            f.state, f.document_id, f.message = "exists", existing, f"Paperless has it already (#{existing})"
        else:
            names = [n for n in (doc.get("brand"), doc.get("model"), *(tags or [])) if n]
            tag_ids = list(dict.fromkeys(pl.tag(str(n)) for n in names))
            type_id = pl.document_type(document_type) if document_type else None
            f.task_id = pl.upload(path, title, tag_ids, type_id)
            f.state, f.message = "queued", "sent; Paperless is consuming it"
            if not looked:
                f.message += (
                    " (this token can't list Paperless's documents: only Manual Beagle's record was checked)"
                )
            if wait > 0:
                finish(pl, f, wait)
    except PaperlessError as e:
        f.state, f.message = "failed", str(e)
    ledger.put(f, checksum)
    return f


def finish(pl: Paperless, f: Filed, wait: float, every: float = 2) -> None:
    """Poll a queued document's task for up to `wait` seconds and record how it ended in `f`."""
    deadline = time.monotonic() + wait
    while True:
        t = pl.task(f.task_id)
        if t["status"] == "SUCCESS":
            f.state, f.document_id = "filed", int(t["document"]) if t["document"] else None
            f.message = f"filed as #{f.document_id}" if f.document_id else "filed"
            return
        if t["status"] in ("FAILURE", "REVOKED"):
            dup = t["duplicate_of"] or ("duplicate" in t["result"].lower())
            f.state, f.message = ("exists" if dup else "failed"), t["result"][:300] or t["status"].lower()
            if isinstance(t["duplicate_of"], int):
                f.document_id = t["duplicate_of"]
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(every)


def check_queued(pl: Paperless, ledger: Ledger) -> list[Filed]:
    """Look up every queued document's task once and record the ones that ended."""
    done = []
    rows = ledger.db.execute("SELECT * FROM paperless WHERE state='queued'").fetchall()
    for sha, checksum, _state, task_id, _doc, _msg, _at in rows:
        f = Filed(sha256=sha, title="", state="queued", task_id=task_id or "")
        try:
            finish(pl, f, 0)
        except PaperlessError as e:
            f.message = str(e)
            continue
        if f.state != "queued":
            ledger.put(f, checksum)
            done.append(f)
    return done


def send(
    pl: Paperless,
    ledger: Ledger,
    documents: list[dict],
    document_type: str = "Manual",
    tags: list[str] | None = None,
    wait: float = 0,
) -> Iterator[Filed]:
    """File each of `documents` (rows of `Library.documents()`), one at a time."""
    for doc in documents:
        yield file_document(pl, ledger, doc, document_type, tags, wait)


def from_config(cfg: Any) -> Paperless:
    """A client from the config's `[paperless]` table. Raises PaperlessError when it isn't set up.

    Also when the address was changed on the Settings page alone: the token goes only to the address set beside it.
    """
    t = cfg.paperless
    held = t.key_held_back(t.url)
    if held:
        raise PaperlessError(f"not sending: {held}")
    return Paperless(t.url, t.token())


def sweep(cfg: Any, lib: Any, wait: float = 0) -> list[Filed]:
    """File every library document Paperless doesn't have yet, as `[paperless] auto` does after downloads.

    First records how earlier queued sends ended. `lib` is an open Library. Raises PaperlessError when Paperless
    isn't set up; a refusal for one document comes back as a "failed" Filed instead.
    """
    pl = from_config(cfg)
    try:
        ledger = Ledger(lib.db)
        check_queued(pl, ledger)
        todo = ledger.unsent(lib.documents())
        return list(send(pl, ledger, todo, cfg.paperless.document_type, cfg.paperless.tags, wait))
    finally:
        pl.close()
