"""Home Assistant as a source: its devices' maker, model, name and area, to find manuals and offer Homebox items.

Read only. Manual Beagle opens Home Assistant's WebSocket API (`/api/websocket`), signs in with a long-lived
token, and sends four list commands, none of which needs an admin, so a read-only user's token is enough:

- `config/device_registry/list`   every device: manufacturer, model, model_id, name, name_by_user, area_id,
                                  entry_type ("service" for things that aren't devices), disabled_by, config entries
- `config/area_registry/list`     area id -> name
- `config_entries/get`            config entry id -> integration domain (sun, reolink, tplink, ...)
- `config_entries/get` with `type_filter: ["helper"]`, to skip devices that helpers (templates, groups) make

Home Assistant counts every refused token toward its login ban (by default a few in a row ban the address), so
a refused token is reported at once and never retried.

Verified against Home Assistant 2026.10.4's source (homeassistant/components/config/*.py), 2026-11-03.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .. import __version__
from ..normalize import BRAND_ALIASES, brand_key, clean, compact


class HomeAssistantError(Exception):
    """Home Assistant refused a request, didn't answer, or isn't configured."""


class Connection(Protocol):
    """The part of a WebSocket connection this client uses (websockets' sync client, or a test's fake)."""

    def send(self, message: str) -> None:
        """Send one text message."""

    def recv(self, timeout: float | None = None) -> str | bytes:
        """Receive one message."""

    def close(self) -> None:
        """Close the connection."""


Connect = Callable[[str, float], Connection]


def _websockets_connect(url: str, timeout: float) -> Connection:
    """Open a WebSocket with the `websockets` package's sync client."""
    from websockets.sync.client import connect

    return connect(
        url,
        open_timeout=timeout,
        close_timeout=5,
        max_size=64 * 2**20,  # a big house's device registry is a few MB
        user_agent_header=f"ManualBeagle/{__version__}",
    )


def websocket_url(url: str) -> str:
    """Home Assistant's WebSocket address for its base URL: http://ha:8123 -> ws://ha:8123/api/websocket."""
    base = url.strip().rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :] + "/api/websocket"
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :] + "/api/websocket"
    raise HomeAssistantError(f"Home Assistant's address must start with http:// or https://, not {url!r}")


@dataclass
class Device:
    """One device from Home Assistant's registry, with what Manual Beagle reads."""

    id: str
    name: str
    manufacturer: str = ""
    model: str = ""  # the part number to search for: HA's model_id when it has one, else its model
    model_name: str = (
        ""  # HA's model as shown (with model_id set, often a friendly name such as "Hue color lamp")
    )
    area: str = ""
    integration: str = ""  # the domain of its main config entry, such as "reolink"
    entry_type: str = ""  # "service" for what isn't a device (the sun, a weather service, an add-on)
    disabled: bool = False
    helper: bool = False  # made by a helper integration (a template, a group)
    part_of: str = ""  # the device this one is a part of: a socket of a power strip ("Socket for HS300")


@dataclass
class Offer:
    """What an import would do with one device: offer it as a new Homebox item, or skip it, and why."""

    device: Device
    action: str  # new | have | skip
    reason: str = ""
    brand: str = ""  # the maker as it would go into Homebox
    model: str = ""
    location: str = ""
    homebox_item: str = ""  # with action "have": the Homebox item that has this maker and model
    same_model: list[str] = field(
        default_factory=list
    )  # other devices of this maker and model, "Name (Area)"

    def as_dict(self) -> dict:
        """The offer as JSON for the web UI, with the device's fields at the top level."""
        d = asdict(self)
        dev = d.pop("device")
        return {
            "device_id": dev["id"],
            "name": dev["name"],
            "manufacturer": dev["manufacturer"],
            "ha_model": dev["model_name"] or dev["model"],
            "area": dev["area"],
            "integration": dev["integration"],
            **d,
        }

    def notes(self) -> str:
        """The description a new Homebox item gets: where it came from, and other devices of its model."""
        d = self.device
        text = f"From Home Assistant: {d.name}" + (f" ({d.integration})" if d.integration else "") + "."
        if self.same_model:
            text += f" The same model in Home Assistant: {', '.join(self.same_model)}."
        return text


