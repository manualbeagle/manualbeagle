"""Home Assistant as a source, against a fake WebSocket (message shapes from Home Assistant 2026.10's source)."""

import json

import pytest

from manualbeagle.cli import main
from manualbeagle.sinks.homebox import Item
from manualbeagle.sources import homeassistant as ha_mod
from manualbeagle.sources.homeassistant import (
    Device,
    HomeAssistant,
    HomeAssistantError,
    add_to_homebox,
    devices_from_registry,
    maker_name,
    part_number,
    plan_import,
    websocket_url,
)

AREAS = [{"area_id": "kitchen", "name": "Kitchen"}, {"area_id": "laundry", "name": "Laundry"}]
ENTRIES = [
    {"entry_id": "e-lutron", "domain": "lutron_caseta"},
    {"entry_id": "e-sun", "domain": "sun"},
    {"entry_id": "e-lg", "domain": "smartthinq"},
    {"entry_id": "e-tpl", "domain": "tplink"},
    {"entry_id": "e-tmpl", "domain": "template"},
    {"entry_id": "e-hue", "domain": "hue"},
]
DEVICES = [
    {
        "id": "d1",
        "name": "Kitchen dimmer",
        "name_by_user": None,
        "manufacturer": "Lutron Electronics Co., Inc",
        "model": "PD-6WCL (PD-6WCL-XX)",
        "model_id": None,
        "area_id": "kitchen",
        "config_entries": ["e-lutron"],
        "primary_config_entry": "e-lutron",
        "entry_type": None,
        "disabled_by": None,
    },
    {
        "id": "d2",
        "name": "Lutron dimmer 2",
        "name_by_user": "Hall dimmer",
        "manufacturer": "Lutron Electronics Co., Inc",
        "model": "PD-6WCL",
        "area_id": None,
        "config_entries": ["e-lutron"],
        "entry_type": None,
        "disabled_by": None,
    },
    {
        "id": "d3",
        "name": "Sun",
        "manufacturer": None,
        "model": None,
        "config_entries": ["e-sun"],
        "entry_type": "service",
        "disabled_by": None,
    },
    {
        "id": "d4",
        "name": "Washer",
        "manufacturer": "LG Electronics",
        "model": "WM4000HWA",
        "area_id": "laundry",
        "config_entries": ["e-lg"],
        "entry_type": None,
        "disabled_by": None,
    },
    {
        "id": "d5",
        "name": "Old plug",
        "manufacturer": "TP-Link",
        "model": "HS103",
        "config_entries": ["e-tpl"],
        "entry_type": None,
        "disabled_by": "user",
    },
    {
        "id": "d6",
        "name": "Average temperature",
        "manufacturer": "Home",
        "model": "Template",
        "config_entries": ["e-tmpl"],
        "entry_type": None,
        "disabled_by": None,
    },
    {
        "id": "d7",
        "name": "Lamp",
        "manufacturer": "Signify Netherlands B.V.",
        "model": "Hue color lamp",
        "model_id": "LCA001",
        "area_id": "kitchen",
        "config_entries": ["e-hue"],
        "entry_type": None,
        "disabled_by": None,
    },
    {
        "id": "d8",
        "name": "Mystery",
        "manufacturer": "Acme",
        "model": "",
        "config_entries": [],
        "disabled_by": None,
    },
    {"id": "d9", "name": "Strip", "manufacturer": "TP-Link", "model": "HS300", "config_entries": ["e-tpl"]},
    {
        "id": "d10",
        "name": "Strip socket 1",
        "manufacturer": "TP-Link",
        "model": "Socket for HS300",
        "via_device_id": "d9",
        "config_entries": ["e-tpl"],
    },
    {
        "id": "d11",
        "name": "Home group",
        "manufacturer": "Google",
        "model": "Google Cast Group",
        "config_entries": [],
    },
    {"id": "d12", "name": "Blinds", "manufacturer": "Rollo", "model": "Unknown", "config_entries": []},
    {
        "id": "d13",
        "name": "Printer",
        "manufacturer": "Brother",
        "model": "HL-L2395DW series",
        "config_entries": [],
    },
    {
        "id": "d14",
        "name": "printer-brother",
        "manufacturer": "Brother",
        "model": "HL-L2395DW",
        "config_entries": [],
    },
]


