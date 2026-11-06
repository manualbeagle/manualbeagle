"""Maintenance suggestions through the web API, offline: Homebox and the model are faked, the library is real."""

import datetime as dt
import time

import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402
from test_library import FakeHTTP  # noqa: E402
from test_maintenance import pages  # noqa: E402

from manualbeagle import maintenance as mt  # noqa: E402
from manualbeagle.config import AIConfig, Config  # noqa: E402
from manualbeagle.library import Library  # noqa: E402
from manualbeagle.sinks.homebox import HomeboxError, Item  # noqa: E402
from manualbeagle.web import app as web  # noqa: E402

BASE = "http://127.0.0.1:8765"
SHA = "a" * 64


class FakeHomebox:
    """Two items; records maintenance entries; refuses one item."""

    entries: list = []

    def items(self):
        return [Item(id="I-ac", name="Window AC", manufacturer="LG", model="AC8000")]

    def summaries(self):
        return [
            {"id": "L1", "name": "Garage", "entityType": {"isLocation": True}},
            {"id": "I-ac", "name": "Window AC", "entityType": {"isLocation": False}},
            {"id": "I-x", "name": "Spare AC", "entityType": {"isLocation": False}},
        ]

    def entity(self, entity_id):
        return {"id": entity_id, "name": {"I-x": "Spare AC"}.get(entity_id, "?")}

    def add_maintenance(self, entity_id, name, description, scheduled):
        if entity_id == "I-refuse":
            raise HomeboxError("POST /entities/I-refuse/maintenance: HTTP 404")
        FakeHomebox.entries.append((entity_id, name, scheduled))
        return {"id": f"E{len(FakeHomebox.entries)}"}

    def close(self):
        pass


@pytest.fixture
def cfg(tmp_path):
    c = Config(home=tmp_path)
    c.search.engine = "none"
    c.homebox.url = "http://homebox.test:7745"
    lib = Library(c.library, c.db_path)
    texts = pages("window_ac")
    lib.db.execute(
        "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)",
        (SHA, "x.pdf", "https://example.com/m.pdf", "LG AC8000 Owner's Manual", "owners_manual", "LG", "AC8000",
         len(texts), 0, time.time()),
    )  # fmt: skip
    lib.db.executemany(
        "INSERT INTO pages (text, sha256, page, brand, model, kind) VALUES (?,?,?,?,?,?)",
        [(t, SHA, i + 1, "LG", "AC8000", "owners_manual") for i, t in enumerate(texts)],
    )
    lib.db.commit()
    lib.close()
    return c


@pytest.fixture
def client(cfg, monkeypatch):
    FakeHomebox.entries = []
    monkeypatch.setattr(web, "homebox", lambda cfg: FakeHomebox())
    with TestClient(web.create_app(cfg, http=FakeHTTP(b"")), base_url=BASE) as tc:
        yield tc


