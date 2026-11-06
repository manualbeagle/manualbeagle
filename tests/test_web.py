"""The web UI's HTTP API, offline: lookups and Homebox are faked, the library is real and holds a generated PDF."""

import time

import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402
from test_library import FakeHTTP, make_pdf  # noqa: E402

from manualbeagle import batch, sync  # noqa: E402
from manualbeagle.config import Config  # noqa: E402
from manualbeagle.library import Library  # noqa: E402
from manualbeagle.models import Candidate, DocKind, FindResult, Query  # noqa: E402
from manualbeagle.normalize import normalise  # noqa: E402
from manualbeagle.sinks.homebox import HomeboxError, Item  # noqa: E402
from manualbeagle.web import app as web  # noqa: E402

PDF = make_pdf(
    [
        "OWNER'S MANUAL WM5700HVA washer",
        "Clean the door seal every month",
        "Warranty: one year parts and labour",
    ]
)
BASE = "http://127.0.0.1:8765"


@pytest.fixture
def cfg(tmp_path):
    c = Config(home=tmp_path)
    c.search.engine = "none"
    return c


@pytest.fixture
def client(cfg):
    with TestClient(web.create_app(cfg, http=FakeHTTP(PDF)), base_url=BASE) as tc:
        yield tc


@pytest.fixture
def sha(cfg):
    lib = Library(cfg.library, cfg.db_path)
    c = Candidate(url="https://example.com/m.pdf", title="Owner's Manual", kind=DocKind.owners_manual)
    doc = lib.fetch(c, normalise(Query(brand="LG", model="WM5700HVA")), FakeHTTP(PDF))
    lib.close()
    return doc.sha256


def fake_find(cands):
    def find(q, reg, http, engine, **kw):
        return FindResult(query=normalise(q), candidates=cands)

    return find