class FakeWS:
    """A Home Assistant WebSocket: auth, then answers each command from `results`."""

    def __init__(self, results: dict, token: str = "good") -> None:
        self.results = results
        self.token = token
        self.inbox = [{"type": "auth_required", "ha_version": "2026.10.4"}]
        self.sent: list[dict] = []
        self.closed = False

    def send(self, message: str) -> None:
        msg = json.loads(message)
        self.sent.append(msg)
        if msg["type"] == "auth":
            ok = msg["access_token"] == self.token
            self.inbox.append({"type": "auth_ok" if ok else "auth_invalid", "ha_version": "2026.10.4"})
            return
        key = msg["type"] + ("+helper" if msg.get("type_filter") == ["helper"] else "")
        self.inbox.append({"type": "event", "id": 99, "event": {}})  # noise the client must skip
        if key in self.results:
            self.inbox.append(
                {"id": msg["id"], "type": "result", "success": True, "result": self.results[key]}
            )
        else:
            self.inbox.append(
                {
                    "id": msg["id"],
                    "type": "result",
                    "success": False,
                    "error": {"code": "unauthorized", "message": "no"},
                }
            )

    def recv(self, timeout=None):
        return json.dumps(self.inbox.pop(0))

    def close(self) -> None:
        self.closed = True


RESULTS = {
    "config/device_registry/list": DEVICES,
    "config/area_registry/list": AREAS,
    "config_entries/get": ENTRIES,
    "config_entries/get+helper": [{"entry_id": "e-tmpl", "domain": "template"}],
}


def client(fake: FakeWS, token: str = "good") -> HomeAssistant:
    return HomeAssistant("http://ha.test:8123", token, connect=lambda url, timeout: fake)


def test_websocket_url():
    assert websocket_url("http://ha.test:8123/") == "ws://ha.test:8123/api/websocket"
    assert websocket_url("https://ha.example") == "wss://ha.example/api/websocket"
    with pytest.raises(HomeAssistantError):
        websocket_url("ha.test:8123")


def test_reads_the_registry_read_only():
    fake = FakeWS(RESULTS)
    devices = {d.id: d for d in client(fake).devices()}
    assert fake.closed
    # Only list commands were sent: Manual Beagle never changes anything in Home Assistant.
    assert [m["type"] for m in fake.sent] == [
        "auth",
        "config/device_registry/list",
        "config/area_registry/list",
        "config_entries/get",
        "config_entries/get",
    ]
    assert devices["d1"].area == "Kitchen" and devices["d1"].integration == "lutron_caseta"
    assert devices["d1"].model == "PD-6WCL"  # the parenthesised variant is dropped
    assert devices["d2"].name == "Hall dimmer"  # the name the user gave wins
    assert devices["d7"].model == "LCA001" and devices["d7"].model_name == "Hue color lamp"
    assert devices["d6"].helper and devices["d3"].entry_type == "service" and devices["d5"].disabled


def test_refused_token_is_not_retried():
    fake = FakeWS(RESULTS, token="other")
    with pytest.raises(HomeAssistantError, match="refused the token"):
        client(fake).devices()
    assert [m["type"] for m in fake.sent] == ["auth"]
    assert fake.closed


def test_failed_command_is_an_error():
    fake = FakeWS({"config/area_registry/list": []})
    with pytest.raises(HomeAssistantError, match="unauthorized"):
        client(fake).devices()


def test_status_gives_the_version():
    assert client(FakeWS(RESULTS)).status() == "2026.10.4"


def test_needs_url_and_token():
    with pytest.raises(HomeAssistantError, match="URL and a token"):
        HomeAssistant("", "t")


def test_maker_name_and_part_number():
    known = {"lutron": "Lutron", "lg": "LG"}.get
    assert maker_name("Lutron Electronics Co., Inc", lambda n: known(n.lower(), "")) == "Lutron"
    assert maker_name("Signify Netherlands B.V.") == "Signify Netherlands"
    assert maker_name("LG Electronics") == "LG"  # a shared alias in normalize.py
    assert part_number("HS200(US)") == "HS200"
    assert part_number("WM4000HWA") == "WM4000HWA"


def offers_by_id():
    devices = devices_from_registry(DEVICES, AREAS, ENTRIES, [{"entry_id": "e-tmpl"}])
    items = [Item(id="i1", name="Washing machine", manufacturer="LG", model="WM4000HWA")]
    known = {"lutron": "Lutron"}.get
    offers = plan_import(devices, items, ["sun", "template"], lambda n: known(n.lower(), ""))
    return {o.device.id: o for o in offers}


