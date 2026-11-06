"""The Homebox client against a mocked API (shapes from Homebox v0.27.1's OpenAPI document)."""

import json

import httpx
import respx

from manualbeagle.sinks.homebox import Homebox

BASE = "http://homebox.test:7745/api/v1"
TYPES = [
    {"id": "t-loc", "name": "Location", "isLocation": True},
    {"id": "t-item", "name": "Item", "isLocation": False},
]


@respx.mock
def test_items_and_manual_detection():
    respx.get(f"{BASE}/entities").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"id": "L1", "name": "Laundry", "entityType": {"isLocation": True}},
                    {"id": "I1", "name": "Washer", "entityType": {"isLocation": False}},
                ],
                "total": 2,
            },
        )
    )
    respx.get(f"{BASE}/entities/I1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "I1",
                "name": "Washer",
                "manufacturer": "LG",
                "modelNumber": "WM5700HVA",
                "parent": {"name": "Laundry"},
                "attachments": [{"type": "manual", "title": "Owner's manual"}],
            },
        )
    )
    hb = Homebox("http://homebox.test:7745", "k")
    items = hb.items()
    assert [(i.name, i.manufacturer, i.model, i.location) for i in items] == [
        ("Washer", "LG", "WM5700HVA", "Laundry")
    ]
    assert len(items[0].manuals) == 1
    assert hb.locations() == {"Laundry": "L1"}


@respx.mock
def test_create_and_attach(tmp_path):
    respx.get(f"{BASE}/entity-types").mock(return_value=httpx.Response(200, json=TYPES))
    create = respx.post(f"{BASE}/entities").mock(return_value=httpx.Response(201, json={"id": "I9"}))
    upload = respx.post(f"{BASE}/entities/I9/attachments").mock(
        return_value=httpx.Response(201, json={"id": "I9"})
    )
    link = respx.post(f"{BASE}/entities/I9/attachments/external").mock(
        return_value=httpx.Response(201, json={"id": "I9"})
    )
    hb = Homebox("http://homebox.test:7745", "secret")
    assert hb.create_item("Dryer", "LG", "DLEX5500V", "L1") == "I9"
    body = json.loads(create.calls[0].request.content)
    assert (
        body["entityTypeId"] == "t-item" and body["parentId"] == "L1" and body["modelNumber"] == "DLEX5500V"
    )
    assert create.calls[0].request.headers["Authorization"] == "Bearer secret"

    pdf = tmp_path / "m.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    hb.attach_file("I9", pdf, "LG DLEX5500V - Owner's manual.pdf")
    req = upload.calls[0].request
    assert b'name="type"' in req.content and b"manual" in req.content

    hb.attach_link("I9", "https://example.com/x.pdf", "Owner's manual")
    assert json.loads(link.calls[0].request.content) == {
        "source_type": "link",
        "external_id": "https://example.com/x.pdf",
        "title": "Owner's manual",
        "attachment_type": "manual",
    }
