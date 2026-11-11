"""The Donetick client and filing a maintenance suggestion as a recurring chore, against a mocked API.

Shapes from Donetick v0.1.86's source: `POST /api/v1/chores/` answers `{"res": <id>}`, `/labels` is a bare list,
and every answer but that list is wrapped in `res`.
"""

import datetime as dt
import json

import httpx
import pytest
import respx

from manualbeagle import maintenance as mt
from manualbeagle.config import DonetickConfig
from manualbeagle.sinks.donetick import Donetick, DonetickError, due_at, interval_chore

BASE = "http://donetick.test:2021/api/v1"


def suggestion(**kw) -> mt.Suggestion:
    d = dict(
        id=7, task="Clean the filter", interval="every two weeks", every_n=2, every_unit="week", sha256="a" * 64,
        page=12, quote="Clean the filter every two weeks.", brand="LG", model="AC8000", item_id="I-ac",
        item_name="Window AC", source="rules", status="new", title="LG AC8000 Owner's Manual",
    )  # fmt: skip
    d.update(kw)
    return mt.Suggestion(**d)


def mock_donetick(labels=None):
    respx.get(f"{BASE}/users/profile").mock(
        return_value=httpx.Response(200, json={"res": {"id": 4, "username": "pat", "timezone": ""}})
    )
    respx.get(f"{BASE}/labels").mock(
        return_value=httpx.Response(200, json=labels if labels is not None else [{"id": 2, "name": "House"}])
    )
    return respx.post(f"{BASE}/chores/").mock(return_value=httpx.Response(200, json={"res": 31}))


def test_the_token_goes_in_the_secretkey_header():
    with respx.mock:
        route = respx.get(f"{BASE}/users/profile").mock(
            return_value=httpx.Response(200, json={"res": {"id": 4, "username": "pat"}})
        )
        assert Donetick("http://donetick.test:2021/", "tok").me()["id"] == 4
        assert route.calls[0].request.headers["secretkey"] == "tok"
        assert "authorization" not in route.calls[0].request.headers


def test_needs_an_address_and_a_token():
    with pytest.raises(DonetickError, match="URL and an API token"):
        Donetick("", "tok")
    with pytest.raises(DonetickError, match="URL and an API token"):
        Donetick("http://donetick.test:2021", "")


@respx.mock
def test_refusals_and_missing_labels_are_errors():
    respx.post(f"{BASE}/chores/").mock(
        return_value=httpx.Response(400, json={"error": "Invalid request format"})
    )
    respx.get(f"{BASE}/labels").mock(return_value=httpx.Response(200, json=[{"id": 1, "name": "Bins"}]))
    dt_ = Donetick("http://donetick.test:2021", "tok")
    with pytest.raises(DonetickError, match="HTTP 400"):
        dt_.create_chore({"name": "x"})
    with pytest.raises(DonetickError, match="no label named 'House'"):
        dt_.label_id("House")
    assert dt_.label_id("bins") == 1  # ignoring case


def test_due_at_in_a_named_zone():
    when = due_at(dt.date(2027, 1, 15), "09:30", "America/New_York")
    assert when.isoformat() == "2027-01-15T09:30:00-05:00"
    with pytest.raises(DonetickError, match="isn't a time zone"):
        due_at(dt.date(2027, 1, 15), "09:00", "Mars/Olympus")
    with pytest.raises(DonetickError, match="isn't a time"):
        due_at(dt.date(2027, 1, 15), "nine", "")
    assert due_at(dt.date(2027, 1, 15), "09:00", "").utcoffset() is not None  # this computer's zone


def test_interval_chore_shape():
    first = due_at(dt.date(2027, 3, 1), "09:00", "Europe/London")
    c = interval_chore(
        "Window AC: Clean the filter", 3, "month", first, "<p>x</p>", 4, timezone="Europe/London"
    )
    assert c["frequencyType"] == "interval" and c["frequency"] == 3
    assert c["frequencyMetadata"] == {
        "unit": "months",
        "time": "2027-03-01T09:00:00+00:00",
        "timezone": "Europe/London",
    }
    assert c["nextDueDate"] == "2027-03-01T09:00:00+00:00" and c["isRolling"] is False
    assert c["assignedTo"] == 4 and c["assignees"] == [{"userId": 4}]
    assert c["notification"] is True and c["notificationMetadata"] == {"dueDate": True}
    assert "labelsV2" not in c
    quiet = interval_chore("x: y", 1, "year", first, "", 4, label_id=2, notify=False, rolling=True)
    assert quiet["labelsV2"] == [{"id": 2}] and "notificationMetadata" not in quiet and quiet["isRolling"]
    assert "timezone" not in quiet["frequencyMetadata"]
    with pytest.raises(DonetickError):
        interval_chore("x", 1, "fortnight", first, "", 4)


@respx.mock
def test_file_in_donetick_makes_one_recurring_chore():
    create = mock_donetick()
    cfg = DonetickConfig(url="http://donetick.test:2021", label="House", timezone="America/New_York")
    client = Donetick(cfg.url, "tok")
    link = "https://manuals.example/#read/" + "a" * 64 + "/12"
    chore_id = mt.file_in_donetick(
        client, suggestion(), "Window AC", "Clean the AC filter", dt.date(2026, 11, 24), cfg, link
    )
    assert chore_id == "31"
    body = json.loads(create.calls[0].request.content)
    assert body["name"] == "Window AC: Clean the AC filter"
    assert body["frequency"] == 2 and body["frequencyMetadata"]["unit"] == "weeks"
    assert body["nextDueDate"] == "2026-11-24T09:00:00-05:00"
    assert body["labelsV2"] == [{"id": 2}] and body["assignedTo"] == 4
    assert "Repeat every 2 weeks" in body["description"] and "page 12</a>" in body["description"]
    assert f'href="{link}"' in body["description"]
    assert "“Clean the filter every two weeks.”" in body["description"]


@respx.mock
def test_the_description_is_escaped_and_the_label_optional():
    create = mock_donetick()
    cfg = DonetickConfig(url="http://donetick.test:2021", timezone="UTC")
    s = suggestion(quote='Use <b>only</b> "mild" soap every month', title="")
    mt.file_in_donetick(Donetick(cfg.url, "tok"), s, "", "Wash the tray", dt.date(2026, 12, 1), cfg)
    body = json.loads(create.calls[0].request.content)
    assert body["name"] == "Wash the tray"  # no appliance: the task alone
    assert "&lt;b&gt;only&lt;/b&gt;" in body["description"] and "<b>" not in body["description"]
    assert "LG AC8000 manual" in body["description"] and "<a " not in body["description"]
    assert "labelsV2" not in body
    assert not any(c.request.url.path.endswith("/labels") for c in respx.calls)  # no label: not read
