"""Homebox (sysadminsmedia/homebox, v0.27+): a source of items and a sink for manuals.

API used (all under /api/v1, `Authorization: Bearer <api key>`; keys come from /users/self/api-keys):
- GET  /entities?page=&pageSize=   summaries of items (both items and locations are "entities"); `isLocation=true`
                                   lists the locations instead; `q=` searches names, serial and model numbers,
                                   manufacturers and more
- GET  /entities/{id}              full entity: manufacturer, modelNumber, attachments, parent (its location)
- PUT  /entities/{id}              replace an entity's fields (EntityUpdate); used to set a serial number, which
                                   POST /entities doesn't take, by sending back what GET returned plus the serial
- GET  /entity-types               which types are locations (isLocation) and which are items
- POST /entities                   create an item or a location (EntityCreate: no serialNumber)
- POST /entities/{id}/attachments  multipart upload: file, name, type=manual|photo, primary=true|false
- POST /entities/{id}/attachments/external  JSON {source_type: link, external_id: <url>, title, attachment_type}
- GET  /entities/{id}/maintenance  the item's maintenance log
- POST /entities/{id}/maintenance  JSON {name, description, scheduledDate: "YYYY-MM-DD", completedDate, cost};
                                   name is required (at most 255), description at most 2500, and one of the
                                   dates must be set. Homebox's notifier sends on the scheduled day.

Verified against Homebox v0.27.1's OpenAPI document and handlers (2026-11-03; maintenance:
handlers/v1/v1_ctrl_maint_entry.go, repo/repo_maintenance_entry.go, services/service_background.go).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .. import __version__


class HomeboxError(Exception):
    """Homebox refused a request, or isn't configured."""

    pass


@dataclass
class Item:
    """A Homebox item, with the fields Manual Beagle reads."""

    id: str
    name: str
    manufacturer: str = ""
    model: str = ""
    location: str = ""
    attachments: list[dict] = field(default_factory=list)
    serial: str = ""

    @property
    def manuals(self) -> list[dict]:
        """The item's attachments of type manual."""
        return [a for a in self.attachments if (a.get("type") or "") == "manual"]

    @property
    def manual_urls(self) -> set[str]:
        """The URLs of the item's manual attachments that are links."""
        out = set()
        for a in self.manuals:
            for k in ("externalId", "external_id", "title"):
                v = a.get(k) or (a.get("document") or {}).get(k)
                if isinstance(v, str) and v.startswith("http"):
                    out.add(v)
        return out


