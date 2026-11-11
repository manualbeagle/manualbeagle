"""Donetick as a sink for maintenance: an approved suggestion becomes a recurring chore.

Donetick (https://donetick.com, self-hosted) is a chore list whose chores repeat. Where Homebox's maintenance
entries are one-off, a Donetick chore comes back by itself: "every 3 months" is a chore that falls due again three
months after the last one. So `[maintenance] target = "donetick"` files approved suggestions here instead.

API used (all under /api/v1, with the header `secretkey: <token>`; a token comes from Donetick's
Settings › API Tokens, or `POST /api/v1/users/tokens {name}` with another token of the same user):
- GET    /api/v1/users/profile   the token's user (`res.id`), who the chore is assigned to
- GET    /api/v1/labels          the user's labels, a bare list of `{id, name, color}`, to find `[donetick] label`
- POST   /api/v1/chores/         makes a chore; answers `{"res": <the new chore's id>}`
- DELETE /api/v1/chores/{id}     deletes one (used by tests and by a person undoing a test, never by the web UI)

A chore that repeats on an interval is `frequencyType: "interval"`, `frequency: N` and
`frequencyMetadata: {unit: days|weeks|months|years, time, timezone}`; `nextDueDate` is its first due date and
time (RFC 3339). `isRolling` false counts the next date from the due date, true from when it's ticked off.

Donetick's tokens have no scopes: a token can do anything its user can. Manual Beagle only reads the profile and
labels and makes chores.

Verified against Donetick v0.1.86's source (internal/chore/handler.go `ChoreReq` and `createChore`,
internal/chore/model/model.go `FrequencyMetadata`), 2026-11-10.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

from .. import __version__

UNITS = {"day": "days", "week": "weeks", "month": "months", "year": "years"}


class DonetickError(Exception):
    """Donetick refused a request, didn't answer, or isn't configured."""


class Donetick:
    """A client for Donetick's REST API, authenticated with one user's API token."""

    def __init__(
        self, url: str, token: str, timeout: float = 30, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not url or not token:
            raise DonetickError(
                "Donetick needs a URL and an API token (config [donetick] url, and token_env or token_file)"
            )
        self.base = url.strip().rstrip("/") + "/api/v1"
        self.http = httpx.Client(
            timeout=timeout,
            transport=transport,
            headers={
                "secretkey": token,
                "Accept": "application/json",
                "User-Agent": f"ManualBeagle/{__version__}",
            },
        )

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self.http.close()

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        """One request; returns the answer's `res` (or the whole answer when it has none)."""
        try:
            r = self.http.request(method, self.base + path, **kw)
        except httpx.HTTPError as e:
            raise DonetickError(f"Donetick didn't answer: {e}") from None
        if r.status_code >= 400:
            raise DonetickError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
        try:
            out = r.json() if r.content else None
        except ValueError:
            raise DonetickError(f"{method} {path}: the answer isn't JSON") from None
        return out.get("res", out) if isinstance(out, dict) else out

    def me(self) -> dict:
        """The token's user: `{id, username, displayName, ...}`."""
        user = self._req("GET", "/users/profile")
        if not isinstance(user, dict) or not user.get("id"):
            raise DonetickError("GET /users/profile: no user in the answer")
        return user

    def label_id(self, name: str) -> int:
        """The id of the label called `name` (ignoring case). Raises DonetickError when there's none."""
        labels = self._req("GET", "/labels") or []
        for lb in labels if isinstance(labels, list) else []:
            if str(lb.get("name", "")).strip().lower() == name.strip().lower():
                return int(lb["id"])
        raise DonetickError(f"Donetick has no label named {name!r}: make it there, or empty [donetick] label")

    def create_chore(self, chore: dict) -> int:
        """Make a chore (the body is Donetick's `ChoreReq`); returns its id."""
        res = self._req("POST", "/chores/", json=chore)
        try:
            return int(res)
        except (TypeError, ValueError):
            raise DonetickError(f"POST /chores/: no chore id in the answer ({str(res)[:100]})") from None

    def delete_chore(self, chore_id: int) -> None:
        """Delete a chore."""
        self._req("DELETE", f"/chores/{int(chore_id)}")


def due_at(day: dt.date, at: str = "09:00", timezone: str = "") -> dt.datetime:
    """`day` at the time `at` ("HH:MM") in `timezone` (an IANA name), or in this computer's own zone when empty.

    Raises DonetickError for a time or zone that isn't one.
    """
    try:
        hh, mm = (int(x) for x in at.split(":"))
        when = dt.time(hh, mm)
    except ValueError:
        raise DonetickError(f"[donetick] due_time {at!r} isn't a time such as 09:00") from None
    if not timezone:
        return dt.datetime.combine(day, when).astimezone()
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise DonetickError(
            f"[donetick] timezone {timezone!r} isn't a time zone such as Europe/London"
        ) from None
    return dt.datetime.combine(day, when, tzinfo=zone)


def interval_chore(
    name: str,
    every_n: int,
    every_unit: str,
    first: dt.datetime,
    description: str,
    user_id: int,
    *,
    timezone: str = "",
    label_id: int | None = None,
    rolling: bool = False,
    notify: bool = True,
) -> dict:
    """A Donetick `ChoreReq` for a chore that repeats every `every_n` `every_unit`s, first due at `first`.

    It's assigned to `user_id` alone, and with `notify` Donetick reminds that person when it falls due.
    """
    if every_unit not in UNITS:
        raise DonetickError(f"no Donetick unit for {every_unit!r}")
    meta: dict[str, Any] = {"unit": UNITS[every_unit], "time": first.isoformat()}
    if timezone:
        meta["timezone"] = timezone
    chore: dict[str, Any] = {
        "name": name[:255],
        "description": description,
        "frequencyType": "interval",
        "frequency": int(every_n),
        "frequencyMetadata": meta,
        "nextDueDate": first.isoformat(),
        "isRolling": rolling,
        "assignedTo": user_id,
        "assignees": [{"userId": user_id}],
        "assignStrategy": "keep_last_assigned",
        "notification": notify,
        "isActive": True,
        "isPrivate": False,
        "priority": 0,
    }
    if notify:
        chore["notificationMetadata"] = {"dueDate": True}
    if label_id is not None:
        chore["labelsV2"] = [{"id": label_id}]
    return chore


def from_config(cfg: Any) -> Donetick:
    """A Donetick client from the config's `[donetick]` table. Raises DonetickError when it isn't set up.

    Also when the address was changed on the Settings page alone: the key goes only to the address set beside it.
    """
    held = cfg.donetick.key_held_back(cfg.donetick.url)
    if held:
        raise DonetickError(f"not connecting: {held}")
    return Donetick(cfg.donetick.url, cfg.donetick.token())
