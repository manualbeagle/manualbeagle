"""Adding a scanned appliance to Homebox: the rules, the Homebox calls (mocked API) and the steps, offline."""

import json

import httpx
import respx

from manualbeagle.newitem import NewItem, add, matches, product_kind, same_item, suggest_name
from manualbeagle.sinks.homebox import Homebox, HomeboxError, Item
from manualbeagle.sync import Plan

BASE = "http://homebox.test:7745/api/v1"


# The rules


def test_product_kind_from_plate_text_and_titles():
    assert product_kind(["LG ELECTRONICS", "DISHWASHER  MODEL LDF5545ST"]) == "Dishwasher"
    assert product_kind(["Owner's Manual: Front Load Washer", "Washer installation guide"]) == "Washer"
    # A longer phrase isn't also counted as the shorter kind it contains.
    assert product_kind(["Pressure washer PW3100", "pressure washer manual"]) == "Pressure washer"
    assert product_kind(["Heat Pump Water Heater", "water heater use and care"]) == "Heat pump water heater"
    # The kind named most often wins.
    assert product_kind(["refrigerator", "Refrigerator", "freezer"]) == "Refrigerator"
    # Words that mean something else on a plate aren't kinds.
    assert product_kind(["OPERATING RANGE 5-40C", "FAN SPEED HIGH"]) == ""
    assert product_kind([]) == ""


def test_suggest_name_keeps_the_brand_as_confirmed():
    assert suggest_name("LG", "LDF5545ST", "Dishwasher") == "LG Dishwasher LDF5545ST"
    assert suggest_name(" Honeywell  Home", "RCHT9610WF") == "Honeywell Home RCHT9610WF"


def test_same_item_ignores_case_punctuation_and_brand_aliases():
    item = Item(id="1", name="Washer", manufacturer="LG Electronics", model="WM 5700-HVA")
    assert same_item(item, "LG", "wm5700hva")
    assert not same_item(item, "LG", "WM5700HV")
    assert not same_item(item, "Samsung", "WM5700HVA")
    assert not same_item(Item(id="2", name="x", manufacturer="LG"), "LG", "WM5700HVA")


# The Homebox client


def entity(eid, name, maker="", model="", **kw):
    return {"id": eid, "name": name, "manufacturer": maker, "modelNumber": model, **kw}


@respx.mock
def test_search_and_matches_read_each_result_and_keep_only_items():
    search = respx.get(f"{BASE}/entities", params={"q": "WM5700HVA"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "L1", "name": "Laundry", "entityType": {"isLocation": True}},
                    {"id": "I1", "name": "Washer", "entityType": {"isLocation": False}},
                    {"id": "I2", "name": "Washer manual binder", "entityType": {"isLocation": False}},
                ],
                "total": 3,
            },
        )
    )
    respx.get(f"{BASE}/entities/I1").mock(
        return_value=httpx.Response(
            200,
            json=entity("I1", "Washer", "LG", "WM5700HVA", serialNumber="123", parent={"name": "Laundry"}),
        )
    )
    respx.get(f"{BASE}/entities/I2").mock(return_value=httpx.Response(200, json=entity("I2", "Binder")))
    hb = Homebox("http://homebox.test:7745", "k")
    found = matches(hb, "LG", "WM5700HVA")
    assert [(i.id, i.serial, i.location) for i in found] == [("I1", "123", "Laundry")]
    assert search.called


@respx.mock
def test_location_choices_ask_for_locations_and_label_them_with_their_parent():
    # Homebox v0.27 lists items only, unless asked for locations.
    respx.get(f"{BASE}/entities", params={"isLocation": "true"}).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "L2",
                        "name": "Closet",
                        "entityType": {"isLocation": True},
                        "parent": {"name": "Upstairs"},
                    },
                    {"id": "L1", "name": "Basement", "entityType": {"isLocation": True}},
                    {"id": "I1", "name": "Washer", "entityType": {"isLocation": False}},
                ],
                "total": 3,
            },
        )
    )
    hb = Homebox("http://homebox.test:7745", "k")
    assert hb.location_choices() == [
        {"id": "L1", "name": "Basement", "label": "Basement"},
        {"id": "L2", "name": "Closet", "label": "Closet (Upstairs)"},
    ]


@respx.mock
def test_set_serial_sends_back_every_field_with_the_serial(tmp_path):
    respx.get(f"{BASE}/entities/I9").mock(
        return_value=httpx.Response(
            200,
            json=entity(
                "I9",
                "LG Dishwasher LDF5545ST",
                "LG",
                "LDF5545ST",
                description="Added by Manual Beagle",
                quantity=1,
                assetId="000-007",
                serialNumber="",
                purchasePrice=0,
                locationId=None,
                entityType={"id": "t-item", "isLocation": False},
                parent={"id": "L1", "name": "Kitchen"},
                tags=[{"id": "tag1", "name": "appliance"}],
                fields=[],
                attachments=[],
            ),
        )
    )
    put = respx.put(f"{BASE}/entities/I9").mock(return_value=httpx.Response(200, json={"id": "I9"}))
    hb = Homebox("http://homebox.test:7745", "k")
    hb.set_serial("I9", "904KWXX1234")
    body = json.loads(put.calls[0].request.content)
    assert body["serialNumber"] == "904KWXX1234" and body["id"] == "I9"
    assert body["name"] == "LG Dishwasher LDF5545ST" and body["modelNumber"] == "LDF5545ST"
    assert body["assetId"] == "000-007" and body["description"] == "Added by Manual Beagle"
    assert body["entityTypeId"] == "t-item" and body["parentId"] == "L1" and body["tagIds"] == ["tag1"]
    assert "locationId" not in body  # null in GET: not sent, so an inherited location stays inherited
    assert "attachments" not in body and "entityType" not in body