class HomeAssistant:
    """A read-only client for Home Assistant's WebSocket API, signed in with a long-lived token."""

    def __init__(self, url: str, token: str, timeout: float = 30, connect: Connect | None = None) -> None:
        if not url or not token:
            raise HomeAssistantError(
                "Home Assistant needs a URL and a token (config [home_assistant] url, and token_env or token_file)"
            )
        self.url = url.strip().rstrip("/")
        self.ws_url = websocket_url(self.url)
        self._token = token
        self.timeout = timeout
        self._connect = connect or _websockets_connect
        self.version = ""

    def _open(self) -> Connection:
        """Connect and sign in. Raises HomeAssistantError when it can't, or when the token is refused."""
        try:
            conn = self._connect(self.ws_url, self.timeout)
        except Exception as e:  # websockets raises OSError, InvalidURI, InvalidHandshake, TimeoutError...
            raise HomeAssistantError(f"can't reach Home Assistant at {self.ws_url}: {e}") from None
        try:
            hello = self._recv(conn)
            if hello.get("type") != "auth_required":
                raise HomeAssistantError(f"not Home Assistant's WebSocket API: {str(hello)[:200]}")
            conn.send(json.dumps({"type": "auth", "access_token": self._token}))
            answer = self._recv(conn)
            if answer.get("type") != "auth_ok":
                raise HomeAssistantError(
                    "Home Assistant refused the token. Check it before trying again: each refusal counts "
                    "toward Home Assistant's ban on this address."
                )
            self.version = str(answer.get("ha_version") or "")
        except BaseException:
            conn.close()
            raise
        return conn

    def _recv(self, conn: Connection) -> dict:
        try:
            raw = conn.recv(timeout=self.timeout)
        except TimeoutError:
            raise HomeAssistantError(f"Home Assistant didn't answer within {self.timeout:g} s") from None
        except Exception as e:  # the connection closed
            raise HomeAssistantError(f"Home Assistant closed the connection: {e}") from None
        try:
            msg = json.loads(raw)
        except ValueError:
            raise HomeAssistantError("Home Assistant sent something that isn't JSON") from None
        if not isinstance(msg, dict):
            raise HomeAssistantError("Home Assistant sent a message that isn't a JSON object")
        return msg

    def _call(self, conn: Connection, n: int, kind: str, **kw: Any) -> Any:
        """Send command `kind` with id `n` and return its result. Raises HomeAssistantError when it fails."""
        conn.send(json.dumps({"id": n, "type": kind, **kw}))
        while True:
            msg = self._recv(conn)
            if msg.get("id") != n or msg.get("type") != "result":
                continue  # an event or another answer; this client subscribes to nothing
            if not msg.get("success"):
                err = msg.get("error") or {}
                raise HomeAssistantError(f"{kind}: {err.get('code', '?')} {err.get('message', '')}".strip())
            return msg.get("result")

    def status(self) -> str:
        """Sign in and out again; return Home Assistant's version. Raises HomeAssistantError."""
        self._open().close()
        return self.version

    def registry(self) -> dict[str, list]:
        """The raw lists: `devices`, `areas`, `entries` and `helper_entries`, over one connection."""
        conn = self._open()
        try:
            return {
                "devices": self._call(conn, 1, "config/device_registry/list") or [],
                "areas": self._call(conn, 2, "config/area_registry/list") or [],
                "entries": self._call(conn, 3, "config_entries/get") or [],
                "helper_entries": self._call(conn, 4, "config_entries/get", type_filter=["helper"]) or [],
            }
        finally:
            conn.close()

    def devices(self) -> list[Device]:
        """Every device in Home Assistant's registry, with its area's and integration's names."""
        return devices_from_registry(**self.registry())