def test_plan_import_decides_each_device():
    o = offers_by_id()
    assert o["d1"].action == "new" and o["d1"].brand == "Lutron" and o["d1"].location == "Kitchen"
    assert o["d1"].same_model == ["Hall dimmer"]
    assert o["d2"].action == "skip" and "same maker and model" in o["d2"].reason
    assert o["d3"].action == "skip" and "service" in o["d3"].reason
    assert o["d4"].action == "have" and o["d4"].homebox_item == "Washing machine"
    assert o["d5"].action == "skip" and "disabled" in o["d5"].reason
    assert o["d6"].action == "skip" and "helper" in o["d6"].reason
    assert o["d7"].action == "new" and o["d7"].model == "LCA001"
    assert o["d8"].action == "skip" and "no model" in o["d8"].reason
    assert o["d9"].action == "new" and o["d10"].reason == "part of Strip"
    assert o["d11"].action == "skip" and "group" in o["d11"].reason
    assert o["d12"].action == "skip" and "no model" in o["d12"].reason  # "Unknown" is no model
    assert o["d13"].action == "new" and o["d13"].model == "HL-L2395DW"
    assert o["d14"].action == "skip" and "same maker and model as Printer" in o["d14"].reason
    assert "The same model in Home Assistant: Hall dimmer." in o["d1"].notes()
    assert ha_mod.summary(list(o.values()), dry_run=True).startswith("DRY RUN")


def test_homebox_match_uses_the_friendly_model_too():
    d = Device(id="x", name="Lamp", manufacturer="Signify", model="LCA001", model_name="Hue color lamp")
    items = [Item(id="i", name="Lamp", manufacturer="Signify", model="Hue color lamp")]
    assert plan_import([d], items)[0].action == "have"


class FakeHomebox:
    def __init__(self):
        self.locs = {"Kitchen": "L1"}
        self.made: list[tuple] = []

    def locations(self):
        return dict(self.locs)

    def create_location(self, name):
        self.locs[name] = f"L-{name}"
        return self.locs[name]

    def create_item(self, name, manufacturer, model, parent_id, notes=""):
        self.made.append((name, manufacturer, model, parent_id))
        return f"I{len(self.made)}"


def test_add_to_homebox_makes_new_items_only():
    hb = FakeHomebox()
    done = list(add_to_homebox(hb, list(offers_by_id().values())))
    made = [(o.device.id, item) for o, item, _err in done]
    assert made == [("d1", "I1"), ("d7", "I2"), ("d13", "I3"), ("d9", "I4")]
    assert hb.made[0] == ("Kitchen dimmer", "Lutron", "PD-6WCL", "L1")


def test_cli_import_dry_run(monkeypatch, tmp_path, capsys):
    fake = FakeWS(RESULTS)
    monkeypatch.setattr(ha_mod, "_websockets_connect", lambda url, timeout: fake)
    monkeypatch.setenv("HA_TOKEN", "good")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'home = "{tmp_path.as_posix()}"\n[home_assistant]\nurl = "http://ha.test:8123"\n', "utf-8"
    )
    assert main(["--config", str(cfg), "ha", "import", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "+ new" in out and "Kitchen dimmer" in out and "DRY RUN, nothing written" in out
    assert "Homebox isn't set up" in out


def test_cli_import_without_a_terminal_writes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ha_mod, "_websockets_connect", lambda url, timeout: FakeWS(RESULTS))
    monkeypatch.setenv("HA_TOKEN", "good")
    hb = FakeHomebox()
    hb.items = lambda: []
    hb.close = lambda: None
    monkeypatch.setattr("manualbeagle.sync.homebox", lambda cfg: hb)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'home = "{tmp_path.as_posix()}"\n[home_assistant]\nurl = "http://ha.test:8123"\n', "utf-8"
    )
    assert main(["--config", str(cfg), "ha", "import"]) == 0
    assert "not written" in capsys.readouterr().out and hb.made == []
    assert main(["--config", str(cfg), "ha", "import", "--yes"]) == 0
    assert [m[0] for m in hb.made] == ["Kitchen dimmer", "Lamp", "Washer", "Printer", "Strip"]