def wait(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("the job never finished")


# The page and the status


def test_page_and_static_files_have_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "Manual Beagle" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/static/app.js").status_code == 200


def test_status(client, sha):
    s = client.get("/api/status").json()
    assert s["documents"] == 1 and s["pages"] == 3
    assert {"value": "owners_manual", "label": "Owner's manual"} in s["kinds"]
    assert s["search"] is False


# Who may ask


def test_refuses_other_host_names(client):
    """A page on another site that rebinds its name to 127.0.0.1 still sends its own Host header."""
    assert client.get("/api/status", headers={"host": "evil.example"}).status_code == 400


def test_refuses_changes_from_other_origins(client):
    r = client.post(
        "/api/find", json={"brand": "LG", "model": "X"}, headers={"origin": "https://evil.example"}
    )
    assert r.status_code == 403


def test_refuses_form_posts(client):
    """A cross-site HTML form can't send JSON, so a change must be JSON."""
    r = client.post("/api/find", data={"brand": "LG", "model": "X"})
    assert r.status_code == 415


def test_token_is_required_when_set(cfg):
    app = web.create_app(cfg, http=FakeHTTP(PDF), token="s3cret", hosts=["*"])
    with TestClient(app, base_url="http://192.0.2.5:8765") as tc:
        assert tc.get("/api/status").status_code == 401
        assert tc.get("/healthz").text == "ok"  # monitoring needs no token
        assert tc.get("/?token=wrong").status_code == 401
        r = tc.get("/?token=s3cret", follow_redirects=False)
        assert r.status_code == 303 and "httponly" in r.headers["set-cookie"].lower()
        assert tc.get("/api/status").status_code == 200  # the cookie now goes with every request
    with TestClient(app, base_url="http://192.0.2.5:8765") as tc:
        assert tc.get("/api/status", headers={"authorization": "Bearer s3cret"}).status_code == 200


# Find and fetch


def test_find_returns_ranked_candidates_and_marks_the_library(client, sha, monkeypatch):
    cands = [
        Candidate(url="https://example.com/m.pdf", title="OM", kind=DocKind.owners_manual, score=90),
        Candidate(url="https://example.com/page", title="Support", link_only=True, score=40),
    ]
    monkeypatch.setattr(web, "find", fake_find(cands))
    r = client.post("/api/find", json={"brand": "LG", "model": "WM5700HVA", "search": "never"})
    assert r.status_code == 200
    body = r.json()
    assert [c["url"] for c in body["candidates"]] == ["https://example.com/m.pdf", "https://example.com/page"]
    assert body["in_library"] == {"https://example.com/m.pdf": sha}


def test_find_checks_its_input(client):
    assert client.post("/api/find", json={"brand": "LG"}).status_code == 400
    assert client.post("/api/find", json={"brand": "LG", "model": "X", "kind": "nope"}).status_code == 400
    assert client.post("/api/find", json={"brand": "LG", "model": "X", "search": "maybe"}).status_code == 400
    assert client.post("/api/find", json={"brand": "LG", "model": "X", "year": "soon"}).status_code == 400


def test_fetch_downloads_into_the_library(client):
    c = {"url": "https://example.com/m.pdf", "title": "Owner's Manual", "kind": "owners_manual"}
    r = client.post("/api/fetch", json={"candidate": c, "brand": "LG", "model": "WM5700HVA"})
    assert r.status_code == 200
    d = r.json()
    assert d["pages"] == 3 and d["check_score"] > 0
    assert len(client.get("/api/documents").json()) == 1


def test_fetch_refuses_a_link_only_candidate(client):
    c = {"url": "https://example.com/page", "link_only": True}
    r = client.post("/api/fetch", json={"candidate": c, "brand": "LG", "model": "WM5700HVA"})
    assert r.status_code == 422 and "link-only" in r.json()["error"]


def test_fetch_refuses_a_manual_mirror(client):
    """The candidate comes back from the browser, so a mirror's URL is refused even when not marked link-only."""
    c = {"url": "https://www.manualslib.com/manual/1/x.html"}
    r = client.post("/api/fetch", json={"candidate": c, "brand": "LG", "model": "WM5700HVA"})
    assert r.status_code == 422 and "mirror" in r.json()["error"]


# The library


def test_documents_and_pages(client, sha):
    docs = client.get("/api/documents").json()
    assert docs[0]["sha256"] == sha and "path" not in docs[0]
    assert "path" not in client.get(f"/api/documents/{sha}").json()
    p = client.get(f"/api/documents/{sha}/pages?start=2&count=5").json()
    assert p["page_count"] == 3 and set(p["pages"]) == {"2", "3"}
    assert "door seal" in p["pages"]["2"]
    assert client.get(f"/api/documents/{sha}/pages?count=500").status_code == 400


def test_pdf_is_served_inline_only_for_library_documents(client, sha):
    r = client.get(f"/api/documents/{sha}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF-")
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")
    assert client.get(f"/api/documents/{'0' * 64}/pdf").status_code == 404
    assert client.get("/api/documents/..%2F..%2Fconfig.toml/pdf").status_code == 404


def test_pdf_outside_the_library_is_refused(client, sha, cfg, tmp_path):
    """A database row pointing outside the library folder is never served."""
    outside = tmp_path / "elsewhere.pdf"
    outside.write_bytes(PDF)
    lib = Library(cfg.library, cfg.db_path)
    lib.db.execute("UPDATE documents SET path=? WHERE sha256=?", (str(outside), sha))
    lib.db.commit()
    lib.close()
    assert client.get(f"/api/documents/{sha}/pdf").status_code == 404


def test_search_marks_matches(client, sha):
    body = client.get("/api/search", params={"q": "seal"}).json()
    assert body["plain"] is False
    assert body["rows"][0]["page"] == 2
    assert "\x02seal\x03" in body["rows"][0]["snip"]


def test_search_falls_back_to_plain_words(client, sha):
    """Text with FTS5 operators in the wrong place (a hyphen, a colon) is searched as plain words."""
    body = client.get("/api/search", params={"q": "door-seal:"}).json()
    assert body["plain"] is True and body["rows"][0]["page"] == 2
    assert client.get("/api/search").status_code == 400


def test_unknown_document(client):
    assert client.get(f"/api/documents/{'a' * 64}").status_code == 404
    assert client.get("/api/documents/not-a-sha/pages").status_code == 404


# Batch


def test_batch_looks_up_every_row(client, monkeypatch):
    cands = [Candidate(url="https://example.com/m.pdf", title="OM", kind=DocKind.owners_manual, score=90)]
    monkeypatch.setattr(batch, "find", fake_find(cands))
    text = "name,brand,model\nWasher,LG,WM5700HVA\nLamp,Acme,\n"
    r = client.post("/api/batch", json={"text": text, "format": "auto"})
    assert r.status_code == 202
    job = wait(client, r.json()["id"])
    assert job["state"] == "done" and [row["result"] for row in job["rows"]] == ["found", "skip"]
    assert "✓ 1 found" in job["summary"] and "– 1 no model" in job["summary"]


def test_batch_with_fetch_downloads(client, monkeypatch):
    cands = [Candidate(url="https://example.com/m.pdf", title="OM", kind=DocKind.owners_manual, score=90)]
    monkeypatch.setattr(sync, "find", fake_find(cands))
    text = "- {name: Washer, brand: LG, model: WM5700HVA}\n"
    job = wait(client, client.post("/api/batch", json={"text": text, "fetch": True}).json()["id"])
    row = job["rows"][0]
    assert row["action"] == "upload" and len(row["document"]) == 64 and "file" not in row
    assert "✓ 1 of 1 with a manual" in job["summary"]


def test_batch_refuses_what_it_cant_read(client):
    assert client.post("/api/batch", json={"text": ""}).status_code == 400
    assert client.post("/api/batch", json={"text": "just: a mapping", "format": "yaml"}).status_code == 400
    assert client.post("/api/batch", json={"text": "a,b", "format": "toml"}).status_code == 400


# Homebox


def test_homebox_not_set_up(client):
    h = client.get("/api/homebox").json()
    assert h == {"configured": False, "ok": False, "error": "no [homebox] url in the config"}


class FakeHomebox:
    attached: list = []

    def __init__(self, refuse: str = "") -> None:
        self.refuse = refuse

    def items(self):
        return [
            Item(id="I1", name="Washer", manufacturer="LG", model="WM5700HVA"),
            Item(id="I2", name="Dryer", manufacturer="LG", model="DLEX5700"),
        ]

    def attach_file(self, entity_id, path, name):
        if entity_id == self.refuse:
            raise HomeboxError("HTTP 500")
        FakeHomebox.attached.append(("file", entity_id, name))

    def attach_link(self, entity_id, url, title):
        FakeHomebox.attached.append(("link", entity_id, url))

    def close(self):
        pass


def test_sync_dry_run_then_attach(client, monkeypatch):
    FakeHomebox.attached = []
    monkeypatch.setattr(web, "homebox", lambda cfg: FakeHomebox())
    cands = [Candidate(url="https://example.com/m.pdf", title="OM", kind=DocKind.owners_manual, score=90)]
    monkeypatch.setattr(sync, "find", fake_find(cands))

    job = wait(client, client.post("/api/sync", json={"item": "wash"}).json()["id"])
    assert job["state"] == "done" and job["total"] == 1
    assert job["rows"][0]["action"] == "upload" and job["summary"].startswith("DRY RUN, nothing written.")
    assert FakeHomebox.attached == []  # a dry run writes nothing

    r = client.post(f"/api/jobs/{job['id']}/attach", json={})
    assert r.status_code == 202
    done = wait(client, r.json()["id"])
    assert done["rows"][0]["done"] is True and done["summary"] == "✓ 1 attached to Homebox"
    assert FakeHomebox.attached == [("file", "I1", "LG WM5700HVA - Owner_s manual.pdf")]
    assert client.post(f"/api/jobs/{job['id']}/attach", json={}).status_code == 409  # only once


def test_attach_reports_what_homebox_refused(client, monkeypatch):
    monkeypatch.setattr(web, "homebox", lambda cfg: FakeHomebox(refuse="I1"))
    cands = [Candidate(url="https://example.com/m.pdf", title="OM", kind=DocKind.owners_manual, score=90)]
    monkeypatch.setattr(sync, "find", fake_find(cands))
    job = wait(client, client.post("/api/sync", json={"item": "wash"}).json()["id"])
    done = wait(client, client.post(f"/api/jobs/{job['id']}/attach", json={}).json()["id"])
    assert done["rows"][0]["error"] == "HTTP 500" and "✗ 1 refused" in done["summary"]


def test_sync_when_homebox_isnt_set_up(client):
    r = client.post("/api/sync", json={})
    assert r.status_code == 502 and "Homebox" in r.json()["error"]


def test_attach_needs_a_sync_job(client):
    assert client.post("/api/jobs/nope/attach", json={}).status_code == 404


# Jobs


def test_one_job_at_a_time_and_cancel(client, monkeypatch):
    import threading

    gate = threading.Event()

    def slow_find(q, reg, http, engine, **kw):
        gate.wait(5)
        return FindResult(query=normalise(q), candidates=[])

    monkeypatch.setattr(batch, "find", slow_find)
    text = "brand,model\nLG,A\nLG,B\nLG,C\n"
    first = client.post("/api/batch", json={"text": text}).json()
    assert client.post("/api/batch", json={"text": text}).status_code == 409
    assert client.post(f"/api/jobs/{first['id']}/cancel", json={}).status_code == 200
    gate.set()
    job = wait(client, first["id"])
    assert job["state"] == "cancelled" and job["done"] < 3
    assert [j["id"] for j in client.get("/api/jobs").json()] == [first["id"]]


# Behind a reverse proxy


def test_behind_a_proxy_accepts_its_name_and_origin(cfg):
    app = web.create_app(
        cfg,
        http=FakeHTTP(PDF),
        token="s3cret",
        hosts=[*web.LOOPBACK, "manuals.example"],
        origins=["https://manuals.example"],
    )
    with TestClient(app, base_url="https://manuals.example") as tc:
        r = tc.get("/?token=s3cret", follow_redirects=False)
        assert "secure" in r.headers["set-cookie"].lower()
        own = {"origin": "https://manuals.example"}
        assert tc.post("/api/find", json={"brand": "LG"}, headers=own).status_code == 400  # past the guard
        other = {"origin": "https://evil.example"}
        assert tc.post("/api/find", json={"brand": "LG"}, headers=other).status_code == 403
        assert tc.get("/api/status", headers={"host": "evil.example"}).status_code == 400


def test_public_url_must_be_http_with_a_name():
    assert web.public_origin("https://manuals.example/") == ("https://manuals.example", "manuals.example")
    with pytest.raises(ValueError):
        web.public_origin("manuals.example")


def test_a_token_from_the_environment_is_never_printed(cfg, monkeypatch, capsys):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("MANUALBEAGLE_WEB_TOKEN", "from-the-env")
    assert web.run(cfg, public_url="https://manuals.example") == 0
    out = capsys.readouterr().out
    assert "from-the-env" not in out and "open https://manuals.example/" in out
    assert seen["host"] == "127.0.0.1"