def devices_from_registry(
    devices: list[dict], areas: list[dict], entries: list[dict], helper_entries: list[dict] | None = None
) -> list[Device]:
    """Turn the registry lists Home Assistant sends into Devices, sorted by area and name."""
    area_names = {a.get("area_id"): a.get("name") or "" for a in areas}
    domains = {e.get("entry_id"): e.get("domain") or "" for e in entries}
    helpers = {e.get("entry_id") for e in helper_entries or []}
    by_id = {d.get("id"): d for d in devices if isinstance(d, dict)}
    out = []
    for d in devices:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        entry_ids = d.get("config_entries") or []
        main = d.get("primary_config_entry") or (entry_ids[0] if entry_ids else None)
        model_name = _real(clean(str(d.get("model") or "")))
        model_id = _real(clean(str(d.get("model_id") or "")))
        out.append(
            Device(
                id=str(d["id"]),
                name=clean(str(d.get("name_by_user") or d.get("name") or "")),
                manufacturer=clean(str(d.get("manufacturer") or "")),
                model=part_number(model_id or model_name),
                model_name=model_name,
                area=area_names.get(d.get("area_id"), ""),
                integration=domains.get(main, ""),
                entry_type=str(d.get("entry_type") or ""),
                disabled=bool(d.get("disabled_by")),
                helper=bool(entry_ids) and all(e in helpers for e in entry_ids),
                part_of=_part_of(d, by_id.get(d.get("via_device_id"))),
            )
        )
    return sorted(out, key=lambda d: (d.area.lower() or "￿", d.name.lower()))


def part_number(model: str) -> str:
    """A model as Manual Beagle searches for it, without a variant in brackets or a trailing "series".

    "PD-6WCL (PD-6WCL-XX)" -> "PD-6WCL", "HL-L2395DW series" -> "HL-L2395DW"; others unchanged.
    """
    m = re.match(r"^(.+?)\s*\(([^()]*)\)$", model)
    model = m.group(1).strip() if m and m.group(1).strip() else model
    return re.sub(r"\s+series$", "", model, flags=re.IGNORECASE) or model


# What some integrations put where a model belongs when they don't know it.
_PLACEHOLDERS = {"unknown", "n/a", "na", "none", "null", "generic", "-", "?"}


def _real(model: str) -> str:
    """The model, or "" for a placeholder such as "Unknown"."""
    return "" if model.lower() in _PLACEHOLDERS else model


def _part_of(d: dict, parent: dict | None) -> str:
    """The parent's name when `d` is part of it, else "".

    Part of it means the same maker, and a model that names the parent's model ("Socket for HS300" under an HS300
    power strip).
    """
    if not parent:
        return ""
    maker, parent_maker = str(d.get("manufacturer") or ""), str(parent.get("manufacturer") or "")
    model, parent_model = compact(str(d.get("model") or "")), compact(str(parent.get("model") or ""))
    if brand_key(maker) != brand_key(parent_maker) or not parent_model or model == parent_model:
        return ""
    return clean(str(parent.get("name_by_user") or parent.get("name") or "")) if parent_model in model else ""


# Corporate endings that say nothing about the brand: "Lutron Electronics Co., Inc" -> "Lutron Electronics".
_CORPORATE = re.compile(
    r"[\s,]+(inc|incorporated|co|company|corp|corporation|ltd|limited|llc|l\.l\.c|gmbh|ag|s\.?a|b\.?v|n\.?v|plc|"
    r"pty|oy|ab|srl|s\.?p\.?a|k\.?k)\.?$",
    re.IGNORECASE,
)


def maker_name(manufacturer: str, known: Callable[[str], str] | None = None) -> str:
    """The brand a manufacturer string names, as it would go into Homebox.

    Corporate endings go ("Signify Netherlands B.V." -> "Signify Netherlands"). Then `known(name)`, which returns
    the canonical brand for a name an adapter or alias answers to (else ""), is asked for the name and for each
    shorter run of its first words, so "Lutron Electronics" becomes "Lutron" when an adapter knows Lutron.
    """
    name = clean(manufacturer)
    while True:
        shorter = _CORPORATE.sub("", name).strip(" ,")
        if shorter == name or not shorter:
            break
        name = shorter
    words = name.split()
    for n in range(len(words), 0, -1):
        candidate = " ".join(words[:n])
        canon = (known(candidate) if known else "") or BRAND_ALIASES.get(brand_key(candidate), "")
        if canon:
            return canon
    return name


def registry_brands(reg: Any) -> Callable[[str], str]:
    """A `known` function for `maker_name` from an adapter Registry: the brand its adapters answer to."""

    def known(name: str) -> str:
        found = reg.for_brand(name)
        return found[0].brand if found else ""

    return known


def _key(maker: str, model: str) -> tuple[str, str]:
    return brand_key(maker), compact(model)


