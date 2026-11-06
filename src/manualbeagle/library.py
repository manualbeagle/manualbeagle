"""The local library: PDFs named by content hash, and a SQLite database with a per-page full-text index."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .http import FetchError, PoliteClient
from .models import Candidate, Query
from .verify import check_pdf_text

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    sha256 TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT,
    kind TEXT,
    brand TEXT,
    model TEXT,
    page_count INTEGER,
    ocr INTEGER DEFAULT 0,
    fetched_at REAL
);
CREATE TABLE IF NOT EXISTS links (
    sha256 TEXT NOT NULL,
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    item TEXT,
    PRIMARY KEY (sha256, brand, model, item)
);
CREATE TABLE IF NOT EXISTS photos (
    sha256 TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    brand TEXT,
    model TEXT,
    serial TEXT,
    ocr_text TEXT,
    taken_at REAL
);
CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(
    text, sha256 UNINDEXED, page UNINDEXED, brand UNINDEXED, model UNINDEXED, kind UNINDEXED,
    tokenize = 'porter unicode61'
);
"""


@dataclass
class Document:
    """A PDF in the library: where it is and came from, its per-page text, and the text check's result."""

    sha256: str
    path: Path
    source_url: str
    title: str
    kind: str | None
    brand: str
    model: str
    pages: list[str]
    ocr: bool = False
    check_score: float = 0.0
    check_reasons: list[str] | None = None

    @property
    def page_count(self) -> int:
        """The number of pages."""
        return len(self.pages)


