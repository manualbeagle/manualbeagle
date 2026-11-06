"""Nameplate scanning through the web API, offline: OCR is faked, the library is real."""

import struct

import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402
from test_library import FakeHTTP  # noqa: E402
from test_nameplate import FIXTURES, PLATE_TEXT, FakeAPI  # noqa: E402

from manualbeagle.config import AIConfig, Config  # noqa: E402
from manualbeagle.web import app as web  # noqa: E402

BASE = "http://127.0.0.1:8765"
PNG = (FIXTURES / "plate_lg.png").read_bytes()


@pytest.fixture
def cfg(tmp_path):
    c = Config(home=tmp_path)
    c.search.engine = "none"
    return c


@pytest.fixture
def client(cfg, monkeypatch):
    monkeypatch.setattr(web, "plate_ocr_available", lambda: True)
    monkeypatch.setattr(web, "ocr_image", lambda data: PLATE_TEXT)
    with TestClient(web.create_app(cfg, http=FakeHTTP(b"")), base_url=BASE) as tc:
        yield tc


def scan(client, body=PNG, ctype="image/png", **kw):
    return client.post("/api/nameplate", content=body, headers={"content-type": ctype, **kw})


def test_scan_reads_keeps_and_lists_the_photo(client, cfg):
    r = scan(client)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ocr"] is True and out["vision"] is None
    assert [s["value"] for s in out["reading"]["brands"]] == ["LG"]
    assert [s["value"] for s in out["reading"]["models"]] == ["WM3400CW"]
    sha = out["photo"]["sha256"]
    assert "path" not in out["photo"] and "ocr_text" not in out["photo"]
    assert (cfg.library / "photos" / f"{sha}.png").exists()

    img = client.get(f"/api/photos/{sha}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content == PNG
    assert client.post(f"/api/photos/{sha}", json={"brand": "LG", "model": "WM3400CW"}).status_code == 200
    listed = client.get("/api/photos").json()
    assert [(p["brand"], p["model"]) for p in listed] == [("LG", "WM3400CW")]
    assert client.get(f"/api/photos/{sha}").json()["ocr_text"] == PLATE_TEXT
    assert client.post(f"/api/photos/{sha}/delete", json={}).status_code == 200
    assert client.get("/api/photos").json() == []
    assert client.get(f"/api/photos/{sha}/image").status_code == 404


def test_scan_strips_metadata_before_keeping(client, cfg):
    jpeg = (FIXTURES / "plate_whirlpool.jpg").read_bytes()
    exif = b"Exif\x00\x00GPS 40.0N 75.0W"
    tagged = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:]
    sha = scan(client, tagged, "image/jpeg").json()["photo"]["sha256"]
    assert b"GPS" not in (cfg.library / "photos" / f"{sha}.jpg").read_bytes()


def test_scan_checks_what_it_is_sent(client, monkeypatch):
    assert scan(client, b"GIF89a....", "image/png").status_code == 415  # not an image it reads
    assert scan(client, PNG, "image/jpeg").status_code == 415  # the bytes and the type disagree
    assert scan(client, PNG, "image/gif").status_code == 415  # only JPEG and PNG get past the guard
    assert scan(client, b"", "image/png").status_code == 400
    huge = PNG[:16] + (60000).to_bytes(4, "big") + (60000).to_bytes(4, "big") + PNG[24:]
    assert "megapixels" in scan(client, huge).json()["error"]  # refused before OCR decodes it
    monkeypatch.setattr(web, "MAX_IMAGE", 1000)
    assert scan(client).status_code == 413
    # another site's page can't send a photo: its Origin is refused (and a browser would preflight it first)
    assert scan(client, origin="https://evil.example").status_code == 403
    # only the nameplate endpoint takes an image body
    assert client.post("/api/find", content=PNG, headers={"content-type": "image/png"}).status_code == 415


def test_scan_without_tesseract_still_keeps_the_photo(client, monkeypatch):
    monkeypatch.setattr(web, "plate_ocr_available", lambda: False)
    out = scan(client).json()
    assert out["ocr"] is False and out["reading"]["models"] == [] and out["photo"]["sha256"]


def test_photo_endpoints_check_their_input(client):
    assert client.get("/api/photos/nothex/image").status_code == 404
    assert client.get(f"/api/photos/{'0' * 64}").status_code == 404
    assert client.post(f"/api/photos/{'0' * 64}", json={"brand": "LG", "model": "X"}).status_code == 404
    sha = scan(client).json()["photo"]["sha256"]
    assert client.post(f"/api/photos/{sha}", json={"brand": "LG"}).status_code == 400


def test_vision_endpoint(cfg, monkeypatch):
    monkeypatch.setattr(web, "plate_ocr_available", lambda: True)
    monkeypatch.setattr(web, "ocr_image", lambda data: "")
    with TestClient(web.create_app(cfg, http=FakeHTTP(b"")), base_url=BASE) as tc:
        sha = scan(tc).json()["photo"]["sha256"]
        assert tc.post(f"/api/photos/{sha}/vision", json={}).status_code == 409  # none set up
    cfg.ai = AIConfig(provider="ollama", url="http://192.168.1.50:11434")
    api = FakeAPI({"message": {"content": '{"brand": "LG", "model": "WM3400CW", "serial": ""}'}})
    with TestClient(web.create_app(cfg, http=api), base_url=BASE) as tc:
        assert tc.get("/api/status").json()["vision"] == "ollama gemma3"
        out = scan(tc).json()
        assert out["vision"] == "ollama gemma3"
        v = tc.post(f"/api/photos/{out['photo']['sha256']}/vision", json={}).json()
        assert v["model"] == "ollama gemma3" and [s["value"] for s in v["models"]] == ["WM3400CW"]
        assert v["models"][0]["source"] == "ollama gemma3"


def test_status_says_whether_photos_can_be_read(client):
    s = client.get("/api/status").json()
    assert s["plate_ocr"] is True and s["vision"] is None and s["vision_leaves_network"] is False


# Installing on a phone, and signing in


def test_manifest_and_service_worker(client):
    m = client.get("/manifest.webmanifest")
    assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
    manifest = m.json()
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/#find"
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and "/api/" in sw.text
    page = client.get("/").text
    assert 'rel="manifest"' in page and 'capture="environment"' in page
    # A second photo input without capture, so a phone offers its photo library as well as the camera.
    library = page.split('id="scan-library"', 1)[1].split(">", 1)[0]
    assert 'accept="image/*"' in library and "capture" not in library


def test_token_sign_in_page_and_public_files(cfg):
    app = web.create_app(cfg, http=FakeHTTP(b""), token="s3cret", hosts=["*"])
    with TestClient(app, base_url="https://manuals.example.org") as tc:
        page = tc.get("/")
        assert (
            page.status_code == 401
            and '<form method="get"' in page.text
            and "default-src" in page.headers["content-security-policy"]
        )
        assert tc.get("/api/photos").status_code == 401
        # the page's own files and the manifest need no token: a browser fetches the manifest without cookies
        assert tc.get("/manifest.webmanifest").status_code == 200
        assert tc.get("/static/app.js").status_code == 200 and tc.get("/sw.js").status_code == 200
        r = tc.get("/?token=s3cret", follow_redirects=False)
        cookie = r.headers["set-cookie"].lower()
        assert r.status_code == 303 and "secure" in cookie and "max-age=31536000" in cookie
    with TestClient(app, base_url="http://192.0.2.5:8765") as tc:
        r = tc.get("/?token=s3cret", follow_redirects=False)
        assert "secure" not in r.headers["set-cookie"].lower()  # a Secure cookie wouldn't come back over http