def plan_import(
    devices: list[Device],
    items: list[Any],
    skip_integrations: list[str] | None = None,
    known: Callable[[str], str] | None = None,
) -> list[Offer]:
    """Decide, for each device, whether to offer it as a new Homebox item.

    `items` are Homebox's items (anything with `name`, `manufacturer` and `model`). A device is skipped when it's
    disabled, a service, made by a helper or by an integration in `skip_integrations`, or has no maker or model.
    One whose maker and model a Homebox item has is "have". Of several devices with the same maker and model,
    the first is offered and names the others, which are skipped: Homebox gets one item per maker and model, as
    the "have" rule would make it on the next import anyway.
    """
    skip = {s.lower() for s in skip_integrations or []}
    have: dict[tuple[str, str], str] = {}
    for it in items:
        if it.manufacturer and it.model:
            have.setdefault(_key(maker_name(it.manufacturer, known), part_number(it.model)), it.name)
    first: dict[tuple[str, str], Offer] = {}
    out = []
    for d in devices:
        brand = maker_name(d.manufacturer, known) if d.manufacturer else ""
        o = Offer(device=d, action="skip", brand=brand, model=d.model, location=d.area)
        out.append(o)
        if d.disabled:
            o.reason = "disabled in Home Assistant"
        elif d.entry_type == "service":
            o.reason = "a service, not a physical thing"
        elif d.helper:
            o.reason = "made by a helper, not a physical thing"
        elif d.part_of:
            o.reason = f"part of {d.part_of}"
        elif re.search(r"\bgroup\b", d.model_name or d.model, re.IGNORECASE):
            o.reason = "a group of devices, not a physical thing"
        elif d.integration.lower() in skip:
            o.reason = f"{d.integration}: not a physical thing"
        elif not d.manufacturer or not d.model:
            o.reason = "no " + ("maker" if not d.manufacturer else "model") + " in Home Assistant"
        else:
            keys = [_key(brand, d.model)]
            if d.model_name and compact(d.model_name) != compact(d.model):
                keys.append(_key(brand, part_number(d.model_name)))
            match = next((have[k] for k in keys if k in have), None)
            if match is not None:
                o.action, o.homebox_item, o.reason = "have", match, f"Homebox has it: {match}"
            elif keys[0] in first:
                lead = first[keys[0]]
                o.reason = f"same maker and model as {lead.device.name}"
                lead.same_model.append(d.name + (f" ({d.area})" if d.area else ""))
            else:
                o.action, o.reason = "new", "not in Homebox"
                first[keys[0]] = o
    return out


def summary(offers: list[Offer], dry_run: bool = False) -> str:
    """The count of each action, as one line with glyphs and words."""
    n = {a: sum(1 for o in offers if o.action == a) for a in ("new", "have", "skip")}
    return (
        f"{'DRY RUN, nothing written. ' if dry_run else ''}"
        f"+ {n['new']} new, ✓ {n['have']} in Homebox already, – {n['skip']} skipped"
    )


def add_to_homebox(hb: Any, offers: list[Offer]) -> Iterator[tuple[Offer, str, str]]:
    """Create a Homebox item for each offer whose action is "new", in its area's location.

    Locations are made when Homebox has none of that name. Yields (offer, item id, "") for each one made and
    (offer, "", error) for each Homebox refused; offers that aren't "new" are left out.
    """
    from ..sinks.homebox import HomeboxError

    locations = None
    for o in offers:
        if o.action != "new":
            continue
        try:
            parent = None
            if o.location:
                if locations is None:
                    locations = hb.locations()
                parent = locations.get(o.location) or hb.create_location(o.location)
                locations[o.location] = parent
            item_id = hb.create_item(
                o.device.name or f"{o.brand} {o.model}", o.brand, o.model, parent, o.notes()
            )
        except HomeboxError as e:
            yield o, "", str(e)
            continue
        yield o, item_id, ""


def from_config(cfg: Any) -> HomeAssistant:
    """A client from the config's `[home_assistant]` table. Raises HomeAssistantError when it isn't set up.

    Also when the address was changed on the Settings page alone: the token goes only to the address set beside it.
    """
    t = cfg.home_assistant
    held = t.key_held_back(t.url)
    if held:
        raise HomeAssistantError(f"not signing in: {held}")
    return HomeAssistant(t.url, t.token())