@respx.mock
def test_attach_photo_uploads_it_as_the_primary_photo(tmp_path):
    up = respx.post(f"{BASE}/entities/I9/attachments").mock(return_value=httpx.Response(201, json={}))
    jpg = tmp_path / "p.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe0 jpeg")
    hb = Homebox("http://homebox.test:7745", "k")
    hb.attach_photo("I9", jpg, "LG LDF5545ST - rating plate.jpg", primary=True)
    content = up.calls[0].request.content
    assert b'name="type"\r\n\r\nphoto' in content and b'name="primary"\r\n\r\ntrue' in content
    assert b"image/jpeg" in content
    assert hb.item_url("I9") == "http://homebox.test:7745/item/I9"


# The steps


class FakeHB:
    """Records what would be written; `refuse` makes one kind of write fail."""

    def __init__(self, refuse: str = "") -> None:
        self.calls: list = []
        self.refuse = refuse

    def create_item(self, name, maker, model, parent, notes=""):
        self.calls.append(("create", name, maker, model, parent))
        return "NEW"

    def set_serial(self, eid, serial):
        self.calls.append(("serial", eid, serial))

    def item(self, eid):
        manuals = [{"type": "manual"}] if eid == "OLD" else []
        return Item(id=eid, name="Dishwasher", manufacturer="LG", model="LDF5545ST", attachments=manuals)

    def attach_photo(self, eid, path, name, primary=False):
        self.calls.append(("photo", eid, name, primary))

    def attach_file(self, eid, path, name):
        if self.refuse == "file":
            raise HomeboxError("HTTP 500")
        self.calls.append(("file", eid, name))

    def attach_link(self, eid, url, title):
        self.calls.append(("link", eid, url))

    def item_url(self, eid):
        return f"http://hb/item/{eid}"


def planner(action, **kw):
    def plan(item):
        return Plan(item=item.name, item_id=item.id, brand="LG", model="LDF5545ST", action=action, **kw)

    return plan


def test_add_creates_sets_serial_attaches_photo_then_the_manual(tmp_path):
    photo = tmp_path / "plate.jpg"
    photo.write_bytes(b"x")
    hb = FakeHB()
    new = NewItem(
        name="LG Dishwasher", brand="LG", model="LDF5545ST", serial="SN1", location_id="L1", photo=photo
    )
    pdf = tmp_path / ("a" * 64 + ".pdf")
    rows = list(
        add(
            hb,
            new,
            planner("upload", file=str(pdf), url="https://lg/m.pdf", reason="PDF text names the model"),
            True,
        )
    )
    assert [r["step"] for r in rows] == ["item", "serial", "photo", "manual"]
    assert all(r["state"] == "ok" for r in rows)
    assert rows[0]["url"] == "http://hb/item/NEW" and rows[3]["document"] == "a" * 64
    assert hb.calls == [
        ("create", "LG Dishwasher", "LG", "LDF5545ST", "L1"),
        ("serial", "NEW", "SN1"),
        ("photo", "NEW", "LG LDF5545ST - rating plate.jpg", True),
        ("file", "NEW", "LG LDF5545ST - Manual.pdf"),
    ]


def test_add_to_an_existing_item_creates_nothing():
    hb = FakeHB()
    new = NewItem(name="", brand="LG", model="LDF5545ST", existing_id="OLD")
    rows = list(add(hb, new, planner("skip", reason="already has 1 manual(s)"), True))
    assert [(r["step"], r["state"]) for r in rows] == [("item", "ok"), ("manual", "skip")]
    assert hb.calls == []


def test_add_respects_attach_links_and_reports_a_refused_manual(tmp_path):
    new = NewItem(name="X", brand="LG", model="LDF5545ST")
    hb = FakeHB()
    rows = list(add(hb, new, planner("link", url="https://lg.com/p", reason="link only"), attach_links=False))
    assert rows[-1]["state"] == "skip" and "attach_links is off" in rows[-1]["text"]
    assert not any(c[0] == "link" for c in hb.calls)

    hb = FakeHB(refuse="file")
    rows = list(add(hb, new, planner("upload", file=str(tmp_path / "m.pdf"), url="u", reason="r"), True))
    assert rows[-1]["state"] == "bad" and "HTTP 500" in rows[-1]["text"]

    rows = list(add(FakeHB(), new, planner("none", reason="nothing scored 60+"), True))
    assert rows[-1]["state"] == "bad" and "no manual found" in rows[-1]["text"]