def wait(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("the job never finished")


def suggest(client, **body):
    r = client.post("/api/maintenance/suggest", json=body)
    assert r.status_code == 202, r.text
    return wait(client, r.json()["id"])


def test_suggest_then_list(client):
    job = suggest(client)
    assert job["state"] == "done" and job["summary"].startswith("✓ 5 new suggestions; 5 found in 1 documents")
    assert job["rows"][0]["items"] == ["Window AC"]
    res = client.get("/api/maintenance").json()
    assert (
        res["counts"] == {"new": 5, "filed": 0, "dismissed": 0}
        and res["homebox"] is True
        and res["ai"] is None
    )
    s = res["suggestions"][0]
    assert s["item_name"] == "Window AC" and s["quote"] and s["page"] == 2 and s["every"] == "every 2 weeks"
    assert s["first_date"] == (dt.date.today() + dt.timedelta(days=14)).isoformat()
    assert FakeHomebox.entries == []  # reading the manuals writes nothing to Homebox


def test_approve_files_one_entry_in_homebox(client):
    suggest(client)
    s = client.get("/api/maintenance").json()["suggestions"][0]
    r = client.post(
        f"/api/maintenance/{s['id']}/approve", json={"task": "Clean the AC filter", "scheduled": "2026-11-20"}
    )
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "filed" and done["homebox_entry"] == "E1" and done["scheduled"] == "2026-11-20"
    assert FakeHomebox.entries == [("I-ac", "Clean the AC filter", "2026-11-20")]
    assert client.post(f"/api/maintenance/{s['id']}/approve", json={}).status_code == 409  # once only
    assert client.get("/api/maintenance").json()["counts"]["filed"] == 1


def test_approve_without_an_item_needs_one_chosen(client, cfg):
    lib = Library(cfg.library, cfg.db_path)
    list(mt.suggest(lib, None))  # no Homebox: suggestions kept by brand and model
    lib.close()
    s = client.get("/api/maintenance").json()["suggestions"][0]
    assert s["item_id"] == ""
    assert client.post(f"/api/maintenance/{s['id']}/approve", json={}).status_code == 400
    items = client.get("/api/maintenance/items").json()
    assert items == [{"id": "I-x", "name": "Spare AC"}, {"id": "I-ac", "name": "Window AC"}]  # items, by name
    r = client.post(f"/api/maintenance/{s['id']}/approve", json={"item_id": "I-x"})
    assert r.status_code == 200 and r.json()["item_name"] == "Spare AC"


def test_homebox_refusing_leaves_it_waiting(client, cfg):
    lib = Library(cfg.library, cfg.db_path)
    list(mt.suggest(lib, None))
    lib.close()
    s = client.get("/api/maintenance").json()["suggestions"][0]
    r = client.post(f"/api/maintenance/{s['id']}/approve", json={"item_id": "I-refuse"})
    assert r.status_code == 502 and "Homebox refused" in r.json()["error"]
    again = client.get("/api/maintenance").json()["suggestions"][0]
    assert again["status"] == "new" and "HTTP 404" in again["error"]


def test_dismiss_and_undo(client):
    suggest(client)
    s = client.get("/api/maintenance").json()["suggestions"][0]
    assert client.post(f"/api/maintenance/{s['id']}/dismiss", json={}).json()["status"] == "dismissed"
    assert client.post(f"/api/maintenance/{s['id']}/dismiss", json={}).status_code == 409
    assert client.post(f"/api/maintenance/{s['id']}/approve", json={}).status_code == 409
    assert [x["id"] for x in client.get("/api/maintenance?status=dismissed").json()["suggestions"]] == [
        s["id"]
    ]
    suggest(client)  # not suggested again
    assert client.get("/api/maintenance").json()["counts"] == {"new": 4, "filed": 0, "dismissed": 1}
    assert client.post(f"/api/maintenance/{s['id']}/reopen", json={}).json()["status"] == "new"


def test_checks_its_input(client):
    assert client.get("/api/maintenance?status=bogus").status_code == 400
    assert client.post("/api/maintenance/abc/approve", json={}).status_code == 404
    assert client.post("/api/maintenance/999/approve", json={}).status_code == 404
    suggest(client)
    sid = client.get("/api/maintenance").json()["suggestions"][0]["id"]
    assert client.post(f"/api/maintenance/{sid}/approve", json={"scheduled": "next week"}).status_code == 400
    # an item id is a Homebox id, never a path
    assert (
        client.post(f"/api/maintenance/{sid}/approve", json={"item_id": "../users/self"}).status_code == 400
    )
    assert client.post("/api/maintenance/suggest", json={"ai": True}).status_code == 400  # no [ai] model
    # a change from another site is refused, as everywhere
    r = client.post(f"/api/maintenance/{sid}/approve", json={}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and FakeHomebox.entries == []


def test_suggest_with_the_model(cfg, monkeypatch):
    cfg.ai = AIConfig(provider="ollama", url="http://ollama.test:11434", model="gemma3")
    FakeHomebox.entries = []
    monkeypatch.setattr(web, "homebox", lambda cfg: FakeHomebox())
    asked = []

    def ask(ai, http, prompt):
        asked.append(prompt)
        return '{"tasks": [{"task": "Rinse the filter", "interval": "every 2 weeks", "quote": "Clean every 2 weeks"}]}'

    monkeypatch.setattr(mt, "ask_model", ask)
    with TestClient(web.create_app(cfg, http=FakeHTTP(b"")), base_url=BASE) as client:
        assert client.get("/api/maintenance").json()["ai"] == "ollama gemma3"
        job = suggest(client, ai=True)
        assert job["rows"][0]["pages_asked"] == 1 and "rules and ollama gemma3" in job["summary"]
        tasks = {s["task"]: s["source"] for s in client.get("/api/maintenance").json()["suggestions"]}
        assert tasks["Rinse the filter"] == "rules+ollama gemma3" and asked
