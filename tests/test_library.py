"""The library end to end, offline: a generated PDF goes in, comes out searchable by page, and exports to a folder."""

from pathlib import Path

from manualbeagle.http import Response
from manualbeagle.library import Library, pdf_pages
from manualbeagle.models import Candidate, DocKind, Query
from manualbeagle.normalize import normalise
from manualbeagle.sinks.folder import export


def make_pdf(pages: list[str]) -> bytes:
    """A minimal PDF with one line of Helvetica text per page."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    font = 3
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content = len(objs)
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R "
            f"/Resources << /Font << /F1 {font} 0 R >> >> >>"
        )
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class FakeHTTP:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def download(self, url: str, dest: Path) -> Response:
        dest.write_bytes(self.body)
        return Response(200, url, {"content-type": "application/pdf"}, self.body[:4096])


def test_fetch_index_search_export(tmp_path):
    pdf = make_pdf(["OWNER'S MANUAL WM5700HVA washer", "Clean the door seal every month", "Warranty"])
    src = tmp_path / "x.pdf"
    src.write_bytes(pdf)
    assert "WM5700HVA" in pdf_pages(src)[0]

    lib = Library(tmp_path / "library", tmp_path / "db.sqlite")
    q = normalise(Query(brand="LG", model="WM5700HVA"))
    c = Candidate(url="https://example.com/m.pdf", title="Owner's Manual", kind=DocKind.owners_manual)
    doc = lib.fetch(c, q, FakeHTTP(pdf))
    assert doc.page_count == 3
    assert doc.check_score > 0 and "PDF text names the model +40" in doc.check_reasons

    hits = lib.search("seal")
    assert hits and hits[0]["page"] == 2 and hits[0]["model"] == "WM5700HVA"
    assert lib.read_pages(doc.sha256, [2])[2].startswith("Clean the door seal")

    again = lib.fetch(c, q, FakeHTTP(pdf))  # the same bytes are stored once
    assert again.sha256 == doc.sha256 and len(lib.documents()) == 1

    files = export(lib, tmp_path / "out")
    assert files == [tmp_path / "out" / "LG" / "WM5700HVA - Owner's manual.pdf"]
    assert files[0].with_suffix(".json").exists()
    lib.close()


def test_html_saved_as_pdf_is_refused(tmp_path):
    import pytest

    from manualbeagle.http import FetchError

    lib = Library(tmp_path / "library", tmp_path / "db.sqlite")
    with pytest.raises(FetchError, match="not a PDF"):
        lib.fetch(
            Candidate(url="https://example.com/m.pdf"),
            normalise(Query(brand="X", model="Y1")),
            FakeHTTP(b"<html>"),
        )
    lib.close()


def test_ocr_only_when_pages_lack_text(monkeypatch, tmp_path):
    from manualbeagle import library

    assert library.needs_ocr([])
    assert library.needs_ocr(["", "", "Some text on page three that is long enough"])
    assert not library.needs_ocr(["A page with plenty of text on it", "Another page with plenty of text"])
    monkeypatch.setattr(library, "ocrmypdf_command", lambda: None)
    assert library.ocr(tmp_path / "x.pdf") is None  # no OCRmyPDF installed: skip OCR, keep the PDF as it is
    assert not library.ocr_available()


def test_ocr_finds_tesseract_in_its_windows_folder(monkeypatch, tmp_path):
    from manualbeagle import library

    (tmp_path / "Tesseract-OCR").mkdir()
    (tmp_path / "Tesseract-OCR" / "tesseract.exe").write_bytes(b"")
    monkeypatch.setattr(library.shutil, "which", lambda name: None)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    # windows=True rather than faking os.name, which makes pathlib build WindowsPaths (impossible on Linux CI).
    assert library.tesseract_folder(windows=True) == tmp_path / "Tesseract-OCR"
    assert library.tesseract_folder(windows=False) is None


def test_ocr_missing_reindexes_documents_without_text(monkeypatch, tmp_path):
    from manualbeagle import library

    blank = make_pdf(["", ""])
    lib = Library(tmp_path / "library", tmp_path / "db.sqlite")
    q = normalise(Query(brand="Hatch", model="Rest+"))
    doc = lib.fetch(Candidate(url="https://example.com/r.pdf"), q, FakeHTTP(blank), use_ocr=False)
    assert lib.search("sunrise") == []

    def fake_ocr(path):
        out = path.with_suffix(".ocr.pdf")
        out.write_bytes(
            make_pdf(["Rest+ sunrise night light, getting started", "Plug it in and open the app"])
        )
        return out

    monkeypatch.setattr(library, "ocr", fake_ocr)
    done = lib.ocr_missing()
    assert [d["pages_with_text"] for d in done] == [2] and done[0]["sha256"] == doc.sha256
    assert lib.search("sunrise")[0]["page"] == 1
    assert lib.ocr_missing() == []  # marked as OCRed: not tried again
    lib.close()
