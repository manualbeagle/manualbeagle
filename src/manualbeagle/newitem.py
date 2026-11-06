"""Add a scanned appliance to Homebox: a new item (or one already there), its plate photo, and its manual.

The web UI's Find page offers this after a rating-plate scan, once the person has confirmed the brand and model.
It goes in two steps, and nothing is written before the second:

1. `preview()` reads Homebox only. It suggests a name from the brand, the kind of product and the model, lists
   Homebox's locations to pick from, and finds items that already have the same maker and model number
   (`same_item`), so the person can add to one of those instead of making a duplicate.
2. `add()` writes what the person confirmed: it creates the item (or uses the existing one), sets its serial
   number, attaches the plate photo, and then finds and attaches the manual with the same rule as
   `manualbeagle sync homebox` (`sync.plan_item`): a PDF whose text names the model is uploaded, otherwise the
   maker's link is attached (when `[homebox] attach_links` is on), otherwise nothing.

The kind of product ("Dishwasher", "Water heater") is found by rules alone (`product_kind`): words from a fixed
list, looked for in the plate's OCR text and the titles of the documents Find returned.
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import BRAND_ALIASES, brand_key, clean
from .sinks.homebox import Homebox, Item

# Kinds of product, as they're named in an item's suggested name. Each is found by its own words (regular
# expressions, matched whole-word and case-blind). Longer, more specific names come first: a phrase that matches
# is taken out of the text before the shorter ones are looked for, so "pressure washer" never counts as "washer"
# and "robot vacuum" never as "vacuum". Words that also mean something else on a plate ("range" as in
# "operating range", "fan" as in "fan speed") are left out or only matched in a longer phrase.
KINDS: list[tuple[str, str]] = [
    ("Washer dryer", r"washer[ -/]dryer|washer and dryer"),
    ("Heat pump water heater", r"heat pump water heater|hybrid water heater"),
    ("Tankless water heater", r"tankless water heater|tankless"),
    ("Water heater", r"water heater"),
    ("Mini-split", r"mini[ -]?split|ductless"),
    ("Window air conditioner", r"window air conditioner|window (?:type )?a/?c\b"),
    ("Portable air conditioner", r"portable air conditioner"),
    ("Air conditioner", r"air conditioner|room air conditioner"),
    ("Heat pump", r"heat pump"),
    ("Dehumidifier", r"dehumidifier"),
    ("Humidifier", r"humidifier"),
    ("Air purifier", r"air purifier|air cleaner"),
    ("Furnace", r"furnace"),
    ("Thermostat", r"thermostat"),
    ("Robot vacuum", r"robot(?:ic)? vacuum|robot cleaner"),
    ("Pressure washer", r"pressure washer"),
    ("Dishwasher", r"dish ?washer"),
    ("Refrigerator", r"refrigerator|fridge"),
    ("Freezer", r"freezer"),
    ("Washer", r"washing machine|clothes washer|washer"),
    ("Dryer", r"clothes dryer|dryer"),
    ("Range hood", r"range hood|vent hood"),
    ("Range", r"(?:electric|gas|induction|slide-in|freestanding) range"),
    ("Microwave", r"microwave(?: oven)?"),
    ("Wall oven", r"wall oven"),
    ("Cooktop", r"cooktop"),
    ("Oven", r"oven"),
    ("Garbage disposer", r"garbage disposal|food waste disposer|disposer"),
    ("Water softener", r"water softener"),
    ("Sump pump", r"sump pump"),
    ("Garage door opener", r"garage door opener"),
    ("Portable power station", r"power station"),
    ("Generator", r"generator"),
    ("Inverter", r"inverter"),
    ("UPS", r"uninterruptible power supply|\bups\b"),
    ("EV charger", r"ev charger|charging station|evse"),
    ("Lawn mower", r"lawn ?mower|mower"),
    ("Air compressor", r"air compressor|compressor"),
    ("Printer", r"printer"),
    ("Monitor", r"monitor"),
    ("TV", r"television|\btv\b"),
    ("Vacuum", r"vacuum cleaner|vacuum"),
    ("Drill", r"drill(?:/driver)?"),
    ("Impact driver", r"impact driver"),
    ("Saw", r"(?:circular|miter|mitre|table|band|reciprocating) saw"),
]
_KIND_RES = [(name, re.compile(rf"\b(?:{pat})\b", re.I)) for name, pat in KINDS]


def product_kind(texts: list[str]) -> str:
    """The kind of product the texts name most often (see `KINDS`), or "" when they name none.

    A tie goes to the kind earlier in `KINDS`. Each phrase found is removed before shorter kinds are looked for.
    """
    text = "\n".join(t for t in texts if t)
    best, best_n = "", 0
    for name, rx in _KIND_RES:
        found = rx.findall(text)
        if len(found) > best_n:
            best, best_n = name, len(found)
        if found:
            text = rx.sub(" ", text)
    return best


def canonical_brand(brand: str, reg=None) -> str:
    """The brand as Manual Beagle spells it: its adapter's name (from `reg`, a Registry), else the shared alias."""
    if reg is not None:
        adapters = reg.for_brand(brand)
        if adapters:
            return adapters[0].brand
    return BRAND_ALIASES.get(brand_key(brand)) or clean(brand)


def suggest_name(brand: str, model: str, kind: str = "") -> str:
    """An item name such as "LG Dishwasher LDF5545ST": the brand as the person confirmed it, the kind and model."""
    return " ".join(p for p in (clean(brand), kind.strip(), model.strip()) if p)[:255]