def pdf_pages(path: Path) -> list[str]:
    """The text of every page of a PDF, extracted with pypdfium2 (which runs no JavaScript)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        out = []
        for i in range(len(pdf)):
            page = pdf[i]
            tp = page.get_textpage()
            out.append(tp.get_text_range() or "")
            tp.close()
            page.close()
        return out
    finally:
        pdf.close()


def ocrmypdf_command() -> str | None:
    """The `ocrmypdf` command: on PATH, or beside this Python (installed into the same virtual environment)."""
    found = shutil.which("ocrmypdf")
    if found:
        return found
    beside = Path(sys.executable).parent / ("ocrmypdf.exe" if os.name == "nt" else "ocrmypdf")
    return str(beside) if beside.exists() else None


def tesseract_folder(windows: bool | None = None) -> Path | None:
    """The folder holding Tesseract, which OCRmyPDF runs: on PATH, or where its Windows installer puts it.

    The Windows installer doesn't add itself to PATH, so on Windows (`windows`, default: this system) the usual
    install folders are tried too.
    """
    found = shutil.which("tesseract")
    if found:
        return Path(found).parent
    if not (os.name == "nt" if windows is None else windows):
        return None
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
        for sub in ("Tesseract-OCR", "Programs/Tesseract-OCR"):
            folder = Path(base or "") / sub
            if base and (folder / "tesseract.exe").exists():
                return folder
    return None


def ocr_available() -> bool:
    """Whether OCR can run: OCRmyPDF and Tesseract are both installed (Ghostscript isn't needed for plain PDFs)."""
    return ocrmypdf_command() is not None and tesseract_folder() is not None


def ocr(path: Path) -> Path | None:
    """OCR only the pages with no text (--skip-text). Returns the new file, or None when OCR isn't installed."""
    command, tesseract = ocrmypdf_command(), tesseract_folder()
    if not command or not tesseract:
        return None
    out = path.with_suffix(".ocr.pdf")
    env = dict(os.environ, PATH=str(tesseract) + os.pathsep + os.environ.get("PATH", ""))
    r = subprocess.run(
        # --output-type pdf: plain PDF, not PDF/A, so Ghostscript isn't needed (OCRmyPDF 17 rasterizes with pypdfium).
        [command, "--skip-text", "--output-type", "pdf", "--quiet", str(path), str(out)],
        capture_output=True,
        timeout=1800,
        env=env,
    )
    if r.returncode not in (0, 6) or not out.exists():  # 6: the file already had text
        return None
    return out


def needs_ocr(pages: list[str]) -> bool:
    """True when the PDF has no pages, or more than 30% of its pages have almost no text."""
    if not pages:
        return True
    empty = sum(1 for p in pages if len(p.strip()) < 20)
    return empty / len(pages) > 0.3


class Library:
    """The local library: content-addressed PDFs under `root` and a SQLite database indexing every page."""

    def __init__(self, root: Path, db_path: Path | None = None) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path or root.parent / "manualbeagle.sqlite")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        """Close the database."""
        self.db.close()

    def fetch(
        self, c: Candidate, q: Query, http: PoliteClient, item: str | None = None, use_ocr: bool = True
    ) -> Document:
        """Download a candidate, store it by content hash, extract and index its text, and check it.

        OCRs pages without text when `use_ocr` and OCRmyPDF is installed. A document already in the library
        isn't indexed again. `item` records which inventory item the document belongs to. Returns the Document
        with the text check's score and reasons. Raises FetchError for link-only candidates, HTTP errors and
        non-PDF answers.
        """
        if c.link_only:
            raise FetchError(f"{c.url} is link-only: open it in a browser")
        tmpdir = Path(tempfile.mkdtemp(prefix="mb-", dir=self.root))
        try:
            tmp = tmpdir / "download.pdf"
            r = http.download(c.url, tmp)
            if not r.ok:
                raise FetchError(f"HTTP {r.status} for {c.url}")
            if r.content[:5] != b"%PDF-":
                raise FetchError(f"not a PDF ({r.content_type or 'unknown type'}): {c.url}")
            sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
            dest = self.root / sha[:2] / f"{sha}.pdf"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                os.replace(tmp, dest)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        row = self.db.execute("SELECT page_count, ocr FROM documents WHERE sha256=?", (sha,)).fetchone()
        texts = self.page_texts(sha) if row else []
        did_ocr = bool(row and row[1])
        if not row:
            texts = pdf_pages(dest)
            if use_ocr and needs_ocr(texts):
                ocred = ocr(dest)
                if ocred:
                    os.replace(ocred, dest)
                    texts = pdf_pages(dest)
                    did_ocr = True
            self._index(sha, dest, c, q, texts, did_ocr)
        self.db.execute(
            "INSERT OR IGNORE INTO links VALUES (?,?,?,?)",
            (sha, q.canonical_brand or q.brand, q.model, item or ""),
        )
        self.db.commit()
        s, reasons = check_pdf_text(texts, q)
        return Document(
            sha256=sha,
            path=dest,
            source_url=c.url,
            title=c.title,
            kind=c.kind.value if c.kind else None,
            brand=q.canonical_brand or q.brand,
            model=q.model,
            pages=texts,
            ocr=did_ocr,
            check_score=s,
            check_reasons=reasons,
        )

    def _index(self, sha: str, dest: Path, c: Candidate, q: Query, texts: list[str], did_ocr: bool) -> None:
        brand = q.canonical_brand or q.brand
        kind = c.kind.value if c.kind else None
        self.db.execute(
            "INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sha, str(dest), c.url, c.title, kind, brand, q.model, len(texts), int(did_ocr), time.time()),
        )
        self.db.execute("DELETE FROM pages WHERE sha256=?", (sha,))
        self.db.executemany(
            "INSERT INTO pages (text, sha256, page, brand, model, kind) VALUES (?,?,?,?,?,?)",
            [(t, sha, i + 1, brand, q.model, kind) for i, t in enumerate(texts)],
        )
        meta = {"source_url": c.url, "title": c.title, "kind": kind, "brand": brand, "model": q.model}
        dest.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    def ocr_missing(self) -> list[dict]:
        """OCR the documents indexed without OCR whose pages lack text, and index their new text.

        For manuals fetched before OCRmyPDF was installed. Returns one dict per document tried: its `sha256`,
        `brand`, `model`, `path`, and `pages_with_text` after OCR, or `pages_with_text` None when OCR failed.
        """
        done = []
        rows = self.db.execute(
            "SELECT sha256, path, kind, brand, model FROM documents WHERE ocr=0"
        ).fetchall()
        for sha, path, kind, brand, model in rows:
            if not Path(path).exists() or not needs_ocr(self.page_texts(sha)):
                continue
            result = {"sha256": sha, "brand": brand, "model": model, "path": path, "pages_with_text": None}
            ocred = ocr(Path(path))
            if ocred:
                os.replace(ocred, path)
                texts = pdf_pages(Path(path))
                self.db.execute("UPDATE documents SET ocr=1, page_count=? WHERE sha256=?", (len(texts), sha))
                self.db.execute("DELETE FROM pages WHERE sha256=?", (sha,))
                self.db.executemany(
                    "INSERT INTO pages (text, sha256, page, brand, model, kind) VALUES (?,?,?,?,?,?)",
                    [(t, sha, i + 1, brand, model, kind) for i, t in enumerate(texts)],
                )
                self.db.commit()
                result["pages_with_text"] = sum(1 for t in texts if len(t.strip()) >= 20)
            done.append(result)
        return done

    def page_texts(self, sha: str) -> list[str]:
        """The indexed text of a document's pages, in page order."""
        rows = self.db.execute("SELECT page, text FROM pages WHERE sha256=? ORDER BY page", (sha,)).fetchall()
        return [t for _, t in rows]

    def documents(self) -> list[dict]:
        """Every document in the library, as dicts, ordered by brand and model."""
        cur = self.db.execute(
            "SELECT sha256, title, kind, brand, model, page_count AS pages, source_url, path FROM documents "
            "ORDER BY brand, model"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def linked(self, brand: str, model: str) -> list[dict]:
        """The documents fetched for this brand and model (any case), as `documents()` gives them."""
        cur = self.db.execute(
            "SELECT DISTINCT d.sha256, d.title, d.kind, d.brand, d.model, d.page_count AS pages, d.source_url, d.path "
            "FROM documents d JOIN links l ON l.sha256 = d.sha256 WHERE lower(l.brand) = lower(?) "
            "AND lower(l.model) = lower(?) ORDER BY d.fetched_at DESC",
            (brand, model),
        )
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def document(self, sha: str) -> dict | None:
        """One document's row (as `documents()` gives it, with `ocr` and `fetched_at`), or None."""
        cur = self.db.execute(
            "SELECT sha256, title, kind, brand, model, page_count AS pages, source_url, path, ocr, fetched_at "
            "FROM documents WHERE sha256=?",
            (sha,),
        )
        row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row, strict=True)) if row else None

    def search(
        self,
        query: str,
        brand: str | None = None,
        model: str | None = None,
        limit: int = 20,
        marks: tuple[str, str] = ("[", "]"),
        document: str | None = None,
    ) -> list[dict]:
        """Full-text search of every page (SQLite FTS5 syntax), best first, with a snippet per hit.

        `brand`, `model` and `document` (a sha256) narrow it. `marks` go before and after each matched word in
        the snippet. Raises sqlite3.OperationalError for a query that isn't valid FTS5 syntax.
        """
        sql = (
            "SELECT p.sha256, p.page, p.brand, p.model, p.kind, d.title, "
            "snippet(pages, 0, ?, ?, ' … ', 12) AS snip, bm25(pages) AS rank "
            "FROM pages p JOIN documents d ON d.sha256 = p.sha256 WHERE pages MATCH ?"
        )
        args: list = [marks[0], marks[1], query]
        if brand:
            sql += " AND p.brand = ? COLLATE NOCASE"
            args.append(brand)
        if model:
            sql += " AND p.model = ? COLLATE NOCASE"
            args.append(model)
        if document:
            sql += " AND p.sha256 = ?"
            args.append(document)
        sql += " ORDER BY rank LIMIT ?"
        args.append(limit)
        cur = self.db.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def read_pages(self, sha: str, pages: list[int]) -> dict[int, str]:
        """The text of the given 1-based pages of a document; pages out of range are left out."""
        texts = self.page_texts(sha)
        return {p: texts[p - 1] for p in pages if 1 <= p <= len(texts)}

    # Nameplate photos: rating plates photographed to read a brand and model, kept beside the manuals.

    PHOTO_COLUMNS = "sha256, media_type, brand, model, serial, ocr_text, taken_at"

    def add_photo(self, data: bytes, media_type: str, ocr_text: str = "") -> dict:
        """Keep a nameplate photo (JPEG or PNG bytes, metadata already stripped) and its OCR text.

        Stored by content hash under `photos/`, so the same photo sent twice is kept once (and keeps the brand
        and model confirmed for it). Returns the photo's row, as `photo()` gives it.
        """
        sha = hashlib.sha256(data).hexdigest()
        ext = ".png" if media_type == "image/png" else ".jpg"
        dest = self.root / "photos" / f"{sha}{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            tmp = dest.with_suffix(".part")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        self.db.execute(
            "INSERT OR IGNORE INTO photos (sha256, path, media_type, ocr_text, taken_at) VALUES (?,?,?,?,?)",
            (sha, str(dest), media_type, ocr_text, time.time()),
        )
        self.db.commit()
        row = self.photo(sha)
        assert row is not None
        return row

    def photos(self) -> list[dict]:
        """Every nameplate photo, newest first (without its OCR text or path)."""
        cur = self.db.execute(
            "SELECT sha256, media_type, brand, model, serial, taken_at FROM photos ORDER BY taken_at DESC"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    def photo(self, sha: str) -> dict | None:
        """One photo's row with its `path` and `ocr_text`, or None."""
        cur = self.db.execute(f"SELECT {self.PHOTO_COLUMNS}, path FROM photos WHERE sha256=?", (sha,))
        row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row, strict=True)) if row else None

    def label_photo(self, sha: str, brand: str, model: str, serial: str = "") -> bool:
        """Record the brand, model and serial a person confirmed for a photo. False when there's no such photo."""
        cur = self.db.execute(
            "UPDATE photos SET brand=?, model=?, serial=? WHERE sha256=?", (brand, model, serial, sha)
        )
        self.db.commit()
        return cur.rowcount > 0

    def delete_photo(self, sha: str) -> bool:
        """Remove a photo and its file. False when there's no such photo."""
        row = self.photo(sha)
        if row is None:
            return False
        path = Path(row["path"])
        if path.resolve().is_relative_to(self.root.resolve()):
            path.unlink(missing_ok=True)
        self.db.execute("DELETE FROM photos WHERE sha256=?", (sha,))
        self.db.commit()
        return True
