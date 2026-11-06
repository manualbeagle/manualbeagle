"""The Paperless-ngx sink, against a fake Paperless (shapes from Paperless-ngx 3.4.1's API, version 10)."""

import hashlib
import json
import os
import sqlite3
import time

import httpx
import pytest

from manualbeagle.cli import main
from manualbeagle.sinks import paperless as pl_mod
from manualbeagle.sinks.paperless import Ledger, Paperless, PaperlessError, file_document, sweep, title_for

PDF = b"%PDF-1.4 a washer manual"


class FakePaperless:
    """Just enough of Paperless's REST API: documents by checksum, tags, types, post_document and tasks."""

    def __init__(self, v9: bool = False) -> None:
        self.docs: dict[int, dict] = {}
        self.named = {"tags": {1: "manual"}, "document_types": {}}
        self.tasks: dict[str, dict] = {}
        self.posted: list[dict] = []
        self.v9 = v9
        self.consume = True  # tasks succeed at once

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Token secret"
        path, q = request.url.path, request.url.params
        h = {"X-Version": "3.4.1"}
        if path == "/api/documents/" and request.method == "GET":
            found = [{"id": i} for i, d in self.docs.items() if d["checksum"] == q.get("checksum__iexact")]
            return httpx.Response(200, json={"count": len(found), "results": found}, headers=h)
        for kind in ("tags", "document_types"):
            if path == f"/api/{kind}/" and request.method == "GET":
                name = (q.get("name__iexact") or "").lower()
                found = [
                    {"id": i, "name": n} for i, n in self.named[kind].items() if n.lower() == name or not name
                ]
                return httpx.Response(200, json={"results": found[:1]}, headers=h)
            if path == f"/api/{kind}/" and request.method == "POST":
                body = request.read()
                data = json.loads(body)
                assert data["matching_algorithm"] == 0  # never auto-assigned to other documents
                i = 100 + len(self.named[kind])
                self.named[kind][i] = data["name"]
                return httpx.Response(201, json={"id": i, "name": data["name"]}, headers=h)
        if path == "/api/documents/post_document/":
            body = request.read()
            self.posted.append({"body": body})
            task = f"task-{len(self.posted)}"
            data = body.split(b"%PDF", 1)[1].split(b"\r\n--", 1)[0]
            doc_id = 10 + len(self.posted)
            if self.consume:
                self.docs[doc_id] = {"checksum": hashlib.sha256(b"%PDF" + data).hexdigest()}
            self.tasks[task] = {"status": "success" if self.consume else "pending", "doc": doc_id}
            return httpx.Response(200, json=task, headers=h)
        if path == "/api/tasks/":
            t = self.tasks.get(q.get("task_id"))
            if t is None:
                rows = []
            elif self.v9:
                rows = [{"status": t["status"].upper(), "related_document": t["doc"], "result": "Success"}]
            else:
                rows = [{"status": t["status"], "related_document_ids": [t["doc"]], "result_data": {}}]
            return httpx.Response(
                200, json=rows if self.v9 else {"count": len(rows), "results": rows}, headers=h
            )
        return httpx.Response(404, json={"detail": "Not found."})


def client(fake: FakePaperless) -> Paperless:
    return Paperless("http://paperless.test:8000", "secret", transport=httpx.MockTransport(fake.handler))


def library_doc(tmp_path, body: bytes = PDF) -> dict:
    path = tmp_path / "doc.pdf"
    path.write_bytes(body)
    return {
        "sha256": hashlib.sha256(body).hexdigest(),
        "path": str(path),
        "brand": "LG",
        "model": "WM4000HWA",
        "kind": "owners_manual",
        "title": "Owner's manual",
    }


def test_title():
    assert title_for("LG", "WM4000HWA", "owners_manual") == "LG WM4000HWA - Owner's manual"
    assert title_for("LG", "WM4000HWA", None) == "LG WM4000HWA - Manual"


def test_files_a_manual_with_tags_and_type(tmp_path):
    fake = FakePaperless()
    pl = client(fake)
    ledger = Ledger(sqlite3.connect(":memory:"))
    f = file_document(pl, ledger, library_doc(tmp_path), "Manual", ["manual"], wait=5)
    assert (f.state, f.document_id) == ("filed", 11)
    assert pl.status() == "3.4.1"
    body = fake.posted[0]["body"]
    assert b"LG WM4000HWA - Owner's manual" in body
    assert sorted(fake.named["tags"].values()) == ["LG", "WM4000HWA", "manual"]  # "manual" existed already
    assert list(fake.named["document_types"].values()) == ["Manual"]
    assert ledger.get(f.sha256)["state"] == "filed"


def test_never_sends_a_file_paperless_has(tmp_path):
    fake = FakePaperless()
    doc = library_doc(tmp_path)
    fake.docs[7] = {"checksum": hashlib.sha256(PDF).hexdigest()}
    ledger = Ledger(sqlite3.connect(":memory:"))
    f = file_document(client(fake), ledger, doc)
    assert (f.state, f.document_id) == ("exists", 7)
    assert fake.posted == []
    assert ledger.unsent([doc]) == []  # and it isn't offered again