def _model_key(model: str) -> str:
    """A model number with case, spaces and punctuation removed, for comparing two spellings of it."""
    return re.sub(r"[^0-9a-z]", "", model.lower())


def _brand_key(brand: str, reg=None) -> str:
    return re.sub(r"[^0-9a-z]", "", canonical_brand(brand, reg).lower())


def same_item(item: Item, brand: str, model: str, reg=None) -> bool:
    """Whether a Homebox item has this maker and model number.

    Brands are compared by their canonical name (so "Honeywell" matches "Resideo", and with `reg` "GE" matches
    "GE Appliances"), model numbers without case, spaces or punctuation.
    """
    if not item.manufacturer or not item.model:
        return False
    return _model_key(item.model) == _model_key(model) and _brand_key(item.manufacturer, reg) == _brand_key(
        brand, reg
    )


def matches(hb: Homebox, brand: str, model: str, reg=None) -> list[Item]:
    """The items already in Homebox with this maker and model number (Homebox's search, then `same_item`)."""
    seen, out = set(), []
    for item in hb.search(model):
        if item.id not in seen and same_item(item, brand, model, reg):
            seen.add(item.id)
            out.append(item)
    return out


def item_out(hb: Homebox, item: Item) -> dict:
    """An existing item for the page: its name, maker, model, serial, location, how many manuals and photos."""
    return {
        "id": item.id,
        "name": item.name,
        "manufacturer": item.manufacturer,
        "model": item.model,
        "serial": item.serial,
        "location": item.location,
        "manuals": len(item.manuals),
        "photos": sum(1 for a in item.attachments if (a.get("type") or "") == "photo"),
        "url": hb.item_url(item.id),
    }


def preview(hb: Homebox, brand: str, model: str, serial: str, texts: list[str], reg=None) -> dict:
    """What `add` would do, read from Homebox without writing: a suggested name, the locations, any matches.

    `texts` are what the kind of product is read from: the plate's OCR text and the titles Find returned.
    """
    kind = product_kind(texts)
    return {
        "brand": clean(brand),  # as confirmed; the canonical name is only for finding duplicates
        "model": model,
        "serial": serial,
        "kind": kind,
        "name": suggest_name(brand, model, kind),
        "locations": hb.location_choices(),
        "matches": [item_out(hb, i) for i in matches(hb, brand, model, reg)],
    }


@dataclass
class NewItem:
    """What the person confirmed: the item's fields, or an existing item to add to, and the plate photo."""

    name: str
    brand: str
    model: str
    serial: str = ""
    location_id: str = ""
    existing_id: str = ""  # add to this item instead of creating one
    photo: Path | None = None  # the plate photo to attach, or None
    notes: str = field(default="Added by Manual Beagle from a rating-plate scan.")


def photo_name(brand: str, model: str, path: Path) -> str:
    """The file name the plate photo gets in Homebox: 'Brand Model - rating plate.jpg'."""
    safe = "".join(ch if ch.isalnum() or ch in " -._" else "_" for ch in f"{brand} {model} - rating plate")
    return safe.strip() + (path.suffix.lower() or ".jpg")


def add(hb: Homebox, new: NewItem, plan_manual, attach_links: bool) -> Iterator[dict]:
    """Write a confirmed item to Homebox, yielding one row per step: {step, state, text, url?}.

    `state` is ok, skip or bad. `plan_manual(item)` runs `sync.plan_item` for the item and returns its Plan;
    the plan's PDF is uploaded, or its link attached when `attach_links`. A step Homebox refuses raises
    HomeboxError after its row is yielded, except the manual's, whose refusal is reported in its row.
    """
    from .sync import attach

    if new.existing_id:
        item = hb.item(new.existing_id)
        yield {"step": "item", "state": "ok", "text": f"using {item.name}", "url": hb.item_url(item.id)}
        created = False
    else:
        item_id = hb.create_item(new.name, new.brand, new.model, new.location_id or None, new.notes)
        yield {"step": "item", "state": "ok", "text": f"created {new.name}", "url": hb.item_url(item_id)}
        created = True
        if new.serial:
            hb.set_serial(item_id, new.serial)
            yield {"step": "serial", "state": "ok", "text": f"serial {new.serial}"}
        item = hb.item(item_id)
    if new.photo is not None:
        hb.attach_photo(item.id, new.photo, photo_name(new.brand, new.model, new.photo), primary=created)
        yield {"step": "photo", "state": "ok", "text": "attached the rating-plate photo"}
    p = plan_manual(item)
    row = {"step": "manual", "state": "skip", "text": p.reason, "url": p.url, "document": ""}
    if p.file:
        row["document"] = Path(p.file).stem
    if p.action == "link" and not attach_links:
        row["text"] = f"not attached ([homebox] attach_links is off): {p.reason}"
    elif p.action in ("upload", "link"):
        try:
            attach(hb, p)
            what = "uploaded the PDF" if p.action == "upload" else "attached the maker's link"
            row.update(state="ok", text=f"{what}: {p.reason}")
        except Exception as e:  # HomeboxError, OSError: the item and photo are in; say what failed
            row.update(state="bad", text=f"Homebox refused the manual: {e}")
    elif p.action == "none":
        row.update(state="bad", text=f"no manual found: {p.reason}")
    yield row


def sync_args(min_score: float = 60) -> argparse.Namespace:
    """The options `sync.plan_item` needs, as `sync homebox` uses them by default."""
    return argparse.Namespace(min_score=min_score, links_only=False, force=False, allow_search_links=False)