class Homebox:
    """A client for Homebox's REST API (v0.27+), authenticated with an API key."""

    def __init__(self, url: str, token: str, timeout: float = 60) -> None:
        if not url or not token:
            raise HomeboxError(
                "Homebox needs a URL and an API key (config [homebox] url and token_env/token_file)"
            )
        self.base = url.rstrip("/") + "/api/v1"
        self.http = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}", "User-Agent": f"ManualBeagle/{__version__}"},
        )
        self._types: list[dict] | None = None

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self.http.close()

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = self.http.request(method, self.base + path, **kw)
        if r.status_code >= 400:
            raise HomeboxError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
        return r.json() if r.content else None

    def status(self) -> dict:
        """Homebox's /status answer, including its build version."""
        return self._req("GET", "/status")

    def entity_types(self) -> list[dict]:
        """Homebox's entity types (cached), which say which are locations and which are items."""
        if self._types is None:
            self._types = self._req("GET", "/entity-types") or []
        return self._types

    def type_id(self, location: bool) -> str:
        """The id of the first location type (`location=True`) or item type."""
        for t in self.entity_types():
            if bool(t.get("isLocation")) == location:
                return t["id"]
        raise HomeboxError(f"no {'location' if location else 'item'} entity type in this Homebox")

    def summaries(self, locations: bool = False) -> list[dict]:
        """Every item's summary (or, with `locations`, every location's), page by page.

        Homebox's list answers with items only unless it's asked for locations (`isLocation=true`).
        """
        out, page = [], 1
        while True:
            params: dict[str, Any] = {"page": page, "pageSize": 100}
            if locations:
                params["isLocation"] = "true"
            res = self._req("GET", "/entities", params=params)
            items = res.get("items") or []
            out += items
            if len(out) >= (res.get("total") or 0) or not items:
                return out
            page += 1

    def locations(self) -> dict[str, str]:
        """Location name -> id."""
        return {e["name"]: e["id"] for e in self.summaries(locations=True) if _is_location(e) is not False}

    def items(self) -> list[Item]:
        """Every item (not location) with its manufacturer, model number, location and attachments."""
        return [
            self.item(s["id"]) for s in self.summaries() if not (s.get("entityType") or {}).get("isLocation")
        ]

    def entity(self, entity_id: str) -> dict:
        """One entity's full record, as Homebox returns it (EntityOut)."""
        return self._req("GET", f"/entities/{entity_id}")

    def item(self, entity_id: str) -> Item:
        """One item with its manufacturer, model number, serial, location and attachments."""
        return _item(self.entity(entity_id))

    def search(self, query: str) -> list[Item]:
        """The items (not locations) Homebox's search finds for `query`, each read in full.

        Homebox searches names, descriptions, serial and model numbers and manufacturers; the caller decides
        which of the results really match. At most 50 are read.
        """
        res = self._req("GET", "/entities", params={"q": query, "page": 1, "pageSize": 50}) or {}
        return [
            self.item(s["id"])
            for s in res.get("items") or []
            if not (s.get("entityType") or {}).get("isLocation")
        ]

    def location_choices(self) -> list[dict]:
        """Every location as {id, name, label}, sorted by label; the label adds the parent's name when it has one.

        For a list to pick from, where two rooms called "Closet" need telling apart.
        """
        out = []
        for e in self.summaries(locations=True):
            if _is_location(e) is False:
                continue
            parent = (e.get("parent") or {}).get("name") or ""
            label = f"{e['name']} ({parent})" if parent else e["name"]
            out.append({"id": e["id"], "name": e["name"], "label": label})
        return sorted(out, key=lambda x: x["label"].lower())

    def create_location(self, name: str) -> str:
        """Create a location and return its id."""
        e = self._req("POST", "/entities", json={"name": name, "entityTypeId": self.type_id(True)})
        return e["id"]

    def create_item(
        self, name: str, manufacturer: str, model: str, parent_id: str | None, notes: str = ""
    ) -> str:
        """Create an item with one unit, optionally inside a location, and return its id."""
        body: dict[str, Any] = {
            "name": name,
            "entityTypeId": self.type_id(False),
            "manufacturer": manufacturer,
            "modelNumber": model,
            "quantity": 1,
        }
        if parent_id:
            body["parentId"] = parent_id
        if notes:
            body["description"] = notes
        return self._req("POST", "/entities", json=body)["id"]

    def attach_file(self, entity_id: str, path: Path, name: str) -> None:
        """Upload a PDF to an item as a manual attachment called `name`."""
        with open(path, "rb") as f:
            self._req(
                "POST",
                f"/entities/{entity_id}/attachments",
                files={"file": (name, f, "application/pdf")},
                data={"name": name, "type": "manual"},
            )

    def attach_link(self, entity_id: str, url: str, title: str) -> None:
        """Attach a URL to an item as a manual (an external link attachment)."""
        self._req(
            "POST",
            f"/entities/{entity_id}/attachments/external",
            json={
                "source_type": "link",
                "external_id": url,
                "title": title[:200],
                "attachment_type": "manual",
            },
        )

    def set_serial(self, entity_id: str, serial: str) -> None:
        """Set an item's serial number, keeping every other field as it is.

        Homebox's create request has no serial number and its PATCH doesn't take one, so this reads the entity
        and sends the same fields back with PUT (EntityUpdate), which replaces them all.
        """
        e = self.entity(entity_id)
        body = {k: e[k] for k in UPDATE_FIELDS if k in e and e[k] is not None}
        body["id"] = entity_id
        body["serialNumber"] = serial
        body["entityTypeId"] = (e.get("entityType") or {}).get("id") or ""
        if (e.get("parent") or {}).get("id"):
            body["parentId"] = e["parent"]["id"]
        body["tagIds"] = [t["id"] for t in e.get("tags") or [] if t.get("id")]
        if not body["entityTypeId"]:
            del body["entityTypeId"]
        self._req("PUT", f"/entities/{entity_id}", json=body)

    def attach_photo(self, entity_id: str, path: Path, name: str, primary: bool = False) -> None:
        """Upload a JPEG or PNG to an item as a photo; `primary` makes it the picture Homebox shows for the item."""
        media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        with open(path, "rb") as f:
            self._req(
                "POST",
                f"/entities/{entity_id}/attachments",
                files={"file": (name, f, media)},
                data={"name": name, "type": "photo", "primary": "true" if primary else "false"},
            )

    def item_url(self, entity_id: str) -> str:
        """The item's page in Homebox's own web UI."""
        return self.base.removesuffix("/api/v1") + f"/item/{entity_id}"

    def maintenance(self, entity_id: str) -> list[dict]:
        """An item's maintenance entries: id, name, description, scheduledDate, completedDate, cost."""
        return self._req("GET", f"/entities/{entity_id}/maintenance") or []

    def add_maintenance(self, entity_id: str, name: str, description: str, scheduled: str) -> dict:
        """Add a scheduled maintenance entry to an item (`scheduled` is YYYY-MM-DD); returns the new entry."""
        return self._req(
            "POST",
            f"/entities/{entity_id}/maintenance",
            json={
                "name": name[:255],
                "description": description[:2500],
                "scheduledDate": scheduled,
                "cost": "0",
            },
        )


# The fields of Homebox's EntityUpdate (PUT /entities/{id}) that EntityOut (GET) returns under the same name. The
# rest (entityTypeId, parentId, tagIds) are rebuilt from the nested objects GET returns.
UPDATE_FIELDS = (
    "name",
    "description",
    "quantity",
    "archived",
    "assetId",
    "insured",
    "lifetimeWarranty",
    "warrantyDetails",
    "warrantyExpires",
    "manufacturer",
    "modelNumber",
    "serialNumber",
    "notes",
    "purchaseDate",
    "purchaseFrom",
    "purchasePrice",
    "soldDate",
    "soldNotes",
    "soldPrice",
    "soldTo",
    "locationId",
    "syncChildEntityLocations",
    "fields",
)


def _is_location(e: dict) -> bool | None:
    """Whether an entity summary says it's a location: True, False, or None when it doesn't say."""
    flag = (e.get("entityType") or {}).get("isLocation")
    return None if flag is None else bool(flag)


def _item(e: dict) -> Item:
    """An Item from Homebox's full entity record."""
    return Item(
        id=e["id"],
        name=e.get("name", ""),
        manufacturer=(e.get("manufacturer") or "").strip(),
        model=(e.get("modelNumber") or "").strip(),
        location=((e.get("parent") or {}).get("name") or (e.get("location") or {}).get("name") or ""),
        attachments=e.get("attachments") or [],
        serial=(e.get("serialNumber") or "").strip(),
    )
