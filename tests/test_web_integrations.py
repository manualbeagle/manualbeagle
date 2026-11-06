"""The web UI's Home Assistant and Paperless-ngx endpoints, offline: both services and Homebox are faked."""

import httpx
import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402
from test_homeassistant import RESULTS, FakeHomebox, FakeWS  # noqa: E402
from test_library import FakeHTTP  # noqa: E402
from test_paperless import FakePaperless  # noqa: E402
from test_web import BASE, PDF, wait  # noqa: E402

from manualbeagle.config import Config  # noqa: E402
from manualbeagle.sinks import paperless as pl_mod  # noqa: E402
from manualbeagle.sources import homeassistant as ha_mod  # noqa: E402
from manualbeagle.web import app as web  # noqa: E402
from manualbeagle.web import integrations  # noqa: E402


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = Config(home=tmp_path)
    c.search.engine = "none"
    c.home_assistant.url = "http://ha.test:8123"
    c.paperless.url = "http://paperless.test:8000"
    c.homebox.url = "http://homebox.test:7745"
    monkeypatch.setenv("HA_TOKEN", "good")
    monkeypatch.setenv("PAPERLESS_TOKEN", "secret")
    monkeypatch.setenv("HOMEBOX_TOKEN", "k")
    return c


@pytest.fixture
def client(cfg):
    with TestClient(web.create_app(cfg, http=FakeHTTP(PDF)), base_url=BASE) as tc:
        yield tc


@pytest.fixture
def homebox(monkeypatch):
    hb = FakeHomebox()
    hb.items = lambda: []
    hb.close = lambda: None
    monkeypatch.setattr(integrations, "homebox", lambda cfg: hb)
    return hb


@pytest.fixture
def ha(monkeypatch):
    fakes = []

    def connect(url, timeout):
        fakes.append(FakeWS(RESULTS))
        return fakes[-1]

    monkeypatch.setattr(ha_mod, "_websockets_connect", connect)
    return fakes


@pytest.fixture
def paperless(monkeypatch):
    fake = FakePaperless()
    real = pl_mod.Paperless
    monkeypatch.setattr(
        pl_mod, "Paperless", lambda url, token: real(url, token, transport=httpx.MockTransport(fake.handler))
    )
    return fake


def test_status_names_both(client):
    s = client.get("/api/status").json()
    assert s["home_assistant"] and s["paperless"]
    keys = client.get("/api/settings").json()["keys"]
    assert keys["home_assistant"] and keys["paperless"]


def test_settings_page_edits_addresses_not_tokens(client):
    fields = {f["key"] for f in client.get("/api/settings").json()["fields"]}
    assert {"home_assistant.url", "paperless.url", "paperless.auto"} <= fields
    assert not any("token" in f for f in fields)
    r = client.post("/api/settings", json={"changes": {"home_assistant.token_env": "PATH"}})
    assert r.status_code == 400


def test_homeassistant_status(client, ha):
    assert client.get("/api/homeassistant").json() == {
        "configured": True,
        "ok": True,
        "url": "http://ha.test:8123",
        "version": "2026.10.4",
    }


def test_devices_then_import_one(client, ha, homebox):
    res = client.get("/api/homeassistant/devices").json()
    by_id = {o["device_id"]: o for o in res["offers"]}
    assert by_id["d1"]["action"] == "new" and by_id["d3"]["action"] == "skip"
    assert res["homebox"] is True
    r = client.post("/api/homeassistant/import", json={"device_ids": ["d1", "d3"]})
    assert r.status_code == 200
    body = r.json()
    assert [row["device_id"] for row in body["rows"]] == ["d1"]  # the sun is never added
    assert "1 added" in body["summary"] and "no longer new" in body["summary"]
    assert homebox.made == [("Kitchen dimmer", "Lutron", "PD-6WCL", "L1")]
    # Home Assistant only ever got list commands.
    sent = {m["type"] for f in ha for m in f.sent}
    assert sent <= {"auth", "config/device_registry/list", "config/area_registry/list", "config_entries/get"}


def test_import_checks_its_input(client, ha, homebox):
    assert client.post("/api/homeassistant/import", json={}).status_code == 400
    assert client.post("/api/homeassistant/import", json={"device_ids": [1]}).status_code == 400


def test_homeassistant_not_set_up(cfg):
    cfg.home_assistant.url = ""
    with TestClient(web.create_app(cfg, http=FakeHTTP(PDF)), base_url=BASE) as tc:
        assert tc.get("/api/homeassistant").json()["configured"] is False
        assert tc.get("/api/homeassistant/devices").status_code == 502


def fetch_one(client) -> str:
    c = {"url": "https://example.com/m.pdf", "title": "Owner's Manual", "kind": "owners_manual"}
    r = client.post("/api/fetch", json={"candidate": c, "brand": "LG", "model": "WM5700HVA"})
    assert r.status_code == 200
    return r.json()["sha256"]


def test_paperless_send_one_then_none_left(client, paperless):
    sha = fetch_one(client)
    p = client.get("/api/paperless").json()
    assert p["ok"] and p["version"] == "3.4.1" and p["unsent"] == 1
    job = client.post("/api/paperless/send", json={"sha256": sha})
    assert job.status_code == 202
    done = wait(client, job.json()["id"])
    assert done["rows"][0]["state"] == "filed"
    assert client.get("/api/paperless").json()["unsent"] == 0
    assert client.post("/api/paperless/send", json={"all": True}).status_code == 400  # nothing left
    assert client.post("/api/paperless/send", json={}).status_code == 400
    assert client.post("/api/paperless/send", json={"sha256": "0" * 64}).status_code == 404


def test_paperless_auto_files_new_manuals(client, paperless, cfg):
    st = client.app.state.mb
    auto = integrations.PaperlessAuto(st)
    fetch_one(client)
    auto.once()
    assert auto.last == []  # auto is off
    cfg.paperless.auto = True
    auto.once()
    assert [f.state for f in auto.last] == ["queued"] and len(paperless.posted) == 1
    auto.once()
    assert auto.last == [] and len(paperless.posted) == 1