def test_queued_then_checked_later(tmp_path):
    fake = FakePaperless(v9=True)
    fake.consume = False
    pl = client(fake)
    ledger = Ledger(sqlite3.connect(":memory:"))
    doc = library_doc(tmp_path)
    f = file_document(pl, ledger, doc)
    assert f.state == "queued" and f.task_id == "task-1"
    fake.tasks["task-1"]["status"] = "success"
    done = pl_mod.check_queued(pl, ledger)
    assert [(d.state, d.document_id) for d in done] == [("filed", 11)]
    assert ledger.counts() == {"filed": 1}


def test_a_changed_file_is_offered_again(tmp_path):
    ledger = Ledger(sqlite3.connect(":memory:"))
    doc = library_doc(tmp_path)
    f = file_document(client(FakePaperless()), ledger, doc, wait=1)
    assert f.state == "filed" and ledger.unsent([doc]) == []
    (tmp_path / "doc.pdf").write_bytes(PDF + b" OCRed")  # OCR rewrites the library's file
    later = time.time() + 60
    os.utime(tmp_path / "doc.pdf", (later, later))  # a clock tick finer than the file system's
    assert ledger.unsent([doc]) == [doc]


def test_a_token_that_cant_list_documents_still_files(tmp_path):
    fake = FakePaperless()
    real = fake.handler

    def no_listing(request):
        if request.url.path == "/api/documents/":
            return httpx.Response(403, json={"detail": "You do not have permission to perform this action."})
        return real(request)

    pl = Paperless("http://paperless.test:8000", "secret", transport=httpx.MockTransport(no_listing))
    ledger = Ledger(sqlite3.connect(":memory:"))
    doc = library_doc(tmp_path)
    f = file_document(pl, ledger, doc, wait=1)
    assert (
        f.state == "filed" and "only Manual Beagle's record" not in f.message
    )  # filed overwrites the message
    assert pl.cant_look and len(fake.posted) == 1
    assert ledger.unsent([doc]) == []  # the record keeps it from being sent twice


def test_refusals_come_back_as_failed(tmp_path):
    def refuse(request):
        return httpx.Response(403, json={"detail": "You do not have permission"})

    pl = Paperless("http://p.test", "secret", transport=httpx.MockTransport(refuse))
    ledger = Ledger(sqlite3.connect(":memory:"))
    f = file_document(pl, ledger, library_doc(tmp_path))
    assert f.state == "failed" and "HTTP 403" in f.message
    assert ledger.unsent([library_doc(tmp_path)])  # tried again next time
    with pytest.raises(PaperlessError, match="URL and an API token"):
        Paperless("", "x")


def setup_cli(monkeypatch, tmp_path, fake, auto=False):
    from manualbeagle.library import Library

    real = pl_mod.Paperless

    monkeypatch.setattr(
        pl_mod, "Paperless", lambda url, token: real(url, token, transport=httpx.MockTransport(fake.handler))
    )
    monkeypatch.setenv("PAPERLESS_TOKEN", "secret")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'home = "{tmp_path.as_posix()}"\n[paperless]\nurl = "http://paperless.test:8000"\n'
        f"auto = {'true' if auto else 'false'}\n",
        "utf-8",
    )
    lib = Library(tmp_path / "library", tmp_path / "manualbeagle.sqlite")
    doc = library_doc(tmp_path)
    lib.db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            doc["sha256"],
            doc["path"],
            "https://lg.test/m.pdf",
            "Owner's manual",
            "owners_manual",
            "LG",
            "WM4000HWA",
            3,
            0,
            0,
        ),
    )
    lib.db.commit()
    lib.close()
    return cfg


def test_cli_send_asks_first(monkeypatch, tmp_path, capsys):
    fake = FakePaperless()
    cfg = setup_cli(monkeypatch, tmp_path, fake)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(["--config", str(cfg), "paperless", "send", "--dry-run"]) == 0
    assert "DRY RUN, nothing sent" in capsys.readouterr().out
    assert main(["--config", str(cfg), "paperless", "send", "WM4000"]) == 0
    assert "not written" in capsys.readouterr().out and fake.posted == []
    assert main(["--config", str(cfg), "paperless", "send", "WM4000", "--yes"]) == 0
    assert "✓ filed" in capsys.readouterr().out and len(fake.posted) == 1
    assert main(["--config", str(cfg), "paperless", "send", "--yes"]) == 0
    assert "Nothing to send" in capsys.readouterr().out and len(fake.posted) == 1
    assert main(["--config", str(cfg), "paperless", "status"]) == 0
    assert "✓ 1 filed" in capsys.readouterr().out


def test_sweep_files_new_manuals(monkeypatch, tmp_path):
    from manualbeagle.config import load
    from manualbeagle.library import Library

    fake = FakePaperless()
    cfg_path = setup_cli(monkeypatch, tmp_path, fake, auto=True)
    cfg = load(cfg_path)
    lib = Library(cfg.library, cfg.db_path)
    assert [f.state for f in sweep(cfg, lib)] == ["queued"]
    assert sweep(cfg, lib) == []  # filed once
    lib.close()
