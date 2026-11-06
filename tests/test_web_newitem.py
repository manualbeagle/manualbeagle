"""Add to Homebox through the web API, offline: Homebox and lookups are faked, the library is real."""

import pytest

pytest.importorskip("starlette")

from starlette.testclient import TestClient  # noqa: E402
from test_library import FakeHTTP, make_pdf  # noqa: E402
from test_web import fake_find, wait  # noqa: E402

from manualbeagle import sync  # noqa: E402
from manualbeagle.config import Config  # noqa: E402
from manualbeagle.library import Library  # noqa: E402
from manualbeagle.models import Candidate, DocKind  # noqa: E402
from manualbeagle.sinks.homebox import Item  # noqa: E402
from manualbeagle.web import app as web  # noqa: E402

BASE = "http://127.0.0.1:8765"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = make_pdf(["OWNER'S MANUAL LDF5545ST dishwasher", "Clean the filter every month"])


@pytest.fixture
def cfg(tmp_path):
    c = Config(home=tmp_path)
    c.search.engine = "none"
    return c


@pytest.fixture
def photo(cfg):
    lib = Library(cfg.library, cfg.db_path)
    row = lib.add_photo(JPEG, "image/jpeg", "LG ELECTRONICS\nDISHWASHER\nMODEL LDF5545ST\nS/N 904KW1234")
    lib.close()
    return row["sha256"]


class FakeHomebox:
    """Homebox with one dishwasher already in it (I1) and two locations; records every write."""

    writes: list = []

    def search(self, q):
        return [Item(id="I1", name="Old dishwasher", manufacturer="LG Electronics", model="LDF 5545ST")]

    def location_choices(self):
        return [{"id": "L1", "name": "Kitchen", "label": "Kitchen"}]

    def item(self, eid):
        return Item(id=eid, name="Old dishwasher", manufacturer="LG", model="LDF5545ST")

    def item_url(self, eid):
        return f"http://homebox.test/item/{eid}"

    def create_item(self, name, maker, model, parent, notes=""):
        FakeHomebox.writes.append(("create", name, maker, model, parent))
        return "NEW"

    def set_serial(self, eid, serial):
        FakeHomebox.writes.append(("serial", eid, serial))

    def attach_photo(self, eid, path, name, primary=False):
        FakeHomebox.writes.append(("photo", eid, primary))

    def attach_file(self, eid, path, name):
        FakeHomebox.writes.append(("file", eid, name))

    def attach_link(self, eid, url, title):
        FakeHomebox.writes.append(("link", eid, url))

    def close(self):
        pass


@pytest.fixture
def client(cfg, monkeypatch):
    FakeHomebox.writes = []
    monkeypatch.setattr(web, "homebox", lambda cfg: FakeHomebox())
    cands = [Candidate(url="https://example.com/m.pdf", title="OM", kind=DocKind.owners_manual, score=90)]
    monkeypatch.setattr(sync, "find", fake_find(cands))
    with TestClient(web.create_app(cfg, http=FakeHTTP(PDF)), base_url=BASE) as tc:
        yield tc


def test_preview_suggests_a_name_and_finds_the_duplicate_without_writing(client, photo):
    r = client.post(
        "/api/homebox/new-item/preview",
        json={"brand": "LG Electronics", "model": "LDF5545ST", "serial": "904KW1234", "photo": photo},
    )
    assert r.status_code == 200
    pv = r.json()
    assert (
        pv["name"] == "LG Electronics Dishwasher LDF5545ST" and pv["kind"] == "Dishwasher"
    )  # "DISHWASHER" on the plate
    assert (
        pv["brand"] == "LG Electronics" and pv["serial"] == "904KW1234"
    )  # as confirmed; still matched to the LG item below
    assert pv["locations"] == [{"id": "L1", "name": "Kitchen", "label": "Kitchen"}]
    assert [m["id"] for m in pv["matches"]] == ["I1"] and pv["matches"][0]["url"].endswith("/item/I1")
    assert FakeHomebox.writes == []


def test_preview_reads_the_kind_from_find_titles_too(client):
    pv = client.post(
        "/api/homebox/new-item/preview",
        json={"brand": "LG", "model": "WM5700HVA", "titles": ["Front Load Washer Owner's Manual"]},
    ).json()
    assert pv["name"] == "LG Washer WM5700HVA"


def test_new_item_needs_confirm_and_checks_its_input(client, photo):
    body = {"name": "LG Dishwasher", "brand": "LG", "model": "LDF5545ST", "photo": photo}
    assert client.post("/api/homebox/new-item", json=body).status_code == 400  # no confirm
    assert client.post("/api/homebox/new-item", json={**body, "confirm": "yes"}).status_code == 400
    bad = {**body, "confirm": True}
    assert client.post("/api/homebox/new-item", json={**bad, "photo": "f" * 64}).status_code == 404
    assert client.post("/api/homebox/new-item", json={**bad, "location_id": "../x"}).status_code == 400
    assert client.post("/api/homebox/new-item", json={**bad, "name": ""}).status_code == 400
    assert FakeHomebox.writes == []


def test_new_item_writes_item_serial_photo_and_manual(client, photo):
    body = {
        "confirm": True,
        "name": "LG Dishwasher LDF5545ST",
        "brand": "LG",
        "model": "LDF5545ST",
        "serial": "904KW1234",
        "location_id": "L1",
        "photo": photo,
    }
    r = client.post("/api/homebox/new-item", json=body)
    assert r.status_code == 202
    job = wait(client, r.json()["id"])
    assert job["state"] == "done", job
    assert [(row["step"], row["state"]) for row in job["rows"]] == [
        ("item", "ok"),
        ("serial", "ok"),
        ("photo", "ok"),
        ("manual", "ok"),
    ]
    assert job["item_url"] == "http://homebox.test/item/NEW"
    assert job["summary"].startswith(
        "✓ added to Homebox: LG Dishwasher LDF5545ST; manual: ✓ uploaded the PDF"
    )
    assert FakeHomebox.writes == [
        ("create", "LG Dishwasher LDF5545ST", "LG", "LDF5545ST", "L1"),
        ("serial", "NEW", "904KW1234"),
        ("photo", "NEW", True),
        ("file", "NEW", "LG LDF5545ST - Owner_s manual.pdf"),
    ]


def test_new_item_adds_to_an_existing_item_only_if_it_is_the_same_model(client, photo):
    body = {"confirm": True, "brand": "LG", "model": "LDF5545ST", "existing_id": "I1", "photo": photo}
    job = wait(client, client.post("/api/homebox/new-item", json=body).json()["id"])
    assert job["state"] == "done" and job["summary"].startswith("✓ updated in Homebox")
    assert FakeHomebox.writes[0] == ("photo", "I1", False)  # not made the item's picture
    assert not any(w[0] == "create" for w in FakeHomebox.writes)

    FakeHomebox.writes = []
    other = {**body, "model": "LDF5678"}
    assert client.post("/api/homebox/new-item", json=other).status_code == 409
    assert FakeHomebox.writes == []


def test_new_item_when_homebox_isnt_set_up(cfg):
    with TestClient(web.create_app(cfg, http=FakeHTTP(PDF)), base_url=BASE) as tc:
        r = tc.post("/api/homebox/new-item/preview", json={"brand": "LG", "model": "X"})
        assert r.status_code == 502 and "Homebox" in r.json()["error"]
